import Darwin
import Foundation
import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct AutomaticPaginationTests {
    @Test func expandedCommandReadsAllPagesWithoutAnotherAction() async throws {
        let fixture = try Fixture()
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { !fixture.model.workProcess.loading.contains(fixture.key) }
        let output = await fixture.replies.output(event: 1)
        #expect(fixture.model.workProcess.details[fixture.key]?.text == output)
        #expect(fixture.model.workProcess.details[fixture.key]?.hasMore == false)
        #expect(fixture.model.workProcess.loadedEvents[fixture.key] == 1)
        #expect(fixture.model.workProcess.errors[fixture.key] == nil)
        #expect(!fixture.model.projectWorkOperations([fixture.block]).contains { $0.activityType == "operation_more" })
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 30_000, 36_000])
        await fixture.close()
    }

    @Test func failedCommandContinuationKeepsTextAndRetriesItsOwnCursor() async throws {
        let fixture = try Fixture(failCursor: 30_000)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { fixture.model.workProcess.errors[fixture.key] != nil }
        #expect(fixture.model.workProcess.details[fixture.key]?.text == String(repeating: "a", count: 30_000))
        #expect(fixture.model.workProcess.loadedEvents[fixture.key] == nil)
        fixture.model.refreshExpandedWorkOperations()
        #expect(await fixture.replies.requests.count == 2)
        fixture.model.activateWorkBlock(TranscriptBlock(id: fixture.block.id + ":retry", kind: .activity,
            text: "Retry", activityType: "operation_retry"))
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 1 }
        let output = await fixture.replies.output(event: 1)
        #expect(fixture.model.workProcess.details[fixture.key]?.text == output)
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 30_000, 30_000, 36_000])
        await fixture.close()
    }

    @Test func commandCannotLoopOnAnUnchangedCursor() async throws {
        let fixture = try Fixture(stalled: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { fixture.model.workProcess.errors[fixture.key] != nil }
        fixture.model.refreshExpandedWorkOperations()
        #expect(await fixture.replies.requests.count == 1)
        #expect(fixture.model.workProcess.loadedEvents[fixture.key] == nil)
        await fixture.close()
    }

    @Test func collapsingAndReopeningCannotCommitTheCancelledRequest() async throws {
        let fixture = try Fixture(pauseCursor: 0)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 1 }
        fixture.model.activateWorkBlock(fixture.block)
        #expect(!fixture.model.workProcess.loading.contains(fixture.key))
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 2 }
        await fixture.replies.release()
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 1 }
        let output = await fixture.replies.output(event: 1)
        #expect(fixture.model.workProcess.details[fixture.key]?.text == output)
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 0, 30_000, 36_000])
        await fixture.close()
    }

    @Test func aNewOperationEventSupersedesItsDelayedOldOutput() async throws {
        let fixture = try Fixture(pauseCursor: 0, pauseEvent: 1)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 1 }
        let newer = TranscriptBlock(id: fixture.block.id, kind: .command, text: "command",
            operation: WorkOperationRecord(id: "operation", eventId: 2, kind: .command,
                status: .completed, detailsAvailable: true))
        fixture.model.transcripts[1] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 1, blocks: [newer])
        fixture.model.refreshExpandedWorkOperations()
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 2 }
        await fixture.replies.release()
        try await Task.sleep(for: .milliseconds(30))
        let output = await fixture.replies.output(event: 2)
        #expect(fixture.model.workProcess.details[fixture.key]?.text == output)
        #expect(fixture.model.workProcess.errorEvents[fixture.key] == nil)
        await fixture.close()
    }

    @Test func streamedRevisionsDoNotCancelValidPrefixReads() async throws {
        let fixture = try Fixture(pauseCursor: 30_000, streaming: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 2 }
        #expect(fixture.model.workProcess.details[fixture.key]?.text.utf8.count == 30_000)
        for event in 2...6 {
            let total = await fixture.replies.output(event: event).utf8.count
            let updated = TranscriptBlock(id: fixture.block.id, kind: .command, text: "command",
                operation: WorkOperationRecord(id: "operation", eventId: event, kind: .command,
                    status: .running, outputBytes: total, detailsAvailable: true))
            fixture.model.transcripts[1] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 1, blocks: [updated])
            fixture.model.refreshExpandedWorkOperations()
        }
        #expect(await fixture.replies.requests.count == 2)
        await fixture.replies.release()
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 6 }
        #expect(fixture.model.workProcess.details[fixture.key]?.text == (await fixture.replies.output(event: 6)))
        #expect(await fixture.replies.requests.filter { $0.cursor == 0 }.count == 1)
        #expect(fixture.model.workProcess.errors[fixture.key] == nil)
        await fixture.close()
    }

    @Test func streamedTailRetriesAfterFailureWithoutAnotherEvent() async throws {
        let fixture = try Fixture(failCursor: 36_000, streaming: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 1 }
        #expect(fixture.model.workProcess.details[fixture.key]?.text == (await fixture.replies.output(event: 1)))
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 30_000, 36_000, 36_000])
        #expect(fixture.model.workProcess.errors[fixture.key] == nil)
        await fixture.close()
    }

    @Test func aKnownOutputPrefixStillFinishesAfterTheRunEnds() async throws {
        let fixture = try Fixture(pauseCursor: 30_000, streaming: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 2 }
        let run = try #require(fixture.model.activeRun)
        var finished = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        finished["status"] = "success"
        finished["workerActive"] = false
        fixture.model.activeRun = try JSONDecoder().decode(RunSummary.self,
            from: JSONSerialization.data(withJSONObject: finished))
        await fixture.replies.release()
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 1 }
        #expect(fixture.model.workProcess.details[fixture.key]?.text == (await fixture.replies.output(event: 1)))
        #expect(fixture.model.activeRun?.isWorking == false)
        await fixture.close()
    }

    @Test func anUnavailableIndexPreservesReadableOutputAndShowsItsFailure() async throws {
        let fixture = try Fixture(streaming: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { fixture.model.workProcess.loadedEvents[fixture.key] == 1 }
        let before = fixture.model.workProcess.details[fixture.key]?.text
        await fixture.replies.removeIndex(event: 2)
        let updated = TranscriptBlock(id: fixture.block.id, kind: .command, text: "command",
            operation: WorkOperationRecord(id: "operation", eventId: 2, kind: .command,
                status: .running, detailsAvailable: true))
        fixture.model.transcripts[1] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 1, blocks: [updated])
        fixture.model.refreshExpandedWorkOperations()
        try await waitUntil { fixture.model.workProcess.errors[fixture.key] != nil }
        #expect(fixture.model.workProcess.details[fixture.key]?.text == before)
        #expect(fixture.model.projectWorkOperations([updated]).contains { $0.activityType == "operation_retry" })
        #expect(fixture.model.activeRun?.isWorking == true)
        let count = await fixture.replies.requests.count
        fixture.model.refreshExpandedWorkOperations()
        #expect(await fixture.replies.requests.count == count)
        await fixture.close()
    }

    @Test func socketOutputRepaintsTheSameNativeReaderBeforeCommandCompletion() async throws {
        let fixture = try Fixture(pauseCursor: 8, streaming: true, smallOutput: true)
        fixture.model.activateWorkBlock(fixture.block)
        try await waitUntil { await fixture.replies.requests.count == 2 }
        let view = NSHostingView(rootView: OperationReader(model: fixture.model, blockID: fixture.block.id))
        view.frame = NSRect(x: 0, y: 0, width: 500, height: 160)
        view.layoutSubtreeIfNeeded()
        let before = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: before)
        func textViews(_ view: NSView) -> [NSTextView] {
            (view as? NSTextView).map { [$0] } ?? view.subviews.flatMap(textViews)
        }
        #expect(textViews(view).contains { $0.string.contains("epoch 1") })
        await fixture.replies.release()
        try await waitUntil {
            view.layoutSubtreeIfNeeded()
            return textViews(view).contains { $0.string.contains("epoch 3") }
        }
        let after = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: after)
        #expect(before.representation(using: .png, properties: [:]) != after.representation(using: .png, properties: [:]))
        #expect(fixture.model.transcripts[1]?.blocks.first?.operation?.status == .running)
        #expect(view.window == nil, "Live output QA must never show a window.")
        await fixture.close()
    }

    private struct OperationReader: NSViewRepresentable {
        @ObservedObject var model: AppModel
        let blockID: String
        func makeCoordinator() -> TranscriptCollectionItem { TranscriptCollectionItem() }
        func makeNSView(context: Context) -> NSView { context.coordinator.view }
        func updateNSView(_ view: NSView, context: Context) {
            guard let block = model.projectWorkOperations(model.selectedTranscriptBlocks)
                .first(where: { $0.id == blockID + ":details" }) else { return }
            context.coordinator.configure(block: block, language: .english, textSize: .standard,
                accentColor: .orange, appearance: .light, findQuery: "", currentFindOccurrence: nil,
                availableWidth: 500, openURL: { _ in }, activateBlock: { _ in })
        }
    }

    @Test func directoryPrefetchIsNearTheEndAndDoesNotBlockNavigation() async throws {
        let fixture = try Fixture(pauseCursor: 500)
        fixture.model.browserPresented = true
        try await fixture.model.loadDirectory("/folder")
        await fixture.model.prefetchDirectory(near: "/folder/10")
        #expect(await fixture.replies.requests.count == 1)
        let first = Task { await fixture.model.prefetchDirectory(near: "/folder/499") }
        try await waitUntil { await fixture.replies.requests.count == 2 }
        await fixture.model.prefetchDirectory(near: "/folder/499")
        #expect(await fixture.replies.requests.count == 2)
        #expect(!fixture.model.browserLoading)
        #expect(fixture.model.browserLoadingNextPage)
        #expect(fixture.model.browserPage?.entries.count == 500)
        await fixture.replies.release()
        await first.value
        await fixture.model.prefetchDirectory(near: "/folder/999")
        #expect(fixture.model.browserPage?.entries.count == 1205)
        #expect(fixture.model.browserPage?.hasMore == false)
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 500, 1000])
        await fixture.close()
    }

    @Test func delayedDirectoryPageCannotReplaceANewlyOpenedFolder() async throws {
        let fixture = try Fixture(pauseCursor: 500)
        fixture.model.browserPresented = true
        try await fixture.model.loadDirectory("/folder")
        let pagination = Task { await fixture.model.prefetchDirectory(near: "/folder/499") }
        try await waitUntil { await fixture.replies.requests.count == 2 }
        await fixture.model.navigateDirectory("/other")
        #expect(fixture.model.browserPage?.path == "/other")
        #expect(!fixture.model.browserLoadingNextPage)
        await fixture.replies.release()
        await pagination.value
        #expect(fixture.model.browserPage?.path == "/other")
        #expect(fixture.model.browserPage?.entries.count == 500)
        #expect(fixture.model.browserPaginationError == nil)
        await fixture.close()
    }

    @Test func directoryFailureIsLocalAndOnlyExplicitRetryRestartsIt() async throws {
        let fixture = try Fixture(failCursor: 500)
        fixture.model.browserPresented = true
        try await fixture.model.loadDirectory("/folder")
        await fixture.model.prefetchDirectory(near: "/folder/499")
        #expect(fixture.model.browserPage?.entries.count == 500)
        #expect(fixture.model.browserPaginationError != nil)
        #expect(fixture.model.browserError == nil)
        #expect(!fixture.model.browserLoading)
        await fixture.model.prefetchDirectory(near: "/folder/499")
        #expect(await fixture.replies.requests.count == 2)
        await fixture.model.retryDirectoryPagination()
        #expect(fixture.model.browserPaginationError == nil)
        #expect(fixture.model.browserPage?.entries.count == 1000)
        #expect(await fixture.replies.requests.map(\.cursor) == [0, 500, 500])
        await fixture.close()
    }

    @Test func returningToTheSameDirectoryRejectsThePreviousVisitAndItsCleanup() async throws {
        let fixture = try Fixture(pauseCursor: 500)
        fixture.model.browserPresented = true
        try await fixture.model.loadDirectory("/folder")
        let old = Task { await fixture.model.prefetchDirectory(near: "/folder/499") }
        try await waitUntil { await fixture.replies.requests.count == 2 }
        await fixture.model.navigateDirectory("/other")
        await fixture.model.navigateDirectory("/folder")
        let current = Task { await fixture.model.prefetchDirectory(near: "/folder/499") }
        try await waitUntil { await fixture.replies.requests.count == 5 }
        await fixture.replies.releaseFirst()
        await old.value
        #expect(fixture.model.browserLoadingNextPage)
        #expect(fixture.model.browserPage?.entries.count == 500)
        await fixture.replies.release()
        await current.value
        #expect(!fixture.model.browserLoadingNextPage)
        #expect(fixture.model.browserPage?.entries.count == 1000)
        #expect(fixture.model.browserPaginationError == nil)
        await fixture.close()
    }

    @Test func directoryCannotLoopOnAnUnchangedCursor() async throws {
        let fixture = try Fixture(stalled: true)
        fixture.model.browserPresented = true
        try await fixture.model.loadDirectory("/folder")
        await fixture.model.prefetchDirectory(near: "/folder/499")
        #expect(fixture.model.browserPaginationError != nil)
        #expect(fixture.model.browserPage?.entries.count == 500)
        await fixture.model.prefetchDirectory(near: "/folder/499")
        #expect(await fixture.replies.requests.count == 2)
        await fixture.close()
    }

    private func waitUntil(_ condition: () async -> Bool) async throws {
        for _ in 0..<300 {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw NSError(domain: "AutomaticPaginationTests", code: 1)
    }

    private actor Replies {
        struct Request: Sendable { let cursor: Int; let event: Int }
        var requests: [Request] = []
        var failCursor: Int?
        var pauseCursor: Int?
        let pauseEvent: Int?
        let stalled: Bool
        let streaming: Bool
        let smallOutput: Bool
        var removedEvent: Int?
        var waiting: [CheckedContinuation<Void, Never>] = []

        init(failCursor: Int?, pauseCursor: Int?, pauseEvent: Int?, stalled: Bool, streaming: Bool, smallOutput: Bool) {
            self.failCursor = failCursor
            self.pauseCursor = pauseCursor
            self.pauseEvent = pauseEvent
            self.stalled = stalled
            self.streaming = streaming
            self.smallOutput = smallOutput
        }

        func chunks(event: Int) -> [String] {
            if smallOutput { return ["epoch 1\n", "epoch 2\n", "epoch 3\n"] }
            let base = [String(repeating: !streaming && event == 2 ? "b" : "a", count: 30_000), String(repeating: "中", count: 2000), "END-OF-OUTPUT"]
            return streaming && event > 1 ? base + (2...event).map { "\nrevision \($0)" } : base
        }

        func output(event: Int) -> String { chunks(event: event).joined() }

        func removeIndex(event: Int) { removedEvent = event }

        func release() {
            pauseCursor = nil
            for continuation in waiting { continuation.resume() }
            waiting.removeAll()
        }

        func releaseFirst() { if !waiting.isEmpty { waiting.removeFirst().resume() } }

        func reply(_ data: Data) async throws -> Data {
            let request = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
            let body = try #require(request["body"] as? [String: Any])
            let cursor = body["cursor"] as? Int ?? 0
            let event = body["event_id"] as? Int ?? 0
            requests.append(Request(cursor: cursor, event: event))
            if cursor == pauseCursor, pauseEvent == nil || event == pauseEvent {
                await withCheckedContinuation { waiting.append($0) }
            }
            var response: [String: Any]
            if event == removedEvent {
                response = ["ok": true, "body": ["text": "", "next_cursor": 0, "has_more": false,
                    "total_bytes": 0, "availability": "removed"]]
            } else if cursor == failCursor {
                failCursor = nil
                response = ["ok": false, "error": ["code": "pcr_error", "message": "Fixture read failure"]]
            } else if request["action"] as? String == "runs.operation" {
                let parts = chunks(event: event)
                var offset = 0
                let offsets = parts.map { part in defer { offset += part.utf8.count }; return offset }
                let index = try #require(offsets.firstIndex(of: cursor))
                let text = stalled ? "" : parts[index]
                response = ["ok": true, "body": ["text": text, "next_cursor": cursor + text.utf8.count,
                    "has_more": stalled || index < parts.count - 1, "total_bytes": parts.joined().utf8.count,
                    "availability": "available"]]
            } else {
                let path = body["path"] as? String ?? "/folder"
                let next = min(cursor + 500, 1205)
                let entries = (cursor..<next).map { index in ["name": "Folder \(index)", "path": "\(path)/\(index)",
                    "kind": "directory", "is_symlink": false, "readable": true, "writable": true, "hidden": false] as [String: Any] }
                response = ["ok": true, "body": ["name": path, "path": path, "parent": "/", "readable": true,
                    "writable": true, "is_git_repository": false, "cursor": cursor,
                    "next_cursor": stalled && cursor > 0 ? cursor : next, "has_more": next < 1205,
                    "total": 1205, "entries": entries]]
            }
            return try JSONSerialization.data(withJSONObject: response) + Data([10])
        }
    }

    @MainActor private final class Fixture {
        let directory: URL
        let listener: Int32
        let replies: Replies
        let server: Task<Void, Never>
        let model: AppModel
        let windowID = UUID()
        let runID = UUID().uuidString
        let block: TranscriptBlock
        var key: String { model.selectedAgentWorkContextKey! + "|" + block.id }

        init(failCursor: Int? = nil, pauseCursor: Int? = nil, pauseEvent: Int? = nil, stalled: Bool = false,
             streaming: Bool = false, smallOutput: Bool = false) throws {
            block = TranscriptBlock(id: UUID().uuidString, kind: .command, text: "command",
                operation: WorkOperationRecord(id: "operation", eventId: 1, kind: .command,
                    status: streaming ? .running : .completed,
                    outputBytes: smallOutput ? 24 : streaming ? 36_000 + "END-OF-OUTPUT".utf8.count : nil,
                    detailsAvailable: true))
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-auto-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socket = directory.appendingPathComponent("s"), token = directory.appendingPathComponent("t")
            try Data("qa-token".utf8).write(to: token)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            let descriptor = listener
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let bytes = Array(socket.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { target in
                bytes.withUnsafeBytes { target.copyBytes(from: $0) }
            }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(bound == 0 && Darwin.listen(descriptor, 8) == 0)
            let replies = Replies(failCursor: failCursor, pauseCursor: pauseCursor, pauseEvent: pauseEvent,
                stalled: stalled, streaming: streaming, smallOutput: smallOutput)
            self.replies = replies
            server = Task.detached {
                await withTaskGroup(of: Void.self) { group in
                    while !Task.isCancelled {
                        var pending = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                        guard Darwin.poll(&pending, 1, 50) >= 0 else { break }
                        guard pending.revents & Int16(POLLIN) != 0 else { continue }
                        let connection = Darwin.accept(descriptor, nil, nil)
                        guard connection >= 0 else { break }
                        group.addTask {
                            defer { Darwin.close(connection) }
                            var timeout = timeval(tv_sec: 2, tv_usec: 0), noSignal: Int32 = 1
                            _ = withUnsafePointer(to: &timeout) {
                                Darwin.setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
                            }
                            _ = withUnsafePointer(to: &noSignal) {
                                Darwin.setsockopt(connection, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size))
                            }
                            var buffer = [UInt8](repeating: 0, count: 8192)
                            let count = Darwin.read(connection, &buffer, buffer.count)
                            guard count > 0, let reply = try? await replies.reply(Data(buffer.prefix(count))) else { return }
                            reply.withUnsafeBytes { bytes in
                                var sent = 0
                                while sent < bytes.count {
                                    let count = Darwin.write(connection, bytes.baseAddress! + sent, bytes.count - sent)
                                    guard count > 0 else { break }
                                    sent += count
                                }
                            }
                        }
                    }
                    group.cancelAll()
                }
            }
            model = AppModel(client: PcrdClient(socketURL: socket, tokenURL: token), windowID: windowID,
                             desktopStateStore: DesktopStateStore(directory: directory.appendingPathComponent("state")))
            model.workProcess.sections.removeAll()
            model.workProcess.expanded.removeAll()
            model.selectedWorkspaceID = UUID().uuidString
            model.selectedAgent = 1
            model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": runID, "status": "running", "workspace": directory.path, "prompt": "Question",
                "createdAt": "2026-09-30T00:00:00Z", "updatedAt": "2026-09-30T00:00:00Z", "config": [:],
                "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": true, "workerAvailable": true, "eventCount": 1, "agents": [],
            ]))
            model.transcripts[1] = AgentTranscript(sourceRunID: runID, sourceAgent: 1, blocks: [block])
        }

        func close() async {
            model.stopWindowObservation()
            _ = model.desktopStateStore.flushPendingWrites()
            await replies.release()
            server.cancel()
            await server.value
            Darwin.close(listener)
            try? FileManager.default.removeItem(at: directory)
            if let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent(
                    "Parallel Codex/window-\(windowID.uuidString).v3.json"))
            }
        }
    }
}
