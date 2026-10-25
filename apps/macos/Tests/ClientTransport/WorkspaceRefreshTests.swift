import AppKit
import Darwin
import Foundation
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceRefreshTests {
    @Test func foregroundAnswerAdvancesWhileBackgroundSocketsAreBlocked() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.selectedWorkspaceID = "background"
        model.activeRun = try fixture.server.run()
        model.selectedAgent = 1
        model.transcripts[1] = AgentTranscript(sourceRunID: "run", sourceAgent: 1,
            cursor: 1, hasMore: false, finalMessage: "Previous reply", finalMessageHasMore: false)
        _ = NSApplication.shared
        let host = NSHostingView(rootView: Answer(model: model).frame(width: 360, height: 80))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 80),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        host.layoutSubtreeIfNeeded()
        let before = try render(host)
        fixture.server.holdHistory()
        let relay = RelayMachine(deviceId: "qa-relay", kind: "machine", name: "QA",
            publicKey: "qa-key", status: "online", online: true, lastSeenAt: nil)
        let background = (0..<24).map { index in
            Task(priority: .utility) {
                let reply: ResumeHistoryPage = try await model.client.call(
                    target: index.isMultiple(of: 2) ? .local : .relay(relay),
                    "runs.history", body: EmptyBody())
                return reply.workspace
            }
        }
        try await fixture.server.waitForBlockedRequest()
        // The independent GCD timer also lets the old implementation finish.
        // No timeout or cancellation of a real user operation is involved.
        let server = fixture.server
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { server.release() }
        let start = ContinuousClock.now
        await model.loadAgentAnswer(1, maxPages: 2, retryImmediately: true)
        let delay = start.duration(to: .now)
        #expect(delay < .seconds(1), "Foreground answer waited for unrelated background I/O: \(delay)")
        #expect(!fixture.server.isReleased, "The new answer must be visible before background requests finish")
        #expect(model.transcripts[1]?.finalMessage == "Current reply")
        try await Task.sleep(for: .milliseconds(30))
        host.layoutSubtreeIfNeeded()
        #expect(try render(host) != before, "The offscreen native view still rendered the previous reply")
        #expect(!window.isVisible && !window.isKeyWindow)
        #expect(fixture.server.blockedCount <= 4, "Background observation must not create an unbounded socket burst")
        print("Workspace contention check: foreground answer and native frame advanced in \(delay) with 24 pending background reads")
        fixture.server.release()
        for task in background { #expect(try await task.value == "/qa") }
    }

    @Test func utilityCommandsDoNotWaitBehindBackgroundReads() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.server.holdHistory()
        let client = fixture.model.client
        let background = (0..<4).map { _ in
            Task(priority: .utility) {
                let _: ResumeHistoryPage = try await client.call("runs.history", body: EmptyBody())
            }
        }
        try await fixture.server.waitForBlockedRequest(count: 2)
        let server = fixture.server
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { server.release() }
        let relay = RelayMachine(deviceId: "qa-relay", kind: "machine", name: "QA",
            publicKey: "qa-key", status: "online", online: true, lastSeenAt: nil)
        // These commands run only against this fixture's private fake socket.
        for target in [PcrdRequestTarget.local, .relay(relay)] {
            let task = Task(priority: .utility) {
                let command: Reply = try await client.call(target: target, "turns.start", body: EmptyBody())
                let unknown: Reply = try await client.call(target: target, "qa.command", body: EmptyBody())
                return command.value == "released" && unknown.value == "released"
            }
            #expect(try await task.value)
            #expect(!server.isReleased, "Utility commands must not wait for observation slots")
        }
        #expect(server.count("turns.start") == 2 && server.count("qa.command") == 2)
        server.release()
        for task in background { try await task.value }
    }

    @Test func unchangedConversationDoesNotRereadAndEveryBranchRefreshesAtCompletion() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        func preload() async throws {
            model.scheduleConversationPreload(machineID: model.currentMachineContextID,
                target: .local, workspaces: model.workspaces)
            let expected = try fixture.server.run()
            let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
                workspaceID: "background", runID: "run")
            try await waitUntil {
                guard let run = model.runSummariesByContext[key], run.status == expected.status,
                      let snapshot = model.runViewStateStore.snapshot(for: key),
                      snapshot.historyLoaded == true else { return false }
                return run.agents.allSatisfy {
                    !selectedAgentDetailNeedsRefresh(run: run, selectedAgent: $0.agent,
                        transcripts: snapshot.transcripts)
                }
            }
        }
        try await preload()
        #expect(fixture.server.count("runs.history") == 1)
        #expect(fixture.server.count("runs.agent") == 10)
        let initialRequests = fixture.server.requestCount
        // Force all legacy retry deadlines due, without waiting 15 seconds.
        for _ in 0..<8 {
            model.conversationPreloadRetryAfter = model.conversationPreloadRetryAfter.mapValues { _ in .distantPast }
            try await preload()
        }
        #expect(fixture.server.requestCount == initialRequests,
            "Metadata polling must not repeatedly trigger full-history preparation")
        fixture.server.setTerminal()
        model.workspaces = [try fixture.workspace(terminal: true)]
        try await preload()
        #expect(fixture.server.count("runs.history") == 1)
        #expect(fixture.server.count("runs.agent") == 15)
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "background", runID: "run")
        #expect(model.runViewStateStore.snapshot(for: key)?.transcripts[1]?.finalMessage == "Completed reply")
        let terminalRequests = fixture.server.requestCount
        try await preload()
        #expect(fixture.server.requestCount == terminalRequests)
        #expect(model.selectedWorkspaceID == "visible")
    }

    @Test func latePreloadCannotOverwriteAForegroundConversation() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        fixture.server.holdHistory()
        model.scheduleConversationPreload(machineID: model.currentMachineContextID,
            target: .local, workspaces: model.workspaces)
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "background", runID: "run")
        let pending = model.conversationReadTokens[key]
        try await fixture.server.waitForBlockedRequest()
        try await waitUntil {
            model.runViewStateStore.snapshot(for: key)?.transcripts[2]?.finalMessage == "Current reply"
        }
        model.selectedWorkspaceID = "background"
        model.activeRun = try fixture.server.run()
        model.selectedAgent = 2
        model.transcripts[2] = AgentTranscript(sourceRunID: "run", sourceAgent: 2,
            cursor: 100, hasMore: false, finalMessage: "Foreground is newer", finalMessageHasMore: false)
        model.cacheVisibleRunViewState()
        fixture.server.release()
        try await waitUntil {
            guard let snapshot = model.runViewStateStore.snapshot(for: key),
                  snapshot.historyLoaded == true else { return false }
            return (1...5).allSatisfy { snapshot.transcripts[$0]?.hasMore == false }
        }
        #expect(model.conversationReadTokens[key] == pending)
        for task in Array(model.conversationHistoryTasks.values) { await task.value }
        #expect(model.runViewStateStore.snapshot(for: key)?.transcripts[2]?.finalMessage == "Foreground is newer")
        #expect(model.runViewStateStore.snapshot(for: key)?.selectedAgent == 2)
        #expect(model.transcripts[2]?.cursor == 100)
        try #require(model.runViewStateStore.flushPendingWrites())
        let restored = WorkspaceRunViewStateStore(
            diskCache: WorkspaceRunDiskCache.standard(windowID: fixture.windowID))
        let restoredSnapshot = await restored.hydrate(for: key)?.snapshot
        #expect(restoredSnapshot?.transcripts[2]?.finalMessage == "Foreground is newer")
        #expect(restoredSnapshot?.selectedAgent == 2)
    }

    @Test func metadataTicksKeepOnePreloadAndPreserveTerminalWarming() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "background", runID: "run")
        fixture.server.holdHistory()
        model.scheduleConversationPreload(machineID: model.currentMachineContextID,
            target: .local, workspaces: model.workspaces)
        let pending = model.conversationPreloadTask
        try await fixture.server.waitForBlockedRequest()
        for count in 2...9 {
            fixture.server.advanceEvents(to: count)
            var run = try fixture.server.run()
            run.eventCount = count
            model.runSummariesByContext[key] = run
            model.scheduleConversationPreload(machineID: model.currentMachineContextID,
                target: .local, workspaces: model.workspaces)
        }
        #expect(fixture.server.count("turns.get") == 1 && fixture.server.count("runs.history") == 1)
        fixture.server.setTerminal()
        model.workspaces = [try fixture.workspace(terminal: true)]
        model.scheduleConversationPreload(machineID: model.currentMachineContextID,
            target: .local, workspaces: model.workspaces)
        fixture.server.release()
        await pending?.value
        for task in Array(model.conversationHistoryTasks.values) { await task.value }
        #expect(model.runSummariesByContext[key]?.eventCount == 9)
        #expect(model.runViewStateStore.snapshot(for: key) != nil,
            "Unrelated metadata updates must not reject a successful preload")
        let deadline = ContinuousClock.now + .seconds(3)
        while !model.preloadedConversationKeys.contains(key), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(fixture.server.count("runs.history") == 1)
        #expect(model.preloadedConversationKeys.contains(key),
            "A terminal tick received during a read must schedule its own follow-up")
        #expect(model.runViewStateStore.snapshot(for: key)?.transcripts[1]?.finalMessage == "Completed reply")
    }

    @Test func cancelledObserverStillCollectsOneOriginalReply() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.server.holdHistory()
        let task = Task(priority: .utility) {
            let reply: ResumeHistoryPage = try await fixture.model.client.call("runs.history", body: EmptyBody())
            return reply.workspace
        }
        try await fixture.server.waitForBlockedRequest()
        task.cancel()
        fixture.server.release()
        #expect(try await task.value == "/qa")
        #expect(fixture.server.count("runs.history") == 1)
    }

    private struct Reply: Decodable, Sendable { let value: String }

    private func waitUntil(_ condition: @MainActor () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(12)
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        try #require(condition())
    }
    private struct Answer: View {
        @ObservedObject var model: AppModel
        var body: some View { Text(model.transcripts[1]?.finalMessage ?? "") }
    }

    private func render(_ host: NSView) throws -> Data {
        let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        return try #require(bitmap.representation(using: .png, properties: [:]))
    }

    private final class Server: @unchecked Sendable {
        private let lock = NSLock()
        private let gate = DispatchGroup()
        private var requests: [String] = []
        private var blocked = 0
        private var released = false
        private var terminal = false
        private var historyHeld = false
        private var eventTotal = 1

        init() { gate.enter() }
        var isReleased: Bool { lock.withLock { released } }
        var blockedCount: Int { lock.withLock { blocked } }
        var requestCount: Int { lock.withLock { requests.count } }
        func count(_ action: String) -> Int { lock.withLock { requests.count { $0 == action } } }
        func setTerminal() { lock.withLock { terminal = true } }
        func advanceEvents(to count: Int) { lock.withLock { eventTotal = count } }
        func holdHistory() { lock.withLock { historyHeld = true } }
        func release() {
            let first = lock.withLock { let first = !released; released = true; return first }
            if first { gate.leave() }
        }
        func waitForBlockedRequest(count: Int = 1) async throws {
            let started = await withCheckedContinuation { continuation in
                DispatchQueue.global().async {
                    let deadline = Date().addingTimeInterval(5)
                    while self.lock.withLock({ self.blocked < count }), Date() < deadline { usleep(1_000) }
                    continuation.resume(returning: self.lock.withLock { self.blocked >= count })
                }
            }
            try #require(started)
        }
        func run() throws -> RunSummary {
            try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": "run", "status": lock.withLock { terminal } ? "completed" : "running",
                "workspace": "/qa", "prompt": "Question", "createdAt": "2026-10-10T00:00:00Z",
                "updatedAt": "2026-10-10T00:00:00Z", "config": [:], "syncedBack": false,
                "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": !lock.withLock { terminal }, "workerAvailable": false, "eventCount": lock.withLock { eventTotal },
                "agents": (1...5).map { ["agent": $0, "name": "Agent \($0)",
                    "status": lock.withLock { terminal } ? "success" : "running",
                    "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false] },
            ]))
        }
        func respond(_ socket: Int32) {
            defer { Darwin.close(socket) }
            var noSignal: Int32 = 1
            _ = withUnsafePointer(to: &noSignal) {
                Darwin.setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size))
            }
            var timeout = timeval(tv_sec: 5, tv_usec: 0)
            _ = withUnsafePointer(to: &timeout) {
                Darwin.setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
            }
            var buffer = [UInt8](repeating: 0, count: 8192)
            let size = Darwin.read(socket, &buffer, buffer.count)
            guard size > 0, let request = try? JSONSerialization.jsonObject(with: Data(buffer.prefix(size))) as? [String: Any],
                  let wireAction = request["action"] as? String else { return }
            let requestBody = request["body"] as? [String: Any] ?? [:]
            let action = wireAction == "controller.call" ? requestBody["target_action"] as? String ?? wireAction : wireAction
            let actionBody = wireAction == "controller.call" ? requestBody["target_body"] as? [String: Any] ?? [:] : requestBody
            lock.withLock { requests.append(action) }
            if action == "runs.history" && lock.withLock({ historyHeld }) {
                lock.withLock { blocked += 1 }
                guard gate.wait(timeout: .now() + 6) == .success else { return }
            }
            let body: Any
            let agent = actionBody["agent"] as? Int ?? 1
            if action == "turns.get" {
                let encoder = JSONEncoder()
                encoder.keyEncodingStrategy = .convertToSnakeCase
                guard let run = try? run(), let data = try? encoder.encode(run),
                      let value = try? JSONSerialization.jsonObject(with: data) else { return }
                body = value
            } else if action == "runs.history" {
                body = ["workspace": "/qa", "entries": [], "cursor": 0,
                    "next_cursor": 0, "has_more": false, "total_entries": 0]
            } else if action == "runs.agent" {
                let answer = lock.withLock { terminal } ? "Completed reply" : "Current reply"
                let cursor = actionBody["cursor"] as? Int ?? 0
                let next = max(cursor, lock.withLock { eventTotal })
                body = ["run_id": "run", "agent": agent, "name": "Agent \(agent)",
                    "status": lock.withLock { terminal } ? "success" : "running", "reasoning_token_counts": [:],
                    "rejected": false, "recommended": false, "finalized": false, "final_message": answer,
                    "stdout_tail": "", "stderr_tail": "",
                    "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": next,
                        "has_more": false, "total": next, "items": []]] as [String: Any]
            } else { body = ["value": "released"] }
            guard let reply = try? JSONSerialization.data(withJSONObject: ["ok": true, "body": body]) + Data([10]) else { return }
            _ = reply.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
        }
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let listener: Int32
        let source: DispatchSourceRead
        let server = Server()
        let model: AppModel
        let windowID = UUID()

        init() throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-load-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("test-token".utf8).write(to: tokenURL)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            let descriptor = listener
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { destination in
                Array(socketURL.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
            }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(bound == 0 && Darwin.listen(descriptor, 64) == 0)
            _ = fcntl(descriptor, F_SETFL, O_NONBLOCK)
            source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .global())
            let server = server
            source.setEventHandler { @Sendable in
                while true {
                    let socket = Darwin.accept(descriptor, nil, nil)
                    guard socket >= 0 else { break }
                    _ = fcntl(socket, F_SETFL, 0)
                    DispatchQueue.global().async { server.respond(socket) }
                }
            }
            source.setCancelHandler { @Sendable in Darwin.close(descriptor) }
            source.resume()
            let id = windowID
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: id,
                desktopStateStore: DesktopStateStore(windowID: id, directory: directory))
            model.selectedWorkspaceID = "visible"
            model.workspaces = [try Self.workspace(terminal: false)]
        }
        func workspace(terminal: Bool) throws -> RemoteWorkspace { try Self.workspace(terminal: terminal) }
        private static func workspace(terminal: Bool) throws -> RemoteWorkspace {
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "background", "path": "/qa", "name": "Background", "lastRunId": "run",
                "lastResumeSessionId": "", "runStatus": terminal ? "completed" : "running",
                "runWorkerActive": !terminal, "selected": false, "available": true, "readable": true, "writable": true,
            ]))
        }
        func close() {
            model.stopWindowObservation()
            model.forgetCachedRunViews(machineID: model.currentMachineContextID, workspaceID: "background")
            server.release()
            source.cancel()
            _ = model.desktopStateStore.flushPendingWrites()
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
