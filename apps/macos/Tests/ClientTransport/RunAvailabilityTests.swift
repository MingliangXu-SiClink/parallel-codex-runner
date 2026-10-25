import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct RunAvailabilityTests {
    @Test(arguments: [false, true])
    func staleWorkspaceLatestCannotMoveTheSecondTurnBackToTheFirst(followRemoteLatest: Bool) async throws {
        let first = try navigationTurn("old-run", index: 0)
        let second = try navigationTurn("second-run", index: 1)
        let fixture = try Fixture(runResponse: first)
        defer { fixture.close() }
        let model = fixture.model
        model.workspaceConversations = workspaceConversationSummaries(from: [first, second])
        model.activeRun = second
        model.selectedAgent = nil
        model.prompt = "Keep the second turn draft"
        for _ in 0..<3 {
            await model.refreshLatestWorkspaceConversation(followRemoteLatest: followRemoteLatest)
            #expect(model.activeRun?.runId == second.runId)
            #expect(model.isViewingLatestConversationTurn)
            #expect(model.selectedConversationID == "conversation")
            #expect(model.prompt == "Keep the second turn draft")
        }
        #expect(await fixture.server.value == ["turns.get", "turns.get", "turns.get"])
    }

    @Test(arguments: [1, 2])
    func lateRefreshCannotOverrideExplicitTurnNavigation(pausedRequest: Int) async throws {
        let first = try navigationTurn("first-run", index: 0)
        let second = try navigationTurn("old-run", index: 1)
        let gate = RefreshGate()
        let fixture = try Fixture(runResponse: second, beforeReply: { requestNumber in
            if requestNumber == pausedRequest { await gate.arriveAndWait() }
        })
        defer { fixture.close() }
        let model = fixture.model
        // Simulate following the first turn when the server advertises a new one.
        model.workspaceConversations = workspaceConversationSummaries(from: [first])
        model.activeRun = first
        model.selectedAgent = nil
        let refresh = Task { await model.refreshLatestWorkspaceConversation() }
        await gate.waitForRequest()
        model.conversationNavigationRequestID += 1
        model.activeRun = first
        await gate.release()
        await refresh.value
        #expect(model.activeRun?.runId == first.runId)
        #expect(model.selectedConversationID == "conversation")
        _ = await fixture.server.value
    }

    @Test(arguments: [false, true])
    func refreshFollowsNewTurnsButPreservesAnExplicitHistoricalSelection(readingHistory: Bool) async throws {
        let first = try navigationTurn("first-run", index: 0)
        let second = try navigationTurn("old-run", index: 1)
        let fixture = try Fixture(runResponse: second)
        defer { fixture.close() }
        let model = fixture.model
        model.workspaceConversations = workspaceConversationSummaries(
            from: readingHistory ? [first, second] : [first])
        model.activeRun = first
        if readingHistory {
            model.historicalConversationTurn = .init(machineID: model.currentMachineContextID,
                workspaceID: try #require(model.selectedWorkspaceID), runID: first.runId)
        }
        model.selectedAgent = nil
        await model.refreshLatestWorkspaceConversation()
        #expect(model.activeRun?.runId == (readingHistory ? first.runId : second.runId))
        _ = await fixture.server.value
    }

    private func navigationTurn(_ id: String, index: Int) throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"\(id)","conversationId":"conversation","turnIndex":\(index),
         "status":"finalized","workspace":"/qa","prompt":"Question \(index)",
         "createdAt":"2026-09-18T00:0\(index):00Z","updatedAt":"2026-09-18T00:0\(index):00Z","config":{},
         "syncedBack":true,"workspacesDeleted":true,"codexHomesDeleted":true,
         "workerActive":false,"workerAvailable":false,"eventCount":1,"agents":[]}
        """.utf8))
    }

    private actor RefreshGate {
        private var arrived = false
        private var observer: CheckedContinuation<Void, Never>?
        private var response: CheckedContinuation<Void, Never>?

        func arriveAndWait() async {
            arrived = true
            observer?.resume()
            observer = nil
            await withCheckedContinuation { response = $0 }
        }

        func waitForRequest() async {
            if !arrived { await withCheckedContinuation { observer = $0 } }
        }

        func release() { response?.resume(); response = nil }
    }

    @Test
    func recentConversationsFilterBothFetchedAndPreviouslyCachedWorkspacesBeforeMerging() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let workspace = try #require(model.selectedWorkspace)
        let localRun = try #require(model.activeRun)
        let localSession = try #require(model.selectedConversation?.resumeSession)
        let foreignRun = try runInOtherWorkspace()
        let foreignSession = ResumeSession(sessionId: "foreign-session", title: "Another workspace",
            cwd: "/qa/child", updatedAt: 9, createdAt: 1, source: "cli", model: "",
            rolloutPath: "", preview: "Foreign", tokensUsed: nil)
        model.workspaceConversations = workspaceConversationSummaries(
            from: [localRun, foreignRun], resumeSessions: [localSession, foreignSession]
        )
        model.applyWorkspaceConversationIndex([foreignRun, localRun],
            resumeSessions: [foreignSession, localSession], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.workspaceConversations.flatMap(\.runs) == [localRun])
        #expect(model.workspaceConversations.compactMap(\.resumeSession) == [localSession])
        #expect(model.selectedConversationID == "conversation")

        let before = model.workspaceConversations
        model.recordRunInConversationIndex(foreignRun)
        #expect(model.workspaceConversations == before)
        #expect(model.selectedConversationID == "conversation")
    }

    @Test
    func lateIndexResponseCannotChangeTheSelectedMachineOrWorkspace() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let workspace = try #require(model.selectedWorkspace)
        let before = model.workspaceConversations
        model.applyWorkspaceConversationIndex([try runInOtherWorkspace()], resumeSessions: [],
            workspace: workspace, machineID: "other-machine")
        #expect(model.workspaceConversations == before)
        model.selectedWorkspaceID = "another-workspace"
        model.applyWorkspaceConversationIndex([], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID, completeAsOf: Date())
        #expect(model.workspaceConversations == before)
        model.selectedWorkspaceID = workspace.workspaceId
    }

    @Test
    func foreignCacheCannotBecomeTheFirstPaintOrBeSavedUnderTheSelectedWorkspace() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let workspace = try #require(model.selectedWorkspace)
        let machine = model.currentMachineContextID
        let original = model.activeRun
        let foreign = try runInOtherWorkspace()
        let key = WorkspaceRunViewKey(machineID: machine, workspaceID: workspace.workspaceId,
            runID: foreign.runId)
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: nil, transcripts: [:], conversationHistory: [])
        let records = [
            WorkspaceRunViewCacheRecord(key: key, runSummary: foreign, snapshot: snapshot),
            WorkspaceRunViewCacheRecord(key: WorkspaceRunViewKey(machineID: machine,
                workspaceID: "foreign-workspace", runID: "resume-session:foreign"),
                runSummary: nil, snapshot: snapshot),
            WorkspaceRunViewCacheRecord(key: WorkspaceRunViewKey(machineID: "other-machine",
                workspaceID: workspace.workspaceId, runID: "resume-session:foreign"),
                runSummary: nil, snapshot: snapshot),
        ]
        #expect(!model.installCachedWorkspaceConversation(records, workspace: workspace, machineID: machine))
        #expect(model.activeRun == original)
        model.activeRun = foreign
        model.cacheVisibleRunViewState()
        #expect(model.runSummariesByContext[key] == nil)
        #expect(model.runViewStateStore.snapshot(for: key) == nil)
        model.activeRun = original
    }

    @Test
    func workspaceMatchingNeverIncludesAParentSiblingOrEmptyPath() {
        #expect(sameConversationWorkspace("/qa/", "/qa"))
        #expect(sameConversationWorkspace("/", "/"))
        #expect(!sameConversationWorkspace("/qa/child", "/qa"))
        #expect(!sameConversationWorkspace("/qa-other", "/qa"))
        #expect(!sameConversationWorkspace("/QA", "/qa"))
        #expect(!sameConversationWorkspace("", ""))
    }

    private func runInOtherWorkspace() throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"foreign-run","conversationId":"conversation","status":"completed","workspace":"/qa/child","prompt":"Foreign question",
         "createdAt":"2026-09-18T00:00:00Z","updatedAt":"2026-09-18T00:00:00Z","config":{"resumeSessionId":"session"},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":false,"workerAvailable":true,"eventCount":1,"agents":[]}
        """.utf8))
    }

    @Test
    func completedResultRequiresTextToSendButKeepsExplicitAcceptanceAvailable() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"old-run","conversationId":"conversation","status":"completed","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-09-15T00:00:00Z","updatedAt":"2026-09-15T00:00:00Z","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":false,"workerAvailable":true,"eventCount":1,
         "agents":[{"agent":1,"name":"Agent 1","status":"success","reasoningTokenCounts":{},
                    "rejected":false,"recommended":true,"finalized":false}]}
        """.utf8))
        model.activeRun = run
        model.workspaceConversations = workspaceConversationSummaries(from: [run])
        model.daemonOnline = true
        model.settings.syncBack = true
        model.prompt = "  \n"
        #expect(!model.canSubmitPrompt)
        #expect(conversationBranchPrimaryAction(
            run: run, agent: run.agents[0], isLatestWorkspaceRun: true,
            syncBack: true, operation: nil, hasPendingSubmission: false
        ) == .useResult(stopsOthers: false))

        model.prompt = "Follow up on this reply"
        #expect(model.canSubmitPrompt)
        #expect(!model.promptSubmissionStopsRemainingAgents)
        model.prompt = ""
        #expect(!model.canSubmitPrompt)
        #expect(model.activeRun?.finalizedAgent == nil)
    }

    @Test
    func backgroundQueueKeepsExplicitSelectionBeforeTheCandidateFinishes() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"old-run","status":"running","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-09-15T00:00:00Z","updatedAt":"2026-09-15T00:00:00Z","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":true,"workerAvailable":true,"eventCount":1,
         "agents":[{"agent":1,"name":"Agent 1","status":"running","reasoningTokenCounts":{},
                    "rejected":false,"recommended":false,"finalized":false}]}
        """.utf8))
        model.activeRun = run
        let key = try #require(model.selectedAgentContextKey())
        model.selectedAgentsByContext.removeValue(forKey: key)
        model.selectedAgent = 1
        #expect(model.remoteFollowUpSelection(runID: run.runId) == nil)

        model.rememberSelectedAgent(1)
        #expect(model.remoteFollowUpSelection(runID: run.runId) == nil)
        #expect(model.remoteFollowUpSelection(runID: "another-run") == nil)
        model.selectedAgentsByContext[key] = 999
        #expect(model.remoteFollowUpSelection(runID: run.runId) == nil)
    }

    @Test
    func finishingRunDiscardsTheBrowsingOnlyRunningPhaseSelection() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let run = try #require(model.activeRun)
        let key = try #require(model.selectedAgentContextKey())
        model.selectedAgentsByContext[key] = 1
        model.selectedAgentContextsWhileWorking.insert(key)

        #expect(model.rememberedSelectedAgent(for: run, contextKey: key) == nil)
        #expect(model.selectedAgentsByContext[key] == nil)
        #expect(!model.selectedAgentContextsWhileWorking.contains(key))
        #expect(model.rememberedSelectedAgent(for: run, contextKey: key) == nil)
    }

    @Test
    func recoveredConversationUsesAdoptedSessionRatherThanItsParent() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let run = try #require(fixture.model.activeRun)
        let adopted = try #require(fixture.model.selectedConversation?.resumeSession)
        let parent = ResumeSession(sessionId: "parent-session", title: "Parent", cwd: "/qa", updatedAt: 9,
            createdAt: 1, source: "cli", model: "", rolloutPath: "", preview: "Parent", tokensUsed: nil)
        let summaries = workspaceConversationSummaries(from: [run], resumeSessions: [parent, adopted],
            unavailableRunIDs: [run.runId])
        let conversation = try #require(summaries.first { $0.id == "conversation" })
        #expect(conversation.resumeSession?.sessionId == "session")
        #expect(conversation.opensResumeHistory)
    }

    @Test
    func onlyCompleteSnapshotsCanRetireCachedExecutions() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let run = try #require(model.activeRun)
        let machine = model.currentMachineContextID
        let workspace = try #require(model.selectedWorkspace)
        model.applyWorkspaceConversationIndex([], resumeSessions: [], workspace: workspace, machineID: machine)
        #expect(model.selectedConversation?.opensResumeHistory == false)
        model.applyWorkspaceConversationIndex([], resumeSessions: [], workspace: workspace,
            machineID: machine, completeAsOf: Date(timeIntervalSince1970: 1))
        #expect(model.selectedConversation?.opensResumeHistory == false)
        model.applyWorkspaceConversationIndex([], resumeSessions: [], workspace: workspace,
            machineID: machine, completeAsOf: Date())
        #expect(model.selectedConversation?.opensResumeHistory == true)
        #expect(model.selectedConversation?.runs == [run])
        #expect(model.unavailableRunIDs(workspaceID: workspace.workspaceId, machineID: "other-machine").isEmpty)
        #expect(model.unavailableRunIDs(workspaceID: "other-workspace", machineID: machine).isEmpty)
        // A later authoritative response makes a restored execution usable again.
        model.applyWorkspaceConversationIndex([run], resumeSessions: [], workspace: workspace, machineID: machine)
        #expect(model.selectedConversation?.opensResumeHistory == false)
    }

    @Test(arguments: [false, true])
    func missingRunUsesRealSessionWithoutAnotherAgentPoll(detailRequest: Bool) async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.prompt = "Keep the draft"
        if detailRequest {
            _ = await model.loadAgentDetail(1, maxPages: 1)
        } else {
            try await model.loadRun("old-run", loadDetailPages: 1)
        }
        #expect(model.activeRun == nil)
        #expect(model.selectedAgent == nil)
        #expect(model.selectedConversationID == "conversation")
        #expect(model.selectedConversation?.opensResumeHistory == true)
        #expect(model.settings.resumeSessionID == "session")
        #expect(model.prompt == "Keep the draft")
        #expect(model.resumeHistory.map(\.text) == ["Real question", "Real answer"])
        #expect(model.errorNotice == nil)
        model.beginPolling("old-run")
        await model.refreshLatestWorkspaceConversation()
        let requests = await fixture.server.value
        #expect(requests == [detailRequest ? "runs.agent" : "turns.get", "codex.resume_history"])
        #expect(model.runPollingTasks.isEmpty)
    }

    @Test(arguments: ["Permission denied", "Unknown PCR run: another-run"])
    func unrelatedErrorsNeverChangeTheConversationSource(message: String) async throws {
        let fixture = try Fixture(message: message)
        defer { fixture.close() }
        do {
            try await fixture.model.loadRun("old-run", loadDetailPages: 1)
            Issue.record("The lookup should fail")
        } catch { }
        #expect(fixture.model.activeRun?.runId == "old-run")
        #expect(fixture.model.selectedConversation?.opensResumeHistory == false)
        #expect(await fixture.server.value == ["turns.get"])
    }

    @Test
    func failedSessionRecoveryKeepsCachedHistoryAndReportsTheRealReadFailure() async throws {
        let fixture = try Fixture(historyAvailable: false)
        defer { fixture.close() }
        fixture.model.resumeHistory = [ResumeHistoryEntry(category: "user", text: "Cached question")]
        try await fixture.model.loadRun("old-run", loadDetailPages: 1)
        #expect(fixture.model.resumeHistory.first?.text == "Cached question")
        #expect(fixture.model.errorNotice == .selectedConversationHistory("History unavailable"))
        #expect(await fixture.server.value == ["turns.get", "codex.resume_history"])
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let listener: Int32
        let model: AppModel
        let server: Task<[String], Never>
        let windowID = UUID()

        init(message: String = "Unknown PCR run: old-run", historyAvailable: Bool = true,
             runResponse: RunSummary? = nil,
             beforeReply: (@Sendable (Int) async -> Void)? = nil) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("test-token".utf8).write(to: tokenURL)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let bytes = Array(socketURL.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { target in
                bytes.withUnsafeBytes { target.copyBytes(from: $0) }
            }
            let descriptor = listener
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            #expect(bound == 0)
            #expect(Darwin.listen(listener, 4) == 0)
            let missing = try JSONSerialization.data(withJSONObject: [
                "ok": false, "error": ["code": "pcr_error", "message": message],
            ]) + Data([10])
            let runReply: Data
            if let runResponse {
                let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(runResponse))
                runReply = try JSONSerialization.data(withJSONObject: ["ok": true, "body": body]) + Data([10])
            } else {
                runReply = missing
            }
            let history = try JSONSerialization.data(withJSONObject: ["ok": true, "body": [
                "workspace": "/qa", "cursor": 0, "next_cursor": 2, "has_more": false, "total_entries": 2,
                "entries": [
                    ["id": "q", "turn_id": "t", "category": "user", "text": "Real question"],
                    ["id": "a", "turn_id": "t", "category": "output", "text": "Real answer"],
                ],
            ]]) + Data([10])
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            _ = try decoder.decode(ResponseEnvelope<ResumeHistoryPage>.self, from: history)
            let failedHistory = Data("{\"ok\":false,\"error\":{\"code\":\"pcr_error\",\"message\":\"History unavailable\"}}\n".utf8)
            server = Task.detached {
                var actions: [String] = []
                while !Task.isCancelled {
                    var pending = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                    guard Darwin.poll(&pending, 1, actions.isEmpty ? 2_000 : 400) > 0 else { break }
                    guard !Task.isCancelled else { break }
                    let client = Darwin.accept(descriptor, nil, nil)
                    guard client >= 0 else { break }
                    defer { Darwin.close(client) }
                    var timeout = timeval(tv_sec: 2, tv_usec: 0)
                    _ = withUnsafePointer(to: &timeout) {
                        Darwin.setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
                    }
                    var buffer = [UInt8](repeating: 0, count: 8192)
                    let count = Darwin.read(client, &buffer, buffer.count)
                    guard count > 0,
                          let request = try? JSONSerialization.jsonObject(with: Data(buffer.prefix(count))) as? [String: Any],
                          let action = request["action"] as? String else { break }
                    actions.append(action)
                    await beforeReply?(actions.count)
                    let reply = action == "codex.resume_history" ? (historyAvailable ? history : failedHistory) : runReply
                    _ = reply.withUnsafeBytes { Darwin.write(client, $0.baseAddress, $0.count) }
                }
                return actions
            }
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: windowID)
            let workspaceID = "history-\(UUID().uuidString)"
            let workspace = try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": workspaceID, "path": "/qa", "name": "History QA", "lastRunId": "old-run",
                "lastResumeSessionId": "session", "selected": false, "available": true, "readable": true, "writable": true,
            ]))
            let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
            {"runId":"old-run","conversationId":"conversation","status":"finalized","workspace":"/qa","prompt":"Question",
             "createdAt":"2026-08-01T00:00:00Z","updatedAt":"2026-08-01T00:00:00Z","config":{"resumeSessionId":"parent-session"},
             "sessionPromotion":{"sessionId":"session"},
             "syncedBack":true,"workspacesDeleted":true,"codexHomesDeleted":true,
             "workerActive":false,"workerAvailable":false,"eventCount":1,"agents":[]}
            """.utf8))
            let session = ResumeSession(sessionId: "session", title: "Question", cwd: "/qa", updatedAt: 1,
                createdAt: 1, source: "cli", model: "", rolloutPath: "", preview: "Question", tokensUsed: nil)
            model.workspaces = [workspace]
            model.selectedWorkspaceID = workspaceID
            model.workspaceConversations = workspaceConversationSummaries(from: [run], resumeSessions: [session])
            model.selectedConversationID = "conversation"
            model.activeRun = run
            model.selectedAgent = 1
        }

        func close() {
            server.cancel()
            model.stopWindowObservation()
            model.forgetCachedRunViews(machineID: model.currentMachineContextID, workspaceID: model.selectedWorkspaceID ?? "")
            if model.desktopStateStore.flushPendingWrites(),
               let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent("Parallel Codex/window-\(windowID.uuidString).v3.json"))
            }
            Darwin.close(listener)
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
