import Foundation

@MainActor
extension AppModel {
    func unavailableRunIDs(workspaceID: String, machineID: String) -> Set<String> {
        let machineID = MachineContextKey.normalizeMachineID(machineID)
        return Set(unavailableRunContexts.filter {
            $0.machineID == machineID && $0.workspaceID == workspaceID
        }.map(\.runID))
    }

    func reconcileRunAvailability(
        cachedRuns: [RunSummary],
        fetchedRuns: [RunSummary],
        workspaceID: String,
        machineID: String,
        completeAsOf: Date?
    ) {
        let fetchedIDs = Set(fetchedRuns.map(\.runId))
        for run in fetchedRuns {
            unavailableRunContexts.remove(WorkspaceRunViewKey(
                machineID: machineID, workspaceID: workspaceID, runID: run.runId
            ))
        }
        // Absence from a partial page or a failed request proves nothing. Runs
        // created while the index was loading are not part of its snapshot.
        guard let completeAsOf else { return }
        for run in cachedRuns where !fetchedIDs.contains(run.runId) {
            guard let createdAt = parseInternetDate(run.createdAt),
                  createdAt < completeAsOf else { continue }
            markRunUnavailable(WorkspaceRunViewKey(
                machineID: machineID, workspaceID: workspaceID, runID: run.runId
            ))
        }
    }

    func markRunUnavailable(_ key: WorkspaceRunViewKey) {
        unavailableRunContexts.insert(key)
    }

    /// Old pcrd versions expired execution records, even for adopted
    /// conversations. Their promoted Codex session is an independent, durable
    /// history source; it must not be polled as a still-existing execution.
    @discardableResult
    func recoverUnavailableRunHistory(_ runID: String, target: PcrdRequestTarget) async -> Bool {
        guard isCurrentMachine(target.machineID),
              let workspace = selectedWorkspace,
              let conversation = selectedConversation,
              conversation.runs.contains(where: { $0.runId == runID }) else { return false }
        markRunUnavailable(WorkspaceRunViewKey(
            machineID: target.machineID, workspaceID: workspace.workspaceId, runID: runID
        ))
        applyWorkspaceConversationIndex(
            [], resumeSessions: [], workspace: workspace, machineID: target.machineID
        )
        guard let recovered = selectedConversation,
              recovered.id == conversation.id,
              recovered.opensResumeHistory,
              let session = recovered.resumeSession else { return false }

        cacheVisibleRunViewState()
        let cachedHistory = resumeHistory
        clearVisibleConversationProjection(invalidateRequests: false)
        resumeHistory = cachedHistory
        errorNotice = nil
        settings.resumeSessionID = session.sessionId
        settingsByWorkspace[machineWorkspaceContextKey(workspaceID: workspace.workspaceId)] = settings
        desktopStateStore.saveSettings(settingsByWorkspace)
        // The identity and draft stay in the same conversation. Only its data
        // source changes; no synthetic Run or accept/finalize action is created.
        await loadResumeHistory(session.sessionId, reportErrors: true)
        return true
    }
}
