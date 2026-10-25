import Foundation

extension AppModel {
    func scheduleVisibleAgentPreload(target: PcrdRequestTarget? = nil) {
        guard let run = activeRun, let workspace = selectedWorkspace else { return }
        let target = target ?? client.currentTarget()
        let key = WorkspaceRunViewKey(machineID: target.machineID,
            workspaceID: workspace.workspaceId, runID: run.runId)
        if runSummariesByContext[key].map({ $0.eventCount <= run.eventCount }) ?? true {
            runSummariesByContext[key] = run
        }
        // Navigation selects a reader's snapshot; it never owns that reader.
        scheduleConversationRead(workspace: workspace, key: key, target: target)
    }
}
