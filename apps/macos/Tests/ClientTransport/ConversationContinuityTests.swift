import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

private func awaitNavigationFixtureRelease(_ gate: DispatchSemaphore) -> Bool {
    gate.wait(timeout: .now() + 5) == .success
}

private func consumeAvailableSignals(_ semaphore: DispatchSemaphore) -> Int {
    var count = 0
    while semaphore.wait(timeout: .now()) == .success { count += 1 }
    return count
}

@Suite(.serialized)
@MainActor
struct ConversationContinuityTests {
    @Test func provenRecoveryInputsStayAuditableButNeverBecomeUserBubbles() throws {
        let text = "Continue the unfinished task from its saved progress, without restarting completed work."
        let control = ResumeHistoryEntry(category: "user", text: text, id: "native-control",
            turnId: "native-turn", inputOrigin: "pcr_recovery")
        let human = ResumeHistoryEntry(category: "user", text: text, id: "native-human", turnId: "human-turn")
        let answer = ResumeHistoryEntry(category: "output", text: "Preserved output", id: "answer")
        let raw = [control, human, answer]
        let blocks = TranscriptFormatter.blocks(history: raw, prompt: "", transcript: AgentTranscript())
        #expect(blocks.map(\.id) == [human.id, answer.id])
        #expect(blocks[0].text == text)
        var legacy = control
        legacy.inputOrigin = nil
        let corrected = mergeConversationHistory(current: [legacy, human, answer], authoritative: raw)
        #expect(corrected == raw)
        #expect(mergeConversationHistory(current: raw, authoritative: [legacy, human, answer]) == raw)
        #expect(mergeConversationHistory(current: [legacy, human, answer], authoritative: [control]) == raw)
        let encoded = try JSONEncoder().encode(raw)
        #expect(try JSONDecoder().decode([ResumeHistoryEntry].self, from: encoded) == raw)
    }

    @Test func oldHistoryCacheRefreshesProvenanceWithoutLosingTheConversation() throws {
        let data = Data("""
        {"transcripts":{},"conversationHistory":[{"category":"user","text":"Original question","id":"q","turnId":"t"}],"historyLoaded":true}
        """.utf8)
        let old = try JSONDecoder().decode(WorkspaceRunViewSnapshot.self, from: data)
        let current = old.validated(for: WorkspaceRunViewKey(machineID: "m", workspaceID: "w", runID: "r"))
        #expect(current.historyLoaded == false)
        #expect(current.conversationHistory == old.conversationHistory)
        #expect(current.historyProjectionVersion == 1)
    }

    private func workspace() throws -> RemoteWorkspace {
        try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"continuity","name":"Continuity QA","path":"/continuity","lastRunId":"a",
         "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
        """.utf8))
    }

    private func run(_ id: String, conversation: String) throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"\(id)","conversationId":"\(conversation)","turnIndex":0,"status":"completed",
         "workspace":"/continuity","prompt":"Question \(id)","createdAt":"2026-10-03T00:00:00Z",
         "updatedAt":"2026-10-03T00:00:00Z","config":{},"syncedBack":false,"workspacesDeleted":false,
         "codexHomesDeleted":false,"workerActive":false,"workerAvailable":true,"eventCount":0,"agents":[]}
        """.utf8))
    }

    private func model() throws -> AppModel {
        let client = PcrdClient(socketURL: URL(fileURLWithPath: "/tmp/pcr-continuity-no-daemon"),
            tokenURL: URL(fileURLWithPath: "/tmp/pcr-continuity-no-token"))
        let model = AppModel(client: client, windowID: UUID())
        model.workspaces = [try workspace()]
        model.selectedWorkspaceID = "continuity"
        model.activeRun = try run("a", conversation: "conversation-a")
        model.selectedConversationID = "conversation-a"
        model.workspaceConversations = [WorkspaceConversationSummary(id: "conversation-a", title: "A",
            runs: [try run("a", conversation: "conversation-a")], resumeSession: nil)]
        return model
    }

    private func queue(runID: String = "a", conversation: String? = "conversation-a",
                       generation: Int = 1, items: [RemoteFollowUpQueueItem]? = nil) -> RemoteFollowUpQueue {
        RemoteFollowUpQueue(workspaceId: "continuity", runId: runID,
            items: items ?? [RemoteFollowUpQueueItem(id: "c6d8a055-86f8-4ca5-9cba-ec04c072ec2a",
                prompt: "Keep this queued message", settings: RunSettings(), attempt: 0,
                failureCount: 0, retryAtEpochMs: nil, lastError: nil, paused: false)],
            selectedAgent: nil, state: "waiting", deadlineEpochMs: nil, lastError: nil,
            generation: generation, lastCompletedItemId: nil, conversationId: conversation)
    }

    @Test func warmNavigationRestoresQueueAndTranscriptBeforeTheTurnRequestReturns() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-nav-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socket = directory.appendingPathComponent("s")
        let token = directory.appendingPathComponent("t")
        try Data("fixture-only".utf8).write(to: token)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            Array(socket.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
        }
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0 && Darwin.listen(listener, 1) == 0)
        let gate = DispatchSemaphore(value: 0)
        let server = Task.detached {
            defer { Darwin.close(listener) }
            var poll = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
            guard Darwin.poll(&poll, 1, 5_000) > 0 else { return false }
            let descriptor = Darwin.accept(listener, nil, nil)
            guard descriptor >= 0 else { return false }
            defer { Darwin.close(descriptor) }
            var bytes = [UInt8](repeating: 0, count: 8192)
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            guard count > 0, let request = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Any],
                  request["action"] as? String == "turns.get" else { return false }
            guard awaitNavigationFixtureRelease(gate) else { return false }
            let reply = Data("{\"ok\":false,\"error\":{\"code\":\"offline\",\"message\":\"Fixture connection unavailable\"}}\n".utf8)
            return reply.withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress, $0.count) } == reply.count
        }
        let model = AppModel(client: PcrdClient(socketURL: socket, tokenURL: token), windowID: UUID())
        defer { model.stopWindowObservation(); gate.signal() }
        let a = try run("a", conversation: "conversation-a")
        let b = try run("b", conversation: "conversation-b")
        let transcript = AgentTranscript(sourceRunID: "a", sourceAgent: 1,
            blocks: [TranscriptBlock(id: "saved", kind: .output, text: "Already read answer")])
        model.workspaces = [try workspace()]
        model.selectedWorkspaceID = "continuity"
        model.workspaceConversations = [
            WorkspaceConversationSummary(id: "conversation-a", title: "A", runs: [a], resumeSession: nil),
            WorkspaceConversationSummary(id: "conversation-b", title: "B", runs: [b], resumeSession: nil)]
        model.runViewStateStore.put(WorkspaceRunViewSnapshot(selectedAgent: nil, transcripts: [1: transcript],
            conversationHistory: []), runSummary: a,
            for: WorkspaceRunViewKey(machineID: model.currentMachineContextID, workspaceID: "continuity", runID: "a"))
        let queued = try #require(queue().items.first?.localValue())
        let saved = PersistedFollowUpQueue(machineID: model.currentMachineContextID, workspaceID: "continuity", runID: "a", items: [queued])
        model.persistedFollowUpQueues[saved.contextKey] = saved
        model.selectedConversationID = "conversation-b"
        model.activeRun = b
        let navigation = Task { await model.selectConversation("conversation-a") }
        for _ in 0..<100 where model.activeRun?.runId != "a" { try await Task.sleep(for: .milliseconds(10)) }
        #expect(model.activeRun?.runId == "a")
        #expect(model.openingConversationID == "conversation-a")
        #expect(model.transcripts[1] == transcript)
        #expect(model.followUpQueue == [queued])
        #expect(model.followUpRunID == "a")
        gate.signal()
        #expect(await server.value)
        await navigation.value
        #expect(model.transcripts[1] == transcript)
        #expect(model.followUpQueue == [queued])
    }

    @Test func restorationIsReadOnlyAndConfirmedItemsRemainAcknowledged() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        let remote = queue()
        let key = model.machineWorkspaceContextKey(workspaceID: "continuity")
        model.daemonFollowUpSnapshotsByWorkspace[key] = remote
        model.restoreCachedFollowUpQueue(workspaceID: "continuity", runID: "a")
        #expect(model.followUpQueue.count == 1)
        #expect(model.followUpQueue.allSatisfy { !$0.needsDaemonAcknowledgement })
        #expect(model.followUpEnqueueTask == nil && model.followUpTask == nil)
        #expect(model.persistedFollowUpQueues.isEmpty)
    }

    @Test func queueFollowsItsConversationEvenBeforeTheNextTurnIndexArrives() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        let remote = queue(runID: "next-a")
        model.daemonFollowUpSnapshotsByWorkspace[model.machineWorkspaceContextKey(workspaceID: "continuity")] = remote
        model.restoreCachedFollowUpQueue(workspaceID: "continuity", runID: "a")
        #expect(model.followUpQueue.count == 1)
        #expect(model.followUpRunID == "next-a")
        #expect(model.activeRun?.runId == "a")
    }

    @Test func unrelatedConversationsAndMachinesNeverShareQueueProjection() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.applyDaemonFollowUpQueue(queue(conversation: "conversation-b"), workspaceID: "continuity",
            machineID: model.currentMachineContextID, revision: 1)
        #expect(model.followUpQueue.isEmpty && model.daemonFollowUpSnapshot == nil)
        model.applyDaemonFollowUpQueue(queue(), workspaceID: "continuity", machineID: "other-machine", revision: 2)
        #expect(model.followUpQueue.isEmpty)
        #expect(model.daemonFollowUpSnapshotsByWorkspace[MachineContextKey.workspace(machineID: "other-machine", workspaceID: "continuity")] != nil)
    }

    @Test func successfulEmptyQueueIsNotUndoneByAnOlderResponse() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.applyDaemonFollowUpQueue(queue(), workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 4)
        #expect(model.followUpQueue.count == 1)
        model.applyDaemonFollowUpQueue(nil, workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 3)
        #expect(model.followUpQueue.count == 1)
        model.applyDaemonFollowUpQueue(nil, workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 5)
        #expect(model.followUpQueue.isEmpty)
        model.applyDaemonFollowUpQueue(queue(), workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 4)
        model.restoreCachedFollowUpQueue(workspaceID: "continuity", runID: "a")
        #expect(model.followUpQueue.isEmpty)
        #expect(model.persistedFollowUpQueues.isEmpty)
    }

    @Test func failedRefreshKeepsTheConfirmedQueue() async throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.daemonFollowUpQueueConfirmedMachines.insert(model.currentMachineContextID)
        model.applyDaemonFollowUpQueue(queue(), workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 1)
        let original = model.followUpQueue
        _ = await model.refreshDaemonFollowUpQueue(workspaceID: "continuity", runID: "a",
            machineID: model.currentMachineContextID, migrateLocalQueue: false)
        #expect(model.followUpQueue == original)
    }

    @Test func pausedAdmissionIsCheckedOnceWithTheOriginalEnqueueIdentity() async throws {
        let directory = URL(fileURLWithPath: "/tmp").appendingPathComponent("pcr-admit-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socket = directory.appendingPathComponent("s")
        let token = directory.appendingPathComponent("t")
        try Data("fixture-only".utf8).write(to: token)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            Array(socket.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
        }
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0 && Darwin.listen(listener, 4) == 0)
        let paused = """
        {"revision":31,"queue":{"workspaceId":"continuity","runId":"a","conversationId":"conversation-a",
        "items":[{"id":"4fba1ec5-0781-4a0d-bf72-47f611573a0b","prompt":"Keep the original message",
        "settings":{},"attempt":0,"failureCount":0,"paused":true,
        "lastError":"The conversation changed before this queued message could be sent. Open the queue to review it."}],
        "state":"paused","generation":31,"lastCompletedItemId":"consumed"}}
        """
        let server = Task.detached {
            defer { Darwin.close(listener) }
            var enqueues = 0
            for _ in 0..<4 {
                var poll = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
                guard Darwin.poll(&poll, 1, 5_000) > 0 else { return "listener timeout" }
                let descriptor = Darwin.accept(listener, nil, nil)
                guard descriptor >= 0 else { return "accept failed" }
                var bytes = [UInt8](repeating: 0, count: 32768)
                let count = Darwin.read(descriptor, &bytes, bytes.count)
                let request = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(max(0, count)))) as? [String: Any]
                guard let request else { Darwin.close(descriptor); return "request decoding failed" }
                if request["action"] as? String == "followups.reconcile_enqueue" {
                    enqueues += 1
                    let body = request["body"] as? [String: Any]
                    let enqueue = body?["enqueue"] as? [String: Any]
                    let item = enqueue?["item"] as? [String: Any]
                    guard body?["enqueue_command_id"] as? String == "follow-up-enqueue-4fba1ec5-0781-4a0d-bf72-47f611573a0b",
                          enqueue?["run_id"] as? String == "d", item?["attempt"] as? Int == 0,
                          item?["prompt"] as? String == "Keep the original message" else {
                        Darwin.close(descriptor); return "original enqueue identity changed"
                    }
                } else if !["followups.get", "system.capabilities"].contains(request["action"] as? String ?? "") {
                    Darwin.close(descriptor); return "unexpected action: \(request["action"] ?? "missing")"
                }
                let payload = request["action"] as? String == "system.capabilities"
                    ? "{\"actions\":[\"followups.enqueue\",\"followups.replace\",\"followups.reconcile_enqueue\"],\"supportsFollowUpEnqueueReconciliation\":true}"
                    : paused
                guard let body = try? JSONSerialization.jsonObject(with: Data(payload.utf8)),
                      var reply = try? JSONSerialization.data(withJSONObject: ["ok": true, "body": body]) else {
                    Darwin.close(descriptor); return "reply encoding failed"
                }
                reply.append(0x0A)
                let sent = reply.withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress, $0.count) } == reply.count
                Darwin.close(descriptor)
                guard sent else { return "reply write failed" }
            }
            return enqueues == 1 ? "one original enqueue" : "wrong enqueue count: \(enqueues)"
        }
        let model = AppModel(client: PcrdClient(socketURL: socket, tokenURL: token), windowID: UUID())
        defer { model.stopWindowObservation() }
        model.workspaces = [try workspace()]
        model.selectedWorkspaceID = "continuity"
        model.activeRun = try run("d", conversation: "conversation-a")
        model.selectedConversationID = "conversation-a"
        model.systemCapabilities = try JSONDecoder().decode(SystemCapabilities.self, from: Data("""
        {"actions":["followups.enqueue","followups.replace"],"supportsFollowUpEnqueueReconciliation":false}
        """.utf8))
        for _ in 0..<2 {
            _ = await model.refreshDaemonFollowUpQueue(workspaceID: "continuity", runID: "a",
                machineID: model.currentMachineContextID, migrateLocalQueue: false)
        }
        let serverResult = await server.value
        #expect(serverResult == "one original enqueue")
        #expect(model.followUpQueue.first?.id.uuidString.lowercased() == "4fba1ec5-0781-4a0d-bf72-47f611573a0b")
        #expect(model.followUpQueue.first?.attempt == 0)
        #expect(model.followUpQueue.first?.isPaused == true)
    }

    @Test func emptyServerQueueDoesNotCombineUnacknowledgedMessagesFromDifferentConversations() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        let a = QueuedFollowUp(prompt: "Pending A")
        let b = QueuedFollowUp(prompt: "Pending B")
        for (runID, item) in [("a", a), ("b", b)] {
            let saved = PersistedFollowUpQueue(machineID: model.currentMachineContextID,
                workspaceID: "continuity", runID: runID, items: [item])
            model.persistedFollowUpQueues[saved.contextKey] = saved
        }
        model.applyDaemonFollowUpQueue(nil, workspaceID: "continuity", machineID: model.currentMachineContextID, revision: 1)
        #expect(model.followUpQueue == [a])
        #expect(model.persistedFollowUpQueues[model.machineRunContextKey(workspaceID: "continuity", runID: "b")]?.items == [b])
    }

    @Test func legacyQueueWithoutConversationIDRequiresAKnownRun() {
        #expect(queue(conversation: nil).belongsToConversation(ownerIDs: ["conversation-a"], runIDs: ["a"]))
        #expect(!queue(runID: "unknown", conversation: nil).belongsToConversation(ownerIDs: ["conversation-a"], runIDs: ["a"]))
    }

    @Test func repeatedCachePersistenceDoesNotDecodeItsOwnUnchangedHistory() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-reuse-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = WorkspaceRunDiskCache(directory: directory)
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        var snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: String(repeating: "Original history. ", count: 20_000))],
            historyLoaded: true)
        for index in 0..<12 {
            snapshot.conversationTitle = "Update \(index)"
            #expect(cache.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        }
        #expect(cache.diskDecodeCount == 0)
        #expect(cache.record(for: key)?.snapshot == snapshot)
        #expect(WorkspaceRunDiskCache(directory: directory).record(for: key)?.snapshot == snapshot)
    }

    @Test func navigatingAnUnchangedCacheDoesNotInvalidateDecodedBytes() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-navigation-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = WorkspaceRunDiskCache(directory: directory)
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: String(repeating: "Original history. ", count: 20_000))],
            historyLoaded: true)
        #expect(cache.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        for _ in 0..<12 {
            #expect(cache.record(for: key)?.snapshot == snapshot)
        }
        #expect(cache.diskDecodeCount == 0)
        let peer = WorkspaceRunDiskCache(directory: directory)
        var changed = snapshot
        changed.conversationTitle = "Published by another cache owner"
        #expect(peer.put(.init(key: key, runSummary: nil, snapshot: changed)))
        #expect(cache.record(for: key)?.snapshot == changed)
        #expect(cache.diskDecodeCount == 1)
    }

    @Test func coldHydrationDoesNotWaitForAnotherConversationToEncode() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-lanes-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "readable", runID: "a")
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Complete original history")], historyLoaded: true)
        #expect(WorkspaceRunDiskCache(directory: directory).put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        let entered = DispatchSemaphore(value: 0), gate = DispatchSemaphore(value: 0)
        let disk = WorkspaceRunDiskCache(directory: directory, encodeRecord: { record in
            entered.signal()
            #expect(awaitNavigationFixtureRelease(gate))
            return try WorkspaceRunDiskCache.encode(record)
        })
        let writer = Task.detached {
            await ClientBlockingIO.cacheWrite {
                disk.put(.init(key: .init(machineID: "mac", workspaceID: "writing", runID: "b"),
                    runSummary: nil, snapshot: snapshot))
            }
        }
        defer { gate.signal() }
        #expect(awaitNavigationFixtureRelease(entered))
        let started = ContinuousClock.now
        let restored = await WorkspaceRunViewStateStore(diskCache: disk).hydrate(for: key)
        let elapsed = started.duration(to: .now)
        #expect(restored?.snapshot == snapshot)
        #expect(elapsed < .seconds(1))
        print("Cold hydration while another cache encode is held: \(elapsed)")
        gate.signal()
        #expect(await writer.value)
    }

    @Test func cacheEncodeRebasesAnAtomicReplacementBeforePublishing() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-rebase-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let original = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Original", id: "original")], historyLoaded: true)
        let peer = WorkspaceRunDiskCache(directory: directory)
        #expect(peer.put(.init(key: key, runSummary: nil, snapshot: original)))
        var peerSnapshot = original
        peerSnapshot.conversationHistory.append(.init(category: "output", text: "Peer answer", id: "peer"))
        peerSnapshot.conversationHistory.append(.init(category: "user", text: "Later question", id: "question"))
        let peerRecord = WorkspaceRunViewCacheRecord(key: key, runSummary: nil, snapshot: peerSnapshot)
        var pending = original
        pending.conversationTitle = "Local selection"
        let pendingRecord = WorkspaceRunViewCacheRecord(key: key, runSummary: nil, snapshot: pending)
        let entered = DispatchSemaphore(value: 0), gate = DispatchSemaphore(value: 0)
        let disk = WorkspaceRunDiskCache(directory: directory, encodeRecord: { record in
            if !record.snapshot.conversationHistory.contains(where: { $0.id == "peer" }) {
                entered.signal()
                #expect(awaitNavigationFixtureRelease(gate))
            }
            return try WorkspaceRunDiskCache.encode(record)
        })
        let writer = Task.detached { disk.put(pendingRecord) }
        defer { gate.signal() }
        #expect(awaitNavigationFixtureRelease(entered))
        #expect(peer.put(peerRecord))
        gate.signal()
        #expect(await writer.value)
        let restored = try #require(WorkspaceRunDiskCache(directory: directory).record(for: key))
        #expect(restored.snapshot.conversationHistory == peerSnapshot.conversationHistory)
        #expect(restored.snapshot.conversationTitle == "Local selection")
    }

    @Test func readingDuringEncodingDoesNotMakeTheWriterEncodeAgain() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-touch-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let original = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Original")], historyLoaded: true)
        #expect(WorkspaceRunDiskCache(directory: directory).put(.init(key: key, runSummary: nil, snapshot: original)))
        let entered = DispatchSemaphore(value: 0), gate = DispatchSemaphore(value: 0)
        let calls = DispatchSemaphore(value: 0)
        let disk = WorkspaceRunDiskCache(directory: directory, encodeRecord: { record in
            calls.signal()
            entered.signal()
            #expect(awaitNavigationFixtureRelease(gate))
            return try WorkspaceRunDiskCache.encode(record)
        })
        var changed = original
        changed.conversationTitle = "New title"
        let record = WorkspaceRunViewCacheRecord(key: key, runSummary: nil, snapshot: changed)
        let writer = Task.detached { disk.put(record) }
        defer { gate.signal() }
        #expect(awaitNavigationFixtureRelease(entered))
        for _ in 0..<10 { #expect(disk.record(for: key)?.snapshot == original) }
        gate.signal()
        // A spare permit prevents a regressed re-encode from hanging the test.
        gate.signal()
        #expect(await writer.value)
        #expect(consumeAvailableSignals(calls) == 1)
        #expect(WorkspaceRunDiskCache(directory: directory).record(for: key)?.snapshot == changed)
    }

    @Test func cacheRemovalDuringEncodingCannotResurrectRemovedRecords() async throws {
        for removal in ["workspace", "machine", "all"] {
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-remove-\(UUID())")
            defer { try? FileManager.default.removeItem(at: directory) }
            let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
            let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:], conversationHistory: [])
            let record = WorkspaceRunViewCacheRecord(key: key, runSummary: nil, snapshot: snapshot)
            let entered = DispatchSemaphore(value: 0), gate = DispatchSemaphore(value: 0)
            let disk = WorkspaceRunDiskCache(directory: directory, encodeRecord: { record in
                entered.signal()
                #expect(awaitNavigationFixtureRelease(gate))
                return try WorkspaceRunDiskCache.encode(record)
            })
            let writer = Task.detached { disk.put(record) }
            defer { gate.signal() }
            #expect(awaitNavigationFixtureRelease(entered))
            switch removal {
            case "workspace": disk.removeWorkspace(machineID: "mac", workspaceID: "continuity")
            case "machine": disk.removeMachine("mac")
            default: disk.removeAll()
            }
            gate.signal()
            #expect(await writer.value == false)
            #expect(disk.record(for: key) == nil)
        }
    }

    @Test func failedCacheEncodingPreservesTheLastDurableConversation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-failure-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Must survive")], historyLoaded: true)
        let peer = WorkspaceRunDiskCache(directory: directory)
        #expect(peer.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        let disk = WorkspaceRunDiskCache(directory: directory, encodeRecord: { _ in throw CocoaError(.fileWriteUnknown) })
        var changed = snapshot
        changed.conversationTitle = "Not durable"
        #expect(!disk.put(.init(key: key, runSummary: nil, snapshot: changed)))
        #expect(disk.record(for: key)?.snapshot == snapshot)
    }

    @Test func workspaceDiscoveryDoesNotDecodeUnrelatedConversationBodies() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-discovery-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let writer = WorkspaceRunDiskCache(directory: directory)
        let history = (0..<10_000).map {
            ResumeHistoryEntry(category: "output", text: "Original answer \($0). " + String(repeating: "history ", count: 20),
                id: "history-\($0)")
        }
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: history, historyLoaded: true)
        for workspace in 0..<64 {
            let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "workspace-\(workspace)", runID: "a")
            #expect(writer.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        }
        let reader = WorkspaceRunDiskCache(directory: directory)
        let began = ContinuousClock.now
        let records = reader.records(machineID: "mac", workspaceID: "workspace-0")
        let elapsed = began.duration(to: .now)
        print("Cold workspace cache discovery across 64 records: \(elapsed), decoded bodies=\(reader.diskDecodeCount)")
        #expect(records.count == 1)
        #expect(records.first?.snapshot == snapshot)
        #expect(reader.diskDecodeCount == 1)
        #expect(reader.diskIdentityDecodeCount == 0)
        #expect(elapsed < .seconds(1))
        let index = directory.appendingPathComponent("runs.pcrcacheindex")
        let indexRevision = try FileManager.default.attributesOfItem(atPath: index.path)
        #expect(reader.records(machineID: "other-machine", workspaceID: "workspace-0").isEmpty)
        #expect(reader.records(machineID: "mac", workspaceID: "workspace-1").first?.snapshot == snapshot)
        #expect(reader.diskDecodeCount == 2)
        #expect(reader.diskIdentityDecodeCount == 0)
        let afterReads = try FileManager.default.attributesOfItem(atPath: index.path)
        #expect(indexRevision[.systemFileNumber] as? NSNumber == afterReads[.systemFileNumber] as? NSNumber)
        #expect(indexRevision[.modificationDate] as? Date == afterReads[.modificationDate] as? Date)
        try FileManager.default.removeItem(at: directory.appendingPathComponent("runs.pcrcacheindex"))
        let legacy = WorkspaceRunDiskCache(directory: directory)
        let legacyBegan = ContinuousClock.now
        #expect(legacy.records(machineID: "mac", workspaceID: "workspace-0").first?.snapshot == snapshot)
        print("Legacy discovery without catalog: \(legacyBegan.duration(to: .now)), identity parses=\(legacy.diskIdentityDecodeCount)")
        #expect(legacy.diskIdentityDecodeCount == 64)
        let repaired = WorkspaceRunDiskCache(directory: directory)
        #expect(repaired.records(machineID: "mac", workspaceID: "workspace-1").first?.snapshot == snapshot)
        #expect(repaired.diskIdentityDecodeCount == 0)
    }

    @Test func cacheCatalogRepairsCorruptionAndAnInterruptedOrExternalWrite() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-catalog-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = WorkspaceRunDiskCache(directory: directory)
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        var snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Original history")], historyLoaded: true)
        #expect(cache.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        let index = directory.appendingPathComponent("runs.pcrcacheindex")
        let oldIndex = try Data(contentsOf: index)
        let peer = WorkspaceRunDiskCache(directory: directory)
        snapshot.conversationHistory.append(.init(category: "output", text: "New original answer"))
        #expect(peer.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        // Simulate termination after record publication but before index publication.
        try oldIndex.write(to: index, options: .atomic)
        let cold = WorkspaceRunDiskCache(directory: directory)
        #expect(cold.records(machineID: "mac", workspaceID: "continuity").first?.snapshot == snapshot)
        #expect(cold.diskIdentityDecodeCount == 1)
        #expect(cache.records(machineID: "mac", workspaceID: "continuity").first?.snapshot == snapshot)
        try Data("broken".utf8).write(to: index)
        let damaged = WorkspaceRunDiskCache(directory: directory)
        #expect(damaged.records(machineID: "mac", workspaceID: "continuity").first?.snapshot == snapshot)
        #expect(damaged.diskIdentityDecodeCount == 1)
        #expect(damaged.records(machineID: "different", workspaceID: "continuity").isEmpty)
        damaged.removeWorkspace(machineID: "mac", workspaceID: "continuity")
        #expect(WorkspaceRunDiskCache(directory: directory).records(machineID: "mac", workspaceID: "continuity").isEmpty)
        #expect(damaged.put(.init(key: key, runSummary: nil, snapshot: snapshot)))
        damaged.removeAll()
        #expect(!FileManager.default.fileExists(atPath: index.path))
        #expect(WorkspaceRunDiskCache(directory: directory).records(machineID: "mac", workspaceID: "continuity").isEmpty)
    }

    @Test func reusedCacheRecordsObserveExternalReplacementCorruptionAndRemoval() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-replacement-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = WorkspaceRunDiskCache(directory: directory)
        let second = WorkspaceRunDiskCache(directory: directory)
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let original = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Original history")], historyLoaded: true)
        #expect(first.put(.init(key: key, runSummary: nil, snapshot: original)))
        var changed = original
        changed.conversationHistory.append(.init(category: "output", text: "Another writer's new answer"))
        #expect(second.put(.init(key: key, runSummary: nil, snapshot: changed)))
        #expect(first.records(machineID: "mac", workspaceID: "continuity").first?.snapshot == changed)
        #expect(first.record(for: key)?.snapshot == changed)
        let file = try #require(FileManager.default.contentsOfDirectory(at: directory,
            includingPropertiesForKeys: nil).first { $0.pathExtension == "pcrconversation" })
        let data = try Data(contentsOf: file)
        let timestamp = try #require(FileManager.default.attributesOfItem(atPath: file.path)[.modificationDate] as? Date)
        // Same size and mtime must not disguise an atomic replacement.
        try Data(repeating: 0, count: data.count).write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.modificationDate: timestamp], ofItemAtPath: file.path)
        #expect(first.record(for: key) == nil)
        #expect(first.put(.init(key: key, runSummary: nil, snapshot: original)))
        second.removeAll()
        #expect(first.record(for: key) == nil)
    }

    @Test func diskHydrationDoesNotReplaceNewerMemoryOrDropAnUnwrittenTurn() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-cache-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let disk = WorkspaceRunDiskCache(directory: directory)
        let store = WorkspaceRunViewStateStore(diskCache: disk)
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let old = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:], conversationHistory: [], conversationTitle: "Before")
        var current = old
        current.conversationTitle = "Already updated in memory"
        current.selectedAgent = 2
        disk.put(WorkspaceRunViewCacheRecord(key: key, runSummary: nil, snapshot: old))
        store.put(current, for: key)
        let nextKey = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "next-a")
        store.put(current, for: nextKey)
        let records = await store.hydrateWorkspace(machineID: "mac", workspaceID: "continuity")
        #expect(records.first { $0.key == key }?.snapshot == current)
        #expect(records.contains { $0.key == nextKey })
        #expect(store.snapshot(for: key) == current)
        #expect(store.flushPendingWrites())
    }

    @Test func evictionDuringDebouncedPersistenceStillRestoresTheLastConfirmedContent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-evicted-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = WorkspaceRunViewStateStore(capacity: 1, diskCache: WorkspaceRunDiskCache(directory: directory))
        let key = WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "a")
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [:], conversationHistory: [], conversationTitle: "Still readable")
        store.put(snapshot, for: key)
        store.put(snapshot, for: WorkspaceRunViewKey(machineID: "mac", workspaceID: "continuity", runID: "b"))
        #expect(store.snapshot(for: key) == nil)
        #expect(await store.hydrate(for: key)?.snapshot == snapshot)
        #expect(await store.hydrateWorkspace(machineID: "mac", workspaceID: "continuity").count == 2)
        #expect(store.flushPendingWrites())
    }
}
