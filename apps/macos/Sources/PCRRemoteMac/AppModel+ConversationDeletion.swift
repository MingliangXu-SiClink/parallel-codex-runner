import Foundation

@MainActor
extension AppModel {
    func requestConversationDelete(_ conversation: WorkspaceConversationSummary? = nil) {
        guard let conversation = conversation ?? selectedConversation,
              let workspaceID = selectedWorkspaceID else { return }
        conversationDeleteRequest = ConversationDeleteRequest(machineID: currentMachineContextID,
            workspaceID: workspaceID, conversation: conversation)
    }

    func conversationDeleteBlockReason(_ conversation: WorkspaceConversationSummary) -> String? {
        let runIDs = Set(conversation.runs.map(\.runId))
        let workspaceID = selectedWorkspaceID ?? ""
        let visibleRunIsActive = activeRun.map {
            runIDs.contains($0.runId) && ($0.isWorking || $0.hasUnresolvedFinalization)
        } == true
        let workspaceRunIsActive = selectedWorkspace.map {
            runIDs.contains($0.lastRunId) && $0.removalLooksActive
        } == true
        let operationIsActive = runIDs.contains {
            let key = machineRunContextKey(workspaceID: workspaceID, runID: $0)
            return activeRunOperationKeys.contains(key) || runOperationsByContext[key] != nil
        }
        if visibleRunIsActive || workspaceRunIsActive || operationIsActive
            || conversation.runs.contains(where: { $0.isWorking || $0.hasUnresolvedFinalization }) {
            return appLanguage.text("此对话仍有任务运行或等待收尾，请结束后再删除。",
                                    "This conversation has an active or unfinished task. Delete it after the task finishes.")
        }
        let submissionIsPending = pendingPromptSubmission.map {
            guard $0.workspaceID == workspaceID, isCurrentMachine($0.machineID) else { return false }
            return $0.sourceRunID.map { runIDs.contains($0) } == true
                || conversationDeletionAliases(conversation).contains("session:" + $0.resumeSessionID)
                || selectedConversationID == conversation.id
        } == true
        if conversation.runs.contains(where: { run in
            let key = machineRunContextKey(workspaceID: workspaceID, runID: run.runId)
            return persistedFollowUpQueues[key]?.items.isEmpty == false
                || (activeRun?.runId == run.runId && !followUpQueue.isEmpty)
        }) || submissionIsPending {
            return appLanguage.text("此对话还有待发送的消息，请处理后再删除。",
                                    "Resolve the pending messages in this conversation before deleting it.")
        }
        return nil
    }

    func deleteConversation(_ request: ConversationDeleteRequest) throws {
        guard isCurrentMachine(request.machineID), selectedWorkspaceID == request.workspaceID else {
            throw NSError(domain: "PCR", code: 1, userInfo: [NSLocalizedDescriptionKey:
                appLanguage.text("设备或工作区已切换，请重新选择要删除的对话。",
                                 "The computer or workspace changed. Select the conversation again.")])
        }
        let conversation = conversationMatchingNavigationID(request.conversation.id, in: workspaceConversations)
            ?? request.conversation
        if let reason = conversationDeleteBlockReason(conversation) {
            throw NSError(domain: "PCR", code: 2, userInfo: [NSLocalizedDescriptionKey: reason])
        }
        try conversationTrash.delete(conversation, machineID: request.machineID, workspaceID: request.workspaceID,
            machineName: selectedMachine?.name ?? appLanguage.text("此 Mac", "This Mac"),
            workspaceName: selectedWorkspace?.name ?? "")
    }

    func restoreDeletedConversation(_ record: DeletedConversation) throws {
        try conversationTrash.restore(record)
        if lastDeletedConversation?.id == record.id { lastDeletedConversation = nil }
    }

    var visibleDeletedConversation: DeletedConversation? {
        guard let workspaceID = selectedWorkspaceID, let selectedConversationID,
              selectedConversationID != Self.newConversationSelectionID else { return nil }
        var aliases: Set<String> = ["conversation:" + selectedConversationID, "run:" + selectedConversationID]
        if selectedConversationID.hasPrefix("resume:") {
            aliases.insert("session:" + String(selectedConversationID.dropFirst("resume:".count)))
        }
        if let run = activeRun {
            aliases.formUnion(conversationDeletionAliases(WorkspaceConversationSummary(
                id: run.conversationKey, title: "", runs: [run], resumeSession: nil)))
        }
        return conversationTrash.records.first {
            $0.machineID == currentMachineContextID && $0.workspaceID == workspaceID
                && !$0.aliases.isDisjoint(with: aliases)
        }
    }

    func applyConversationTrashChange(_ change: ConversationTrashChange) {
        objectWillChange.send()
        switch change {
        case .deleted(let record):
            guard isCurrentMachine(record.machineID), selectedWorkspaceID == record.workspaceID else { return }
            let selected = selectedConversation.map {
                record.matches($0, machineID: currentMachineContextID, workspaceID: record.workspaceID)
            } == true || visibleDeletedConversation?.id == record.id
            if selected {
                cacheVisibleRunViewState()
                conversationNavigationRequestID += 1
                clearVisibleConversationProjection()
                selectedConversationID = Self.newConversationSelectionID
                conversationNavigationTarget = nil
                rememberConversationNavigation()
                preparePromptHistoryContext()
                promptFocusRequestSequence &+= 1
            }
            workspaceConversations = conversationTrash.visible(workspaceConversations,
                machineID: currentMachineContextID, workspaceID: record.workspaceID)
            lastDeletedConversation = record
        case .restored(let record):
            if lastDeletedConversation.map({ !$0.aliases.isDisjoint(with: record.aliases) }) == true {
                lastDeletedConversation = nil
            }
            guard isCurrentMachine(record.machineID), let workspace = selectedWorkspace,
                  workspace.workspaceId == record.workspaceID else { return }
            let runs = runSummariesByContext.filter {
                $0.key.machineID == record.machineID && $0.key.workspaceID == record.workspaceID
            }.map(\.value)
            applyWorkspaceConversationIndex(runs, resumeSessions: resumeSessions,
                workspace: workspace, machineID: currentMachineContextID)
            guard daemonOnline else { return }
            let target = client.currentTarget()
            let generation = workspaceContextGeneration
            Task { await loadWorkspaceConversationIndex(workspace: workspace, target: target, generation: generation) }
        }
    }
}
