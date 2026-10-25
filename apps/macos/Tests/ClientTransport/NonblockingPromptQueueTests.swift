import AppKit
import Foundation
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct NonblockingPromptQueueTests {
    typealias Fixture = PromptDeliveryTests.Fixture

    @Test func slowAcceptanceQueuesRepeatedTextAndDrainsInOrderOffscreen() async throws {
        let fixture = try Fixture(mode: .accepted, fragmentReplies: true)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        fixture.model.settings.followUpDelay = 0
        await fixture.replies.enableOrderedDelivery()
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("First request")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.createdCommands.count == 1 }
        let first = try #require(fixture.model.pendingPromptSubmission)
        for _ in 0..<2 {
            fixture.model.updatePrompt("The same text is a distinct intentional message")
            #expect(fixture.model.canSubmitPrompt)
            await fixture.model.submitPrompt()
            #expect(fixture.model.prompt.isEmpty)
        }
        let queued = try #require(fixture.store.loadSnapshot().deferredPromptsByWorkspace[first.contextKey])
        #expect(queued.count == 2 && Set(queued.map(\.item.id)).count == 2)
        #expect(queued.allSatisfy { $0.destination == .afterCommand(first.commandID) })
        fixture.model.selectedWorkspaceID = "unrelated-workspace"
        fixture.model.selectedConversationID = "unrelated-conversation"
        fixture.model.activeRun = nil
        fixture.model.updatePrompt("A different workspace's draft")
        await fixture.replies.releaseAcceptance()
        try await wait { fixture.model.acceptedPromptResultsByWorkspace.isEmpty && fixture.model.promptOutboxByWorkspace.isEmpty }
        let destination = try #require(await fixture.replies.knownRuns["ordered-1"])
        fixture.model.selectedWorkspaceID = "workspace"
        fixture.model.selectedConversationID = "delivery-conversation"
        fixture.model.activeRun = destination
        fixture.model.selectedAgent = 1
        fixture.model.workspaces = fixture.model.workspaces.map { $0.replacingRunSummary(with: destination, startingNewTurn: true) }
        fixture.model.workspaceConversations = [.init(id: "delivery-conversation", title: "Conversation", runs: [destination], resumeSession: nil)]
        fixture.model.updatePrompt("Typed after the receipt, behind the existing backlog")
        await fixture.model.submitPrompt()
        let backlog = try #require(fixture.store.loadSnapshot().deferredPromptsByWorkspace[first.contextKey])
        #expect(backlog.count == 3 && Set(backlog.compactMap(\.chainID)).count == 1)
        fixture.model.selectedWorkspaceID = "unrelated-workspace"
        fixture.model.selectedConversationID = "unrelated-conversation"
        fixture.model.activeRun = nil
        fixture.model.updatePrompt("A different workspace's draft")
        try await fixture.replies.completeOrderedRun("ordered-1")
        try await wait { await fixture.replies.createdCommands.count == 2 }
        try await wait { fixture.model.acceptedPromptResultsByWorkspace.isEmpty && fixture.model.promptOutboxByWorkspace.isEmpty }
        try await fixture.replies.completeOrderedRun("ordered-2")
        try await wait { await fixture.replies.createdCommands.count == 3 }
        try await wait { fixture.model.acceptedPromptResultsByWorkspace.isEmpty && fixture.model.promptOutboxByWorkspace.isEmpty }
        try await fixture.replies.completeOrderedRun("ordered-3")
        try await wait { await fixture.replies.createdCommands.count == 4 }
        try await wait { fixture.model.acceptedPromptResultsByWorkspace.isEmpty && fixture.model.promptOutboxByWorkspace.isEmpty }
        let bodies = try await fixture.replies.startBodies.map { try JSONSerialization.jsonObject(with: $0) as! [String: Any] }
        #expect(bodies.map { $0["prompt"] as? String } == ["First request", queued[0].item.prompt, queued[1].item.prompt,
            "Typed after the receipt, behind the existing backlog"])
        #expect(bodies.map { $0["run_id"] as? String } == ["previous-turn", "ordered-1", "ordered-2", "ordered-3"])
        #expect(bodies.map { $0["agent"] as? Int } == [7, 1, 1, 1])
        #expect(await fixture.replies.allStartsWereDurable)
        #expect(await fixture.replies.startCommands.count == 4)
        #expect(fixture.model.selectedWorkspaceID == "unrelated-workspace")
        #expect(fixture.model.prompt == "A different workspace's draft")
        #expect(fixture.store.loadSnapshot().deferredPromptsByWorkspace.values.allSatisfy { $0.isEmpty })
    }

    @Test func aRestartRecoversTheSameCommandAndItsSuccessorWithoutAResend() async throws {
        let fixture = try Fixture(mode: .accepted, fragmentReplies: true)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.settings.followUpDelay = 0
        await fixture.replies.enableOrderedDelivery()
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("Before restart")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.createdCommands.count == 1 }
        let original = try #require(fixture.store.loadSnapshot().promptOutboxByWorkspace.values.first)
        fixture.model.updatePrompt("After the original, not another copy of it")
        await fixture.model.submitPrompt()
        let pending = try #require(fixture.store.loadSnapshot().deferredPromptsByWorkspace[original.contextKey]?.first)
        let task = try #require(fixture.model.submitPendingPrompt(original))
        task.cancel()
        fixture.model.cancelDeferredPromptProcessing()
        await fixture.replies.releaseAcceptance()
        await task.value
        fixture.model.stopWindowObservation()
        let restored = AppModel(client: fixture.client, windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: fixture.directory),
            runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: fixture.cacheDirectory)))
        defer { restored.cancelDeferredPromptProcessing(); restored.stopWindowObservation() }
        restored.workspaces = fixture.model.workspaces
        restored.selectedWorkspaceID = "unrelated-workspace"
        restored.updatePrompt("Keep this draft")
        restored.resumePendingPromptSubmissions()
        try await wait { restored.promptOutboxByWorkspace.isEmpty && restored.acceptedPromptResultsByWorkspace.isEmpty }
        try await fixture.replies.completeOrderedRun("ordered-1")
        try await wait { await fixture.replies.createdCommands.count == 2 }
        #expect(await fixture.replies.startCommands == [original.commandID, "\(pending.item.id.uuidString.lowercased())-attempt-0"])
        #expect(await fixture.replies.receiptCommands == [original.commandID])
        #expect(restored.prompt == "Keep this draft")
        #expect(restored.selectedWorkspaceID == "unrelated-workspace")
    }

    @Test(arguments: [false, true])
    func navigationHydrationDoesNotBlockSavingAndDeletionWinsALateSourceRead(remove: Bool) async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        fixture.model.settings.followUpDelay = 0
        let run = try #require(fixture.model.activeRun)
        await fixture.replies.enableOrderedDelivery()
        await fixture.replies.holdSourceReading(run)
        fixture.model.activeRun = nil
        fixture.model.updatePrompt("Deliver only to the captured conversation")
        #expect(fixture.model.promptSubmissionPlan == .enqueue && fixture.model.canSubmitPrompt)
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.sourceReads == 1 }
        let pending = try #require(fixture.model.visibleDeferredPrompts.first)
        #expect(pending.destination == .run("previous-turn", agent: nil))
        if remove {
            #expect(await fixture.model.changeQueuedFollowUp(pending.item.id, change: .remove))
        } else {
            fixture.model.selectedWorkspaceID = "unrelated-workspace"
            fixture.model.selectedConversationID = "another"
            fixture.model.settings.resumeSessionID = "DO-NOT-USE"
            fixture.model.updatePrompt("Do not clear this other draft")
        }
        await fixture.replies.releaseSourceReading()
        if remove {
            try await Task.sleep(for: .milliseconds(100))
            #expect(await fixture.replies.startCommands.isEmpty)
            #expect(fixture.store.loadSnapshot().deferredPromptsByWorkspace.values.allSatisfy { $0.isEmpty })
        } else {
            try await wait { await fixture.replies.startCommands.count == 1 }
            let body = try #require(JSONSerialization.jsonObject(with: await fixture.replies.startBodies[0]) as? [String: Any])
            #expect(body["run_id"] as? String == "previous-turn" && body["agent"] as? Int == 7)
            #expect(fixture.model.prompt == "Do not clear this other draft")
        }
    }

    @Test func choosingAnotherAgentFreezesThatBranchInsteadOfFollowingThePendingMessage() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("From Agent 7")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.startCommands.count == 1 }
        fixture.model.selectedAgent = 6
        fixture.model.updatePrompt("From Agent 6, not the pending Agent 7 result")
        await fixture.model.submitPrompt()
        let pending = try #require(fixture.model.visibleDeferredPrompts.first)
        #expect(pending.destination == .run("previous-turn", agent: 6))
        fixture.model.selectedAgent = 7
        #expect(fixture.store.loadSnapshot().deferredPromptsByWorkspace[pending.contextKey]?.first == pending)
        #expect(await fixture.model.changeQueuedFollowUp(pending.item.id, change: .remove))
        await fixture.replies.releaseAcceptance()
    }

    @Test func storageApprovalIsStillRequiredWhileLaterMessagesCanBeSaved() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.updatePrompt("Requires capacity approval")
        await fixture.model.submitPrompt()
        try await wait { fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation }
        let original = try #require(fixture.model.pendingPromptSubmission)
        fixture.model.updatePrompt("Wait behind that original approval")
        await fixture.model.submitPrompt()
        #expect(fixture.model.prompt.isEmpty)
        #expect(fixture.model.visibleDeferredPrompts.count == 1)
        #expect(fixture.model.pendingPromptSubmission?.commandID == original.commandID)
        #expect(await fixture.replies.startCommands == [original.commandID])
        fixture.model.cancelStorageUsage(expectedCommandID: original.commandID)
        #expect(fixture.store.loadSnapshot().deferredPromptsByWorkspace[original.contextKey]?.first?.item.isPaused == true)
        #expect(await fixture.replies.createdCommands.isEmpty)
    }

    @Test func previewingAnUnusedAgentCannotForkTheAlreadyAppliedBranch() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("Continue the applied result")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.startCommands.count == 1 }
        let original = try #require(fixture.model.pendingPromptSubmission)
        fixture.model.selectedAgent = 6
        fixture.model.updatePrompt("The next question still follows the applied conversation")
        await fixture.model.submitPrompt()
        let queued = try #require(fixture.model.visibleDeferredPrompts.first)
        #expect(queued.destination == .afterCommand(original.commandID))
        #expect(queued.originAgent == 7)
        await fixture.replies.releaseAcceptance()
    }

    @Test func aRemoteHandoffQueuesToItsKnownNewTurnInsteadOfTheOldVisibleTurn() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(runID: "old")
        let old = try #require(fixture.model.activeRun)
        try fixture.installCompletedConversation(runID: "new")
        let new = try #require(fixture.model.activeRun)
        fixture.model.workspaceConversations = [.init(id: "delivery-conversation", title: "Conversation",
            runs: [old, new], resumeSession: nil)]
        fixture.model.activeRun = old
        fixture.model.remoteTurnTransition = .init(requestID: 1, machineID: "local",
            workspaceID: "workspace", conversationID: "delivery-conversation")
        fixture.model.updatePrompt("Follow the actual next turn")
        await fixture.model.submitPrompt()
        #expect(fixture.model.visibleDeferredPrompts.first?.destination == .run("new", agent: nil))
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func anotherWorkspacesStaleProjectionCannotSupplyTheMessageSourceOrSettings() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.workspaces.append(try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"other","name":"Other","path":"/other","lastRunId":"",
             "lastResumeSessionId":"","selected":false,"available":true,"readable":true,"writable":true}
            """.utf8)))
        fixture.model.selectedWorkspaceID = "other"
        var settings = RunSettings()
        settings.agents = 2
        fixture.model.settingsByWorkspace[MachineContextKey.workspace(machineID: "local", workspaceID: "other")] = settings
        fixture.model.settings.agents = 9
        fixture.model.updatePrompt("This belongs only to the newly chosen workspace")
        await fixture.model.submitPrompt()
        let queued = try #require(fixture.model.deferredPromptsByWorkspace.values.first?.first)
        #expect(queued.workspaceID == "other" && queued.workspacePath == "/other")
        #expect(queued.destination == .workspace && queued.originRunID == nil)
        #expect(queued.item.settings?.agents == 2)
        #expect(await fixture.replies.startCommands.isEmpty)
    }

    @Test func stoppingPausesTheDeferredQueueBeforeALateReadCanPromoteIt() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        let run = try #require(fixture.model.activeRun)
        await fixture.replies.holdSourceReading(run)
        fixture.model.activeRun = nil
        fixture.model.updatePrompt("Do not send after Stop")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.sourceReads == 1 }
        await fixture.model.pauseDeferredPrompts(workspaceID: "workspace", machineID: "local")
        await fixture.replies.releaseSourceReading()
        try await Task.sleep(for: .milliseconds(100))
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(fixture.model.visibleDeferredPrompts.first?.item.isPaused == true)
    }

    @Test func anUnregisteredInlineSnapshotIsRevalidatedInTheExactWorkspaceWithoutUserAction() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        await fixture.replies.enableOrderedDelivery()
        await fixture.replies.useUnregisteredInlineSnapshot()
        fixture.model.updatePrompt("The correct registered workspace")
        await fixture.model.submitPrompt()
        try await wait { fixture.model.activeRun?.runId == "ordered-1" }
        #expect(fixture.model.pendingPromptSubmission == nil)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
        #expect(await fixture.replies.scopedReads.contains("workspace"))
        #expect(await fixture.replies.startCommands.count == 1)
    }

    @Test func theNativeComposerRemainsEditableWithQueuedMessagesAndNoInternalWarning() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation(finalized: false)
        await fixture.replies.holdAcceptance()
        fixture.model.updatePrompt("First message")
        await fixture.model.submitPrompt()
        try await wait { await fixture.replies.startCommands.count == 1 }
        fixture.model.updatePrompt("第二条消息")
        await fixture.model.submitPrompt()
        fixture.model.updatePrompt("可以继续输入和排队发送")
        let host = NSHostingView(rootView: PromptComposer().environmentObject(fixture.model)
            .environment(\.appLanguage, .chinese).environment(\.appTextSize, .standard)
            .preferredColorScheme(.dark))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 920, height: 420),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        host.frame = window.contentView!.bounds
        host.layoutSubtreeIfNeeded()
        #expect(!window.isVisible && !window.isKeyWindow)
        #expect(fixture.model.canSubmitPrompt && fixture.model.promptComposerContextText == nil)
        #expect(fixture.model.composerQueuedMessages.map(\.prompt) == ["第二条消息"])
        if let path = ProcessInfo.processInfo.environment["PCR_NONBLOCKING_COMPOSER_PNG"] {
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            try #require(bitmap.representation(using: .png, properties: [:])).write(to: URL(fileURLWithPath: path))
        }
        await fixture.replies.releaseAcceptance()
    }

    @Test func approvalRebindsTheDependencyWithoutChangingEitherMessageIdentity() async throws {
        let fixture = try Fixture(mode: .storageConfirmation)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        fixture.model.updatePrompt("Approve this original message")
        await fixture.model.submitPrompt()
        try await wait { fixture.model.pendingPromptSubmission?.status == .awaitingConfirmation }
        let original = try #require(fixture.model.pendingPromptSubmission)
        fixture.model.updatePrompt("Keep my original queue identity")
        await fixture.model.submitPrompt()
        let queued = try #require(fixture.model.visibleDeferredPrompts.first)
        await fixture.model.confirmStorageUsage(expectedCommandID: original.commandID)
        try await wait { await fixture.replies.startCommands.count == 2 }
        let saved = try #require(fixture.store.loadSnapshot().deferredPromptsByWorkspace[original.contextKey]?.first)
        #expect(saved.item.id == queued.item.id && saved.item.prompt == queued.item.prompt)
        #expect(saved.destination == .afterCommand(original.commandID + "-confirmed")
            || saved.destination == .run("accepted", agent: nil))
        #expect(await fixture.replies.startCommands == [original.commandID, original.commandID + "-confirmed"])
    }

    @Test func queuePromotionHasOneDurableOwnerAndDoesNotEraseAnotherWorkspace() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let queued = PersistedDeferredPrompt(item: QueuedFollowUp(prompt: "Queued once", settings: RunSettings()),
            machineID: "local", workspaceID: "workspace", workspacePath: "/qa", conversationID: "conversation",
            destination: .newConversation)
        var other = queued
        other = PersistedDeferredPrompt(item: other.item, machineID: "other-machine", workspaceID: "workspace",
            workspacePath: "/other", conversationID: "other-conversation", destination: .newConversation)
        #expect(await fixture.store.appendDeferredPrompt(queued) != nil)
        #expect(await fixture.store.appendDeferredPrompt(other) != nil)
        let submission = PersistedPromptSubmission(id: queued.item.id.uuidString.lowercased(), machineID: "local",
            workspaceID: "workspace", prompt: queued.item.prompt, settings: RunSettings(), sourceRunID: nil, sourceAgent: nil,
            resumeSessionID: "NO", confirmLargeRun: false, attempt: 0, status: .pending, error: "", workspacePath: "/qa")
        #expect(await fixture.store.promoteDeferredPrompt(queued, submission: submission) != nil)
        #expect(await fixture.store.promoteDeferredPrompt(queued, submission: submission) == nil)
        #expect(!(await fixture.store.replaceDeferredPrompts(key: queued.contextKey, expected: [queued], replacement: [])))
        let saved = fixture.store.loadSnapshot()
        #expect(saved.promptOutboxByWorkspace[queued.contextKey]?.id == submission.id)
        #expect(saved.deferredPromptsByWorkspace[queued.contextKey] == nil)
        #expect(saved.deferredPromptsByWorkspace[other.contextKey] == [other])
    }

    @Test func aLateStatusWriteCannotResurrectASettledMessageOrEraseANewOwner() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        var original = PersistedPromptSubmission(id: "old", machineID: "local", workspaceID: "workspace", prompt: "Original",
            settings: RunSettings(), sourceRunID: nil, sourceAgent: nil, resumeSessionID: "", confirmLargeRun: false,
            attempt: 0, status: .pending, error: "", workspacePath: "/qa")
        original.errorCode = "invalid_token"
        #expect(await fixture.store.recordPromptSubmission(original))
        let accepted = PersistedAcceptedPromptResult(submission: original, runID: "accepted", continuedSessionID: "", workspaceRevision: 1)
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: original.contextKey, expected: original, result: accepted))
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace[original.contextKey]?.submission.needsConnectionRecovery == false)
        #expect(fixture.store.consumeAcceptedPromptResult(accepted))
        original.status = .retrying
        fixture.store.updatePromptSubmission(original, expected: original)
        fixture.store.updateAcceptedPromptResult(accepted)
        fixture.store.flushPendingWrites()
        #expect(fixture.store.loadSnapshot().promptOutboxByWorkspace.isEmpty)
        #expect(fixture.store.loadSnapshot().acceptedPromptResultsByWorkspace.isEmpty)
    }

    @Test func anAppendRacingPromotionAndSettlementKeepsItsCausalPredecessor() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let original = PersistedDeferredPrompt(item: QueuedFollowUp(prompt: "First", settings: RunSettings()),
            machineID: "local", workspaceID: "workspace", workspacePath: "/qa", conversationID: "conversation",
            destination: .session("saved"), chainID: "chain")
        #expect(await fixture.store.appendDeferredPrompt(original) != nil)
        let submission = PersistedPromptSubmission(id: original.item.id.uuidString.lowercased(), machineID: "local",
            workspaceID: "workspace", prompt: "First", settings: RunSettings(), sourceRunID: nil, sourceAgent: nil,
            resumeSessionID: "saved", confirmLargeRun: false, attempt: 0, status: .pending, error: "",
            workspacePath: "/qa", deliveryChainID: "chain")
        #expect(await fixture.store.promoteDeferredPrompt(original, submission: submission) != nil)
        var second = original
        second.item = QueuedFollowUp(prompt: "Second", settings: RunSettings())
        #expect(await fixture.store.appendDeferredPrompt(second)?.last?.destination == .afterCommand(submission.commandID))
        let accepted = PersistedAcceptedPromptResult(submission: submission, runID: "next",
            continuedSessionID: "saved", workspaceRevision: 1)
        #expect(fixture.store.recordAcceptedPromptResult(contextKey: submission.contextKey, expected: submission, result: accepted))
        #expect(fixture.store.consumeAcceptedPromptResult(accepted, conversationID: "canonical"))
        var third = second
        third.item = QueuedFollowUp(prompt: "Third", settings: RunSettings())
        third.destination = .afterCommand(submission.commandID)
        let saved = try #require(await fixture.store.appendDeferredPrompt(third))
        #expect(saved.map(\.destination) == [.run("next", agent: nil), .run("next", agent: nil)])
        #expect(saved.last?.conversationID == "canonical")
        #expect(saved.map(\.item.prompt) == ["Second", "Third"])
    }

    @Test func queueWriteFailureRetainsTheDraftAndMakesNoNetworkMutation() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let blocked = fixture.directory.appendingPathComponent("not-a-directory")
        try Data("blocked".utf8).write(to: blocked)
        let model = AppModel(client: fixture.client, windowID: UUID(),
            desktopStateStore: DesktopStateStore(windowID: UUID(), directory: blocked),
            runViewStateStore: WorkspaceRunViewStateStore())
        defer { model.cancelDeferredPromptProcessing(); model.stopWindowObservation() }
        model.workspaces = fixture.model.workspaces
        model.selectedWorkspaceID = "workspace"
        model.selectedConversationID = "still-loading"
        model.updatePrompt("Keep every byte of this draft")
        await model.submitPrompt()
        #expect(model.prompt == "Keep every byte of this draft")
        #expect(model.deferredPromptsByWorkspace.isEmpty)
        #expect(await fixture.replies.startCommands.isEmpty)
        #expect(model.errorMessage != nil)
    }

    @Test func reconnectingAutomaticallyDeliversTheSameQueuedMessageWithoutManualRetry() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        try fixture.installCompletedConversation()
        let source = try #require(fixture.model.activeRun)
        fixture.model.settings.followUpDelay = 0
        await fixture.replies.enableOrderedDelivery()
        await fixture.replies.registerRun(source)
        await fixture.replies.requireSourceLogin(true)
        fixture.model.activeRun = nil
        fixture.model.updatePrompt("Retain this exact message through login recovery")
        await fixture.model.submitPrompt()
        try await wait { fixture.model.visibleDeferredPrompts.first?.item.lastError != nil }
        let original = try #require(fixture.model.visibleDeferredPrompts.first)
        #expect(!original.item.isPaused)
        #expect(await fixture.replies.startCommands.isEmpty)
        await fixture.replies.requireSourceLogin(false)
        try await wait { await fixture.replies.createdCommands.count == 1 }
        #expect(await fixture.replies.startCommands == ["\(original.item.id.uuidString.lowercased())-attempt-0"])
        #expect(await fixture.replies.allStartsWereDurable)
    }

    @Test func aSlowLocalCommitCannotClearAnIdenticalDraftInAnotherWorkspaceWithoutAConversationID() async throws {
        let fixture = try Fixture(mode: .accepted)
        defer { fixture.close() }
        let writes = DispatchQueue(label: "nonblocking-delivery-test-writes")
        let store = DesktopStateStore(windowID: UUID(), directory: fixture.directory.appendingPathComponent("slow"), writeQueue: writes)
        let model = AppModel(client: fixture.client, windowID: UUID(), desktopStateStore: store,
            runViewStateStore: WorkspaceRunViewStateStore())
        defer { model.cancelDeferredPromptProcessing(); model.stopWindowObservation() }
        model.workspaces = fixture.model.workspaces
        model.selectedWorkspaceID = "workspace"
        model.selectedConversationID = nil
        model.updatePrompt("Identical text in two independent drafts")
        let gate = DispatchSemaphore(value: 0)
        writes.async { gate.wait() }
        let saving = Task { await model.submitPrompt() }
        let key = MachineContextKey.workspace(machineID: "local", workspaceID: "workspace")
        try await wait { model.deferredPromptSaves.contains(key) }
        model.selectedWorkspaceID = "other-workspace"
        model.selectedConversationID = nil
        model.updatePrompt("Identical text in two independent drafts")
        gate.signal()
        await saving.value
        model.cancelDeferredPromptProcessing()
        #expect(model.prompt == "Identical text in two independent drafts")
        #expect(store.loadSnapshot().deferredPromptsByWorkspace[key]?.first?.workspaceID == "workspace")
    }

    private func wait(timeout: Duration = .seconds(8), _ predicate: @MainActor () async -> Bool) async throws {
        let deadline = ContinuousClock.now + timeout
        while !(await predicate()), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
        try #require(await predicate())
    }
}
