import Foundation

struct RunOperationIssue: Equatable, Sendable {
    let kind: RunOperationKind
    let message: String
    let workspace: String
    var needsReadback = false

    func isResolved(by run: RunSummary) -> Bool {
        guard run.workspace == workspace else { return false }
        switch kind {
        case .stopTurn:
            return !run.isWorking
        case .discardTurn:
            return !run.isWorking && run.normalizedStatus == "discarded"
        case .useResult(let agent):
            return !run.isWorking && run.normalizedStatus == "finalized"
                && run.finalizedAgent == agent && run.syncedBack
        case .recoverFinalization, .retryCleanup:
            return !run.isWorking && run.normalizedStatus == "finalized" && run.syncedBack
        case .stopAgent(let agent):
            return run.agents.first { $0.agent == agent }?.isTerminal == true
        case .retryAgent, .updateRecommendation, .addAgents:
            return false
        }
    }
}

@MainActor
extension AppModel {
    func reconcileRunOperationIssue(_ run: RunSummary, key: WorkspaceRunViewKey) {
        guard run.runId == key.runID else { return }
        let contextKey = MachineContextKey.run(machineID: key.machineID,
            workspaceID: key.workspaceID, runID: key.runID)
        if runOperationIssues[contextKey]?.isResolved(by: run) == true {
            runOperationIssues.removeValue(forKey: contextKey)
        } else if runOperationIssues[contextKey]?.workspace == run.workspace {
            runOperationIssues[contextKey]?.needsReadback = false
        }
    }

    func currentRunOperationContext() -> RunOperationContext? {
        guard let workspaceID = selectedWorkspaceID,
              let runID = activeRun?.runId else { return nil }
        let target = client.currentTarget()
        return RunOperationContext(
            machineID: target.machineID,
            target: target,
            workspaceID: workspaceID,
            runID: runID,
            settings: settings
        )
    }

    func isCurrentRunOperation(_ context: RunOperationContext) -> Bool {
        isCurrentMachine(context.machineID)
            && selectedWorkspaceID == context.workspaceID
            && activeRun?.runId == context.runID
    }

    @discardableResult
    func recordRunOperationResult(_ incoming: RunSummary, context: RunOperationContext,
                                  workspace: String) throws -> RunSummary {
        guard sameConversationWorkspace(incoming.workspace, workspace) else {
            throw PcrdClientError.invalidResponse
        }
        let key = WorkspaceRunViewKey(machineID: context.machineID,
            workspaceID: context.workspaceID, runID: context.runID)
        return try recordRunObservation(incoming, for: key)
    }

    var currentRunOperationKind: RunOperationKind? {
        currentRunOperationContext().flatMap { runOperationsByContext[$0.key] }
    }

    func refreshVisibleRunOperationState() {
        let updated: Bool
        guard let workspaceID = selectedWorkspaceID else {
            updated = false
            if isRunOperationInProgress != updated {
                isRunOperationInProgress = updated
            }
            return
        }
        let runOperationIsActive = currentRunOperationContext().map {
            activeRunOperationKeys.contains($0.key)
        } ?? false
        updated = activeWorkspaceOperationIDs.contains(
            machineWorkspaceContextKey(workspaceID: workspaceID)
        )
            || runOperationIsActive
        if isRunOperationInProgress != updated {
            isRunOperationInProgress = updated
        }
    }

    func performWorkspaceOperation(
        _ workspaceID: String,
        operation: @escaping (WorkspaceOperationContext) async throws -> Void
    ) async {
        let target = client.currentTarget()
        let context = WorkspaceOperationContext(
            machineID: target.machineID,
            target: target,
            workspaceID: workspaceID
        )
        guard activeWorkspaceOperationIDs.insert(context.key).inserted else { return }
        refreshVisibleRunOperationState()
        defer {
            activeWorkspaceOperationIDs.remove(context.key)
            refreshVisibleRunOperationState()
        }
        do {
            try await operation(context)
        } catch {
            reportWorkspaceOperationFailure(appLanguage.userMessage(for: error), context: context)
        }
    }

    func reportWorkspaceOperationFailure(_ message: String, context: WorkspaceOperationContext) {
        guard isCurrentMachine(context.machineID) else { return }
        let name = workspaces.first { $0.workspaceId == context.workspaceID }?.name
            ?? context.workspaceID
        // A sidebar action may target a workspace other than the open conversation.
        errorMessage = appLanguage.text("工作区「\(name)」：\(message)", "Workspace \"\(name)\": \(message)")
    }

    func performRunOperation(
        _ context: RunOperationContext,
        kind: RunOperationKind,
        operation: @escaping (RunOperationContext) async throws -> Void
    ) async {
        guard activeRunOperationKeys.insert(context.key).inserted else { return }
        runOperationsByContext[context.key] = kind
        runOperationIssues.removeValue(forKey: context.key)
        let workspace = activeRun?.runId == context.runID ? activeRun?.workspace : nil
        refreshVisibleRunOperationState()
        defer {
            activeRunOperationKeys.remove(context.key)
            runOperationsByContext.removeValue(forKey: context.key)
            refreshVisibleRunOperationState()
        }
        do {
            try await operation(context)
        } catch {
            if let workspace {
                runOperationIssues[context.key] = RunOperationIssue(kind: kind,
                    message: appLanguage.userMessage(for: error), workspace: workspace,
                    needsReadback: true)
                // Verify a lost mutation reply against server state, never repeat it.
                if let run: RunSummary = try? await client.call(target: context.target,
                    "turns.get", body: GetRunBody(runId: context.runID,
                        includeEvents: false, cursor: 0, eventLimit: 1), priority: .utility) {
                    _ = try? recordRunOperationResult(run, context: context, workspace: workspace)
                }
            }
        }
    }

    func setPendingStorageOperation(
        _ operation: PendingStorageOperation,
        message: String,
        machineID explicitMachineID: String? = nil
    ) {
        let machineID = explicitMachineID ?? currentMachineContextID
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: operation.workspaceID,
            machineID: machineID
        )
        pendingStorageOperationsByWorkspace[workspaceKey] = operation
        storageConfirmationMessagesByWorkspace[workspaceKey] = message
        guard isCurrentMachine(machineID),
              selectedWorkspaceID == operation.workspaceID else { return }
        pendingStorageOperation = operation
        storageConfirmationMessage = message
    }

    func clearPendingStorageOperation(
        workspaceID: String,
        machineID explicitMachineID: String? = nil
    ) {
        let machineID = explicitMachineID ?? currentMachineContextID
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        pendingStorageOperationsByWorkspace.removeValue(forKey: workspaceKey)
        storageConfirmationMessagesByWorkspace.removeValue(forKey: workspaceKey)
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspaceID else { return }
        pendingStorageOperation = nil
        storageConfirmationMessage = nil
    }

    func resetRunView() {
        workspaceEntryDefaultNavigationRequestID = nil
        conversationNavigationRequestID += 1
        workspaceContextGeneration += 1
        conversationIndexRequestID += 1
        conversationIndexTask?.cancel()
        conversationIndexTask = nil
        errorMessage = nil
        liveConversationWarning = nil
        workspaceContextLoadingID = nil
        remoteTurnTransition = nil
        dismissWorkspaceDocument()
        clearFollowUpQueue(persist: false)
        diffPresented = false
        diffLoading = false
        diffText = ""
        activeRun = nil
        workspaceConversations = []
        selectedConversationID = nil
        conversationNavigationTarget = nil
        historicalConversationTurn = nil
        conversationIndexLoading = false
        selectedAgent = nil
        pendingAgentSelection = nil
        agentSelectionRequestID += 1
        transcripts = [:]
        agentDetailLoadTokens.removeAll()
        agentAnswerLoadTokens.removeAll()
        agentAnswerReadRetries.removeAll()
        loadingAgents.removeAll()
        loadingAgentKeys.removeAll()
        resumeHistory = []
        resumeHistoryRequestID += 1
        resumeSessions = []
        modelOptionErrors.removeAll()
        modelOptionsError = nil
        promptHistoryEntries = []
        promptHistoryIndex = 0
        pendingPromptSubmission = nil
        let selectedWorkspaceKey = selectedWorkspaceID.map {
            machineWorkspaceContextKey(workspaceID: $0)
        }
        pendingStorageOperation = selectedWorkspaceKey.flatMap {
            pendingStorageOperationsByWorkspace[$0]
        }
        storageConfirmationMessage = selectedWorkspaceKey.flatMap {
            storageConfirmationMessagesByWorkspace[$0]
        }
        refreshVisibleRunOperationState()
    }

    func isCurrentWorkspaceContext(
        _ workspaceID: String,
        generation: Int
    ) -> Bool {
        selectedWorkspaceID == workspaceID && workspaceContextGeneration == generation
    }
}
