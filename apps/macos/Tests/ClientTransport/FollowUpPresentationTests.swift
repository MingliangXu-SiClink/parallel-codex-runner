import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct FollowUpPresentationTests {
    @Test(arguments: [2, 3])
    func cachedSuccessCannotHideAnAuthoritativeNewAttempt(eventCount: Int) throws {
        let previous = try run(status: "completed", synthesisStatus: "success")
        var refreshed = try run(status: "running", synthesisStatus: "running", eventCount: eventCount)
        #expect(visibleRunRefreshNeedsReload(current: previous, remote: refreshed,
                                            selectedAgent: nil, transcripts: [:]))
        refreshed.preserveTerminalAgentStates(from: previous)
        #expect(refreshed.agents[0].status == "success")
        #expect(refreshed.agents[1].status == "running")
    }

    @Test
    func claimedQueueIsNotPresentedAsWaitingForTheFinishedAgent() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.appLanguage = .chinese
        var item = QueuedFollowUp(prompt: "Next question")
        item.daemonAcknowledged = true
        model.followUpQueue = [item]
        model.selectedWorkspaceID = "workspace"
        model.followUpRunID = "run"

        model.activeRun = try run(status: "running", synthesisStatus: "running")
        #expect(model.followUpStatusText == "当前轮完成后发送")
        model.activeRun = try run(status: "running", synthesisStatus: "success")
        #expect(model.followUpStatusText == "当前轮完成后发送")
        model.activeRun = try run(status: "finalizing", synthesisStatus: "success")
        #expect(model.followUpStatusText == "当前轮完成后发送")
        model.daemonFollowUpSnapshot = queue(state: "dispatching")
        #expect(model.followUpStatusText == "正在续接原会话")
        #expect(model.followUpIsDispatching)
        model.daemonFollowUpSnapshot = queue(state: "dispatching", workspace: "other-workspace")
        #expect(model.followUpStatusText == "当前轮完成后发送")
        #expect(!model.followUpIsDispatching)
    }

    @Test
    func claimedHeadDoesNotCountAsAnUnsentMessage() {
        let items = [QueuedFollowUp(prompt: "First"), QueuedFollowUp(prompt: "Second")]
        #expect(ComposerQueueView.pendingCount(items: items, dispatching: false) == 2)
        #expect(ComposerQueueView.pendingCount(items: items, dispatching: true) == 1)
        #expect(ComposerQueueView.pendingCount(items: Array(items.prefix(1)), dispatching: true) == 0)
        #expect(ComposerQueueView.pendingCount(items: [], dispatching: true) == 0)
    }

    @Test
    func countdownNamesTheDaemonBranchNotTheCandidateBeingViewed() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.appLanguage = .chinese
        model.selectedWorkspaceID = "workspace"
        model.followUpRunID = "run"
        var item = QueuedFollowUp(prompt: "Next question")
        item.daemonAcknowledged = true
        model.followUpQueue = [item]
        model.activeRun = try run(status: "completed", synthesisStatus: "success")
        model.selectedAgent = 1
        model.followUpSecondsRemaining = 5
        model.daemonFollowUpSnapshot = queue(state: "countdown")
        #expect(model.followUpStatusText == "5 秒后从 AGENT-002 开始")
        model.clearFollowUpQueue(persist: false)
        #expect(model.daemonFollowUpSnapshot == nil)
        #expect(model.followUpStatusText.isEmpty)
    }

    private func queue(state: String, workspace: String = "workspace") -> RemoteFollowUpQueue {
        RemoteFollowUpQueue(workspaceId: workspace, runId: "run", items: [], selectedAgent: 2,
                            state: state, deadlineEpochMs: nil, lastError: nil,
                            generation: 1, lastCompletedItemId: nil)
    }

    @Test
    func completionUsesContinuationPolicyOnceAndThenHonorsManualChoice() throws {
        let running = try run(status: "running", synthesisStatus: "running")
        var completed = try run(status: "completed", synthesisStatus: "success")
        completed.continuationAgent = 2
        #expect(selectedAgentAfterRunUpdate(previousRun: running, currentSelection: 1,
                                            updatedRun: completed) == 2)
        #expect(selectedAgentAfterRunUpdate(previousRun: completed, currentSelection: 1,
                                            updatedRun: completed, explicitSelection: 1) == 1)
        #expect(selectedAgentForRestoredRun(explicitSelection: nil, cachedSelection: 1,
                                            run: completed) == 2)
    }

    @Test
    func restoringQueueUsesServerChoiceInsteadOfOldBrowsingMemory() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "workspace"
        let completed = try run(status: "completed", synthesisStatus: "success")
        model.activeRun = completed
        let key = try #require(model.selectedAgentContextKey())
        model.selectedAgentsByContext[key] = 1
        model.selectedAgentContextsWhileWorking.insert(key)
        var remote = queue(state: "countdown")
        remote.selectionSource = "automatic"
        model.daemonFollowUpSnapshot = remote
        #expect(model.rememberedSelectedAgent(for: completed, contextKey: key) == 2)
        #expect(model.selectedAgentsByContext[key] == nil)
        model.daemonFollowUpSnapshot = nil
        model.rememberSelectedAgent(1)
        #expect(model.rememberedSelectedAgent(for: completed, contextKey: key) == 1)
    }

    private func run(status: String, synthesisStatus: String, eventCount: Int = 2) throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"run","status":"\(status)","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-09-19T03:17:00Z","updatedAt":"2026-09-19T03:22:00Z","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":\(status != "completed"),"workerAvailable":true,"eventCount":\(eventCount),
         "agents":[
          {"agent":1,"name":"AGENT-001","role":"candidate","status":"success",
           "reasoningTokenCounts":{},"rejected":false,"recommended":false,"finalized":false},
          {"agent":2,"name":"AGENT-002","role":"synthesis","status":"\(synthesisStatus)",
           "reasoningTokenCounts":{},"rejected":false,"recommended":true,"finalized":false}]}
        """.utf8))
    }
}
