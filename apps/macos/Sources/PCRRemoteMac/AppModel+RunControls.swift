import Foundation

@MainActor
extension AppModel {
    func selectAgent(
        _ agent: Int,
        manageFollowUpQueue: Bool = true
    ) async {
        workspaceEntryDefaultNavigationRequestID = nil
        guard let run = activeRun,
              let targetAgent = run.agents.first(where: { $0.agent == agent }),
              let workspaceID = selectedWorkspaceID else { return }

        if selectedAgent == agent {
            if pendingAgentSelection == agent { return }
            if selectedAgentAnswerNeedsRefresh(run: run, selectedAgent: agent, transcripts: transcripts) {
                Task { [weak self] in await self?.loadAgentAnswer(agent, maxPages: 20) }
                resumeEventPollingIfNeeded()
            }
            if manageFollowUpQueue, !run.isWorking, continuationSelectionNeedsConfirmation {
                requestAgentContinuationSelection(agent, run: run)
            }
            guard pendingAgentSelection != nil else { return }
            agentSelectionRequestID += 1
            pendingAgentSelection = nil
            if manageFollowUpQueue, !followUpQueue.isEmpty, !run.isWorking {
                await reconcileFollowUpQueue(for: run, resetCountdown: false)
            }
            return
        }
        guard pendingAgentSelection != agent else { return }

        let requestID = agentSelectionRequestID + 1
        agentSelectionRequestID = requestID
        let runID = run.runId
        let machineID = currentMachineContextID
        let generation = workspaceContextGeneration
        // A failed branch fetch is a transient navigation error. Starting a
        // new selection is the retry, so do not leave the old failure banner
        // over a branch that subsequently loads successfully.
        errorMessage = nil
        pendingAgentSelection = agent

        if manageFollowUpQueue, !run.isWorking {
            // Submit the business intent before laying out the cached answer,
            // but never put a network acknowledgement on the reading path.
            requestAgentContinuationSelection(agent, run: run)
        }

        let key = WorkspaceRunViewKey(machineID: machineID, workspaceID: workspaceID, runID: runID)
        if transcripts[agent] == nil,
           let cached = runViewStateStore.snapshot(for: key)?.validated(for: key).transcripts[agent] {
            transcripts[agent] = cached
        }
        let alreadyLoaded = agentTranscriptReadyForDisplay(agent)
        let commitsBeforeLoading = agentSelectionCommitsBeforeHydration(
            transcriptReady: alreadyLoaded,
            agent: targetAgent
        )
        if commitsBeforeLoading {
            // Navigation acknowledges the click immediately. Only this branch's
            // content may appear below it while its answer is being fetched.
            commitVisibleAgentSelection(agent, keepPending: true, selectedWhileWorking: run.isWorking)
        }
        var loaded = alreadyLoaded
        if !alreadyLoaded {
            guard agentSelectionIsCurrent(
                requestID: requestID,
                agent: agent,
                machineID: machineID,
                workspaceID: workspaceID,
                runID: runID,
                generation: generation
            ) else { return }
            if targetAgent.isTerminal {
                loaded = await loadAgentAnswer(agent, maxPages: 1)
            }
            if !loaded {
                guard agentSelectionIsCurrent(requestID: requestID, agent: agent,
                    machineID: machineID, workspaceID: workspaceID, runID: runID,
                    generation: generation) else { return }
                loaded = await loadAgentDetail(
                    agent,
                    maxPages: 1,
                    initialEventLimit: 100,
                    allowInactiveSelection: true
                )
            }
        }

        guard agentSelectionIsCurrent(
            requestID: requestID,
            agent: agent,
            machineID: machineID,
            workspaceID: workspaceID,
            runID: runID,
            generation: generation
        ) else { return }

        guard loaded else {
            pendingAgentSelection = nil
            if commitsBeforeLoading {
                // Keep the user's branch choice. Summary polling will retry the
                // live transcript without snapping the interface back to the
                // previously selected Agent after a transient read failure.
                cacheVisibleRunViewState()
                resumeEventPollingIfNeeded()
                return
            }
            errorMessage = appLanguage.text(
                "无法加载 \(run.agents.first { $0.agent == agent }?.name ?? "Agent")，当前回复保持不变。",
                "Could not load \(run.agents.first { $0.agent == agent }?.name ?? "Agent"). The current response is unchanged."
            )
            if manageFollowUpQueue,
               !followUpQueue.isEmpty,
               let currentRun = activeRun,
               !currentRun.isWorking {
                // Load failed — the visible selection did not change, so the
                // countdown should not be restarted.
                await reconcileFollowUpQueue(for: currentRun, resetCountdown: false)
            }
            return
        }

        // Clear only the reading indicator; execution and continuation-source
        // confirmation have their own independent states.
        if run.isWorking, activeRun?.isWorking == false {
            pendingAgentSelection = nil
            return
        }
        commitVisibleAgentSelection(agent, keepPending: false, selectedWhileWorking: run.isWorking)

        if manageFollowUpQueue,
           !followUpQueue.isEmpty,
           let currentRun = activeRun,
           !currentRun.isWorking {
            // Switching to a different agent changes the continuation source but
            // preserves the countdown. Restarting here would extend the delay
            // by the full follow_up_delay each time the user browses agents.
            await reconcileFollowUpQueue(for: currentRun, resetCountdown: false)
        }
        resumeEventPollingIfNeeded()
        // Complete pagination after the branch and its first available content
        // have appeared. A newer selection cancels this through the normal
        // selected-Agent guard in loadAgentDetail(_:).
        Task { [weak self] in
            guard let self, self.activeRun?.runId == runID,
                  self.selectedWorkspaceID == workspaceID,
                  self.isCurrentMachine(machineID), self.selectedAgent == agent,
                  self.workspaceContextGeneration == generation,
                  selectedAgentDetailNeedsRefresh(run: self.activeRun,
                    selectedAgent: agent, transcripts: self.transcripts) else { return }
            if selectedAgentAnswerNeedsRefresh(run: self.activeRun,
                selectedAgent: agent, transcripts: self.transcripts) {
                Task { [weak self] in
                    guard let self, self.activeRun?.runId == runID,
                          self.selectedWorkspaceID == workspaceID, self.isCurrentMachine(machineID),
                          self.selectedAgent == agent, self.workspaceContextGeneration == generation else { return }
                    await self.loadAgentAnswer(agent, maxPages: 20)
                }
            }
            // Answer tails and work can both supply readable content. Neither
            // reader is a completion barrier for the other.
            await self.loadAgentDetail(agent, maxPages: 20)
        }
    }

    private func commitVisibleAgentSelection(
        _ agent: Int,
        keepPending: Bool,
        selectedWhileWorking: Bool
    ) {
        // Open documents retain their own origin. Browsing another candidate
        // must not close them or cancel a file the user just requested.
        selectedAgent = agent
        if !keepPending {
            pendingAgentSelection = nil
        }
        rememberSelectedAgent(agent, selectedWhileWorking: selectedWhileWorking)
        cacheVisibleRunViewState()
    }

    private func agentSelectionIsCurrent(
        requestID: Int,
        agent: Int,
        machineID: String,
        workspaceID: String,
        runID: String,
        generation: Int
    ) -> Bool {
        !Task.isCancelled
            && agentSelectionRequestID == requestID
            && pendingAgentSelection == agent
            && currentMachineContextID == machineID
            && selectedWorkspaceID == workspaceID
            && activeRun?.runId == runID
            && workspaceContextGeneration == generation
    }

    func selectAdjacentAgent(_ offset: Int) async {
        let agents = activeRun?.agents.map(\.agent).sorted() ?? []
        guard agents.count > 1 else { return }
        let currentAgent = pendingAgentSelection ?? selectedAgent
        let current = currentAgent.flatMap { agents.firstIndex(of: $0) } ?? 0
        let next = (current + offset + agents.count) % agents.count
        await selectAgent(agents[next])
    }

    func refreshCurrentRun() async {
        guard let runID = activeRun?.runId,
              let workspaceID = selectedWorkspaceID else { return }
        let machineID = currentMachineContextID
        let generation = workspaceContextGeneration
        let agent = selectedAgent
        do {
            try await loadRun(runID, loadDetailPages: 20)
            guard currentMachineContextID == machineID,
                  selectedWorkspaceID == workspaceID,
                  activeRun?.runId == runID,
                  workspaceContextGeneration == generation else { return }
            if let agent, selectedAgent == agent {
                if activeRun?.agents.first(where: { $0.agent == agent })?.isTerminal == true {
                    await loadAgentAnswer(agent, maxPages: 20, retryImmediately: true)
                }
                await loadAgentDetail(agent, maxPages: 20, refreshFromStart: true)
            }
            guard currentMachineContextID == machineID,
                  selectedWorkspaceID == workspaceID,
                  activeRun?.runId == runID,
                  workspaceContextGeneration == generation else { return }
            resumeEventPollingIfNeeded()
            await loadRunHistory(runID, reportErrors: true)
        } catch {
            if currentMachineContextID == machineID,
               selectedWorkspaceID == workspaceID,
               activeRun?.runId == runID {
                errorMessage = appLanguage.userMessage(for: error)
            }
        }
    }

    func stopCurrentRun() async {
        guard activeRun?.isWorking == true,
              let context = currentRunOperationContext() else { return }
        await pauseDeferredPrompts(workspaceID: context.workspaceID, machineID: context.machineID)
        await performRunOperation(context, kind: .stopTurn) { context in
            let run: RunSummary = try await self.client.call(
                target: context.target,
                "runs.stop",
                body: RunStopBody(runId: context.runID, waitSeconds: 10),
                timeoutSeconds: 45
            )
            guard self.isCurrentRunOperation(context) else { return }
            self.activeRun = run
            self.beginPolling(context.runID)
            await self.refreshDaemonFollowUpQueue(
                workspaceID: context.workspaceID,
                runID: context.runID,
                machineID: context.machineID,
                migrateLocalQueue: false
            )
        }
    }

    func discardCurrentRun() async {
        guard let context = currentRunOperationContext() else { return }
        await performRunOperation(context, kind: .discardTurn) { context in
            let run: RunSummary = try await self.client.call(
                target: context.target,
                "runs.discard",
                body: RunDiscardBody(
                    runId: context.runID,
                    keepWorkspaces: context.settings.keepWorkspaces,
                    waitSeconds: 20
                ),
                timeoutSeconds: 60
            )
            self.clearFollowUpQueue(
                workspaceID: context.workspaceID,
                runID: context.runID,
                machineID: context.machineID
            )
            guard self.isCurrentRunOperation(context) else { return }
            self.activeRun = run
            if run.isWorking {
                self.beginPolling(context.runID)
            }
        }
    }

    var canVerifyInterruptedSave: Bool {
        systemCapabilities?.supports("runs.verify_finalization") == true
    }

    func recoverFinalization(syncWasApplied: Bool? = nil) async {
        guard let run = activeRun,
              run.normalizedStatus == "sync_ambiguous",
              run.finalizedAgent != nil,
              let context = currentRunOperationContext() else { return }
        await performRunOperation(context, kind: .recoverFinalization) { context in
            do {
                var recovered: RunSummary = try await self.client.call(
                    target: context.target,
                    syncWasApplied == nil ? "runs.verify_finalization" : "runs.recover_finalization",
                    body: RecoverFinalizationBody(
                        runId: context.runID,
                        syncWasApplied: syncWasApplied ?? false
                    ),
                    timeoutSeconds: 60
                )
                if syncWasApplied != false {
                    guard let agent = recovered.finalizedAgent else {
                        throw PcrdClientError.invalidResponse
                    }
                    recovered = try await self.client.call(
                        target: context.target,
                        "turns.finalize",
                        body: AgentActionBody(runId: context.runID, agent: agent),
                        timeoutSeconds: 60
                    )
                }
                guard self.isCurrentRunOperation(context) else { return }
                await self.applyRecoveredRun(recovered)
                await self.refresh(reportErrors: false)
            } catch {
                if self.isCurrentRunOperation(context) {
                    try? await self.loadRun(context.runID, loadDetailPages: 4)
                }
                throw error
            }
        }
        if isCurrentRunOperation(context), let run = activeRun, !followUpQueue.isEmpty {
            await reconcileFollowUpQueue(for: run, resetCountdown: true)
        }
    }

    func retryFinalizationCleanup() async {
        guard let run = activeRun,
              run.normalizedStatus == "cleanup_failed",
              let agent = run.finalizedAgent,
              let context = currentRunOperationContext() else { return }
        await performRunOperation(context, kind: .retryCleanup) { context in
            do {
                let recovered: RunSummary = try await self.client.call(
                    target: context.target,
                    "turns.finalize",
                    body: AgentActionBody(runId: context.runID, agent: agent),
                    timeoutSeconds: 60
                )
                guard self.isCurrentRunOperation(context) else { return }
                await self.applyRecoveredRun(recovered)
                await self.refresh(reportErrors: false)
            } catch {
                if self.isCurrentRunOperation(context) {
                    try? await self.loadRun(context.runID, loadDetailPages: 4)
                }
                throw error
            }
        }
        if isCurrentRunOperation(context), let run = activeRun, !followUpQueue.isEmpty {
            await reconcileFollowUpQueue(for: run, resetCountdown: true)
        }
    }

    private func applyRecoveredRun(_ run: RunSummary) async {
        if let snapshot = run.conversationSnapshot {
            domainRepositories.ingest(snapshot)
        }
        activeRun = run
        if let revision = run.workspaceRevision {
            workspaceRevision = revision
        }
        if let sessionID = run.sessionPromotion?.sessionId,
           !sessionID.isEmpty {
            settings.resumeSessionID = sessionID
            rememberCurrentSettings()
            preparePromptHistoryContext()
            await loadRunHistory(run.runId)
            await loadPromptHistory()
        }
        if run.isWorking { beginPolling(run.runId) }
    }

    func killSelectedAgent() async {
        guard let agent = selectedAgentSummary, let runID = activeRun?.runId else { return }
        await stopAgent(agent.agent, in: runID)
    }

    func stopAgent(_ agentID: Int, in runID: String) async {
        guard let context = currentRunOperationContext(), context.runID == runID,
              isViewingLatestWorkspaceRun,
              let agent = activeRun?.agents.first(where: { $0.agent == agentID }),
              agent.canStop else { return }
        await performRunOperation(context, kind: .stopAgent(agent.agent)) { context in
            let _: RunAgentSummary = try await self.client.call(
                target: context.target,
                "runs.kill_agent",
                body: AgentActionBody(runId: context.runID, agent: agent.agent)
            )
            guard self.isCurrentRunOperation(context) else { return }
            // The mutation already succeeded. Start polling before the follow-up
            // read so a transient refresh failure cannot leave the turn frozen.
            self.beginPolling(context.runID)
            try await self.loadRun(context.runID, loadDetailPages: 4)
        }
    }

    func retrySelectedAgent() async {
        guard let agent = selectedAgentSummary,
              agent.mayRetry,
              let context = currentRunOperationContext() else { return }
        await performRunOperation(context, kind: .retryAgent(agent.agent)) { context in
            let run: RunSummary = try await self.client.call(
                target: context.target,
                "runs.retry_agent",
                body: AgentActionBody(runId: context.runID, agent: agent.agent)
            )
            guard self.isCurrentRunOperation(context) else { return }
            // The retry is a new attempt. Invalidate any old page request
            // before clearing its cursor and event identities, otherwise a
            // late response can splice the failed attempt into the new one.
            let loadingKey = self.agentLoadingKey(
                machineID: context.machineID,
                workspaceID: context.workspaceID,
                runID: context.runID,
                agent: agent.agent
            )
            self.agentDetailLoadTokens.removeValue(forKey: loadingKey)
            self.loadingAgents.remove(loadingKey)
            self.loadingAgentKeys.remove(loadingKey)
            self.transcripts[agent.agent] = AgentTranscript(
                sourceRunID: context.runID,
                sourceAgent: agent.agent
            )
            self.activeRun = run
            self.notifiedRunIDs.remove(
                MachineContextKey.task(machineID: context.machineID, runID: context.runID)
            )
            self.beginPolling(context.runID)
        }
    }

    func toggleSelectedAgentRejection() async {
        guard let agent = selectedAgentSummary,
              let context = currentRunOperationContext() else { return }
        await performRunOperation(
            context,
            kind: .updateRecommendation(agent.agent)
        ) { context in
            let run: RunSummary = try await self.client.call(
                target: context.target,
                "runs.reject_agent",
                body: RejectAgentBody(
                    runId: context.runID,
                    agent: agent.agent,
                    rejected: !agent.rejected
                )
            )
            guard self.isCurrentRunOperation(context) else { return }
            self.activeRun = run
        }
    }

    func acceptSelectedAgent() async {
        _ = await acceptSelectedAgent(dispatchQueuedFollowUp: true)
    }

    /// Explicit resume-source changes may need to resolve the visible branch.
    /// Window closing and application exit never enter this transition gate.
    func prepareVisibleResultForContextTransition(
        destination: ConversationContextDestination = .workspace,
        dispatchQueuedFollowUp: Bool = true
    ) async -> Bool {
        let plan = ConversationContextTransitionPlan.make(
            destination: destination,
            run: activeRun,
            selectedAgent: selectedAgentSummary,
            isLatestWorkspaceRun: isViewingLatestWorkspaceRun,
            syncBack: settings.syncBack,
            hasPendingSubmission: pendingPromptSubmission != nil,
            hasRunOperation: isRunOperationInProgress
        )
        switch plan {
        case .proceed:
            return true
        case .blocked:
            errorMessage = plan.blockedReason(language: appLanguage)
            return false
        case .applyVisibleAgent(let agent):
            guard selectedAgent == agent else { return false }
            return await acceptSelectedAgent(
                dispatchQueuedFollowUp: dispatchQueuedFollowUp
            )
        }
    }

    @discardableResult
    func acceptSelectedAgent(dispatchQueuedFollowUp: Bool) async -> Bool {
        guard promptSubmissionPlan != .loading else { return false }
        guard pendingPromptSubmission == nil, !isRunOperationInProgress else { return false }
        guard let agent = selectedAgentSummary else {
            errorMessage = appLanguage.text(
                "请先选择一个 Agent。",
                "Select an Agent first."
            )
            return false
        }
        guard agent.mayAccept else {
            errorMessage = appLanguage.text(
                "该 Agent 尚未完成，无法采用。",
                "This Agent has not finished yet and cannot be used."
            )
            return false
        }
        guard let context = currentRunOperationContext() else {
            errorMessage = appLanguage.text(
                "当前没有可操作的对话。",
                "There is no active conversation."
            )
            return false
        }
        guard context.settings.syncBack else {
            errorMessage = appLanguage.text(
                "采用 Agent 结果前，请先开启同步回原工作区。",
                "Turn on Sync Back before using an Agent result."
            )
            return false
        }
        let key = WorkspaceRunViewKey(machineID: context.machineID,
            workspaceID: context.workspaceID, runID: context.runID)
        guard let originalWorkspace = activeRun?.workspace else { return false }
        cacheVisibleRunViewState()
        await performRunOperation(context, kind: .useResult(agent.agent)) { context in
            let run: RunSummary = try await self.client.call(
                target: context.target,
                "turns.finalize",
                body: AcceptAgentBody(
                    runId: context.runID,
                    agent: agent.agent,
                    waitSeconds: 20,
                    keepWorkspaces: context.settings.keepWorkspaces,
                    syncBack: context.settings.syncBack
                ),
                timeoutSeconds: 60
            )
            try self.recordRunOperationResult(run, context: context, workspace: originalWorkspace)
        }
        let observed = runSummariesByContext[key]
        if observed?.isWorking == true || runOperationIssues[context.key]?.needsReadback == true {
            beginPolling(context.runID, workspaceID: context.workspaceID, target: context.target)
        }
        // Only the mutation owns the continuation gate. History and health
        // reads may outlive it, including after a child turn has been accepted.
        Task { [weak self] in
            guard let self, self.isCurrentRunOperation(context) else { return }
            await self.loadRunHistory(context.runID, waitForCompletion: false)
            guard self.isCurrentRunOperation(context) else { return }
            await self.loadPromptHistory()
            guard self.isCurrentRunOperation(context) else { return }
            await self.refresh(reportErrors: false)
        }
        let accepted = observed?.normalizedStatus == "finalized"
            && observed?.finalizedAgent == agent.agent && observed?.syncedBack == true
        // Unconfirmed outcomes stay in the original run's operation issue;
        // duplicating one as a global alert would outlive successful readback.
        if accepted,
           isCurrentRunOperation(context),
           dispatchQueuedFollowUp,
           !followUpQueue.isEmpty {
            await dispatchFollowUp()
        }
        return accepted
    }

    func addMoreAgents() async {
        guard let run = activeRun,
              run.mayAddAgents,
              let context = currentRunOperationContext() else { return }
        let count = max(1, additionalAgentCount)
        guard count <= effectiveMaximumAgentCount else {
            errorMessage = appLanguage.text(
                "追加 Agent 数量必须在 1 到 \(effectiveMaximumAgentCount) 之间。",
                "Additional Agent count must be between 1 and \(effectiveMaximumAgentCount)."
            )
            return
        }
        addAgentsPresented = false
        await performRunOperation(context, kind: .addAgents) { context in
            let response: RunMutationResponse = try await self.client.call(
                target: context.target,
                "runs.add_agents",
                body: AddAgentsBody(
                    runId: context.runID,
                    count: count,
                    confirmLargeRun: false
                ),
                timeoutSeconds: 120
            )
            if let snapshot = response.conversationSnapshot {
                self.domainRepositories.ingest(snapshot)
            }
            if response.status == "confirmation_required" {
                self.setPendingStorageOperation(
                    .more(
                        workspaceID: context.workspaceID,
                        runID: context.runID,
                        count: count
                    ),
                    message: response.message
                        ?? "Additional Agents need more disk space than usual.",
                    machineID: context.machineID
                )
                return
            }
            self.clearPendingStorageOperation(
                workspaceID: context.workspaceID,
                machineID: context.machineID
            )
            guard self.isCurrentRunOperation(context) else { return }
            if let run = response.run { self.activeRun = run }
            self.notifiedRunIDs.remove(
                MachineContextKey.task(machineID: context.machineID, runID: context.runID)
            )
            self.beginPolling(context.runID)
        }
    }

    func showSelectedAgentDiff(file: String? = nil) async {
        guard let runID = activeRun?.runId, let workspaceID = selectedWorkspaceID,
              let agent = selectedAgentSummary else { return }
        diffContext = ReviewContext(target: client.currentTarget(), workspaceID: workspaceID,
                                    runID: runID, agent: agent.agent, conversationID: selectedConversationID)
        diffSelectedFile = file
        let agentName = activeRun?.displayName(forAgent: agent.agent, language: appLanguage)
            ?? appLanguage.text("Agent \(agent.agent)", "Agent \(agent.agent)")
        diffTitle = appLanguage.text("\(agentName) 的更改", "\(agentName) Changes")
        diffPresented = true
        dismissWorkspaceDocument()
    }

}
