import Foundation

private struct DeferredConversationBody: Encodable, Sendable {
    let conversationId: String
}

private struct DeferredConversationResponse: Decodable, Sendable {
    let conversation: ConversationRecord
    let turns: [ConversationSnapshot]
}

@MainActor
extension AppModel {
    var shouldDeferPrompt: Bool {
        guard visibleDeletedConversation == nil, let workspaceID = selectedWorkspaceID,
              selectedWorkspace != nil else { return false }
        let key = machineWorkspaceContextKey(workspaceID: workspaceID)
        return !daemonOnline || !promptDestinationIsResolved || promptOutboxByWorkspace[key] != nil
            || acceptedPromptResultsByWorkspace[key] != nil
            || !deferredPromptsByWorkspace[key, default: []].isEmpty
    }

    var visibleDeferredPrompts: [PersistedDeferredPrompt] {
        guard let workspaceID = selectedWorkspaceID else { return [] }
        return deferredPromptsByWorkspace[machineWorkspaceContextKey(workspaceID: workspaceID), default: []].filter {
            if let selectedConversationID {
                if $0.conversationID == selectedConversationID || $0.navigationConversationID == selectedConversationID { return true }
            } else if $0.conversationID == nil && $0.navigationConversationID == nil { return true }
            if case .run(let runID, _) = $0.destination {
                return selectedConversation?.runs.contains { $0.runId == runID } == true
            }
            return false
        }
    }

    var composerQueuedMessages: [QueuedFollowUp] {
        followUpQueue + visibleDeferredPrompts.map(\.item)
    }

    private var scopedDeferredConversation: WorkspaceConversationSummary? {
        guard let workspace = selectedWorkspace, let conversation = selectedConversation,
              conversation.runs.allSatisfy({ sameConversationWorkspace($0.workspace, workspace.path) }),
              conversation.resumeSession.map({ sameConversationWorkspace($0.cwd, workspace.path) }) ?? true else { return nil }
        return conversation
    }

    private func deferredPromptDestination() -> DeferredPromptDestination {
        let key = machineWorkspaceContextKey(workspaceID: selectedWorkspaceID ?? "")
        if let navigation = conversationNavigationTarget,
           navigation.belongsTo(machineID: currentMachineContextID, workspaceID: selectedWorkspaceID,
               requestID: conversationNavigationRequestID), navigation.conversationID != selectedConversationID {
            return navigation.runID.map { .run($0, agent: nil) } ?? .conversation(navigation.conversationID)
        }
        let preceding = promptOutboxByWorkspace[key]
            ?? acceptedPromptResultsByWorkspace[key]?.submission
        if let preceding, isPromptSubmissionVisible(preceding),
           preceding.sourceAgent == nil || preceding.sourceAgent == promptContinuationAgentSummary?.agent
                || activeRun?.isWorking == true {
            return .afterCommand(preceding.commandID)
        }
        if let queued = visibleDeferredPrompts.last {
            if queued.originRunID == activeRun?.runId,
               queued.originAgent == promptContinuationAgentSummary?.agent { return queued.destination }
            if let run = activeRun, case .run(let id, let agent) = queued.destination, id == run.runId,
               run.isWorking || (agent ?? followUpContinuationSource(in: run, rememberedAgent: nil)?.agent)
                    == promptContinuationAgentSummary?.agent { return queued.destination }
        }
        if isComposingNewConversation { return .newConversation }
        if remoteTurnTransitionInProgress, let conversation = scopedDeferredConversation {
            if let latest = selectedWorkspace?.lastRunId,
               conversation.runs.contains(where: { $0.runId == latest }) { return .run(latest, agent: nil) }
            if !conversation.opensResumeHistory { return .conversation(conversation.id) }
        }
        if let navigation = conversationNavigationTarget,
           navigation.belongsTo(machineID: currentMachineContextID, workspaceID: selectedWorkspaceID,
               requestID: conversationNavigationRequestID), let runID = navigation.runID {
            return .run(runID, agent: activeRun?.runId == runID ? selectedAgent : nil)
        }
        if let conversation = scopedDeferredConversation {
            if conversation.opensResumeHistory, let session = conversation.resumeSession {
                return .session(session.sessionId)
            }
            if !conversation.latestRunID.isEmpty {
                return .run(conversation.latestRunID,
                    agent: activeRun?.runId == conversation.latestRunID ? promptContinuationAgentSummary?.agent : nil)
            }
            return .conversation(conversation.id)
        }
        if promptDestinationIsResolved, let run = activeRun {
            return .run(run.runId, agent: run.finalizedAgent ?? selectedAgent)
        }
        if selectedConversation != nil, scopedDeferredConversation == nil { return .workspace }
        if let conversationID = selectedConversationID { return .conversation(conversationID) }
        return .workspace
    }

    func saveDeferredPrompt(_ value: String, originalDraft: String) async {
        guard let workspace = selectedWorkspace else { return }
        let originalContext = currentPromptDraftContext()
        let originalGeneration = workspaceContextGeneration
        let navigation = conversationNavigationTarget.flatMap {
            $0.belongsTo(machineID: currentMachineContextID, workspaceID: selectedWorkspaceID,
                requestID: conversationNavigationRequestID) ? $0 : nil
        }
        let hasForeignProjection = selectedConversation != nil && scopedDeferredConversation == nil
        let conversationID = navigation?.conversationID ?? (hasForeignProjection ? nil : selectedConversationID)
        let queuedSettings = hasForeignProjection ? backgroundSettings(for: workspace, machineID: currentMachineContextID) : settings
        let destination = deferredPromptDestination()
        let key = machineWorkspaceContextKey(workspaceID: workspace.workspaceId)
        let predecessor = promptOutboxByWorkspace[key] ?? acceptedPromptResultsByWorkspace[key]?.submission
        let chainID: String
        if let predecessor, destination == .afterCommand(predecessor.commandID) {
            chainID = predecessor.deliveryChainID ?? predecessor.commandID
        } else if let tail = visibleDeferredPrompts.last, tail.destination == destination {
            chainID = tail.chainID ?? UUID().uuidString.lowercased()
        } else { chainID = UUID().uuidString.lowercased() }
        let queued = PersistedDeferredPrompt(item: QueuedFollowUp(prompt: value, settings: queuedSettings),
            machineID: currentMachineContextID, workspaceID: workspace.workspaceId, workspacePath: workspace.path,
            conversationID: conversationID, destination: destination,
            originRunID: hasForeignProjection ? nil : activeRun?.runId,
            originAgent: hasForeignProjection ? nil : promptContinuationAgentSummary?.agent,
            navigationConversationID: conversationID, chainID: chainID)
        deferredPromptSaves.insert(queued.contextKey)
        defer { deferredPromptSaves.remove(queued.contextKey) }
        guard await desktopStateStore.appendDeferredPrompt(queued) != nil else {
            if isCurrentMachine(queued.machineID), selectedWorkspaceID == queued.workspaceID,
               workspaceContextGeneration == originalGeneration, currentPromptDraftContext() == originalContext {
                errorMessage = appLanguage.text("无法保存消息，请检查本机可用空间。草稿未清除。",
                    "Could not save the message. Check local storage. Your draft is unchanged.")
            }
            return
        }
        deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
        if isCurrentMachine(queued.machineID), selectedWorkspaceID == queued.workspaceID,
           workspaceContextGeneration == originalGeneration, currentPromptDraftContext() == originalContext,
           prompt == originalDraft { updatePrompt("") }
        ensureDeferredPromptProcessing()
    }

    func ensureDeferredPromptProcessing() {
        for (key, items) in deferredPromptsByWorkspace where !items.isEmpty
            && items.first?.item.isPaused != true && deferredPromptTasks[key] == nil {
            deferredPromptTasks[key] = Task { [weak self] in
                defer { self?.deferredPromptTasks[key] = nil }
                while !Task.isCancelled {
                    guard let self, let head = self.deferredPromptsByWorkspace[key]?.first,
                          !head.item.isPaused else { return }
                    await self.processDeferredPrompt(key: key)
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                }
            }
        }
    }

    func cancelDeferredPromptProcessing() {
        deferredPromptTasks.values.forEach { $0.cancel() }
        deferredPromptTasks.removeAll()
        acceptedPromptSettlementTasks.values.forEach { $0.cancel() }
        acceptedPromptSettlementTasks.removeAll()
    }

    func ensureAcceptedPromptSettlement() {
        for (key, _) in acceptedPromptResultsByWorkspace where acceptedPromptSettlementTasks[key] == nil {
            acceptedPromptSettlementTasks[key] = Task { [weak self] in
                defer { self?.acceptedPromptSettlementTasks[key] = nil }
                var failures = 0
                while !Task.isCancelled {
                    guard let self, let result = self.acceptedPromptResultsByWorkspace[key] else { return }
                    if await self.adoptAcceptedPromptResult(result) { return }
                    failures += 1
                    do { try await Task.sleep(for: .seconds(PromptSubmissionRetryPolicy.delay(after: failures))) }
                    catch { return }
                }
            }
        }
    }

    func processDeferredPrompt(key: String) async {
        guard !deferredPromptSaves.contains(key),
              let queued = deferredPromptsByWorkspace[key]?.first, !queued.item.isPaused,
              promptOutboxByWorkspace[key] == nil, acceptedPromptResultsByWorkspace[key] == nil,
              !activeWorkspaceOperationIDs.contains(key),
              !activeRunOperationKeys.contains(where: {
                  MachineContextKey.belongsToWorkspace($0, machineID: queued.machineID, workspaceID: queued.workspaceID)
                  }), queued.item.retryAt.map({ $0 <= Date() }) ?? true,
              queued.readyAt.map({ $0 <= Date() }) ?? true,
              let target = requestTarget(for: queued.machineID) else { return }
        do {
            guard let submission = try await resolveDeferredPrompt(queued, target: target) else {
                if queued.item.lastError != nil, let items = deferredPromptsByWorkspace[key],
                   var head = items.first, head.item.id == queued.item.id,
                   !head.item.isPaused, head.item.lastError == queued.item.lastError {
                    head.item.lastError = nil
                    head.item.failureCount = 0
                    head.item.retryAt = nil
                    let replacement = [head] + items.dropFirst()
                    if await desktopStateStore.replaceDeferredPrompts(key: key, expected: items, replacement: replacement) {
                        deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
                    }
                }
                return
            }
            guard !Task.isCancelled,
                  deferredPromptsByWorkspace[key]?.first == queued,
                  !deferredPromptSaves.contains(key), !activeWorkspaceOperationIDs.contains(key),
                  !activeRunOperationKeys.contains(where: {
                      MachineContextKey.belongsToWorkspace($0, machineID: queued.machineID, workspaceID: queued.workspaceID)
                  }), await desktopStateStore.promoteDeferredPrompt(queued, submission: submission) != nil else { return }
            // Promotion commits the exact command and removes its queue owner
            // in one write. No network mutation can precede that write.
            deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
            promptOutboxByWorkspace[key] = submission
            restoreVisiblePromptSubmission()
            submitPendingPrompt(submission)
        } catch {
            guard let items = deferredPromptsByWorkspace[key], items.first == queued else { return }
            var changed = queued
            let needsConnection = PersistedPromptSubmission.connectionRecoveryCodes.contains(
                PromptSubmissionRetryPolicy.errorCode(error) ?? "")
            let retryable = PromptSubmissionRetryPolicy.shouldRetry(error) || needsConnection
            let failures = max(0, changed.item.failureCount ?? 0) + 1
            changed.item.failureCount = failures
            changed.item.retryAt = retryable ? Date().addingTimeInterval(PromptSubmissionRetryPolicy.delay(after: failures)) : nil
            changed.item.paused = !retryable
            changed.item.lastError = needsConnection
                ? appLanguage.text("请重新连接设备，消息已保留。", "Reconnect the device. Your message is retained.")
                : (retryable ? nil : error.localizedDescription)
            let replacement = [changed] + items.dropFirst()
            if await desktopStateStore.replaceDeferredPrompts(key: key, expected: items, replacement: replacement) {
                deferredPromptsByWorkspace[key] = replacement
            }
        }
    }

    private func resolveDeferredPrompt(_ queued: PersistedDeferredPrompt,
                                       target: PcrdRequestTarget) async throws -> PersistedPromptSubmission? {
        var settings = queued.item.settings ?? RunSettings()
        var runID: String?
        var agent: Int?
        switch queued.destination {
        case .afterCommand: return nil
        case .newConversation: settings.resumeSessionID = "NO"
        case .session(let session): settings.resumeSessionID = session
        case .run(let id, let selected):
            let run: RunSummary = try await client.call(target: target, "turns.get",
                body: GetRunBody(runId: id, includeEvents: false, cursor: 0, eventLimit: 1,
                    workspaceId: queued.workspaceID))
            guard run.runId == id, sameConversationWorkspace(run.workspace, queued.workspacePath),
                  run.conversationSnapshot.map({ $0.workspace.workspaceId == queued.workspaceID }) ?? true else {
                throw PcrdClientError.remote("prompt_destination_conflict", "The saved message's workspace does not match its source.")
            }
            guard !run.isWorking, !run.hasUnresolvedFinalization else { return nil }
            if run.normalizedStatus == "interrupted" { return nil }
            guard !["discarded", "cancelled", "canceled", "stopped"].contains(run.normalizedStatus),
                  run.artifactsAvailable != false else {
                throw PcrdClientError.remote("source_stopped", "The source was stopped. This queued message remains paused.")
            }
            let eligible = run.agents.filter(\.mayContinue)
            let choice = run.finalizedAgent ?? selected
                ?? followUpContinuationSource(in: run, rememberedAgent: nil)?.agent
            guard let choice, eligible.contains(where: { $0.agent == choice }) else { return nil }
            if selected == nil, settings.followUpDelay > 0, queued.readyAt == nil,
               let items = deferredPromptsByWorkspace[queued.contextKey], items.first == queued {
                var ready = queued
                ready.readyAt = Date().addingTimeInterval(TimeInterval(settings.followUpDelay))
                let replacement = [ready] + items.dropFirst()
                if await desktopStateStore.replaceDeferredPrompts(key: queued.contextKey, expected: items, replacement: replacement) {
                    deferredPromptsByWorkspace[queued.contextKey] = replacement
                }
                return nil
            }
            runID = id
            agent = choice
        case .conversation(let id):
            let response: DeferredConversationResponse = try await client.call(target: target, "conversations.get",
                body: DeferredConversationBody(conversationId: id))
            guard response.conversation.conversationId == id,
                  response.conversation.workspaceId == queued.workspaceID,
                  response.turns.allSatisfy({ $0.workspace.workspaceId == queued.workspaceID && $0.turn.conversationId == id }) else {
                throw PcrdClientError.remote("prompt_destination_conflict", "The saved conversation does not match this workspace.")
            }
            let latest = response.turns.max { $0.turn.turnIndex < $1.turn.turnIndex }
            let destination: DeferredPromptDestination
            if let latest, !latest.turn.runId.isEmpty { destination = .run(latest.turn.runId, agent: nil) }
            else if let session = response.conversation.resumeSessionId { destination = .session(session) }
            else { return nil }
            await bindDeferredDestination(queued, to: destination)
            return nil
        case .workspace:
            let registry: WorkspaceList = try await client.call(target: target, "workspaces.list", body: EmptyBody())
            guard let workspace = registry.workspaces.first(where: { $0.workspaceId == queued.workspaceID }),
                  sameConversationWorkspace(workspace.path, queued.workspacePath) else {
                throw PcrdClientError.remote("prompt_destination_conflict", "The saved workspace is no longer available.")
            }
            let destination: DeferredPromptDestination = !workspace.lastRunId.isEmpty
                ? .run(workspace.lastRunId, agent: nil)
                : (usableResumeSessionID(workspace.lastResumeSessionId).map { .session($0) } ?? .newConversation)
            await bindDeferredDestination(queued, to: destination)
            return nil
        }
        return PersistedPromptSubmission(id: queued.item.id.uuidString.lowercased(), machineID: queued.machineID,
            workspaceID: queued.workspaceID, prompt: queued.item.prompt, settings: settings,
            sourceRunID: runID, sourceAgent: agent, resumeSessionID: settings.resumeSessionID,
            confirmLargeRun: false, attempt: max(0, queued.item.attempt ?? 0), status: .pending, error: "",
            receiptUncertain: false, conversationID: queued.conversationID, workspacePath: queued.workspacePath,
            deliveryChainID: queued.chainID)
    }

    private func bindDeferredDestination(_ queued: PersistedDeferredPrompt, to destination: DeferredPromptDestination) async {
        guard let items = deferredPromptsByWorkspace[queued.contextKey], items.first == queued else { return }
        let replacement = items.map { item in
            var item = item
            if item.destination == queued.destination, item.conversationID == queued.conversationID { item.destination = destination }
            return item
        }
        if await desktopStateStore.replaceDeferredPrompts(key: queued.contextKey, expected: items, replacement: replacement) {
            deferredPromptsByWorkspace[queued.contextKey] = replacement
        }
    }

    func changeDeferredPrompt(_ id: UUID, change: QueuedFollowUpChange) async -> Bool? {
        guard let workspaceID = selectedWorkspaceID else { return nil }
        let key = machineWorkspaceContextKey(workspaceID: workspaceID)
        guard let items = deferredPromptsByWorkspace[key], items.contains(where: { $0.item.id == id }) else { return nil }
        // Do not reorder across a different frozen branch or conversation.
        guard let original = items.first(where: { $0.item.id == id }) else { return false }
        let peers = items.filter { $0.destination == original.destination && $0.conversationID == original.conversationID }
        guard let updated = change.applying(to: peers.map(\.item), id: id) else { return false }
        var iterator = updated.makeIterator()
        var replacement: [PersistedDeferredPrompt] = []
        for item in items {
            if !peers.contains(where: { $0.item.id == item.item.id }) { replacement.append(item); continue }
            if let next = iterator.next(), let saved = peers.first(where: { $0.item.id == next.id }) {
                var changed = saved
                changed.item = next
                if case .edit = change, changed.item.id == id {
                    changed.item.resetAfterEdit(settings: saved.item.settings ?? RunSettings())
                    changed.readyAt = nil
                }
                replacement.append(changed)
            }
        }
        guard await desktopStateStore.replaceDeferredPrompts(key: key, expected: items, replacement: replacement) else { return false }
        deferredPromptsByWorkspace[key] = replacement.isEmpty ? nil : replacement
        ensureDeferredPromptProcessing()
        return true
    }

    func pauseDeferredPrompts(workspaceID: String, machineID: String) async {
        let key = machineWorkspaceContextKey(workspaceID: workspaceID, machineID: machineID)
        guard let items = deferredPromptsByWorkspace[key] else { return }
        let replacement = items.map { item in
            var item = item
            item.item.paused = true
            return item
        }
        if await desktopStateStore.replaceDeferredPrompts(key: key, expected: items, replacement: replacement) {
            deferredPromptsByWorkspace[key] = replacement
        }
    }
}
