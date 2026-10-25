import Darwin
import AppKit
import Foundation
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct PromptDeliveryTests {
    @Test func workspaceReentryKeepsTheUnacceptedFollowupBeforeAnyNetworkRead() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let model = fixture.model
        let source = try #require(model.activeRun)
        model.transcripts[7] = AgentTranscript(sourceRunID: source.runId, sourceAgent: 7,
            hasMore: false, finalMessage: "The original completed answer", finalMessageHasMore: false,
            finalMessageReadStatus: "success")
        model.workspaces.append(try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"other","name":"Other","path":"/other","lastRunId":"",
             "lastResumeSessionId":"","selected":false,"available":true,"readable":true,"writable":true}
            """.utf8)))
        await fixture.replies.registerRun(source)
        await fixture.replies.holdAcceptance()
        model.updatePrompt("The follow-up must remain visible")
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let submitted = try #require(model.pendingPromptSubmission)
        await model.selectWorkspace("other")
        await model.selectWorkspace("workspace")
        #expect(model.activeRun?.runId == source.runId)
        #expect(model.pendingPromptSubmission?.commandID == submitted.commandID)
        #expect(model.selectedTranscriptBlocks.contains { $0.text == submitted.prompt })
        #expect(model.selectedTranscriptBlocks.contains { $0.text == "The original completed answer" })
        let surface = NativeHandoffSurface(model: model)
        defer { surface.close() }
        surface.update()
        try await waitUntil {
            surface.update()
            return surface.texts.contains(submitted.prompt) && surface.texts.contains("The original completed answer")
        }
        #expect(model.selectedAgent == 7)
        #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        if let path = ProcessInfo.processInfo.environment["PCR_CONTINUATION_EVIDENCE_DIR"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path).appendingPathComponent("workspace-pending-return.png"))
        }
        await fixture.replies.allowOpening()
        await fixture.replies.releaseAcceptance()
        try await waitUntil { model.promptOutboxByWorkspace.isEmpty
            && model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(await fixture.replies.startCommands == [submitted.commandID])
    }

    @Test(arguments: [false, true])
    func offscreenAcceptanceCommitsTheDestinationAndSourceHistoryBeforeSettlement(offscreen: Bool) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let model = fixture.model
        let source = try #require(model.activeRun)
        let original = submission(status: .opening, sourceRunID: source.runId, sourceAgent: 7)
        let result = PersistedAcceptedPromptResult(submission: original, runID: "accepted",
            continuedSessionID: "session-7", workspaceRevision: 1)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["runId"] = result.runID
        json["prompt"] = original.prompt
        json["continuedFromRunId"] = source.runId
        json["continuedFromAgent"] = 7
        json["turnIndex"] = 1
        json["createdAt"] = "2026-10-18T00:00:00Z"
        let child = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        await fixture.replies.registerRun(child)
        #expect(await fixture.store.recordPromptSubmission(original))
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: original.contextKey, expected: original, result: result))
        model.acceptedPromptResultsByWorkspace[original.contextKey] = result
        model.transcripts[7] = AgentTranscript(sourceRunID: source.runId, sourceAgent: 7,
            hasMore: false, finalMessage: "Original source answer", finalMessageHasMore: false,
            finalMessageReadStatus: "success")
        model.cacheVisibleRunViewState()
        model.workspaces.append(try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"other","name":"Other","path":"/other","lastRunId":"",
             "lastResumeSessionId":"","selected":false,"available":true,"readable":true,"writable":true}
            """.utf8)))
        if offscreen { await model.selectWorkspace("other") }
        #expect(await model.adoptAcceptedPromptResult(result,
            response: RunStartResponse(status: "accepted", message: nil, runId: child.runId,
                storage: nil, run: child, workspaceId: "workspace", workspaceRevision: 1, continuedFrom: nil)))
        #expect(model.selectedWorkspaceID == (offscreen ? "other" : "workspace"))
        #expect(offscreen ? model.activeRun == nil : model.activeRun?.runId == child.runId)
        #expect(model.workspaces.first { $0.workspaceId == "workspace" }?.lastRunId == child.runId)
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: child.runId)
        let coldStore = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let saved = try #require(await coldStore.hydrate(for: key))
        #expect(saved.snapshot.conversationHistory.contains { $0.text == "Original source answer" })
        let offlineClient = PcrdClient(socketURL: fixture.directory.appendingPathComponent("offline.sock"),
            tokenURL: fixture.directory.appendingPathComponent("t"))
        let reopened = AppModel(client: offlineClient, windowID: UUID(),
            desktopStateStore: fixture.store,
            runViewStateStore: WorkspaceRunViewStateStore(capacity: 1,
                diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { reopened.stopWindowObservation() }
        reopened.workspaces = model.workspaces.map {
            $0.workspaceId == "workspace" ? $0.replacingRunSummary(with: source, startingNewTurn: true) : $0
        }
        reopened.selectedWorkspaceID = "other"
        await reopened.selectWorkspace("workspace")
        #expect(reopened.activeRun == nil || reopened.activeRun?.runId == child.runId)
        // Cold disk hydration is asynchronous, but no server can supply the
        // destination or its history in this reopened client.
        try await waitUntil { reopened.activeRun?.runId == child.runId }
        #expect(reopened.activeRun?.runId == child.runId)
        #expect(reopened.selectedTranscriptBlocks.count { $0.text == original.prompt } == 1)
        #expect(reopened.resumeHistory.contains { $0.text == "Original source answer" })
        await model.selectWorkspace("workspace")
        #expect(model.activeRun?.runId == child.runId)
        #expect(model.selectedTranscriptBlocks.contains { $0.text == original.prompt })
        #expect(model.selectedTranscriptBlocks.contains { $0.text == "Original source answer" })
        let surface = NativeHandoffSurface(model: model)
        defer { surface.close() }
        for _ in 0..<3 {
            await model.selectWorkspace("other")
            await model.selectWorkspace("workspace")
            #expect(model.activeRun?.runId == child.runId)
            #expect(model.selectedTranscriptBlocks.count { $0.text == original.prompt } == 1)
            surface.update()
            try await waitUntil {
                surface.update()
                return surface.texts.contains(original.prompt) && surface.texts.contains("Original source answer")
            }
            #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        }
        if let path = ProcessInfo.processInfo.environment["PCR_CONTINUATION_EVIDENCE_DIR"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path)
                .appendingPathComponent("workspace-accepted-\(offscreen ? "offscreen" : "foreground").png"))
        }
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func switchingAgentsDuringAcceptanceNeverAttachesTheQuestionToAnotherBranch() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let source = try #require(fixture.model.activeRun)
        await fixture.replies.registerRun(source)
        await fixture.replies.allowOpening()
        for agent in [6, 7] {
            fixture.model.transcripts[agent] = AgentTranscript(sourceRunID: source.runId, sourceAgent: agent,
                hasMore: false, finalMessage: "Original answer \(agent)", finalMessageHasMore: false,
                finalMessageReadStatus: "success")
        }
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("One follow-up for Agent 7")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(original.sourceAgent == 7)
        await fixture.model.selectAgent(6)
        #expect(fixture.model.selectedTranscriptBlocks.contains { $0.text == "Original answer 6" })
        #expect(!fixture.model.selectedTranscriptBlocks.contains { $0.text == original.prompt })
        await fixture.model.selectAgent(7)
        #expect(fixture.model.selectedTranscriptBlocks.contains { $0.text == "Original answer 7" })
        #expect(fixture.model.selectedTranscriptBlocks.count { $0.text == original.prompt } == 1)
        fixture.model.updatePrompt("Keep the next unsent draft")
        await fixture.replies.releaseAcceptance()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(fixture.model.resumeHistory.contains { $0.text == "Original answer 7" })
        #expect(!fixture.model.resumeHistory.contains { $0.text == "Original answer 6" })
        #expect(fixture.model.prompt == "Keep the next unsent draft")
        #expect(!fixture.model.isViewingEarlierConversationTurn)
        #expect(await fixture.replies.startCommands == [original.commandID])
    }

    @Test func completionNotificationsExcludeReadingAdoptionAndStopTransitions() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        fixture.model.completionNotificationsEnabled = true
        for status in ["finalized", "discarded", "stopped", "cancelled", "cleanup_failed", "sync_ambiguous"] {
            try fixture.installCompletedConversation(finalized: true, status: status)
            let run = try #require(fixture.model.activeRun)
            #expect(!run.isExecutionComplete)
            fixture.model.notifyRunCompletionIfNeeded(run, observedTransition: true)
        }
        try fixture.installCompletedConversation(finalized: false)
        var completed = try #require(fixture.model.activeRun)
        #expect(completed.isExecutionComplete)
        fixture.model.notifyRunCompletionIfNeeded(completed)
        #expect(fixture.model.notifiedRunIDs.isEmpty)
        completed.completionReason = "finalization"
        #expect(!completed.isExecutionComplete)
        fixture.model.notifyRunCompletionIfNeeded(completed, observedTransition: true)
        #expect(fixture.model.notifiedRunIDs.isEmpty)
    }

    @Test func acceptedContinuationKeepsPreparedAnswersAndAgentSelection() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let source = try #require(fixture.model.activeRun)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["runId"] = "accepted"
        json["prompt"] = "Follow-up question"
        json["turnIndex"] = 1
        json["continuedFromRunId"] = source.runId
        json["continuedFromAgent"] = 7
        json["createdAt"] = "2026-10-18T00:00:00Z"
        let child = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        await fixture.replies.registerRun(child)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: child.runId)
        let history = [ResumeHistoryEntry(category: "user", text: source.prompt, id: "source-user"),
            ResumeHistoryEntry(category: "output", text: "Original Agent 7 answer", id: "source-answer")]
        let answers: [Int: AgentTranscript] = Dictionary(uniqueKeysWithValues: [6, 7].map { (agent: Int) in
            (agent, AgentTranscript(sourceRunID: child.runId, sourceAgent: agent, cursor: 20,
                hasMore: false, finalMessage: "Prepared follow-up answer \(agent)", finalMessageCursor: 30,
                finalMessageHasMore: false, finalMessageID: "child-\(agent)", finalMessageReadStatus: "success"))
        })
        fixture.model.runViewStateStore.put(.init(selectedAgent: 6, transcripts: answers,
            conversationHistory: history, historyLoaded: true), runSummary: child, for: key)
        let response = RunStartResponse(status: "accepted", message: nil, runId: child.runId,
            storage: nil, run: child, workspaceId: "workspace", workspaceRevision: nil, continuedFrom: nil)
        #expect(try await fixture.model.activateStartedRun(response, expectedWorkspaceID: "workspace",
            target: .local, carriedHistory: history))
        #expect(fixture.model.selectedAgent == 6)
        #expect(fixture.model.transcripts == answers)
        #expect(fixture.model.resumeHistory == history)
        let surface = NativeHandoffSurface(model: fixture.model)
        defer { surface.close() }
        for agent in [6, 7, 6] {
            await fixture.model.selectAgent(agent)
            surface.update()
            try await waitUntil { surface.texts.contains("Prepared follow-up answer \(agent)") }
            #expect(surface.texts.contains("Original Agent 7 answer"))
            #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
        }
        if let path = ProcessInfo.processInfo.environment["PCR_CONTINUATION_EVIDENCE_DIR"] {
            try surface.framePNG().write(to: URL(fileURLWithPath: path).appendingPathComponent("accepted-agent-6.png"))
        }
        #expect(fixture.model.activeRun?.runId == child.runId)
        #expect(fixture.model.transcripts[6]?.finalMessage == "Prepared follow-up answer 6")
        #expect(fixture.model.resumeHistory == history)
        #expect(!fixture.model.isViewingEarlierConversationTurn)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test(arguments: [false, true])
    func recoveredContinuationUsesItsOriginalSourceBranchNotTheVisibleAgent(partialOptimisticHistory: Bool) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let source = try #require(fixture.model.activeRun)
        let original = submission(status: .opening, sourceRunID: source.runId, sourceAgent: 7)
        let accepted = PersistedAcceptedPromptResult(submission: original, runID: "accepted",
            continuedSessionID: "session-7", workspaceRevision: 1)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["runId"] = accepted.runID
        json["prompt"] = original.prompt
        json["continuedFromRunId"] = source.runId
        json["continuedFromAgent"] = 7
        json["turnIndex"] = 1
        json["createdAt"] = "2026-10-18T00:00:00Z"
        await fixture.replies.registerRun(try JSONDecoder().decode(RunSummary.self,
            from: JSONSerialization.data(withJSONObject: json)))
        #expect(await fixture.store.recordPromptSubmission(original))
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: original.contextKey, expected: original, result: accepted))
        fixture.model.acceptedPromptResultsByWorkspace[original.contextKey] = accepted
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumeHistory = [ResumeHistoryEntry(category: "user", text: "Earlier question", id: "earlier")]
        fixture.model.transcripts[7] = AgentTranscript(sourceRunID: source.runId, sourceAgent: 7,
            hasMore: false, finalMessage: "Original source answer", finalMessageHasMore: false,
            finalMessageReadStatus: "success")
        fixture.model.cacheVisibleRunViewState()
        #expect(fixture.model.runViewStateStore.flushPendingWrites(timeout: 3))
        let cold = AppModel(client: fixture.client, windowID: UUID(), desktopStateStore: fixture.store,
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { cold.stopWindowObservation(); cold.cancelDeferredPromptProcessing() }
        cold.workspaces = fixture.model.workspaces
        cold.selectedWorkspaceID = "workspace"
        cold.selectedConversationID = "delivery-conversation"
        cold.acceptedPromptResultsByWorkspace[original.contextKey] = accepted
        cold.pendingPromptSubmission = original
        let partial = partialOptimisticHistory ? [ResumeHistoryEntry(category: "user", text: source.prompt)] : nil
        #expect(await cold.adoptAcceptedPromptResult(accepted, carriedHistory: partial))
        #expect(cold.activeRun?.runId == "accepted")
        #expect(cold.resumeHistory.contains { $0.text == "Earlier question" })
        #expect(cold.resumeHistory.contains { $0.text == source.prompt })
        #expect(cold.resumeHistory.contains { $0.text == "Original source answer" })
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func anInFlightDeliveryDoesNotOwnTheNextMessageComposer() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("First message")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        fixture.model.updatePrompt("Second message")
        #expect(fixture.model.canSubmitPrompt)
        #expect(fixture.model.promptSubmissionPlan == .enqueue)
        #expect(fixture.model.promptComposerContextText == nil)
        await fixture.model.submitPrompt()
        #expect(fixture.model.prompt.isEmpty)
        #expect(await fixture.replies.startCommands.count == 1)
        await fixture.replies.releaseAcceptance()
    }

    @Test func internalDeliveryStatesAreNotComposerWarnings() {
        for status in [PromptSubmissionStatus.pending, .retrying, .checkingReceipt,
                       .receiptUnresolved, .waitingForPreviousTurn, .opening, .openingFailed] {
            #expect(promptSubmissionPresentation(status.rawValue, language: .chinese) == nil)
        }
        #expect(promptSubmissionPresentation(PromptSubmissionStatus.awaitingConfirmation.rawValue,
            language: .chinese) != nil)
        #expect(promptSubmissionPresentation(PromptSubmissionStatus.openingFailed.rawValue,
            language: .chinese, needsConnectionRecovery: true) != nil)
    }

    @Test func storageConfirmationIsDurableAndNeverAutomaticallyCreatesATurn() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        fixture.model.prompt = "Begin the planned research"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(original.sourceAgent == 7 && original.sourceRunID == "previous-turn")
        #expect(original.receiptUncertain == false && !original.resumesAutomatically)
        #expect(original.storageEstimate?.totalBytes == 50_000_000_000)
        #expect(original.storageEstimate?.freeBytes == 80_000_000_000)
        #expect(original.conversationLabel == "Storage confirmation needed")
        #expect(promptSubmissionPresentation(original.status.rawValue, language: .chinese)?.title
            == "等待容量确认，尚未开始下一轮")
        #expect(promptSubmissionDetail(original, language: .chinese).contains("46.60 GiB"))
        #expect(original.storageEstimate?.warningThresholdBytes == 5_368_709_120)
        #expect(promptSubmissionDetail(original, language: .chinese).contains("超过 5.00 GiB 的容量确认阈值"))
        #expect(promptSubmissionDetail(original, language: .chinese).contains("空间足够"))
        #expect(promptSubmissionDetail(original, language: .english).contains("which is sufficient"))
        fixture.store.flushPendingWrites()
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.storageEstimate
            == original.storageEstimate)
        fixture.model.resumePendingPromptSubmissions()
        try await Task.sleep(for: .milliseconds(50))
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(await fixture.replies.createdCommands.isEmpty)
        #expect(!fixture.model.canSubmitPrompt)
    }

    @Test func storageConfirmationDistinguishesInsufficientSpaceAndLegacyReceipts() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.prompt = "Original message"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation }
        var pending = try #require(fixture.model.pendingPromptSubmission)
        pending.storageEstimate = .init(totalBytes: 50_000_000_000, total: "46.60 GiB",
            freeBytes: 1_000_000, free: "1 MiB", fits: false, confirmationRequired: true,
            warningThresholdBytes: 5_368_709_120, warningThreshold: "5.00 GiB")
        #expect(promptSubmissionDetail(pending, language: .chinese).contains("空间不足"))
        #expect(!promptSubmissionDetail(pending, language: .chinese).contains("空间足够"))
        #expect(promptSubmissionDetail(pending, language: .english).contains("insufficient"))
        pending.storageEstimate = .init(totalBytes: nil, total: "46.60 GiB",
            freeBytes: nil, free: "74.51 GiB", fits: nil, confirmationRequired: true)
        #expect(!promptSubmissionDetail(pending, language: .chinese).contains("阈值"))
        #expect(!promptSubmissionDetail(pending, language: .chinese).contains("空间足够"))
        #expect(await fixture.replies.createdCommands.isEmpty)
    }

    @Test func viewingAnotherAgentCannotHideTheOriginalStorageConfirmation() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        fixture.model.prompt = "Keep the chosen continuation source"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation }
        let original = try #require(fixture.model.pendingPromptSubmission)
        fixture.model.selectedAgent = 6
        fixture.model.restoreVisiblePromptSubmission()
        #expect(fixture.model.pendingPromptSubmission?.commandID == original.commandID)
        #expect(fixture.model.pendingPromptSubmission?.sourceAgent == 7)
        await fixture.model.confirmStorageUsage(expectedCommandID: original.commandID)
        try await waitUntil { await fixture.replies.startCommands.count == 2 }
        let body = try #require(JSONSerialization.jsonObject(with: await fixture.replies.startBodies[1]) as? [String: Any])
        #expect(body["agent"] as? Int == 7)
        #expect(body["prompt"] as? String == original.prompt)
        #expect(body["confirm_large_run"] as? Bool == true)
        #expect(await fixture.replies.startCommands == [original.commandID, original.commandID + "-confirmed"])
        #expect(await fixture.replies.allStartsWereDurable)
    }

    @Test func legacyStorageReceiptsAreHydratedReadOnlyWithoutSendingAgain() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.pendingPromptSubmission?.storageEstimate != nil }
        fixture.model.resumePendingPromptSubmissions()
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation)
    }

    @Test func aLateStorageReceiptCannotResurrectACancelledMessage() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        await fixture.replies.holdReceipts()
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { await fixture.replies.receiptCommands.count == 1 }
        fixture.model.cancelStorageUsage(expectedCommandID: original.commandID)
        await fixture.replies.releaseReceipts()
        try await Task.sleep(for: .milliseconds(60))
        fixture.model.resumePendingPromptSubmissions()
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(fixture.model.prompt == original.prompt)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func storageConfirmationAndReturnToDraftHaveOnlyOnePersistentOwner() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        #expect(fixture.store.returnPromptSubmissionToDraft(original, draftContext: "original-draft"))
        await fixture.model.confirmStorageUsage(expectedCommandID: original.commandID)
        #expect(fixture.model.pendingPromptSubmission == nil)
        #expect(fixture.store.loadSnapshot().promptDraftsByContext["original-draft"] == original.prompt)
        #expect(await fixture.replies.startCommands.isEmpty)

        #expect(await fixture.store.recordPromptSubmission(original))
        var confirmed = original
        confirmed.confirmLargeRun = true
        confirmed.status = .pending
        #expect(fixture.store.updateStorageConfirmation(original, with: confirmed))
        #expect(!fixture.store.updateStorageConfirmation(original, with: confirmed))
        #expect(!fixture.store.returnPromptSubmissionToDraft(original, draftContext: "other-draft"))
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey] == confirmed)
    }

    @Test func returningStorageConfirmationNeverOverwritesAnotherPersistedDraft() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.store.savePromptDrafts(["shared-draft": "A newer draft from another view"])
        fixture.store.flushPendingWrites()
        #expect(!fixture.store.returnPromptSubmissionToDraft(original, draftContext: "shared-draft"))
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptDraftsByContext["shared-draft"] == "A newer draft from another view")
        #expect(saved.promptOutboxByWorkspace[original.contextKey] == original)
    }

    @Test func oldConfirmationActionsCannotOperateOnADifferentMessage() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        await fixture.model.confirmStorageUsage(expectedCommandID: "another-command")
        fixture.model.cancelStorageUsage(expectedCommandID: "another-command")
        #expect(fixture.model.pendingPromptSubmission == original)
        #expect(await fixture.replies.startCommands.isEmpty)
        fixture.model.selectedConversationID = "another-conversation"
        fixture.model.restoreVisiblePromptSubmission()
        #expect(fixture.model.pendingPromptSubmission == nil)
        #expect(fixture.model.promptOutboxByWorkspace[original.contextKey] == original)
    }

    @Test func returningStorageConfirmationToDraftIsAtomicAndDoesNotOverwriteALaterDraft() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        var original = submission(status: .awaitingConfirmation, sourceRunID: "previous-turn", sourceAgent: 7)
        original.receiptUncertain = false
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.updatePrompt("A separate later draft")
        fixture.model.cancelStorageUsage(expectedCommandID: original.commandID)
        #expect(fixture.model.prompt == "A separate later draft")
        #expect(fixture.model.pendingPromptSubmission == original)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey] == original)
        fixture.model.updatePrompt("")
        fixture.model.cancelStorageUsage(expectedCommandID: original.commandID)
        #expect(fixture.model.prompt == original.prompt)
        #expect(fixture.model.pendingPromptSubmission == nil)
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptOutboxByWorkspace.isEmpty)
        #expect(saved.promptDraftsByContext[fixture.model.currentPromptDraftContext()] == original.prompt)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test(arguments: ["discarded", "cancelled", "canceled", "stopped", "interrupted"])
    func stoppedTurnKeepsSavedConversationSeparateFromCancelledCandidate(_ status: String) throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: status)
        let model = fixture.model
        model.settings.resumeSessionID = "unrelated-workspace-session"
        model.prompt = "A later question, not the cancelled request"
        let run = try #require(model.activeRun)
        #expect(run.agents.allSatisfy { !$0.mayContinue })
        #expect(model.promptSubmissionPlan == .start(resuming: true))
        let canSend = model.canSubmitPrompt
        #expect(canSend)
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, language: .chinese)
            == "本轮已停止；下一条消息从已保存的对话继续")
    }

    @Test(arguments: ["discarded", "cancelled"])
    func stoppedTurnSendsOnlyNewPromptUsingItsOwnSavedSession(_ status: String) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: status)
        let model = fixture.model
        model.settings.resumeSessionID = "unrelated-workspace-session"
        model.prompt = "My new question"
        try await Task.sleep(for: .milliseconds(60))
        #expect(await fixture.replies.startCommands.isEmpty)
        await fixture.replies.holdAcceptance()
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let saved = try #require(fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first)
        #expect(saved.prompt == "My new question")
        #expect(saved.resumeSessionID == "saved-conversation-session")
        #expect(saved.sourceRunID == nil && saved.sourceAgent == nil)
        #expect(saved.conversationID == "delivery-conversation")
        let body = try #require(JSONSerialization.jsonObject(with: await fixture.replies.startBodies[0]) as? [String: Any])
        #expect(body["resume_session_id"] as? String == "saved-conversation-session")
        #expect(body["prompt"] as? String == "My new question")
        #expect(body["run_id"] == nil && body["agent"] == nil)
        #expect(await fixture.replies.startActions == ["turns.start"])
        #expect(model.activeRun?.normalizedStatus == status)
        #expect(model.activeRun?.agents[0].status == "cancelled")
        await fixture.replies.releaseAcceptance()
        try await waitUntil { !model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(await fixture.replies.startCommands == [saved.commandID])
    }

    @Test func stoppedFirstTurnNeverBorrowsAnotherConversationsSession() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: "discarded", savedSession: nil)
        let model = fixture.model
        model.settings.resumeSessionID = "unrelated-workspace-session"
        model.prompt = "A new question without saved history"
        #expect(model.promptSubmissionPlan == .start(resuming: false))
        let canSend = model.canSubmitPrompt
        #expect(canSend)
        #expect(ResultAgentContext.text(run: try #require(model.activeRun), viewedAgent: 1, language: .chinese)
            == "本轮已停止；下一条消息将开始新对话")
    }

    @Test(arguments: ["discarded", "cancelled"])
    func savedSessionResumeOpensTheCanonicalParentWithoutAnotherSubmit(_ stoppedStatus: String) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: stoppedStatus)
        await fixture.replies.inferSavedSessionParent()
        let answer = "The correct saved-session answer, not the stopped request."
        await fixture.replies.showDestinationAnswer(answer)
        await fixture.replies.allowOpening()
        fixture.model.updatePrompt("Continue the saved conversation, not the stopped request")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.openRequests > 0
            && fixture.model.acceptedPromptAdoptionsInFlight.isEmpty }
        let commands = await fixture.replies.startCommands
        #expect(commands.count == 1)
        #expect(await fixture.replies.startActions == ["turns.start"])
        #expect(await fixture.replies.createdCommands == Set(commands))
        #expect(fixture.model.activeRun?.runId == "accepted")
        #expect(fixture.model.activeRun?.continuedFromRunId == "saved-parent")
        #expect(fixture.model.activeRun?.continuedFromAgent == 7)
        #expect(fixture.model.activeRun?.config.resumeSessionId == "saved-conversation-session")
        #expect(fixture.model.pendingPromptSubmission == nil)
        #expect(fixture.model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        try await waitUntil { fixture.model.transcripts[1]?.finalMessageReadStatus == "success" }
        #expect(fixture.model.selectedAgent == 1)
        #expect(fixture.model.transcripts[1]?.sourceRunID == "accepted")
        #expect(fixture.model.transcripts[1]?.sourceAgent == 1)
        #expect(fixture.model.selectedTranscriptBlocks.contains { $0.isFinalAnswer && $0.text == answer })
        #expect(fixture.model.selectedTimelineItems.contains {
            if case .message(let block) = $0 { return block.isFinalAnswer && block.text == answer }
            return false
        })
        #expect(await fixture.replies.startCommands == commands)
    }

    @Test(arguments: [false, true])
    func aPersistedSessionHandoffRecoversReadOnlyAfterRestart(inBackground: Bool) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        var settings = RunSettings()
        settings.resumeSessionID = "saved-conversation-session"
        var original = PersistedPromptSubmission(id: "saved-session-handoff", machineID: "local",
            workspaceID: "workspace", prompt: "The already accepted question", settings: settings,
            sourceRunID: nil, sourceAgent: nil, resumeSessionID: settings.resumeSessionID,
            confirmLargeRun: false, attempt: 0, status: .openingFailed, error: "Old destination conflict",
            errorCode: "prompt_destination_conflict", receiptUncertain: false,
            conversationID: "delivery-conversation", workspacePath: "/qa")
        original.firstAttemptAt = Date(timeIntervalSinceNow: -3600)
        #expect(await fixture.store.recordPromptSubmission(original))
        let result = PersistedAcceptedPromptResult(submission: original, runID: "accepted",
            continuedSessionID: "", workspaceRevision: 1)
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: original.contextKey,
            expected: original, result: result))
        await fixture.replies.inferSavedSessionParent()
        await fixture.replies.allowOpening()
        fixture.model.stopWindowObservation()
        let diskStore = DesktopStateStore(directory: fixture.directory)
        let restored = AppModel(client: fixture.client, windowID: UUID(), desktopStateStore: diskStore,
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { restored.stopWindowObservation() }
        restored.workspaces = fixture.model.workspaces
        restored.selectedWorkspaceID = "workspace"
        restored.selectedConversationID = inBackground ? "another-conversation" : "delivery-conversation"
        restored.updatePrompt("A later draft")
        restored.resumePendingPromptSubmissions()
        try await waitUntil { await fixture.replies.openRequests > 0
            && restored.acceptedPromptAdoptionsInFlight.isEmpty }
        #expect(restored.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(diskStore.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(restored.pendingPromptSubmission == nil)
        #expect(restored.prompt == "A later draft")
        #expect(restored.activeRun?.runId == (inBackground ? nil : "accepted"))
        #expect(restored.selectedConversationID == (inBackground ? "another-conversation" : "delivery-conversation"))
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let cached = await cold.hydrate(for: WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted"))
        #expect(cached?.runSummary?.prompt == original.prompt)
        #expect(cached?.runSummary?.config.submissionCommand?.commandId == original.commandID)
        #expect(cached?.runSummary?.continuedFromRunId == "saved-parent")
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(await fixture.replies.createdCommands.isEmpty)
    }

    @Test func anAlreadyOpenedAcceptedMessageHasOneCurrentBubbleAndKeepsRealRepeatedHistory() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let blocks = fixture.model.selectedTranscriptBlocks
        #expect(blocks.filter { $0.kind == .user && $0.text == result.submission.prompt }.count == 2)
        #expect(blocks.filter { $0.isFinalAnswer && $0.text == "Complete answer from Agent 2." }.count == 1)
        #expect(blocks.filter { $0.kind == .user }.allSatisfy { $0.status == nil })
        #expect(fixture.model.conversationSurfaceState?.toolbarStatusLabel(language: .english) == "Completed")
        #expect(fixture.model.currentConversationAgentSwitchState?.selectedAgent == 2)
        #expect(fixture.model.currentConversationAgentSwitchState?.selectionEnabled == true)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[result.contextKey] != nil)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func reconcilingAnAlreadyOpenedRunPreservesItsAnswerAgentHistoryAndDraft() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let model = fixture.model
        let transcripts = model.transcripts
        let history = model.resumeHistory
        let navigation = model.conversationNavigationRequestID
        let agentSelection = model.agentSelectionRequestID
        model.workspaceRevision = 6
        model.updatePrompt("Keep the next draft")
        #expect(await model.adoptAcceptedPromptResult(result))
        #expect(model.transcripts == transcripts)
        #expect(model.resumeHistory == history)
        #expect(model.selectedAgent == 2)
        #expect(model.conversationNavigationRequestID == navigation)
        #expect(model.agentSelectionRequestID == agentSelection)
        #expect(model.workspaceRevision == 6)
        #expect(model.prompt == "Keep the next draft")
        #expect(model.selectedTranscriptBlocks.filter { $0.kind == .user && $0.text == result.submission.prompt }.count == 2)
        #expect(model.pendingPromptSubmission == nil && model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let cached = try #require(await cold.hydrate(for: WorkspaceRunViewKey(machineID: "local",
            workspaceID: "workspace", runID: result.runID)))
        #expect(cached.snapshot.transcripts == transcripts)
        #expect(cached.snapshot.conversationHistory == history)
        #expect(cached.snapshot.selectedAgent == 2)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func identicalTextWithADifferentCommandIsNotAbsorbedIntoAnOpenedTurn() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let original = result.submission
        let next = PersistedPromptSubmission(id: "a-real-later-message", machineID: original.machineID,
            workspaceID: original.workspaceID, prompt: original.prompt, settings: original.settings,
            sourceRunID: original.sourceRunID, sourceAgent: original.sourceAgent,
            resumeSessionID: original.resumeSessionID, confirmLargeRun: false, attempt: 0,
            status: .pending, error: "", conversationID: original.conversationID, workspacePath: original.workspacePath)
        fixture.model.pendingPromptSubmission = next
        let blocks = fixture.model.selectedTranscriptBlocks
        #expect(blocks.filter { $0.kind == .user && $0.text == original.prompt }.count == 3)
        #expect(blocks.last(where: { $0.kind == .user })?.status == next.conversationLabel)
        #expect(blocks.contains { $0.isFinalAnswer && $0.text == "Complete answer from Agent 2." })
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func lateHandoffPersistenceCannotRewindANewerVisibleAnswerOrAgentChoice() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let model = fixture.model
        model.cacheVisibleRunViewState()
        let gate = DispatchGroup()
        let entered = DispatchGroup()
        gate.enter(); entered.enter()
        let blocker = Task { await ClientBlockingIO.cache { entered.leave(); gate.wait() } }
        var released = false
        defer { if !released { gate.leave() } }
        func cacheWasEntered() -> Bool { entered.wait(timeout: .now()) == .success }
        try await waitUntil { cacheWasEntered() }
        let settling = Task { await model.adoptAcceptedPromptResult(result) }
        try await waitUntil { model.acceptedPromptAdoptionsInFlight.contains(result.commandID) }
        try await Task.sleep(for: .milliseconds(30))
        model.transcripts[2]?.finalMessage = "A newer answer that arrived during the disk write."
        model.selectedAgent = 1
        model.cacheVisibleRunViewState()
        gate.leave()
        released = true
        #expect(await settling.value)
        await blocker.value
        #expect(model.transcripts[2]?.finalMessage == "A newer answer that arrived during the disk write.")
        #expect(model.selectedAgent == 1)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: result.runID)
        #expect(model.runViewStateStore.snapshot(for: key)?.transcripts[2]?.finalMessage
            == "A newer answer that arrived during the disk write.")
        let immediateCold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        #expect(await immediateCold.hydrate(for: key)?.snapshot.transcripts[2]?.finalMessage
            == "A newer answer that arrived during the disk write.")
        #expect(model.runViewStateStore.flushPendingWrites(timeout: 3))
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        #expect(await cold.hydrate(for: key)?.snapshot.transcripts[2]?.finalMessage
            == "A newer answer that arrived during the disk write.")
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func anOpenedReceiptSurvivesWriteFailureWithoutLosingTheVisibleAnswer() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        try Data("not a directory".utf8).write(to: fixture.cacheDirectory)
        #expect(await fixture.model.adoptAcceptedPromptResult(result) == false)
        #expect(fixture.model.transcripts[2]?.finalMessage == "Complete answer from Agent 2.")
        #expect(fixture.model.selectedAgent == 2)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[result.contextKey] != nil)
        try FileManager.default.removeItem(at: fixture.cacheDirectory)
        #expect(await fixture.model.adoptAcceptedPromptResult(result))
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let saved = await cold.hydrate(for: WorkspaceRunViewKey(machineID: "local",
            workspaceID: "workspace", runID: result.runID))
        #expect(saved?.snapshot.transcripts[2]?.finalMessage == "Complete answer from Agent 2.")
        #expect(saved?.snapshot.selectedAgent == 2)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func theNativeTimelineShowsOneCurrentMessageBeforeAndAfterReceiptSettlement() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let surface = NativeHandoffSurface(model: fixture.model)
        defer { surface.close() }
        for settled in [false, true] {
            if settled { #expect(await fixture.model.adoptAcceptedPromptResult(result)) }
            surface.update()
            try await waitUntil { surface.texts.contains("Complete answer from Agent 2.") }
            #expect(surface.texts.filter { $0 == result.submission.prompt }.count == 2)
            #expect(surface.texts.filter { $0 == "Complete answer from Agent 2." }.count == 1)
            #expect(!surface.texts.contains("Sent"))
            #expect(!surface.window.isVisible && !surface.window.isKeyWindow)
            if let path = ProcessInfo.processInfo.environment["PCR_SETTLEMENT_EVIDENCE_DIR"] {
                try surface.framePNG().write(to: URL(fileURLWithPath: path)
                    .appendingPathComponent(settled ? "native-settled.png" : "native-held-receipt.png"))
            }
        }
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test(arguments: ["workspace", "prompt", "command", "resume_session"])
    func anOpenedTurnWithConflictingIdentityCannotAbsorbOrConsumeTheReceipt(_ conflict: String) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let result = try await fixture.installOpenedAcceptedHandoff()
        let run = try #require(fixture.model.activeRun)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        var config = try #require(json["config"] as? [String: Any])
        switch conflict {
        case "workspace": json["workspace"] = "/different-workspace"
        case "prompt": json["prompt"] = "Another actual question"
        case "command": config["submissionCommand"] = ["clientId": "mac-app", "commandId": "another-command", "fingerprint": "fixture"]
        default: config["resumeSessionId"] = "a-different-session"
        }
        json["config"] = config
        fixture.model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        #expect(fixture.model.unrepresentedPromptSubmission?.commandID == result.commandID)
        #expect(fixture.model.currentConversationAgentSwitchState == nil)
        #expect(await fixture.model.adoptAcceptedPromptResult(result) == false)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[result.contextKey] != nil)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test(arguments: [AgentContinuationSelection.Status.pending, .unavailable])
    func staleCandidateChoiceCannotBlockTheSavedConversation(_ status: AgentContinuationSelection.Status) throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: "discarded")
        let model = fixture.model
        let key = try #require(model.selectedAgentContextKey())
        model.continuationSelections[key] = .init(id: UUID(), agent: 1, status: status)
        let needsConfirmation = model.continuationSelectionNeedsConfirmation
        #expect(!needsConfirmation)
        #expect(model.continuationSelectionContextText == nil)
        #expect(model.promptSubmissionPlan == .start(resuming: true))
    }

    @Test func stoppedInspectorUsesTheActualDestinationAndConnectionGates() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: "discarded")
        let model = fixture.model
        let run = try #require(model.activeRun)
        model.conversationNavigationTarget = .init(requestID: model.conversationNavigationRequestID,
            machineID: "local", workspaceID: "workspace", conversationID: "delivery-conversation", runID: "another-turn")
        #expect(model.promptSubmissionPlan == .enqueue)
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, submissionPlan: model.promptSubmissionPlan,
            language: .chinese) == "本轮已停止；后续消息保留在队列中，等待处理")
        model.conversationNavigationTarget = nil
        model.daemonOnline = false
        #expect(model.promptSubmissionPlan == .enqueue)
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, submissionPlan: model.promptSubmissionPlan,
            language: .chinese) == "本轮已停止；后续消息保留在队列中，等待处理")
        #expect(ResultAgentContext.text(run: run, viewedAgent: 1, submissionPlan: .enqueue,
            language: .chinese) == "本轮已停止；后续消息保留在队列中，等待处理")
    }

    @Test func stoppingAndUnchosenSuccessfulResultsKeepTheirExistingGuards() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: "stopping")
        fixture.model.prompt = "Wait for the worker to stop"
        #expect(fixture.model.promptSubmissionPlan == .enqueue)
        try fixture.installCompletedConversation(finalized: false, status: "cancelled")
        let run = try #require(fixture.model.activeRun)
        #expect(run.isReviewable)
        #expect(fixture.model.promptSubmissionPlan == .continueFrom(runID: "previous-turn", agent: 7,
            agentName: run.displayName(forAgent: 7, language: fixture.model.appLanguage), stopsRemainingAgents: false))
        try fixture.installCompletedConversation(finalized: false, status: "sync_ambiguous")
        #expect(fixture.model.promptSubmissionPlan == .enqueue)
    }

    @Test func daemonOwnedHandoffWaitsForReceiptWithoutResubmittingOrLosingDraft() async throws {
        let fixture = try Fixture(mode: .daemonOwned)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        model.prompt = "Continue after the original result is saved"
        await model.submitPrompt()
        try await waitUntil {
            let queried = !(await fixture.replies.receiptCommands.isEmpty)
            return model.pendingPromptSubmission?.status == .waitingForPreviousTurn && queried
        }
        let saved = try #require(model.pendingPromptSubmission)
        #expect(saved.sourceRunID == "previous-turn")
        #expect(saved.sourceAgent == 7)
        #expect(await fixture.replies.startCommands == [saved.commandID])
        model.updatePrompt("Keep the following draft")
        #expect(model.canSubmitPrompt)
        try await waitUntil { await fixture.replies.receiptCommands.count >= 2 }
        #expect(await fixture.replies.startCommands == [saved.commandID])
        await fixture.replies.finishDaemonHandoff()
        try await waitUntil { !model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(await fixture.replies.startCommands == [saved.commandID])
        #expect(await fixture.replies.receiptCommands.allSatisfy { $0 == saved.commandID })
        #expect(model.prompt == "Keep the following draft")
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(!model.acceptedPromptResultsByWorkspace.isEmpty || model.activeRun?.runId == "accepted")
        #expect(model.promptSubmissionPlan == .enqueue)
        await fixture.replies.allowOpening()
        model.resumePendingPromptSubmissions()
        try await waitUntil { model.activeRun?.runId == "accepted" }
        #expect(model.pendingPromptSubmission == nil)
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(model.prompt == "Keep the following draft")
        #expect(await fixture.replies.startCommands == [saved.commandID])
    }

    @Test func completedConversationAcceptsMessagesWhileReadingContentIsHydrating() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        model.workspaceContextLoadingID = "workspace"
        model.prompt = "Do not wait for historical pages"
        #expect(model.promptSubmissionPlan.isAvailable)
        #expect(model.canSubmitPrompt)
        await fixture.replies.holdAcceptance()
        await model.submitPrompt()
        let saved = fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first
        #expect(saved?.sourceRunID == "previous-turn")
        #expect(saved?.sourceAgent == 7)
        #expect(saved?.prompt == "Do not wait for historical pages")
        #expect(model.prompt.isEmpty)
        #expect(model.workspaceContextLoadingID == "workspace")
        await fixture.replies.releaseAcceptance()
    }

    @Test func alreadyBoundConversationDoesNotWaitForOpeningEnrichment() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.conversationNavigationTarget = .init(
            requestID: fixture.model.conversationNavigationRequestID, machineID: "local",
            workspaceID: "workspace", conversationID: "delivery-conversation", runID: "previous-turn"
        )
        fixture.model.workspaceContextLoadingID = "workspace"
        fixture.model.prompt = "The destination is already known"
        #expect(fixture.model.canSubmitPrompt)
        fixture.model.conversationNavigationTarget = .init(
            requestID: fixture.model.conversationNavigationRequestID, machineID: "local",
            workspaceID: "workspace", conversationID: "different-conversation", runID: "different-turn"
        )
        #expect(fixture.model.canSubmitPrompt)
    }

    @Test func directContinuationCapturesTheChosenAgentBeforeSelectionAcknowledgement() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let model = fixture.model
        model.selectedAgent = 6
        let key = try #require(model.selectedAgentContextKey())
        model.continuationSelections[key] = .init(id: UUID(), agent: 6, status: .pending)
        model.daemonFollowUpSnapshot = try JSONDecoder().decode(RemoteFollowUpQueue.self, from: Data("""
            {"workspaceId":"workspace","runId":"previous-turn","selectedAgent":7,"items":[],"state":"empty"}
            """.utf8))
        let gate = SelectionGate()
        model.continuationSelectionTasks[key] = Task {
            await gate.wait()
            model.continuationSelections[key]?.status = .confirmed
            model.continuationSelectionTasks[key] = nil
        }
        model.prompt = "Continue the explicitly selected branch"
        #expect(model.canSubmitPrompt)
        await fixture.replies.holdAcceptance()
        let submit = Task { await model.submitPrompt() }
        try? await waitUntil(timeout: .seconds(1)) {
            !fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty
        }
        let saved = fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first
        #expect(saved?.sourceAgent == 6)
        #expect(saved?.sourceRunID == "previous-turn")
        #expect(model.prompt.isEmpty)
        #expect(model.continuationSelectionTasks[key] != nil)
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let transmitted = try #require(await fixture.replies.startBodies.first)
        let body = try #require(JSONSerialization.jsonObject(with: transmitted) as? [String: Any])
        #expect(body["agent"] as? Int == 6)
        #expect(body["run_id"] as? String == "previous-turn")
        #expect(await fixture.replies.allStartsWereDurable)
        model.selectedAgent = 7
        model.updatePrompt("A later draft must remain separate")
        await gate.release()
        await submit.value
        await fixture.replies.releaseAcceptance()
        #expect(model.prompt == "A later draft must remain separate")
        #expect(await fixture.replies.startCommands.count == 1)
    }

    @Test func unresolvedNavigationCannotSendFromTheOldVisibleTurn() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        model.conversationNavigationTarget = .init(requestID: model.conversationNavigationRequestID,
            machineID: "local", workspaceID: "workspace", conversationID: "delivery-conversation", runID: "newer-turn")
        model.prompt = "This belongs to the requested destination"
        #expect(model.promptSubmissionPlan == .enqueue)
        #expect(model.canSubmitPrompt)
        #expect(model.promptComposerContextText == nil)
        await model.submitPrompt()
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(model.prompt.isEmpty)
        #expect(model.visibleDeferredPrompts.first?.destination == .run("newer-turn", agent: nil))
        #expect(model.visibleDeferredPrompts.first?.item.prompt == "This belongs to the requested destination")
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test(arguments: [false, true])
    func anotherContextCannotHoldTheComposerNavigationGate(differentMachine: Bool) throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.conversationNavigationTarget = .init(requestID: fixture.model.conversationNavigationRequestID,
            machineID: differentMachine ? "other-machine" : "local",
            workspaceID: differentMachine ? "workspace" : "other-workspace",
            conversationID: "different-conversation", runID: "different-turn")
        fixture.model.prompt = "Only this workspace owns this draft"
        #expect(fixture.model.canSubmitPrompt)
    }

    @Test func remoteTransitionBlocksOnlyUntilItsDestinationIsBound() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(runID: "newer-turn")
        let newer = try #require(fixture.model.activeRun)
        try fixture.installCompletedConversation()
        let older = try #require(fixture.model.activeRun)
        try fixture.installCompletedConversation(runID: "newer-turn")
        let model = fixture.model
        model.workspaceConversations = [.init(id: "delivery-conversation", title: "Conversation",
            runs: [older, newer], resumeSession: nil)]
        model.activeRun = older
        model.remoteTurnTransition = .init(requestID: 1, machineID: "local",
            workspaceID: "workspace", conversationID: "delivery-conversation")
        model.prompt = "Follow the new turn without waiting for its transcript"
        #expect(model.canSubmitPrompt)
        model.activeRun = newer
        model.workspaceContextLoadingID = "workspace"
        #expect(model.canSubmitPrompt)
        #expect(model.remoteTurnTransitionInProgress)
        model.remoteTurnTransition = nil
        model.activeRun = older
        #expect(!model.canSubmitPrompt)
        #expect(model.promptSubmissionPlan.recoveryAction == .openLatestConversationTurn)
    }

    @Test func queueSelectionFailureDoesNotChangeAnExplicitContinuationSource() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let model = fixture.model
        let key = try #require(model.selectedAgentContextKey())
        model.continuationSelections[key] = .init(id: UUID(), agent: 6, status: .unavailable)
        model.prompt = "A separate queue-selection error is not continuation authorization"
        #expect(model.promptSubmissionPlan == .continueFrom(runID: "previous-turn", agent: 6,
            agentName: model.activeRun!.displayName(forAgent: 6, language: model.appLanguage), stopsRemainingAgents: false))
        var run = try #require(model.activeRun)
        run.agents[0].status = "failed"
        model.activeRun = run
        #expect(!model.canSubmitPrompt)
        #expect(model.promptSubmissionPlan == .blocked("Select a successful Agent to continue"))
    }

    @Test func cachedDestinationCanBeSavedWhileItsKnownConnectionRecovers() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        model.systemCapabilitiesByMachine["local"] = try JSONDecoder().decode(SystemCapabilities.self, from: Data("""
            {"daemonVersion":"0.1.69","actions":["system.health","turns.continue"]}
            """.utf8))
        model.daemonOnline = false
        model.workspaceContextLoadingID = "workspace"
        model.prompt = "Save first; do not pretend the server accepted it"
        await fixture.replies.holdCapabilities()
        #expect(model.canSubmitPrompt)
        await model.submitPrompt()
        let saved = try #require(fixture.store.loadSnapshot().deferredPromptsByWorkspace.values.first?.first)
        #expect(saved.destination == .run("previous-turn", agent: 7))
        #expect(saved.item.prompt == "Save first; do not pretend the server accepted it")
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands.isEmpty)
        await fixture.replies.releaseCapabilities()
    }

    @Test func aKnownWorkspaceCanRetainAnOfflineMessageWithoutGuessingAnotherDestination() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.daemonOnline = false
        fixture.model.prompt = "Do not guess a disconnected destination"
        #expect(fixture.model.canSubmitPrompt)
        #expect(fixture.model.promptComposerContextText == nil)
        await fixture.model.submitPrompt()
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(fixture.model.prompt.isEmpty)
        #expect(fixture.model.visibleDeferredPrompts.first?.destination == .run("previous-turn", agent: 7))
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func anotherConversationOutboxCannotSilentlySwallowThisDraft() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let pending = submission(status: .receiptUnresolved)
        fixture.model.promptOutboxByWorkspace[pending.contextKey] = pending
        fixture.model.pendingPromptSubmission = nil
        fixture.model.prompt = "Keep this new draft too"
        #expect(fixture.model.canSubmitPrompt)
        #expect(fixture.model.promptSubmissionPlan == .enqueue)
        #expect(fixture.model.promptComposerContextText == nil)
        await fixture.model.submitPrompt()
        #expect(fixture.model.prompt.isEmpty)
        #expect(fixture.model.visibleDeferredPrompts.first?.item.prompt == "Keep this new draft too")
        #expect(fixture.model.promptOutboxByWorkspace[pending.contextKey] == pending)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func resultMutationIsNotMistakenForReadingHydration() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        model.prompt = "Do not submit from a source being discarded"
        model.isRunOperationInProgress = true
        let context = try #require(model.currentRunOperationContext())
        model.runOperationsByContext[context.key] = .discardTurn
        #expect(!model.canSubmitPrompt)
        #expect(model.promptComposerContextText != nil)
        model.isRunOperationInProgress = false
        model.runOperationsByContext.removeAll()
        model.workspaceContextLoadingID = "workspace"
        model.diffLoading = true
        model.workspaceFileLoading = true
        model.pendingAgentSelection = 7
        #expect(model.canSubmitPrompt)
    }

    @Test func cachedNavigationCanSubmitBeforeItsBackgroundSummaryReadReturns() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        let source = try #require(model.activeRun)
        model.cacheVisibleRunViewState()
        model.selectedConversationID = "another-conversation"
        model.activeRun = nil
        model.selectedAgent = nil
        await fixture.replies.holdSourceReading(source)
        let opening = Task { await model.selectConversation("delivery-conversation") }
        try await waitUntil { await fixture.replies.sourceReads == 1 }
        model.updatePrompt("Send without waiting for the old summary")
        #expect(model.openingConversationID == "delivery-conversation")
        #expect(model.activeRun?.runId == "previous-turn")
        #expect(model.canSubmitPrompt)
        let action = ComposerPrimaryAction.make(hasDraft: true, canSend: model.canSubmitPrompt,
            runIsStoppable: false, queuesMessage: false, canStop: false, submissionInFlight: false)
        #expect(action.kind == .send && action.enabled)
        await model.submitPrompt()
        let saved = fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first
        #expect(saved?.sourceAgent == 7)
        #expect(saved?.sourceRunID == "previous-turn")
        #expect(await fixture.replies.sourceReadingHeld)
        await fixture.replies.releaseSourceReading()
        await opening.value
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        #expect(await fixture.replies.allStartsWereDurable)
    }

    @Test func acceptedDestinationCannotBeReplacedByALateSourceNavigationRead() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let model = fixture.model
        let source = try #require(model.activeRun)
        model.cacheVisibleRunViewState()
        model.selectedConversationID = "another-conversation"
        model.activeRun = nil
        await fixture.replies.holdSourceReading(source)
        let opening = Task { await model.selectConversation("delivery-conversation") }
        try await waitUntil { await fixture.replies.sourceReads == 1 }
        var body = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        body["runId"] = "accepted"
        body["prompt"] = "A new accepted question"
        let accepted = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: body))
        let response = RunStartResponse(status: "accepted", message: nil, runId: "accepted",
            storage: nil, run: accepted, workspaceId: "workspace", workspaceRevision: 1, continuedFrom: nil)
        #expect(try await model.activateStartedRun(response, expectedWorkspaceID: "workspace",
            target: .local, carriedHistory: []))
        model.updatePrompt("Keep this later draft")
        #expect(model.activeRun?.runId == "accepted")
        #expect(model.openingConversationID == nil)
        await fixture.replies.releaseSourceReading()
        await opening.value
        #expect(model.activeRun?.runId == "accepted")
        #expect(model.prompt == "Keep this later draft")
        #expect(model.workspaceContextLoadingID == nil)
        #expect(model.selectedConversationID == "delivery-conversation")
    }

    @Test func anAcceptedTurnCannotSendTheNextDraftFromItsStillVisibleSource() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.replies.allowOpening()
        await fixture.replies.holdOpening()
        let model = fixture.model
        model.prompt = "The first explicit continuation"
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.openRequests == 1 }
        #expect(model.activeRun?.runId == "previous-turn")
        #expect(model.promptOutboxByWorkspace.isEmpty)
        #expect(model.acceptedPromptResultsByWorkspace.values.first?.runID == "accepted")
        model.updatePrompt("The next draft must not branch from the old turn")
        #expect(model.canSubmitPrompt)
        await model.submitPrompt()
        #expect(await fixture.replies.startCommands.count == 1)
        // The durable handoff also protects another view of this workspace,
        // even when that view has not projected its pending bubble yet.
        model.pendingPromptSubmission = nil
        #expect(model.promptSubmissionPlan == .enqueue)
        #expect(model.promptComposerContextText == nil)
        await model.submitPrompt()
        #expect(await fixture.replies.startCommands.count == 1)
        await fixture.replies.releaseOpening()
        try await waitUntil { model.activeRun?.runId == "accepted" }
        #expect(model.prompt.isEmpty)
        #expect(model.visibleDeferredPrompts.first?.destination == .run("accepted", agent: nil))
        #expect(model.visibleDeferredPrompts.first?.item.prompt == "The next draft must not branch from the old turn")
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
    }

    @Test func unsuccessfulConversationOpeningReleasesOnlyItsOwnNavigationGate() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.model.selectConversation("no-such-conversation")
        #expect(fixture.model.openingConversationID == nil)
    }

    @Test func automaticRecoveryHasNoManualTransportControls() {
        for status in [PromptSubmissionStatus.pending, .retrying, .checkingReceipt, .waitingForPreviousTurn, .opening] {
            let submission = submission(status: status)
            #expect(!submission.allowsManualRecovery)
            #expect(promptSubmissionPresentation(status.rawValue, language: .chinese) == nil)
        }
        #expect(PromptSubmissionRetryPolicy.shouldRetry(PcrdClientError.remote("relay_unreachable", "offline")))
        #expect(!PromptSubmissionRetryPolicy.shouldRetry(PcrdClientError.remote("invalid_request", "rejected")))
    }

    @Test func durableDeliveryNeverTrapsTheUserInTheApplication() {
        #expect(ConversationContextTransitionPlan.make(destination: .applicationExit,
            run: nil, selectedAgent: nil, isLatestWorkspaceRun: true, syncBack: true,
            hasPendingSubmission: true, hasRunOperation: true) == .proceed)
    }

    @Test func deliveryHeaderDoesNotExposeInternalHandoffStages() throws {
        let fixture = try Fixture(mode: .rejected)
        defer { fixture.close() }
        for status in [PromptSubmissionStatus.waitingForPreviousTurn, .checkingReceipt, .receiptUnresolved] {
            fixture.model.pendingPromptSubmission = submission(status: status)
            #expect(fixture.model.conversationSurfaceState?.toolbarStatusLabel(language: .chinese) == nil)
        }
        #expect(fixture.model.conversationSurfaceState?.runPresentation?.animated == false)
        #expect(fixture.model.conversationSurfaceState?.runPresentation?.tone == .neutral)
    }

    @Test func editingCannotRemoveTheOnlyCopyWhenDraftPersistenceFails() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-draft-\(UUID().uuidString.prefix(8))")
        try Data("not a directory".utf8).write(to: directory)
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = AppModel(windowID: UUID(), desktopStateStore: DesktopStateStore(directory: directory))
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "workspace"
        model.selectedConversationID = "delivery-conversation"
        var rejected = submission(status: .failed)
        rejected.retryRequiresNewCommandID = true
        model.pendingPromptSubmission = rejected
        model.promptOutboxByWorkspace[rejected.contextKey] = rejected
        model.editPendingPrompt()
        #expect(model.pendingPromptSubmission == rejected)
        #expect(model.promptOutboxByWorkspace[rejected.contextKey] == rejected)
        #expect(model.prompt.isEmpty)
        #expect(model.errorMessage != nil)
    }

    @Test func lostAcceptanceReceiptsRecoverAutomaticallyWithoutDuplicateTasksOrEditing() async throws {
        let fixture = try Fixture(mode: .lostReceipt)
        defer { fixture.close() }
        let model = fixture.model
        model.prompt = "Deliver exactly once"
        await model.submitPrompt()
        try await waitUntil { model.pendingPromptSubmission?.status == .retrying }
        let original = try #require(model.pendingPromptSubmission)
        let projected = model.selectedTimelineItems
        let sequence = model.presentationProjectionSequence
        await model.retryPendingPrompt()
        model.editPendingPrompt()
        #expect(model.pendingPromptSubmission == original)
        #expect(model.prompt.isEmpty)
        #expect(model.selectedTimelineItems == projected)
        #expect(model.presentationProjectionSequence == sequence)
        #expect(await fixture.replies.startCommands.count == 2)
        #expect(await fixture.replies.createdCommands.count == 1)
        #expect(await fixture.replies.allStartsWereDurable)

        // A reply for the old workspace cannot replace the newly opened page.
        model.selectedWorkspaceID = "other"
        try await waitUntil { !model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(model.activeRun == nil)
        #expect(model.promptOutboxByWorkspace.isEmpty)
        #expect(await fixture.replies.createdCommands == [original.commandID])
        model.selectedWorkspaceID = "workspace"
        model.pendingPromptSubmission = nil
        model.resumePendingPromptSubmissions()
        try await waitUntil { await fixture.replies.openRequests > 0 }
        #expect(model.errorMessage == nil)
        #expect(!model.acceptedPromptResultsByWorkspace.isEmpty)
        model.updatePrompt(original.prompt)
        await fixture.replies.allowOpening()
        model.resumePendingPromptSubmissions()
        try await waitUntil { model.activeRun?.runId == "accepted" }
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(model.pendingPromptSubmission == nil)
        #expect(model.prompt == original.prompt)
        #expect(await fixture.replies.createdCommands.count == 1)
    }

    @Test func restoredOutboxUsesThePersistedCommandIdentity() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let original = submission(status: .retrying)
        #expect(await fixture.store.recordPromptSubmission(original))
        let restored = AppModel(client: fixture.client, windowID: UUID(), desktopStateStore: fixture.store)
        defer { restored.stopWindowObservation(); restored.promptOutboxByWorkspace.removeAll() }
        restored.selectedWorkspaceID = "other"
        restored.resumePendingPromptSubmissions()
        try await waitUntil { !restored.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(restored.promptOutboxByWorkspace.isEmpty)
        #expect(await fixture.replies.createdCommands == [original.commandID])
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptOutboxByWorkspace.isEmpty)
        #expect(saved.acceptedPromptResultsByWorkspace[original.contextKey]?.commandID == original.commandID)
    }

    @Test func aNewSubmitRefreshesTheReceiptStoreAfterTheDaemonWasUpgraded() async throws {
        let fixture = try Fixture(mode: .lostBeforeStart)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        let oldCapabilities = try JSONDecoder().decode(SystemCapabilities.self, from: Data("""
            {"daemonVersion":"0.1.69","actions":["system.health","turns.start","turns.continue"]}
            """.utf8))
        fixture.model.systemCapabilitiesByMachine["local"] = oldCapabilities
        fixture.model.systemCapabilities = oldCapabilities
        fixture.model.prompt = "An upgraded daemon must not inherit a stale receipt-store cache"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .retrying }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(original.receiptStoreID == "fixture-store")
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.receiptStoreID == "fixture-store")
        #expect(await fixture.replies.createdCommands.isEmpty)
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.startCommands == [original.commandID, original.commandID, original.commandID])
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.createdCommands == [original.commandID])
        #expect(await fixture.replies.allStartsWereDurable)
    }

    @Test func acceptanceAndOpeningCannotHijackAnotherConversationInTheSameWorkspace() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        await fixture.replies.holdOpening()
        let model = fixture.model
        model.prompt = "Stay with the originating conversation"
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.openRequests > 0 }
        let result = try #require(model.acceptedPromptResultsByWorkspace.values.first)
        #expect(result.submission.conversationID == "delivery-conversation")
        #expect(model.conversationSurfaceState?.toolbarStatusLabel(language: .chinese) == nil)
        #expect(model.promptComposerPlaceholder == model.appLanguage.text("输入下一条消息", "Message"))
        model.selectedConversationID = "another-conversation"
        model.conversationNavigationRequestID += 1
        model.restoreVisiblePromptSubmission()
        #expect(model.pendingPromptSubmission == nil)
        model.updatePrompt("Draft in the other conversation")
        await fixture.replies.releaseOpening()
        try await waitUntil { model.acceptedPromptAdoptionsInFlight.isEmpty }
        #expect(model.selectedConversationID == "another-conversation")
        #expect(model.activeRun == nil)
        #expect(model.prompt == "Draft in the other conversation")
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted")
        let coldCache = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let cached = await coldCache.hydrate(for: key)
        #expect(cached?.runSummary?.prompt == "Stay with the originating conversation")
        #expect(cached?.runSummary?.config.submissionCommand?.commandId == result.commandID)
        model.resumePendingPromptSubmissions()
        #expect(model.pendingPromptSubmission == nil)
        #expect(await fixture.replies.openRequests == 1)
        #expect(await fixture.replies.createdCommands.count == 1)
        #expect(await fixture.replies.startCommands.count == 1)
    }

    @Test func onlyTheActualAcceptanceReceiptChangesSendingToSentBeforeOpeningCompletes() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        await fixture.replies.holdAcceptance()
        await fixture.replies.allowOpening()
        await fixture.replies.holdOpening()
        let model = fixture.model
        model.prompt = "Acceptance is independent of answer and file loading"
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let sending = try #require(model.pendingPromptSubmission)
        #expect(sending.status == .pending)
        #expect(model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[sending.contextKey]?.commandID == sending.commandID)
        await model.submitPrompt()
        #expect(await fixture.replies.startCommands.count == 1)

        await fixture.replies.releaseAcceptance()
        try await waitUntil { await fixture.replies.openRequests == 1 }
        #expect(model.activeRun == nil)
        #expect(model.pendingPromptSubmission?.status == .opening)
        #expect(model.pendingPromptSubmission?.conversationLabel == "Sent")
        #expect(model.promptOutboxByWorkspace.isEmpty)
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptOutboxByWorkspace.isEmpty)
        #expect(saved.acceptedPromptResultsByWorkspace[sending.contextKey]?.commandID == sending.commandID)
        #expect(await fixture.replies.startCommands.count == 1)

        await fixture.replies.releaseOpening()
        try await waitUntil { model.activeRun?.runId == "accepted" }
        #expect(model.pendingPromptSubmission == nil)
        #expect(await fixture.replies.createdCommands.count == 1)
    }

    @Test func aFailedDurableWriteKeepsTheDraftAndNeverSends() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let blocked = fixture.directory.appendingPathComponent("not-a-directory")
        try Data("fixture".utf8).write(to: blocked)
        let model = AppModel(client: fixture.client, windowID: UUID(),
                             desktopStateStore: DesktopStateStore(directory: blocked))
        defer { model.stopWindowObservation() }
        model.workspaces = fixture.model.workspaces
        model.selectedWorkspaceID = "workspace"
        model.daemonOnline = true
        model.prompt = "Keep this draft"
        await model.submitPrompt()
        #expect(model.prompt == "Keep this draft")
        #expect(model.promptOutboxByWorkspace.isEmpty)
        #expect(model.pendingPromptSubmission == nil)
        #expect(model.errorMessage != nil)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func aRejectedResultReadIsNotMistakenForAnUnsentMessage() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        await fixture.replies.rejectOpening()
        fixture.model.prompt = "Accepted before authentication was lost"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .openingFailed }
        let opening = try #require(fixture.model.pendingPromptSubmission)
        #expect(opening.conversationLabel == "Sent")
        #expect(opening.needsConnectionRecovery && !opening.allowsManualRecovery)
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
        #expect(!fixture.model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(promptSubmissionDetail(opening, language: .english).contains("was sent"))
        await fixture.model.retryPendingPrompt()
        fixture.model.editPendingPrompt()
        #expect(await fixture.replies.startCommands.count == 1)
        await fixture.replies.allowOpening()
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(fixture.model.acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands.count == 1)
        #expect(!fixture.model.runPollingIsComplete(WorkspaceRunViewKey(
            machineID: "local", workspaceID: "workspace", runID: "accepted")))
    }

    @Test func onlyFullyCachedTerminalAnswersCanFinishPollingAfterAReadFailure() throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "cached")
        var payload: [String: Any] = ["runId": "cached", "status": "completed", "workspace": "/qa",
            "prompt": "A complete cached answer", "createdAt": "2026-10-04T00:00:00Z",
            "updatedAt": "2026-10-04T00:00:00Z", "config": [:], "syncedBack": false,
            "workspacesDeleted": false, "codexHomesDeleted": false, "workerActive": false,
            "workerAvailable": false, "eventCount": 1,
            "agents": [["agent": 1, "name": "Agent 1", "status": "success",
                "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false]]]
        let run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: payload))
        fixture.model.activeRun = run
        fixture.model.selectedAgent = 1
        fixture.model.runSummariesByContext[key] = run
        fixture.model.transcripts[1] = AgentTranscript(sourceRunID: "cached", sourceAgent: 1,
            cursor: 1, hasMore: false, finalMessage: "Saved answer", finalMessageHasMore: false)
        #expect(!fixture.model.runPollingIsComplete(key))
        fixture.model.transcripts[1]?.finalMessageReadStatus = "success"
        #expect(fixture.model.runPollingIsComplete(key))
        fixture.model.transcripts[1]?.hasMore = true
        #expect(!fixture.model.runPollingIsComplete(key))
        fixture.model.transcripts[1]?.hasMore = false
        payload["workerActive"] = true
        fixture.model.runSummariesByContext[key] = try JSONDecoder().decode(
            RunSummary.self, from: JSONSerialization.data(withJSONObject: payload))
        #expect(!fixture.model.runPollingIsComplete(key))
    }

    @Test func onlyDefinitivelyRejectedMessagesCanBeReturnedToTheEditor() async throws {
        let fixture = try Fixture(mode: .rejected)
        defer { fixture.close() }
        fixture.model.prompt = "Editable rejected message"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .failed }
        #expect(fixture.model.pendingPromptSubmission?.allowsManualRecovery == true)
        #expect(!promptSubmissionDetail(try #require(fixture.model.pendingPromptSubmission),
                                       language: .english).contains("internal-fixture-diagnostic"))
        fixture.model.editPendingPrompt()
        #expect(fixture.model.prompt == "Editable rejected message")
        #expect(fixture.model.pendingPromptSubmission == nil)
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptOutboxByWorkspace.isEmpty)
        #expect(saved.promptDraftsByContext[fixture.model.currentPromptDraftContext()] == "Editable rejected message")
    }

    @Test func rejectedMessageEditingNeverOverwritesAnotherDraftOrLosesTheOutbox() async throws {
        let fixture = try Fixture(mode: .rejected)
        defer { fixture.close() }
        fixture.model.prompt = "Rejected message"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .failed }
        fixture.model.updatePrompt("Next draft")
        fixture.model.editPendingPrompt()
        #expect(fixture.model.prompt == "Next draft")
        #expect(fixture.model.pendingPromptSubmission?.prompt == "Rejected message")
        #expect(!fixture.model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func aLaterAuthenticationRejectionCannotEraseAnEarlierLostReceipt() async throws {
        let fixture = try Fixture(mode: .lostThenRejected)
        defer { fixture.close() }
        fixture.model.prompt = "Keep the original command"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.needsConnectionRecovery == true }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(original.receiptUncertain == true)
        #expect(original.status == .checkingReceipt)
        #expect(!original.allowsManualRecovery)
        #expect(await fixture.replies.createdCommands == [original.commandID])
        await fixture.model.retryPendingPrompt()
        fixture.model.editPendingPrompt()
        #expect(fixture.model.pendingPromptSubmission == original)
        await fixture.replies.restoreConnection()
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { !fixture.model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(await fixture.replies.createdCommands == [original.commandID])
        #expect(await fixture.replies.startCommands.allSatisfy { $0 == original.commandID })
    }

    @Test func expiredAmbiguousReceiptsAreRetainedWithoutUnsafeAutomaticReplay() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        var original = submission(status: .retrying)
        original.receiptUncertain = true
        original.firstAttemptAt = Date().addingTimeInterval(-31 * 24 * 60 * 60)
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .receiptUnresolved }
        #expect(fixture.model.pendingPromptSubmission?.errorCode == "receipt_expired")
        #expect(fixture.model.pendingPromptSubmission?.allowsManualRecovery == false)
        #expect(fixture.model.pendingPromptSubmission?.resumesAutomatically == true)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func legacyMessagesNeverGetAFabricatedSafeReplayDate() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        var original = submission(status: .retrying)
        original.firstAttemptAt = nil
        original.receiptUncertain = nil
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .receiptUnresolved }
        let held = try #require(fixture.model.pendingPromptSubmission)
        #expect(held.errorCode == "receipt_date_unknown")
        #expect(held.firstAttemptAt == nil)
        #expect(held.resumesAutomatically && !held.allowsManualRecovery)
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(promptSubmissionDetail(held, language: .english).contains("held"))
    }

    @Test func anUncertainCommandCannotBeDiscardedOrReissuedWithANewIdentity() async throws {
        let fixture = try Fixture(mode: .uncertain)
        defer { fixture.close() }
        fixture.model.prompt = "Uncertain receipt"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .checkingReceipt }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(!original.allowsManualRecovery)
        await fixture.model.retryPendingPrompt()
        fixture.model.editPendingPrompt()
        #expect(fixture.model.pendingPromptSubmission == original)
        #expect(fixture.model.promptOutboxByWorkspace[original.contextKey] == original)
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(promptSubmissionDetail(original, language: .english).contains("checks delivery"))
    }

    @Test func repeatedDiagnosticsDoNotRebuildLongConversationProjections() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.resumeHistory = (0..<200).map { ResumeHistoryEntry(category: "assistant", text: "Prior answer \($0)") }
        var pending = submission(status: .pending)
        model.pendingPromptSubmission = pending
        let items = model.selectedTimelineItems
        let sequence = model.presentationProjectionSequence
        for count in 1...30 {
            pending.status = .retrying
            pending.error = "Transport diagnostic \(count)"
            model.pendingPromptSubmission = pending
            #expect(model.selectedTimelineItems == items)
            #expect(model.presentationProjectionSequence == sequence)
        }
        pending.status = .failed
        pending.retryRequiresNewCommandID = true
        model.pendingPromptSubmission = pending
        #expect(model.selectedTimelineItems != items)
        #expect(model.presentationProjectionSequence > sequence)
    }

    @Test func acceptedBusinessStateRecoversAProcessingErrorWithoutAnotherSubmit() async throws {
        let fixture = try Fixture(mode: .acceptedAfterError)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        fixture.model.prompt = "Accepted even though the receipt write failed"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.startCommands.count == 1)
        #expect(await fixture.replies.createdCommands.count == 1)
        #expect(await fixture.replies.receiptCommands == fixture.replies.startCommands)
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
        #expect(fixture.model.pendingPromptSubmission == nil)
    }

    @Test func finalizationIsWaitedOutUsingOnlyTheOriginalCommand() async throws {
        let fixture = try Fixture(mode: .finalizationPending)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        fixture.model.prompt = "Continue after safe finalization"
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .waitingForPreviousTurn }
        let original = try #require(fixture.model.pendingPromptSubmission)
        #expect(!original.allowsManualRecovery)
        #expect(promptSubmissionPresentation(original.status.rawValue, language: .chinese) == nil)
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.startCommands == [original.commandID, original.commandID])
        #expect(await fixture.replies.createdCommands == [original.commandID])
        #expect(await fixture.replies.receiptCommands == [original.commandID])
    }

    @Test func theProvenLegacyPreflightFailureResumesWithoutChangingIdentity() async throws {
        let fixture = try Fixture(mode: .legacyPreflight)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        var original = submission(status: .failed, sourceRunID: "previous-turn", sourceAgent: 7)
        original.errorCode = "pcr_error"
        original.receiptUncertain = true
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
    }

    @Test(arguments: [false, true])
    func aMisclassifiedHistoryFailureChecksTheOriginalReceiptBeforeAnyReplay(alreadyAccepted: Bool) async throws {
        let fixture = try Fixture(mode: alreadyAccepted ? .recoverAccepted : .legacyPreflight)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        var original = submission(status: .failed, sourceRunID: "previous-turn", sourceAgent: 7)
        original.attempt = 1
        original.errorCode = "invalid_argument"
        original.error = "Unsupported Codex history schema; no global database was copied: guardian_review_feedback, thread_read_receipts"
        original.retryRequiresNewCommandID = true
        original.receiptUncertain = false
        original.receiptStoreID = "fixture-store"
        #expect(original.resumesAutomatically && original.needsReceiptReconciliation)
        #expect(!original.allowsManualRecovery && !original.canReturnToDraft)
        #expect(promptSubmissionPresentation(original.presentationStatus.rawValue, language: .chinese) == nil)
        #expect(await fixture.store.recordPromptSubmission(original))
        // Exercise decoding the old persisted failure, not a new in-memory send.
        fixture.model.promptOutboxByWorkspace = DesktopStateStore(directory: fixture.directory)
            .loadSnapshot().promptOutboxByWorkspace
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.startCommands == (alreadyAccepted ? [] : [original.commandID]))
        #expect(await fixture.replies.createdCommands.count == (alreadyAccepted ? 0 : 1))
        #expect(await fixture.replies.allStartsWereDurable)
        if !alreadyAccepted {
            #expect(await fixture.replies.startBodies == fixture.replies.receiptBodies)
        }
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func aHistoryFailureWithoutAuthoritativeRetryProofRemainsHeldWithItsOriginalIdentity() async throws {
        let fixture = try Fixture(mode: .inDoubtReceipt)
        defer { fixture.close() }
        var original = submission(status: .failed, sourceRunID: "previous-turn", sourceAgent: 7)
        original.errorCode = "invalid_argument"
        original.error = "Unsupported Codex history schema; no global database was copied: unknown"
        original.retryRequiresNewCommandID = true
        original.receiptUncertain = false
        original.receiptStoreID = "fixture-store"
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .receiptUnresolved }
        fixture.model.editPendingPrompt()
        await fixture.model.retryPendingPrompt()
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(fixture.model.pendingPromptSubmission?.commandID == original.commandID)
        #expect(fixture.model.pendingPromptSubmission?.receiptUncertain == true)
        #expect(!fixture.model.pendingPromptSubmission!.allowsManualRecovery)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.prompt == original.prompt)
    }

    @Test func onlyTheExactLegacyHistoryErrorOverridesTheOldUnsentClassification() {
        let message = "Unsupported Codex history schema; no global database was copied: future_table"
        #expect(PromptSubmissionRetryPolicy.outcomeIsUncertain(
            PcrdClientError.remote("invalid_argument", message)))
        var ordinary = submission(status: .failed)
        ordinary.errorCode = "invalid_argument"
        ordinary.error = "num_agents must be greater than zero"
        ordinary.receiptUncertain = false
        ordinary.retryRequiresNewCommandID = true
        #expect(ordinary.allowsManualRecovery && !ordinary.resumesAutomatically)
        #expect(!ordinary.needsReceiptReconciliation)
    }

    @Test(arguments: [false, true])
    func aRetainedLegacyMessageRecoversWhenReceiptsBecomeAvailableWithoutRestarting(
        alreadyAccepted: Bool
    ) async throws {
        let fixture = try Fixture(mode: .receiptUpgrade)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        var original = submission(status: .receiptUnresolved, sourceRunID: "previous-turn", sourceAgent: 7)
        original.confirmLargeRun = true
        original.error = "Resolve the previous finalization before continuing"
        original.errorCode = "receipt_lookup_unsupported"
        original.firstAttemptAt = Date().addingTimeInterval(-3 * 60 * 60)
        original.receiptStoreID = nil
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { await fixture.replies.receiptCommands.count == 1 }
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.commandID == original.commandID)

        // The same delivery task must observe a daemon upgrade on its next
        // reconciliation, without user retry, navigation or a client restart.
        await fixture.replies.enableReceipts(alreadyAccepted: alreadyAccepted)
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil(timeout: .seconds(75)) { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.receiptCommands == [original.commandID, original.commandID])
        #expect(await fixture.replies.startCommands == (alreadyAccepted ? [] : [original.commandID]))
        #expect(await fixture.replies.createdCommands.count == (alreadyAccepted ? 0 : 1))
        #expect(await fixture.replies.allStartsWereDurable)
        if !alreadyAccepted {
            let queryBody = try #require(await fixture.replies.receiptBodies.last)
            #expect(await fixture.replies.startBodies == [queryBody])
        }
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
        #expect(fixture.model.pendingPromptSubmission == nil)
    }

    @Test(arguments: [false, true])
    func authoritativeAcceptedReceiptCanBeReadWithoutReopeningAnExpiredReplayWindow(missingDate: Bool) async throws {
        let fixture = try Fixture(mode: .recoverAccepted)
        defer { fixture.close() }
        await fixture.replies.allowOpening()
        var original = submission(status: .failed)
        original.firstAttemptAt = missingDate ? nil : Date().addingTimeInterval(-31 * 24 * 60 * 60)
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
    }

    @Test(arguments: [Optional<String>.none, "different-store"])
    func anAbsentReceiptWithoutStorageContinuityNeverTriggersASubmit(storeID: String?) async throws {
        let fixture = try Fixture(mode: .absentReceipt)
        defer { fixture.close() }
        var original = submission(status: .retrying)
        original.receiptStoreID = storeID
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.pendingPromptSubmission = original
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .receiptUnresolved }
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(!fixture.model.pendingPromptSubmission!.allowsManualRecovery)
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.commandID == original.commandID)
    }

    @Test func receiptReconciliationCannotHijackADifferentConversationOrDraft() async throws {
        let fixture = try Fixture(mode: .recoverAccepted)
        defer { fixture.close() }
        let original = submission(status: .failed)
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        fixture.model.selectedConversationID = "another-conversation"
        fixture.model.updatePrompt("Keep my new draft")
        fixture.model.resumePendingPromptSubmissions()
        await fixture.replies.allowOpening()
        try await waitUntil { await fixture.replies.openRequests == 1 && fixture.model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(fixture.model.activeRun == nil)
        #expect(fixture.model.selectedConversationID == "another-conversation")
        #expect(fixture.model.prompt == "Keep my new draft")
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(await fixture.replies.openRequests == 1)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
    }

    @Test func aLateAcceptanceAfterClientRestartIsSettledOffscreenWithoutSendingAgain() async throws {
        let fixture = try Fixture(mode: .recoverAccepted, fragmentReplies: true)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.replies.holdAcceptance()
        await fixture.replies.allowOpening()
        fixture.model.updatePrompt("Original message before restart")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.createdCommands.count == 1 }
        let original = try #require(fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first)
        let task = try #require(fixture.model.submitPendingPrompt(original))
        task.cancel()
        await fixture.replies.releaseAcceptance()
        await task.value
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey]?.commandID == original.commandID)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        fixture.model.stopWindowObservation()
        let diskStore = DesktopStateStore(directory: fixture.directory)
        let restored = AppModel(client: fixture.client, windowID: UUID(), desktopStateStore: diskStore,
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { restored.stopWindowObservation() }
        restored.workspaces = fixture.model.workspaces
        restored.selectedWorkspaceID = "workspace"
        restored.selectedConversationID = "another-conversation"
        restored.updatePrompt("A different conversation's draft")
        restored.resumePendingPromptSubmissions()
        try await waitUntil { diskStore.loadSnapshot().promptOutboxByWorkspace.isEmpty
            && diskStore.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty }
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted")
        let coldCache = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let cached = await coldCache.hydrate(for: key)
        #expect(cached?.runSummary?.prompt == original.prompt)
        #expect(cached?.runSummary?.config.submissionCommand?.commandId == original.commandID)
        #expect(restored.activeRun == nil && restored.selectedConversationID == "another-conversation")
        #expect(restored.prompt == "A different conversation's draft")
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(await fixture.replies.createdCommands == [original.commandID])
        let body = try #require(await fixture.replies.startBodies.first)
        let sent = try #require(JSONSerialization.jsonObject(with: body) as? [String: Any])
        #expect(sent["run_id"] as? String == "previous-turn")
        #expect(sent["agent"] as? Int == 7)
        #expect(sent["prompt"] as? String == original.prompt)
    }

    @Test(arguments: [false, true])
    func rapidConversationSwitchesCannotStrandOrRetargetALateHandoff(resumingSession: Bool) async throws {
        let fixture = try Fixture(mode: .accepted, fragmentReplies: true)
        defer { fixture.close() }
        if resumingSession {
            try fixture.installStoppedConversation(status: "discarded")
            await fixture.replies.inferSavedSessionParent()
        } else {
            try fixture.installCompletedConversation()
        }
        let model = fixture.model
        let source = try #require(model.activeRun)
        model.cacheVisibleRunViewState()
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["runId"] = "other-run"
        json["conversationId"] = "another-conversation"
        let other = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        await fixture.replies.registerRun(source)
        await fixture.replies.registerRun(other)
        model.workspaceConversations.append(WorkspaceConversationSummary(id: "another-conversation", title: "Other",
            runs: [other], resumeSession: nil))
        model.runViewStateStore.put(WorkspaceRunViewSnapshot(selectedAgent: model.selectedAgent, transcripts: [:], conversationHistory: []),
            runSummary: other, for: WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: other.runId))
        await fixture.replies.allowOpening()
        await fixture.replies.holdOpening()
        model.updatePrompt("Keep this exact source and Agent")
        await model.submitPrompt()
        try await waitUntil { await fixture.replies.openRequests == 1 }
        let original = try #require(model.acceptedPromptResultsByWorkspace.values.first)
        var navigations: [Task<Void, Never>] = []
        for index in 0..<8 {
            let destination = index.isMultiple(of: 2) ? "delivery-conversation" : "another-conversation"
            navigations.append(Task { await model.selectConversation(destination) })
            try await waitUntil { model.selectedConversationID == destination }
            model.updatePrompt("Draft \(index)")
        }
        await fixture.replies.releaseOpening()
        for navigation in navigations { await navigation.value }
        try await waitUntil { model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(model.selectedConversationID == "another-conversation")
        #expect(model.activeRun?.runId == "other-run")
        #expect(model.prompt == "Draft 7")
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let destination = await cold.hydrate(for: WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted"))
        #expect(destination?.runSummary?.prompt == original.submission.prompt)
        #expect(destination?.runSummary?.config.submissionCommand?.commandId == original.commandID)
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(await fixture.replies.createdCommands == [original.commandID])
    }

    @Test func browsingAnotherAgentCannotHideOrChangeAnInFlightMessage() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        await fixture.replies.holdAcceptance()
        await fixture.replies.allowOpening()
        fixture.model.updatePrompt("The originally selected Agent owns this message")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let original = try #require(fixture.model.pendingPromptSubmission)
        fixture.model.selectedAgent = 6
        fixture.model.restoreVisiblePromptSubmission()
        #expect(fixture.model.pendingPromptSubmission?.commandID == original.commandID)
        #expect(fixture.model.pendingPromptSubmission?.sourceAgent == 7)
        fixture.model.updatePrompt("Retain the later draft")
        await fixture.replies.releaseAcceptance()
        try await waitUntil { fixture.model.activeRun?.runId == "accepted" }
        #expect(fixture.model.prompt == "Retain the later draft")
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands == [original.commandID])
    }

    @Test(arguments: ["workspace", "prompt", "command", "source"])
    func anUnprovenDestinationNeverConsumesTheReceiptOrStartsAnotherTask(_ conflict: String) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.replies.allowOpening()
        await fixture.replies.tamperDestination(conflict)
        fixture.model.updatePrompt("Keep the verified original message")
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .openingFailed }
        let original = try #require(fixture.model.acceptedPromptResultsByWorkspace.values.first)
        #expect(fixture.model.activeRun?.runId == "previous-turn")
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[original.contextKey]?.commandID == original.commandID)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted")
        #expect(fixture.model.runViewStateStore.snapshot(for: key) == nil)
        #expect(await fixture.replies.createdCommands == [original.commandID])
        await fixture.replies.tamperDestination(nil)
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(fixture.model.activeRun?.prompt == original.submission.prompt)
        #expect(await fixture.replies.startCommands == [original.commandID])
    }

    @Test(arguments: ["workspace", "prompt", "command", "client", "resume_session"])
    func sessionHandoffStillRejectsRealIdentityConflictsAndReconcilesWithoutResending(_ conflict: String) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installStoppedConversation(status: "discarded")
        await fixture.replies.inferSavedSessionParent()
        await fixture.replies.allowOpening()
        await fixture.replies.tamperDestination(conflict)
        fixture.model.updatePrompt("Only this saved session and command may be opened")
        await fixture.model.submitPrompt()
        try await waitUntil { fixture.model.pendingPromptSubmission?.status == .openingFailed }
        let held = try #require(fixture.model.acceptedPromptResultsByWorkspace.values.first)
        #expect(fixture.model.activeRun?.runId == "stopped-turn")
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[held.contextKey]?.commandID == held.commandID)
        #expect(fixture.model.runViewStateStore.snapshot(for: WorkspaceRunViewKey(
            machineID: "local", workspaceID: "workspace", runID: "accepted")) == nil)
        let reason = ["workspace": "workspace_path", "client": "command"][conflict] ?? conflict
        #expect(held.submission.error.contains("(\(reason))"))
        await fixture.replies.tamperDestination(nil)
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { fixture.model.acceptedPromptResultsByWorkspace.isEmpty }
        #expect(fixture.model.activeRun?.runId == "accepted")
        #expect(await fixture.replies.startCommands == [held.commandID])
        #expect(await fixture.replies.createdCommands == [held.commandID])
    }

    @Test func aFailedDestinationWriteRetainsTheReceiptUntilTheColdCacheIsDurable() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try Data("not a directory".utf8).write(to: fixture.cacheDirectory)
        await fixture.replies.allowOpening()
        fixture.model.updatePrompt("This message must survive a cache write failure")
        await fixture.model.submitPrompt()
        try await waitUntil { await fixture.replies.openRequests >= 1 && fixture.model.acceptedPromptAdoptionsInFlight.isEmpty }
        let original = try #require(fixture.model.acceptedPromptResultsByWorkspace.values.first)
        #expect(fixture.model.activeRun == nil)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[original.contextKey]?.commandID == original.commandID)
        try FileManager.default.removeItem(at: fixture.cacheDirectory)
        try await waitUntil { fixture.model.acceptedPromptResultsByWorkspace.isEmpty }
        let cold = WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory))
        let saved = await cold.hydrate(for: WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: "accepted"))
        #expect(saved?.runSummary?.prompt == original.submission.prompt)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands == [original.commandID])
    }

    @Test(arguments: [false, true])
    func aProvenRetryableReceiptRecoversWithoutInventingOrRefreshingItsReplayDate(missingDate: Bool) async throws {
        let fixture = try Fixture(mode: .legacyPreflight)
        defer { fixture.close() }
        var original = submission(status: .failed, sourceRunID: "previous-turn", sourceAgent: 7)
        original.firstAttemptAt = missingDate ? nil : Date(timeIntervalSinceNow: -31 * 24 * 3600)
        #expect(await fixture.store.recordPromptSubmission(original))
        fixture.model.promptOutboxByWorkspace[original.contextKey] = original
        await fixture.replies.allowOpening()
        await fixture.replies.holdAcceptance()
        fixture.model.resumePendingPromptSubmissions()
        try await waitUntil { await fixture.replies.startCommands.count == 1 }
        let during = try #require(fixture.store.loadSnapshot().promptOutboxByWorkspace[original.contextKey])
        #expect(during.firstAttemptAt == original.firstAttemptAt)
        #expect(during.commandID == original.commandID && during.prompt == original.prompt)
        await fixture.replies.releaseAcceptance()
        try await waitUntil { fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty
            && fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty }
        #expect(await fixture.replies.startCommands == [original.commandID])
        #expect(await fixture.replies.createdCommands == [original.commandID])
    }

    @Test func anUnavailableComputerCannotPermanentlyAcquireTheHandoffGuard() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        var original = submission(status: .opening)
        original.machineID = "temporarily-unavailable"
        let result = PersistedAcceptedPromptResult(submission: original, runID: "accepted",
            continuedSessionID: "", workspaceRevision: 1)
        #expect(await fixture.store.recordPromptSubmission(original))
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: original.contextKey,
            expected: original, result: result))
        fixture.model.acceptedPromptResultsByWorkspace[original.contextKey] = result
        #expect(await fixture.model.adoptAcceptedPromptResult(result) == false)
        #expect(fixture.model.acceptedPromptAdoptionsInFlight.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[original.contextKey] != nil)
        fixture.model.localDaemonMachineID = original.machineID
        await fixture.replies.allowOpening()
        #expect(await fixture.model.adoptAcceptedPromptResult(result))
        #expect(fixture.model.acceptedPromptAdoptionsInFlight.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func aLostControlReplyVerifiesTheActualOutcomeWithoutRepeatingTheOperation() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let context = try #require(fixture.model.currentRunOperationContext())
        let source = try #require(fixture.model.activeRun)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["status"] = "finalized"
        json["finalizedAgent"] = 7
        json["syncedBack"] = true
        let committed = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        await fixture.replies.registerRun(committed)
        var mutationCount = 0
        await fixture.model.performRunOperation(context, kind: .useResult(7)) { _ in
            mutationCount += 1
            throw PcrdClientError.connection("The reply arrived after the connection closed")
        }
        #expect(mutationCount == 1)
        #expect(fixture.model.activeRun?.finalizedAgent == 7)
        #expect(fixture.model.activeRun?.syncedBack == true)
        #expect(fixture.model.runOperationIssues[context.key] == nil)
        #expect(!fixture.model.isRunOperationInProgress)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func aHealthyReadOrAnotherAgentsResultCannotDismissARealControlFailure() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let context = try #require(fixture.model.currentRunOperationContext())
        let source = try #require(fixture.model.activeRun)
        await fixture.replies.registerRun(source)
        await fixture.model.performRunOperation(context, kind: .useResult(7)) { _ in
            throw PcrdClientError.connection("Result not verified")
        }
        #expect(fixture.model.runOperationIssues[context.key] != nil)
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(source)) as? [String: Any])
        json["status"] = "finalized"
        json["finalizedAgent"] = 6
        json["syncedBack"] = true
        let other = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "workspace", runID: source.runId)
        fixture.model.reconcileRunOperationIssue(other, key: key)
        #expect(fixture.model.runOperationIssues[context.key] != nil)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    private func submission(status: PromptSubmissionStatus, sourceRunID: String? = nil,
                            sourceAgent: Int? = nil) -> PersistedPromptSubmission {
        PersistedPromptSubmission(id: "delivery-test", machineID: "local", workspaceID: "workspace",
            prompt: "A saved message", settings: RunSettings(), sourceRunID: sourceRunID, sourceAgent: sourceAgent,
            resumeSessionID: "", confirmLargeRun: false, attempt: 0, status: status, error: "",
            receiptUncertain: true, firstAttemptAt: Date(), conversationID: "delivery-conversation")
    }

    private func waitUntil(timeout: Duration = .seconds(6),
                           _ predicate: @MainActor () async -> Bool) async throws {
        let deadline = ContinuousClock.now + timeout
        while !(await predicate()), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        try #require(await predicate())
    }

    enum Mode: Sendable {
        case accepted, lostReceipt, lostThenRejected, rejected, uncertain
        case acceptedAfterError, recoverAccepted, finalizationPending, legacyPreflight, absentReceipt, inDoubtReceipt
        case receiptUpgrade, lostBeforeStart, daemonOwned, storageConfirmation
    }

    @MainActor private final class NativeHandoffSurface {
        let model: AppModel
        let window: NSWindow
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 920, height: 900))
        let suite = "PromptSettlement.\(UUID().uuidString)"
        let defaults: UserDefaults
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
                appearance: .dark, contextID: "\(model.activeRun?.runId ?? "pending")/\(model.selectedAgent ?? 0)", items: model.selectedTimelineItems,
                contentIsLoading: model.isSelectedTimelineLoading, jumpToLatestRequest: 0,
                findQuery: "", findMatch: nil, findNavigationRequest: 0, memory: memory,
                resultsInspectorVisible: false, openURL: { _, _ in }, activateBlock: { _ in },
                selectAgent: { _ in }, toggleResults: {},
                itemsRevision: model.conversationTimelineItemsProjectionGeneration,
                isNearBottom: .constant(true), inlineAgentSwitchPosition: .constant(.missing))
        }

        func update() {
            coordinator.update(parent: parent())
            scroll.layoutSubtreeIfNeeded()
            collection.layoutSubtreeIfNeeded()
        }

        var texts: [String] {
            func strings(_ view: NSView) -> [String] {
                (view as? NSTextView).map { [$0.string] } ?? view.subviews.flatMap(strings)
            }
            return collection.visibleItems().flatMap { strings($0.view) }
        }

        func framePNG() throws -> Data {
            let bitmap = try #require(scroll.bitmapImageRepForCachingDisplay(in: scroll.bounds))
            scroll.cacheDisplay(in: scroll.bounds, to: bitmap)
            return try #require(bitmap.representation(using: .png, properties: [:]))
        }

        func close() {
            model.cancelDeferredPromptProcessing()
            coordinator.detach(); memory.freezeForTermination(); window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }

    private actor SelectionGate {
        private var released = false
        func release() { released = true }
        func wait() async {
            while !released { try? await Task.sleep(for: .milliseconds(10)) }
        }
    }

    actor Replies {
        let mode: Mode
        let directory: URL
        var startCommands: [String] = []
        var startActions: [String] = []
        var createdCommands: Set<String> = []
        var allStartsWereDurable = true
        var openRequests = 0
        var openingAllowed = false
        var openingHeld = false
        var openingRejected = false
        var acceptanceHeld = false
        var capabilitiesHeld = false
        var sourceReadingHeld = false
        var receiptsHeld = false
        var sourceRun: RunSummary?
        var sourceReads = 0
        var connectionRestored = false
        var receiptCommands: [String] = []
        var receiptBodies: [Data] = []
        var startBodies: [Data] = []
        var upgradedReceiptAlreadyAccepted: Bool?
        var daemonHandoffFinished = false
        var knownRuns: [String: RunSummary] = [:]
        var committedSubmission: PersistedPromptSubmission?
        var destinationConflict: String?
        var infersSavedSessionParent = false
        var destinationAnswer: String?
        var orderedDelivery = false
        var orderedRunIDs: [String: String] = [:]
        var orderedBodies: [String: Data] = [:]
        var scopedReads: [String?] = []
        var wrongInlineWorkspace = false
        var sourceLoginRequired = false
        init(mode: Mode, directory: URL) { self.mode = mode; self.directory = directory }
        func allowOpening() { openingAllowed = true; openingRejected = false }
        func rejectOpening() { openingRejected = true }
        func holdOpening() { openingHeld = true }
        func releaseOpening() { openingHeld = false }
        func restoreConnection() { connectionRestored = true }
        func holdAcceptance() { acceptanceHeld = true }
        func releaseAcceptance() { acceptanceHeld = false }
        func holdCapabilities() { capabilitiesHeld = true }
        func releaseCapabilities() { capabilitiesHeld = false }
        func holdSourceReading(_ run: RunSummary) { sourceRun = run; sourceReadingHeld = true }
        func releaseSourceReading() { sourceReadingHeld = false }
        func holdReceipts() { receiptsHeld = true }
        func releaseReceipts() { receiptsHeld = false }
        func enableReceipts(alreadyAccepted: Bool) { upgradedReceiptAlreadyAccepted = alreadyAccepted }
        func finishDaemonHandoff() { daemonHandoffFinished = true }
        func registerRun(_ run: RunSummary) { knownRuns[run.runId] = run }
        func tamperDestination(_ conflict: String?) { destinationConflict = conflict }
        func inferSavedSessionParent() { infersSavedSessionParent = true }
        func showDestinationAnswer(_ answer: String) { destinationAnswer = answer }
        func enableOrderedDelivery() { orderedDelivery = true }
        func useUnregisteredInlineSnapshot() { wrongInlineWorkspace = true }
        func requireSourceLogin(_ value: Bool) { sourceLoginRequired = value }
        func completeOrderedRun(_ runID: String) throws {
            guard let run = knownRuns[runID] else { return }
            var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
            json["status"] = "completed"
            json["workerActive"] = false
            knownRuns[runID] = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
        }

        func reply(_ request: Data) async throws -> Data? {
            let payload = try #require(JSONSerialization.jsonObject(with: request) as? [String: Any])
            let action = payload["action"] as? String ?? ""
            if orderedDelivery, ["turns.start", "turns.continue"].contains(action) {
                let command = try #require(payload["command_id"] as? String)
                let body = try #require(payload["body"] as? [String: Any])
                startCommands.append(command)
                startActions.append(action)
                startBodies.append(try JSONSerialization.data(withJSONObject: body))
                let state = DesktopStateStore(directory: directory).loadSnapshot()
                let original = try #require(state.promptOutboxByWorkspace.values.first { $0.commandID == command })
                allStartsWereDurable = allStartsWereDurable && original.prompt == body["prompt"] as? String
                if orderedRunIDs[command] == nil {
                    let id = "ordered-\(orderedRunIDs.count + 1)"
                    orderedRunIDs[command] = id
                    orderedBodies[command] = try JSONSerialization.data(withJSONObject: body)
                    createdCommands.insert(command)
                    let json: [String: Any] = ["runId": id, "workspace": original.workspacePath ?? "/qa",
                        "prompt": original.prompt, "conversationId": original.conversationID ?? "delivery-conversation",
                        "continuedFromRunId": original.sourceRunID as Any? ?? NSNull(),
                        "continuedFromAgent": original.sourceAgent as Any? ?? NSNull(),
                        "status": "running", "workerActive": true, "workerAvailable": true,
                        "createdAt": "2026-10-15T00:00:00Z", "updatedAt": "2026-10-15T00:00:01Z",
                        "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                        "eventCount": 0, "recommendedAgent": 1,
                        "config": ["resumeSessionId": usableResumeSessionID(original.resumeSessionID) as Any? ?? NSNull(),
                            "submissionCommand": ["clientId": "mac-app", "commandId": command, "fingerprint": "ordered-fixture"]],
                        "agents": [["agent": 1, "name": "Agent 1", "status": "success", "reasoningTokenCounts": [:],
                            "rejected": false, "recommended": true, "finalized": false]]]
                    var run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
                    run.conversationSnapshot = ConversationSnapshot(workspace: WorkspaceRecord(workspaceId: original.workspaceID))
                    knownRuns[id] = run
                }
                while acceptanceHeld { try await Task.sleep(for: .milliseconds(10)) }
                let id = try #require(orderedRunIDs[command])
                var response: [String: Any] = ["status": "accepted", "run_id": id,
                    "workspace_id": original.workspaceID, "workspace_revision": 1]
                if wrongInlineWorkspace, var run = knownRuns[id] {
                    run.conversationSnapshot = ConversationSnapshot(workspace: WorkspaceRecord(workspaceId: "path-derived-workspace"))
                    response["run"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(run))
                }
                return try encode(["ok": true, "body": response])
            }
            if orderedDelivery, action == "commands.get" {
                let body = try #require(payload["body"] as? [String: Any])
                let command = try #require(body["command_id"] as? String)
                receiptCommands.append(command)
                if let id = orderedRunIDs[command] {
                    let supplied = try #require(body["body"] as? [String: Any])
                    let expected = try JSONSerialization.jsonObject(with: try #require(orderedBodies[command]))
                    #expect(NSDictionary(dictionary: supplied).isEqual(expected))
                    return try encode(["ok": true, "body": ["command_id": command, "status": "completed",
                        "result": ["status": "accepted", "run_id": id], "receipt_store_id": "fixture-store"]])
                }
            }
            if action == "system.capabilities" {
                while capabilitiesHeld { try await Task.sleep(for: .milliseconds(10)) }
                return try encode(["ok": true, "body": ["daemon_version": "0.1.70",
                    "actions": ["system.health", "turns.start", "turns.continue", "commands.get"],
                    "command_receipt_store_id": "fixture-store"]])
            }
            if action == "workspaces.list" {
                return try encode(["ok": true, "body": ["revision": 1, "selected_workspace_id": "workspace",
                    "workspaces": [["workspace_id": "workspace", "name": "Delivery QA", "path": "/qa",
                        "last_run_id": "accepted", "last_resume_session_id": "", "selected": true,
                        "available": true, "readable": true, "writable": true]]]])
            }
            if action == "commands.get" {
                let query = try #require(payload["body"] as? [String: Any])
                let command = try #require(query["command_id"] as? String)
                receiptCommands.append(command)
                receiptBodies.append(try JSONSerialization.data(withJSONObject: query["body"]!, options: [.sortedKeys]))
                while receiptsHeld { try await Task.sleep(for: .milliseconds(10)) }
                var receipt: [String: Any] = ["command_id": command, "receipt_store_id": "fixture-store"]
                switch mode {
                case .storageConfirmation:
                    receipt["status"] = "completed"
                    receipt["result"] = storageConfirmationResult
                case .daemonOwned:
                    receipt["status"] = daemonHandoffFinished ? "completed" : "retryable"
                    receipt["recovery_owned_by_daemon"] = true
                    if daemonHandoffFinished {
                        receipt["result"] = ["status": "accepted", "run_id": "accepted", "workspace_revision": 1]
                    }
                case .acceptedAfterError, .recoverAccepted:
                    receipt["status"] = "completed"
                    receipt["result"] = ["status": "accepted", "run_id": "accepted", "workspace_revision": 1]
                case .finalizationPending, .legacyPreflight:
                    receipt["status"] = "retryable"
                case .inDoubtReceipt:
                    receipt["status"] = "in_doubt"
                case .absentReceipt, .lostBeforeStart:
                    receipt["status"] = "not_found"
                case .receiptUpgrade:
                    guard let alreadyAccepted = upgradedReceiptAlreadyAccepted else { return error("unknown_action") }
                    receipt["status"] = alreadyAccepted ? "completed" : "retryable"
                    if alreadyAccepted {
                        receipt["result"] = ["status": "accepted", "run_id": "accepted", "workspace_revision": 1]
                    }
                default:
                    return error("unknown_action")
                }
                return try encode(["ok": true, "body": receipt])
            }
            if action == "turns.start" || action == "turns.continue" {
                let command = try #require(payload["command_id"] as? String)
                startCommands.append(command)
                startActions.append(action)
                startBodies.append(try JSONSerialization.data(withJSONObject: payload["body"]!, options: [.sortedKeys]))
                let persisted = DesktopStateStore(directory: directory).loadSnapshot()
                if let original = persisted.promptOutboxByWorkspace.values.first(where: { $0.commandID == command }) {
                    committedSubmission = original
                }
                allStartsWereDurable = allStartsWereDurable && persisted.promptOutboxByWorkspace.values.contains {
                    $0.commandID == command
                }
                if mode == .rejected || mode == .uncertain {
                    return error(mode == .rejected ? "invalid_request" : "command_in_doubt")
                }
                if mode == .daemonOwned || (mode == .finalizationPending && startCommands.count == 1) {
                    return error("pcr_operation_pending")
                }
                if mode == .lostBeforeStart && startCommands.count <= 2 { return nil }
                if mode == .storageConfirmation,
                   (payload["body"] as? [String: Any])?["confirm_large_run"] as? Bool != true {
                    return try encode(["ok": true, "body": storageConfirmationResult])
                }
                createdCommands.insert(command)
                if mode == .acceptedAfterError { return error("internal_error") }
                if mode == .lostReceipt && startCommands.count <= 2 { return nil }
                if mode == .lostThenRejected && !connectionRestored {
                    return startCommands.count <= 2 ? nil : error("invalid_token")
                }
                while acceptanceHeld { try await Task.sleep(for: .milliseconds(10)) }
                return try encode(["ok": true, "body": ["status": "accepted", "run_id": "accepted", "workspace_revision": 1]])
            }
            if action == "turns.get" {
                scopedReads.append((payload["body"] as? [String: Any])?["workspace_id"] as? String)
                if sourceLoginRequired { return error("invalid_token") }
                if let body = payload["body"] as? [String: Any], let id = body["run_id"] as? String,
                   let known = knownRuns[id] {
                    return try encode(["ok": true, "body": JSONSerialization.jsonObject(with: JSONEncoder().encode(known))])
                }
                if let sourceRun, let body = payload["body"] as? [String: Any],
                   body["run_id"] as? String == sourceRun.runId {
                    sourceReads += 1
                    while sourceReadingHeld { try await Task.sleep(for: .milliseconds(10)) }
                    return try encode(["ok": true,
                        "body": JSONSerialization.jsonObject(with: JSONEncoder().encode(sourceRun))])
                }
                openRequests += 1
                if openingRejected { return error("invalid_token") }
                guard openingAllowed else { return error("relay_unreachable") }
                while openingHeld { try await Task.sleep(for: .milliseconds(10)) }
                let state = DesktopStateStore(directory: directory).loadSnapshot()
                let accepted = state.acceptedPromptResultsByWorkspace.values.first
                let original = accepted?.submission ?? committedSubmission ?? state.promptOutboxByWorkspace.values.first
                if let original { committedSubmission = original }
                var configuration: [String: Any] = [:]
                if let original {
                    configuration["submission_command"] = ["client_id": destinationConflict == "client" ? "other-client" : "mac-app",
                        "command_id": destinationConflict == "command" ? "different-command" : original.commandID,
                        "fingerprint": "fixture-fingerprint"]
                    configuration["resume_session_id"] = destinationConflict == "resume_session"
                        ? "different-session" : usableResumeSessionID(original.resumeSessionID)
                }
                var destination: [String: Any] = ["run_id": "accepted", "status": "completed",
                    "workspace": destinationConflict == "workspace" ? "/different" : "/qa",
                    "prompt": destinationConflict == "prompt" ? "Different message" : original?.prompt ?? "Delivered",
                    "conversation_id": original?.conversationID ?? "delivery-conversation",
                    "created_at": "2026-10-04T00:00:00Z",
                    "updated_at": "2026-10-04T00:00:00Z", "config": configuration, "synced_back": false,
                    "workspaces_deleted": false, "codex_homes_deleted": false,
                    "worker_active": false, "worker_available": false, "event_count": 0, "agents": []]
                if let source = original?.sourceRunID {
                    destination["continued_from_run_id"] = destinationConflict == "source" ? "unrelated-source" : source
                    destination["continued_from_agent"] = original?.sourceAgent
                } else if infersSavedSessionParent {
                    destination["continued_from_run_id"] = "saved-parent"
                    destination["continued_from_agent"] = 7
                }
                if destinationAnswer != nil {
                    destination["agents"] = [["agent": 1, "name": "Agent 1", "status": "success",
                        "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false]]
                }
                return try encode(["ok": true, "body": destination])
            }
            if action == "runs.agent", let answer = destinationAnswer,
               let body = payload["body"] as? [String: Any], body["run_id"] as? String == "accepted" {
                return try encode(["ok": true, "body": ["run_id": "accepted", "agent": 1, "name": "Agent 1",
                    "status": "success", "reasoning_token_counts": [:], "rejected": false, "recommended": false,
                    "finalized": false, "final_message": "", "stdout_tail": "", "stderr_tail": "",
                    "final_message_page": ["cursor": 0, "next_cursor": answer.utf8.count, "has_more": false,
                        "total_bytes": answer.utf8.count, "message_id": "saved-session-answer", "text": answer],
                    "transcript_page": ["schema_version": 1, "cursor": 0, "next_cursor": 0,
                        "has_more": false, "total": 0, "items": []]]])
            }
            return error("fixture_query_unavailable")
        }

        private func error(_ code: String) -> Data {
            try! encode(["ok": false, "error": ["code": code, "message": "internal-fixture-diagnostic"]])
        }
        private var storageConfirmationResult: [String: Any] {
            ["status": "confirmation_required", "message": "Confirm the storage allocation",
             "storage": ["total_bytes": 50_000_000_000, "total": "46.60 GiB",
                         "free_bytes": 80_000_000_000, "free": "74.51 GiB", "fits": true,
                         "warning_threshold_bytes": 5_368_709_120, "warning_threshold": "5.00 GiB",
                         "confirmation_required": true]]
        }
        private func encode(_ value: [String: Any]) throws -> Data {
            try JSONSerialization.data(withJSONObject: value) + Data([10])
        }
    }

    @MainActor final class Fixture {
        let directory: URL
        let store: DesktopStateStore
        let client: PcrdClient
        let model: AppModel
        let cacheDirectory: URL
        let replies: Replies
        let listener: Int32
        let server: Task<Void, Never>

        init(mode: Mode, fragmentReplies: Bool = false) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-send-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socket = directory.appendingPathComponent("s"), token = directory.appendingPathComponent("t")
            try Data("fixture-token".utf8).write(to: token)
            store = DesktopStateStore(directory: directory)
            cacheDirectory = directory.appendingPathComponent("cache")
            client = PcrdClient(socketURL: socket, tokenURL: token)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { destination in
                Array(socket.path.utf8CString).withUnsafeBytes { destination.copyBytes(from: $0) }
            }
            let descriptor = listener
            let result = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(result == 0 && Darwin.listen(descriptor, 8) == 0)
            let replies = Replies(mode: mode, directory: directory)
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
                    var bytes = [UInt8](repeating: 0, count: 65536)
                    let count = Darwin.read(connection, &bytes, bytes.count)
                    guard count > 0, let reply = try? await replies.reply(Data(bytes.prefix(count))) else { continue }
                    var offset = 0
                    while offset < reply.count {
                        let count = min(fragmentReplies ? 17 : reply.count, reply.count - offset)
                        let written = reply.withUnsafeBytes {
                            Darwin.write(connection, $0.baseAddress!.advanced(by: offset), count)
                        }
                        guard written > 0 else { break }
                        offset += written
                        if fragmentReplies { try? await Task.sleep(for: .milliseconds(1)) }
                    }
                }
            }
            model = AppModel(client: client, windowID: UUID(), desktopStateStore: store,
                runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: cacheDirectory)))
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
                {"workspaceId":"workspace","name":"Delivery QA","path":"/qa","lastRunId":"",
                "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
                """.utf8))]
            model.selectedWorkspaceID = "workspace"
            model.selectedConversationID = "delivery-conversation"
            _ = model.preparePromptHistoryContext()
            model.daemonOnline = true
        }

        func close() {
            model.stopWindowObservation()
            model.cancelDeferredPromptProcessing()
            model.promptOutboxByWorkspace.removeAll()
            model.acceptedPromptResultsByWorkspace.removeAll()
            server.cancel()
            Darwin.close(listener)
            _ = store.flushPendingWrites()
            try? FileManager.default.removeItem(at: directory)
        }

        func installOpenedAcceptedHandoff() async throws -> PersistedAcceptedPromptResult {
            var settings = RunSettings()
            settings.resumeSessionID = "saved-conversation-session"
            let original = PersistedPromptSubmission(id: "opened-message", machineID: "local", workspaceID: "workspace",
                prompt: "Repeat this exact question.", settings: settings, sourceRunID: nil, sourceAgent: nil,
                resumeSessionID: settings.resumeSessionID, confirmLargeRun: false, attempt: 0,
                status: .openingFailed, error: "Old destination conflict", errorCode: "prompt_destination_conflict",
                receiptUncertain: false, conversationID: "delivery-conversation", workspacePath: "/qa")
            let result = PersistedAcceptedPromptResult(submission: original, runID: "accepted",
                continuedSessionID: "", workspaceRevision: 1)
            #expect(await store.recordPromptSubmission(original))
            #expect(store.recordAcceptedPromptResult(contextKey: original.contextKey, expected: original, result: result))
            let run = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": "accepted", "workspace": "/qa", "prompt": original.prompt, "status": "completed",
                "conversationId": "delivery-conversation", "continuedFromRunId": "saved-parent", "continuedFromAgent": 7,
                "config": ["resumeSessionId": original.resumeSessionID,
                    "submissionCommand": ["clientId": "mac-app", "commandId": original.commandID, "fingerprint": "fixture"]],
                "createdAt": "2026-10-15T00:00:00Z", "updatedAt": "2026-10-15T00:10:00Z", "recommendedAgent": 1,
                "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": false, "workerAvailable": false, "eventCount": 40,
                "agents": (1...2).map { ["agent": $0, "name": "Agent \($0)", "status": "success",
                    "reasoningTokenCounts": [:], "rejected": false, "recommended": $0 == 1, "finalized": false] }
            ]))
            model.acceptedPromptResultsByWorkspace[original.contextKey] = result
            model.pendingPromptSubmission = original
            model.workspaces = model.workspaces.map { $0.replacingRunSummary(with: run, startingNewTurn: true) }
            model.activeRun = run
            model.workspaceConversations = [.init(id: "delivery-conversation", title: "Conversation", runs: [run], resumeSession: nil)]
            model.selectedAgent = 2
            model.settings = settings
            model.resumeHistory = [ResumeHistoryEntry(category: "user", text: original.prompt, id: "earlier-real-user"),
                ResumeHistoryEntry(category: "output", text: "An earlier real answer.", id: "earlier-real-answer")]
            model.transcripts[2] = AgentTranscript(sourceRunID: run.runId, sourceAgent: 2,
                cursor: 40, hasMore: false, finalMessage: "Complete answer from Agent 2.", finalMessageCursor: 29,
                finalMessageHasMore: false, finalMessageID: "answer-2", finalMessageReadStatus: "success")
            await replies.registerRun(run)
            return result
        }

        func installCompletedConversation(finalized: Bool = true, runID: String = "previous-turn", status: String? = nil) throws {
            let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
                {"runId":"\(runID)","status":"\(status ?? (finalized ? "finalized" : "completed"))",
                 "conversationId":"delivery-conversation",
                 "workspace":"/qa","prompt":"Previous question","config":{},
                 "createdAt":"2026-10-04T00:00:00Z","updatedAt":"2026-10-04T00:00:00Z",
                 "finalizedAgent":\(finalized ? "7" : "null"),"syncedBack":\(finalized),
                 "workerActive":false,"workerAvailable":false,"workspacesDeleted":false,"codexHomesDeleted":false,
                 "eventCount":0,
                 "agents":[{"agent":6,"name":"Agent 6","status":"success","codexThreadId":"session-6",
                            "reasoningTokenCounts":{},"rejected":false,"recommended":false,"finalized":false},
                           {"agent":7,"name":"Synthesis 1","status":"success","codexThreadId":"session-7",
                            "reasoningTokenCounts":{},"rejected":false,"recommended":true,"finalized":\(finalized)}]}
                """.utf8))
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
                {"workspaceId":"workspace","name":"Delivery QA","path":"/qa","lastRunId":"\(runID)",
                 "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
                """.utf8))]
            model.activeRun = run
            model.workspaceConversations = [WorkspaceConversationSummary(
                id: "delivery-conversation", title: "Conversation", runs: [run], resumeSession: nil
            )]
            model.selectedAgent = 7
            model.settings.syncBack = true
        }

        func installStoppedConversation(status: String, savedSession: String? = "saved-conversation-session") throws {
            let session = savedSession.map { "\"\($0)\"" } ?? "null"
            let discarded = status == "discarded"
            let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
                {"runId":"stopped-turn","status":"\(status)","conversationId":"delivery-conversation",
                 "workspace":"/qa","prompt":"The cancelled cleanup request",
                 "config":{"resumeSessionId":\(session),"syncBack":true},
                 "createdAt":"2026-10-09T00:00:00Z","updatedAt":"2026-10-09T00:10:00Z",
                 "syncedBack":false,"workerActive":false,"workerAvailable":false,
                 "workspacesDeleted":\(discarded),"codexHomesDeleted":\(discarded),"eventCount":10,
                 "agents":[{"agent":1,"name":"Agent 1","status":"cancelled",
                            "codexThreadId":"deleted-private-session","canContinue":false,
                            "workspaceAvailable":\(!discarded),"reasoningTokenCounts":{},
                            "rejected":false,"recommended":false,"finalized":false}]}
                """.utf8))
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
                {"workspaceId":"workspace","name":"Delivery QA","path":"/qa","lastRunId":"stopped-turn",
                 "lastResumeSessionId":"unrelated-workspace-session","selected":true,
                 "available":true,"readable":true,"writable":true}
                """.utf8))]
            model.activeRun = run
            model.workspaceConversations = [.init(id: "delivery-conversation", title: "Conversation",
                runs: [run], resumeSession: nil)]
            model.selectedAgent = 1
            model.settings.syncBack = true
        }
    }
}
