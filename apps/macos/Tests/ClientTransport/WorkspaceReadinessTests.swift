import AppKit
import Darwin
import Foundation
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceReadinessTests {
    @Test(arguments: [false, true], [false, true])
    func saveReceiptSurvivesWorkspaceSwitchAndLostReply(lostReply: Bool, returnBeforeReply: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        let finalized = try savedRun(original)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        model.transcripts = [1: AgentTranscript(sourceRunID: key.runID, sourceAgent: 1,
            cursor: 6, hasMore: false, finalMessage: "Original answer", finalMessageHasMore: false,
            finalMessageReadStatus: "success")]
        model.resumeHistory = [.init(category: "user", text: "Earlier question", id: "earlier")]
        // Isolate receipt merging from the fixture's different canonical history.
        fixture.server.holdHistory()
        fixture.server.holdFinalization(reply: finalized, lostReply: lostReply)
        let save = Task { await model.acceptSelectedAgent(dispatchQueuedFollowUp: false) }
        defer { save.cancel() }
        try await waitUntil { fixture.server.count("turns.finalize") == 1 }
        // Use the production navigation path, not only a different selected ID.
        await model.selectWorkspace(fixture.keys[1].workspaceID)
        model.prompt = "Other workspace draft"
        if returnBeforeReply {
            await model.selectWorkspace(key.workspaceID)
            model.prompt = "New original-workspace draft"
            await model.selectAgent(2, manageFollowUpQueue: false)
            #expect(model.selectedAgent == 2)
        }
        fixture.server.releaseFinalization()
        #expect(await save.value)
        #expect(model.selectedWorkspaceID == (returnBeforeReply ? key.workspaceID : fixture.keys[1].workspaceID))
        #expect((model.activeRun?.runId == key.runID) == returnBeforeReply)
        #expect(model.prompt == (returnBeforeReply ? "New original-workspace draft" : "Other workspace draft"))
        if returnBeforeReply {
            #expect(model.activeRun?.finalizedAgent == 1)
            #expect(model.selectedAgent == 2)
        }
        #expect(model.runOperationIssues.isEmpty)
        #expect(model.runSummariesByContext[key]?.finalizedAgent == 1)
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let restored = await cold.hydrate(for: key)
        #expect(restored?.runSummary?.syncedBack == true)
        #expect(restored?.runSummary?.finalizedAgent == 1)
        #expect(restored?.snapshot.transcripts[1]?.finalMessage == "Original answer")
        #expect(restored?.snapshot.conversationHistory.contains { $0.id == "earlier" } == true)
        await model.selectWorkspace(key.workspaceID)
        #expect(model.activeRun?.finalizedAgent == 1)
        #expect(!model.isRunOperationInProgress)
        #expect(fixture.server.count("turns.finalize") == 1)
    }

    @Test func lostSaveReplyAndUnavailableReadContinueOriginalVerification() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        fixture.server.holdFinalization(reply: try savedRun(original), lostReply: true)
        let save = Task { await model.acceptSelectedAgent(dispatchQueuedFollowUp: false) }
        defer { save.cancel() }
        try await waitUntil { fixture.server.count("turns.finalize") == 1 }
        await model.selectWorkspace(fixture.keys[1].workspaceID)
        fixture.server.failRun = key.runID
        fixture.server.releaseFinalization()
        #expect(!(await save.value))
        #expect(model.runPollingTasks[key] != nil)
        #expect(!model.runPollingIsComplete(key))
        #expect(!model.isRunOperationInProgress)
        fixture.server.failRun = nil
        try await waitUntil { model.runSummariesByContext[key]?.finalizedAgent == 1 }
        #expect(model.runOperationIssues.isEmpty)
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).record(for: key)?.runSummary?.syncedBack == true)
        #expect(model.selectedWorkspaceID == fixture.keys[1].workspaceID)
        #expect(fixture.server.count("turns.finalize") == 1)
    }

    @Test func resumeSourceChangeCannotApplyToAnotherWorkspaceAfterSaving() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        fixture.server.holdFinalization(reply: try savedRun(original))
        let change = Task { await model.updateResumeSession("chosen-for-original-workspace") }
        defer { change.cancel() }
        try await waitUntil { fixture.server.count("turns.finalize") == 1 }
        await model.selectWorkspace(fixture.keys[1].workspaceID)
        model.settings.resumeSessionID = "other-workspace-session"
        fixture.server.releaseFinalization()
        await change.value
        #expect(model.settings.resumeSessionID == "other-workspace-session")
        #expect(model.selectedWorkspaceID == fixture.keys[1].workspaceID)
        #expect(model.runSummariesByContext[key]?.finalizedAgent == 1)
    }

    @Test func returningDuringBackgroundSaveReadPublishesItsCompletedState() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        let pending = try savedRun(original, pending: true)
        let answer = AgentTranscript(sourceRunID: key.runID, sourceAgent: 1,
            cursor: 6, hasMore: false, finalMessage: "Retained answer",
            finalMessageHasMore: false, finalMessageReadStatus: "success")
        model.runSummariesByContext[key] = pending
        model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [1: answer],
            conversationHistory: []), runSummary: pending, for: key)
        model.selectedWorkspaceID = fixture.keys[1].workspaceID
        model.activeRun = try fixture.server.run(fixture.keys[1].runID)
        fixture.server.installRun(try savedRun(original))
        fixture.server.holdRun(key.runID)
        fixture.server.holdAgent(runID: key.runID, agent: 1)
        model.beginPolling(key.runID, workspaceID: key.workspaceID, target: .local)
        try await waitUntil { fixture.server.heldRunReads > 0 }
        // Simulate the cache-backed first paint after returning to A. Its next
        // foreground read is unavailable; the earlier background reply owns completion.
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = pending
        model.selectedAgent = 1
        model.transcripts = [1: answer]
        model.prompt = "Retained return draft"
        fixture.server.failRun = key.runID
        fixture.server.releaseRun()
        try await waitUntil { model.runSummariesByContext[key]?.finalizedAgent == 1 }
        #expect(model.activeRun?.finalizedAgent == 1)
        #expect(model.activeRun?.syncedBack == true)
        #expect(model.prompt == "Retained return draft")
        #expect(model.transcripts[1]?.finalMessage == "Retained answer")
        #expect(fixture.server.count("turns.finalize") == 0)
    }

    @Test(arguments: [false, true])
    func pendingSaveKeepsObservingOriginalRunAfterNavigation(switchAway: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        let pending = try savedRun(original, pending: true)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        fixture.server.holdFinalization(reply: pending)
        let save = Task { await model.acceptSelectedAgent(dispatchQueuedFollowUp: false) }
        defer { save.cancel() }
        try await waitUntil { fixture.server.count("turns.finalize") == 1 }
        if switchAway {
            await model.selectWorkspace(fixture.keys[1].workspaceID)
            await model.selectWorkspace(key.workspaceID)
        }
        #expect(!(await model.acceptSelectedAgent(dispatchQueuedFollowUp: false)))
        if switchAway { await model.selectWorkspace(fixture.keys[1].workspaceID) }
        fixture.server.releaseFinalization()
        #expect(!(await save.value)) // Accepted for processing is not saved yet.
        #expect(model.errorMessage == nil)
        #expect(model.runSummariesByContext[key]?.normalizedStatus == "finalizing")
        fixture.server.installRun(try savedRun(original))
        try await waitUntil { model.runSummariesByContext[key]?.finalizedAgent == 1 }
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).record(for: key)?.runSummary?.syncedBack == true)
        #expect(model.selectedWorkspaceID == (switchAway ? fixture.keys[1].workspaceID : key.workspaceID))
        #expect(fixture.server.count("turns.finalize") == 1)
    }

    @Test func lateSaveReceiptCannotRewindChildOrItsSettings() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        fixture.server.holdFinalization(reply: try savedRun(original))
        let save = Task { await model.acceptSelectedAgent(dispatchQueuedFollowUp: false) }
        defer { save.cancel() }
        try await waitUntil { fixture.server.count("turns.finalize") == 1 }
        var body = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        body["runId"] = "child-0"
        body["continuedFromRunId"] = key.runID
        body["continuedFromAgent"] = 1
        body["turnIndex"] = 1
        body["prompt"] = "Child question"
        let child = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
        fixture.server.installRun(child)
        let started = try JSONDecoder().decode(RunStartResponse.self, from: JSONSerialization.data(withJSONObject: [
            "status": "started", "runId": child.runId, "run": body]))
        #expect(try await model.activateStartedRun(started, expectedWorkspaceID: key.workspaceID,
            target: .local, carriedHistory: [.init(category: "output", text: "Parent history")]))
        model.settings.resumeSessionID = "child-session"
        model.prompt = "Unsent child draft"
        fixture.server.releaseFinalization()
        #expect(await save.value)
        #expect(model.activeRun?.runId == child.runId)
        #expect(model.selectedWorkspace?.lastRunId == child.runId)
        #expect(model.settings.resumeSessionID == "child-session")
        #expect(model.prompt == "Unsent child draft")
        #expect(model.runSummariesByContext[key]?.finalizedAgent == 1)
        // A late pre-save summary must not resurrect the Save action in cache.
        _ = try model.recordRunObservation(original, for: key)
        #expect(model.runSummariesByContext[key]?.finalizedAgent == 1)
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).record(for: key)?.runSummary?.syncedBack == true)
        #expect(fixture.server.count("turns.finalize") == 1)
    }

    private func savedRun(_ original: RunSummary, pending: Bool = false) throws -> RunSummary {
        var body = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        body["status"] = pending ? "finalizing" : "finalized"
        body[pending ? "finalizingAgent" : "finalizedAgent"] = 1
        body["syncedBack"] = !pending
        body["workerActive"] = pending
        body["updatedAt"] = "2026-10-25T01:00:00Z"
        body["eventCount"] = original.eventCount + (pending ? 1 : 2)
        if !pending { body["sessionPromotion"] = ["sessionId": "fixture-native-session"] }
        return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
    }

    @Test func completedSaveReleasesMutationGateBeforeHistoryAndHealthReads() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let original = try fixture.server.run(key.runID)
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = original
        model.selectedAgent = 1
        model.settings.syncBack = true
        model.daemonOnline = true
        model.prompt = "Unsent draft stays here"
        var body = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        body["status"] = "finalized"
        body["finalizedAgent"] = 1
        body["syncedBack"] = true
        body["sessionPromotion"] = ["sessionId": "fixture-native-session"]
        let finalized = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
        fixture.server.installRun(finalized)
        fixture.server.holdHistory()
        var returned = false
        let save = Task {
            let accepted = await model.acceptSelectedAgent(dispatchQueuedFollowUp: false)
            #expect(accepted)
            returned = true
        }
        defer { save.cancel() }
        try await waitUntil { fixture.server.historyRequestCount > 0 }
        #expect(model.activeRun?.finalizedAgent == 1)
        #expect(!model.isRunOperationInProgress)
        #expect(model.currentRunOperationKind == nil)
        #expect(returned)
        #expect(model.promptSubmissionPlan != .enqueue)
        #expect(model.prompt == "Unsent draft stays here")
        #expect(fixture.server.count("turns.finalize") == 1)
        // A new accepted child may open while both old read paths are pending.
        body["runId"] = "child-0"
        body["prompt"] = "Accepted child question"
        body["continuedFromRunId"] = original.runId
        body["continuedFromAgent"] = 1
        body["turnIndex"] = 1
        body["updatedAt"] = "2026-10-23T11:00:00Z"
        let child = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
        fixture.server.installRun(child)
        let response = try JSONDecoder().decode(RunStartResponse.self, from: JSONSerialization.data(withJSONObject: [
            "status": "started", "runId": child.runId, "run": body]))
        let carried: [ResumeHistoryEntry] = [.init(category: "user", text: original.prompt),
            .init(category: "output", text: "Parent answer")]
        #expect(try await model.activateStartedRun(response, expectedWorkspaceID: key.workspaceID,
            target: .local, carriedHistory: carried))
        model.prompt = "New draft for child"
        fixture.server.release()
        await save.value
        for task in Array(model.conversationHistoryTasks.values) { _ = await task.value }
        #expect(model.activeRun?.runId == child.runId)
        #expect(model.resumeHistory.contains { $0.text == "Parent answer" })
        #expect(model.prompt == "New draft for child")
        #expect(fixture.server.count("turns.finalize") == 1)
    }

    @Test(arguments: [false, true])
    func switchingAwayFromPartialPageCannotReplaceCompleteDiskHistory(flushBeforeReturn: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0], run = try fixture.server.run(key.runID)
        let history: [ResumeHistoryEntry] = [
            .init(category: "user", text: "First question", id: "first-question"),
            .init(category: "output", text: "First answer", id: "first-answer"),
            .init(category: "user", text: "Second question", id: "second-question"),
            .init(category: "output", text: "Second answer", id: "second-answer")]
        let saved = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [1:
            AgentTranscript(sourceRunID: key.runID, sourceAgent: 1, cursor: 6, hasMore: false,
                finalMessage: "Current answer", finalMessageHasMore: false, finalMessageReadStatus: "success")],
            conversationHistory: history, historyLoaded: true)
        let disk = WorkspaceRunDiskCache(directory: fixture.cacheDirectory)
        #expect(disk.put(.init(key: key, runSummary: run, snapshot: saved)))
        // A current answer can arrive before its independent history/cache reader.
        model.selectedWorkspaceID = key.workspaceID
        model.activeRun = run
        model.selectedAgent = 1
        model.transcripts = saved.transcripts
        model.resumeHistory = []
        fixture.server.holdHistory()
        await model.selectWorkspace(fixture.keys[1].workspaceID)
        if flushBeforeReturn { #expect(model.runViewStateStore.flushPendingWrites(timeout: 3)) }
        await model.selectWorkspace(key.workspaceID)
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        #expect(model.activeRun?.runId == key.runID)
        #expect(model.resumeHistory == history)
        #expect(model.transcripts[1]?.finalMessage == "Current answer")
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        #expect(disk.record(for: key)?.snapshot.conversationHistory == history)
        let surface = NativeSurface(model: model)
        defer { surface.close() }
        surface.update()
        #expect(surface.texts.contains { $0.contains("First answer") })
        #expect(fixture.server.commandCount == 0)
    }

    @Test(arguments: [false, true])
    func delayedDiskHistoryOnlyFillsCoverageAndKeepsCurrentSelectionAndAnswer(workspaceHydration: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0], run = try fixture.server.run(key.runID)
        let disk = WorkspaceRunDiskCache(directory: fixture.cacheDirectory)
        let store = WorkspaceRunViewStateStore(diskCache: disk)
        let history: [ResumeHistoryEntry] = [.init(category: "user", text: "Earlier question", id: "question"),
            .init(category: "output", text: "Earlier answer", id: "answer")]
        let old = WorkspaceRunViewCacheRecord(key: key, runSummary: run, snapshot: .init(selectedAgent: 1,
            transcripts: [1: AgentTranscript(sourceRunID: key.runID, sourceAgent: 1, cursor: 6,
                finalMessage: "Old answer", finalMessageCursor: 6, finalMessageHasMore: false,
                finalMessageID: "same-answer", finalMessageReadStatus: "running")],
            conversationHistory: history, historyLoaded: true))
        #expect(disk.put(old))
        let entered = DispatchGroup(), gate = DispatchSemaphore(value: 0)
        entered.enter()
        let blocker = Task { await ClientBlockingIO.cache { entered.leave(); gate.wait() } }
        defer { gate.signal() }
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async { entered.wait(); continuation.resume() }
        }
        let hydration = Task {
            if workspaceHydration {
                return await store.hydrateWorkspace(machineID: key.machineID, workspaceID: key.workspaceID)
                    .first { $0.key == key }
            }
            return await store.hydrate(for: key)
        }
        await Task.yield()
        var body = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        body["status"] = "finalized"
        body["finalizedAgent"] = 2
        let finalized = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
        let newest = AgentTranscript(sourceRunID: key.runID, sourceAgent: 1, cursor: 20,
            finalMessage: "Newly completed answer", finalMessageCursor: 20, finalMessageHasMore: false,
            finalMessageID: "same-answer", finalMessageReadStatus: "success")
        store.put(.init(selectedAgent: 2, transcripts: [1: newest], conversationHistory: []),
            runSummary: finalized, for: key)
        gate.signal()
        await blocker.value
        let restored = try #require(await hydration.value)
        #expect(restored.snapshot.conversationHistory == history)
        #expect(restored.snapshot.transcripts[1] == newest)
        #expect(restored.snapshot.selectedAgent == 2)
        #expect(restored.runSummary?.finalizedAgent == 2)
        #expect(store.flushPendingWrites(timeout: 3))
        // A stale already-created write must not overwrite a newer committed page.
        #expect(disk.put(old))
        let cold = try #require(await WorkspaceRunViewStateStore(diskCache: disk).hydrate(for: key))
        #expect(cold.snapshot.conversationHistory == history)
        #expect(cold.snapshot.transcripts[1] == newest)
        #expect(cold.snapshot.selectedAgent == 2)
        #expect(cold.runSummary?.finalizedAgent == 2)
    }

    @Test(arguments: [false, true])
    func freshProcessCanReadPersistedConversationsBeforeHealthReturns(hasDirectory: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0], run = try fixture.server.run(key.runID)
        let state = fixture.model.desktopStateStore
        state.saveConversationNavigation(selectedWorkspaceByMachine: [key.machineID: key.workspaceID],
            selectedConversationByWorkspace: [MachineContextKey.workspace(machineID: key.machineID,
                workspaceID: key.workspaceID): run.conversationKey])
        if hasDirectory {
            fixture.model.persistWorkspaceDirectory(fixture.destinations, machineID: key.machineID)
        }
        #expect(state.flushPendingWrites(timeout: 3))
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).put(.init(key: key,
            runSummary: run, snapshot: .init(selectedAgent: 8, transcripts: [8:
                AgentTranscript(sourceRunID: key.runID, sourceAgent: 8, cursor: 6,
                    finalMessage: "Persisted latest answer", finalMessageHasMore: false,
                    finalMessageReadStatus: "success")],
                conversationHistory: [.init(category: "output", text: "Persisted earlier answer")], historyLoaded: true))))
        let cold = AppModel(client: fixture.model.client, windowID: UUID(), desktopStateStore: state,
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { cold.stopWindowObservation() }
        // Only the machine identity is available; neither directory nor active run is injected.
        cold.machines = fixture.model.machines
        cold.selectedMachineID = key.machineID
        cold.nextMachineDirectoryRefresh = .distantFuture
        #expect(cold.workspaces.isEmpty && cold.activeRun == nil)
        let refresh = Task { await cold.refresh() }
        defer { refresh.cancel() }
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        #expect(cold.selectedWorkspaceID == key.workspaceID)
        #expect(cold.activeRun?.runId == key.runID)
        #expect(cold.workspaceDirectoryIsCached && !cold.workspaceDirectoryLoaded)
        #expect(!cold.daemonOnline && cold.selectedWorkspace?.writable == false)
        #expect(cold.transcripts[8]?.finalMessage == "Persisted latest answer")
        #expect(cold.resumeHistory.first?.text == "Persisted earlier answer")
        #expect(cold.selectedTranscriptBlocks.filter { $0.text == run.prompt }.count == 1)
        let surface = NativeSurface(model: cold)
        defer { surface.close() }
        try await waitUntil {
            surface.update()
            return surface.texts.contains { $0.contains("Persisted earlier answer") }
        }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func coldHistoryReaderMustHydrateTheCompleteDiskPageBeforeWriting() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0], run = try fixture.server.run(key.runID)
        let saved = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8:
            AgentTranscript(sourceRunID: key.runID, sourceAgent: 8, cursor: 6, hasMore: false,
                finalMessage: "Complete answer saved before restart", finalMessageHasMore: false,
                finalMessageReadStatus: "success")],
            conversationHistory: [.init(category: "output", text: "Complete history saved before restart")],
            historyLoaded: true)
        let disk = WorkspaceRunDiskCache(directory: fixture.cacheDirectory)
        #expect(disk.put(.init(key: key, runSummary: run, snapshot: saved)))
        fixture.model.scheduleConversationHistoryRead(key: key, run: run, target: .local)
        _ = await fixture.model.conversationHistoryTasks[key]?.value
        #expect(fixture.model.runViewStateStore.snapshot(for: key) == saved)
        #expect(fixture.server.historyRequestCount == 0)
        #expect(fixture.model.runViewStateStore.flushPendingWrites(timeout: 3))
        #expect(disk.record(for: key)?.snapshot == saved)
    }

    @Test(arguments: [false, true])
    func workspaceEntryRestoresTheCurrentDiskTurnInsteadOfAWarmOlderConversation(sameConversation: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let current = try fixture.server.run(key.runID)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(current)) as? [String: Any])
        json["runId"] = "older-0"
        json["conversationId"] = sameConversation ? current.conversationKey : "older-conversation"
        json["updatedAt"] = "2026-10-18T10:00:00Z"
        let older = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "An unrelated older conversation")], historyLoaded: true),
            runSummary: older, for: .init(machineID: key.machineID, workspaceID: key.workspaceID, runID: older.runId))
        let saved = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8:
            AgentTranscript(sourceRunID: key.runID, sourceAgent: 8, cursor: 6, hasMore: false,
                finalMessage: "Current cached answer", finalMessageHasMore: false, finalMessageReadStatus: "success")],
            conversationHistory: [.init(category: "output", text: "Current cached history")], historyLoaded: true)
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).put(.init(key: key, runSummary: current, snapshot: saved)))
        model.selectedAgentsByContext[model.machineRunContextKey(workspaceID: key.workspaceID,
            runID: key.runID)] = 8
        await model.selectWorkspace(key.workspaceID)
        #expect(model.activeRun?.runId != older.runId)
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        #expect(model.activeRun?.runId == current.runId)
        #expect(model.transcripts[8]?.finalMessage == "Current cached answer")
        #expect(model.resumeHistory == saved.conversationHistory)
        #expect(model.promptSubmissionPlan.isAvailable)
        let surface = NativeSurface(model: model)
        defer { surface.close() }
        surface.update()
        try await waitUntil {
            surface.update()
            return surface.texts.contains { $0.contains("Current cached answer") }
                && surface.texts.contains { $0.contains("Current cached history") }
        }
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        #expect(fixture.server.healthReadsHeld > 0)
        if let path = ProcessInfo.processInfo.environment["PCR_FIRST_PAINT_EVIDENCE"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path))
        }
        #expect(fixture.server.commandCount == 0)
    }

    @Test(arguments: [false, true])
    func coldDestinationBodyReadsDoNotWaitForGlobalHealth(knownSummary: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        if knownSummary { model.runSummariesByContext[key] = try fixture.server.run(key.runID) }
        await model.selectWorkspace(key.workspaceID)
        try await waitUntil {
            fixture.server.healthReadsHeld > 0
                && model.transcripts[model.selectedAgent ?? 1]?.finalMessageHasMore == false
                && model.resumeHistory.first?.text == "Saved history \(key.runID)"
        }
        #expect(model.activeRun?.runId == key.runID)
        #expect(model.transcripts[model.selectedAgent ?? 1]?.finalMessage
            == fixture.server.answer(key.runID, model.selectedAgent ?? 1))
        #expect(fixture.server.commandCount == 0)
    }

    @Test func workspaceBootstrapCanBindItsExactTaskWithoutAnIndex() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        model.selectedWorkspaceID = key.workspaceID
        await model.loadSelectedWorkspaceContext()
        #expect(model.activeRun?.runId == key.runID)
        #expect(model.selectedAgent != nil)
        #expect(!model.transcripts.isEmpty)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func coldIncompleteHistoryEnrichmentPreservesTheSavedAnswer() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0], run = try fixture.server.run(key.runID)
        let answer = AgentTranscript(sourceRunID: key.runID, sourceAgent: 8, cursor: 6, hasMore: false,
            finalMessage: "Retain this answer while history loads", finalMessageHasMore: false,
            finalMessageReadStatus: "success")
        #expect(WorkspaceRunDiskCache(directory: fixture.cacheDirectory).put(.init(key: key, runSummary: run,
            snapshot: .init(selectedAgent: 8, transcripts: [8: answer], conversationHistory: [], historyLoaded: false))))
        fixture.model.scheduleConversationHistoryRead(key: key, run: run, target: .local)
        _ = await fixture.model.conversationHistoryTasks[key]?.value
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8] == answer)
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.historyLoaded == true)
        #expect(fixture.server.historyRequestCount == 1)
    }

    @Test func workspaceBootstrapRejectsAPrefetchedTurnAfterTheIndexAdvances() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0], workspace = fixture.destinations[0]
        let older = try fixture.server.run(key.runID)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(older)) as? [String: Any])
        json["runId"] = "newer-0"
        json["turnIndex"] = 1
        let newer = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "History \(older.runId)")], historyLoaded: true),
            runSummary: older, for: key)
        model.selectedWorkspaceID = key.workspaceID
        model.selectedConversationID = older.conversationKey
        model.workspaceConversations = workspaceConversationSummaries(from: [older])
        model.activeRun = older
        fixture.server.holdRun(older.runId)
        let bootstrap = Task { await model.loadSelectedWorkspaceContext() }
        try await waitUntil { fixture.server.heldRunReads > 0 && !model.conversationIndexLoading }
        model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "History \(newer.runId)")], historyLoaded: true),
            runSummary: newer, for: .init(machineID: key.machineID, workspaceID: key.workspaceID, runID: newer.runId))
        model.applyWorkspaceConversationIndex([newer], resumeSessions: [], workspace: workspace, machineID: key.machineID)
        #expect(model.activeRun?.runId == newer.runId)
        fixture.server.releaseRun()
        await bootstrap.value
        #expect(model.activeRun?.runId == newer.runId)
        #expect(model.resumeHistory.first?.text == "History \(newer.runId)")
        #expect(!model.isViewingEarlierConversationTurn)
    }

    @Test func coldArchivesWaitForTheUnvisitedLiveBodyWorkingSet() async throws {
        let fixture = try Fixture(workspaceCount: 2, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let archive = fixture.keys[0], live = fixture.keys[1]
        fixture.server.finishRun(archive.runID)
        fixture.model.workspaces = [try Fixture.workspace(0, running: false), try Fixture.workspace(1, running: true)]
        fixture.server.holdAgent(runID: live.runID, agent: 8)
        fixture.model.scheduleConversationPreload(machineID: "local", target: .local, workspaces: fixture.model.workspaces)
        try await waitUntil { fixture.server.heldAgentReads > 0
            && fixture.model.runViewStateStore.snapshot(for: live)?.transcripts[7]?.conversation?.hasMore == false }
        #expect(fixture.model.conversationReadTasks[archive] == nil)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.model.runViewStateStore.snapshot(for: live)?.transcripts[8]?.conversation?.hasMore == false }
        fixture.model.scheduleConversationPreload(machineID: "local", target: .local, workspaces: fixture.model.workspaces)
        try await waitUntil { fixture.ready(archive) }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func oldWorkHeavyCachesCannotDelayTheFirstIndependentConversationCommit() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        let run = try fixture.server.run(key.runID)
        let work = (0..<3000).map { TranscriptBlock(id: "old-tool-\($0)", kind: .command, text: "Historical tool \($0)") }
        let legacy: [Int: AgentTranscript] = Dictionary(uniqueKeysWithValues: (1...8).map { (agent: Int) in
            (agent, AgentTranscript(sourceRunID: key.runID, sourceAgent: agent, blocks: work, cursor: 5))
        })
        fixture.model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: legacy, conversationHistory: [],
            historyLoaded: true), runSummary: run, for: key)
        fixture.server.holdWorkBackfill = true
        let start = ContinuousClock.now
        fixture.start()
        try await waitUntil(timeout: .seconds(2)) { (1...8).allSatisfy {
            fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[$0]?.conversation?.hasMore == false
        } }
        #expect(start.duration(to: .now) < .seconds(2))
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.blocks == work)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func historicalCacheChurnCannotEvictActiveRunBodiesOrTheirCursors() throws {
        let fixture = try Fixture(workspaceCount: 2, running: true)
        defer { fixture.close() }
        let store = WorkspaceRunViewStateStore(capacity: 1)
        for key in fixture.keys {
            store.put(.init(selectedAgent: 1, transcripts: [1: .init(sourceRunID: key.runID, sourceAgent: 1,
                cursor: 500, hasMore: true, finalMessage: "Ready \(key.runID)")], conversationHistory: []),
                runSummary: try fixture.server.run(key.runID), for: key)
        }
        for index in 0..<24 {
            let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "old-\(index)", runID: "old-\(index)")
            store.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: []), for: key)
        }
        for key in fixture.keys {
            #expect(store.snapshot(for: key)?.transcripts[1]?.cursor == 500)
            #expect(store.snapshot(for: key)?.transcripts[1]?.finalMessage == "Ready \(key.runID)")
        }
        fixture.server.finish()
        store.retainReadyRuns(Set(fixture.keys))
        for key in fixture.keys {
            store.put(store.snapshot(for: key)!, runSummary: try fixture.server.run(key.runID), for: key)
        }
        #expect(fixture.keys.allSatisfy { store.snapshot(for: $0)?.transcripts[1]?.cursor == 500 })
        store.retainReadyRuns([])
        #expect(store.count == 1)
    }

    @Test func interleavedConversationAndWorkPagesMatchTheCompleteSourceProjection() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let run = try fixture.server.run("run-0")
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 1)
        let events: [[String: Any]] = (0..<4096).map { index in
            let output = [10, 1000, 2000, 4095].contains(index)
            return ["id": index == 4095 ? "event-10" : "event-\(index)", "event_id": index,
                "kind": output ? "output" : "command",
                "text": index == 4095 ? "Completed version of the first message" : output ? "Repeated real output" : "Tool \(index)",
                "activity_type": output ? "assistant_progress" : "command"]
        }
        func page(_ items: [[String: Any]], cursor: Int, next: Int, hasMore: Bool,
                  requested: AgentTranscript, conversationOnly: Bool) throws -> ConversationAgentRead {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let detail = try decoder.decode(RunAgentDetail.self, from: JSONSerialization.data(withJSONObject: [
                "run_id": run.runId, "agent": 1, "name": "Agent 1", "status": "running",
                "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
                "stdout_tail": "", "stderr_tail": "", "final_message": "",
                "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": next,
                    "has_more": hasMore, "total": events.count, "items": items]]))
            return ConversationAgentRead(agent: 1, requested: requested, status: "running",
                detail: detail, mode: conversationOnly ? .conversation : .work)
        }
        let empty = try page([], cursor: 0, next: 7, hasMore: true, requested: initial, conversationOnly: true)
        var dual = try #require(await ConversationReadProjection.merge(empty, into: initial, run: run, history: []))
        #expect(dual.input.transcript.conversation?.cursor == 7)
        #expect(dual.input.transcript.cursor == 0)
        let body = try page(events.filter { $0["kind"] as? String == "output" }, cursor: 7,
            next: events.count, hasMore: false, requested: dual.input.transcript, conversationOnly: true)
        dual = try #require(await ConversationReadProjection.merge(body, into: dual.input.transcript, run: run, history: []))
        let ready = dual.blocks.filter { $0.kind == .output }
        #expect(ready.contains { $0.text == "Completed version of the first message" })
        // A full-work request started before the answer must merge into, not replace, it.
        for cursor in stride(from: 0, to: events.count, by: 256) {
            let end = min(cursor + 256, events.count)
            let work = try page(Array(events[cursor..<end]), cursor: cursor, next: end,
                hasMore: end < events.count, requested: initial, conversationOnly: false)
            dual = try #require(await ConversationReadProjection.merge(work, into: dual.input.transcript, run: run, history: []))
            #expect(dual.blocks.filter { $0.kind == .output } == ready)
            let duplicate = try #require(await ConversationReadProjection.merge(body, into: dual.input.transcript, run: run, history: []))
            #expect(duplicate.input == dual.input)
            #expect(duplicate.blocks == dual.blocks)
        }
        let whole = try page(events, cursor: 0, next: events.count, hasMore: false,
            requested: initial, conversationOnly: false)
        let reference = try #require(await ConversationReadProjection.merge(whole, into: initial, run: run, history: []))
        #expect(dual.blocks == reference.blocks)
        #expect(dual.input.transcript.eventIDs == reference.input.transcript.eventIDs)
        #expect(dual.input.transcript.cursor == reference.input.transcript.cursor)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func recentNativeCommandsAreVisibleWhileLongHistoryBackfillRemainsBlocked() async throws {
        let fixture = try Fixture(workspaceCount: 2, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        fixture.model.appLanguage = .english
        fixture.server.simulateLongWorkBacklog()
        fixture.server.holdWorkBackfill = true
        fixture.start()
        try await waitUntil {
            fixture.keys.allSatisfy { key in
                (1...8).allSatisfy { agent in
                    fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[agent]?.recentWork?.cursor == 240_000
                }
            }
        }
        for key in fixture.keys {
            let snapshot = try #require(fixture.model.runViewStateStore.snapshot(for: key))
            for agent in 1...8 {
                let transcript = try #require(snapshot.transcripts[agent])
                #expect(transcript.cursor == 0 && transcript.hasMore)
                #expect(transcript.conversation?.cursor == 240_000)
                let blocks = TranscriptFormatter.blocks(history: [], prompt: "", transcript: transcript)
                #expect(blocks.map(\.text) == ["Checking recent work", "printf fixture", "Recent result is ready"])
                #expect(blocks.filter { $0.operation != nil }.count == 1)
                #expect(blocks.first { $0.operation != nil }?.operation?.status == .completed)
            }
        }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 8
        fixture.model.transcripts = try #require(fixture.model.runViewStateStore.snapshot(for: key)).transcripts
        let surface = NativeSurface(model: fixture.model)
        defer { surface.close() }
        surface.update()
        try await waitUntil {
            surface.updateLayout()
            return surface.texts.contains { $0.contains("Ran commands") }
                && surface.texts.contains { $0.contains("Recent result is ready") }
        }
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        if let path = ProcessInfo.processInfo.environment["PCR_RECENT_WORK_EVIDENCE_DIR"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path).appendingPathComponent("recent-work-native.png"))
        }
        let summaryIDs = fixture.model.selectedTimelineBlocks.filter {
            $0.activityType?.hasPrefix("work_summary") == true
        }.map(\.id)
        #expect(fixture.server.heldAgentReads > 0)
        #expect(fixture.server.commandCount == 0)
        fixture.server.releaseAgent()
        try await waitUntil {
            fixture.keys.allSatisfy { key in
                (1...8).allSatisfy { fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[$0]?.cursor == 240_000 }
            }
        }
        let transcript = try #require(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8])
        #expect(TranscriptFormatter.blocks(history: [], prompt: "", transcript: transcript).filter { $0.operation != nil }.count == 1)
        surface.update()
        #expect(fixture.model.selectedTimelineBlocks.filter {
            $0.activityType?.hasPrefix("work_summary") == true
        }.map(\.id) == summaryIDs)
        #expect(surface.texts.contains { $0.contains("Recent result is ready") })
        #expect(fixture.server.commandCount == 0)
    }

    @Test func recentRangesYieldAfterTwoEmptyWorkPagesWithoutFakingCoverageOrBlockingReplies() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        fixture.server.simulateLongWorkBacklog(agentCount: 1, nonWorkTail: true)
        fixture.server.holdWorkBackfill = true
        fixture.start()
        let key = fixture.keys[0]
        let start = 240_000 - AgentRecentWork.initialEventWindow
        try await waitUntil {
            (fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[1]?.recentWork?.cursor ?? 0) >= start + 2
        }
        try await Task.sleep(for: .milliseconds(100))
        let transcript = try #require(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[1])
        #expect(transcript.recentWork?.cursor == start + 2)
        #expect(transcript.recentWork?.hasMore == true && transcript.recentWork?.items.isEmpty == true)
        #expect(transcript.cursor == 0 && transcript.hasMore)
        #expect(transcript.conversation?.cursor == 240_000 && transcript.conversation?.items.count == 2)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func conversationEOFContinuesProbingWhileWorkBackfillRemainsBlocked() async throws {
        let fixture = try Fixture(workspaceCount: 2, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        fixture.server.holdWorkBackfill = true
        fixture.start()
        try await waitUntil {
            fixture.keys.allSatisfy { fixture.model.runViewStateStore.snapshot(for: $0)?.transcripts[8]?.conversation?.cursor == 6 }
        }
        fixture.server.advanceWork()
        for key in fixture.keys { fixture.model.runSummariesByContext[key] = try fixture.server.run(key.runID) }
        try await waitUntil {
            fixture.keys.allSatisfy { fixture.model.runViewStateStore.snapshot(for: $0)?.transcripts[8]?.conversation?.cursor == 7 }
        }
        for key in fixture.keys {
            let transcript = try #require(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8])
            #expect(transcript.cursor == 0 && transcript.hasMore)
            #expect(transcript.conversation?.items.last?.block.text == fixture.server.workText(key.runID, 8, cursor: 6))
        }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func everyWorkspacesConversationIsReadableBeforeSlowWorkBackfill() async throws {
        let fixture = try Fixture(workspaceCount: 3, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        fixture.server.holdWorkBackfill = true
        fixture.start()
        var maximum = Duration.zero
        for key in fixture.keys {
            try await waitUntil(timeout: .seconds(2)) {
                guard let snapshot = fixture.model.runViewStateStore.snapshot(for: key) else { return false }
                return (1...8).allSatisfy { agent in
                    guard let transcript = snapshot.transcripts[agent] else { return false }
                    return TranscriptFormatter.blocks(history: [], prompt: "", transcript: transcript).contains {
                        $0.text == fixture.server.workText(key.runID, agent, cursor: 5)
                    }
                }
            }
            await fixture.model.selectWorkspace(key.workspaceID)
            let surface = NativeSurface(model: fixture.model)
            defer { surface.close() }
            // AppKit's first TextKit/font initialization is not a warm navigation.
            surface.update()
            try await waitUntil {
                surface.updateLayout()
                return surface.texts.contains { $0.contains(fixture.server.workText(key.runID,
                    fixture.model.selectedAgent ?? 1, cursor: 5)) }
            }
            for agent in [8, 2, 7, 3, 6, 4, 5, 1] {
                let start = ContinuousClock.now
                await fixture.model.selectAgent(agent, manageFollowUpQueue: false)
                surface.update()
                try await waitUntil {
                    surface.updateLayout()
                    return surface.texts.contains { $0.contains(fixture.server.workText(key.runID, agent, cursor: 5)) }
                }
                let elapsed = start.duration(to: .now)
                maximum = max(maximum, elapsed)
                #expect(elapsed < .milliseconds(100))
                #expect(fixture.model.transcripts[agent]?.cursor == 0)
                #expect(fixture.model.transcripts[agent]?.hasMore == true)
            }
            #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
            if let directory = ProcessInfo.processInfo.environment["PCR_CROSS_WORKSPACE_EVIDENCE_DIR"] {
                try surface.framePNG().write(to: URL(fileURLWithPath: directory).appendingPathComponent("\(key.runID).png"))
            }
        }
        print("Cross-workspace native body: 3 workspaces, 24 branches, full work history blocked; maximum=\(maximum)")
        #expect(fixture.server.heldAgentReads > 0)
        #expect(fixture.server.commandCount == 0)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0, terminal: false) } }
        for key in fixture.keys {
            let snapshot = try #require(fixture.model.runViewStateStore.snapshot(for: key))
            for agent in 1...8 {
                let blocks = TranscriptFormatter.blocks(history: [], prompt: "", transcript: snapshot.transcripts[agent]!)
                #expect(blocks.filter { $0.text == fixture.server.workText(key.runID, agent, cursor: 5) }.count == 1)
            }
        }
    }

    @Test func independentConversationCursorSurvivesRestartAndRejectsLatePages() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        let run = try fixture.server.run(key.runID)
        let initial = AgentTranscript(sourceRunID: key.runID, sourceAgent: 8)
        let first = await ConversationReadProjection.agentPage(client: fixture.model.client, target: .local,
            run: run, agent: 8, transcript: initial, mode: .conversation)
        let prepared = try #require(await ConversationReadProjection.merge(first, into: initial, run: run, history: []))
        #expect(prepared.input.transcript.cursor == 0)
        #expect(prepared.input.transcript.conversation?.cursor == 6)
        fixture.model.runViewStateStore.put(.init(selectedAgent: 8, transcripts: [8: prepared.input.transcript],
            conversationHistory: [], historyLoaded: true), runSummary: run, for: key)
        try #require(fixture.model.runViewStateStore.flushPendingWrites(timeout: 3))
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let restored = try #require(await cold.hydrate(for: key)?.snapshot.transcripts[8])
        #expect(restored == prepared.input.transcript)
        fixture.server.advanceWork()
        let second = await ConversationReadProjection.agentPage(client: fixture.model.client, target: .local,
            run: run, agent: 8, transcript: restored, mode: .conversation)
        let newer = try #require(await ConversationReadProjection.merge(second, into: restored, run: run, history: []))
        let late = try #require(await ConversationReadProjection.merge(first, into: newer.input.transcript, run: run, history: []))
        #expect(late.input.transcript.conversation?.cursor == 7)
        #expect(late.blocks.filter { $0.text == fixture.server.workText(key.runID, 8, cursor: 6) }.count == 1)
        let oldVisible = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8: restored], conversationHistory: [])
        let retained = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8: late.input.transcript],
            conversationHistory: []).mergingVisible(oldVisible, for: key)
        #expect(retained.transcripts[8]?.conversation?.cursor == 7)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func aNewLatestRunCannotCancelAnActiveOrStillDrainingReader() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.server.holdAgent(runID: key.runID, agent: 8)
        fixture.start()
        try await waitUntil { fixture.server.heldAgentReads > 0 }
        let token = fixture.model.conversationReadTokens[key]
        let polling = Task<Void, Never> { try? await Task.sleep(for: .seconds(10)) }
        fixture.model.runPollingTasks[key] = polling
        fixture.model.workspaces = [try Fixture.workspace(0, running: false, lastRunID: "replacement")]
        fixture.model.reconcileRunPollingContexts(machineID: key.machineID, target: .local,
            workspaces: fixture.model.workspaces)
        #expect(!polling.isCancelled)
        #expect(fixture.model.conversationReadTokens[key] == token)
        fixture.server.finish()
        fixture.model.runSummariesByContext[key] = try fixture.server.run(key.runID)
        fixture.model.reconcileRunPollingContexts(machineID: key.machineID, target: .local,
            workspaces: fixture.model.workspaces)
        #expect(!polling.isCancelled)
        #expect(fixture.model.conversationReadTokens[key] == token)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.ready(key) }
    }

    @Test func selectedRunningWorkspacePreparesEveryLiveBranchBeforeAnyClick() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.model.updatePrompt("Do not alter this draft")
        fixture.start()
        try await waitUntil(timeout: .seconds(1)) { fixture.ready(key, terminal: false) }
        for agent in 1...8 {
            #expect(fixture.model.transcripts[agent]?.blocks.contains {
                $0.text == fixture.server.workText(key.runID, agent, cursor: 5)
            } == true)
            #expect(fixture.model.transcripts[agent]?.finalMessage.isEmpty == true)
        }
        #expect(fixture.model.prompt == "Do not alter this draft")
        #expect(fixture.model.selectedAgent == 1)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func slowVisibleBranchDoesNotSerializeAllOtherRunningAgents() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.server.holdAgent(runID: key.runID, agent: 2)
        try await fixture.model.loadRun(key.runID, loadDetailPages: 1)
        try await waitUntil { fixture.server.heldAgentReads > 0 }
        try await waitUntil(timeout: .seconds(1)) {
            fixture.model.transcripts[8]?.blocks.contains {
                $0.text == fixture.server.workText(key.runID, 8, cursor: 5)
            } == true
        }
        #expect(fixture.model.transcripts[2] == nil)
        #expect(fixture.model.selectedAgent == 1)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.ready(key, terminal: false) }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func slowMetadataReadsCannotOccupyEveryLiveOutputSlot() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.runSummariesByContext[key] = try fixture.server.run(key.runID)
        fixture.model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
            conversationHistory: []), runSummary: try fixture.server.run(key.runID), for: key)
        let reads = (0..<4).map { _ in Task {
            let _: HealthResponse? = try? await fixture.model.client.call("system.health",
                body: EmptyBody(), priority: .utility)
        } }
        defer { reads.forEach { $0.cancel() } }
        try await waitUntil { fixture.server.healthReadsHeld >= 2 }
        fixture.start()
        try await waitUntil(timeout: .seconds(1)) { fixture.ready(key, requireHistory: false, terminal: false) }
        #expect(fixture.server.healthReadsHeld >= 2)
        #expect(fixture.server.maximumConcurrentReads <= 6)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func runningNativeBranchesStayPreparedAsOutputAdvancesWithoutSelectingThem() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        fixture.server.advanceWork()
        fixture.model.activeRun = try fixture.server.run(key.runID)
        try await waitUntil {
            (1...8).allSatisfy { fixture.model.transcripts[$0]?.cursor == 7 }
        }
        #expect(fixture.model.selectedAgent == 1)
        let readsBefore = fixture.server.agentRequestCount
        let surface = NativeSurface(model: fixture.model)
        defer { surface.close() }
        surface.update()
        try await waitUntil {
            surface.updateLayout()
            return surface.texts.contains { $0.contains(fixture.server.workText(key.runID, 1, cursor: 6)) }
        }
        var maximum = Duration.zero
        let first = try surface.framePNG()
        for _ in 0..<3 {
            for agent in [8, 3, 6, 2, 7, 4, 5, 1] {
                let started = ContinuousClock.now
                await fixture.model.selectAgent(agent, manageFollowUpQueue: false)
                surface.update()
                try await waitUntil {
                    surface.updateLayout()
                    return surface.texts.contains { $0.contains(fixture.server.workText(key.runID, agent, cursor: 6)) }
                }
                let elapsed = started.duration(to: .now)
                maximum = max(maximum, elapsed)
                #expect(elapsed < .milliseconds(100), "Running native body appearance: \(elapsed)")
                #expect(fixture.model.transcripts[agent]?.finalMessage.isEmpty == true)
            }
        }
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        surface.update()
        let last = try surface.framePNG()
        #expect(first != last)
        #expect(fixture.server.agentRequestCount == readsBefore)
        #expect(fixture.server.commandCount == 0)
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        if let path = ProcessInfo.processInfo.environment["PCR_RUNNING_READINESS_EVIDENCE_DIR"] {
            try last.write(to: URL(fileURLWithPath: path).appendingPathComponent("native-live-agent-8.png"))
        }
        print("Running native reading: 24 switches, 8 branches, no saved final artifact; maximum=\(maximum)")
    }

    @Test func activeWorkspaceReaderSurvivesLeavingAndKeepsNewOutputOffscreen() async throws {
        let fixture = try Fixture(workspaceCount: 2, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.server.holdAgent(runID: key.runID, agent: 2)
        fixture.start()
        try await waitUntil { fixture.server.heldAgentReads > 0 && fixture.ready(fixture.keys[1], terminal: false) }
        let token = fixture.model.conversationReadTokens[key]
        await fixture.model.selectWorkspace(fixture.keys[1].workspaceID)
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        #expect(fixture.model.conversationReadTokens[key] == token)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.ready(key, terminal: false) }
        fixture.server.advanceWork()
        fixture.model.runSummariesByContext[key] = try fixture.server.run(key.runID)
        try await waitUntil {
            fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[2]?.cursor == 7
        }
        #expect(fixture.model.selectedWorkspaceID == fixture.keys[1].workspaceID)
        #expect(fixture.model.transcripts[8]?.sourceRunID == fixture.keys[1].runID)
        await fixture.model.selectWorkspace(key.workspaceID)
        await fixture.model.selectAgent(2, manageFollowUpQueue: false)
        #expect(fixture.model.selectedTranscriptBlocks.contains {
            $0.text == fixture.server.workText(key.runID, 2, cursor: 6)
        })
        #expect(fixture.server.commandCount == 0)
    }

    @Test func laterSynthesisBranchesJoinTheExistingReaderWithoutANavigationEvent() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        let token = fixture.model.conversationReadTokens[key]
        fixture.server.addSynthesis()
        fixture.model.activeRun = try fixture.server.run(key.runID)
        try await waitUntil {
            (9...11).allSatisfy { fixture.model.transcripts[$0]?.cursor == 6 }
        }
        #expect(fixture.model.conversationReadTokens[key] == token)
        #expect(fixture.model.selectedAgent == 1)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func aPartialVisiblePageCannotEraseAlreadyPreparedBranchesOrMakeTheirClicksFetch() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        let cached = try #require(fixture.model.runViewStateStore.snapshot(for: key))
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 2
        fixture.model.transcripts = [2: try #require(cached.transcripts[2])]
        fixture.model.resumeHistory = []
        fixture.model.cacheVisibleRunViewState()
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts.count == 8)
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.conversationHistory == cached.conversationHistory)
        let reads = fixture.server.agentRequestCount
        let started = ContinuousClock.now
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        #expect(started.duration(to: .now) < .milliseconds(100))
        #expect(fixture.model.selectedTranscriptBlocks.contains {
            $0.text.contains(fixture.server.workText(key.runID, 8, cursor: 5))
        })
        #expect(fixture.server.agentRequestCount == reads)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func liveEOFReadersKeepFollowingOutputWhenMetadataIsLate() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true, liveWorkOnly: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        let metadataReads = fixture.server.count("turns.get")
        let held = (0..<4).map { _ in Task {
            let _: HealthResponse? = try? await fixture.model.client.call("system.health",
                body: EmptyBody(), priority: .utility)
        } }
        defer { held.forEach { $0.cancel() } }
        try await waitUntil { fixture.server.healthReadsHeld >= 2 }
        fixture.server.advanceWork()
        try await waitUntil(timeout: .seconds(5)) {
            (1...8).allSatisfy { fixture.model.transcripts[$0]?.cursor == 7 }
        }
        #expect(fixture.model.activeRun?.eventCount == 6)
        #expect(fixture.server.count("turns.get") == metadataReads)
        #expect(fixture.model.transcripts[8]?.blocks.contains {
            $0.text == fixture.server.workText(key.runID, 8, cursor: 6)
        } == true)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func everyWorkspaceAndAllEightAgentsDrainWithoutOpeningThem() async throws {
        let fixture = try Fixture(workspaceCount: 5)
        defer { fixture.close() }
        fixture.model.updatePrompt("Keep this unsent draft")
        fixture.start()
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0) } }
        #expect(fixture.model.selectedWorkspaceID == "reader")
        #expect(fixture.model.activeRun == nil)
        #expect(fixture.model.prompt == "Keep this unsent draft")
        #expect(fixture.server.commandCount == 0, "Unexpected actions: \(fixture.server.unexpectedActions)")
        #expect(fixture.server.maximumConcurrentReads <= 6)
        for key in fixture.keys {
            let snapshot = try #require(fixture.model.runViewStateStore.snapshot(for: key))
            #expect(snapshot.transcripts.count == 8)
            for agent in 1...8 {
                #expect(snapshot.transcripts[agent]?.finalMessage == fixture.server.answer(key.runID, agent))
                #expect(snapshot.transcripts[agent]?.cursor == 6)
                #expect(snapshot.transcripts[agent]?.finalMessageReadStatus == "success")
            }
        }
    }

    @Test func warmSwitchesRenderBeforeBlockedDiskAndHealthReads() async throws {
        let fixture = try Fixture(workspaceCount: 5)
        defer { fixture.close() }
        fixture.start()
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0) } }
        let refresh = Task { () -> Void in
            let _: HealthResponse? = try? await fixture.model.client.call("system.health", body: EmptyBody())
        }
        defer { refresh.cancel() }
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        let diskGate = DispatchGroup()
        let diskEntered = DispatchGroup()
        diskGate.enter()
        diskEntered.enter()
        let blockedDisk = Task {
            await ClientBlockingIO.cache { diskEntered.leave(); diskGate.wait() }
        }
        defer { diskGate.leave() }
        try await waitUntil { diskEntered.wait(timeout: .now()) == .success }
        var latencies: [Double] = []
        let requestsBefore = fixture.server.agentRequestCount
        for pass in 0..<10 {
            for workspace in fixture.destinations {
                let start = ContinuousClock.now
                await fixture.model.selectWorkspace(workspace.workspaceId)
                #expect(fixture.model.selectedWorkspaceID == workspace.workspaceId)
                #expect(fixture.model.activeRun?.runId == workspace.lastRunId)
                #expect(!fixture.model.isSelectedWorkspaceContextLoading)
                for agent in (pass.isMultiple(of: 2) ? Array(1...8) : Array((1...8).reversed())) {
                    await fixture.model.selectAgent(agent, manageFollowUpQueue: false)
                    #expect(fixture.model.selectedTranscriptBlocks.contains {
                        $0.isFinalAnswer && $0.text == fixture.server.answer(workspace.lastRunId, agent)
                    })
                    #expect(!fixture.model.isSelectedTimelineLoading)
                }
                let elapsed = start.duration(to: .now)
                let milliseconds = Double(elapsed.components.seconds) * 1000
                    + Double(elapsed.components.attoseconds) / 1e15
                latencies.append(milliseconds)
                #expect(elapsed < .milliseconds(100), "Warm workspace plus eight rendered branches: \(elapsed)")
            }
        }
        #expect(fixture.server.agentRequestCount == requestsBefore)
        #expect(fixture.server.commandCount == 0, "Unexpected actions: \(fixture.server.unexpectedActions)")
        #expect(fixture.server.healthReadsHeld > 0)
        let sorted = latencies.sorted()
        print("Warm reading: 50 workspace switches, 400 complete answer switches; p95=\(sorted[47])ms, max=\(sorted.last ?? 0)ms; disk and health held")
        // The owning scope releases the cache gate before teardown.
        blockedDisk.cancel()
    }

    @Test func aHeldAgentCannotBlockOtherBranchesAndNavigationDoesNotCancelItsReader() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        fixture.server.holdAgent(runID: "run-0", agent: 7)
        fixture.start()
        try await waitUntil { fixture.ready(fixture.keys[1]) && fixture.server.heldAgentReads > 0 }
        try await waitUntil {
            fixture.model.runViewStateStore.snapshot(for: fixture.keys[0])?.transcripts[8]?.finalMessageHasMore == false
        }
        await fixture.model.selectWorkspace("workspace-1")
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.ready(fixture.keys[0]) }
        #expect(fixture.model.selectedWorkspaceID == "workspace-1")
        #expect(fixture.model.selectedAgent == 8)
        #expect(fixture.model.transcripts[8]?.sourceRunID == "run-1")
        await fixture.model.selectWorkspace("workspace-0")
        await fixture.model.selectAgent(7, manageFollowUpQueue: false)
        #expect(fixture.model.transcripts[7]?.finalMessage == fixture.server.answer("run-0", 7))
        #expect(fixture.server.commandCount == 0, "Unexpected actions: \(fixture.server.unexpectedActions)")
    }

    @Test func reentryAfterMemoryEvictionHydratesTheExactRunBeforeSlowHealthOrIndex() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        fixture.start()
        let key = fixture.keys[0]
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0) } }
        let health = Task { () -> Void in
            let _: HealthResponse? = try? await fixture.model.client.call("system.health", body: EmptyBody())
        }
        defer { health.cancel() }
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        #expect(fixture.model.runViewStateStore.flushPendingWrites(timeout: 3))
        for index in 0..<16 {
            fixture.model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: []),
                for: .init(machineID: key.machineID, workspaceID: "eviction-\(index)", runID: "unused"))
        }
        #expect(fixture.model.runViewStateStore.cachedRecords(machineID: key.machineID,
            workspaceID: key.workspaceID).isEmpty)
        await fixture.model.selectWorkspace(key.workspaceID)
        #expect(fixture.model.activeRun?.runId == key.runID)
        #expect(fixture.model.promptDestinationIsResolved)
        fixture.model.cacheVisibleRunViewState()
        let deadline = ContinuousClock.now + .seconds(1)
        while fixture.model.transcripts[8]?.finalMessage != fixture.server.answer(key.runID, 8),
              ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        #expect(fixture.model.transcripts[8]?.finalMessage == fixture.server.answer(key.runID, 8))
        #expect(fixture.model.promptDestinationIsResolved)
        let surface = NativeSurface(model: fixture.model)
        defer { surface.close() }
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        let body = fixture.model.makeRunContinueBody(runID: try #require(fixture.model.activeRun?.runId),
            agent: try #require(fixture.model.selectedAgent), prompt: "The next distinct question",
            settings: fixture.model.settings, confirmLargeRun: false)
        #expect(body.runId == key.runID && body.agent == 8)
        surface.update()
        #expect(surface.texts.contains(fixture.server.answer(key.runID, 8)))
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        #expect(fixture.server.healthReadsHeld > 0)
        if let path = ProcessInfo.processInfo.environment["PCR_REENTRY_EVIDENCE_DIR"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path).appendingPathComponent("native-reentry.png"))
        }
        #expect(fixture.server.commandCount == 0, "Unexpected actions: \(fixture.server.unexpectedActions)")
    }

    @Test func lateSummaryCannotRollLatestNavigationBackOrEraseItsCachedHistory() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0], workspace = fixture.destinations[0]
        let older = try fixture.server.run(key.runID)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(older)) as? [String: Any])
        json["runId"] = "newer-0"
        json["turnIndex"] = 1
        json["updatedAt"] = "2026-10-18T10:00:00Z"
        let recent = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        fixture.server.installRun(recent)
        fixture.server.holdRun(older.runId)
        model.selectedWorkspaceID = key.workspaceID
        model.selectedConversationID = AppModel.newConversationSelectionID
        model.workspaceConversations = workspaceConversationSummaries(from: [older])
        let navigation = Task { await model.selectConversation(older.conversationKey) }
        try await waitUntil { fixture.server.heldRunReads == 1 }
        let history = [ResumeHistoryEntry(category: "output", text: "Full latest-turn history")]
        model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [1:
            AgentTranscript(sourceRunID: recent.runId, sourceAgent: 1, cursor: 6,
                hasMore: false, finalMessage: fixture.server.answer(recent.runId, 1),
                finalMessageHasMore: false, finalMessageReadStatus: "success")],
            conversationHistory: history, historyLoaded: true), runSummary: recent,
            for: .init(machineID: key.machineID, workspaceID: key.workspaceID, runID: recent.runId))
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace, machineID: key.machineID)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(model.promptDestinationIsResolved)
        #expect(!model.isViewingEarlierConversationTurn)
        fixture.server.releaseRun()
        await navigation.value
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(model.conversationNavigationTarget == nil)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.promptOutboxByWorkspace.isEmpty)
        #expect(!fixture.server.unexpectedActions.contains { $0.contains("continue") || $0.contains("start") || $0.contains("accept") })
    }

    @Test func lateBackgroundLatestRefreshCannotRollBackAnAdvancedIndex() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0], workspace = fixture.destinations[0]
        let older = try fixture.server.run(key.runID)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(older)) as? [String: Any])
        json["runId"] = "newer-0"
        json["turnIndex"] = 1
        json["updatedAt"] = "2026-10-18T10:00:00Z"
        let recent = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        fixture.server.installRun(recent)
        fixture.server.holdRun(older.runId, afterReads: 1)
        model.selectedWorkspaceID = key.workspaceID
        model.selectedConversationID = older.conversationKey
        model.workspaceConversations = workspaceConversationSummaries(from: [older])
        let history = [ResumeHistoryEntry(category: "output", text: "Retain cached latest history")]
        for run in [older, recent] {
            model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
                conversationHistory: history, historyLoaded: true), runSummary: run,
                for: .init(machineID: key.machineID, workspaceID: key.workspaceID, runID: run.runId))
        }
        let refresh = Task { await model.refreshLatestWorkspaceConversation() }
        try await waitUntil { fixture.server.heldRunReads == 1 }
        #expect(model.conversationNavigationTarget == nil)
        #expect(model.activeRun?.runId == older.runId)
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace, machineID: key.machineID)
        #expect(model.activeRun?.runId == recent.runId)
        fixture.server.releaseRun()
        await refresh.value
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func navigationFinishesWithoutWaitingForColdHistoryAndLaterPagesKeepTheDestination() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model, key = fixture.keys[0]
        let run = try fixture.server.run(key.runID)
        fixture.server.holdHistory(after: 1)
        fixture.server.historyPages = 3
        model.selectedWorkspaceID = key.workspaceID
        model.selectedConversationID = AppModel.newConversationSelectionID
        model.workspaceConversations = workspaceConversationSummaries(from: [run])
        await model.selectConversation(run.conversationKey)
        #expect(model.conversationNavigationTarget == nil)
        #expect(model.activeRun?.runId == run.runId)
        #expect(!model.isViewingEarlierConversationTurn)
        try await waitUntil { fixture.server.historyRequestCount == 2 }
        #expect(model.resumeHistory.count == 1)
        fixture.server.releaseHistory()
        try await waitUntil { model.resumeHistory.count == 3 }
        #expect(model.activeRun?.runId == run.runId)
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func slowHistoryCannotHoldRepliesOrPretendToBeFullyLoaded() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        fixture.server.holdHistory()
        fixture.start()
        let key = fixture.keys[0]
        try await waitUntil { fixture.ready(key, requireHistory: false) }
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.historyLoaded != true)
        await fixture.model.selectWorkspace(key.workspaceID)
        await fixture.model.selectAgent(8, manageFollowUpQueue: false)
        #expect(fixture.model.selectedTranscriptBlocks.contains {
            $0.isFinalAnswer && $0.text == fixture.server.answer(key.runID, 8)
        })
        #expect(fixture.model.isSelectedTimelineLoading, "An absent history anchor must not be restored as if loading had finished.")
        fixture.server.releaseHistory()
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.model.resumeHistory.contains { $0.text == "Saved history run-0" })
    }

    @Test func aWarmCompleteHistoryIsRestoredWithoutReadingItAgain() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0], run = try fixture.server.run(fixture.keys[0].runID)
        let history = [ResumeHistoryEntry(category: "output", text: "A complete cached history")]
        fixture.model.runViewStateStore.put(.init(selectedAgent: 1, transcripts: [:],
            conversationHistory: history, historyLoaded: true), runSummary: run, for: key)
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = run
        await fixture.model.loadRunHistory(key.runID, reportErrors: true)
        #expect(fixture.model.resumeHistory == history)
        #expect(fixture.server.historyRequestCount == 0)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func foregroundAndBackgroundShareHistoryPagesAndCommitBeforeTheLastPage() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        fixture.server.historyPages = 3
        fixture.server.holdHistory(after: 1)
        fixture.start()
        let key = fixture.keys[0]
        try await waitUntil { fixture.server.historyRequestCount == 2 && fixture.ready(key, requireHistory: false) }
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.conversationHistory.first?.text == "Saved history run-0")
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.historyLoaded != true)
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 1
        let foreground = Task { await fixture.model.loadRunHistory(key.runID, reportErrors: true) }
        try await Task.sleep(for: .milliseconds(100))
        #expect(fixture.server.historyRequestCount == 2, "Joining a background read must not restart its pagination.")
        #expect(fixture.model.resumeHistory.first?.text == "Saved history run-0")
        fixture.server.releaseHistory()
        await foreground.value
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.model.resumeHistory.count == 3)
        #expect(fixture.server.historyRequestCount == 3)
        await fixture.model.loadRunHistory(key.runID)
        #expect(fixture.server.historyRequestCount == 3)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func lateHistoryWarmsItsOwnRunWithoutReplacingTheNewlySelectedConversation() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        let key = fixture.keys[0], other = fixture.keys[1]
        fixture.server.historyPages = 3
        fixture.server.holdHistory(after: 1)
        fixture.model.scheduleConversationRead(workspace: fixture.destinations[0], key: key, target: .local)
        try await waitUntil { fixture.server.historyRequestCount == 2 }
        fixture.model.selectedWorkspaceID = other.workspaceID
        fixture.model.activeRun = try fixture.server.run(other.runID)
        fixture.model.selectedConversationID = "conversation-1"
        fixture.model.preparePromptHistoryContext()
        fixture.model.updatePrompt("The new conversation's unsent draft")
        fixture.model.resumeHistory = [.init(category: "output", text: "Keep this other conversation")]
        fixture.server.releaseHistory()
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.conversationHistory.count == 3)
        #expect(fixture.model.resumeHistory.last?.text == "Keep this other conversation")
        #expect(fixture.model.activeRun?.runId == other.runID)
        #expect(fixture.model.prompt == "The new conversation's unsent draft")
        #expect(fixture.server.commandCount == 0)
    }

    @Test func newMetadataRefreshesFinishedReadersWhileAnotherBranchIsStillHeld() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.server.holdAgent(runID: key.runID, agent: 7)
        fixture.start()
        try await waitUntil {
            fixture.server.heldAgentReads > 0
                && fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.finalMessageHasMore == false
        }
        fixture.server.finish()
        fixture.model.runSummariesByContext[key] = try fixture.server.run(key.runID)
        fixture.model.workspaces = [try fixture.workspace(0, running: false)]
        fixture.start()
        try await waitUntil {
            fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.finalMessageReadStatus == "success"
                && fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.finalMessageHasMore == false
        }
        #expect(fixture.model.conversationReadTasks[key] != nil)
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.finalMessage
            == fixture.server.answer(key.runID, 8))
        fixture.server.releaseAgent()
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func terminalAnswersRefreshOffscreenWithoutNewWorkEvents() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true)
        defer { fixture.close() }
        fixture.start()
        let key = fixture.keys[0]
        try await waitUntil { fixture.ready(key, terminal: false) }
        let previous = try #require(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8])
        fixture.server.finish()
        fixture.model.workspaces = [try fixture.workspace(0, running: false)]
        fixture.start()
        try await waitUntil { fixture.ready(key) }
        let updated = try #require(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8])
        #expect(previous.cursor == updated.cursor)
        #expect(previous.finalMessage != updated.finalMessage)
        #expect(updated.finalMessage == fixture.server.answer(key.runID, 8))
        #expect(fixture.server.historyRequestCount == 1)
        #expect(fixture.model.selectedWorkspaceID == "reader")
        #expect(fixture.server.commandCount == 0)
    }

    @Test func removedWorkspaceCannotBeRecreatedByLateBackgroundReads() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.server.holdAgent(runID: key.runID, agent: 7)
        fixture.start()
        try await waitUntil { fixture.server.heldAgentReads > 0 }
        fixture.model.workspaces = []
        fixture.model.forgetCachedRunViews(machineID: key.machineID, workspaceID: key.workspaceID)
        fixture.server.releaseAgent()
        try await waitUntil { fixture.model.conversationReadTasks.isEmpty }
        #expect(fixture.model.runViewStateStore.snapshot(for: key) == nil)
        #expect(fixture.model.activeRun == nil)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func coldStoreRestoresAllPreparedBranchesWithNoTurnSubmission() async throws {
        let fixture = try Fixture(workspaceCount: 2)
        defer { fixture.close() }
        fixture.start()
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0) } }
        try #require(fixture.model.runViewStateStore.flushPendingWrites(timeout: 3))
        let store = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        for key in fixture.keys {
            let restored = try #require(await store.hydrate(for: key))
            #expect(restored.snapshot.historyLoaded == true)
            #expect(restored.snapshot.transcripts.count == 8)
            #expect(restored.snapshot.transcripts[8]?.finalMessage == fixture.server.answer(key.runID, 8))
        }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func nativeTimelinePaintsEveryWarmBranchWhileTransportIsHeld() async throws {
        let fixture = try Fixture(workspaceCount: 5)
        defer { fixture.close() }
        fixture.server.historyPages = 3
        fixture.start()
        try await waitUntil { fixture.keys.allSatisfy { fixture.ready($0) } }
        let held = Task { () -> Void in
            let _: HealthResponse? = try? await fixture.model.client.call("system.health", body: EmptyBody())
        }
        defer { held.cancel() }
        try await waitUntil { fixture.server.healthReadsHeld > 0 }
        let surface = NativeSurface(model: fixture.model)
        defer { surface.close() }
        // Account for AppKit's one-time font/TextKit startup separately from navigation.
        await fixture.model.selectWorkspace("workspace-0")
        await fixture.model.selectAgent(1, manageFollowUpQueue: false)
        surface.update()
        try await waitUntil {
            surface.updateLayout()
            return surface.texts.contains(fixture.server.answer("run-0", 1))
        }
        let firstFrame = try surface.framePNG()
        var longest = Duration.zero
        for workspace in fixture.destinations {
            await fixture.model.selectWorkspace(workspace.workspaceId)
            for agent in 1...8 {
                let start = ContinuousClock.now
                await fixture.model.selectAgent(agent, manageFollowUpQueue: false)
                let selected = ContinuousClock.now
                surface.update()
                let updated = ContinuousClock.now
                let answer = fixture.server.answer(workspace.lastRunId, agent)
                try await waitUntil {
                    surface.updateLayout()
                    return surface.texts.contains(answer)
                }
                let elapsed = start.duration(to: .now)
                longest = max(longest, elapsed)
                #expect(elapsed < .milliseconds(100), "Native answer appearance: \(workspace.workspaceId)/\(agent) \(elapsed); selection=\(start.duration(to: selected)); layout=\(selected.duration(to: updated))")
                #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
                #expect(fixture.model.resumeHistory.count == 3)
            }
        }
        #expect(fixture.server.commandCount == 0)
        let finalFrame = try surface.framePNG()
        #expect(firstFrame != finalFrame, "The native frame must change with the selected branch")
        if let path = ProcessInfo.processInfo.environment["PCR_READINESS_EVIDENCE_DIR"] {
            try finalFrame.write(to: URL(fileURLWithPath: path).appendingPathComponent("native-warm-reading.png"))
        }
        print("Native offscreen reading: 40 complete branches, three history pages each; slowest paint=\(longest); health held")
    }

    @Test(arguments: [false, true])
    func backgroundFailureRetainsTheGoodAnswerAndRetriesWithTheSameIdentity(replacementFirstPageFails: Bool) async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        let previous = fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]
        fixture.server.finish()
        if replacementFirstPageFails { fixture.server.failZeroAgent = 8 }
        else { fixture.server.failAgent = 8 }
        fixture.model.workspaces = [try fixture.workspace(0, running: false)]
        fixture.start()
        await fixture.model.conversationPreloadTask?.value
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8] == previous)
        #expect(fixture.model.errorMessage == nil)
        fixture.server.failAgent = nil
        fixture.server.failZeroAgent = nil
        fixture.model.conversationPreloadRetryAfter[key] = .distantPast
        fixture.start()
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[8]?.finalMessage
            == fixture.server.answer(key.runID, 8))
        #expect(fixture.server.commandCount == 0)
    }

    @Test func invalidHistoryPageCannotBeMarkedCompleteOrReplaceSavedHistory() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0]
        let run = try fixture.server.run(key.runID)
        let saved = [ResumeHistoryEntry(category: "assistant", text: "Keep original history")]
        fixture.model.runViewStateStore.put(WorkspaceRunViewSnapshot(selectedAgent: 1,
            transcripts: [:], conversationHistory: saved), runSummary: run, for: key)
        fixture.server.invalidHistory = true
        fixture.start()
        try await waitUntil { fixture.ready(key, requireHistory: false) }
        for task in Array(fixture.model.conversationHistoryTasks.values) { await task.value }
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.historyLoaded != true)
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.conversationHistory == saved)
        fixture.server.invalidHistory = false
        fixture.model.conversationPreloadRetryAfter[key] = .distantPast
        fixture.start()
        try await waitUntil { fixture.ready(key) }
        #expect(fixture.server.commandCount == 0)
    }

    @Test func historyRetryDoesNotWaitForAnUnrelatedAgentReaderToFinish() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.server.invalidHistory = true
        fixture.server.holdAgent(runID: key.runID, agent: 8)
        fixture.start()
        try await waitUntil { fixture.server.heldAgentReads > 0 && fixture.server.historyRequestCount > 0
            && fixture.model.conversationHistoryTasks[key] == nil }
        #expect(fixture.model.conversationReadTasks[key] != nil)
        fixture.server.invalidHistory = false
        fixture.model.conversationPreloadRetryAfter[key] = .distantPast
        fixture.start()
        try await waitUntil(timeout: .seconds(2)) {
            fixture.model.runViewStateStore.snapshot(for: key)?.historyLoaded == true
        }
        #expect(fixture.server.heldAgentReads > 0)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func foregroundWorkReceiptCannotRegressAnAdvancedBackgroundSnapshot() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = try fixture.server.run(key.runID)
        fixture.model.selectedAgent = 7
        fixture.server.holdAgent(runID: key.runID, agent: 7)
        let read = Task { await fixture.model.loadAgentDetail(7, maxPages: 1) }
        try await waitUntil { fixture.server.heldAgentReads > 0 }
        let newer = AgentTranscript(sourceRunID: key.runID, sourceAgent: 7, cursor: 6, hasMore: false,
            finalMessage: "Newer complete answer", finalMessageCursor: 21, finalMessageHasMore: false,
            finalMessageID: "newer", finalMessageReadStatus: "success")
        fixture.model.transcripts[7] = newer
        fixture.model.cacheVisibleRunViewState()
        fixture.server.releaseAgent()
        #expect(await read.value)
        #expect(fixture.model.transcripts[7] == newer)
        #expect(fixture.model.runViewStateStore.snapshot(for: key)?.transcripts[7] == newer)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func aLateForegroundSummaryCannotUndoNewerVisibleProgress() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        let older = try fixture.server.run(key.runID)
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.selectedConversationID = older.conversationId
        fixture.model.activeRun = older
        fixture.model.selectedAgent = 7
        fixture.server.holdRun(key.runID)
        let read = Task { try await fixture.model.loadRun(key.runID, loadDetailPages: 1) }
        try await waitUntil { fixture.server.heldRunReads == 1 }
        fixture.server.advanceWork()
        fixture.server.finish()
        let newer = try fixture.server.run(key.runID)
        fixture.model.activeRun = newer
        fixture.model.runSummariesByContext[key] = newer
        fixture.model.selectedAgent = 6
        fixture.model.transcripts[6] = AgentTranscript(sourceRunID: key.runID, sourceAgent: 6,
            cursor: 7, hasMore: false, finalMessage: "Newer confirmed answer", finalMessageCursor: 22,
            finalMessageHasMore: false, finalMessageID: "new-answer", finalMessageReadStatus: "success")
        fixture.model.cacheVisibleRunViewState()
        fixture.server.releaseRun()
        try await read.value
        #expect(fixture.model.activeRun?.eventCount == newer.eventCount)
        #expect(fixture.model.activeRun?.status == "completed")
        #expect(fixture.model.runSummariesByContext[key]?.eventCount == newer.eventCount)
        #expect(fixture.model.selectedAgent == 6)
        #expect(fixture.model.transcripts[6]?.finalMessage == "Newer confirmed answer")
        #expect(fixture.server.commandCount == 0)
    }

    @Test func enteringACachedRunCannotRollBackNewerBackgroundMetadata() async throws {
        let fixture = try Fixture(workspaceCount: 1, running: true)
        defer { fixture.close() }
        let key = fixture.keys[0]
        fixture.start()
        try await waitUntil { fixture.ready(key, terminal: false) }
        let olderRun = try fixture.server.run(key.runID)
        let older = try #require(fixture.model.runViewStateStore.snapshot(for: key))
        fixture.server.holdAgent(runID: key.runID, agent: 7)
        fixture.server.finish()
        fixture.model.workspaces = [try fixture.workspace(0, running: false)]
        fixture.start()
        try await waitUntil { fixture.server.heldAgentReads > 0 }
        // The synchronous first paint can precede the foreground revalidation.
        fixture.model.selectedWorkspaceID = key.workspaceID
        fixture.model.activeRun = olderRun
        fixture.model.selectedAgent = 7
        fixture.model.transcripts = older.transcripts
        fixture.model.resumeHistory = older.conversationHistory
        fixture.model.cacheVisibleRunViewState()
        #expect(fixture.model.runSummariesByContext[key]?.status == "completed")
        fixture.server.releaseAgent()
        try await waitUntil { fixture.model.transcripts[7]?.finalMessageReadStatus == "success" }
        #expect(fixture.model.runSummariesByContext[key]?.status == "completed")
        #expect(fixture.model.selectedWorkspaceID == key.workspaceID)
        #expect(fixture.model.selectedAgent == 7)
        #expect(fixture.server.commandCount == 0)
    }

    @Test func latePagesCannotRegressWorkOrReplaceAnotherReadersAnswer() async throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let run = try fixture.server.run("run-0")
        let requested = AgentTranscript(sourceRunID: run.runId, sourceAgent: 1, cursor: 1,
            finalMessage: "older", finalMessageCursor: 5, finalMessageHasMore: true, finalMessageID: "old")
        var current = AgentTranscript(sourceRunID: run.runId, sourceAgent: 1, cursor: 6, hasMore: false,
            finalMessage: "newer confirmed answer", finalMessageCursor: 22, finalMessageHasMore: false,
            finalMessageID: "new", finalMessageReadStatus: "success")
        current.error = "Newer verified diagnostic"
        let detail = try fixture.server.detail(runID: run.runId, agent: 1, cursor: 1, finalCursor: 5)
        let merged = try #require(await ConversationReadProjection.merge(
            ConversationAgentRead(agent: 1, requested: requested, status: "running", detail: detail),
            into: current, run: run, history: []))
        #expect(merged.input.transcript.cursor == 6)
        #expect(merged.input.transcript.finalMessage == current.finalMessage)
        #expect(merged.input.transcript.finalMessageReadStatus == "success")
        #expect(merged.input.transcript.error == current.error)
        let wrong = ConversationAgentRead(agent: 2, requested: requested, status: "success", detail: detail)
        #expect(await ConversationReadProjection.merge(wrong, into: current, run: run, history: []) == nil)
    }

    @Test func pendingReceiptWithMegabytesOfWorkDoesNotBlockNativeUpdates() throws {
        let fixture = try Fixture(workspaceCount: 1)
        defer { fixture.close() }
        let model = fixture.model
        let key = fixture.keys[0]
        model.selectedWorkspaceID = key.workspaceID
        model.selectedConversationID = key.runID
        model.activeRun = try fixture.server.run(key.runID)
        model.selectedAgent = 1
        let payload = String(repeating: "work output with whitespace ", count: 160)
        model.transcripts[1] = AgentTranscript(sourceRunID: key.runID, sourceAgent: 1,
            blocks: (0..<1200).map {
                TranscriptBlock(id: "work-\($0)", kind: .thought, text: "\($0): \(payload)")
            }, finalMessage: "Ready native answer", finalMessageHasMore: false)
        model.pendingPromptSubmission = PersistedPromptSubmission(id: "native-pending-message",
            machineID: key.machineID, workspaceID: key.workspaceID, prompt: "Next native question",
            settings: RunSettings(), sourceRunID: key.runID, sourceAgent: 1, resumeSessionID: "",
            confirmLargeRun: false, attempt: 0, status: .failed, error: "Receipt unknown")
        let surface = NativeSurface(model: model)
        defer { surface.close() }
        surface.update()
        #expect(surface.texts.contains { $0.contains("Ready native answer") })
        #expect(surface.texts.contains { $0.contains("Next native question") })
        let generation = model.transcriptProjectionGeneration
        var maximum = Duration.zero
        for index in 0..<30 {
            model.transcripts[1]?.cursor += 1
            model.followUpSecondsRemaining = index
            let start = ContinuousClock.now
            surface.update()
            maximum = max(maximum, start.duration(to: .now))
        }
        #expect(maximum < .milliseconds(100))
        #expect(model.transcriptProjectionGeneration == generation)
        #expect(model.pendingPromptSubmission?.id == "native-pending-message")
        #expect(model.transcripts[1]?.blocks.count == 1200)
        #expect(fixture.server.commandCount == 0)
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        print("Long pending native body: 30 updates, slowest update=\(maximum), no commands or visible QA window")
    }

    private func waitUntil(timeout: Duration = .seconds(12), _ condition: @MainActor () -> Bool) async throws {
        let deadline = ContinuousClock.now + timeout
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        try #require(condition())
    }

    @MainActor
    private final class NativeSurface {
        let model: AppModel
        let window: NSWindow
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 920, height: 600))
        let defaults: UserDefaults
        let suite = "WorkspaceReadiness.\(UUID().uuidString)"
        let memory: TranscriptScrollMemory
        var coordinator: ConversationTimelineView.Coordinator!
        var collection: WidthAwareCollectionView { scroll.documentView as! WidthAwareCollectionView }

        init(model: AppModel) {
            self.model = model
            _ = NSApplication.shared
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
            window = NSWindow(contentRect: scroll.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            coordinator = ConversationTimelineView.Coordinator(parent: parent())
            let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            collection.frame = scroll.bounds
            scroll.documentView = collection
            window.contentView = scroll
            coordinator.attach(scrollView: scroll, collectionView: collection)
        }
        func parent() -> ConversationTimelineView {
            ConversationTimelineView(language: .english, textSize: .standard, accentColor: .orange,
                appearance: .dark, contextID: transcriptScrollContextID(
                    machineID: model.currentMachineContextID,
                    workspaceID: model.selectedWorkspaceID ?? "",
                    conversationID: transcriptConversationContextID(
                        selectedConversationID: model.selectedConversationID,
                        activeRunID: model.activeRun?.runId,
                        workspaceLastRunID: model.selectedWorkspace?.lastRunId),
                    runID: model.activeRun?.runId ?? model.selectedConversation?.latestRunID ?? "history",
                    agentID: transcriptAgentContextID(selectedAgent: model.selectedAgent, restoredAgent: nil)),
                items: model.selectedTimelineItems, contentIsLoading: model.isSelectedTimelineLoading,
                jumpToLatestRequest: 0, findQuery: "", findMatch: nil, findNavigationRequest: 0,
                memory: memory, resultsInspectorVisible: false, openURL: { _, _ in },
                activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                itemsRevision: model.conversationTimelineItemsProjectionGeneration,
                isNearBottom: .constant(true), inlineAgentSwitchPosition: .constant(.missing))
        }
        func update() { coordinator.update(parent: parent()); updateLayout() }
        func updateLayout() { scroll.layoutSubtreeIfNeeded(); collection.layoutSubtreeIfNeeded() }
        func framePNG() throws -> Data {
            let bitmap = try #require(scroll.bitmapImageRepForCachingDisplay(in: scroll.bounds))
            scroll.cacheDisplay(in: scroll.bounds, to: bitmap)
            return try #require(bitmap.representation(using: .png, properties: [:]))
        }
        var texts: [String] {
            func strings(_ view: NSView) -> [String] {
                (view as? NSTextView).map { [$0.string] } ?? view.subviews.flatMap(strings)
            }
            return collection.visibleItems().flatMap { strings($0.view) }
        }
        func close() {
            coordinator.detach(); memory.freezeForTermination(); window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }

    private final class Server: @unchecked Sendable {
        private let lock = NSLock()
        private let healthGate = DispatchGroup()
        private let agentGate = DispatchGroup()
        private let historyGate = DispatchGroup()
        private let runGate = DispatchGroup()
        private let finalizeGate = DispatchGroup()
        private var finalizationReply: RunSummary?
        private var finalizationLostReply = false
        private var finalizationReleased = false
        private var runReleased = false
        private var heldRunID: String?
        private var runHoldAfterReads = 0
        private var runReadsHeld = 0
        private var runOverrides: [String: RunSummary] = [:]
        private var healthReleased = false
        private var agentReleased = false
        private var historyReleased = false
        private var heldAgent: (String, Int)?
        private var historyHeld = false
        private var historyHoldCursor = 0
        private var requests: [String] = []
        private var concurrent = 0
        private var maximum = 0
        private var heldAgents = 0
        private var heldHealth = 0
        private var running: Bool
        private var finishedRunIDs: Set<String> = []
        private var pageCount = 1
        private var invalidPage = false
        private var failingAgent: Int?
        private var failingRun: String?
        private var failingZeroAgent: Int?
        private var eventTotal = 6
        private var longWorkBacklog = false
        private var nonWorkRecentTail = false
        private var agentTotal = 8
        var holdWorkBackfill = false
        let liveWorkOnly: Bool

        init(running: Bool, liveWorkOnly: Bool = false) {
            self.running = running
            self.liveWorkOnly = liveWorkOnly
            healthGate.enter(); agentGate.enter(); historyGate.enter(); runGate.enter(); finalizeGate.enter()
        }
        private func locked<T>(_ operation: () throws -> T) rethrows -> T {
            lock.lock(); defer { lock.unlock() }; return try operation()
        }
        var maximumConcurrentReads: Int { locked { maximum } }
        var agentRequestCount: Int { locked { requests.filter { $0 == "runs.agent" }.count } }
        var historyRequestCount: Int { locked { requests.filter { $0 == "runs.history" }.count } }
        var heldAgentReads: Int { locked { heldAgents } }
        var healthReadsHeld: Int { locked { heldHealth } }
        var heldRunReads: Int { locked { runReadsHeld } }
        func holdRun(_ id: String, afterReads: Int = 0) {
            locked { heldRunID = id; runHoldAfterReads = afterReads }
        }
        func installRun(_ run: RunSummary) { locked { runOverrides[run.runId] = run } }
        func holdFinalization(reply: RunSummary, lostReply: Bool = false) {
            locked { finalizationReply = reply; finalizationLostReply = lostReply }
        }
        func releaseFinalization() {
            if locked({ let first = !finalizationReleased; finalizationReleased = true; return first }) {
                finalizeGate.leave()
            }
        }
        func releaseRun() { if locked({ let first = !runReleased; runReleased = true; return first }) { runGate.leave() } }
        var unexpectedActions: [String] { locked { requests.filter {
            !["system.health", "system.capabilities", "relay.status", "turns.get", "runs.agent", "runs.history"].contains($0)
        } } }
        var commandCount: Int { unexpectedActions.count }
        func count(_ action: String) -> Int { locked { requests.filter { $0 == action }.count } }
        var historyPages: Int { get { locked { pageCount } } set { locked { pageCount = newValue } } }
        var invalidHistory: Bool { get { locked { invalidPage } } set { locked { invalidPage = newValue } } }
        var failAgent: Int? { get { locked { failingAgent } } set { locked { failingAgent = newValue } } }
        var failRun: String? { get { locked { failingRun } } set { locked { failingRun = newValue } } }
        var failZeroAgent: Int? { get { locked { failingZeroAgent } } set { locked { failingZeroAgent = newValue } } }
        func holdAgent(runID: String, agent: Int) { locked { heldAgent = (runID, agent) } }
        func holdHistory(after cursor: Int = 0) { locked { historyHeld = true; historyHoldCursor = cursor } }
        func finish() { locked { running = false } }
        func finishRun(_ runID: String) { locked { _ = finishedRunIDs.insert(runID) } }
        func advanceWork() { locked { eventTotal += 1 } }
        func simulateLongWorkBacklog(agentCount: Int = 8, nonWorkTail: Bool = false) {
            locked { eventTotal = 240_000; longWorkBacklog = true; agentTotal = agentCount; nonWorkRecentTail = nonWorkTail }
        }
        func addSynthesis() { locked { agentTotal = 11 } }
        func releaseAgent() { if locked({ let first = !agentReleased; agentReleased = true; return first }) { agentGate.leave() } }
        func releaseHistory() { if locked({ let first = !historyReleased; historyReleased = true; return first }) { historyGate.leave() } }
        func release() {
            releaseAgent(); releaseHistory(); releaseRun(); releaseFinalization()
            if locked({ let first = !healthReleased; healthReleased = true; return first }) { healthGate.leave() }
        }
        func answer(_ runID: String, _ agent: Int) -> String {
            "\(runID) Agent \(agent) \(locked { running } ? "working" : "complete") "
                + String(repeating: "saved answer ", count: 6).trimmingCharacters(in: .whitespaces)
        }
        func workText(_ runID: String, _ agent: Int, cursor: Int) -> String {
            "Live \(runID) Agent \(agent) page \(cursor)"
        }
        func run(_ runID: String) throws -> RunSummary {
            if let override = locked({ runOverrides[runID] }) { return override }
            let isRunning = locked { running && !finishedRunIDs.contains(runID) }
            let id = runID.replacingOccurrences(of: "run-", with: "")
            return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": runID, "conversationId": "conversation-\(id)", "turnIndex": 0,
                "status": isRunning ? "running" : "completed", "workspace": "/qa/workspace-\(id)",
                "prompt": "Question \(id)", "createdAt": "2026-10-15T00:00:00Z",
                "updatedAt": "2026-10-15T00:00:00Z", "config": [:], "syncedBack": false,
                "workspacesDeleted": false, "codexHomesDeleted": false, "workerActive": isRunning,
                "workerAvailable": true, "eventCount": locked { eventTotal },
                "agents": (1...locked { agentTotal }).map { ["agent": $0, "name": "Agent \($0)",
                    "status": isRunning ? "running" : "success", "reasoningTokenCounts": [:],
                    "rejected": false, "recommended": false, "finalized": false] },
            ]))
        }
        func detail(runID: String, agent: Int, cursor: Int, finalCursor: Int) throws -> RunAgentDetail {
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(RunAgentDetail.self, from: JSONSerialization.data(withJSONObject:
                detailBody(runID: runID, agent: agent, cursor: cursor, finalCursor: finalCursor)))
        }
        private func detailBody(runID: String, agent: Int, cursor: Int, finalCursor: Int,
                                conversationOnly: Bool = false) -> [String: Any] {
            let text = liveWorkOnly ? "" : answer(runID, agent)
            let bytes = Data(text.utf8)
            let offset = min(finalCursor, bytes.count)
            let end = min(offset + 24, bytes.count)
            let total = locked { eventTotal }
            if locked({ longWorkBacklog }) {
                if !conversationOnly, cursor > 0, locked({ nonWorkRecentTail }) {
                    return ["run_id": runID, "agent": agent, "name": "Agent \(agent)", "status": "running",
                        "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
                        "final_message": "", "stdout_tail": "", "stderr_tail": "",
                        "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": cursor + 1,
                            "has_more": true, "total": total, "items": [
                                ["id": "ignored-\(cursor)", "event_id": cursor,
                                 "kind": "activity", "text": "raw-media-fragment"]]]]
                }
                var items: [[String: Any]] = [
                    ["id": "before", "event_id": total - 50, "kind": "output", "text": "Checking recent work"],
                    ["id": "after", "event_id": total - 10, "kind": "output", "text": "Recent result is ready"]]
                if !conversationOnly {
                    for (event, status) in [(total - 30, "running"), (total - 20, "completed")] {
                        items.append(["id": "event-\(event)", "event_id": event, "kind": "command",
                            "text": "printf fixture", "status": status,
                            "operation": ["id": "\(runID)-\(agent)-operation", "event_id": event,
                                "kind": "command", "status": status, "command": "printf fixture",
                                "targets": [], "details_available": true]])
                    }
                }
                return ["run_id": runID, "agent": agent, "name": "Agent \(agent)", "status": "running",
                    "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
                    "final_message": "", "stdout_tail": "", "stderr_tail": "",
                    "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": total,
                        "has_more": false, "total": total,
                        "items": items.filter { ($0["event_id"] as! Int) >= cursor }
                            .sorted { ($0["event_id"] as! Int) < ($1["event_id"] as! Int) }]]
            }
            let next = min(cursor + (conversationOnly ? total : 1), total)
            return ["run_id": runID, "agent": agent, "name": "Agent \(agent)",
                "status": locked { running } ? "running" : "success", "reasoning_token_counts": [:],
                "rejected": false, "recommended": false, "finalized": false, "final_message": "",
                "stdout_tail": "", "stderr_tail": "",
                "final_message_page": ["cursor": offset, "next_cursor": end, "has_more": end < bytes.count,
                    "total_bytes": bytes.count, "message_id": "artifact-\(text)",
                    "text": String(decoding: bytes[offset..<end], as: UTF8.self)],
                "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": next,
                    "has_more": next < total, "total": total, "items": liveWorkOnly && cursor < total ? (cursor..<next).map {
                        ["id": "live-\(runID)-\(agent)-\($0)", "event_id": $0,
                         "kind": "output", "text": workText(runID, agent, cursor: $0),
                         "activity_type": "assistant_progress"]
                    } : []]]
        }
        func respond(_ socket: Int32) {
            defer { Darwin.close(socket) }
            var noSignal: Int32 = 1
            _ = withUnsafePointer(to: &noSignal) { Darwin.setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size)) }
            var buffer = [UInt8](repeating: 0, count: 8192)
            let count = Darwin.read(socket, &buffer, buffer.count)
            guard count > 0, let request = try? JSONSerialization.jsonObject(with: Data(buffer.prefix(count))) as? [String: Any],
                  let action = request["action"] as? String else { return }
            let body = request["body"] as? [String: Any] ?? [:]
            let runID = body["run_id"] as? String ?? "run-0"
            let agent = body["agent"] as? Int ?? 1
            locked { requests.append(action) }
            if ["runs.agent", "runs.history", "turns.get"].contains(action) {
                locked { concurrent += 1; maximum = max(maximum, concurrent) }
            }
            defer { if ["runs.agent", "runs.history", "turns.get"].contains(action) { locked { concurrent -= 1 } } }
            if action == "system.health" {
                locked { heldHealth += 1 }
                _ = healthGate.wait(timeout: .now() + 15)
            }
            if action == "runs.agent", locked({ heldAgent?.0 == runID && heldAgent?.1 == agent && !agentReleased }) {
                locked { heldAgents += 1 }; _ = agentGate.wait(timeout: .now() + 15)
            }
            if action == "runs.agent", holdWorkBackfill,
               body["include_events"] as? Bool == true,
               (!locked({ longWorkBacklog }) || (body["cursor"] as? Int ?? 0) < 240_000 - AgentRecentWork.initialEventWindow),
               body["conversation_items_only"] as? Bool != true, locked({ !agentReleased }) {
                locked { heldAgents += 1 }; _ = agentGate.wait(timeout: .now() + 15)
            }
            if action == "runs.history", locked({ historyHeld && !historyReleased
                && (body["cursor"] as? Int ?? 0) >= historyHoldCursor }) {
                _ = historyGate.wait(timeout: .now() + 15)
            }
            let reply: [String: Any]
            if action == "turns.finalize", let final = locked({ finalizationReply }) {
                _ = finalizeGate.wait(timeout: .now() + 15)
                installRun(final)
                if locked({ finalizationLostReply }) {
                    let data = try! JSONSerialization.data(withJSONObject: ["ok": false,
                        "error": ["code": "deadline_exceeded", "message": "Reply unavailable; verify original run"]]) + Data([10])
                    _ = data.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
                    return
                }
            }
            if action == "turns.get", locked({ failingRun == runID }) {
                reply = ["ok": false, "error": ["code": "fixture_offline", "message": "Original run read unavailable"]]
            } else if ["turns.get", "turns.finalize"].contains(action), let run = try? run(runID) {
                if locked({ heldRunID == runID && !runReleased
                    && requests.filter { $0 == "turns.get" }.count > runHoldAfterReads }) {
                    locked { runReadsHeld += 1 }; _ = runGate.wait(timeout: .now() + 15)
                }
                let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
                reply = ["ok": true, "body": (try? JSONSerialization.jsonObject(with: encoder.encode(run))) ?? [:]]
            } else if action == "runs.agent", locked({ failingAgent == agent
                || (failingZeroAgent == agent && (body["final_message_cursor"] as? Int ?? 0) == 0) }) {
                reply = ["ok": false, "error": ["code": "fixture_offline", "message": "Temporary read failure"]]
            } else if action == "runs.agent" {
                reply = ["ok": true, "body": detailBody(runID: runID, agent: agent,
                    cursor: body["cursor"] as? Int ?? 0, finalCursor: body["final_message_cursor"] as? Int ?? 0,
                    conversationOnly: body["conversation_items_only"] as? Bool == true)]
            } else if action == "runs.history" {
                let cursor = body["cursor"] as? Int ?? 0
                let next = cursor + 1
                let pages = historyPages
                reply = ["ok": true, "body": ["workspace": "/qa", "entries": [
                    ["category": "assistant", "text": cursor == 0 ? "Saved history \(runID)" : "Earlier page \(cursor)",
                     "id": "saved-\(runID)-\(cursor)", "turn_id": "prior-\(runID)-\(cursor)"]],
                    "cursor": invalidHistory ? cursor + 1 : cursor,
                    "next_cursor": next, "has_more": next < pages, "total_entries": pages]]
            } else if action == "system.capabilities" {
                reply = ["ok": true, "body": ["daemon_version": "0.1.86", "actions": ["system.health"]]]
            } else {
                reply = ["ok": false, "error": ["code": "fixture_offline", "message": "Read-only fixture"]]
            }
            guard let data = try? JSONSerialization.data(withJSONObject: reply) + Data([10]) else { return }
            _ = data.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
        }
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let cacheDirectory: URL
        let listener: Int32
        let source: DispatchSourceRead
        let server: Server
        let model: AppModel
        let destinations: [RemoteWorkspace]
        let keys: [WorkspaceRunViewKey]

        init(workspaceCount: Int, running: Bool = false, liveWorkOnly: Bool = false) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-ready-\(UUID().uuidString.prefix(8))")
            cacheDirectory = directory.appendingPathComponent("cache")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("fixture-token".utf8).write(to: tokenURL)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            let descriptor = listener
            var address = sockaddr_un(); address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { destination in
                Array(socketURL.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
            }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(bound == 0 && Darwin.listen(descriptor, 128) == 0)
            _ = fcntl(descriptor, F_SETFL, O_NONBLOCK)
            let server = Server(running: running, liveWorkOnly: liveWorkOnly); self.server = server
            source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .global())
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
            let windowID = UUID()
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: windowID,
                desktopStateStore: DesktopStateStore(windowID: windowID, directory: directory),
                runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: cacheDirectory)))
            model.machines = [RelayMachine(deviceId: "local", kind: "machine", name: "Fixture",
                publicKey: "fixture-key", status: "online", online: true, lastSeenAt: nil)]
            model.selectedMachineID = "local"
            model.nextMachineDirectoryRefresh = .distantFuture
            destinations = try (0..<workspaceCount).map { try Self.workspace($0, running: running) }
            let machineID = model.currentMachineContextID
            keys = destinations.map { WorkspaceRunViewKey(machineID: machineID,
                workspaceID: $0.workspaceId, runID: $0.lastRunId) }
            model.workspaces = destinations
            model.selectedWorkspaceID = "reader"
        }
        static func workspace(_ index: Int, running: Bool, lastRunID: String? = nil) throws -> RemoteWorkspace {
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "workspace-\(index)", "name": "Workspace \(index)", "path": "/qa/workspace-\(index)",
                "lastRunId": lastRunID ?? "run-\(index)", "lastResumeSessionId": "", "runStatus": running ? "running" : "completed",
                "runWorkerActive": running, "selected": false, "available": true, "readable": true, "writable": true]))
        }
        func workspace(_ index: Int, running: Bool) throws -> RemoteWorkspace { try Self.workspace(index, running: running) }
        func start() {
            model.scheduleConversationPreload(machineID: model.currentMachineContextID,
                target: .local, workspaces: model.workspaces)
        }
        func ready(_ key: WorkspaceRunViewKey, requireHistory: Bool = true, terminal: Bool = true) -> Bool {
            guard let snapshot = model.runViewStateStore.snapshot(for: key),
                  !requireHistory || snapshot.historyLoaded == true else { return false }
            return (1...8).allSatisfy {
                guard let transcript = snapshot.transcripts[$0] else { return false }
                return transcript.cursor == 6 && !transcript.hasMore && !transcript.finalMessageHasMore
                    && transcript.finalMessageReadStatus == (terminal ? "success" : "running")
            }
        }
        func close() {
            model.stopWindowObservation()
            server.release()
            _ = model.desktopStateStore.flushPendingWrites()
            _ = model.runViewStateStore.flushPendingWrites(timeout: 3)
            source.cancel()
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
