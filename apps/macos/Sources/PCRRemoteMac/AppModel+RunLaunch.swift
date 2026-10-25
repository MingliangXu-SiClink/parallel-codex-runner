import Foundation

func acceptedPromptDestinationConflict(
    _ run: RunSummary,
    result: PersistedAcceptedPromptResult,
    workspacePath: String?,
    response: RunStartResponse? = nil
) -> String? {
    let submission = result.submission
    if run.runId != result.runID { return "run_id" }
    if run.prompt != submission.prompt { return "prompt" }
    guard let workspacePath, sameConversationWorkspace(run.workspace, workspacePath) else { return "workspace_path" }
    if let id = response?.workspaceId, id != submission.workspaceID { return "workspace_id" }
    for snapshot in [run.conversationSnapshot, response?.conversationSnapshot].compactMap({ $0 }) {
        if snapshot.workspace.workspaceId != submission.workspaceID { return "workspace_id" }
    }
    if let command = run.config.submissionCommand,
       command.commandId != result.commandID || command.clientId != PcrdClient.clientID { return "command" }

    switch (submission.sourceRunID, submission.sourceAgent) {
    case (let sourceRun?, let sourceAgent?):
        if run.continuedFromRunId != sourceRun { return "source_run" }
        if run.continuedFromAgent != sourceAgent { return "source_agent" }
    case (nil, nil):
        // turns.start may resume a saved session. pcrd resolves its canonical
        // parent; absent explicit source IDs do not mean "must have no parent".
        let session = usableResumeSessionID(submission.settings.resumeSessionID)
        if session != usableResumeSessionID(submission.resumeSessionID) { return "request_resume_session" }
        if usableResumeSessionID(run.config.resumeSessionId ?? "") != session { return "resume_session" }
        if session != nil {
            if (run.continuedFromRunId == nil) != (run.continuedFromAgent == nil) { return "session_parent" }
        } else if run.continuedFromRunId != nil || run.continuedFromAgent != nil {
            return "unexpected_source"
        }
    default:
        return "incomplete_source"
    }
    return nil
}

@MainActor
extension AppModel {
    func makeRunStartBody(
        workspaceID: String,
        prompt: String,
        settings: RunSettings,
        confirmLargeRun: Bool
    ) -> RunStartBody {
        RunStartBody(
                workspaceId: workspaceID,
                prompt: prompt,
                numAgents: max(1, settings.agents),
                maxParallel: settings.execution == .serial
                    ? 1
                    : (settings.automaticMaxParallel ? nil : max(1, settings.maxParallel)),
                serial: settings.execution == .serial,
                recommendBy: settings.recommendation.rawValue,
                model: settings.model.isEmpty ? nil : settings.model,
                effort: settings.effort.isEmpty ? nil : settings.effort,
                resumeSessionId: usableResumeSessionID(settings.resumeSessionID),
                syncBack: settings.syncBack,
                keepWorkspaces: settings.keepWorkspaces,
                confirmLargeRun: confirmLargeRun,
                fast: settings.fast.requestValue,
                synthesisAgents: max(0, settings.synthesisAgents),
                followUpDelay: max(0, settings.followUpDelay),
                synthesisModel: settings.synthesisModel.isEmpty
                    ? nil : settings.synthesisModel,
                synthesisEffort: settings.synthesisEffort.isEmpty
                    ? nil : settings.synthesisEffort,
                synthesisFast: settings.synthesisFast.requestValue,
                subagents: settings.subagents,
                subagentsLimit: max(1, settings.subagentsLimit),
                runsDir: settings.runsDirectory.isEmpty ? nil : settings.runsDirectory,
                codexBin: settings.codexExecutable.isEmpty ? "codex" : settings.codexExecutable
            )
    }

    func requestStartRun(
        workspaceID: String, prompt: String, settings: RunSettings,
        confirmLargeRun: Bool, commandID: String? = nil,
        target: PcrdRequestTarget? = nil
    ) async throws -> RunStartResponse {
        try await client.call(
            target: target ?? client.currentTarget(), "turns.start",
            body: makeRunStartBody(workspaceID: workspaceID, prompt: prompt,
                settings: settings, confirmLargeRun: confirmLargeRun),
            timeoutSeconds: 120, commandID: commandID
        )
    }

    func makeRunContinueBody(
        runID: String,
        agent: Int,
        prompt: String,
        settings: RunSettings,
        confirmLargeRun: Bool
    ) -> RunContinueBody {
        RunContinueBody(
                runId: runID,
                agent: agent,
                prompt: prompt,
                numAgents: max(1, settings.agents),
                maxParallel: settings.execution == .serial
                    ? 1
                    : (settings.automaticMaxParallel ? nil : max(1, settings.maxParallel)),
                automaticMaxParallel: settings.automaticMaxParallel,
                serial: settings.execution == .serial,
                recommendBy: settings.recommendation.rawValue,
                model: settings.model,
                effort: settings.effort,
                fastMode: settings.fast.rawValue,
                synthesisAgents: max(0, settings.synthesisAgents),
                followUpDelay: max(0, settings.followUpDelay),
                synthesisModel: settings.synthesisModel,
                synthesisEffort: settings.synthesisEffort,
                synthesisFastMode: settings.synthesisFast.rawValue,
                syncBack: settings.syncBack,
                keepWorkspaces: settings.keepWorkspaces,
                subagents: settings.subagents,
                subagentsLimit: max(1, settings.subagentsLimit),
                runsDir: settings.runsDirectory,
                codexBin: settings.codexExecutable.isEmpty ? "codex" : settings.codexExecutable,
                confirmLargeRun: confirmLargeRun
            )
    }

    func requestContinuation(
        runID: String, agent: Int, prompt: String, settings: RunSettings,
        confirmLargeRun: Bool, commandID: String? = nil,
        target: PcrdRequestTarget? = nil
    ) async throws -> RunStartResponse {
        try await client.call(
            target: target ?? client.currentTarget(), "turns.continue",
            body: makeRunContinueBody(runID: runID, agent: agent, prompt: prompt,
                settings: settings, confirmLargeRun: confirmLargeRun),
            timeoutSeconds: 120, commandID: commandID
        )
    }

    func promptCommandReceipt(
        _ submission: PersistedPromptSubmission, target: PcrdRequestTarget
    ) async throws -> PromptCommandReceipt {
        let receipt: PromptCommandReceipt
        if let runID = submission.sourceRunID, let agent = submission.sourceAgent {
            receipt = try await client.call(target: target, "commands.get",
                body: PromptCommandReceiptBody(commandId: submission.commandID,
                    action: "turns.continue", body: makeRunContinueBody(runID: runID,
                        agent: agent, prompt: submission.prompt, settings: submission.settings,
                        confirmLargeRun: submission.confirmLargeRun)), timeoutSeconds: 30)
        } else {
            receipt = try await client.call(target: target, "commands.get",
                body: PromptCommandReceiptBody(commandId: submission.commandID,
                    action: "turns.start", body: makeRunStartBody(workspaceID: submission.workspaceID,
                        prompt: submission.prompt, settings: submission.settings,
                        confirmLargeRun: submission.confirmLargeRun)), timeoutSeconds: 30)
        }
        guard receipt.commandId == submission.commandID else { throw PcrdClientError.invalidResponse }
        return receipt
    }

    func startRun(
        workspaceID: String,
        prompt: String,
        confirmLargeRun: Bool,
        commandID: String? = nil,
        settingsSnapshot: RunSettings? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async throws -> Bool {
        let target = explicitTarget ?? client.currentTarget()
        if settingsSnapshot == nil { rememberCurrentSettings() }
        let runSettings = settingsSnapshot ?? settings
        let carriedHistory = usableResumeSessionID(runSettings.resumeSessionID) == nil
            ? []
            : (activeRun == nil ? resumeHistory : currentConversationHistory())
        let response = try await requestStartRun(
            workspaceID: workspaceID,
            prompt: prompt,
            settings: runSettings,
            confirmLargeRun: confirmLargeRun,
            commandID: commandID,
            target: target
        )
        if response.status == "confirmation_required" {
            setPendingStorageOperation(
                .start(
                    workspaceID: workspaceID,
                    prompt: prompt,
                    settings: runSettings
                ),
                message: response.message ?? "This run needs more disk space than usual.",
                machineID: target.machineID
            )
            return false
        }
        clearPendingStorageOperation(workspaceID: workspaceID, machineID: target.machineID)
        guard target.machineID == currentMachineContextID,
              selectedWorkspaceID == workspaceID else {
            if target.machineID == currentMachineContextID {
                await refresh(reportErrors: false)
            }
            return true
        }
        if let revision = response.workspaceRevision { workspaceRevision = revision }
        try await activateStartedRun(
            response,
            expectedWorkspaceID: workspaceID,
            target: target,
            carriedHistory: carriedHistory
        )
        await refresh(reportErrors: false)
        return true
    }

    func continueRun(
        workspaceID: String,
        runID: String,
        agent: Int,
        prompt: String,
        confirmLargeRun: Bool,
        commandID: String? = nil,
        settingsSnapshot: RunSettings? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async throws -> Bool {
        let target = explicitTarget ?? client.currentTarget()
        let carriedHistory = conversationHistory(for: agent)
        let runSettings = settingsSnapshot ?? settings
        let response = try await requestContinuation(
            runID: runID,
            agent: agent,
            prompt: prompt,
            settings: runSettings,
            confirmLargeRun: confirmLargeRun,
            commandID: commandID,
            target: target
        )
        if response.status == "confirmation_required" {
            setPendingStorageOperation(
                .continuation(
                    workspaceID: workspaceID,
                    runID: runID,
                    agent: agent,
                    prompt: prompt,
                    settings: runSettings
                ),
                message: response.message ?? "This continuation needs more disk space than usual.",
                machineID: target.machineID
            )
            return false
        }
        clearPendingStorageOperation(workspaceID: workspaceID, machineID: target.machineID)
        guard target.machineID == currentMachineContextID,
              selectedWorkspaceID == workspaceID else {
            if target.machineID == currentMachineContextID {
                await refresh(reportErrors: false)
            }
            return true
        }
        if let revision = response.workspaceRevision { workspaceRevision = revision }
        if let sessionID = response.continuedFrom?.sessionId,
           !sessionID.isEmpty {
            settings.resumeSessionID = sessionID
            rememberCurrentSettings()
        }
        try await activateStartedRun(
            response,
            expectedWorkspaceID: workspaceID,
            target: target,
            carriedHistory: carriedHistory
        )
        await refresh(reportErrors: false)
        return true
    }

    @discardableResult
    func activateStartedRun(
        _ response: RunStartResponse,
        expectedWorkspaceID: String,
        target: PcrdRequestTarget,
        carriedHistory: [ResumeHistoryEntry]
    ) async throws -> Bool {
        guard let runID = response.runId ?? response.run?.runId else {
            throw PcrdClientError.invalidResponse
        }
        guard isCurrentMachine(target.machineID),
              selectedWorkspaceID == expectedWorkspaceID else { return false }
        let expectedConversationID = selectedConversationID
        let expectedNavigationRequestID = conversationNavigationRequestID
        var startedRun: RunSummary
        if let run = response.run {
            startedRun = run
        } else {
            startedRun = try await client.call(
                target: target,
                "turns.get",
                body: GetRunBody(
                    runId: runID,
                    includeEvents: false,
                    cursor: 0,
                    eventLimit: 1
                )
            )
        }
        guard startedRun.runId == runID else { throw PcrdClientError.invalidResponse }
        let key = WorkspaceRunViewKey(machineID: target.machineID,
            workspaceID: expectedWorkspaceID, runID: runID)
        let cached = await runViewStateStore.hydrate(for: key)
        if let snapshot = response.conversationSnapshot ?? startedRun.conversationSnapshot {
            domainRepositories.ingest(snapshot)
        }
        guard isCurrentMachine(target.machineID),
              selectedWorkspaceID == expectedWorkspaceID,
              selectedConversationID == expectedConversationID,
              conversationNavigationRequestID == expectedNavigationRequestID else { return false }
        if let storedRun = cached?.runSummary, startedRun.isOlderProjection(than: storedRun) {
            startedRun = storedRun
        }
        startedRun = currentReadRun(key, fallback: startedRun)
        startedRun.reconcileTerminalAgentStates()
        cacheVisibleRunViewState()
        let visibleDestination = activeRun?.runId == runID
            ? WorkspaceRunViewSnapshot(selectedAgent: selectedAgent, transcripts: transcripts,
                conversationHistory: resumeHistory) : nil
        var prepared = runViewStateStore.snapshot(for: key) ?? cached?.snapshot
            ?? WorkspaceRunViewSnapshot(selectedAgent: nil, transcripts: [:], conversationHistory: [])
        if let visibleDestination { prepared = prepared.mergingVisible(visibleDestination, for: key) }
        prepared.conversationHistory = mergeConversationHistory(current: prepared.conversationHistory,
            authoritative: carriedHistory)
        // The command's accepted destination supersedes outstanding readers of
        // its source. Their late summaries/history cannot navigate backwards.
        workspaceContextGeneration += 1
        conversationNavigationRequestID += 1
        resumeHistoryRequestID += 1
        conversationNavigationTarget = nil
        historicalConversationTurn = nil
        workspaceContextLoadingID = nil
        remoteTurnTransition = nil
        // The accepted turn and its workspace summary are one state change.
        // A health response started before this handoff must not restore the
        // previous turn and make it look newer than the one just submitted.
        refreshRequestID += 1
        workspaces = workspaces.map { workspace in
            workspace.workspaceId == expectedWorkspaceID
                ? workspace.replacingRunSummary(with: startedRun, startingNewTurn: true)
                : workspace
        }
        resumeHistory = prepared.conversationHistory
        transcripts = prepared.transcripts
        activeRun = startedRun
        recordRunInConversationIndex(startedRun)
        pendingAgentSelection = nil
        agentSelectionRequestID += 1
        selectedAgent = selectedAgentForRestoredRun(explicitSelection: rememberedSelectedAgent(
            for: startedRun, contextKey: machineRunContextKey(workspaceID: expectedWorkspaceID,
                runID: runID, machineID: target.machineID)), cachedSelection: prepared.selectedAgent, run: startedRun)
        let clearsDirectLaunchDraft = pendingPromptSubmission == nil
            && acceptedPromptResultsByWorkspace.values.allSatisfy { $0.runID != runID }
            && prompt == startedRun.prompt
        pendingPromptSubmission = nil
        if clearsDirectLaunchDraft { updatePrompt("") }
        storageConfirmationMessage = nil
        notifiedRunIDs.remove(
            MachineContextKey.task(machineID: currentMachineContextID, runID: runID)
        )
        runSummariesByContext[
            WorkspaceRunViewKey(
                machineID: target.machineID,
                workspaceID: expectedWorkspaceID,
                runID: runID
            )
        ] = startedRun
        cacheVisibleRunViewState()
        beginPolling(runID)

        // The Run already exists at this point. Do not keep the composer in an
        // "Opening" state while a long history or transcript page crosses the
        // Relay; both projections can merge in after live polling has started.
        Task { [weak self] in
            await self?.loadRunHistory(runID)
        }
        if let selectedAgent {
            Task { [weak self] in
                await self?.loadAgentDetail(selectedAgent, maxPages: 4, target: target)
            }
        }
        return true
    }

    func isPromptSubmissionVisible(
        _ submission: PersistedPromptSubmission,
        acceptedRunID: String? = nil
    ) -> Bool {
        guard isCurrentMachine(submission.machineID),
              selectedWorkspaceID == submission.workspaceID else { return false }
        if let acceptedRunID, activeRun?.runId == acceptedRunID { return true }
        if let conversationID = submission.conversationID {
            guard selectedConversationID == conversationID else { return false }
            if let sourceRunID = submission.sourceRunID, let run = activeRun {
                return run.runId == sourceRunID
            }
            return true
        }
        if let sourceRunID = submission.sourceRunID {
            return activeRun?.runId == sourceRunID
                || selectedConversation?.runs.contains { $0.runId == sourceRunID } == true
        }
        return selectedConversationID == nil || isComposingNewConversation
    }

    func restoreVisiblePromptSubmission() {
        guard let workspaceID = selectedWorkspaceID else {
            pendingPromptSubmission = nil
            storageConfirmationMessage = nil
            return
        }
        let key = machineWorkspaceContextKey(workspaceID: workspaceID)
        var visible = promptOutboxByWorkspace[key].flatMap {
            isPromptSubmissionVisible($0) ? $0 : nil
        }
        if visible == nil, let result = acceptedPromptResultsByWorkspace[key],
           isPromptSubmissionVisible(result.submission, acceptedRunID: result.runID) {
            visible = result.submission
            if visible?.status != .openingFailed { visible?.status = .opening }
            visible?.error = ""
        }
        if pendingPromptSubmission?.commandID != visible?.commandID
            || pendingPromptSubmission?.status != visible?.status
            || (visible?.status != .retrying && pendingPromptSubmission != visible) {
            pendingPromptSubmission = visible
        }
        if workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID,
           let visible, visible.sourceRunID == activeRun?.runId,
           let sourceAgent = visible.sourceAgent,
           activeRun?.agents.contains(where: { $0.agent == sourceAgent }) == true {
            // Re-entry restores the branch bound to the outstanding command;
            // an explicit Agent navigation clears the default-entry intent.
            selectedAgent = sourceAgent
        }
        storageConfirmationMessage = visible?.status == .awaitingConfirmation
            ? visible?.error : nil
    }

    func isDisplayingAcceptedPromptResult(_ result: PersistedAcceptedPromptResult) -> Bool {
        guard isCurrentMachine(result.submission.machineID),
              selectedWorkspaceID == result.submission.workspaceID,
              let run = activeRun else { return false }
        return acceptedPromptDestinationConflict(run, result: result,
            workspacePath: result.submission.workspacePath ?? selectedWorkspace?.path) == nil
    }

    @discardableResult
    func adoptAcceptedPromptResult(
        _ result: PersistedAcceptedPromptResult,
        response explicitResponse: RunStartResponse? = nil,
        carriedHistory explicitHistory: [ResumeHistoryEntry]? = nil
    ) async -> Bool {
        let submission = result.submission
        let contextKey = submission.contextKey
        guard acceptedPromptResultsByWorkspace[contextKey]?.commandID == result.commandID,
              let target = requestTarget(for: submission.machineID),
              acceptedPromptAdoptionsInFlight.insert(result.commandID).inserted else {
            return false
        }
        defer { acceptedPromptAdoptionsInFlight.remove(result.commandID) }
        let wasVisible = isPromptSubmissionVisible(submission, acceptedRunID: result.runID)
        let navigationRequestID = conversationNavigationRequestID
        let resumesConversation = submission.sourceRunID != nil
            || usableResumeSessionID(submission.resumeSessionID) != nil
        var carriedHistory = explicitHistory ?? []
        if resumesConversation {
            if let sourceRunID = submission.sourceRunID, let sourceAgent = submission.sourceAgent {
                let sourceKey = WorkspaceRunViewKey(machineID: submission.machineID,
                    workspaceID: submission.workspaceID, runID: sourceRunID)
                if isCurrentMachine(submission.machineID), selectedWorkspaceID == submission.workspaceID,
                   activeRun?.runId == sourceRunID { cacheVisibleRunViewState() }
                if let cached = await runViewStateStore.hydrate(for: sourceKey), let source = cached.runSummary {
                    let sourceHistory = branchConversationHistory(history: cached.snapshot.conversationHistory,
                        prompt: source.prompt, transcript: cached.snapshot.transcripts[sourceAgent] ?? AgentTranscript())
                    carriedHistory = mergeConversationHistory(current: carriedHistory, authoritative: sourceHistory)
                }
            } else if wasVisible, explicitHistory == nil {
                carriedHistory = resumeHistory
            }
        }
        do {
            var run: RunSummary
            if let received = explicitResponse?.run {
                run = received
            } else if isCurrentMachine(submission.machineID), activeRun?.runId == result.runID,
                      let current = activeRun {
                run = current
            } else {
                run = try await client.call(target: target, "turns.get",
                    body: GetRunBody(runId: result.runID, includeEvents: false, cursor: 0, eventLimit: 1,
                        workspaceId: submission.workspaceID))
            }
            var path = submission.workspacePath
            if path == nil, isCurrentMachine(submission.machineID) {
                path = workspaces.first { $0.workspaceId == submission.workspaceID }?.path
            }
            if path == nil {
                let registry: WorkspaceList = try await client.call(target: target, "workspaces.list", body: EmptyBody())
                path = registry.workspaces.first { $0.workspaceId == submission.workspaceID }?.path
            }
            var validationResponse = explicitResponse
            if acceptedPromptDestinationConflict(run, result: result, workspacePath: path,
                response: explicitResponse) == "workspace_id",
               explicitResponse?.workspaceId == nil || explicitResponse?.workspaceId == submission.workspaceID {
                // Older schedulers may attach a path-derived workspace snapshot
                // to a continuation. Read the exact Run in its registered scope;
                // never treat that projection mismatch as proof of another task.
                run = try await client.call(target: target, "turns.get",
                    body: GetRunBody(runId: result.runID, includeEvents: false, cursor: 0, eventLimit: 1,
                        workspaceId: submission.workspaceID))
                validationResponse = nil
            }
            if let conflict = acceptedPromptDestinationConflict(run, result: result,
                workspacePath: path, response: validationResponse) {
                throw PcrdClientError.remote("prompt_destination_conflict",
                    "The accepted task does not match the original message (\(conflict)). The receipt is retained.")
            }
            let key = WorkspaceRunViewKey(machineID: submission.machineID,
                workspaceID: submission.workspaceID, runID: result.runID)
            if isDisplayingAcceptedPromptResult(result) { cacheVisibleRunViewState() }
            guard await runViewStateStore.persistAcceptedRun(run, for: key, carriedHistory: carriedHistory) else {
                throw DesktopStatePersistenceError.promptHandoff
            }
            guard !Task.isCancelled,
                  acceptedPromptResultsByWorkspace[contextKey]?.commandID == result.commandID,
                  desktopStateStore.loadSnapshot().acceptedPromptResultsByWorkspace[contextKey]?.commandID
                    == result.commandID else { return false }
            run = currentReadRun(key, fallback: run)
            runSummariesByContext[key] = run
            if isCurrentMachine(submission.machineID) {
                // Acceptance updates the workspace, not just its current view.
                // A later task already known in that lineage must still win.
                workspaces = workspaces.map { workspace in
                    guard workspace.workspaceId == submission.workspaceID else { return workspace }
                    let current = reconciledWorkspaceHead(workspace, machineID: submission.machineID)
                    if current.lastRunId == run.runId || current.lastRunId == submission.sourceRunID
                        || current.lastRunId.isEmpty || (result.workspaceRevision.map { $0 >= workspaceRevision } ?? false) {
                        return reconciledWorkspaceHead(current.replacingRunSummary(with: run, startingNewTurn: true),
                            machineID: submission.machineID)
                    }
                    return current
                }
                if let revision = result.workspaceRevision { workspaceRevision = max(workspaceRevision, revision) }
            }
            let alreadyDisplayed = isDisplayingAcceptedPromptResult(result)
            if alreadyDisplayed {
                // Receipt settlement is not navigation. Preserve the selected
                // branch, its answer and any newer reads made during the write.
                cacheVisibleRunViewState()
            } else {
                if let snapshot = validationResponse?.conversationSnapshot ?? run.conversationSnapshot {
                    domainRepositories.ingest(snapshot)
                }
                runSummariesByContext[key] = run
            }
            // Settlement belongs to the command, not to the page being read.
            // Only an unchanged originating view may install its destination.
            if wasVisible, navigationRequestID == conversationNavigationRequestID,
               isPromptSubmissionVisible(submission, acceptedRunID: result.runID) {
                if alreadyDisplayed {
                    applyAcceptedPromptMetadata(result)
                } else {
                    let response = RunStartResponse(status: "accepted", message: nil, runId: result.runID,
                        storage: nil, run: run, workspaceId: submission.workspaceID,
                        workspaceRevision: result.workspaceRevision,
                        continuedFrom: RunContinuationSource(runId: submission.sourceRunID,
                            agent: submission.sourceAgent, sessionId: result.continuedSessionID.isEmpty ? nil : result.continuedSessionID))
                    if try await activateStartedRun(response, expectedWorkspaceID: submission.workspaceID,
                        target: target, carriedHistory: carriedHistory) {
                        applyAcceptedPromptMetadata(result)
                    }
                }
            } else if isCurrentMachine(submission.machineID), selectedWorkspaceID == submission.workspaceID {
                recordRunInConversationIndex(run, select: false)
            }
            let settled = consumeAcceptedPromptResult(result, conversationID: run.conversationId)
            if settled, run.isWorking {
                beginPolling(run.runId, workspaceID: submission.workspaceID, target: target)
            }
            return settled
        } catch {
            // The accepted handoff remains durable. Context refresh resumes
            // opening it; never resend the prompt or ask the user to refresh.
            if !PromptSubmissionRetryPolicy.shouldRetry(error),
               var held = acceptedPromptResultsByWorkspace[contextKey],
               held.commandID == result.commandID {
                held.submission.status = .openingFailed
                held.submission.receiptUncertain = false
                held.submission.errorCode = PromptSubmissionRetryPolicy.errorCode(error)
                held.submission.error = error.localizedDescription
                if acceptedPromptResultsByWorkspace[contextKey] != held {
                    acceptedPromptResultsByWorkspace[contextKey] = held
                    desktopStateStore.updateAcceptedPromptResult(held)
                }
                restoreVisiblePromptSubmission()
            }
            return false
        }
    }

    private func applyAcceptedPromptMetadata(_ result: PersistedAcceptedPromptResult) {
        if let revision = result.workspaceRevision { workspaceRevision = max(workspaceRevision, revision) }
        guard !result.continuedSessionID.isEmpty,
              settings.resumeSessionID != result.continuedSessionID else { return }
        settings.resumeSessionID = result.continuedSessionID
        rememberCurrentSettings()
    }

    @discardableResult
    private func consumeAcceptedPromptResult(_ result: PersistedAcceptedPromptResult, conversationID: String?) -> Bool {
        guard acceptedPromptResultsByWorkspace[result.contextKey]?.commandID
                == result.commandID,
              desktopStateStore.consumeAcceptedPromptResult(result, conversationID: conversationID) else { return false }
        acceptedPromptResultsByWorkspace.removeValue(forKey: result.contextKey)
        deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
        ensureDeferredPromptProcessing()
        if pendingPromptSubmission?.commandID == result.commandID {
            pendingPromptSubmission = nil
            storageConfirmationMessage = nil
        }
        return true
    }

    func addAgents(
        workspaceID: String,
        runID: String,
        count: Int,
        confirmLargeRun: Bool,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async throws -> Bool {
        let target = explicitTarget ?? client.currentTarget()
        let response: RunMutationResponse = try await client.call(
            target: target,
            "runs.add_agents",
            body: AddAgentsBody(
                runId: runID,
                count: max(1, count),
                confirmLargeRun: confirmLargeRun
            ),
            timeoutSeconds: 120
        )
        if let snapshot = response.conversationSnapshot {
            domainRepositories.ingest(snapshot)
        }
        if response.status == "confirmation_required" {
            setPendingStorageOperation(
                .more(workspaceID: workspaceID, runID: runID, count: count),
                message: response.message ?? "Additional Agents need more disk space than usual.",
                machineID: target.machineID
            )
            return false
        }
        clearPendingStorageOperation(workspaceID: workspaceID, machineID: target.machineID)
        guard target.machineID == currentMachineContextID,
              selectedWorkspaceID == workspaceID,
              activeRun?.runId == runID else { return true }
        if let run = response.run { activeRun = run }
        notifiedRunIDs.remove(
            MachineContextKey.task(machineID: target.machineID, runID: runID)
        )
        beginPolling(runID)
        return true
    }

}
