import AppKit
import Foundation

extension AppModel {
    var selectedWorkspace: RemoteWorkspace? {
        workspaces.first { $0.workspaceId == selectedWorkspaceID }
    }

    var selectedMachine: RelayMachine? {
        machines.first { $0.deviceId == selectedMachineID }
    }

    var localComputerDisplayName: String {
        Host.current().localizedName ?? "This Mac"
    }

    var effectiveMaximumAgentCount: Int {
        systemCapabilities?.clientMaximumAgentCount ?? maximumAgentCountLimit
    }

    func feedbackDiagnostics() -> [String: String] {
        var values: [String: String] = [
            "app_platform": "macos",
            "app_version": Bundle.main.object(
                forInfoDictionaryKey: "CFBundleShortVersionString"
            ) as? String ?? "development",
            "app_build": Bundle.main.object(
                forInfoDictionaryKey: "CFBundleVersion"
            ) as? String ?? "development",
            "os_version": ProcessInfo.processInfo.operatingSystemVersionString,
            "device_model": Host.current().localizedName ?? "Mac",
        ]
        if let selectedMachine {
            values["mac_device_id"] = selectedMachine.deviceId
            values["mac_name"] = selectedMachine.name
        }
        if let value = systemCapabilities?.daemonVersion, !value.isEmpty {
            values["daemon_version"] = value
        }
        if let value = systemCapabilities?.protocolVersion {
            values["protocol_version"] = String(value)
        }
        if let workspace = selectedWorkspace {
            values["workspace_id"] = workspace.workspaceId
            values["workspace_name"] = workspace.name
        }
        if let runID = activeRun?.runId, !runID.isEmpty {
            values["run_id"] = runID
        }
        if let selectedAgent {
            values["agent"] = String(selectedAgent)
        }
        return values
    }

    var selectedAgentSummary: RunAgentSummary? {
        guard let selectedAgent else { return nil }
        return activeRun?.agents.first { $0.agent == selectedAgent }
    }

    var selectedAgentDisplayName: String? {
        guard let run = activeRun, let selectedAgent else { return nil }
        return run.displayName(forAgent: selectedAgent, language: appLanguage)
    }

    var selectedAgentDiffSummary: AgentDiffSummary? {
        guard let key = selectedAgentDiffSummaryKey else { return nil }
        return agentDiffSummaries[key]
    }

    var isSelectedAgentDiffSummaryLoading: Bool {
        guard let key = selectedAgentDiffSummaryKey else { return false }
        return agentDiffSummaryLoadingKeys.contains(key)
    }

    private var selectedAgentDiffSummaryKey: String? {
        guard let workspaceID = selectedWorkspaceID,
              let runID = activeRun?.runId,
              let selectedAgent else { return nil }
        return agentDiffSummaryKey(
            machineID: currentMachineContextID,
            workspaceID: workspaceID,
            runID: runID,
            agent: selectedAgent
        )
    }

    var conversationSurfaceState: ConversationSurfaceState? {
        guard let workspace = selectedWorkspace else { return nil }
        return ConversationSurfaceState.make(
            workspace: workspace,
            conversation: selectedConversation,
            composingNewConversation: isComposingNewConversation,
            run: activeRun,
            selectedAgent: selectedAgentSummary,
            isLatestTurn: isViewingLatestConversationTurn,
            loading: isSelectedWorkspaceContextLoading,
            pendingSubmission: unrepresentedPromptSubmission,
            presentationOverride: visibleRunPresentation,
            language: appLanguage
        )
    }

    var visibleRunPresentation: ConversationRunPresentation? {
        guard let run = activeRun else { return nil }
        switch currentRunOperationKind {
        case .useResult:
            return ConversationRunPresentation(
                phase: .finalizing,
                label: appLanguage.text("正在采用所选结果", "Using selected result"),
                systemImage: "arrow.triangle.merge",
                tone: .accent,
                animated: true
            )
        case .stopTurn:
            return ConversationRunPresentation(
                phase: .stopping,
                label: appLanguage.text("正在停止当前轮", "Stopping turn"),
                systemImage: "stop.circle",
                tone: .warning,
                animated: true
            )
        case .discardTurn:
            return ConversationRunPresentation(
                phase: .stopping,
                label: appLanguage.text("正在放弃当前轮", "Discarding turn"),
                systemImage: "trash",
                tone: .warning,
                animated: true
            )
        case .recoverFinalization:
            return ConversationRunPresentation(
                phase: .finalizing,
                label: appLanguage.text("正在处理工作区同步", "Resolving workspace sync"),
                systemImage: "arrow.triangle.2.circlepath",
                tone: .accent,
                animated: true
            )
        case .retryCleanup:
            return ConversationRunPresentation(
                phase: .finalizing,
                label: appLanguage.text("正在清理临时文件", "Cleaning temporary files"),
                systemImage: "trash.slash",
                tone: .accent,
                animated: true
            )
        case .addAgents:
            return ConversationRunPresentation(
                phase: .preparing,
                label: appLanguage.text("正在追加 Agent", "Adding Agents"),
                systemImage: "person.badge.plus",
                tone: .accent,
                animated: true
            )
        case .stopAgent, .retryAgent, .updateRecommendation, nil:
            return ConversationRunPresentation.make(run: run, language: appLanguage)
        }
    }

    var isSelectedWorkspaceContextHydrating: Bool {
        selectedWorkspaceID != nil && workspaceContextLoadingID == selectedWorkspaceID
    }

    var isSelectedWorkspaceContextLoading: Bool {
        workspaceSurfaceNeedsBlockingLoading(
            hydrating: isSelectedWorkspaceContextHydrating,
            composingNewConversation: isComposingNewConversation,
            hasRenderableContent: !selectedTimelineItems.isEmpty,
            hasPendingSubmission: pendingPromptSubmission != nil
        )
    }

    var selectedTranscriptBlocks: [TranscriptBlock] {
        let pending = unrepresentedPromptSubmission
        let key = TranscriptPresentationKey(machineID: currentMachineContextID,
            workspaceID: selectedWorkspaceID ?? "", conversationID: selectedConversationID ?? "",
            runID: activeRun?.runId ?? "", agent: selectedAgent ?? 0)
        let transcript = selectedAgent.flatMap { transcripts[$0] } ?? AgentTranscript()
        let input = TranscriptProjectionInput(
            runID: activeRun?.runId,
            runStatus: activeRun?.status,
            prompt: activeRun?.prompt ?? "",
            history: resumeHistory,
            transcript: transcript,
            runError: activeRun?.error,
            workerError: activeRun?.workerError,
            pendingSubmission: pending,
            questions: selectedQuestionPresentations
        )
        if transcriptPresentationKey == key, transcriptProjectionInput == input {
            return transcriptProjectionBlocks
        }
        if let oldKey = transcriptPresentationKey, let oldInput = transcriptProjectionInput {
            transcriptPresentationCache.put(TranscriptPresentationSnapshot(
                input: oldInput, blocks: transcriptProjectionBlocks, generation: transcriptProjectionGeneration,
                timelineInput: transcriptTimelineProjectionInput, timelineBlocks: transcriptTimelineProjectionBlocks,
                timelineGeneration: transcriptTimelineProjectionGeneration,
                itemsInput: conversationTimelineItemsProjectionInput, items: conversationTimelineItemsProjectionItems,
                itemsGeneration: conversationTimelineItemsProjectionGeneration), for: oldKey)
        }
        transcriptPresentationKey = key
        if let cached = transcriptPresentationCache.value(for: key, matching: input) {
            transcriptProjectionInput = cached.input
            transcriptProjectionBlocks = cached.blocks
            transcriptProjectionGeneration = cached.generation
            transcriptTimelineProjectionInput = cached.timelineInput
            transcriptTimelineProjectionBlocks = cached.timelineBlocks
            transcriptTimelineProjectionGeneration = cached.timelineGeneration
            conversationTimelineItemsProjectionInput = cached.itemsInput
            conversationTimelineItemsProjectionItems = cached.items
            conversationTimelineItemsProjectionGeneration = cached.itemsGeneration
            return cached.blocks
        }

        let coalesced = TranscriptFormatter.preparedConversationBlocks(input)
        transcriptProjectionInput = input
        transcriptProjectionBlocks = coalesced
        presentationProjectionSequence &+= 1
        transcriptProjectionGeneration = presentationProjectionSequence
        return coalesced
    }

    var unrepresentedPromptSubmission: PersistedPromptSubmission? {
        guard let pending = pendingPromptSubmission else { return nil }
        // The command retains its source Agent even while another branch is
        // being read. Never attach its optimistic turn to that branch's answer.
        if activeRun?.runId == pending.sourceRunID, let sourceAgent = pending.sourceAgent,
           selectedAgent != sourceAgent { return nil }
        if let accepted = acceptedPromptResultsByWorkspace[pending.contextKey],
           accepted.commandID == pending.commandID,
           isDisplayingAcceptedPromptResult(accepted) { return nil }
        return pending
    }

    var selectedTimelineBlocks: [TranscriptBlock] {
        let blocks = selectedTranscriptBlocks
        let input = TranscriptTimelineProjectionInput(
            transcriptGeneration: transcriptProjectionGeneration,
            runID: activeRun?.runId ?? "run",
            agent: selectedAgent ?? 0,
            expanded: showsSelectedAgentWork,
            language: appLanguage.rawValue,
            workRevision: workProcess.revision
        )
        if transcriptTimelineProjectionInput == input {
            return transcriptTimelineProjectionBlocks
        }
        // Each contiguous work segment stays before its following reply.
        let filtered = TranscriptFormatter.blocksWithWorkDisclosure(
            blocks,
            runID: input.runID,
            agent: input.agent,
            expanded: input.expanded,
            language: appLanguage,
            sectionOverrides: selectedWorkSectionOverrides
        )
        let projected = TranscriptFormatter.coalescedVisibleConversation(projectWorkOperations(filtered))
        transcriptTimelineProjectionInput = input
        transcriptTimelineProjectionBlocks = projected
        presentationProjectionSequence &+= 1
        transcriptTimelineProjectionGeneration = presentationProjectionSequence
        return projected
    }

    var selectedTimelineItems: [ConversationTimelineItem] {
        let blocks = selectedTimelineBlocks
        let input = ConversationTimelineItemsProjectionInput(
            timelineGeneration: transcriptTimelineProjectionGeneration,
            switchState: currentConversationAgentSwitchState
        )
        if conversationTimelineItemsProjectionInput == input {
            return conversationTimelineItemsProjectionItems
        }
        let projected = ConversationTimelineProjection.make(
            blocks: blocks,
            switchState: input.switchState
        )
        conversationTimelineItemsProjectionInput = input
        conversationTimelineItemsProjectionItems = projected
        presentationProjectionSequence &+= 1
        conversationTimelineItemsProjectionGeneration = presentationProjectionSequence
        return projected
    }

    private var selectedQuestionPresentations: [AgentQuestionPresentation] {
        guard let workspaceID = selectedWorkspaceID else { return [] }
        let target = client.currentTarget()
        func presentation(_ request: AgentInputRequest, runID: String, agent: Int,
                          historical: Bool) -> AgentQuestionPresentation {
            let command = questionAnswers.entry(machineID: target.machineID, workspaceID: workspaceID,
                runID: runID, agent: agent, requestID: request.id)
            let received = command.flatMap { questionAnswers.receipts[$0.id] }
            let current = received.flatMap { AgentInputRequest.merge([$0], into: [request]).first } ?? request
            let identity = "question:\(runID):\(agent):\(request.id)"
            var value = AgentQuestionPresentation(runID: runID, agent: agent, request: current,
                command: command, undurable: command.map { questionAnswers.undurableAnswers.contains($0.id) } ?? false,
                expanded: expandedQuestionRequests.contains(identity), historical: historical)
            if !historical { value.anchorAfterID = transcripts[agent]?.questionAnchors?[request.id] }
            if command != nil, let error = questionAnswers.storageError { value.error = error }
            return value
        }
        var values = resumeHistory.flatMap { AgentQuestionTranscript.historyQuestions(in: $0) }.map {
            presentation($0.request, runID: $0.runID, agent: $0.agent, historical: true)
        }
        if let runID = activeRun?.runId, let agent = selectedAgent,
           let transcript = transcripts[agent], transcript.sourceRunID == runID, transcript.sourceAgent == agent {
            values += (transcript.inputRequests ?? []).map {
                presentation($0, runID: runID, agent: agent, historical: false)
            }
        }
        return values
    }

    func toggleQuestionDetails(_ question: AgentQuestionPresentation) {
        if !expandedQuestionRequests.insert(question.id).inserted {
            expandedQuestionRequests.remove(question.id)
        }
    }

    var currentConversationAgentSwitchState: ConversationAgentSwitchState? {
        guard unrepresentedPromptSubmission == nil else { return nil }
        return activeRun.map { run in
            ConversationAgentSwitchState(
                runID: run.runId,
                agents: run.agents,
                selectedAgent: selectedAgent,
                pendingAgent: pendingAgentSelection,
                recommendedAgent: run.recommendedAgent,
                finalizedAgent: run.finalizedAgent,
                // Choosing a candidate is a read-only view operation. Result
                // adoption, retry, and other run operations must not lock the
                // user onto whichever Agent happened to be visible before the
                // operation began.
                selectionEnabled: true,
                executionConfirmed: ExecutionActivityState.isWorking(
                    run: run,
                    isLatestTurn: isViewingLatestConversationTurn,
                    hasPendingSubmission: false,
                    connectionConfirmed: daemonOnline && connectionErrorMessage == nil
                        && liveConversationWarning == nil && !machineSelectionInProgress
                )
            )
        }
    }

    var selectedAgentWorkItemCount: Int {
        selectedTranscriptBlocks.filter {
            TranscriptFormatter.isAgentWorkBlock($0) && !$0.isHistorical
        }.count
    }

    var showsSelectedAgentWork: Bool {
        guard let key = selectedAgentWorkContextKey else { return false }
        if expandedAgentWorkContexts.contains(key) { return true }
        if collapsedAgentWorkContexts.contains(key) { return false }
        return workExpandedByDefault
    }

    func toggleSelectedAgentWork() {
        guard let key = selectedAgentWorkContextKey else { return }
        let shouldExpand = !showsSelectedAgentWork
        workProcess.sections = workProcess.sections.filter { !$0.key.hasPrefix(key + "|") }
        workProcess.save()
        if shouldExpand {
            collapsedAgentWorkContexts.remove(key)
            expandedAgentWorkContexts.insert(key)
        } else {
            expandedAgentWorkContexts.remove(key)
            collapsedAgentWorkContexts.insert(key)
        }
    }

    var selectedAgentWorkContextKey: String? {
        guard let workspaceID = selectedWorkspaceID,
              let runID = activeRun?.runId,
              let selectedAgent else { return nil }
        return machineRunContextKey(workspaceID: workspaceID, runID: runID)
            + "\u{1f}\(selectedAgent)\u{1f}work"
    }

    var isSelectedAgentDetailLoading: Bool {
        guard let workspaceID = selectedWorkspaceID,
              let runID = activeRun?.runId,
              let selectedAgent else { return false }
        let loadingKey = agentLoadingKey(
            machineID: currentMachineContextID,
            workspaceID: workspaceID,
            runID: runID,
            agent: selectedAgent
        )
        return !agentTranscriptReadyForDisplay(selectedAgent)
            && (pendingAgentSelection == selectedAgent || loadingAgentKeys.contains(loadingKey))
    }

    var isSelectedTimelineLoading: Bool {
        guard let workspaceID = selectedWorkspaceID,
              let runID = activeRun?.runId,
              let selectedAgent else { return false }
        let loadingKey = agentLoadingKey(
            machineID: currentMachineContextID,
            workspaceID: workspaceID,
            runID: runID,
            agent: selectedAgent
        )
        let key = WorkspaceRunViewKey(machineID: currentMachineContextID, workspaceID: workspaceID, runID: runID)
        return isSelectedWorkspaceContextHydrating
            || (conversationHistoryTasks[key] != nil && runViewStateStore.snapshot(for: key)?.historyLoaded != true)
            || (!agentTranscriptReadyForDisplay(selectedAgent)
                && (pendingAgentSelection == selectedAgent || loadingAgentKeys.contains(loadingKey)))
    }

    var canSubmitPrompt: Bool {
        promptSubmissionPlan.isAvailable
            && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && (pendingPromptSubmission == nil || shouldDeferPrompt)
    }

    var canSaveFollowUpLocally: Bool {
        shouldDeferPrompt || (promptSubmissionPlan == .enqueue && daemonOwnsFollowUpQueue())
    }

    var promptSubmissionContextAvailable: Bool {
        promptSubmissionPlan.isAvailable
    }

    var promptSubmissionStopsRemainingAgents: Bool {
        promptSubmissionPlan.stopsRemainingAgents
    }

    var promptSubmissionPlan: PromptSubmissionPlan {
        if shouldDeferPrompt { return .enqueue }
        let plan = resolvedPromptSubmissionPlan
        guard plan.isAvailable, !daemonOnline else { return plan }
        if plan == .enqueue, daemonOwnsFollowUpQueue() { return plan }
        let capabilities = systemCapabilitiesByMachine[currentMachineContextID] ?? systemCapabilities
        let action: String
        switch plan {
        case .start: action = "turns.start"
        case .continueFrom: action = "turns.continue"
        case .loading, .enqueue, .blocked:
            return .blocked("Connect to this computer before sending")
        }
        guard requestTarget(for: currentMachineContextID) != nil,
              capabilities?.supports(action) == true else {
            return .blocked("Connect to this computer before sending")
        }
        return plan
    }

    private var resolvedPromptSubmissionPlan: PromptSubmissionPlan {
        if visibleDeletedConversation != nil {
            return .blocked("This conversation has been deleted. Restore it to continue.")
        }
        guard promptDestinationIsResolved else { return .loading }
        if isRunOperationInProgress, currentRunOperationKind == .discardTurn {
            return .blocked("This turn is being discarded. Your draft is retained")
        }
        if !followUpQueue.isEmpty {
            return PromptSubmissionPlan.make(
                composingNewConversation: isComposingNewConversation,
                run: activeRun,
                selectedAgent: promptContinuationAgentSummary,
                agentDisplayName: promptContinuationAgentDisplayName,
                hasQueuedFollowUps: true,
                syncBack: settings.syncBack,
                hasSelectedResumeSession: hasSelectedResumeSession
            )
        }
        if let workspace = selectedWorkspace,
           activeRun?.runId != workspace.lastRunId {
            if workspace.removalLooksActive {
                return .blocked("A newer turn is still running in this workspace")
            }
            if activeRun == nil, !isComposingNewConversation, workspace.hasUnresolvedResultSelection {
                return .blocked("A newer turn is waiting for review")
            }
        }
        if let run = activeRun,
           let workspace = selectedWorkspace,
           let reason = earlierConversationContinuationBlockReason(
               run: run,
               workspace: workspace,
               isLatestConversationTurn: isViewingLatestConversationTurn
           ) {
            return .blocked(reason)
        }
        if isRunOperationInProgress {
            guard activeRun != nil, isViewingLatestConversationTurn,
                  settings.syncBack, daemonOwnsFollowUpQueue() else {
                return .blocked("The continuation source is being updated. Your draft is retained")
            }
            return .enqueue
        }
        return PromptSubmissionPlan.make(
            composingNewConversation: isComposingNewConversation,
            run: activeRun,
            selectedAgent: promptContinuationAgentSummary,
            agentDisplayName: promptContinuationAgentDisplayName,
            hasQueuedFollowUps: !followUpQueue.isEmpty,
            syncBack: settings.syncBack,
            hasSelectedResumeSession: hasSelectedResumeSession
        )
    }

    // Reading progress is not submission readiness. Only a coherent, scoped
    // destination can authorize a command while its transcript is still loading.
    var promptDestinationIsResolved: Bool {
        guard let workspace = selectedWorkspace else { return false }
        if let destination = conversationNavigationTarget,
           destination.belongsTo(machineID: currentMachineContextID,
               workspaceID: selectedWorkspaceID, requestID: conversationNavigationRequestID) {
            guard destination.conversationID == selectedConversationID else { return false }
            if destination.runID != nil {
                guard let visibleID = activeRun?.runId,
                      navigationAcceptsRun(visibleID, requestID: destination.requestID) else { return false }
            }
        }
        if remoteTurnTransitionInProgress, activeRun?.runId != workspace.lastRunId { return false }
        if isComposingNewConversation { return true }
        if let run = activeRun {
            guard sameConversationWorkspace(run.workspace, workspace.path) else { return false }
            if let conversation = selectedConversation {
                return conversation.runs.contains { $0.runId == run.runId }
            }
            return true
        }
        if let conversation = selectedConversation {
            if conversation.opensResumeHistory, let session = conversation.resumeSession {
                return settings.resumeSessionID == session.sessionId
            }
            if !conversation.latestRunID.isEmpty { return false }
        }
        return !isSelectedWorkspaceContextHydrating && workspace.lastRunId.isEmpty
    }

    var promptContinuationAgentSummary: RunAgentSummary? {
        guard let run = activeRun else { return nil }
        if let finalized = run.finalizedAgent {
            return run.agents.first { $0.agent == finalized }
        }
        if !run.isWorking, let key = selectedAgentContextKey(),
           !selectedAgentContextsWhileWorking.contains(key),
           let reading = selectedAgentsByContext[key] {
            return run.agents.first { $0.agent == reading }
        }
        if !run.isWorking, let key = selectedAgentContextKey(),
           let choice = continuationSelections[key] {
            // turns.continue carries an explicit Agent. A queue-selection RPC
            // neither authorizes it nor replaces it with the last server choice.
            return run.agents.first { $0.agent == choice.agent }
        }
        if !run.isWorking, let confirmed = daemonFollowUpSnapshot,
           confirmed.workspaceId == selectedWorkspaceID, confirmed.runId == run.runId,
           let agent = confirmed.selectedAgent {
            return run.agents.first { $0.agent == agent }
        }
        return selectedAgentSummary
    }

    private var promptContinuationAgentDisplayName: String? {
        guard let run = activeRun,
              let agent = promptContinuationAgentSummary else { return nil }
        return run.displayName(forAgent: agent.agent, language: appLanguage)
    }

    var promptComposerContextText: String? {
        if shouldDeferPrompt { return nil }
        if promptSubmissionPlan == .enqueue, let context = continuationSelectionContextText { return context }
        if isPreviewingUnusedAgentResult,
           let finalized = activeRun?.finalizedAgent {
            let name = activeRun?.displayName(forAgent: finalized, language: appLanguage)
                ?? appLanguage.text("Agent \(finalized)", "Agent \(finalized)")
            return appLanguage.text(
                "仅预览；此对话将从 \(name) 继续",
                "Preview only; this conversation continues from \(name)"
            )
        }
        return promptSubmissionPlan.visibleContextText(language: appLanguage)
    }

    var promptSubmissionActionDescription: String? {
        if shouldDeferPrompt { return appLanguage.text("加入队列", "Queue message") }
        if promptSubmissionPlan == .enqueue, continuationSelectionNeedsConfirmation {
            return appLanguage.text("准备续问", "Prepare follow-up")
        }
        return promptSubmissionPlan.contextText(language: appLanguage)
    }

    var promptComposerPlaceholder: String {
        pendingPromptSubmission == nil
            ? promptSubmissionPlan.promptPlaceholder(language: appLanguage)
            : appLanguage.text("输入下一条消息", "Message")
    }

    var promptComposerContextIsRelevant: Bool {
        guard let submission = pendingPromptSubmission else { return true }
        guard let result = acceptedPromptResultsByWorkspace[submission.contextKey],
              let latestRunID = selectedWorkspace?.lastRunId else { return false }
        return !latestRunID.isEmpty && latestRunID != result.runID
            && latestRunID != submission.sourceRunID
    }

    var promptComposerContextSystemImage: String {
        if isPreviewingUnusedAgentResult { return "eye" }
        return promptSubmissionPlan.contextSystemImage
    }

    var promptComposerRecoveryAction: PromptComposerRecoveryAction? {
        if remoteTurnTransitionInProgress || isRunOperationInProgress { return nil }
        return promptSubmissionPlan.recoveryAction
    }

    var isPreviewingUnusedAgentResult: Bool {
        guard let run = activeRun,
              run.normalizedStatus == "finalized",
              let finalized = run.finalizedAgent,
              let selectedAgent else { return false }
        return selectedAgent != finalized
    }

    private var hasSelectedResumeSession: Bool {
        usableResumeSessionID(settings.resumeSessionID) != nil
    }

    var followUpIsDispatching: Bool {
        guard !followUpQueue.isEmpty, let remote = daemonFollowUpSnapshot,
              remote.workspaceId == selectedWorkspaceID, remote.runId == followUpRunID else { return false }
        return remote.state == "dispatching" || followUpQueue.first?.dispatch != nil
    }

    var followUpStatusText: String {
        guard !followUpQueue.isEmpty else { return "" }
        if followUpQueue.first?.isPaused == true {
            if followUpQueue.first?.lastError == "Queue paused after stopping this turn." {
                return appLanguage.text("已停止本轮，队列已暂停", "Turn stopped; queue paused")
            }
            return appLanguage.text("排队提问需要处理", "Queued follow-up needs attention")
        }
        if followUpQueue.allSatisfy(\.needsDaemonAcknowledgement) {
            return appLanguage.text("排队消息", "Queued messages")
        }
        let remote = daemonFollowUpSnapshot.flatMap {
            $0.workspaceId == selectedWorkspaceID && $0.runId == followUpRunID ? $0 : nil
        }
        if followUpIsDispatching {
            return appLanguage.text("正在续接原会话", "Continuing the original session")
        }
        if let run = activeRun, run.isWorking {
            return appLanguage.text("当前轮完成后发送", "Send after the current turn finishes")
        }
        let source = remote?.selectedAgent.flatMap { selected in
            activeRun?.agents.first { $0.agent == selected }
        } ?? selectedAgentSummary
        if let seconds = followUpSecondsRemaining {
            let agent = source?.name
                ?? appLanguage.text("所选 Agent", "selected Agent")
            return appLanguage.text("\(seconds) 秒后从 \(agent) 开始", "Starts from \(agent) in \(seconds)s")
        }
        if source?.mayContinue == true {
            let agent = source?.name
                ?? appLanguage.text("所选 Agent", "selected Agent")
            return appLanguage.text("准备从 \(agent) 继续", "Ready from \(agent)")
        }
        return appLanguage.text("等待可继续的 Agent", "Waiting for a successful Agent")
    }

    var canNavigateToOlderPrompt: Bool {
        !promptHistoryEntries.isEmpty && promptHistoryIndex > 0
    }

    var canNavigateToNewerPrompt: Bool {
        promptHistoryIndex < promptHistoryEntries.count
    }

    func promptSubmissionStatus(for workspaceID: String) -> PromptSubmissionStatus? {
        if selectedWorkspaceID == workspaceID,
           let pendingPromptSubmission,
           pendingPromptSubmission.workspaceID == workspaceID {
            return pendingPromptSubmission.status
        }
        return promptOutboxByWorkspace[
            machineWorkspaceContextKey(workspaceID: workspaceID)
        ]?.status
    }
}
