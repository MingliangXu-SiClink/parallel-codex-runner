import Darwin
import AppKit
import CryptoKit
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct AgentPreloadTests {
    @Test
    func stoppedMoreKeepsReadingEachSynthesisAcrossPolling() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        let run = try await installStoppedMore(fixture, stale: true)
        for agent in [9, 10, 11, 9] {
            await model.selectAgent(agent)
            #expect(model.selectedAgent == agent)
            try await model.loadRun(fixture.runID, loadDetailPages: 1)
            #expect(model.selectedAgent == agent)
            #expect(model.transcripts[agent]?.finalMessage == "Synthesis \(agent - 8)")
            #expect(model.rememberedSelectedAgent(for: run,
                contextKey: try #require(model.selectedAgentContextKey())) == agent)
        }
        #expect(await fixture.replies.selectionRequests.isEmpty)
        #expect(await fixture.replies.continuationRequests.isEmpty)
        #expect(model.activeRun?.agents.filter { $0.normalizedStatus == "cancelled" }.map(\.agent) == [12, 13])
    }

    @Test
    func readingOnlySynthesisSurvivesClientStateReload() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let run = try await installStoppedMore(fixture, stale: true)
        await fixture.model.selectAgent(9)
        #expect(fixture.model.desktopStateStore.flushPendingWrites())
        let restored = AppModel(client: fixture.model.client, windowID: fixture.windowID,
            desktopStateStore: DesktopStateStore(windowID: fixture.windowID, directory: fixture.directory))
        defer { restored.stopWindowObservation() }
        restored.selectedWorkspaceID = fixture.workspaceID
        restored.activeRun = run
        restored.daemonFollowUpSnapshot = fixture.model.daemonFollowUpSnapshot
        let key = try #require(restored.selectedAgentContextKey())
        #expect(restored.rememberedSelectedAgent(for: run, contextKey: key) == 9)
    }

    @Test
    func lateQueueConfirmationCannotReplaceAReadingOnlySynthesis() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        _ = try await installStoppedMore(fixture, stale: true)
        await model.selectAgent(9)
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        #expect(model.selectedAgent == 9)
        #expect(model.promptContinuationAgentSummary?.agent == 9)
        model.daemonOnline = true
        model.prompt = "Do not silently send this to the old branch"
        let plan = model.promptSubmissionPlan
        #expect(plan == .blocked("Select a successful Agent to continue"))
        #expect(await fixture.replies.continuationRequests.isEmpty)
    }

    @Test
    func revalidatedSynthesisContinuesFromItsPhysicalIDWithoutResendingStoppedMore() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        _ = try await installStoppedMore(fixture, stale: false)
        model.daemonOnline = true
        await fixture.replies.holdSelection(10)
        await model.selectAgent(10)
        try await waitUntil { await fixture.replies.selectionRequests == [10] }
        model.updatePrompt("Continue the second synthesis")
        #expect(model.canSubmitPrompt)
        await model.submitPrompt()
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.continuationRequests == [10] }
        let submission = try #require(model.promptOutboxByWorkspace.values.first)
        #expect(submission.sourceAgent == 10)
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        #expect(model.selectedAgent == 10)
        #expect(Set(await fixture.replies.continuationCommandIDs) == [submission.commandID])
        #expect(model.activeRun?.agents.first { $0.agent == 12 }?.status == "cancelled")
        #expect(model.activeRun?.agents.first { $0.agent == 13 }?.status == "cancelled")
    }

    private func installStoppedMore(_ fixture: Fixture, stale: Bool) async throws -> RunSummary {
        let model = fixture.model
        let original = try #require(model.activeRun)
        var payload = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        payload["status"] = "cancelled"
        payload["agents"] = (1...13).map { agent -> [String: Any] in
            let synthesis = (9...11).contains(agent)
            return ["agent": agent, "name": "Agent \(agent)",
                "role": synthesis ? "synthesis" : "candidate", "status": agent > 11 ? "cancelled" : "success",
                "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false,
                "stale": synthesis && stale, "canContinue": agent <= 11 && (!synthesis || !stale)]
        }
        let run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: payload))
        model.activeRun = run
        model.selectedAgent = 11
        for agent in 9...11 {
            model.transcripts[agent] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: agent,
                cursor: 2, hasMore: false, finalMessage: "Synthesis \(agent - 8)", finalMessageHasMore: false,
                finalMessageReadStatus: "success")
        }
        let queue = RemoteFollowUpQueue(workspaceId: fixture.workspaceID, runId: fixture.runID,
            items: [], selectedAgent: 11, state: "waiting", deadlineEpochMs: nil,
            lastError: nil, generation: 1, lastCompletedItemId: nil, selectionSource: "automatic")
        model.daemonFollowUpSnapshot = queue
        model.daemonFollowUpSnapshotsByWorkspace[MachineContextKey.workspace(
            machineID: model.currentMachineContextID, workspaceID: fixture.workspaceID)] = queue
        try await fixture.replies.installRun(run)
        return run
    }

    @Test
    func cancellingBeforeConfirmationStartsCannotSpin() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        await fixture.model.selectAgent(2)
        let task = fixture.model.continuationSelectionTasks.values.first
        fixture.model.stopWindowObservation()
        await task?.value
        #expect(fixture.model.continuationSelectionTasks.isEmpty)
        #expect(fixture.model.continuationSelectionTaskIDs.isEmpty)
        #expect(fixture.model.selectedAgent == 2)
    }
    @Test
    func defaultWarmSwitchDisplaysBeforeSelectionReceiptAndSerializesRapidChoices() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        #expect(model.selectedAgent == 2)
        #expect(model.pendingAgentSelection == nil)
        #expect(model.transcripts[2]?.finalMessage == "Candidate 2")
        #expect(model.continuationSelectionNeedsConfirmation)
        #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 1)
        #expect(model.continuationSelectionContextText == "Confirming the continuation source")
        model.daemonOnline = true
        #expect(model.promptComposerContextText == "The next message continues from Agent 2")
        await model.selectAgent(4)
        await model.selectAgent(3)
        try await Task.sleep(for: .milliseconds(100))
        #expect(model.selectedAgent == 3)
        #expect(await fixture.replies.selectionRequests == [2])
        #expect(model.rememberedSelectedAgent(for: try #require(model.activeRun),
            contextKey: try #require(model.selectedAgentContextKey())) == 3)
        await fixture.replies.releaseSelection()
        try await waitUntil { !model.continuationSelectionNeedsConfirmation }
        #expect(await fixture.replies.selectionRequests == [2, 3])
        #expect(model.selectedAgent == 3)
        #expect(model.daemonFollowUpSnapshot?.selectedAgent == 3)
        #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 3)
        #expect(await fixture.replies.requests.isEmpty)
    }

    @Test
    func returningToTheConfirmedAgentWhileAnotherChoiceIsInFlightStillConfirmsTheLastClick() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await model.selectAgent(2)
        try await waitUntil { !model.continuationSelectionNeedsConfirmation }
        await fixture.replies.holdSelection(3)
        await model.selectAgent(3)
        try await waitUntil { await fixture.replies.selectionRequests == [2, 3] }
        await model.selectAgent(2)
        #expect(model.selectedAgent == 2)
        #expect(model.continuationSelectionNeedsConfirmation)
        await fixture.replies.releaseSelection()
        try await waitUntil { !model.continuationSelectionNeedsConfirmation }
        #expect(await fixture.replies.selectionRequests == [2, 3, 2])
        #expect(model.daemonFollowUpSnapshot?.selectedAgent == 2)
        #expect(model.selectedAgent == 2)
    }

    @Test
    func historicalReceiptCannotOverrideANewerQueueOrConfirmItsOldSource() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        model.applyDaemonFollowUpQueue(RemoteFollowUpQueue(workspaceId: fixture.workspaceID,
            runId: fixture.runID, items: [], selectedAgent: 6, state: "dispatching",
            deadlineEpochMs: nil, lastError: nil, generation: 2, lastCompletedItemId: nil,
            selectionSource: "manual"),
            workspaceID: fixture.workspaceID, machineID: model.currentMachineContextID, revision: 100)
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        #expect(model.selectedAgent == 2)
        #expect(model.daemonFollowUpSnapshot?.selectedAgent == 6)
        #expect(model.daemonFollowUpSnapshot?.state == "dispatching")
        #expect(model.continuationSelectionNeedsConfirmation)
        #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 6)
    }

    @Test
    func duplicateSendsDuringQueueSelectionPersistOneExplicitSubmissionIntent() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        model.daemonOnline = true
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        model.updatePrompt("Keep this unsent draft")
        #expect(model.canSubmitPrompt)
        await model.submitPrompt()
        let original = try #require(model.promptOutboxByWorkspace.values.first)
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.continuationRequests == [2] }
        #expect(model.promptPreparationContexts.isEmpty)
        #expect(model.promptOutboxByWorkspace.count == 1)
        #expect(original.sourceAgent == 2)
        #expect(model.prompt.isEmpty)
        await fixture.replies.rejectSelection()
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        #expect(model.promptPreparationContexts.isEmpty)
        #expect(model.promptOutboxByWorkspace.values.first?.commandID == original.commandID)
        #expect(Set(await fixture.replies.continuationCommandIDs) == [original.commandID])
    }

    @Test
    func uncertainSelectionReceiptUsesOneCommandIdentityWithoutBlockingReading() async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        await fixture.replies.dropSelectionReceipts(2)
        await fixture.model.selectAgent(2)
        #expect(fixture.model.selectedAgent == 2)
        try await waitUntil { !fixture.model.continuationSelectionNeedsConfirmation }
        let ids = await fixture.replies.selectionCommandIDs
        #expect(ids.count == 3)
        #expect(Set(ids).count == 1)
        #expect(fixture.model.selectedAgent == 2)
        #expect(fixture.model.errorNotice == nil)
    }

    @Test(arguments: [false, true])
    func readingNeverPretendsARejectedOrPreviousContextSelectionWasConfirmed(changeContext: Bool) async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        if changeContext {
            model.workspaceContextGeneration += 1
            model.selectedWorkspaceID = "other"
            model.selectedAgent = 6
        } else {
            await fixture.replies.rejectSelection()
        }
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        #expect(model.selectedAgent == (changeContext ? 6 : 2))
        if !changeContext {
            #expect(model.continuationSelectionNeedsConfirmation)
            #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 1)
        }
        #expect(model.errorNotice == nil)
    }

    @Test
    func workingAndAdoptedBranchClicksRemainReadOnly() async throws {
        let fixture = try Fixture(modernSelection: true, running: true)
        defer { fixture.close() }
        await fixture.model.selectAgent(2)
        let run = try #require(fixture.model.activeRun)
        var payload = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        payload["status"] = "completed"
        payload["finalizedAgent"] = 1
        payload["workerActive"] = false
        fixture.model.activeRun = try JSONDecoder().decode(RunSummary.self,
            from: JSONSerialization.data(withJSONObject: payload))
        try await fixture.replies.installRun(try #require(fixture.model.activeRun))
        await fixture.model.selectAgent(3)
        try await Task.sleep(for: .milliseconds(50))
        #expect(await fixture.replies.selectionRequests.isEmpty)
        #expect(fixture.model.selectedAgent == 3)
        #expect(fixture.model.remoteFollowUpSelection(runID: fixture.runID) == 1)
    }

    @Test(arguments: [false, true])
    func queueConfirmationCannotRetargetASavedMessageOrReplaceALaterDraft(changeContext: Bool) async throws {
        let fixture = try Fixture(modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        model.daemonOnline = true
        await fixture.replies.holdSelection(2)
        await model.selectAgent(2)
        try await waitUntil { await fixture.replies.selectionRequests == [2] }
        model.updatePrompt("Keep this unsent draft")
        #expect(model.canSubmitPrompt)
        await model.submitPrompt()
        let original = try #require(model.promptOutboxByWorkspace.values.first)
        #expect(original.sourceAgent == 2)
        #expect(model.prompt.isEmpty)
        try await waitUntil { await fixture.replies.continuationRequests == [2] }
        if changeContext {
            model.workspaceContextGeneration += 1
            model.selectedWorkspaceID = "other"
            model.restoreVisiblePromptSubmission()
            model.updatePrompt("Other conversation draft")
        } else {
            model.updatePrompt("A later draft")
            await fixture.replies.rejectSelection()
        }
        await fixture.replies.releaseSelection()
        try await waitUntil { model.continuationSelectionTasks.isEmpty }
        #expect(model.prompt == (changeContext ? "Other conversation draft" : "A later draft"))
        #expect(model.promptOutboxByWorkspace.values.first?.sourceAgent == 2)
        #expect(model.promptOutboxByWorkspace.values.first?.commandID == original.commandID)
        #expect(Set(await fixture.replies.continuationCommandIDs) == [original.commandID])
    }

    @Test
    func openingARunPreloadsEveryCandidateAndWarmSwitchesNeedNoRPC() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let original = model.selectedTimelineItems
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        try await waitUntil { (2...6).allSatisfy { model.transcripts[$0]?.cursor == 2 } }
        #expect(model.selectedAgent == 1)
        #expect(model.pendingAgentSelection == nil)
        #expect(model.selectedTimelineItems == original)
        #expect(model.loadingAgentKeys.isEmpty)
        for reader in Array(model.conversationReadTasks.values) { await reader.value }
        let preparedRequests = await fixture.replies.requests
        #expect(Set(preparedRequests).isSuperset(of: Set(2...6)))
        let document = WorkspaceFileDocument(name: "answer.md", content: "Original file",
            fileURL: URL(fileURLWithPath: "/qa/answer.md"), relativePath: "answer.md", line: nil,
            context: ReviewContext(target: .local, workspaceID: "workspace", runID: fixture.runID, agent: 1))
        model.workspaceDocument = document
        for agent in [6, 2, 5, 1, 4, 3] {
            await model.selectAgent(agent, manageFollowUpQueue: false)
            #expect(model.selectedAgent == agent)
            #expect(model.agentTranscriptReadyForDisplay(agent))
            #expect(!model.isSelectedTimelineLoading)
            #expect(model.workspaceDocument?.id == document.id)
            #expect(model.workspaceDocument?.context?.agent == 1)
        }
        try await Task.sleep(for: .milliseconds(100))
        #expect(await fixture.replies.requests == preparedRequests)
    }

    @Test(arguments: [false, true])
    func cancelledOrPreviousWorkspacePreloadCannotPublish(changeWorkspace: Bool) async throws {
        let fixture = try Fixture(pausedAgent: 2)
        defer { fixture.close() }
        let model = fixture.model
        model.scheduleVisibleAgentPreload()
        let key = WorkspaceRunViewKey(machineID: model.currentMachineContextID,
            workspaceID: fixture.workspaceID, runID: fixture.runID)
        let task = model.conversationReadTasks[key]
        try await waitUntil { await fixture.replies.requests.contains(2) }
        #expect(model.transcripts[2] == nil)
        if changeWorkspace {
            model.workspaceContextGeneration += 1
            model.selectedWorkspaceID = "different-workspace"
        } else {
            model.cancelConversationRead(key)
        }
        await fixture.replies.release()
        await task?.value
        #expect(model.transcripts[2] == nil)
        if changeWorkspace {
            #expect(model.runViewStateStore.snapshot(for: key)?.transcripts[2]?.cursor == 2)
        }
        #expect(model.selectedAgent == 1)
        #expect(model.errorNotice == nil)
        #expect(await fixture.replies.requests.contains(2))
    }

    @Test
    func completedAnswerBypassesAnInFlightWorkPreloadWithoutAnotherWorkRequest() async throws {
        let fixture = try Fixture(pausedAgent: 3)
        defer { fixture.close() }
        let model = fixture.model
        model.scheduleVisibleAgentPreload()
        try await waitUntil { await fixture.replies.requests.contains(3) }
        let selection = Task { await model.selectAgent(3, manageFollowUpQueue: false) }
        await selection.value
        #expect(model.selectedAgent == 3)
        #expect(model.pendingAgentSelection == nil)
        #expect(model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(model.transcripts[3]?.cursor == 0)
        #expect(model.transcripts[3]?.hasMore == true)
        #expect(!model.isSelectedTimelineLoading)
        #expect(await fixture.replies.answerRequests == [3])
        #expect(await fixture.replies.requests.filter { $0 == 3 }.count == 1)
        await fixture.replies.release()
        try await waitUntil { model.transcripts[3]?.cursor == 2 }
        #expect(model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(await fixture.replies.requests.filter { $0 == 3 }.count == 1)
    }

    @Test
    func coldDefaultSelectionReadsBeforeWorkAndContinuationConfirmation() async throws {
        let fixture = try Fixture(pausedAgent: 3, modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        // A concurrent cold refresh restores the canonical observed queue.
        model.daemonFollowUpSnapshotsByWorkspace[model.machineWorkspaceContextKey(workspaceID: fixture.workspaceID)] = model.daemonFollowUpSnapshot
        model.transcripts.removeValue(forKey: 3)
        await fixture.replies.holdSelection(3)
        await model.selectAgent(3)
        try await waitUntil { await fixture.replies.selectionRequests == [3] }
        #expect(model.selectedAgent == 3)
        #expect(model.pendingAgentSelection == nil)
        #expect(model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(model.transcripts[3]?.hasMore == true)
        #expect(model.continuationSelectionNeedsConfirmation)
        #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 1)
        #expect(model.activeRun?.agents.first { $0.agent == 3 }?.status == "success")
        await fixture.replies.releaseSelection()
        await fixture.replies.release()
        try await waitUntil { !model.continuationSelectionNeedsConfirmation }
        #expect(model.remoteFollowUpSelection(runID: fixture.runID) == 3)
    }

    @Test
    func warmProgressCacheCannotSuppressATerminalAnswerWhileWorkIsHeld() async throws {
        let fixture = try Fixture(pausedAgent: 3, running: true)
        defer { fixture.close() }
        var run = try #require(fixture.model.activeRun)
        run.agents[3].status = "running"
        fixture.model.activeRun = run
        try await fixture.replies.installRun(run)
        await fixture.replies.configureAnswer(pageBytes: 5)
        fixture.model.transcripts[3] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 3,
            blocks: [TranscriptBlock(id: "opening", kind: .output, text: "Checking files",
                activityType: "assistant_progress")],
            cursor: run.eventCount, hasMore: false, finalMessageHasMore: false)
        let work = Task { await fixture.model.loadAgentDetail(3, maxPages: 1, allowInactiveSelection: true) }
        try await waitUntil { await fixture.replies.requests == [3] }
        await fixture.model.selectAgent(3, manageFollowUpQueue: false)
        try await waitUntil { fixture.model.transcripts[3]?.finalMessage == "Candidate 3" }
        #expect(fixture.model.transcripts[3]?.finalMessageHasMore == false)
        #expect(fixture.model.activeRun?.agents[3].status == "running")
        #expect(fixture.model.pendingAgentSelection == nil)
        #expect(await fixture.replies.answerRequests.filter { $0 == 3 }.count == 3)
        await fixture.replies.release()
        _ = await work.value
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
    }

    @Test
    func terminalTransitionLoadsTheAnswerWithoutANewWorkEventOrSelection() async throws {
        let fixture = try Fixture(pausedAgent: 3, running: true)
        defer { fixture.close() }
        let model = fixture.model
        var run = try #require(model.activeRun)
        run.agents[2].status = "running"
        run.agents[3].status = "running"
        model.activeRun = run
        model.selectedAgent = 3
        model.transcripts[3] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 3,
            blocks: [TranscriptBlock(id: "opening", kind: .output, text: "Checking files",
                activityType: "assistant_progress")],
            cursor: run.eventCount, hasMore: false, finalMessageHasMore: false)
        run.agents[2].status = "success"
        try await fixture.replies.installRun(run)
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        try await waitUntil { model.transcripts[3]?.finalMessage == "Candidate 3" }
        #expect(model.activeRun?.isWorking == true)
        #expect(model.activeRun?.agents[3].status == "running")
        #expect(model.selectedAgent == 3)
        #expect(model.transcripts[3]?.cursor == run.eventCount)
        #expect(await fixture.replies.answerRequests == [3])
    }

    @Test
    func aConfirmedTerminalEmptyAnswerDoesNotSpinOnUnchangedSummaries() async throws {
        let fixture = try Fixture(pausedAgent: 3, modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        await fixture.replies.configureAnswer(missing: true)
        let run = try #require(model.activeRun)
        model.selectedAgent = 3
        model.transcripts[3] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 3,
            blocks: [TranscriptBlock(id: "opening", kind: .output, text: "Checking files")],
            cursor: run.eventCount, hasMore: false, finalMessageHasMore: false)
        let work = Task { await model.loadAgentDetail(3, maxPages: 1) }
        try await waitUntil { await fixture.replies.requests == [3] }
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        try await waitUntil { model.transcripts[3]?.finalMessageReadStatus == "success" }
        for _ in 0..<3 { try await model.loadRun(fixture.runID, loadDetailPages: 1) }
        #expect(await fixture.replies.answerRequests == [3])
        #expect(model.transcripts[3]?.finalMessage.isEmpty == true)
        #expect(!selectedAgentDetailNeedsRefresh(run: model.activeRun, selectedAgent: 3, transcripts: model.transcripts))
        await fixture.replies.release()
        _ = await work.value
    }

    @Test
    func failedAnswerReadCanRecoverWithoutNewWorkEventsAndNeverRegressesExecution() async throws {
        let fixture = try Fixture(pausedAgent: 3, failFirst: true, modernSelection: true)
        defer { fixture.close() }
        let model = fixture.model
        let run = try #require(model.activeRun)
        model.selectedAgent = 3
        model.transcripts[3] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 3,
            blocks: [TranscriptBlock(id: "opening", kind: .output, text: "Checking files")],
            cursor: run.eventCount, hasMore: false, finalMessageHasMore: false)
        let work = Task { await model.loadAgentDetail(3, maxPages: 1) }
        try await waitUntil { await fixture.replies.requests == [3] }
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        try await waitUntil { model.errorNotice != nil }
        #expect(model.activeRun?.agents[2].isTerminal == true)
        #expect(model.transcripts[3]?.error.isEmpty == true)
        #expect(model.transcripts[3]?.finalMessageReadStatus == nil)
        // A new summary must not reset the failed read's retry backoff.
        try await model.loadRun(fixture.runID, loadDetailPages: 1)
        try await Task.sleep(for: .milliseconds(30))
        #expect(await fixture.replies.answerRequests == [3])
        await model.loadAgentAnswer(3, maxPages: 20, retryImmediately: true)
        #expect(model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(model.errorNotice == nil && model.activeRun?.agents[2].isTerminal == true)
        await fixture.replies.release()
        _ = await work.value
    }

    @Test
    func runningWorkReceiptCannotCertifyAnAnswerAfterTheTerminalTransition() async throws {
        let fixture = try Fixture(pausedAgent: 3, modernSelection: true, running: true)
        defer { fixture.close() }
        let model = fixture.model
        var run = try #require(model.activeRun)
        run.agents[2].status = "running"
        model.activeRun = run
        model.selectedAgent = 3
        model.transcripts[3] = AgentTranscript(sourceRunID: fixture.runID, sourceAgent: 3,
            cursor: run.eventCount, hasMore: false, finalMessageHasMore: false)
        await fixture.replies.configureAnswer(staleWorkArtifact: true)
        let work = Task { await model.loadAgentDetail(3, maxPages: 1) }
        try await waitUntil { await fixture.replies.requests == [3] }
        run.agents[2].status = "success"
        model.activeRun = run
        await fixture.replies.release()
        _ = await work.value
        #expect(model.transcripts[3]?.finalMessageReadStatus == "running")
        #expect(selectedAgentAnswerNeedsRefresh(run: run, selectedAgent: 3, transcripts: model.transcripts))
        await model.loadAgentAnswer(3, maxPages: 20)
        #expect(model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(model.transcripts[3]?.finalMessageReadStatus == "success")
    }

    @Test
    func realCompletedBranchCacheLoadsItsEntireSavedAnswerWithoutReplayingATurn() async throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_ANSWER_DELIVERY_EVIDENCE"] else { return }
        let evidence = URL(fileURLWithPath: path, isDirectory: true)
        let cached = try JSONDecoder().decode(WorkspaceRunViewCacheRecord.self,
            from: Data(contentsOf: evidence.appendingPathComponent("scene-cache.json")))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let detail = try decoder.decode(RunAgentDetail.self,
            from: Data(contentsOf: evidence.appendingPathComponent("scene-agent5.json")))
        let answer = try #require(detail.finalMessagePage?.text)
        #expect(answer.utf8.count == 3271)
        let hash = SHA256.hash(data: Data(answer.utf8)).map { String(format: "%02x", $0) }.joined()
        #expect(hash == "ca46d7caf4b21dd39a5a8bec6168888256d894e9ae18c5a49b99448efde26626")
        let fixture = try Fixture(pausedAgent: 5, modernSelection: true, running: true)
        defer { fixture.close() }
        let model = fixture.model
        var transcript = try #require(cached.snapshot.transcripts[5])
        #expect(transcript.blocks.count == 6 && transcript.cursor == 205)
        #expect(transcript.finalMessage.isEmpty && !transcript.finalMessageHasMore)
        #expect(transcript.finalMessageReadStatus == nil)
        transcript.sourceRunID = fixture.runID
        model.transcripts[5] = transcript
        await fixture.replies.useSavedAnswer(answer, messageID: try #require(detail.finalMessagePage?.messageId))
        let work = Task { await model.loadAgentDetail(5, maxPages: 1, allowInactiveSelection: true) }
        try await waitUntil { await fixture.replies.requests == [5] }
        await model.selectAgent(5, manageFollowUpQueue: false)
        try await waitUntil { model.transcripts[5]?.finalMessage == answer }
        #expect(model.selectedAgent == 5 && model.pendingAgentSelection == nil)
        #expect(model.transcripts[5]?.finalMessageCursor == 3271)
        #expect(model.transcripts[5]?.finalMessageReadStatus == "success")
        let output = try #require(model.selectedTranscriptBlocks.first { $0.isFinalAnswer && $0.text == answer })
        let item = TranscriptCollectionItem()
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 1800),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let content = NSView(frame: window.contentRect(forFrameRect: window.frame))
        window.contentView = content
        item.view.frame = content.bounds
        content.addSubview(item.view)
        item.configure(block: output, language: .chinese, textSize: .standard,
            accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 1000, openURL: { _ in }, activateBlock: { _ in })
        item.view.layoutSubtreeIfNeeded()
        func textViews(_ view: NSView) -> [NSTextView] {
            if let text = view as? NSTextView { return [text] }
            return view.subviews.flatMap(textViews)
        }
        let text = try #require(textViews(item.view).first)
        #expect(text.string == TranscriptTextRenderer.attributedText(for: output,
            textSize: .standard, accentColor: .orange, appearance: .dark).string)
        #expect(text.string.contains("保持现有强对照"))
        let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
        item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
        try #require(bitmap.representation(using: .png, properties: [:]))
            .write(to: evidence.appendingPathComponent("recovered-agent5-offscreen.png"))
        #expect(!window.isVisible)
        await fixture.replies.release()
        _ = await work.value
        #expect(model.transcripts[5]?.finalMessage == answer)
        #expect(await fixture.replies.continuationRequests.isEmpty)
        print("Scene replay passed: 3271 bytes, SHA256=\(hash), selected Agent 5, held work reader, no user turn.")
    }

    @Test
    func longAnswerContinuesWhileWorkIsHeldAndLateEmptyArtifactCannotEraseIt() async throws {
        let fixture = try Fixture(pausedAgent: 3)
        defer { fixture.close() }
        await fixture.replies.configureAnswer(pageBytes: 5, staleWorkArtifact: true)
        fixture.model.scheduleVisibleAgentPreload()
        try await waitUntil { await fixture.replies.requests.contains(3) }
        await fixture.model.selectAgent(3, manageFollowUpQueue: false)
        try await waitUntil { fixture.model.transcripts[3]?.finalMessageHasMore == false }
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(fixture.model.transcripts[3]?.cursor == 0)
        #expect(fixture.model.transcripts[3]?.hasMore == true)
        #expect(await fixture.replies.answerRequests.filter { $0 == 3 }.count == 3)
        await fixture.replies.release()
        try await waitUntil { fixture.model.transcripts[3]?.cursor == 2 }
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(fixture.model.transcripts[3]?.finalMessageCursor == 11)
    }

    @Test
    func aHeldAnswerTailDoesNotBlockWorkWithoutAnExistingPreload() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        await fixture.replies.configureAnswer(pageBytes: 5)
        await fixture.replies.holdAnswerTail(3)
        await fixture.model.selectAgent(3, manageFollowUpQueue: false)
        try await waitUntil { await fixture.replies.answerRequests.filter { $0 == 3 }.count >= 2 }
        try await waitUntil { fixture.model.transcripts[3]?.cursor == 2 }
        #expect(await fixture.replies.requests.contains(3))
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(fixture.model.transcripts[3]?.finalMessageHasMore == false)
        await fixture.replies.releaseAnswer()
        try await Task.sleep(for: .milliseconds(50))
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
    }

    @Test
    func missingAnswerArtifactFallsBackToTheWorkStreamWithoutCompletingItsCursorEarly() async throws {
        let fixture = try Fixture(pausedAgent: 3)
        defer { fixture.close() }
        await fixture.replies.configureAnswer(missing: true)
        let selection = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.requests == [3] }
        #expect(fixture.model.selectedAgent == 3)
        #expect(fixture.model.transcripts[3]?.hasMore == true)
        #expect(!fixture.model.agentTranscriptReadyForDisplay(3))
        #expect(fixture.model.isSelectedTimelineLoading)
        await fixture.replies.release()
        await selection.value
        #expect(fixture.model.transcripts[3]?.blocks.contains { $0.text == "Candidate 3" } == true)
        #expect(fixture.model.transcripts[3]?.finalMessage.isEmpty == true)
        #expect(fixture.model.pendingAgentSelection == nil)
    }

    @Test(arguments: [false, true])
    func lateAnswerCannotChangeANewerChoiceOrAnotherWorkspace(changeWorkspace: Bool) async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        await fixture.replies.holdAnswer(3)
        let first = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.answerRequests == [3] }
        #expect(fixture.model.selectedAgent == 3)
        #expect(fixture.model.isSelectedTimelineLoading)
        if changeWorkspace {
            fixture.model.workspaceContextGeneration += 1
            fixture.model.selectedWorkspaceID = "other"
            fixture.model.selectedAgent = 6
        } else {
            await fixture.model.selectAgent(4, manageFollowUpQueue: false)
        }
        await fixture.replies.releaseAnswer()
        await first.value
        #expect(fixture.model.selectedAgent == (changeWorkspace ? 6 : 4))
        #expect(fixture.model.transcripts[3] == nil)
        #expect(fixture.model.errorNotice == nil)
    }

    @Test
    func returningToAColdAgentDoesNotJoinItsObsoleteSelectionRead() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        await fixture.replies.holdAnswer(3)
        let first = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.answerRequests == [3] }
        await fixture.model.selectAgent(4, manageFollowUpQueue: false)
        let returning = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.answerRequests.filter { $0 == 3 }.count == 2 }
        #expect(fixture.model.selectedAgent == 3)
        await fixture.replies.releaseAnswer()
        await first.value
        await returning.value
        #expect(fixture.model.selectedAgent == 3)
        #expect(fixture.model.pendingAgentSelection == nil)
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(!fixture.model.isSelectedTimelineLoading)
    }

    @Test
    func repeatedClickOnAColdSelectedAgentJoinsItsAnswerRead() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        await fixture.replies.holdAnswer(3)
        let first = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.answerRequests == [3] }
        let selectionID = fixture.model.agentSelectionRequestID
        await fixture.model.selectAgent(3, manageFollowUpQueue: false)
        #expect(fixture.model.agentSelectionRequestID == selectionID)
        #expect(fixture.model.pendingAgentSelection == 3)
        #expect(await fixture.replies.answerRequests == [3])
        await fixture.replies.releaseAnswer()
        await first.value
        #expect(fixture.model.pendingAgentSelection == nil)
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
    }

    @Test
    func failedAnswerReadFallsBackWithoutSavingTheTransportErrorAsOutput() async throws {
        let fixture = try Fixture(failFirst: true)
        defer { fixture.close() }
        await fixture.model.selectAgent(3, manageFollowUpQueue: false)
        #expect(await fixture.replies.answerRequests == [3])
        #expect(await fixture.replies.requests == [3])
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(fixture.model.transcripts[3]?.error.isEmpty == true)
        #expect(fixture.model.errorNotice == nil)
    }

    @Test
    func answerCanFillAnEmptyArtifactReceiptThatArrivedDuringTheRead() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        await fixture.replies.configureAnswer(staleWorkArtifact: true)
        await fixture.replies.holdAnswer(3)
        let selection = Task { await fixture.model.selectAgent(3, manageFollowUpQueue: false) }
        try await waitUntil { await fixture.replies.answerRequests == [3] }
        await fixture.model.loadAgentDetail(3, maxPages: 1, allowInactiveSelection: true)
        #expect(fixture.model.transcripts[3]?.finalMessageHasMore == false)
        #expect(fixture.model.transcripts[3]?.finalMessage.isEmpty == true)
        await fixture.replies.releaseAnswer()
        await selection.value
        #expect(fixture.model.transcripts[3]?.finalMessage == "Candidate 3")
        #expect(fixture.model.pendingAgentSelection == nil)
    }

    @Test
    func aFailedPreloadPreservesTheVisibleAnswerAndRetriesInBackground() async throws {
        let fixture = try Fixture(failFirst: true)
        defer { fixture.close() }
        await fixture.replies.failNextRead(of: 2)
        let model = fixture.model
        let original = model.transcripts[1]
        model.scheduleVisibleAgentPreload()
        try await waitUntil { model.transcripts[6] != nil }
        #expect(model.errorNotice == nil)
        #expect(model.transcripts[1] == original)
        try await waitUntil { model.transcripts[2]?.cursor == 2 }
        #expect(model.selectedAgent == 1)
        #expect(await fixture.replies.failedReads == [2])
        let reads = await fixture.replies.requests.filter { $0 == 2 }.count
        #expect((2...3).contains(reads))
    }

    @Test
    func inactiveLiveCandidatesKeepUpdatingAndReplaceChangedArtifacts() async throws {
        let fixture = try Fixture(running: true)
        defer { fixture.close() }
        let model = fixture.model
        model.scheduleVisibleAgentPreload()
        try await waitUntil { model.transcripts[3]?.cursor == 2 }
        let run = try #require(model.activeRun)
        var payload = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        payload["eventCount"] = 4
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: payload))
        await fixture.replies.advance()
        try await waitUntil { model.transcripts[3]?.finalMessage == "Updated candidate 3" }
        #expect(model.transcripts[3]?.cursor == 4)
        #expect(model.transcripts[3]?.finalMessageHasMore == false)
        #expect(model.selectedAgent == 1)
        #expect(model.errorNotice == nil)
    }

    private func waitUntil(_ condition: @MainActor () async -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(6)
        while !(await condition()), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        try #require(await condition())
    }

    private actor Replies {
        var requests: [Int] = []
        var conversationRequests: [Int] = []
        var answerRequests: [Int] = []
        var pausedAnswer: Int?
        var pausesOnlyAnswerTail = false
        var answerReplies: [CheckedContinuation<Void, Never>] = []
        var answerPageBytes = Int.max
        var missingAnswer = false
        var staleWorkArtifact = false
        var savedAnswer: String?
        var savedAnswerMessageID = ""
        let runID: String
        var runReply: Data
        var pausedAgent: Int?
        var pendingReplies: [CheckedContinuation<Void, Never>] = []
        var failFirst: Bool
        var failedReads: [Int] = []
        var firstFailureAgent: Int?
        var eventTotal = 2
        var selectionRequests: [Int] = []
        var selectionCommandIDs: [String] = []
        var selectedAgent = 1
        var selectionRevision = 1
        var pausedSelection: Int?
        var selectionReply: CheckedContinuation<Void, Never>?
        var rejectedSelection = false
        var droppedSelectionReceipts = 0
        var continuationRequests: [Int] = []
        var continuationCommandIDs: [String] = []

        func holdSelection(_ agent: Int) { pausedSelection = agent }
        func rejectSelection() { rejectedSelection = true }
        func dropSelectionReceipts(_ count: Int) { droppedSelectionReceipts = count }
        func releaseSelection() {
            pausedSelection = nil
            selectionReply?.resume()
            selectionReply = nil
        }

        init(runID: String, runReply: Data, pausedAgent: Int?, failFirst: Bool) {
            self.runID = runID
            self.runReply = runReply
            self.pausedAgent = pausedAgent
            self.failFirst = failFirst
        }

        func release() {
            pausedAgent = nil
            for reply in pendingReplies { reply.resume() }
            pendingReplies.removeAll()
        }

        func advance() { eventTotal = 4 }
        func failNextRead(of agent: Int) { failFirst = true; firstFailureAgent = agent }
        func configureAnswer(pageBytes: Int = Int.max, missing: Bool = false, staleWorkArtifact: Bool = false) {
            answerPageBytes = pageBytes
            missingAnswer = missing
            self.staleWorkArtifact = staleWorkArtifact
        }
        func useSavedAnswer(_ answer: String, messageID: String) {
            savedAnswer = answer
            savedAnswerMessageID = messageID
            answerPageBytes = 1024
        }
        func holdAnswer(_ agent: Int) { pausedAnswer = agent; pausesOnlyAnswerTail = false }
        func holdAnswerTail(_ agent: Int) { pausedAnswer = agent; pausesOnlyAnswerTail = true }
        func releaseAnswer() {
            pausedAnswer = nil
            for reply in answerReplies { reply.resume() }
            answerReplies.removeAll()
        }
        func installRun(_ run: RunSummary) throws {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            runReply = try JSONSerialization.data(withJSONObject: ["ok": true,
                "body": JSONSerialization.jsonObject(with: encoder.encode(run))]) + Data([10])
        }

        func reply(action: String, agent: Int, cursor: Int, finalCursor: Int, includeEvents: Bool,
                   conversationOnly: Bool,
                   workspaceID: String, commandID: String) async throws -> Data {
            if action == "system.capabilities" {
                return try JSONSerialization.data(withJSONObject: ["ok": true, "body": [
                    "daemon_version": "0.1.70", "command_receipt_store_id": "selection-fixture",
                    "actions": ["system.health", "turns.continue", "followups.select_agent", "followups.replace"]
                ]]) + Data([10])
            }
            if action == "turns.continue" {
                continuationRequests.append(agent)
                continuationCommandIDs.append(commandID)
                return Data("{\"ok\":false,\"error\":{\"code\":\"pcr_operation_pending\",\"message\":\"Fixture hold\"}}\n".utf8)
            }
            if action == "followups.select_agent" {
                selectionRequests.append(agent)
                selectionCommandIDs.append(commandID)
                if pausedSelection == agent {
                    await withCheckedContinuation { selectionReply = $0 }
                }
                if rejectedSelection {
                    return Data("{\"ok\":false,\"error\":{\"code\":\"follow_up_busy\",\"message\":\"Already dispatching\"}}\n".utf8)
                }
                selectedAgent = agent
                selectionRevision += 1
                if droppedSelectionReceipts > 0 {
                    droppedSelectionReceipts -= 1
                    return Data()
                }
                return try JSONSerialization.data(withJSONObject: ["ok": true, "body": [
                    "revision": selectionRevision, "queue": ["workspace_id": workspaceID,
                        "run_id": runID, "items": [], "selected_agent": agent, "state": "waiting",
                        "selection_source": "manual", "generation": 1]
                ]]) + Data([10])
            }
            guard action == "runs.agent" else { return runReply }
            if includeEvents {
                if conversationOnly { conversationRequests.append(agent) }
                else { requests.append(agent) }
            } else { answerRequests.append(agent) }
            if includeEvents, pausedAgent == agent {
                await withCheckedContinuation { pendingReplies.append($0) }
            }
            if !includeEvents, pausedAnswer == agent, !pausesOnlyAnswerTail || finalCursor > 0 {
                await withCheckedContinuation { answerReplies.append($0) }
            }
            if failFirst, firstFailureAgent == nil || firstFailureAgent == agent {
                failFirst = false
                failedReads.append(agent)
                return Data("{\"ok\":false,\"error\":{\"code\":\"pcr_error\",\"message\":\"Retry later\"}}\n".utf8)
            }
            let next = agent == 2 && cursor == 0 ? 1 : eventTotal
            let defaultAnswer = eventTotal == 2 ? "Candidate \(agent)" : "Updated candidate \(agent)"
            let answer = agent == 5 ? (savedAnswer ?? defaultAnswer) : defaultAnswer
            let bytes = Data(answer.utf8)
            let offset = min(finalCursor, answer.utf8.count)
            var end = answerPageBytes >= answer.utf8.count ? answer.utf8.count : min(offset + answerPageBytes, answer.utf8.count)
            while end > offset && String(data: bytes[offset..<end], encoding: .utf8) == nil { end -= 1 }
            let noArtifact = missingAnswer || (includeEvents && staleWorkArtifact)
            var body: [String: Any] = [
                "run_id": runID, "agent": agent, "name": "Agent \(agent)", "status": "success",
                "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
                "stdout_tail": "", "stderr_tail": "", "final_message": "",
                "final_message_page": ["cursor": noArtifact ? 0 : offset, "next_cursor": noArtifact ? 0 : end,
                    "has_more": !noArtifact && end < answer.utf8.count, "total_bytes": noArtifact ? 0 : answer.utf8.count,
                    "message_id": noArtifact ? "" : (agent == 5 && savedAnswer != nil ? savedAnswerMessageID : "\(agent)-\(eventTotal)"),
                    "text": noArtifact ? "" : String(decoding: bytes[offset..<end], as: UTF8.self)],
            ]
            if includeEvents {
                let items: [[String: Any]] = missingAnswer && cursor == 0
                    ? [["id": "answer-\(agent)", "event_id": 1, "kind": "output", "text": answer]] : []
                body["transcript_page"] = ["schema_version": 1, "cursor": cursor, "next_cursor": next,
                    "has_more": next < eventTotal, "total": eventTotal, "items": items]
            }
            return try JSONSerialization.data(withJSONObject: ["ok": true, "body": body]) + Data([10])
        }
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let windowID = UUID()
        let runID = "run-\(UUID().uuidString)"
        let workspaceID = "workspace-\(UUID().uuidString)"
        let listener: Int32
        let model: AppModel
        let replies: Replies
        let server: Task<Void, Never>

        init(pausedAgent: Int? = nil, failFirst: Bool = false,
             modernSelection: Bool = false, running: Bool = false) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("test-token".utf8).write(to: tokenURL)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            let descriptor = listener
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let bytes = Array(socketURL.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { target in
                bytes.withUnsafeBytes { target.copyBytes(from: $0) }
            }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(bound == 0 && Darwin.listen(descriptor, 8) == 0)
            let runBody: [String: Any] = [
                "runId": runID, "status": running ? "running" : "completed", "workspace": "/qa", "prompt": "Question",
                "createdAt": "2026-09-18T00:00:00Z", "updatedAt": "2026-09-18T00:00:00Z", "config": [:],
                "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": running, "workerAvailable": false, "eventCount": 2,
                "agents": (1...6).map { ["agent": $0, "name": "Agent \($0)", "status": "success",
                    "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false] },
            ]
            let run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: runBody))
            // The transport uses snake_case, while the model fixture uses Codable's native keys.
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            let wireRun = try JSONSerialization.jsonObject(with: encoder.encode(run))
            let replies = Replies(runID: runID, runReply: try JSONSerialization.data(
                withJSONObject: ["ok": true, "body": wireRun]) + Data([10]),
                pausedAgent: pausedAgent, failFirst: failFirst)
            self.replies = replies
            server = Task.detached {
                defer { Darwin.close(descriptor) }
                while !Task.isCancelled {
                    let socket: Int32 = (try? await ClientBlockingIO.transport(priority: .utility, isObservation: false) {
                        var pending = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                        guard Darwin.poll(&pending, 1, 100) >= 0,
                              pending.revents & Int16(POLLIN) != 0 else { return -1 }
                        return Darwin.accept(descriptor, nil, nil)
                    }) ?? -1
                    guard socket >= 0 else { continue }
                    guard !Task.isCancelled else { Darwin.close(socket); return }
                    var noSignal: Int32 = 1
                    _ = withUnsafePointer(to: &noSignal) {
                        Darwin.setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size))
                    }
                    Task.detached {
                    defer { Darwin.close(socket) }
                    var timeout = timeval(tv_sec: 2, tv_usec: 0)
                    _ = withUnsafePointer(to: &timeout) {
                        Darwin.setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
                    }
                    let data: Data = (try? await ClientBlockingIO.transport(priority: .utility, isObservation: false) {
                        var buffer = [UInt8](repeating: 0, count: 8192)
                        let count = Darwin.read(socket, &buffer, buffer.count)
                        return count > 0 ? Data(buffer.prefix(count)) : Data()
                    }) ?? Data()
                    guard !data.isEmpty,
                          let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          let action = request["action"] as? String else { return }
                    let body = request["body"] as? [String: Any] ?? [:]
                    guard let reply = try? await replies.reply(action: action,
                        agent: body["agent"] as? Int ?? 0, cursor: body["cursor"] as? Int ?? 0,
                        finalCursor: body["final_message_cursor"] as? Int ?? 0,
                        includeEvents: body["include_events"] as? Bool ?? true,
                        conversationOnly: body["conversation_items_only"] as? Bool == true,
                        workspaceID: body["workspace_id"] as? String ?? "",
                        commandID: request["command_id"] as? String ?? "") else { return }
                    _ = try? await ClientBlockingIO.transport(priority: .utility, isObservation: false) {
                        reply.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
                    }
                    }
                }
            }
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: windowID,
                desktopStateStore: DesktopStateStore(windowID: windowID, directory: directory))
            model.appLanguage = .english
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self,
                from: JSONSerialization.data(withJSONObject: [
                    "workspaceId": workspaceID, "path": "/qa", "name": "Agent QA", "lastRunId": runID,
                    "lastResumeSessionId": "", "selected": false, "available": true, "readable": true, "writable": true,
                ]))]
            model.selectedWorkspaceID = workspaceID
            model.activeRun = run
            model.selectedAgent = 1
            model.transcripts[1] = AgentTranscript(sourceRunID: runID, sourceAgent: 1,
                cursor: 2, hasMore: false, finalMessage: "Candidate 1", finalMessageHasMore: false,
                finalMessageReadStatus: "success")
            if modernSelection {
                model.systemCapabilities = try JSONDecoder().decode(SystemCapabilities.self,
                    from: Data("{\"actions\":[\"followups.select_agent\",\"followups.replace\"]}".utf8))
                for agent in 2...6 {
                    model.transcripts[agent] = AgentTranscript(sourceRunID: runID, sourceAgent: agent,
                        cursor: 2, hasMore: false, finalMessage: "Candidate \(agent)", finalMessageHasMore: false,
                        finalMessageReadStatus: "success")
                }
                model.daemonFollowUpSnapshot = RemoteFollowUpQueue(workspaceId: workspaceID, runId: runID,
                    items: [], selectedAgent: 1, state: "waiting", deadlineEpochMs: nil,
                    lastError: nil, generation: 1, lastCompletedItemId: nil, selectionSource: "automatic")
            }
        }

        func close() {
            model.stopWindowObservation()
            model.forgetCachedRunViews(machineID: model.currentMachineContextID, workspaceID: workspaceID)
            Task { await replies.release(); await replies.releaseSelection(); await replies.releaseAnswer() }
            server.cancel()
            if model.desktopStateStore.flushPendingWrites(),
               let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent(
                    "Parallel Codex/window-\(windowID.uuidString).v3.json"))
            }
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
