import Foundation

@MainActor
extension AppModel {
    func agentDiffSummaryKey(
        machineID: String,
        workspaceID: String,
        runID: String,
        agent: Int
    ) -> String {
        agentLoadingKey(
            machineID: machineID,
            workspaceID: workspaceID,
            runID: runID,
            agent: agent
        ) + "\u{1f}diff-summary"
    }

    func prefetchSelectedAgentDiffSummary(refresh: Bool = false) async {
        guard let workspaceID = selectedWorkspaceID,
              let run = activeRun,
              let agent = selectedAgentSummary else { return }
        let target = client.currentTarget()
        let key = agentDiffSummaryKey(
            machineID: target.machineID,
            workspaceID: workspaceID,
            runID: run.runId,
            agent: agent.agent
        )
        guard !agentDiffSummaryLoadingKeys.contains(key),
              refresh || !agentDiffSummaryTerminalKeys.contains(key) else { return }

        agentDiffSummaryLoadingKeys.insert(key)
        defer { agentDiffSummaryLoadingKeys.remove(key) }
        do {
            var header = ""
            var cursor = 0
            var snapshotID: String?
            while true {
                let page: RunDiffPage = try await client.call(
                    target: target,
                    "runs.diff",
                    body: DiffBody(
                        runId: run.runId,
                        agent: agent.agent,
                        cursor: cursor,
                        limit: 50_000,
                        snapshotId: snapshotID
                    )
                )
                try Task.checkCancellation()
                snapshotID = page.snapshotId
                header += page.text
                if header.contains("\n\n") || !page.hasMore { break }
                guard page.nextCursor > cursor else { throw PcrdClientError.invalidResponse }
                cursor = page.nextCursor
            }
            let summary = AgentDiffSummary.parse(header)
            if agentDiffSummaries[key] != summary { agentDiffSummaries[key] = summary }
            if agent.isTerminal { agentDiffSummaryTerminalKeys.insert(key) }
        } catch is CancellationError {
            return
        } catch {
            if agentDiffSummaries[key] == nil { agentDiffSummaries[key] = AgentDiffSummary(unavailable: true) }
        }
    }
}
