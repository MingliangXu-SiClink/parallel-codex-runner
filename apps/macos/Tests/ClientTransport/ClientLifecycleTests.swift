import AppKit
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ClientLifecycleTests {
    private func model(directory: URL, windowID: UUID, store: DesktopStateStore) throws -> AppModel {
        let model = AppModel(
            client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
                               tokenURL: directory.appendingPathComponent("absent.token")),
            windowID: windowID, desktopStateStore: store,
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(
                directory: directory.appendingPathComponent("cache"))),
            questionAnswers: AgentAnswerOutbox(file: AgentAnswerFile(url: directory.appendingPathComponent("answers.json"))))
        model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"lifecycle","name":"Lifecycle fixture","path":"/lifecycle",
         "lastRunId":"unadopted","lastResumeSessionId":"","selected":true,
         "available":true,"readable":true,"writable":true}
        """.utf8))]
        model.selectedWorkspaceID = "lifecycle"
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"unadopted","conversationId":"conversation","status":"running",
         "workspace":"/lifecycle","prompt":"Compare without adopting","createdAt":"2026-10-17T00:00:00Z",
         "updatedAt":"2026-10-17T00:00:00Z","config":{},"syncedBack":false,"workspacesDeleted":false,
         "codexHomesDeleted":false,"workerActive":true,"workerAvailable":true,"eventCount":0,
         "agents":[
           {"agent":1,"name":"Agent 1","status":"success","canAccept":true,"finalized":false,
            "reasoningTokenCounts":{},"rejected":false,"recommended":false},
           {"agent":2,"name":"Agent 2","status":"success","canAccept":true,"finalized":false,
            "reasoningTokenCounts":{},"rejected":false,"recommended":false},
           {"agent":3,"name":"Agent 3","status":"running","canAccept":false,"finalized":false,
            "reasoningTokenCounts":{},"rejected":false,"recommended":false}]}
        """.utf8))
        model.workspaceConversations = workspaceConversationSummaries(from: [try #require(model.activeRun)])
        model.selectedConversationID = "conversation"
        model.selectedAgent = 1
        model.settings.syncBack = true
        for agent in 1...3 {
            model.transcripts[agent] = AgentTranscript(sourceRunID: "unadopted", sourceAgent: agent,
                cursor: 0, hasMore: false, finalMessage: "Original answer \(agent)", finalMessageHasMore: false,
                finalMessageReadStatus: "success")
        }
        model.preparePromptHistoryContext()
        return model
    }

    @Test(arguments: [false, true])
    func quittingIsImmediateAndNeverResolvesTheVisibleCompletedCandidate(syncBack: Bool) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-exit-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let windowID = UUID()
        let store = DesktopStateStore(windowID: windowID, directory: directory)
        let model = try model(directory: directory, windowID: windowID, store: store)
        defer { model.questionAnswers.stop(); model.stopWindowObservation() }
        model.settings.syncBack = syncBack
        model.updatePrompt("Unsent draft must survive exit")
        let run = model.activeRun
        let delegate = ParallelCodexAppDelegate()
        delegate.model = model
        #expect(model.isViewingLatestWorkspaceRun)
        #expect(model.selectedAgentSummary?.mayAccept == true)
        #expect(delegate.applicationShouldTerminate(NSApplication.shared) == .terminateNow)
        #expect(delegate.isQuitting)
        #expect(model.activeRun == run)
        #expect(model.activeRun?.finalizedAgent == nil)
        #expect(model.selectedAgent == 1)
        #expect(!model.isRunOperationInProgress)
        #expect(model.promptOutboxByWorkspace.isEmpty)
        #expect(model.prompt == "Unsent draft must survive exit")
        #expect(store.loadSnapshot().promptDraftsByContext[model.promptDraftContext] == model.prompt)
    }

    @Test func closingAndReopeningRetainsEveryCandidateAndAllowsAnotherSelection() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-close-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let windowID = UUID()
        let store = DesktopStateStore(windowID: windowID, directory: directory)
        let model = try model(directory: directory, windowID: windowID, store: store)
        defer { model.questionAnswers.stop(); model.stopWindowObservation() }
        model.updatePrompt("A draft is not a request to adopt")
        let original = try #require(model.activeRun)
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID, workspaceID: "lifecycle", runID: original.runId)
        let delegate = ParallelCodexAppDelegate()
        let registration = ClientWindowRegistrationView()
        registration.model = model
        registration.delegate = delegate
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 100, height: 100),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = registration
        window.close()
        #expect(model.activeRun == original)
        #expect(!delegate.isQuitting)
        #expect(delegate.windowModels.count == 0)
        #expect(model.activeRun?.agents.map(\.status) == ["success", "success", "running"])
        #expect(store.loadSnapshot().promptDraftsByContext[model.promptDraftContext] == model.prompt)

        let reopened = try self.model(directory: directory, windowID: windowID,
                                      store: DesktopStateStore(windowID: windowID, directory: directory))
        defer { reopened.stopWindowObservation() }
        let record = try #require(await reopened.runViewStateStore.hydrate(for: key))
        #expect(record.runSummary == original)
        #expect(record.snapshot.selectedAgent == 1)
        #expect(Set(record.snapshot.transcripts.keys) == [1, 2, 3])
        reopened.activeRun = record.runSummary
        reopened.selectedAgent = reopened.restoreCachedRunViewState(machineID: key.machineID,
            workspaceID: key.workspaceID, run: original)
        #expect(reopened.prompt == "A draft is not a request to adopt")
        await reopened.selectAgent(2, manageFollowUpQueue: false)
        #expect(reopened.selectedAgent == 2)
        #expect(reopened.transcripts[try #require(reopened.selectedAgent)]?.finalMessage == "Original answer 2")
        #expect(reopened.activeRun == original)
        #expect(reopened.activeRun?.finalizedAgent == nil)
        #expect(reopened.promptOutboxByWorkspace.isEmpty)
        #expect(reopened.prompt == "A draft is not a request to adopt")
    }
}
