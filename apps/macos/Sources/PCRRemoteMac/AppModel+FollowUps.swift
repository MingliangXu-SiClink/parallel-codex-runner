import Foundation

extension AppModel {
    var followUpQueueContext: String {
        "\(currentMachineContextID):\(selectedWorkspaceID ?? ""):\(promptDraftContext):\(workspaceContextGeneration)"
    }

    func daemonOwnsFollowUpQueue(machineID explicitMachineID: String? = nil) -> Bool {
        let machineID = MachineContextKey.normalizeMachineID(
            explicitMachineID ?? currentMachineContextID
        )
        if daemonFollowUpQueueConfirmedMachines.contains(machineID) {
            return true
        }
        if systemCapabilitiesByMachine[machineID]?.supports("followups.replace") == true {
            return true
        }
        return isCurrentMachine(machineID)
            && systemCapabilities?.supports("followups.replace") == true
    }

    func followUpQueueOwnershipIsKnown(
        machineID explicitMachineID: String? = nil
    ) -> Bool {
        let machineID = MachineContextKey.normalizeMachineID(
            explicitMachineID ?? currentMachineContextID
        )
        if systemCapabilitiesByMachine[machineID] != nil { return true }
        return isCurrentMachine(machineID) && systemCapabilities != nil
    }

    func remoteFollowUpSelection(runID: String) -> Int? {
        guard let run = activeRun, run.runId == runID else { return nil }
        if let finalized = run.finalizedAgent,
           run.agents.contains(where: { $0.agent == finalized && $0.mayContinue }) {
            return finalized
        }
        if continuationSelectionNeedsConfirmation {
            return daemonFollowUpSnapshot.flatMap { $0.runId == runID ? $0.selectedAgent : nil }
        }
        guard !run.isWorking,
              let key = selectedAgentContextKey(runID: runID),
              let selectedAgent = rememberedSelectedAgent(for: run, contextKey: key),
              run.agents.contains(where: { $0.agent == selectedAgent && $0.mayContinue }) else {
            return nil
        }
        return selectedAgent
    }

    private func daemonSupportsAtomicFollowUpEnqueue(machineID: String) -> Bool {
        let normalized = MachineContextKey.normalizeMachineID(machineID)
        if systemCapabilitiesByMachine[normalized]?.supports("followups.enqueue") == true {
            return true
        }
        return isCurrentMachine(normalized)
            && systemCapabilities?.supports("followups.enqueue") == true
    }

    private func replaceDaemonFollowUpQueue(
        workspaceID: String,
        runID: String,
        items: [QueuedFollowUp],
        machineID: String,
        resetCountdown: Bool,
        expectedGeneration: Int? = nil,
        bodySnapshot: FollowUpQueueReplaceBody? = nil
    ) async throws -> RemoteFollowUpQueueResponse {
        guard let target = requestTarget(for: machineID) else {
            throw PcrdClientError.connection("The selected computer is not available.")
        }
        let response: RemoteFollowUpQueueResponse = try await client.call(
            target: target,
            "followups.replace",
            body: bodySnapshot ?? FollowUpQueueReplaceBody(
                workspaceId: workspaceID,
                runId: runID,
                items: items.map {
                    FollowUpQueueItemBody($0, fallbackSettings: settings)
                },
                selectedAgent: remoteFollowUpSelection(runID: runID),
                resetCountdown: resetCountdown,
                expectedGeneration: expectedGeneration
            ),
            commandID: "follow-up-queue-\(UUID().uuidString.lowercased())"
        )
        daemonFollowUpQueueConfirmedMachines.insert(
            MachineContextKey.normalizeMachineID(machineID)
        )
        return response
    }

    private func enqueueDaemonFollowUp(
        workspaceID: String,
        runID: String,
        item: QueuedFollowUp,
        machineID: String
    ) async throws -> RemoteFollowUpQueueResponse {
        guard let target = requestTarget(for: machineID) else {
            throw PcrdClientError.connection("The selected computer is not available.")
        }
        let response: RemoteFollowUpQueueResponse = try await client.call(
            target: target,
            "followups.enqueue",
            body: FollowUpQueueEnqueueBody(
                workspaceId: workspaceID,
                runId: runID,
                item: FollowUpQueueItemBody(item, fallbackSettings: settings),
                selectedAgent: nil
            ),
            commandID: "follow-up-enqueue-\(item.id.uuidString.lowercased())"
        )
        daemonFollowUpQueueConfirmedMachines.insert(
            MachineContextKey.normalizeMachineID(machineID)
        )
        return response
    }

    private func recoverDaemonFollowUpEnqueue(
        workspaceID: String,
        runID: String,
        item: QueuedFollowUp,
        queuedItems: [QueuedFollowUp],
        machineID: String,
        supportsAtomicEnqueue: Bool
    ) async -> RemoteFollowUpQueueResponse? {
        // A relay timeout is ambiguous: pcrd may already have committed the
        // message. Retry the same idempotent command before telling the user it
        // failed, otherwise a second tap can enqueue the prompt twice.
        try? await Task.sleep(for: .milliseconds(200))
        guard !Task.isCancelled else { return nil }
        let repeated: RemoteFollowUpQueueResponse?
        if supportsAtomicEnqueue {
            repeated = try? await enqueueDaemonFollowUp(
                workspaceID: workspaceID,
                runID: runID,
                item: item,
                machineID: machineID
            )
        } else {
            repeated = try? await replaceDaemonFollowUpQueue(
                workspaceID: workspaceID,
                runID: runID,
                items: queuedItems,
                machineID: machineID,
                resetCountdown: false
            )
        }
        if let repeated { return repeated }

        guard let target = requestTarget(for: machineID),
              let snapshot: RemoteFollowUpQueueResponse = try? await client.call(
                  target: target,
                  "followups.get",
                  body: FollowUpQueueGetBody(workspaceId: workspaceID)
              ),
              snapshot.queue?.items.contains(where: {
                  $0.id.caseInsensitiveCompare(item.id.uuidString) == .orderedSame
              }) == true else { return nil }
        daemonFollowUpQueueConfirmedMachines.insert(
            MachineContextKey.normalizeMachineID(machineID)
        )
        return snapshot
    }

    private func clearDaemonFollowUpQueue(
        workspaceID: String,
        runID: String,
        machineID: String
    ) async throws -> RemoteFollowUpQueueResponse {
        guard let target = requestTarget(for: machineID) else {
            throw PcrdClientError.connection("The selected computer is not available.")
        }
        let response: RemoteFollowUpQueueResponse = try await client.call(
            target: target,
            "followups.clear",
            body: FollowUpQueueClearBody(
                workspaceId: workspaceID,
                runId: runID
            ),
            commandID: "follow-up-clear-\(UUID().uuidString.lowercased())"
        )
        daemonFollowUpQueueConfirmedMachines.insert(
            MachineContextKey.normalizeMachineID(machineID)
        )
        return response
    }

    func applyDaemonFollowUpQueue(
        _ incoming: RemoteFollowUpQueue?,
        workspaceID: String,
        machineID: String,
        revision: Int? = nil,
        acknowledged: Set<UUID> = []
    ) {
        let normalizedMachineID = MachineContextKey.normalizeMachineID(machineID)
        let workspaceKey = MachineContextKey.workspace(machineID: normalizedMachineID, workspaceID: workspaceID)
        guard incoming == nil || incoming?.workspaceId == workspaceID else { return }
        if let revision {
            guard revision >= (daemonFollowUpRevisionsByWorkspace[workspaceKey] ?? -1) else { return }
            daemonFollowUpRevisionsByWorkspace[workspaceKey] = revision
        }
        let remote = revision != nil ? incoming : FollowUpQueueReconciliation.newest(
            incoming, previous: daemonFollowUpSnapshotsByWorkspace[workspaceKey]
        )
        var acknowledged = acknowledged
        if let recovery = queueIssueRecovery, recovery.workspaceKey == workspaceKey,
           recovery.proof.matches(remote) {
            acknowledged.formUnion(recovery.proof.previousIDs)
            queueIssueRecovery = nil
            if isCurrentMachine(normalizedMachineID), selectedWorkspaceID == workspaceID,
               queueIssue == recovery.message {
                queueIssue = nil
            }
        }
        daemonFollowUpSnapshotsByWorkspace[workspaceKey] = remote
        let localQueues = persistedFollowUpQueues.values.filter {
            MachineContextKey.normalizeMachineID($0.machineID) == normalizedMachineID
                && $0.workspaceID == workspaceID
        }
        let local = localQueues.filter { $0.runID == remote?.runId }
            .flatMap(\.items)
        persistedFollowUpQueues = persistedFollowUpQueues.filter {
            MachineContextKey.normalizeMachineID($0.value.machineID) != normalizedMachineID
                || $0.value.workspaceID != workspaceID
        }
        let items = FollowUpQueueReconciliation.merge(
            remote: remote?.items.compactMap { $0.localValue() } ?? [],
            local: local, acknowledged: acknowledged, completedID: remote?.lastCompletedItemId
        )
        // An unacknowledged message belongs to its original conversation. A
        // different workspace queue must neither erase it nor send it elsewhere.
        for saved in localQueues where remote == nil || saved.runID != remote?.runId {
            let pending = FollowUpQueueReconciliation.merge(
                remote: [], local: saved.items, acknowledged: acknowledged,
                completedID: remote?.lastCompletedItemId
            )
            if !pending.isEmpty {
                let retained = PersistedFollowUpQueue(machineID: normalizedMachineID,
                    workspaceID: workspaceID, runID: saved.runID, items: pending)
                persistedFollowUpQueues[retained.contextKey] = retained
            }
        }
        let deadline = remote?.deadlineEpochMs.map {
            Date(timeIntervalSince1970: TimeInterval($0) / 1_000)
        }
        if let remote, !items.isEmpty {
            let saved = PersistedFollowUpQueue(
                machineID: normalizedMachineID,
                workspaceID: workspaceID,
                runID: remote.runId,
                items: items,
                deadline: deadline
            )
            persistedFollowUpQueues[saved.contextKey] = saved
        }
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)

        guard isCurrentMachine(normalizedMachineID),
              selectedWorkspaceID == workspaceID else { return }
        let previousSelection = daemonFollowUpSnapshot?.selectedAgent
        let previousSelectionRun = daemonFollowUpSnapshot?.runId
        restoreCachedFollowUpQueue(workspaceID: workspaceID, runID: activeRun?.runId)
        if let run = activeRun, !run.isWorking, run.finalizedAgent == nil,
           let remote, remote.runId == run.runId,
           ["manual", "automatic"].contains(remote.selectionSource ?? ""),
           let agent = remote.selectedAgent, pendingAgentSelection == nil,
           !continuationSelectionNeedsConfirmation,
           let key = selectedAgentContextKey(),
           selectedAgentsByContext[key] == nil || selectedAgentContextsWhileWorking.contains(key),
           run.agents.contains(where: { $0.agent == agent }),
           previousSelectionRun != remote.runId || previousSelection != agent {
            selectedAgent = agent
            rememberSelectedAgent(agent)
            Task { [weak self] in await self?.loadSelectedAgentDetail(maxPages: 20) }
        }
        if followUpQueue.isEmpty {
            // Do not cancel the daemon monitor from inside its own refresh. It
            // still needs to load the newly-created run before it exits.
            followUpSecondsRemaining = nil
        } else {
            ensureDaemonFollowUpMonitor(
                workspaceID: workspaceID,
                machineID: normalizedMachineID
            )
        }
    }

    @discardableResult
    func refreshDaemonFollowUpQueue(
        workspaceID: String,
        runID: String?,
        machineID: String,
        migrateLocalQueue: Bool = true
    ) async -> String? {
        guard daemonOwnsFollowUpQueue(machineID: machineID),
              let target = requestTarget(for: machineID) else { return nil }
        do {
            let response: RemoteFollowUpQueueResponse = try await client.call(
                target: target,
                "followups.get",
                body: FollowUpQueueGetBody(workspaceId: workspaceID)
            )
            daemonFollowUpQueueConfirmedMachines.insert(
                MachineContextKey.normalizeMachineID(machineID)
            )
            let pendingItems: [QueuedFollowUp] = if isCurrentMachine(machineID),
                                                    selectedWorkspaceID == workspaceID,
                                                    followUpRunID == runID {
                followUpQueue.filter { $0.needsDaemonAcknowledgement && !$0.isPaused }
            } else {
                persistedFollowUpQueues.values.filter {
                    MachineContextKey.normalizeMachineID($0.machineID) == MachineContextKey.normalizeMachineID(machineID)
                        && $0.workspaceID == workspaceID && $0.runID == runID
                }.flatMap(\.items).filter { $0.needsDaemonAcknowledgement && !$0.isPaused }
            }
            var resolved = response.queue
            var resolvedRevision = response.revision
            var acknowledged: Set<UUID> = []
            let migrateLocalQueue = migrateLocalQueue && followUpEnqueueCount == 0
            if resolved == nil, !pendingItems.isEmpty, migrateLocalQueue {
                guard let sourceRunID = runID else { return nil }
                if daemonSupportsAtomicFollowUpEnqueue(machineID: machineID) {
                    for item in pendingItems {
                        let updated = try await enqueueDaemonFollowUp(workspaceID: workspaceID,
                            runID: sourceRunID, item: item, machineID: machineID)
                        resolved = updated.queue ?? resolved
                        resolvedRevision = updated.revision
                        acknowledged.insert(item.id)
                    }
                } else {
                    let updated = try await replaceDaemonFollowUpQueue(workspaceID: workspaceID,
                        runID: sourceRunID, items: pendingItems, machineID: machineID,
                        resetCountdown: false)
                    resolved = updated.queue
                    resolvedRevision = updated.revision
                }
            } else if let currentRemote = resolved {
                let remoteIDs = Set(currentRemote.items.map(\.id))
                let missing = pendingItems.filter {
                    !remoteIDs.contains($0.id.uuidString.lowercased())
                        && $0.id.uuidString.lowercased() != currentRemote.lastCompletedItemId?.lowercased()
                }
                if !missing.isEmpty, migrateLocalQueue {
                    if daemonSupportsAtomicFollowUpEnqueue(machineID: machineID) {
                        for item in missing {
                            let updated = try await enqueueDaemonFollowUp(
                                workspaceID: workspaceID,
                                runID: runID ?? currentRemote.runId,
                                item: item,
                                machineID: machineID
                            )
                            resolved = updated.queue ?? resolved
                            resolvedRevision = updated.revision
                            acknowledged.insert(item.id)
                        }
                    } else {
                        let merged = currentRemote.items.compactMap { $0.localValue() }
                            + missing
                        let updated = try await replaceDaemonFollowUpQueue(
                            workspaceID: workspaceID,
                            runID: runID ?? currentRemote.runId,
                            items: merged,
                            machineID: machineID,
                            resetCountdown: false
                        )
                        resolved = updated.queue
                        resolvedRevision = updated.revision
                    }
                }
            }
            if let remote = resolved, !queuePresented, followUpEnqueueCount == 0,
               isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
               let run = activeRun,
               let conversationID = remote.conversationId, !conversationID.isEmpty,
               conversationID == run.conversationId,
               let original = FollowUpAdmissionRecovery.originalItem(in: remote, currentRunID: run.runId),
               let generation = remote.generation {
                let normalized = MachineContextKey.normalizeMachineID(machineID)
                var capabilities = systemCapabilitiesByMachine[normalized] ?? systemCapabilities
                if capabilities?.supportsFollowUpEnqueueReconciliation != true,
                   Date().timeIntervalSince(followUpAdmissionCapabilityChecksByMachine[normalized] ?? .distantPast) >= 60 {
                    followUpAdmissionCapabilityChecksByMachine[normalized] = Date()
                    capabilities = try? await verifiedSystemCapabilities(forceRefresh: true, target: target)
                }
                let key = machineWorkspaceContextKey(workspaceID: workspaceID, machineID: machineID)
                if capabilities?.supportsFollowUpEnqueueReconciliation == true,
                   capabilities?.supports("followups.reconcile_enqueue") == true,
                   followUpAdmissionChecksByWorkspace[key] != generation {
                    followUpAdmissionChecksByWorkspace[key] = generation
                    do {
                        // Only reconcile a known receipt. Repeating enqueue itself
                        // could create a new item after the old receipt expires.
                        let checked: RemoteFollowUpQueueResponse = try await client.call(
                            target: target, "followups.reconcile_enqueue",
                            body: FollowUpQueueReconcileBody(
                                enqueueCommandId: "follow-up-enqueue-\(original.id.uuidString.lowercased())",
                                enqueue: FollowUpQueueEnqueueBody(workspaceId: workspaceID, runId: run.runId,
                                    item: FollowUpQueueItemBody(original, fallbackSettings: settings), selectedAgent: nil)
                            ),
                            commandID: "follow-up-admission-\(original.id.uuidString.lowercased())-\(generation)"
                        )
                        resolved = checked.queue
                        resolvedRevision = checked.revision
                    } catch {
                        if PromptSubmissionRetryPolicy.shouldRetry(error) {
                            followUpAdmissionChecksByWorkspace.removeValue(forKey: key)
                        }
                    }
                }
            }
            // The workspace queue can belong to another conversation, including
            // an already consumed queue. Only refresh(), while actually opening
            // a new Turn, may suspend the visible conversation's composer.
            applyDaemonFollowUpQueue(
                resolved,
                workspaceID: workspaceID,
                machineID: machineID,
                revision: resolvedRevision,
                acknowledged: acknowledged
            )
            return daemonFollowUpSnapshotsByWorkspace[
                machineWorkspaceContextKey(workspaceID: workspaceID, machineID: machineID)
            ]?.runId
        } catch {
            // The daemon remains authoritative. Keep the local projection until
            // the next connection refresh instead of running the queue twice.
            return nil
        }
    }

    private func ensureDaemonFollowUpMonitor(
        workspaceID: String,
        machineID: String
    ) {
        guard followUpTask?.isCancelled != false else { return }
        let token = UUID()
        daemonFollowUpMonitorToken = token
        let navigationRequestID = conversationNavigationRequestID
        followUpTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.daemonFollowUpMonitorToken == token {
                    self.followUpTask = nil
                    self.daemonFollowUpMonitorToken = nil
                }
            }
            while !Task.isCancelled {
                guard self.isCurrentMachine(machineID),
                      self.selectedWorkspaceID == workspaceID,
                      self.conversationNavigationRequestID == navigationRequestID,
                      !self.followUpQueue.isEmpty,
                      self.daemonOwnsFollowUpQueue(machineID: machineID) else { return }
                if let deadline = self.followUpDeadline {
                    let seconds = max(
                        0,
                        Int(ceil(deadline.timeIntervalSinceNow))
                    )
                    if self.followUpSecondsRemaining != seconds { self.followUpSecondsRemaining = seconds }
                } else {
                    if self.followUpSecondsRemaining != nil { self.followUpSecondsRemaining = nil }
                }
                do {
                    try await Task.sleep(for: .seconds(1))
                } catch {
                    return
                }
                let previousRunID = self.followUpRunID
                let hasPendingAcknowledgement = self.followUpQueue.contains(
                    where: \.needsDaemonAcknowledgement
                )
                let nextRunID = await self.refreshDaemonFollowUpQueue(
                    workspaceID: workspaceID,
                    runID: previousRunID,
                    machineID: machineID,
                    migrateLocalQueue: hasPendingAcknowledgement
                )
                guard !Task.isCancelled,
                      self.isCurrentMachine(machineID), self.selectedWorkspaceID == workspaceID,
                      self.conversationNavigationRequestID == navigationRequestID else { return }
                if let previousRunID,
                   let nextRunID,
                   previousRunID != nextRunID {
                    await self.refresh(reportErrors: false)
                }
            }
        }
    }

    @discardableResult
    func changeQueuedFollowUp(_ id: UUID, change: QueuedFollowUpChange, expectedContext: String? = nil) async -> Bool {
        guard expectedContext == nil || expectedContext == followUpQueueContext else { return false }
        if let changed = await changeDeferredPrompt(id, change: change) { return changed }
        queueIssue = nil
        let machineID = currentMachineContextID
        let workspaceID = selectedWorkspaceID
        let runID = followUpRunID
        let generation = workspaceContextGeneration
        if followUpQueue.first(where: { $0.id == id })?.needsDaemonAcknowledgement == true {
            _ = await followUpEnqueueTask?.value
        }
        if let workspaceID, daemonOwnsFollowUpQueue() {
            guard let target = requestTarget(for: machineID) else { return false }
            do {
                let response: RemoteFollowUpQueueResponse = try await client.call(
                    target: target, "followups.get", body: FollowUpQueueGetBody(workspaceId: workspaceID)
                )
                applyDaemonFollowUpQueue(response.queue, workspaceID: workspaceID, machineID: machineID, revision: response.revision)
            } catch {
                if isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
                   workspaceContextGeneration == generation {
                    queueIssue = appLanguage.userMessage(for: error)
                }
                return false
            }
        }
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
              followUpRunID == runID, workspaceContextGeneration == generation else { return false }
        let lockedIDs: Set<UUID> = daemonFollowUpSnapshot?.state == "dispatching"
            ? Set(followUpQueue.prefix(1).map(\.id)) : []
        guard let items = change.applying(to: followUpQueue, id: id, lockedIDs: lockedIDs) else {
            queueIssue = appLanguage.text(
                "这条排队消息已发送或已被修改，请查看最新队列。",
                "This queued message has already been sent or changed. Check the updated queue."
            )
            return false
        }
        if items == followUpQueue { return true }
        return await replaceFollowUpQueue(items)
    }

    @discardableResult
    func replaceFollowUpQueue(_ items: [QueuedFollowUp]) async -> Bool {
        let previous = Dictionary(uniqueKeysWithValues: followUpQueue.map { ($0.id, $0) })
        let normalized = items.compactMap { item -> QueuedFollowUp? in
            let value = item.prompt.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty else { return nil }
            var normalized = item
            normalized.prompt = value
            if previous[item.id]?.prompt != value {
                normalized.resetAfterEdit(settings: settings)
            }
            return normalized
        }
        let machineID = currentMachineContextID
        let workspaceID = selectedWorkspaceID
        let contextGeneration = workspaceContextGeneration
        let runID = followUpRunID ?? activeRun?.runId
        if let workspaceID, let runID {
            let body = FollowUpQueueReplaceBody(workspaceId: workspaceID, runId: runID,
                items: normalized.map { FollowUpQueueItemBody($0, fallbackSettings: settings) },
                selectedAgent: remoteFollowUpSelection(runID: runID), resetCountdown: true,
                expectedGeneration: daemonFollowUpSnapshot?.generation)
            do {
                let response: RemoteFollowUpQueueResponse
                response = try await replaceDaemonFollowUpQueue(
                        workspaceID: workspaceID,
                        runID: runID,
                        items: normalized,
                        machineID: machineID,
                        resetCountdown: true,
                        expectedGeneration: body.expectedGeneration,
                        bodySnapshot: body
                    )
                applyDaemonFollowUpQueue(
                    response.queue,
                    workspaceID: workspaceID,
                    machineID: machineID,
                    revision: response.revision,
                    acknowledged: Set(previous.keys)
                )
                for item in normalized where previous[item.id]?.prompt != item.prompt {
                    await recordPromptHistory(item.prompt)
                }
                return true
            } catch {
                guard isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
                      workspaceContextGeneration == contextGeneration else { return false }
                let message = appLanguage.text(
                    "无法更新排队消息。\(appLanguage.userMessage(for: error))",
                    "The queue could not be updated. \(error.localizedDescription)"
                )
                if previous.values.allSatisfy({ !$0.needsDaemonAcknowledgement }) {
                    queueIssueRecovery = (MachineContextKey.workspace(machineID: machineID, workspaceID: workspaceID),
                        FollowUpQueueReplacementProof(runID: runID, generation: body.expectedGeneration,
                            previousIDs: Set(previous.keys), items: body.items), message)
                }
                queueIssue = message
                if let target = requestTarget(for: machineID),
                   let latest: RemoteFollowUpQueueResponse = try? await client.call(target: target,
                       "followups.get", body: FollowUpQueueGetBody(workspaceId: workspaceID)) {
                    applyDaemonFollowUpQueue(latest.queue, workspaceID: workspaceID, machineID: machineID,
                        revision: latest.revision)
                    if isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
                       workspaceContextGeneration == contextGeneration, queueIssue == nil { return true }
                }
                return false
            }
        }

        followUpQueue = normalized
        for item in normalized where previous[item.id]?.prompt != item.prompt {
            await recordPromptHistory(item.prompt)
        }
        if normalized.isEmpty {
            clearFollowUpQueue()
        } else {
            if followUpRunID == nil { followUpRunID = activeRun?.runId }
            persistFollowUpQueue()
            if let run = activeRun, !run.isWorking {
                await reconcileFollowUpQueue(for: run, resetCountdown: true)
            }
        }
        return true
    }

    func presentFollowUpQueue() {
        if !daemonOwnsFollowUpQueue() { cancelFollowUpCountdown() }
        queuePresented = true
    }

    func cancelFollowUpQueueEditing() async {
        queuePresented = false
        if !daemonOwnsFollowUpQueue(), let run = activeRun, !run.isWorking, !followUpQueue.isEmpty {
            await reconcileFollowUpQueue(for: run, resetCountdown: true)
        }
    }

    func enqueueFollowUp(_ value: String) async -> Bool {
        guard let run = activeRun,
              let workspaceID = selectedWorkspaceID else { return false }
        queueIssue = nil
        if followUpQueue.isEmpty { followUpRunID = run.runId }
        var queuedItem = QueuedFollowUp(prompt: value, settings: settings)
        queuedItem.daemonAcknowledged = false
        followUpQueue.append(queuedItem)
        let submittedItems = followUpQueue
        let submittedRunID = followUpRunID ?? run.runId
        persistFollowUpQueue()
        updatePrompt("")
        let machineID = currentMachineContextID
        let previous = followUpEnqueueTask
        followUpEnqueueCount += 1
        let task = Task { [weak self] in
            _ = await previous?.value
            guard let self else { return false }
            defer {
                self.followUpEnqueueCount -= 1
                if self.followUpEnqueueCount == 0 { self.followUpEnqueueTask = nil }
            }
            return await self.deliverQueuedFollowUp(queuedItem, submittedItems: submittedItems,
                runID: submittedRunID, workspaceID: workspaceID, machineID: machineID)
        }
        followUpEnqueueTask = task
        return await task.value
    }

    private func deliverQueuedFollowUp(_ queuedItem: QueuedFollowUp,
                                      submittedItems: [QueuedFollowUp], runID submittedRunID: String,
                                      workspaceID: String, machineID: String) async -> Bool {
        guard persistedFollowUpQueues.values.contains(where: {
            $0.workspaceID == workspaceID && $0.runID == submittedRunID
                && MachineContextKey.normalizeMachineID($0.machineID) == MachineContextKey.normalizeMachineID(machineID)
                && $0.items.contains(where: { $0.id == queuedItem.id && $0.needsDaemonAcknowledgement })
        }) else { return true }
        ensureDaemonFollowUpMonitor(
            workspaceID: workspaceID,
            machineID: machineID
        )
        let supportsAtomicEnqueue = daemonSupportsAtomicFollowUpEnqueue(
            machineID: machineID
        )
        do {
            let response = if supportsAtomicEnqueue {
                try await enqueueDaemonFollowUp(
                    workspaceID: workspaceID,
                    runID: submittedRunID,
                    item: queuedItem,
                    machineID: machineID
                )
            } else {
                try await replaceDaemonFollowUpQueue(
                    workspaceID: workspaceID,
                    runID: submittedRunID,
                    items: submittedItems,
                    machineID: machineID,
                    resetCountdown: false
                )
            }
            applyDaemonFollowUpQueue(
                response.queue,
                workspaceID: workspaceID,
                machineID: machineID,
                revision: response.revision,
                acknowledged: [queuedItem.id]
            )
            return true
        } catch {
            if let recovered = await recoverDaemonFollowUpEnqueue(
                workspaceID: workspaceID,
                runID: submittedRunID,
                item: queuedItem,
                queuedItems: submittedItems,
                machineID: machineID,
                supportsAtomicEnqueue: supportsAtomicEnqueue
            ) {
                applyDaemonFollowUpQueue(
                    recovered.queue,
                    workspaceID: workspaceID,
                    machineID: machineID,
                    revision: recovered.revision,
                    acknowledged: [queuedItem.id]
                )
                return true
            }
            if PromptSubmissionRetryPolicy.shouldRetry(error) {
                ensureDaemonFollowUpMonitor(
                    workspaceID: workspaceID,
                    machineID: machineID
                )
                return true
            }
            let message = appLanguage.text(
                "无法加入队列。\(appLanguage.userMessage(for: error))",
                "The message could not be queued. \(error.localizedDescription)"
            )
            recordFollowUpSubmissionFailure(queuedItem.id, workspaceID: workspaceID,
                runID: submittedRunID, machineID: machineID, message: message)
            return false
        }
    }

    func recordFollowUpSubmissionFailure(_ id: UUID, workspaceID: String, runID: String,
                                         machineID: String, message: String) {
        let key = MachineContextKey.run(machineID: machineID, workspaceID: workspaceID, runID: runID)
        guard let saved = persistedFollowUpQueues[key],
              let index = saved.items.firstIndex(where: { $0.id == id && $0.needsDaemonAcknowledgement }) else { return }
        var items = saved.items
        items[index].paused = true
        items[index].lastError = message
        persistedFollowUpQueues[key] = PersistedFollowUpQueue(machineID: machineID,
            workspaceID: workspaceID, runID: runID, items: items, deadline: saved.deadline)
        desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        if isCurrentMachine(machineID), selectedWorkspaceID == workspaceID, followUpRunID == runID,
           let visibleIndex = followUpQueue.firstIndex(where: { $0.id == id }) {
            followUpQueue[visibleIndex] = items[index]
        }
    }

    func clearFollowUpQueue(persist: Bool = true) {
        let persistedKey = followUpQueueContextKey()
        let workspaceID = selectedWorkspaceID
        let runID = followUpRunID ?? activeRun?.runId
        let machineID = currentMachineContextID
        followUpQueue = []
        daemonFollowUpSnapshot = nil
        followUpRunID = nil
        followUpDeadline = nil
        cancelFollowUpCountdown()
        if persist, let persistedKey {
            persistedFollowUpQueues.removeValue(forKey: persistedKey)
            self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        }
        if persist,
           let workspaceID,
           let runID,
           daemonOwnsFollowUpQueue(machineID: machineID) {
            Task { [weak self] in
                guard let self else { return }
                do {
                    let response = try await self.clearDaemonFollowUpQueue(
                        workspaceID: workspaceID,
                        runID: runID,
                        machineID: machineID
                    )
                    self.applyDaemonFollowUpQueue(response.queue, workspaceID: workspaceID, machineID: machineID, revision: response.revision)
                } catch {
                    await self.refreshDaemonFollowUpQueue(
                        workspaceID: workspaceID,
                        runID: runID,
                        machineID: machineID,
                        migrateLocalQueue: false
                    )
                    self.errorMessage = self.appLanguage.text(
                        "无法清空队列。\(self.appLanguage.userMessage(for: error))",
                        "The queue could not be cleared. \(error.localizedDescription)"
                    )
                }
            }
        }
    }

    func clearFollowUpQueue(
        workspaceID: String,
        runID: String,
        machineID explicitMachineID: String? = nil
    ) {
        let machineID = explicitMachineID ?? currentMachineContextID
        let key = machineRunContextKey(
            workspaceID: workspaceID,
            runID: runID,
            machineID: machineID
        )
        if persistedFollowUpQueues.removeValue(forKey: key) != nil {
            self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        }
        if daemonOwnsFollowUpQueue(machineID: machineID) {
            Task { [weak self] in
                guard let self else { return }
                _ = try? await self.clearDaemonFollowUpQueue(
                    workspaceID: workspaceID,
                    runID: runID,
                    machineID: machineID
                )
            }
        }
        if isCurrentMachine(machineID),
           selectedWorkspaceID == workspaceID,
           activeRun?.runId == runID {
            clearFollowUpQueue(persist: false)
        }
    }

    func persistFollowUpQueue() {
        guard let workspaceID = selectedWorkspaceID,
              let runID = followUpRunID ?? activeRun?.runId,
              !followUpQueue.isEmpty else { return }
        let queue = PersistedFollowUpQueue(
            machineID: currentMachineContextID,
            workspaceID: workspaceID,
            runID: runID,
            items: followUpQueue,
            deadline: followUpDeadline
        )
        persistedFollowUpQueues = persistedFollowUpQueues.filter {
            MachineContextKey.normalizeMachineID($0.value.machineID) != currentMachineContextID
                || $0.value.workspaceID != workspaceID
                || $0.key == queue.contextKey
                || $0.value.items.contains(where: \.needsDaemonAcknowledgement)
        }
        persistedFollowUpQueues[queue.contextKey] = queue
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        if followUpQueueOwnershipIsKnown(), !daemonOwnsFollowUpQueue() {
            ensureFollowUpCoordinator()
        }
    }

    func restoreCachedFollowUpQueue(workspaceID: String, runID: String?) {
        guard selectedWorkspaceID == workspaceID else { return }
        let machineID = currentMachineContextID
        let workspaceKey = machineWorkspaceContextKey(workspaceID: workspaceID, machineID: machineID)
        let runIDs = Set((selectedConversation?.runs.map(\.runId) ?? []) + [runID].compactMap { $0 })
        let ownerIDs = Set([selectedConversationID, activeRun?.conversationKey].compactMap { $0 })
        let observedRemote = daemonFollowUpSnapshotsByWorkspace[workspaceKey]
        let remote = observedRemote.flatMap {
            $0.belongsToConversation(ownerIDs: ownerIDs, runIDs: runIDs) ? $0 : nil
        }
        let queueRunID = remote?.runId ?? runID
        let saved = queueRunID.flatMap {
            persistedFollowUpQueues[machineRunContextKey(workspaceID: workspaceID, runID: $0, machineID: machineID)]
        }
        let items: [QueuedFollowUp]
        if let remote {
            let pending = persistedFollowUpQueues.values.filter {
                MachineContextKey.normalizeMachineID($0.machineID) == machineID
                    && $0.workspaceID == workspaceID && runIDs.contains($0.runID)
            }.flatMap(\.items)
            items = FollowUpQueueReconciliation.merge(remote: remote.items.compactMap { $0.localValue() },
                local: pending, completedID: remote.lastCompletedItemId)
        } else if observedRemote != nil {
            // A confirmed queue owned by another conversation cannot lend its
            // acknowledged items to this Run, even if an old ID was reused.
            items = saved?.items.filter(\.needsDaemonAcknowledgement) ?? []
        } else {
            items = saved?.items ?? []
        }
        if followUpQueue != items { followUpQueue = items }
        if daemonFollowUpSnapshot != remote { daemonFollowUpSnapshot = remote }
        followUpRunID = remote?.runId ?? (items.isEmpty ? nil : queueRunID)
        followUpDeadline = items.isEmpty ? nil : remote?.deadlineEpochMs.map {
            Date(timeIntervalSince1970: TimeInterval($0) / 1_000)
        } ?? saved?.deadline
        let seconds = followUpDeadline.map { max(0, Int(ceil($0.timeIntervalSinceNow))) }
        if followUpSecondsRemaining != seconds { followUpSecondsRemaining = seconds }
    }

    func restoreFollowUpQueue(workspaceID: String, runID: String?) {
        restoreCachedFollowUpQueue(workspaceID: workspaceID, runID: runID)
        if daemonOwnsFollowUpQueue() {
            let machineID = currentMachineContextID
            let navigationRequestID = conversationNavigationRequestID
            Task { [weak self] in
                guard let self else { return }
                let remoteRunID = await self.refreshDaemonFollowUpQueue(
                    workspaceID: workspaceID,
                    runID: runID,
                    machineID: machineID,
                    migrateLocalQueue: false
                )
                guard self.isCurrentMachine(machineID), self.selectedWorkspaceID == workspaceID,
                      self.conversationNavigationRequestID == navigationRequestID else { return }
                if let runID,
                   let remoteRunID,
                   runID != remoteRunID {
                    await self.refresh(reportErrors: false)
                }
            }
        }
    }

    private func followUpQueueContextKey(
        workspaceID: String? = nil,
        runID: String? = nil
    ) -> String? {
        guard let workspaceID = workspaceID ?? selectedWorkspaceID,
              let runID = runID ?? followUpRunID ?? activeRun?.runId else {
            return nil
        }
        return machineRunContextKey(workspaceID: workspaceID, runID: runID)
    }

    func forgetDesktopState(
        for workspaceID: String,
        machineID explicitMachineID: String? = nil
    ) {
        let machineID = explicitMachineID ?? currentMachineContextID
        promptDraftsByContext = promptDraftsByContext.filter {
            !MachineContextKey.belongsToWorkspace(
                $0.key,
                machineID: machineID,
                workspaceID: workspaceID
            )
        }
        self.desktopStateStore.savePromptDrafts(promptDraftsByContext)
        let previousCount = persistedFollowUpQueues.count
        persistedFollowUpQueues = persistedFollowUpQueues.filter {
            MachineContextKey.normalizeMachineID($0.value.machineID) != machineID
                || $0.value.workspaceID != workspaceID
        }
        if persistedFollowUpQueues.count != previousCount {
            self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        }
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        promptOutboxByWorkspace.removeValue(forKey: workspaceKey)
        acceptedPromptResultsByWorkspace.removeValue(forKey: workspaceKey)
        deferredPromptsByWorkspace.removeValue(forKey: workspaceKey)
        deferredPromptTasks.removeValue(forKey: workspaceKey)?.cancel()
        acceptedPromptSettlementTasks.removeValue(forKey: workspaceKey)?.cancel()
        desktopStateStore.forgetPromptDelivery(workspaceKey: workspaceKey)
        let selectedAgentCount = selectedAgentsByContext.count
        selectedAgentsByContext = selectedAgentsByContext.filter {
            !MachineContextKey.belongsToWorkspace(
                $0.key,
                machineID: machineID,
                workspaceID: workspaceID
            )
        }
        if selectedAgentsByContext.count != selectedAgentCount {
            selectedAgentContextsWhileWorking = selectedAgentContextsWhileWorking.filter {
                selectedAgentsByContext[$0] != nil
            }
            persistAgentSelections()
        }
    }

    func selectedAgentContextKey(
        workspaceID: String? = nil,
        runID: String? = nil
    ) -> String? {
        guard let workspaceID = workspaceID ?? selectedWorkspaceID,
              let runID = runID ?? activeRun?.runId else { return nil }
        return machineRunContextKey(workspaceID: workspaceID, runID: runID)
    }

    func rememberSelectedAgent(_ agent: Int, selectedWhileWorking: Bool? = nil) {
        guard let key = selectedAgentContextKey() else { return }
        selectedAgentsByContext[key] = agent
        if selectedWhileWorking ?? (activeRun?.isWorking == true) {
            selectedAgentContextsWhileWorking.insert(key)
        } else {
            selectedAgentContextsWhileWorking.remove(key)
        }
        persistAgentSelections()
    }

    func rememberedSelectedAgent(
        for run: RunSummary,
        contextKey key: String
    ) -> Int? {
        if !run.isWorking, selectedAgentContextsWhileWorking.remove(key) != nil {
            selectedAgentsByContext.removeValue(forKey: key)
            persistAgentSelections()
        }
        // A queue source is a default, not permission to replace a reading choice.
        if let selected = selectedAgentsByContext[key] { return selected }
        if !run.isWorking, run.finalizedAgent == nil, pendingAgentSelection == nil,
           key == selectedAgentContextKey(runID: run.runId),
           let remote = daemonFollowUpSnapshot, remote.runId == run.runId,
           remote.workspaceId == selectedWorkspaceID,
           ["manual", "automatic"].contains(remote.selectionSource ?? "") {
            return remote.selectedAgent
        }
        return selectedAgentsByContext[key]
    }

    func forgetRememberedSelectedAgent(
        workspaceID: String,
        runID: String,
        machineID explicitMachineID: String? = nil
    ) {
        let key = machineRunContextKey(
            workspaceID: workspaceID,
            runID: runID,
            machineID: explicitMachineID ?? currentMachineContextID
        )
        let removedAgent = selectedAgentsByContext.removeValue(forKey: key) != nil
        let removedPhase = selectedAgentContextsWhileWorking.remove(key) != nil
        guard removedAgent || removedPhase else { return }
        persistAgentSelections()
    }

    private func persistAgentSelections() {
        self.desktopStateStore.saveAgentSelections(
            selectedAgentsByContext,
            selectedWhileWorking: selectedAgentContextsWhileWorking
        )
    }

    private func cancelFollowUpCountdown() {
        followUpTask?.cancel()
        followUpTask = nil
        daemonFollowUpMonitorToken = nil
        followUpSecondsRemaining = nil
    }

    func pauseFollowUpForAgentSelection() {
        cancelFollowUpCountdown()
        followUpDeadline = nil
        persistFollowUpQueue()
    }

    func reconcileFollowUpQueue(
        for run: RunSummary,
        resetCountdown: Bool
    ) async {
        guard !followUpQueue.isEmpty, activeRun?.runId == run.runId else {
            cancelFollowUpCountdown()
            return
        }
        if queuePresented && !daemonOwnsFollowUpQueue() {
            cancelFollowUpCountdown()
            return
        }
        if followUpQueue.first?.isPaused == true {
            cancelFollowUpCountdown()
            return
        }
        followUpRunID = run.runId
        let machineID = currentMachineContextID
        if continuationSelectionNeedsConfirmation {
            cancelFollowUpCountdown()
            if daemonOwnsFollowUpQueue(machineID: machineID), let workspaceID = selectedWorkspaceID {
                ensureDaemonFollowUpMonitor(workspaceID: workspaceID, machineID: machineID)
            }
            return
        }
        if daemonOwnsFollowUpQueue(machineID: machineID),
           let workspaceID = selectedWorkspaceID {
            if resetCountdown {
                do {
                    let response = try await replaceDaemonFollowUpQueue(
                        workspaceID: workspaceID,
                        runID: run.runId,
                        items: followUpQueue,
                        machineID: machineID,
                        resetCountdown: true
                    )
                    applyDaemonFollowUpQueue(
                        response.queue,
                        workspaceID: workspaceID,
                        machineID: machineID,
                        revision: response.revision
                    )
                } catch {
                    // Run polling will reconcile this queue again. Avoid showing
                    // a false continuation error while finalization is in flight.
                }
            } else {
                let remoteRunID = await refreshDaemonFollowUpQueue(
                    workspaceID: workspaceID,
                    runID: run.runId,
                    machineID: machineID
                )
                if let remoteRunID, remoteRunID != run.runId {
                    await refresh(reportErrors: false)
                }
            }
            ensureDaemonFollowUpMonitor(
                workspaceID: workspaceID,
                machineID: machineID
            )
            return
        }
        // Capability discovery happens before the workspace finishes loading.
        // Never let the legacy in-app executor race the daemon during that gap.
        guard followUpQueueOwnershipIsKnown(machineID: machineID) else { return }
        persistFollowUpQueue()
        if run.isWorking {
            cancelFollowUpCountdown()
            if followUpDeadline != nil {
                followUpDeadline = nil
                persistFollowUpQueue()
            }
            return
        }
        if run.hasUnresolvedFinalization {
            cancelFollowUpCountdown()
            return
        }
        if pendingAgentSelection != nil {
            cancelFollowUpCountdown()
            return
        }
        if run.finalizedAgent != nil {
            cancelFollowUpCountdown()
            await dispatchFollowUp()
            return
        }

        let current = selectedAgent.flatMap { selected in
            run.agents.first { $0.agent == selected && $0.mayContinue }
        }
        let recommended = run.recommendedAgent.flatMap { recommended in
            run.agents.first { $0.agent == recommended && $0.mayContinue }
        }
        guard let source = current ?? recommended ?? run.agents.first(where: \.mayContinue) else {
            cancelFollowUpCountdown()
            return
        }
        if selectedAgent != source.agent {
            await selectAgent(source.agent, manageFollowUpQueue: false)
            guard selectedAgent == source.agent else {
                cancelFollowUpCountdown()
                return
            }
        }
        if resetCountdown {
            startFollowUpCountdown(runID: run.runId)
        } else if followUpTask == nil {
            startFollowUpCountdown(runID: run.runId, resumeExisting: true)
        }
    }

    private func startFollowUpCountdown(
        runID: String,
        minimumDelay: Int = 0,
        resumeExisting: Bool = false
    ) {
        cancelFollowUpCountdown()
        let configuredDelay = followUpQueue.first?.settings?.followUpDelay
            ?? settings.followUpDelay
        let delay = max(minimumDelay, configuredDelay)
        if let retryAt = followUpQueue.first?.retryAt, retryAt > Date() {
            followUpDeadline = retryAt
            persistFollowUpQueue()
        } else if !resumeExisting || followUpDeadline == nil {
            followUpDeadline = Date().addingTimeInterval(TimeInterval(delay))
            persistFollowUpQueue()
        }
        guard let deadline = followUpDeadline else { return }
        let initialRemaining = max(0, Int(ceil(deadline.timeIntervalSinceNow)))
        if initialRemaining == 0 {
            followUpTask = Task { [weak self] in
                await self?.dispatchFollowUp()
            }
            return
        }
        followUpSecondsRemaining = initialRemaining
        followUpTask = Task { [weak self] in
            guard let self else { return }
            while true {
                do {
                    try await Task.sleep(for: .seconds(1))
                } catch {
                    return
                }
                guard self.activeRun?.runId == runID,
                      self.followUpRunID == runID,
                      !self.followUpQueue.isEmpty else { return }
                let remaining = max(0, Int(ceil(deadline.timeIntervalSinceNow)))
                self.followUpSecondsRemaining = remaining
                if remaining == 0 { break }
            }
            await self.dispatchFollowUp()
        }
    }

    func dispatchFollowUp() async {
        guard let item = followUpQueue.first,
              let run = activeRun,
              let workspaceID = selectedWorkspaceID,
              !run.isWorking,
              !run.hasUnresolvedFinalization,
              !item.isPaused,
              item.retryAt.map({ $0 <= Date() }) ?? true,
              !isRunOperationInProgress else { return }
        guard !continuationSelectionNeedsConfirmation else { return }
        let machineID = currentMachineContextID
        switch FollowUpDispatchRoute.resolve(
            ownershipKnown: followUpQueueOwnershipIsKnown(machineID: machineID),
            daemonOwnsQueue: daemonOwnsFollowUpQueue(machineID: machineID)
        ) {
        case .daemon:
            // Finalization only makes the daemon-owned queue eligible. Running
            // the legacy executor here as well can start the same prompt twice.
            cancelFollowUpCountdown()
            let remoteRunID = await refreshDaemonFollowUpQueue(
                workspaceID: workspaceID,
                runID: run.runId,
                machineID: machineID
            )
            ensureDaemonFollowUpMonitor(
                workspaceID: workspaceID,
                machineID: machineID
            )
            if let remoteRunID, remoteRunID != run.runId {
                await refresh(reportErrors: false)
            }
            return
        case .waitForCapabilities:
            // Capability discovery is asynchronous. Waiting is safer than
            // racing a daemon that may already own this queue.
            cancelFollowUpCountdown()
            return
        case .legacyClient:
            break
        }
        let queueKey = machineRunContextKey(
            workspaceID: workspaceID,
            runID: run.runId,
            machineID: machineID
        )
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        guard let target = requestTarget(for: machineID),
              backgroundFollowUpKeys.insert(queueKey).inserted,
              activeWorkspaceOperationIDs.insert(workspaceKey).inserted else {
            backgroundFollowUpKeys.remove(queueKey)
            return
        }
        let context = BackgroundFollowUpContext(
            key: queueKey,
            machineID: machineID,
            target: target,
            workspaceID: workspaceID,
            runID: run.runId
        )
        cancelFollowUpCountdown()
        followUpDeadline = nil
        persistFollowUpQueue()
        refreshVisibleRunOperationState()
        defer {
            backgroundFollowUpKeys.remove(queueKey)
            activeWorkspaceOperationIDs.remove(workspaceKey)
            refreshVisibleRunOperationState()
        }
        await processBackgroundFollowUp(
            context,
            forceReady: true
        )

        guard isCurrentMachine(machineID),
              selectedWorkspaceID == workspaceID else { return }
        let latest = persistedFollowUpQueues.values.first {
            MachineContextKey.normalizeMachineID($0.machineID) == machineID
                && $0.workspaceID == workspaceID
                && !$0.items.isEmpty
        }
        guard let latest else {
            clearFollowUpQueue(persist: false)
            return
        }
        followUpQueue = latest.items
        followUpRunID = latest.runID
        followUpDeadline = latest.deadline
        if latest.items.first?.isPaused == true {
            errorMessage = appLanguage.text(
                "排队消息多次发送失败，已暂停。请打开队列检查或重试。",
                "A queued follow-up was paused after repeated failures. Open the queue to review or retry it."
            )
        } else if let run = activeRun, run.runId == latest.runID {
            await reconcileFollowUpQueue(for: run, resetCountdown: false)
        }
    }

    private func followUpRetryDelay(_ failureCount: Int) -> TimeInterval {
        let exponent = min(max(1, failureCount) - 1, 6)
        return min(300, 5 * pow(2, Double(exponent)))
    }

    func followUpContinuationSource(
        in run: RunSummary,
        rememberedAgent: Int?
    ) -> RunAgentSummary? {
        let eligible = run.agents.filter(\.mayContinue)
        if let finalized = run.finalizedAgent {
            return eligible.first { $0.agent == finalized }
        }
        let remembered = rememberedAgent.flatMap { selected in
            eligible.first { $0.agent == selected }
        }
        let recommended = (run.continuationAgent ?? run.recommendedAgent).flatMap { selected in
            eligible.first { $0.agent == selected }
        } ?? eligible.first { $0.recommended }
        return remembered ?? recommended ?? eligible.first
    }

    func ensureFollowUpCoordinator() {
        let hasLegacyQueue = persistedFollowUpQueues.values.contains {
            followUpQueueOwnershipIsKnown(machineID: $0.machineID)
                && !daemonOwnsFollowUpQueue(machineID: $0.machineID)
        }
        guard hasLegacyQueue,
              followUpCoordinatorTask?.isCancelled != false else { return }
        followUpCoordinatorTask = Task { [weak self] in
            defer {
                if let self {
                    self.followUpCoordinatorTask = nil
                    if self.persistedFollowUpQueues.values.contains(where: {
                        self.followUpQueueOwnershipIsKnown(machineID: $0.machineID)
                            && !self.daemonOwnsFollowUpQueue(machineID: $0.machineID)
                    }) {
                        self.ensureFollowUpCoordinator()
                    }
                }
            }
            while !Task.isCancelled {
                guard let self, !self.persistedFollowUpQueues.isEmpty else { return }
                let visibleKey = self.followUpQueueContextKey()
                let contexts: [BackgroundFollowUpContext] = self
                    .persistedFollowUpQueues.values.compactMap { queue in
                        let machineID = MachineContextKey.normalizeMachineID(queue.machineID)
                        guard self.followUpQueueOwnershipIsKnown(machineID: machineID),
                              !self.daemonOwnsFollowUpQueue(machineID: machineID) else {
                            return nil
                        }
                        guard let target = self.requestTarget(for: machineID) else { return nil }
                        let workspaceKey = self.machineWorkspaceContextKey(
                            workspaceID: queue.workspaceID,
                            machineID: machineID
                        )
                        guard !queue.items.isEmpty,
                              queue.contextKey != visibleKey,
                              !self.backgroundFollowUpKeys.contains(queue.contextKey),
                              !self.activeWorkspaceOperationIDs.contains(workspaceKey),
                              !self.activeRunOperationKeys.contains(where: {
                                  MachineContextKey.belongsToWorkspace(
                                      $0,
                                      machineID: machineID,
                                      workspaceID: queue.workspaceID
                                  )
                              }) else { return nil }
                        return BackgroundFollowUpContext(
                            key: queue.contextKey,
                            machineID: machineID,
                            target: target,
                            workspaceID: queue.workspaceID,
                            runID: queue.runID
                        )
                    }
                for context in contexts {
                    let workspaceKey = self.machineWorkspaceContextKey(
                        workspaceID: context.workspaceID,
                        machineID: context.machineID
                    )
                    guard self.backgroundFollowUpKeys.insert(context.key).inserted,
                          self.activeWorkspaceOperationIDs.insert(
                              workspaceKey
                          ).inserted else {
                        self.backgroundFollowUpKeys.remove(context.key)
                        continue
                    }
                    self.refreshVisibleRunOperationState()
                    Task { [weak self] in
                        guard let self else { return }
                        defer {
                            self.backgroundFollowUpKeys.remove(context.key)
                            self.activeWorkspaceOperationIDs.remove(workspaceKey)
                            self.refreshVisibleRunOperationState()
                        }
                        await self.processBackgroundFollowUp(context)
                    }
                }
                do {
                    try await Task.sleep(for: .seconds(5))
                } catch {
                    return
                }
            }
        }
    }

    private func processBackgroundFollowUp(
        _ context: BackgroundFollowUpContext,
        forceReady: Bool = false
    ) async {
        guard var queue = persistedFollowUpQueues[context.key],
              var item = queue.items.first,
              !item.isPaused,
              item.retryAt.map({ $0 <= Date() }) ?? true,
              let workspace = await backgroundWorkspace(for: context),
              !workspace.lastRunId.isEmpty else { return }

        if workspace.lastRunId != context.runID, item.dispatch == nil {
            migrateBackgroundFollowUpQueue(
                queue,
                toRunID: workspace.lastRunId
            )
            return
        }

        let run: RunSummary
        do {
            run = try await client.call(
                target: context.target,
                "turns.get",
                body: GetRunBody(
                    runId: context.runID,
                    includeEvents: false,
                    cursor: 0,
                    eventLimit: 1
                )
            )
        } catch {
            return
        }
        guard !run.isWorking, !run.hasUnresolvedFinalization else { return }

        let dispatch: QueuedFollowUpDispatch
        if let saved = item.dispatch {
            var scoped = saved
            if scoped.machineID == nil {
                scoped.machineID = context.machineID
            }
            if scoped.mode == .start, run.finalizedAgent != nil {
                guard let source = followUpContinuationSource(
                    in: run,
                    rememberedAgent: nil
                ) else { return }
                scoped = QueuedFollowUpDispatch(
                    mode: .continuation,
                    workspaceID: context.workspaceID,
                    runID: context.runID,
                    agent: source.agent,
                    settings: scoped.settings,
                    machineID: context.machineID
                )
            }
            item.dispatch = scoped
            queue = replacingFirstItem(in: queue, with: item)
            saveBackgroundFollowUpQueue(queue)
            dispatch = scoped
        } else {
            let runSettings = item.settings ?? backgroundSettings(
                for: workspace,
                machineID: context.machineID
            )
            let rememberedAgent = rememberedSelectedAgent(
                for: run,
                contextKey: context.key
            )
            guard let source = followUpContinuationSource(
                in: run,
                rememberedAgent: rememberedAgent
            ) else { return }
            dispatch = QueuedFollowUpDispatch(
                mode: .continuation,
                workspaceID: context.workspaceID,
                runID: context.runID,
                agent: source.agent,
                settings: runSettings,
                machineID: context.machineID
            )
            item.dispatch = dispatch
            queue = replacingFirstItem(in: queue, with: item)
            saveBackgroundFollowUpQueue(queue)
        }

        let now = Date()
        if !forceReady {
            if queue.deadline == nil {
                queue.deadline = now.addingTimeInterval(
                    TimeInterval(max(0, dispatch.settings.followUpDelay))
                )
                saveBackgroundFollowUpQueue(queue)
            }
            guard let deadline = queue.deadline, deadline <= now else { return }
        }

        queue.deadline = nil
        saveBackgroundFollowUpQueue(queue)
        let carriedHistory: [ResumeHistoryEntry]? = if
            isCurrentMachine(context.machineID),
            selectedWorkspaceID == context.workspaceID,
            activeRun?.runId == context.runID
        {
            conversationHistory(for: dispatch.agent)
        } else {
            nil
        }
        do {
            let response: RunStartResponse
            switch dispatch.mode {
            case .start:
                response = try await requestStartRun(
                    workspaceID: dispatch.workspaceID,
                    prompt: item.prompt,
                    settings: dispatch.settings,
                    confirmLargeRun: true,
                    commandID: item.commandID,
                    target: context.target
                )
            case .continuation:
                guard let runID = dispatch.runID,
                      let agent = dispatch.agent else { return }
                response = try await requestContinuation(
                    runID: runID,
                    agent: agent,
                    prompt: item.prompt,
                    settings: dispatch.settings,
                    confirmLargeRun: true,
                    commandID: item.commandID,
                    target: context.target
                )
            }
            guard let nextRunID = response.runId ?? response.run?.runId else {
                throw PcrdClientError.invalidResponse
            }
            completeBackgroundFollowUp(
                queue,
                itemID: item.id,
                nextRunID: nextRunID
            )
            if let carriedHistory,
               isCurrentMachine(context.machineID),
               selectedWorkspaceID == context.workspaceID,
               activeRun?.runId == context.runID {
                restoreFollowUpQueue(
                    workspaceID: context.workspaceID,
                    runID: nextRunID
                )
                do {
                    try await activateStartedRun(
                        response,
                        expectedWorkspaceID: context.workspaceID,
                        target: context.target,
                        carriedHistory: carriedHistory
                    )
                } catch {
                    if isCurrentMachine(context.machineID),
                       selectedWorkspaceID == context.workspaceID {
                        errorMessage = appLanguage.text(
                            "排队任务已启动，但暂时无法打开实时对话。请刷新后重新连接。"
                                + appLanguage.userMessage(for: error),
                            "The queued task started, but its live conversation could not be opened yet. "
                                + "Refresh to reconnect. \(error.localizedDescription)"
                        )
                    }
                }
            }
            await recordPromptHistory(
                item.prompt,
                workspaceID: context.workspaceID,
                sessionID: response.continuedFrom?.sessionId
                    ?? dispatch.settings.resumeSessionID,
                target: context.target
            )
            if isCurrentMachine(context.machineID),
               selectedWorkspaceID == context.workspaceID {
                await refresh(reportErrors: false)
            }
        } catch {
            guard var latest = persistedFollowUpQueues[context.key],
                  var failed = latest.items.first,
                  failed.id == item.id else { return }
            let retryable = PromptSubmissionRetryPolicy.shouldRetry(error)
            let failures = max(0, failed.failureCount ?? 0) + 1
            failed.failureCount = failures
            failed.lastError = error.localizedDescription
            failed.paused = !retryable
            failed.retryAt = retryable
                ? Date().addingTimeInterval(followUpRetryDelay(failures))
                : nil
            latest = replacingFirstItem(in: latest, with: failed)
            latest.deadline = failed.retryAt
            saveBackgroundFollowUpQueue(latest)
        }
    }

    private func backgroundSettings(for workspace: RemoteWorkspace) -> RunSettings {
        backgroundSettings(for: workspace, machineID: currentMachineContextID)
    }

    private func backgroundWorkspace(
        for context: BackgroundFollowUpContext
    ) async -> RemoteWorkspace? {
        if isCurrentMachine(context.machineID) {
            return workspaces.first { $0.workspaceId == context.workspaceID }
        }
        do {
            let health: HealthResponse = try await client.call(
                target: context.target,
                "system.health",
                body: EmptyBody()
            )
            return health.workspaceRegistry.workspaces.first {
                $0.workspaceId == context.workspaceID
            }
        } catch {
            return nil
        }
    }

    func backgroundSettings(
        for workspace: RemoteWorkspace,
        machineID: String
    ) -> RunSettings {
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspace.workspaceId,
            machineID: machineID
        )
        let cached = settingsByWorkspace[workspaceKey]
        var value = cached ?? RunSettings()
        if let remote = workspace.settings, remote.configured {
            if let cached {
                value = cached.merging(
                    remote: remote,
                    preserving: dirtySettingsFieldsByWorkspace[workspaceKey] ?? []
                )
            } else {
                value.apply(remote)
            }
        }
        if value.resumeSessionID.isEmpty,
           !workspace.lastResumeSessionId.isEmpty {
            value.resumeSessionID = workspace.lastResumeSessionId
        }
        return value
    }

    private func replacingFirstItem(
        in queue: PersistedFollowUpQueue,
        with item: QueuedFollowUp
    ) -> PersistedFollowUpQueue {
        PersistedFollowUpQueue(
            machineID: queue.machineID,
            workspaceID: queue.workspaceID,
            runID: queue.runID,
            items: [item] + queue.items.dropFirst(),
            deadline: queue.deadline
        )
    }

    private func saveBackgroundFollowUpQueue(_ queue: PersistedFollowUpQueue) {
        persistedFollowUpQueues[queue.contextKey] = queue
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
    }

    private func migrateBackgroundFollowUpQueue(
        _ queue: PersistedFollowUpQueue,
        toRunID runID: String
    ) {
        let nextKey = machineRunContextKey(
            workspaceID: queue.workspaceID,
            runID: runID,
            machineID: queue.machineID
        )
        let existing = persistedFollowUpQueues[nextKey]
        let queuedIDs = Set(queue.items.map(\.id))
        let merged = queue.items + (existing?.items ?? []).filter {
            !queuedIDs.contains($0.id)
        }
        persistedFollowUpQueues.removeValue(forKey: queue.contextKey)
        persistedFollowUpQueues[nextKey] = PersistedFollowUpQueue(
            machineID: queue.machineID,
            workspaceID: queue.workspaceID,
            runID: runID,
            items: merged,
            deadline: queue.deadline ?? existing?.deadline
        )
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
    }

    private func completeBackgroundFollowUp(
        _ queue: PersistedFollowUpQueue,
        itemID: UUID,
        nextRunID: String
    ) {
        guard let latest = persistedFollowUpQueues[queue.contextKey],
              latest.items.first?.id == itemID else { return }
        let remaining = Array(latest.items.dropFirst())
        persistedFollowUpQueues.removeValue(forKey: queue.contextKey)
        if !remaining.isEmpty {
            let nextKey = machineRunContextKey(
                workspaceID: queue.workspaceID,
                runID: nextRunID,
                machineID: queue.machineID
            )
            let existing = persistedFollowUpQueues[nextKey]
            let remainingIDs = Set(remaining.map(\.id))
            let merged = remaining + (existing?.items ?? []).filter {
                !remainingIDs.contains($0.id)
            }
            persistedFollowUpQueues[nextKey] = PersistedFollowUpQueue(
                machineID: queue.machineID,
                workspaceID: queue.workspaceID,
                runID: nextRunID,
                items: merged,
                deadline: existing?.deadline
            )
        }
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
    }

}
