import Foundation

@MainActor
extension AppModel {
    func persistWorkspaceDirectory(_ workspaces: [RemoteWorkspace], machineID: String) {
        let entries = workspaces.map {
            CachedWorkspaceDirectoryEntry(workspaceID: $0.workspaceId, path: $0.path,
                name: $0.name, lastRunID: $0.lastRunId, lastResumeSessionID: $0.lastResumeSessionId)
        }
        workspaceDirectoriesByMachine[machineID] = entries
        desktopStateStore.saveWorkspaceDirectory(entries, machineID: machineID)
        if isCurrentMachine(machineID) {
            workspaceDirectoryIsCached = false
            workspaceDirectoryLoaded = true
        }
    }

    func restoreWorkspaceDirectoryFromCache(machineID: String, requestID: Int) async {
        guard isCurrentMachine(machineID), workspaces.isEmpty, !workspaceDirectoryLoaded else { return }
        let selectionID = workspaceSelectionRequestID
        let generation = workspaceContextGeneration
        let saved = workspaceDirectoriesByMachine[machineID]
        var workspaceIDs = Set(saved?.map(\.workspaceID) ?? [])
        if saved == nil {
            // Older versions persisted navigation and conversation bodies, but no directory.
            // Do not resurrect every historical cache, or infer identity across machines.
            workspaceIDs.formUnion(selectedConversationByWorkspace.keys.compactMap {
                MachineContextKey.workspaceID(from: $0, machineID: machineID)
            })
            if let remembered = selectedWorkspaceByMachine[machineID] { workspaceIDs.insert(remembered) }
        }
        let records = workspaceIDs.isEmpty ? [] : await runViewStateStore.hydrateWorkspaces(
            machineID: machineID, workspaceIDs: workspaceIDs
        )
        guard isCurrentMachine(machineID), requestID == refreshRequestID,
              selectionID == workspaceSelectionRequestID, generation == workspaceContextGeneration,
              workspaces.isEmpty, !workspaceDirectoryLoaded else { return }
        let entries = saved ?? workspaceIDs.sorted().compactMap { workspaceID -> CachedWorkspaceDirectoryEntry? in
            let runs = records.filter { $0.key.workspaceID == workspaceID }.compactMap(\.runSummary)
            guard let run = workspaceConversationSummaries(from: runs).first?.latestRun,
                  run.workspace.hasPrefix("/"),
                  runs.allSatisfy({ sameConversationWorkspace($0.workspace, run.workspace) }) else { return nil }
            return CachedWorkspaceDirectoryEntry(workspaceID: workspaceID, path: run.workspace,
                name: URL(fileURLWithPath: run.workspace).lastPathComponent,
                lastRunID: run.runId, lastResumeSessionID: "")
        }
        for record in records {
            if let run = record.runSummary { runSummariesByContext[record.key] = run }
        }
        workspaces = entries.map { entry in
            reconciledWorkspaceHead(RemoteWorkspace(workspaceId: entry.workspaceID,
                path: entry.path, name: entry.name, lastRunId: entry.lastRunID,
                lastResumeSessionId: entry.lastResumeSessionID, runStatus: nil,
                runWorkerActive: nil, runAgentsTotal: nil, runAgentsFinished: nil,
                runAgentsFailed: nil, selected: false, available: false, readable: false,
                writable: false, settings: nil), machineID: machineID)
        }
        workspaceDirectoryIsCached = !workspaces.isEmpty
        guard selectedWorkspaceID == nil,
              let workspaceID = rememberedWorkspaceID(machineID: machineID, available: workspaces)
                ?? workspaces.first?.workspaceId,
              let workspace = workspaces.first(where: { $0.workspaceId == workspaceID }) else { return }
        selectedWorkspaceID = workspaceID
        resetRunView()
        workspaceEntryDefaultNavigationRequestID = conversationNavigationRequestID
        settings = resolvedWorkspaceSettings(
            cached: settingsByWorkspace[machineWorkspaceContextKey(workspaceID: workspaceID)],
            remote: nil, preserving: dirtySettingsFieldsByWorkspace[
                machineWorkspaceContextKey(workspaceID: workspaceID)] ?? []
        )
        installCachedWorkspaceConversation(records.filter { $0.key.workspaceID == workspaceID },
            workspace: workspace, machineID: machineID)
        preparePromptHistoryContext()
        restoreVisiblePromptSubmission()
        refreshVisibleRunOperationState()
        rememberWorkspaceSelection()
    }

    func applyDesktopStateSnapshot(_ snapshot: DesktopStateSnapshot) {
        workspaceDirectoriesByMachine = snapshot.workspaceDirectoriesByMachine.merging(
            workspaceDirectoriesByMachine, uniquingKeysWith: { _, current in current }
        )
        for (key, record) in snapshot.conversationTitlesByContext {
            let current = domainRepositories.conversations.titlesByContext[key]
            if current == nil || (record.titleRevision ?? 0) > (current?.titleRevision ?? 0) {
                domainRepositories.conversations.titlesByContext[key] = record
            }
        }
        settingsByWorkspace = snapshot.settingsByWorkspace.merging(
            settingsByWorkspace,
            uniquingKeysWith: { _, current in current }
        )
        dirtySettingsFieldsByWorkspace = snapshot.dirtySettingsFieldsByWorkspace.merging(
            dirtySettingsFieldsByWorkspace,
            uniquingKeysWith: { saved, current in saved.union(current) }
        )
        defaultSettingsStates = snapshot.defaultSettingsByMachine.merging(
            defaultSettingsStates,
            uniquingKeysWith: { _, current in current }
        )
        promptDraftsByContext = snapshot.promptDraftsByContext.merging(
            promptDraftsByContext,
            uniquingKeysWith: { _, current in current }
        )
        promptSelectionsByContext = snapshot.promptSelectionsByContext.merging(
            promptSelectionsByContext, uniquingKeysWith: { _, current in current }
        )
        persistedFollowUpQueues = snapshot.followUpQueuesByContext.merging(
            persistedFollowUpQueues,
            uniquingKeysWith: { _, current in current }
        )
        promptOutboxByWorkspace = snapshot.promptOutboxByWorkspace.merging(
            promptOutboxByWorkspace,
            uniquingKeysWith: { _, current in current }
        )
        acceptedPromptResultsByWorkspace = snapshot.acceptedPromptResultsByWorkspace.merging(
            acceptedPromptResultsByWorkspace,
            uniquingKeysWith: { _, current in current }
        )
        deferredPromptsByWorkspace = snapshot.deferredPromptsByWorkspace.merging(
            deferredPromptsByWorkspace, uniquingKeysWith: { _, current in current }
        )
        selectedAgentsByContext = snapshot.selectedAgentsByContext.merging(
            selectedAgentsByContext,
            uniquingKeysWith: { _, current in current }
        )
        selectedAgentContextsWhileWorking.formUnion(
            snapshot.selectedAgentContextsWhileWorking.filter {
                selectedAgentsByContext[$0] != nil
            }
        )
        selectedWorkspaceByMachine = snapshot.selectedWorkspaceByMachine.merging(
            selectedWorkspaceByMachine,
            uniquingKeysWith: { _, current in current }
        )
        selectedConversationByWorkspace = snapshot.selectedConversationByWorkspace.merging(
            selectedConversationByWorkspace,
            uniquingKeysWith: { _, current in current }
        )
        feedback.mergePersistedOutbox(snapshot.feedbackOutboxByID)
        if let notice = snapshot.notice { errorMessage = notice }
        if completionNotificationsEnabled { DesktopNotifier.prepare() }
    }

}
