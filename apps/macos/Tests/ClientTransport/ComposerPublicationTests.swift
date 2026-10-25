import Combine
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ComposerPublicationTests {
    @Test
    func typingDoesNotPublishTheReaderOrRebuildALongFiveAgentTimeline() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        var run = try #require(model.activeRun)
        let original = model.selectedTimelineItems
        let generation = model.conversationTimelineItemsProjectionGeneration
        #expect(original.count > 500)
        var readerPublications = 0
        var draftPublications = 0
        let reader = model.objectWillChange.sink { readerPublications += 1 }
        let draft = model.promptDraft.objectWillChange.sink { draftPublications += 1 }
        defer { reader.cancel(); draft.cancel() }
        let start = ContinuousClock.now
        for index in 1...1_000 {
            model.updatePrompt("Draft \(index): 输入、编辑与后台计算互不阻塞。")
            run.eventCount = index
            run.agents[0].seconds = Double(index)
            run.agents[0].reasoningTokens = index
            model.activeRun = run
            #expect(model.canSubmitPrompt)
            #expect(model.selectedTimelineItems == original)
        }
        #expect(readerPublications == 0)
        #expect(draftPublications == 1_000)
        #expect(model.conversationTimelineItemsProjectionGeneration == generation)
        #expect(model.activeRunMetrics.agents[0].reasoningTokens == 1_000)
        print("Composer isolation: 1000 draft/metric updates, \(original.count) timeline items, \(start.duration(to: .now)); reader publications=0")
    }

    @Test
    func historyAndPersistenceRemainLocalAndContextScoped() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let firstContext = model.promptDraftContext
        model.promptHistoryEntries = ["First", "Second"]
        model.updatePrompt("Unsaved latest draft")
        var readerPublications = 0
        var draftPublications = 0
        let reader = model.objectWillChange.sink { readerPublications += 1 }
        let draft = model.promptDraft.objectWillChange.sink { draftPublications += 1 }
        defer { reader.cancel(); draft.cancel() }
        model.updatePrompt(model.prompt)
        #expect(draftPublications == 0)
        model.navigatePromptHistory(-1)
        #expect(model.prompt == "Second")
        model.navigatePromptHistory(-1)
        #expect(model.prompt == "First")
        model.navigatePromptHistory(1)
        model.navigatePromptHistory(1)
        #expect(model.prompt == "Unsaved latest draft")
        #expect(readerPublications == 0)
        model.flushPromptDraftPersistence()
        #expect(fixture.store.loadSnapshot().promptDraftsByContext[firstContext] == model.prompt)
        model.selectedConversationID = "other-conversation"
        model.preparePromptHistoryContext()
        #expect(model.prompt.isEmpty)
        model.updatePrompt("Other conversation draft")
        model.selectedConversationID = "conversation"
        model.preparePromptHistoryContext()
        #expect(model.prompt == "Unsaved latest draft")
        #expect(model.promptDraftsByContext.values.contains("Other conversation draft"))
    }

    @Test
    func waitingStateAndOutputChangesStillPublishWhileTyping() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        var run = try #require(model.activeRun)
        var publications = 0
        let subscription = model.objectWillChange.sink { publications += 1 }
        defer { subscription.cancel() }
        model.updatePrompt("Keep this draft")
        run.agents[0].activity = AgentActivityRecord(state: "waiting",
            observedAt: "2026-10-07T08:31:02Z", waitUntil: "2099-10-07T08:40:00Z")
        model.activeRun = run
        #expect(publications == 1)
        #expect(model.activeRun?.agents[0].isObservedWaiting == true)
        run.agents[0].activity = AgentActivityRecord(state: "waiting",
            observedAt: "2026-10-07T08:31:59Z", waitUntil: "2099-10-07T08:40:00Z")
        model.activeRun = run
        #expect(publications == 1)
        run.agents[0].activity = AgentActivityRecord(state: "executing",
            lastOutputAt: "2026-10-07T08:32:01Z", activeOperationCount: 3)
        model.activeRun = run
        #expect(publications == 2)
        #expect(model.prompt == "Keep this draft")
    }

    @MainActor
    private struct Fixture {
        let directory: URL
        let store: DesktopStateStore
        let model: AppModel

        init() throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            store = DesktopStateStore(directory: directory)
            model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
                tokenURL: directory.appendingPathComponent("absent.token")),
                windowID: UUID(), desktopStateStore: store)
            let agent: [String: Any] = ["agent": 1, "name": "Agent 1", "status": "running",
                "reasoningTokenCounts": [:], "rejected": false, "recommended": false,
                "finalized": false, "activity": ["state": "executing",
                    "observedAt": "2026-10-07T08:31:00.654321+00:00",
                    "lastOutputAt": "2026-10-07T08:30:10.123456+00:00", "activeOperationCount": 2]]
            let run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": "run", "conversationId": "conversation", "status": "running", "workspace": "/qa",
                "prompt": "Question", "createdAt": "2026-10-07T08:00:00Z", "updatedAt": "poll",
                "config": [:], "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": true, "workerAvailable": true, "eventCount": 0,
                "agents": (1...5).map { index in var item = agent; item["agent"] = index; item["name"] = "Agent \(index)"; return item }
            ]))
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"workspace","path":"/qa","name":"QA","lastRunId":"run",
             "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
            """.utf8))]
            model.selectedWorkspaceID = "workspace"
            model.selectedConversationID = "conversation"
            model.activeRun = run
            model.workspaceConversations = workspaceConversationSummaries(from: [run])
            model.daemonOnline = true
            model.selectedAgent = 1
            model.transcripts[1] = AgentTranscript(sourceRunID: "run", sourceAgent: 1,
                blocks: (1...600).map { TranscriptBlock(id: "row-\($0)", kind: $0.isMultiple(of: 2) ? .output : .user,
                    text: "Reply \($0): " + String(repeating: "Long conversation, exact history retained. ", count: 10),
                    isHistorical: true) },
                hasMore: false, finalMessageHasMore: false)
            model.preparePromptHistoryContext()
        }

        func close() {
            model.promptDraftSaveTask?.cancel()
            model.stopWindowObservation()
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
