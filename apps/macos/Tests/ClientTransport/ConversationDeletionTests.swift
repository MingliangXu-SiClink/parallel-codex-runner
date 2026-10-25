import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ConversationDeletionTests {
    private func location() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("pcr-trash-tests-\(UUID())/trash.json")
    }

    private func run(_ id: String, conversation: String? = nil, previous: String? = nil,
                     session: String? = nil, status: String = "completed") throws -> RunSummary {
        var object: [String: Any] = [
            "runId": id, "status": status, "workspace": "/trash-qa", "prompt": "Same title",
            "createdAt": "2026-10-03T01:00:00Z", "updatedAt": "2026-10-03T01:00:00Z",
            "config": session.map { ["resumeSessionId": $0] } ?? [:], "syncedBack": false,
            "workspacesDeleted": false, "codexHomesDeleted": false, "workerActive": status == "running",
            "workerAvailable": true, "eventCount": 0, "agents": [],
        ]
        object["conversationId"] = conversation
        object["continuedFromRunId"] = previous
        return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func summary(_ run: RunSummary) -> WorkspaceConversationSummary {
        WorkspaceConversationSummary(id: run.conversationKey, title: run.prompt, runs: [run], resumeSession: nil)
    }

    private func session(_ id: String) throws -> ResumeSession {
        try JSONDecoder().decode(ResumeSession.self, from: JSONSerialization.data(withJSONObject: [
            "sessionId": id, "title": "Same title", "cwd": "/trash-qa", "source": "cli",
            "model": "gpt-6.1-sol", "rolloutPath": "/trash-qa/fictional.jsonl", "preview": "Same title",
        ]))
    }

    private func workspace(lastRun: String = "a", status: String = "completed") throws -> RemoteWorkspace {
        try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
            "workspaceId": "trash-qa", "name": "QA", "path": "/trash-qa", "lastRunId": lastRun,
            "lastResumeSessionId": "", "selected": true, "available": true, "readable": true,
            "writable": true, "runStatus": status, "runAgentsTotal": 1, "runAgentsFinished": 1,
            "runAgentsFailed": 0, "runWorkerActive": status == "running",
        ]))
    }

    private func remove(_ conversation: WorkspaceConversationSummary, from store: ConversationTrashStore,
                        machine: String = "local", workspace: String = "trash-qa") throws {
        try store.delete(conversation, machineID: machine, workspaceID: workspace,
                         machineName: "QA Mac", workspaceName: "QA")
    }

    private func model(store: ConversationTrashStore, workspace: RemoteWorkspace) -> AppModel {
        let result = AppModel(windowID: UUID(), conversationTrash: store)
        result.workspaces = [workspace]
        result.selectedWorkspaceID = workspace.workspaceId
        result.daemonOnline = false
        return result
    }

    @Test
    func deletionIsDurableScopedAndDoesNotUseTheTitle() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let a = summary(try run("a", conversation: "one", previous: "root", session: "session-one"))
        let b = summary(try run("b", conversation: "two"))
        try remove(a, from: store)
        #expect(store.visible([a, b], machineID: "local", workspaceID: "trash-qa") == [b])
        #expect(store.visible([a], machineID: "other", workspaceID: "trash-qa") == [a])
        #expect(store.visible([a], machineID: "local", workspaceID: "other") == [a])
        let restored = ConversationTrashStore(fileURL: url)
        #expect(restored.visible([a, b], machineID: "local", workspaceID: "trash-qa") == [b])
        #expect(restored.records.first?.aliases.isSuperset(of: ["run:a", "run:root", "session:session-one", "conversation:one"]) == true)
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber
        #expect(mode?.intValue == 0o600)
        try restored.restore(try #require(restored.records.first))
        #expect(restored.visible([a, b], machineID: "local", workspaceID: "trash-qa") == [a, b])
        #expect(ConversationTrashStore(fileURL: url).records.isEmpty)
    }

    @Test
    func paginatedAndTransitiveAliasesNeverResurrectDeletedConversations() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let a = summary(try run("a", conversation: "one"))
        try remove(a, from: store)
        let bridge = summary(try run("b", conversation: "new-root", previous: "a"))
        let third = summary(try run("c", conversation: "third-root", previous: "b", session: "later-session"))
        // Reverse order requires more than one absorption pass.
        #expect(store.visible([third, bridge], machineID: "local", workspaceID: "trash-qa").isEmpty)
        let standalone = WorkspaceConversationSummary(id: "resume:later-session", title: "Same title", runs: [],
                                                      resumeSession: try session("later-session"))
        let reopened = ConversationTrashStore(fileURL: url)
        #expect(reopened.visible([standalone], machineID: "local", workspaceID: "trash-qa").isEmpty)
        try reopened.restore(try #require(reopened.records.first))
        #expect(reopened.visible([standalone], machineID: "local", workspaceID: "trash-qa") == [standalone])
    }

    @Test
    func restoringAFormerAliasRestoresTheWholeMergedLineage() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let a = summary(try run("a", conversation: "one"))
        let b = summary(try run("b", conversation: "two"))
        try remove(a, from: store)
        try remove(b, from: store)
        let oldRecord = try #require(store.records.last)
        let merged = WorkspaceConversationSummary(id: "root", title: "Same title", runs: a.runs + b.runs, resumeSession: nil)
        #expect(store.visible([merged], machineID: "local", workspaceID: "trash-qa").isEmpty)
        #expect(store.records.count == 1)
        try store.restore(oldRecord)
        #expect(store.records.isEmpty)
        #expect(store.visible([merged], machineID: "local", workspaceID: "trash-qa") == [merged])
    }

    @Test
    func persistenceFailureAndCorruptStateCannotPretendDeletionSucceeded() throws {
        let directory = location().deletingLastPathComponent()
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let parentFile = directory.appendingPathComponent("not-a-directory")
        try Data("block".utf8).write(to: parentFile)
        let store = ConversationTrashStore(fileURL: parentFile.appendingPathComponent("trash.json"))
        #expect(throws: (any Error).self) { try remove(summary(try run("a")), from: store) }
        #expect(store.records.isEmpty)
        let invalid = directory.appendingPathComponent("invalid.json")
        let original = Data("invalid-state".utf8)
        try original.write(to: invalid)
        let corrupt = ConversationTrashStore(fileURL: invalid)
        #expect(corrupt.loadError != nil)
        #expect(corrupt.visible([summary(try run("a"))], machineID: "local", workspaceID: "trash-qa").isEmpty)
        #expect(throws: (any Error).self) { try remove(summary(try run("a")), from: corrupt) }
        #expect(try Data(contentsOf: invalid) == original)
    }

    @Test
    func deletingTheSelectionOpensNewConversationAndKeepsDraftsAndCaches() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let workspace = try workspace()
        let model = model(store: store, workspace: workspace)
        defer { model.stopWindowObservation() }
        let a = try run("a")
        let b = try run("b")
        model.applyWorkspaceConversationIndex([a, b], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
        model.selectedConversationID = "a"
        model.activeRun = a
        model.preparePromptHistoryContext()
        model.updatePrompt("Unsent draft")
        let draftKey = model.currentPromptDraftContext()
        let generation = model.workspaceContextGeneration
        model.runSummariesByContext[WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId, runID: "a")] = a
        model.requestConversationDelete()
        try model.deleteConversation(try #require(model.conversationDeleteRequest))
        #expect(model.selectedConversationID == AppModel.newConversationSelectionID)
        #expect(model.activeRun == nil)
        #expect(model.workspaceContextGeneration > generation)
        #expect(model.promptDraftsByContext[draftKey] == "Unsent draft")
        #expect(model.runSummariesByContext.values.contains(a))
        #expect(!model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "a" })
        model.applyWorkspaceConversationIndex([a, b], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == AppModel.newConversationSelectionID)
        #expect(!model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "a" })
        try model.restoreDeletedConversation(try #require(model.lastDeletedConversation))
        #expect(model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "a" })
        #expect(model.selectedConversationID == AppModel.newConversationSelectionID)
    }

    @Test
    func unrelatedRunningConversationAndOtherWindowsKeepTheirSelection() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let workspace = try workspace(lastRun: "b", status: "running")
        let first = model(store: store, workspace: workspace)
        let second = model(store: store, workspace: workspace)
        defer { first.stopWindowObservation(); second.stopWindowObservation() }
        let a = try run("a")
        let b = try run("b", status: "running")
        for model in [first, second] {
            model.applyWorkspaceConversationIndex([a, b], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
            model.selectedConversationID = "b"
            model.activeRun = b
        }
        #expect(first.conversationDeleteBlockReason(summary(a)) == nil)
        first.requestConversationDelete(summary(a))
        try first.deleteConversation(try #require(first.conversationDeleteRequest))
        for model in [first, second] {
            #expect(model.selectedConversationID == "b")
            #expect(model.activeRun == b)
            #expect(!model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "a" })
        }
    }

    @Test
    func liveActivityAndInFlightOperationsBlockOnlyTheirOwnConversation() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let model = model(store: ConversationTrashStore(fileURL: url), workspace: try workspace())
        defer { model.stopWindowObservation() }
        let a = summary(try run("a"))
        #expect(model.conversationDeleteBlockReason(a) == nil)
        model.activeRun = try run("a", status: "running")
        #expect(model.conversationDeleteBlockReason(a) != nil)
        model.activeRun = nil
        let key = model.machineRunContextKey(workspaceID: "trash-qa", runID: "a")
        model.activeRunOperationKeys.insert(key)
        #expect(model.conversationDeleteBlockReason(a) != nil)
        #expect(model.conversationDeleteBlockReason(summary(try run("b"))) == nil)
        model.activeRunOperationKeys.remove(key)
        model.workspaces = [try workspace(status: "running")]
        #expect(model.conversationDeleteBlockReason(a) != nil)
        model.workspaces = [try workspace()]
        #expect(model.conversationDeleteBlockReason(summary(try run("a", status: "cleanup_failed"))) != nil)
    }

    @Test
    func pendingSubmissionAndChangedWorkspaceCannotDeleteWrongConversation() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let model = model(store: store, workspace: try workspace())
        defer { model.stopWindowObservation() }
        let a = summary(try run("a"))
        model.pendingPromptSubmission = PersistedPromptSubmission(id: "qa", machineID: model.currentMachineContextID,
            workspaceID: "trash-qa", prompt: "Follow up", settings: model.settings, sourceRunID: "a", sourceAgent: nil,
            resumeSessionID: "", confirmLargeRun: false, attempt: 0, status: .pending, error: "")
        model.selectedConversationID = "b"
        #expect(model.conversationDeleteBlockReason(a) != nil)
        model.pendingPromptSubmission = nil
        model.requestConversationDelete(a)
        let request = try #require(model.conversationDeleteRequest)
        model.selectedWorkspaceID = "another-workspace"
        #expect(throws: (any Error).self) { try model.deleteConversation(request) }
        #expect(store.records.isEmpty)
    }

    @Test
    func lateLineageDiscoveryPreservesReadingButRequiresRestoreToContinue() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let workspace = try workspace(lastRun: "b")
        let model = model(store: store, workspace: workspace)
        defer { model.stopWindowObservation() }
        let a = summary(try run("a", conversation: "one"))
        try remove(a, from: store, machine: model.currentMachineContextID)
        let b = try run("b", conversation: "two")
        model.applyWorkspaceConversationIndex([b], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
        model.selectedConversationID = "two"
        model.activeRun = b
        let linked = try run("b", conversation: "two", previous: "a")
        model.applyWorkspaceConversationIndex([linked], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
        #expect(model.workspaceConversations.isEmpty)
        #expect(model.selectedConversationID == "two")
        #expect(model.activeRun == b)
        #expect(model.visibleDeletedConversation != nil)
        #expect(model.promptSubmissionPlan == .blocked("This conversation has been deleted. Restore it to continue."))
    }

    @Test
    func cachedWindowRestorationCannotReopenDeletedHistory() throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let workspace = try workspace()
        let model = model(store: store, workspace: workspace)
        defer { model.stopWindowObservation() }
        let run = try run("a")
        try remove(summary(run), from: store, machine: model.currentMachineContextID)
        model.selectedConversationID = "a"
        model.selectedConversationByWorkspace[model.machineWorkspaceContextKey(workspaceID: workspace.workspaceId)] = "a"
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID, workspaceID: workspace.workspaceId, runID: "a")
        let record = WorkspaceRunViewCacheRecord(key: key, runSummary: run,
            snapshot: WorkspaceRunViewSnapshot(selectedAgent: nil, transcripts: [:], conversationHistory: []))
        #expect(model.installCachedWorkspaceConversation([record], workspace: workspace, machineID: model.currentMachineContextID))
        #expect(model.workspaceConversations.isEmpty)
        #expect(model.selectedConversationID == AppModel.newConversationSelectionID)
        #expect(model.activeRun == nil)
        #expect(model.visibleDeletedConversation == nil)
    }

    @Test
    func directSelectionAndPollingCannotReopenDeletedHistory() async throws {
        let url = location()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = ConversationTrashStore(fileURL: url)
        let workspace = try workspace(lastRun: "b")
        let model = model(store: store, workspace: workspace)
        defer { model.stopWindowObservation() }
        let a = try run("a")
        let b = try run("b")
        model.applyWorkspaceConversationIndex([a, b], resumeSessions: [], workspace: workspace, machineID: model.currentMachineContextID)
        model.selectedConversationID = "b"
        model.activeRun = b
        try remove(summary(a), from: store, machine: model.currentMachineContextID)
        await model.selectConversation("a")
        model.recordRunInConversationIndex(a, select: true)
        #expect(model.selectedConversationID == "b")
        #expect(model.activeRun == b)
        #expect(!model.workspaceConversations.flatMap(\.runs).contains { $0.runId == "a" })
    }
}
