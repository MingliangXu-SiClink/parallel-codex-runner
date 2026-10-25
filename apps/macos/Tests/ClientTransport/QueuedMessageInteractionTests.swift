import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct QueuedMessageInteractionTests {
    @Test
    func openManagerUsesFreshQueueGenerationAndPreservesConcurrentNewMessages() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let added = QueuedFollowUp(prompt: "Added by another window", settings: RunSettings())
        let fixture = try Fixture(local: items, remote: items + [added])
        defer { fixture.close() }
        fixture.model.presentFollowUpQueue()
        let saved = await fixture.model.changeQueuedFollowUp(items[2].id,
            change: .place(relativeTo: items[0].id, after: false), expectedContext: fixture.model.followUpQueueContext)
        #expect(saved)
        #expect(fixture.model.queuePresented)
        #expect(fixture.model.followUpQueue.map(\.id) == [items[2].id, items[0].id, items[1].id, added.id])
        #expect(await fixture.replies.replacementGenerations == [7])
        #expect(await fixture.replies.replaceCount == 1)
        await fixture.model.cancelFollowUpQueueEditing()
        #expect(await fixture.replies.replaceCount == 1)
    }

    @Test
    func latestSendingHeadStaysLockedWhileTheWaitingTailCanMove() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: items, state: "dispatching")
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[0].id, change: .remove) == false)
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id, change: .move(-1)) == false)
        #expect(await fixture.replies.replaceCount == 0)
        #expect(await fixture.model.changeQueuedFollowUp(items[2].id,
            change: .place(relativeTo: items[1].id, after: false)))
        #expect(fixture.model.followUpQueue.map(\.id) == [items[0].id, items[2].id, items[1].id])
        #expect(await fixture.replies.replaceCount == 1)
    }

    @Test
    func disappearedSourceAndAnchorCannotBeRecreatedByAnOldDrag() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: Array(items.dropFirst()))
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[0].id,
            change: .place(relativeTo: items[2].id, after: true)) == false)
        #expect(await fixture.model.changeQueuedFollowUp(items[2].id,
            change: .place(relativeTo: items[0].id, after: false)) == false)
        #expect(fixture.model.followUpQueue.map(\.id) == [items[1].id, items[2].id])
        #expect(await fixture.replies.replaceCount == 0)
    }

    @Test
    func aConflictOrContextChangeNeverReplacesTheQueueWithADragSnapshot() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: items, rejectReplace: true)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[2].id, change: .move(-1)) == false)
        #expect(fixture.model.followUpQueue.map(\.id) == items.map(\.id))
        #expect(fixture.model.queueIssue != nil)
        let context = fixture.model.followUpQueueContext
        fixture.model.workspaceContextGeneration += 1
        #expect(await fixture.model.changeQueuedFollowUp(items[2].id, change: .move(-1), expectedContext: context) == false)
        #expect(await fixture.replies.replaceCount == 1)
        #expect(await fixture.replies.actions.allSatisfy { $0 == "followups.get" || $0 == "followups.replace" })
    }

    @Test
    func aNoOpDoesNotResetCountdownAndAnOldSuccessfulResponseCannotRestoreSentItems() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: items)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id,
            change: .place(relativeTo: items[0].id, after: true)))
        #expect(await fixture.replies.replaceCount == 0)
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: Array(items.dropFirst()), generation: 9),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 9)
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: items, generation: 8),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 8)
        #expect(fixture.model.followUpQueue.map(\.id) == [items[1].id, items[2].id])
    }

    @Test
    func removingAMessagePersistsOnlyOnceAndRefreshCannotRestoreIt() async throws {
        let items = (1...3).map { index in
            var item = QueuedFollowUp(prompt: "Message \(index)", settings: RunSettings())
            item.daemonAcknowledged = true
            return item
        }
        var added = QueuedFollowUp(prompt: "Added by another window", settings: RunSettings())
        added.daemonAcknowledged = true
        let fixture = try Fixture(local: items, remote: items + [added])
        defer { fixture.close() }
        let context = fixture.model.followUpQueueContext
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id, change: .remove, expectedContext: context))
        let remaining = [items[0], items[2], added]
        #expect(fixture.model.followUpQueue == remaining)
        #expect(await fixture.replies.items == remaining)
        #expect(await fixture.replies.replacementGenerations == [7])
        #expect(fixture.model.queueIssue == nil)
        #expect(fixture.model.desktopStateStore.flushPendingWrites())
        let persisted = fixture.model.desktopStateStore.loadSnapshot().followUpQueuesByContext
        #expect(persisted.values.flatMap(\.items).map(\.id) == remaining.map(\.id))

        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: items, generation: 7),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 7)
        #expect(fixture.model.followUpQueue == remaining)
        fixture.model.presentFollowUpQueue()
        await fixture.model.cancelFollowUpQueueEditing()
        #expect(fixture.model.followUpQueue == remaining)
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id, change: .remove, expectedContext: context) == false)
        #expect(fixture.model.followUpQueue == remaining)
        #expect(await fixture.replies.replaceCount == 1)
        #expect(await fixture.replies.actions.allSatisfy { $0 == "followups.get" || $0 == "followups.replace" })
    }

    @Test
    func failedRemovalKeepsTheOriginalQueueAndReportsTheSaveFailure() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: items, rejectReplace: true)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id, change: .remove) == false)
        #expect(fixture.model.followUpQueue.map(\.id) == items.map(\.id))
        #expect(fixture.model.followUpQueue.map(\.prompt) == items.map(\.prompt))
        #expect(await fixture.replies.items == items)
        #expect(fixture.model.queueIssue != nil)
        #expect(await fixture.replies.replaceCount == 1)
    }

    @Test
    func removingTheLastMessageClearsThePersistedQueue() async throws {
        let item = QueuedFollowUp(prompt: "Intentionally removed", settings: RunSettings())
        let fixture = try Fixture(local: [item], remote: [item])
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(item.id, change: .remove))
        #expect(fixture.model.followUpQueue.isEmpty)
        #expect(await fixture.replies.items.isEmpty)
        #expect(await fixture.replies.replaceCount == 1)
        #expect(fixture.model.followUpSecondsRemaining == nil)
        #expect(fixture.model.queueIssue == nil)
        #expect(fixture.model.desktopStateStore.flushPendingWrites())
        #expect(fixture.model.desktopStateStore.loadSnapshot().followUpQueuesByContext.isEmpty)
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: [item], generation: 7),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 7)
        #expect(fixture.model.followUpQueue.isEmpty)
    }

    @Test
    func aLostRemovalReplyReconcilesExactStateWithoutRepeatingOrLosingConcurrentMessages() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let added = QueuedFollowUp(prompt: "Concurrent new message", settings: RunSettings())
        let fixture = try Fixture(local: items, remote: items, loseReplaceReply: true, appendAfterReplace: added)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[1].id, change: .remove))
        let expectedIDs = [items[0].id, items[2].id, added.id]
        #expect(await fixture.replies.items.map(\.id) == expectedIDs)
        #expect(fixture.model.followUpQueue.map(\.id) == expectedIDs)
        #expect(fixture.model.queueIssue == nil)
        #expect(fixture.model.queueIssueRecovery == nil)
        #expect(await fixture.replies.replaceCount == 1)
        #expect(await Set(fixture.replies.replacementCommands).count == 1)
        #expect(fixture.model.desktopStateStore.flushPendingWrites())
        #expect(DesktopStateStore(directory: fixture.directory).loadSnapshot()
            .followUpQueuesByContext.values.flatMap(\.items).map(\.id) == expectedIDs)
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: items, generation: 7),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 7)
        #expect(fixture.model.followUpQueue.map(\.id) == expectedIDs)
        #expect(await fixture.replies.actions.allSatisfy { $0 == "followups.get" || $0 == "followups.replace" })
    }

    @Test
    func aDelayedQueueReadOnlySettlesAnExactlyMatchingAuthoritativeOutcome() async throws {
        let items = (1...3).map { QueuedFollowUp(prompt: "Message \($0)", settings: RunSettings()) }
        let fixture = try Fixture(local: items, remote: items,
            loseReplaceReply: true, loseRecoveryRead: true)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(items[2].id, change: .move(-1)) == false)
        #expect(fixture.model.queueIssue != nil)
        #expect(fixture.model.queueIssueRecovery != nil)
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: items, generation: 9),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 9)
        #expect(fixture.model.queueIssue != nil)
        let moved = [items[0], items[2], items[1]]
        fixture.model.applyDaemonFollowUpQueue(Fixture.snapshot(items: moved, generation: 10),
            workspaceID: "workspace", machineID: fixture.model.currentMachineContextID, revision: 10)
        #expect(fixture.model.queueIssue == nil)
        #expect(fixture.model.followUpQueue.map(\.id) == moved.map(\.id))
        #expect(await fixture.replies.replaceCount == 1)
    }

    @Test
    func aLostReplyForTheLastRemovalCannotResurrectTheDeletedMessage() async throws {
        let item = QueuedFollowUp(prompt: "Removed deliberately", settings: RunSettings())
        let fixture = try Fixture(local: [item], remote: [item], loseReplaceReply: true)
        defer { fixture.close() }
        #expect(await fixture.model.changeQueuedFollowUp(item.id, change: .remove))
        #expect(fixture.model.followUpQueue.isEmpty)
        #expect(fixture.model.queueIssue == nil)
        #expect(fixture.model.desktopStateStore.flushPendingWrites())
        #expect(DesktopStateStore(directory: fixture.directory).loadSnapshot().followUpQueuesByContext.isEmpty)
        #expect(await fixture.replies.replaceCount == 1)
    }

    private actor Replies {
        var items: [QueuedFollowUp]
        let state: String
        let rejectReplace: Bool
        let loseReplaceReply: Bool
        let loseRecoveryRead: Bool
        let appendAfterReplace: QueuedFollowUp?
        var generation = 7
        var replaceCount = 0
        var replacementGenerations: [Int] = []
        var replacementCommands: [String] = []
        var replacementsByCommand: [String: Data] = [:]
        var actions: [String] = []

        init(items: [QueuedFollowUp], state: String, rejectReplace: Bool,
             loseReplaceReply: Bool, loseRecoveryRead: Bool, appendAfterReplace: QueuedFollowUp?) {
            self.items = items
            self.state = state
            self.rejectReplace = rejectReplace
            self.loseReplaceReply = loseReplaceReply
            self.loseRecoveryRead = loseRecoveryRead
            self.appendAfterReplace = appendAfterReplace
        }

        func reply(_ data: Data) throws -> Data {
            let request = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let action = try #require(request["action"] as? String)
            actions.append(action)
            if action == "followups.replace" {
                let body = try #require(request["body"] as? [String: Any])
                let command = try #require(request["command_id"] as? String)
                replacementCommands.append(command)
                let bytes = try JSONSerialization.data(withJSONObject: body, options: .sortedKeys)
                if let original = replacementsByCommand[command] {
                    #expect(original == bytes)
                    #expect(loseReplaceReply)
                    return Data()
                }
                replacementsByCommand[command] = bytes
                replaceCount += 1
                let expected = try #require(body["expected_generation"] as? Int)
                replacementGenerations.append(expected)
                if rejectReplace || expected != generation {
                    return try encode(["ok": false, "error": ["code": "follow_up_conflict", "message": "Queue changed"]])
                }
                let replacements = try #require(body["items"] as? [[String: Any]])
                items = try replacements.map { value in
                    let id = try #require((value["id"] as? String).flatMap(UUID.init(uuidString:)))
                    return try #require(items.first { $0.id == id })
                }
                generation += 1
                if let appendAfterReplace { items.append(appendAfterReplace) }
                if loseReplaceReply { return Data() }
            } else if action == "followups.get" && replaceCount > 0 && loseRecoveryRead {
                return Data()
            } else if action != "followups.get" {
                Issue.record("Queue interaction must not submit, stop or retry a turn: \(action)")
                return try encode(["ok": false, "error": ["code": "unknown_action", "message": action]])
            }
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            let wireItems = try items.map { item -> [String: Any] in
                var value = try #require(JSONSerialization.jsonObject(with: encoder.encode(
                    FollowUpQueueItemBody(item, fallbackSettings: RunSettings()))) as? [String: Any])
                value["failure_count"] = item.failureCount ?? 0
                value["paused"] = item.isPaused
                value["last_error"] = item.lastError
                return value
            }
            return try encode(["ok": true, "body": ["revision": generation, "queue": [
                "workspace_id": "workspace", "run_id": "queue-run", "conversation_id": "queue-conversation",
                "items": wireItems, "generation": generation, "state": state]]])
        }

        private func encode(_ value: [String: Any]) throws -> Data {
            try JSONSerialization.data(withJSONObject: value) + Data([10])
        }
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let model: AppModel
        let replies: Replies
        let listener: Int32
        let server: Task<Void, Never>

        init(local: [QueuedFollowUp], remote: [QueuedFollowUp], state: String = "waiting",
             rejectReplace: Bool = false, loseReplaceReply: Bool = false,
             loseRecoveryRead: Bool = false, appendAfterReplace: QueuedFollowUp? = nil) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-queue-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socket = directory.appendingPathComponent("s"), token = directory.appendingPathComponent("t")
            try Data("fixture-token".utf8).write(to: token)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            let descriptor = listener
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { destination in
                Array(socket.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
            }
            let result = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(result == 0 && Darwin.listen(descriptor, 8) == 0)
            let replies = Replies(items: remote, state: state, rejectReplace: rejectReplace,
                loseReplaceReply: loseReplaceReply, loseRecoveryRead: loseRecoveryRead,
                appendAfterReplace: appendAfterReplace)
            self.replies = replies
            server = Task.detached {
                while !Task.isCancelled {
                    await Task.yield()
                    var pending = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                    guard Darwin.poll(&pending, 1, 100) >= 0 else { return }
                    guard pending.revents & Int16(POLLIN) != 0 else { continue }
                    let connection = Darwin.accept(descriptor, nil, nil)
                    guard connection >= 0 else { return }
                    defer { Darwin.close(connection) }
                    var data = Data()
                    var bytes = [UInt8](repeating: 0, count: 65_536)
                    repeat {
                        let count = Darwin.read(connection, &bytes, bytes.count)
                        guard count > 0 else { break }
                        data.append(contentsOf: bytes.prefix(count))
                    } while !data.contains(10)
                    guard let reply = try? await replies.reply(data) else { continue }
                    _ = reply.withUnsafeBytes { Darwin.write(connection, $0.baseAddress, $0.count) }
                }
            }
            model = AppModel(client: PcrdClient(socketURL: socket, tokenURL: token), windowID: UUID(),
                desktopStateStore: DesktopStateStore(directory: directory))
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
                {"workspaceId":"workspace","name":"Queue fixture","path":"/qa","lastRunId":"queue-run",
                 "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
                """.utf8))]
            model.selectedWorkspaceID = "workspace"
            model.selectedConversationID = "queue-conversation"
            model.activeRun = try JSONDecoder().decode(RunSummary.self, from: Data("""
                {"runId":"queue-run","conversationId":"queue-conversation","status":"running",
                 "workspace":"/qa","prompt":"Fixture","config":{},"createdAt":"2026-10-10T00:00:00Z",
                 "updatedAt":"fixture","syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
                 "workerActive":true,"workerAvailable":true,"eventCount":0,
                 "agents":[{"agent":1,"name":"Agent 1","status":"running","reasoningTokenCounts":{},
                            "rejected":false,"recommended":false,"finalized":false}]}
                """.utf8))
            model.preparePromptHistoryContext()
            model.daemonFollowUpQueueConfirmedMachines.insert(model.currentMachineContextID)
            model.applyDaemonFollowUpQueue(Self.snapshot(items: local, generation: 1),
                workspaceID: "workspace", machineID: model.currentMachineContextID, revision: 1)
        }

        static func snapshot(items: [QueuedFollowUp], generation: Int) -> RemoteFollowUpQueue {
            RemoteFollowUpQueue(workspaceId: "workspace", runId: "queue-run", items: items.map {
                RemoteFollowUpQueueItem(id: $0.id.uuidString.lowercased(), prompt: $0.prompt,
                    settings: $0.settings ?? RunSettings(), attempt: $0.attempt, failureCount: $0.failureCount,
                    retryAtEpochMs: nil, lastError: $0.lastError, paused: $0.paused)
            }, selectedAgent: nil, state: "waiting", deadlineEpochMs: nil, lastError: nil,
                generation: generation, lastCompletedItemId: nil, conversationId: "queue-conversation")
        }

        func close() {
            model.followUpTask?.cancel()
            model.promptDraftSaveTask?.cancel()
            model.stopWindowObservation()
            server.cancel()
            Darwin.close(listener)
            _ = model.desktopStateStore.flushPendingWrites()
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
