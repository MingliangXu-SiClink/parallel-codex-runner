import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceConversationNavigationTests {
    @Test func legacyDirectoryMigrationUsesContinuationOrderAndRetainsFullHistory() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-directory-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        model.workspaces = []; model.selectedWorkspaceID = nil
        model.selectedWorkspaceByMachine["local"] = "recent-navigation"
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let child = try run("child", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        let disk = WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache"))
        // Parent is touched last; LRU and equal createdAt must not choose it over the child.
        for run in [child, older] {
            #expect(disk.put(.init(key: .init(machineID: "local", workspaceID: "recent-navigation", runID: run.runId),
                runSummary: run, snapshot: .init(selectedAgent: nil, transcripts: [:],
                    conversationHistory: [.init(category: "output", text: "Earlier accepted answer")]))))
        }
        await model.restoreWorkspaceDirectoryFromCache(machineID: "local", requestID: model.refreshRequestID)
        #expect(model.activeRun?.runId == child.runId)
        #expect(model.selectedTranscriptBlocks.filter { $0.text == child.prompt }.count == 1)
        #expect(model.resumeHistory.last?.text == "Earlier accepted answer")
        #expect(model.workspaceDirectoryIsCached && model.selectedWorkspace?.writable == false)
    }

    @Test(arguments: [false, true])
    func emptyAuthoritativeDirectoryAndOtherMachineCachesNeverResurrectWorkspace(knownEmpty: Bool) async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-directory-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        model.workspaces = []; model.selectedWorkspaceID = nil
        model.selectedWorkspaceByMachine["local"] = "recent-navigation"
        if knownEmpty { model.persistWorkspaceDirectory([], machineID: "local"); model.workspaceDirectoryLoaded = false }
        let cached = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        #expect(WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache")).put(
            .init(key: .init(machineID: knownEmpty ? "local" : "other-machine", workspaceID: "recent-navigation", runID: cached.runId),
                runSummary: cached, snapshot: .init(selectedAgent: nil, transcripts: [:], conversationHistory: []))))
        await model.restoreWorkspaceDirectoryFromCache(machineID: "local", requestID: model.refreshRequestID)
        #expect(model.workspaces.isEmpty && model.activeRun == nil)
    }

    @Test func lateDirectoryHydrationCannotReplaceAnAuthoritativeDirectoryOrUserSelection() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-directory-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        model.workspaces = []; model.selectedWorkspaceID = nil
        model.selectedWorkspaceByMachine["local"] = "recent-navigation"
        let entered = DispatchGroup(), gate = DispatchSemaphore(value: 0)
        entered.enter()
        let blocker = Task { await ClientBlockingIO.cache { entered.leave(); gate.wait() } }
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async { entered.wait(); continuation.resume() }
        }
        let hydration = Task { await model.restoreWorkspaceDirectoryFromCache(machineID: "local", requestID: model.refreshRequestID) }
        await Task.yield()
        model.workspaces = [try workspace()]
        model.selectedWorkspaceID = "recent-navigation"
        model.persistWorkspaceDirectory(model.workspaces, machineID: "local")
        gate.signal(); await blocker.value; await hydration.value
        #expect(model.workspaces.count == 1 && model.workspaces.first?.available == true)
        #expect(model.workspaceDirectoryLoaded && !model.workspaceDirectoryIsCached)
        #expect(model.selectedWorkspaceID == "recent-navigation")
    }

    @Test func staleRegistryCannotRewindKnownContinuationButNewUnrelatedHeadWins() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-head-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        for run in [older, recent] {
            model.runSummariesByContext[.init(machineID: model.currentMachineContextID,
                workspaceID: "recent-navigation", runID: run.runId)] = run
        }
        model.workspaceRevision = 10
        model.workspaces = [try workspace().replacingRunSummary(with: recent, startingNewTurn: true)]
        #expect(model.reconciledWorkspaceRegistry([], revision: 9,
            machineID: model.currentMachineContextID).count == 1)
        #expect(model.reconciledWorkspaceRegistry([], revision: 11,
            machineID: model.currentMachineContextID).isEmpty)
        for revision in [9, 10, 11] {
            #expect(model.reconciledWorkspaceRegistry([try workspace()], revision: revision,
                machineID: model.currentMachineContextID).first?.lastRunId == recent.runId)
        }
        let other = try run("remote-new", updatedAt: "2026-10-05T00:00:00Z")
        let incoming = try workspace().replacingRunSummary(with: other, startingNewTurn: true)
        #expect(model.reconciledWorkspaceRegistry([incoming], revision: 11,
            machineID: model.currentMachineContextID).first?.lastRunId == other.runId)
        model.workspaces = [incoming]
        model.workspaceRevision = 11
        #expect(model.runSummariesByContext.values.allSatisfy { $0.runId != other.runId })
        #expect(model.reconciledWorkspaceRegistry([try workspace()], revision: 10,
            machineID: model.currentMachineContextID).first?.lastRunId == other.runId)
        #expect(model.reconciledWorkspaceRegistry([try workspace()], revision: 1,
            machineID: "other-machine").first?.lastRunId == older.runId)
    }

    @Test func coldWorkspaceEntryFindsCommittedChildInsteadOfHydratingOnlyRegistryParent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-head-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        let cache = WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache"))
        for run in [older, recent] {
            #expect(cache.put(.init(key: .init(machineID: "local", workspaceID: "recent-navigation", runID: run.runId),
                runSummary: run, snapshot: .init(selectedAgent: nil, transcripts: [:],
                    conversationHistory: [.init(category: "output", text: "Source answer")]))))
        }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        model.workspaceEntryDefaultNavigationRequestID = model.conversationNavigationRequestID
        await model.restoreCachedWorkspaceConversation(workspace: try workspace(), machineID: model.currentMachineContextID,
            generation: model.workspaceContextGeneration)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.selectedTranscriptBlocks.contains { $0.text == recent.prompt })
        #expect(model.resumeHistory.last?.text == "Source answer")
    }

    @Test func cacheRevalidationPreservesExplicitHistoricalTurn() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-head-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        model.activeRun = older
        model.workspaceConversations = workspaceConversationSummaries(from: [older, recent])
        model.selectedConversationID = "shared"
        model.conversationNavigationTarget = .init(requestID: model.conversationNavigationRequestID,
            machineID: model.currentMachineContextID, workspaceID: "recent-navigation", conversationID: "shared",
            runID: older.runId, followsLatest: false)
        model.installCachedWorkspaceConversation([older, recent].map { run in
            .init(key: .init(machineID: model.currentMachineContextID, workspaceID: "recent-navigation", runID: run.runId),
                runSummary: run, snapshot: .init(selectedAgent: nil, transcripts: [:], conversationHistory: []))
        }, workspace: try workspace(), machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == older.runId)
        #expect(model.workspaces.first?.lastRunId == recent.runId)
        model.cacheVisibleRunViewState()
        model.selectedWorkspaceID = "other"
        await model.selectWorkspace("recent-navigation")
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.workspaces.first?.lastRunId == recent.runId)
        #expect(model.selectedTranscriptBlocks.contains { $0.text == recent.prompt })
    }

    @Test func evictedLatestBodyCannotReopenTheCachedCompletedParent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-head-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        let oldKey = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: older.runId)
        let latestKey = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: recent.runId)
        model.runSummariesByContext[oldKey] = older
        model.runSummariesByContext[latestKey] = recent
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: []),
            runSummary: older, for: oldKey)
        model.selectedWorkspaceID = "other"
        await model.selectWorkspace("recent-navigation")
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.selectedConversation?.latestRunID == recent.runId)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.selectedTranscriptBlocks.contains { $0.text == recent.prompt })
    }

    @Test func aCachedBootstrapCannotReplaceAnAlreadyVisibleNewerProjection() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-head-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let old = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let fresh = try run("older", updatedAt: "2026-10-04T00:00:00Z")
        model.activeRun = fresh
        model.workspaceConversations = workspaceConversationSummaries(from: [fresh])
        model.selectedConversationID = fresh.conversationKey
        model.resumeHistory = [.init(category: "output", text: "Already visible history")]
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: old.runId)
        model.installCachedWorkspaceConversation([.init(key: key, runSummary: old,
            snapshot: .init(selectedAgent: nil, transcripts: [:], conversationHistory: []))],
            workspace: try workspace(), machineID: model.currentMachineContextID)
        #expect(model.activeRun?.updatedAt == fresh.updatedAt)
        #expect(model.resumeHistory.last?.text == "Already visible history")
        let newer = try run("older", updatedAt: "2026-10-05T00:00:00Z")
        model.installCachedWorkspaceConversation([.init(key: key, runSummary: newer,
            snapshot: .init(selectedAgent: nil, transcripts: [:], conversationHistory: []))],
            workspace: try workspace(), machineID: model.currentMachineContextID)
        #expect(model.activeRun?.updatedAt == newer.updatedAt)
        #expect(model.resumeHistory.last?.text == "Already visible history")
        #expect(model.selectedTranscriptBlocks.contains { $0.text == newer.prompt })
    }

    private func workspace() throws -> RemoteWorkspace {
        try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"recent-navigation","name":"Recent navigation QA","path":"/recent-navigation",
         "lastRunId":"older","lastResumeSessionId":"","selected":true,
         "available":true,"readable":true,"writable":true}
        """.utf8))
    }

    private func run(_ id: String, updatedAt: String, conversationID: String? = nil, turnIndex: Int = 0) throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"\(id)","conversationId":"\(conversationID ?? "conversation-\(id)")","turnIndex":\(turnIndex),"status":"completed",
         "workspace":"/recent-navigation","prompt":"Question \(id)","createdAt":"2026-10-01T00:00:00Z",
         "updatedAt":"\(updatedAt)","config":{},"syncedBack":false,"workspacesDeleted":false,
         "codexHomesDeleted":false,"workerActive":false,"workerAvailable":true,"eventCount":0,"agents":[]}
        """.utf8))
    }

    private func model(directory: URL) throws -> AppModel {
        let model = AppModel(
            client: PcrdClient(socketURL: directory.appendingPathComponent("unused.sock"),
                               tokenURL: directory.appendingPathComponent("unused.token")),
            windowID: UUID(),
            desktopStateStore: DesktopStateStore(windowID: UUID(), directory: directory),
            runViewStateStore: WorkspaceRunViewStateStore(capacity: 1,
                diskCache: WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache")))
        )
        model.workspaces = [try workspace()]
        model.selectedWorkspaceID = "recent-navigation"
        return model
    }

    @Test func enteringWorkspaceOpensRecentConversationInsteadOfSavedOlderConversation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-recent-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        model.selectedConversationByWorkspace[model.machineWorkspaceContextKey(workspaceID: workspace.workspaceId)]
            = "conversation-older"
        model.resetRunView()
        model.applyWorkspaceConversationIndex([older, recent], resumeSessions: [], workspace: workspace,
                                               machineID: model.currentMachineContextID)
        #expect(model.workspaceConversations.first?.id == "conversation-recent")
        #expect(model.selectedConversationID == "conversation-recent")
    }

    @Test func conversationRecencyUsesInstantsInsteadOfTimestampSpellings() throws {
        let earlier = try run("earlier", updatedAt: "2026-10-04T14:00:00+08:00")
        let later = try run("later", updatedAt: "2026-10-04T07:00:00Z")
        let fractional = try run("fractional", updatedAt: "2026-10-04T07:00:00.500+00:00")
        #expect(workspaceConversationSummaries(from: [later, earlier, fractional]).map(\.id)
            == ["conversation-fractional", "conversation-later", "conversation-earlier"])
    }

    @Test func backgroundIndexRefreshPreservesExplicitConversationAndDraft() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-recent-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        model.selectedConversationID = "conversation-older"
        model.workspaceEntryDefaultNavigationRequestID = model.conversationNavigationRequestID
        model.updatePrompt("Draft for the older conversation")
        #expect(model.workspaceEntryDefaultNavigationRequestID == nil)
        model.applyWorkspaceConversationIndex([older, recent], resumeSessions: [], workspace: workspace,
                                               machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == "conversation-older")
        #expect(model.prompt == "Draft for the older conversation")
    }

    @Test func aPartialIndexCannotReplaceExplicitNavigationOrNewConversation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-recent-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        model.selectedConversationID = "conversation-on-an-older-page"
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
                                               machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == "conversation-on-an-older-page")
        model.selectedConversationID = AppModel.newConversationSelectionID
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
                                               machineID: model.currentMachineContextID)
        #expect(model.isComposingNewConversation)
    }

    @Test func evictingTheBodyDoesNotForgetTheKnownWorkspaceDestination() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let run = try run("older", updatedAt: "2026-10-04T00:00:00Z")
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: run.runId)
        model.runSummariesByContext[key] = run
        model.selectedWorkspaceID = "another-workspace"
        await model.selectWorkspace("recent-navigation")
        #expect(model.activeRun?.runId == run.runId)
        #expect(model.selectedConversation?.runs.contains { $0.runId == run.runId } == true)
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptSubmissionPlan != .loading)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func nativeHistoryReentryBindsItsOwnSessionBeforeAnyIndexOrHistoryRead() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let key = resumeConversationViewKey(machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId, sessionID: "selected-native-session")
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "The saved native answer")]), for: key)
        var settings = RunSettings()
        settings.resumeSessionID = "a-different-saved-session"
        model.settingsByWorkspace[model.machineWorkspaceContextKey(workspaceID: workspace.workspaceId)] = settings
        model.selectedConversationByWorkspace[model.machineWorkspaceContextKey(workspaceID: workspace.workspaceId)]
            = "resume:selected-native-session"
        model.selectedWorkspaceID = "another-workspace"
        await model.selectWorkspace(workspace.workspaceId)
        #expect(model.selectedConversation?.resumeSession?.sessionId == "selected-native-session")
        #expect(model.settings.resumeSessionID == "selected-native-session")
        #expect(model.resumeHistory.last?.text == "The saved native answer")
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptSubmissionPlan != .loading)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func indexSelectionAndItsReadyTurnAreOneStateChange() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        model.activeRun = older
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId, runID: recent.runId)
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "Correct recent history")]), runSummary: recent, for: key)
        model.applyWorkspaceConversationIndex([older, recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == "conversation-recent")
        #expect(model.activeRun?.runId == "recent")
        #expect(model.resumeHistory.last?.text == "Correct recent history")
        #expect(model.promptDestinationIsResolved)
    }

    @Test(arguments: [false, true])
    func theLatestTurnAndItsHistoryAdvanceTogetherWithinTheSameConversation(alreadyIndexed: Bool) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-same-conversation-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        model.workspaceConversations = workspaceConversationSummaries(from: alreadyIndexed ? [older, recent] : [older])
        model.selectedConversationID = "shared"
        model.activeRun = older
        let history = [ResumeHistoryEntry(category: "output", text: "The complete saved prior conversation")]
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: history,
            historyLoaded: true), runSummary: recent, for: .init(machineID: model.currentMachineContextID,
                workspaceID: workspace.workspaceId, runID: recent.runId))
        model.preparePromptHistoryContext()
        model.updatePrompt("Keep this conversation's draft")
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == "shared")
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(model.isViewingLatestConversationTurn)
        #expect(model.prompt == "Keep this conversation's draft")
    }

    @Test func anUnresolvedIndexIsNotEvidenceThatTheVisibleTurnIsHistorical() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-unresolved-index-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        model.activeRun = recent
        model.selectedConversationID = "shared"
        #expect(!model.isViewingEarlierConversationTurn)
        model.workspaceConversations = workspaceConversationSummaries(from: [older, recent])
        #expect(!model.isViewingEarlierConversationTurn)
        model.activeRun = older
        #expect(!model.isViewingEarlierConversationTurn)
        model.historicalConversationTurn = .init(machineID: model.currentMachineContextID,
            workspaceID: try workspace().workspaceId, runID: older.runId)
        #expect(model.isViewingEarlierConversationTurn)
    }

    @Test func explicitHistoryStaysPinnedButReturningToLatestRestoresItsCompleteCache() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-explicit-history-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        model.workspaceConversations = workspaceConversationSummaries(from: [older, recent])
        model.selectedConversationID = "shared"
        model.activeRun = recent
        let history = [ResumeHistoryEntry(category: "output", text: "Complete cached latest answer", id: "latest")]
        for run in [older, recent] {
            model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory:
                run.runId == recent.runId ? history : [], historyLoaded: true), runSummary: run,
                for: .init(machineID: model.currentMachineContextID, workspaceID: workspace.workspaceId, runID: run.runId))
        }
        await model.selectConversationTurn(older.runId)
        #expect(model.activeRun?.runId == older.runId)
        #expect(model.isViewingEarlierConversationTurn)
        model.applyWorkspaceConversationIndex([older, recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == older.runId)
        await model.selectConversationTurn(recent.runId)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(!model.isViewingEarlierConversationTurn)
    }

    @Test func aLatestNavigationAdvancesWhenItsIndexChangesBeforeTheReadFinishes() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-latest-race-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        model.workspaceConversations = workspaceConversationSummaries(from: [older])
        model.selectedConversationID = "shared"
        model.activeRun = older
        let request = model.conversationNavigationRequestID
        model.conversationNavigationTarget = .init(requestID: request, machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId, conversationID: "shared", runID: older.runId, followsLatest: true)
        let history = [ResumeHistoryEntry(category: "output", text: "Complete destination history")]
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: history,
            historyLoaded: true), runSummary: recent, for: .init(machineID: model.currentMachineContextID,
                workspaceID: workspace.workspaceId, runID: recent.runId))
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.promptDestinationIsResolved)
        #expect(!model.navigationAcceptsRun(older.runId, requestID: request))
        #expect(model.navigationAcceptsRun(recent.runId, requestID: request))
        model.conversationNavigationTarget = nil
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func anIndexedSameRunRestoresItsDiskHistoryBeforeAnOfflineSummaryRead() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-thin-cache-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let run = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: run.runId)
        let history = [ResumeHistoryEntry(category: "output", text: "Already saved full history")]
        let disk = WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache"))
        #expect(disk.put(.init(key: key, runSummary: run,
            snapshot: .init(selectedAgent: nil, transcripts: [:], conversationHistory: history, historyLoaded: true))))
        model.workspaceConversations = workspaceConversationSummaries(from: [run])
        model.selectedConversationID = run.conversationKey
        #expect(model.installIndexedConversationProjection(try #require(model.selectedConversation), workspace: try workspace()))
        #expect(model.resumeHistory.isEmpty)
        do { try await model.loadRun(run.runId, loadDetailPages: 1) } catch { }
        #expect(model.resumeHistory == history)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func aLateDiskReadCannotInstallTheFormerLatestTurn() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-disk-race-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 1)
        let disk = WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache"))
        let oldKey = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId, runID: older.runId)
        #expect(disk.put(.init(key: oldKey, runSummary: older,
            snapshot: .init(selectedAgent: nil, transcripts: [:],
                conversationHistory: [.init(category: "output", text: "Obsolete visible page")]))))
        model.workspaceConversations = workspaceConversationSummaries(from: [older])
        model.selectedConversationID = AppModel.newConversationSelectionID
        let gate = DispatchGroup(), entered = DispatchGroup()
        gate.enter(); entered.enter()
        var released = false
        defer { if !released { gate.leave() } }
        let blocker = Task { await ClientBlockingIO.cache { entered.leave(); gate.wait() } }
        func hasEntered() -> Bool { entered.wait(timeout: .now()) == .success }
        while !hasEntered() { try await Task.sleep(for: .milliseconds(5)) }
        let navigation = Task { await model.selectConversation("shared") }
        let deadline = ContinuousClock.now + .seconds(2)
        while model.conversationNavigationTarget == nil, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(5))
        }
        try #require(model.conversationNavigationTarget?.followsLatest == true)
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        let history = [ResumeHistoryEntry(category: "output", text: "Latest complete history")]
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: history,
            historyLoaded: true), runSummary: recent, for: .init(machineID: oldKey.machineID,
                workspaceID: oldKey.workspaceID, runID: recent.runId))
        gate.leave(); released = true
        await blocker.value
        await navigation.value
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory == history)
        #expect(!model.isViewingEarlierConversationTurn)
        #expect(model.conversationNavigationTarget == nil)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test(arguments: [false, true])
    func aNewIndexDoesNotAdvanceAnExplicitHistoricalTurnOrItsPendingNavigation(pendingNavigation: Bool) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-same-conversation-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z", conversationID: "shared")
        let middle = try run("middle", updatedAt: "2026-10-02T00:00:00Z", conversationID: "shared", turnIndex: 1)
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z", conversationID: "shared", turnIndex: 2)
        model.workspaceConversations = workspaceConversationSummaries(from: [older, middle])
        model.selectedConversationID = "shared"
        model.activeRun = pendingNavigation ? middle : older
        if !pendingNavigation {
            model.historicalConversationTurn = .init(machineID: model.currentMachineContextID,
                workspaceID: workspace.workspaceId, runID: older.runId)
        }
        if pendingNavigation {
            model.conversationNavigationTarget = .init(requestID: model.conversationNavigationRequestID,
                machineID: model.currentMachineContextID, workspaceID: workspace.workspaceId,
                conversationID: "shared", runID: older.runId)
        }
        model.resumeHistory = [.init(category: "output", text: "Keep the selected history")]
        model.applyWorkspaceConversationIndex([recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == (pendingNavigation ? middle.runId : older.runId))
        #expect(model.resumeHistory.last?.text == "Keep the selected history")
    }

    @Test func repairingAConversationAliasDoesNotHoldAnAlreadyBoundRunBehindItsRead() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let run = try run("older", updatedAt: "2026-10-04T00:00:00Z")
        model.activeRun = run
        model.selectedConversationID = "older"
        model.conversationNavigationTarget = .init(requestID: model.conversationNavigationRequestID,
            machineID: model.currentMachineContextID, workspaceID: workspace.workspaceId,
            conversationID: "older", runID: run.runId)
        model.applyWorkspaceConversationIndex([run], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.selectedConversationID == "conversation-older")
        #expect(model.conversationNavigationTarget?.conversationID == "conversation-older")
        #expect(model.conversationNavigationTarget?.runID == run.runId)
        #expect(model.promptDestinationIsResolved)
    }

    @Test func leavingAWorkspaceInvalidatesTheOldNavigationEvenWhenReturningToIt() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let request = model.conversationNavigationRequestID
        model.conversationNavigationTarget = .init(requestID: request, machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", conversationID: "older", runID: "older")
        model.resetRunView()
        #expect(model.conversationNavigationRequestID > request)
        #expect(model.conversationNavigationTarget == nil)
    }

    @Test func capturedWorkspaceMetadataCanRestoreTheExactDestinationWithoutItsBodyWhenProvided() async throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_WORKSPACE_REENTRY_FIXTURE"] else { return }
        let json = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let entries = try #require(json["workspaces"] as? [[String: Any]])
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        for entry in entries {
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-production-reentry-\(UUID())")
            defer { try? FileManager.default.removeItem(at: directory) }
            let model = try model(directory: directory)
            defer { model.stopWindowObservation() }
            let workspace = try decoder.decode(RemoteWorkspace.self,
                from: JSONSerialization.data(withJSONObject: try #require(entry["workspace"])))
            let runs = try decoder.decode(RunListResponse.self,
                from: JSONSerialization.data(withJSONObject: try #require(entry["runs"])))
            let run = try #require(runs.runs.first { $0.runId == workspace.lastRunId })
            model.workspaces = [workspace]
            model.selectedWorkspaceID = "another-workspace"
            model.runSummariesByContext[WorkspaceRunViewKey(machineID: model.currentMachineContextID,
                workspaceID: workspace.workspaceId, runID: run.runId)] = run
            await model.selectWorkspace(workspace.workspaceId)
            #expect(model.activeRun?.runId == workspace.lastRunId)
            #expect(model.activeRun?.config.submissionCommand == run.config.submissionCommand)
            #expect(model.selectedConversation?.runs.contains { $0.runId == run.runId } == true)
            #expect(model.promptDestinationIsResolved)
            #expect(model.promptOutboxByWorkspace.isEmpty)
        }
    }

    @Test(arguments: ["workspace", "run"])
    func aMismatchedKnownSummaryCannotBecomeTheSubmissionDestination(_ conflict: String) async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let run = try run("older", updatedAt: "2026-10-04T00:00:00Z")
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        json[conflict == "workspace" ? "workspace" : "runId"] = "different-destination"
        model.runSummariesByContext[.init(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: "older")] = try JSONDecoder().decode(RunSummary.self,
                from: JSONSerialization.data(withJSONObject: json))
        model.selectedWorkspaceID = "another-workspace"
        await model.selectWorkspace("recent-navigation")
        #expect(model.activeRun == nil)
        #expect(!model.promptDestinationIsResolved)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func aDelayedOldTurnCannotReopenAfterLeavingAndReenteringTheSameWorkspace() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: "recent-navigation", runID: older.runId)
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "The old turn")]), runSummary: older, for: key)
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:], conversationHistory: []),
            runSummary: recent, for: .init(machineID: key.machineID, workspaceID: key.workspaceID, runID: recent.runId))
        let conversation = WorkspaceConversationSummary(id: "conversation", title: "Conversation",
            runs: [older, recent], resumeSession: nil)
        model.workspaceConversations = [conversation]
        model.selectedConversationID = conversation.id
        model.activeRun = recent
        let gate = DispatchGroup()
        let entered = DispatchGroup()
        gate.enter(); entered.enter()
        var released = false
        defer { if !released { gate.leave() } }
        let blocker = Task { await ClientBlockingIO.cache { entered.leave(); gate.wait() } }
        func hasEntered() -> Bool { entered.wait(timeout: .now()) == .success }
        while !hasEntered() { try await Task.sleep(for: .milliseconds(5)) }
        let navigation = Task { await model.selectConversationTurn(older.runId) }
        let deadline = ContinuousClock.now + .seconds(2)
        while model.conversationNavigationTarget?.runID != older.runId, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(5))
        }
        try #require(model.conversationNavigationTarget?.runID == older.runId)
        model.resetRunView()
        model.selectedWorkspaceID = key.workspaceID
        model.workspaceConversations = [conversation]
        model.selectedConversationID = conversation.id
        model.activeRun = recent
        model.resumeHistory = [.init(category: "output", text: "The new visible answer")]
        model.updatePrompt("The returning conversation's draft")
        gate.leave(); released = true
        await blocker.value
        await navigation.value
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.resumeHistory.last?.text == "The new visible answer")
        #expect(model.prompt == "The returning conversation's draft")
        #expect(model.conversationNavigationTarget == nil)
    }

    @Test(arguments: ["workspace", "run"])
    func anInvalidCachedProjectionCannotDisplaceTheIndexedDestination(_ conflict: String) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-reentry-\(UUID())")
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = try model(directory: directory)
        defer { model.stopWindowObservation() }
        let workspace = try workspace()
        let older = try run("older", updatedAt: "2026-10-01T00:00:00Z")
        let recent = try run("recent", updatedAt: "2026-10-04T00:00:00Z")
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(recent)) as? [String: Any])
        json[conflict == "workspace" ? "workspace" : "runId"] = "another-destination"
        let wrong = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        model.activeRun = older
        model.runViewStateStore.put(.init(selectedAgent: nil, transcripts: [:],
            conversationHistory: [.init(category: "output", text: "An unrelated cached answer")]), runSummary: wrong,
            for: .init(machineID: model.currentMachineContextID, workspaceID: workspace.workspaceId, runID: recent.runId))
        model.applyWorkspaceConversationIndex([older, recent], resumeSessions: [], workspace: workspace,
            machineID: model.currentMachineContextID)
        #expect(model.activeRun?.runId == recent.runId)
        #expect(model.activeRun?.workspace == workspace.path)
        #expect(model.resumeHistory.isEmpty)
        #expect(model.promptDestinationIsResolved)
        #expect(model.promptOutboxByWorkspace.isEmpty)
    }
}
