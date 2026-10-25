import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ConversationUsabilityTests {
    @Test(arguments: ["queued", "copying", "starting", "running", "waiting_for_models", "stopping"])
    func preparingAgentsAreNotMissingResultsBeforeTheirWorkspaceExists(status: String) throws {
        let data = Data("""
        {"runId":"pipeline","status":"running","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-10-05","updatedAt":"2026-10-05","config":{},"syncedBack":false,
         "workspacesDeleted":false,"codexHomesDeleted":false,"workerActive":true,
         "workerAvailable":true,"eventCount":0,"agents":[
          {"agent":1,"name":"AGENT-001","status":"\(status)","recommended":false,
           "finalized":false,"rejected":false,"workspaceAvailable":false,"reasoningTokenCounts":{}}]}
        """.utf8)
        let run = try JSONDecoder().decode(RunSummary.self, from: data)
        let agent = try #require(run.agents.first)
        #expect(agent.isRunning)
        #expect(conversationAgentGlyphStatus(run: run, agent: agent) == status)
        #expect(!agent.mayAccept)
        #expect(!agent.mayContinue)
    }

    @Test
    func webPortsNeverBecomeWorkspaceLineNumbers() throws {
        for reference in ["http://127.0.0.1:8891", "https://localhost:3000", "http://[::1]:8080", "https://example.com:8443/path?q=x#y"] {
            #expect(workspaceLinkKind(reference) == .external)
            #expect(externalWorkspaceLinkURL(reference)?.absoluteString == reference)
            #expect(resolveWorkspaceLink(reference, relativeTo: "docs/README.md") == reference)
        }
        for reference in ["src/app.swift:42", "/tmp/a.md:3:7", "file:///tmp/a.md"] {
            #expect(workspaceLinkKind(reference) == .workspaceFile)
        }
        #expect(workspaceLinkKind("javascript:alert(1)") == .unsupported)
        #expect(AppLanguage.chinese.localizedClientMessage("Only links to files in this workspace can be opened").contains("切换"))
    }

    @Test
    func workAndStreamingFinalAnswersHaveSeparateRolesWithoutLosingUpdates() {
        var blocks = [TranscriptBlock(id: "question", kind: .user, text: "Question")]
        let values: [(String, String, String?)] = [
            ("thought", "Planning", nil), ("output", "Found the cause", "assistant_progress"),
            ("command", "rg foo", nil), ("output", "Answer streaming", "assistant_final"),
        ]
        TranscriptFormatter.append(items: values.enumerated().map { index, value in
            RemoteTranscriptItem(id: "block-\(index)", eventId: index, kind: value.0, text: value.1,
                                 status: nil, operationId: nil, activityType: value.2)
        }, to: &blocks)
        let collapsed = TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1, expanded: false)
        #expect(collapsed.map(\.id) == ["question", "work-summary-run-1-block-0", "block-1",
                                      "work-summary-run-1-block-2", "block-3"])
        #expect(collapsed.last?.isFinalAnswer == true)
        let expanded = TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1, expanded: true)
        #expect(expanded.filter { $0.activityType?.hasPrefix("work_summary") == true }.count == 2)
        #expect(expanded.contains { $0.text == "Found the cause" })
        #expect(expanded.first { $0.text == "Found the cause" }?.isAssistantProgress == true)
        #expect(expanded.last?.text == "Answer streaming")
        let legacyLive = TranscriptFormatter.blocksWithWorkDisclosure([
            TranscriptBlock(id: "live", kind: .output, text: "Legacy streaming answer")
        ], runID: "run", agent: 1, expanded: false)
        #expect(legacyLive.first?.id == "live")
        let legacyCompleted = TranscriptFormatter.blocksWithWorkDisclosure([
            TranscriptBlock(id: "update", kind: .output, text: "Older progress update"),
            TranscriptBlock(id: "answer", kind: .output, text: "Final answer", isFinalAnswer: true),
        ], runID: "run", agent: 1, expanded: true)
        #expect(legacyCompleted.first { $0.id == "update" }?.isAssistantProgress == false)
        #expect(legacyCompleted.map(\.id) == ["update", "answer"])
        #expect(legacyCompleted.last?.isAssistantProgress == false)
    }

    @Test
    func fileGroupsDoNotHideOrMisclassifySourceNames() {
        for path in ["src/app.ts", "docs/build.md", "src/build.ts", "schema/database.sql"] {
            #expect(AgentFileCategory.classify(path) == .project)
        }
        for path in ["dist/assets/app.js", "node_modules/.vite/cache.js", "apps\\web\\build\\app.js",
                     "official-latest/.venv/bin/python", "venv/lib/python3.13/site-packages/package.py"] {
            #expect(AgentFileCategory.classify(path) == .generated)
        }
        for path in [".local/business/siclab.sqlite-shm", "data.db-wal", "logs/server.log", ".local/preview.log"] {
            #expect(AgentFileCategory.classify(path) == .runtime)
        }
    }

    @Test
    func foregroundAndDisabledNotificationsAreConsumedRatherThanDeferred() {
        #expect(DesktopNotifier.suppression(enabled: true, frontmostID: "pcr", appID: "pcr") == .foreground)
        #expect(DesktopNotifier.suppression(enabled: false, frontmostID: "other", appID: "pcr") == .disabled)
        #expect(DesktopNotifier.suppression(enabled: true, frontmostID: "other", appID: "pcr") == nil)
        #expect(DesktopNotifier.Outcome.foreground.consumed)
        #expect(DesktopNotifier.Outcome.notAuthorized.consumed)
        #expect(!DesktopNotifier.Outcome.failed.consumed)
    }

    @Test
    func inspectorContinuationMatchesTheComposerInsteadOfAnOldRecommendation() throws {
        let data = Data("""
        {"runId":"run","status":"completed","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-09-19","updatedAt":"2026-09-19","config":{},"syncedBack":false,
         "workspacesDeleted":false,"codexHomesDeleted":false,"workerActive":false,
         "workerAvailable":true,"eventCount":0,"continuationAgent":3,"agents":[
          {"agent":1,"name":"AGENT-001","status":"success","recommended":false,"finalized":false,
           "rejected":false,"canContinue":true,"workspaceAvailable":true,"reasoningTokenCounts":{}},
          {"agent":3,"name":"AGENT-003","status":"success","role":"synthesis","recommended":true,
           "finalized":false,"rejected":false,"canContinue":true,"workspaceAvailable":true,"reasoningTokenCounts":{}}]}
        """.utf8)
        func summary(status: String = "completed", finalizedAgent: Int? = nil) throws -> RunSummary {
            var object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            object["status"] = status
            object["finalizedAgent"] = finalizedAgent
            return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: object))
        }
        let run = try summary()
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, language: .english)
            == "Continue from Agent 1")
        #expect(ResultAgentContext.text(run: try summary(finalizedAgent: 3), viewedAgent: 1, language: .english)
            == "Continue from Synthesis 1 · Saved")
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, isLatestTurn: false, language: .english)
            == "Viewing an earlier turn")
        #expect(ResultAgentContext.text(run: try summary(status: "running"), viewedAgent: 1, language: .english)
            == "Viewing this Agent's work")
    }

    @Test
    func aDraftSurvivesRefreshWhileThePreviousResultIsUnadopted() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        let workspace = try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"ux-qa","name":"QA","path":"/ux-qa","lastRunId":"old","lastResumeSessionId":"",
         "selected":true,"available":true,"readable":true,"writable":true,"runStatus":"completed",
         "runAgentsTotal":1,"runAgentsFinished":1,"runAgentsFailed":0,"runWorkerActive":false}
        """.utf8))
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"old","status":"completed","workspace":"/ux-qa","prompt":"Old question",
         "createdAt":"2026-09-19","updatedAt":"2026-09-19","config":{},"syncedBack":false,
         "workspacesDeleted":false,"codexHomesDeleted":false,"workerActive":false,
         "workerAvailable":true,"eventCount":0,"agents":[]}
        """.utf8))
        model.workspaces = [workspace]
        model.daemonOnline = true
        model.selectedWorkspaceID = workspace.workspaceId
        model.selectedConversationID = AppModel.newConversationSelectionID
        model.prompt = "New topic draft"
        model.applyWorkspaceConversationIndex([run], resumeSessions: [], workspace: workspace,
                                               machineID: model.currentMachineContextID)
        #expect(model.isComposingNewConversation)
        #expect(model.prompt == "New topic draft")
        #expect(model.newConversationBlockReason == nil)
        #expect(model.promptSubmissionPlan == .start(resuming: false))
        #expect(model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "old" })
    }
}
