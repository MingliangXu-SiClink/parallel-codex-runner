import Foundation
import Testing
@testable import PCRRemoteMac

struct FollowUpReconciliationTests {
    @Test func firstReceiptPreservesNextUnacknowledgedMessage() {
        let first = QueuedFollowUp(prompt: "First")
        let next = QueuedFollowUp(prompt: "Next")
        var received = first
        received.daemonAcknowledged = true
        let merged = FollowUpQueueReconciliation.merge(remote: [received], local: [first, next])
        #expect(merged.map(\.id) == [first.id, next.id])
        #expect(merged[0].daemonAcknowledged == true)
        #expect(merged[1].needsDaemonAcknowledgement)
    }

    @Test func receiptAfterDispatchDoesNotResurrectTheMessage() {
        let first = QueuedFollowUp(prompt: "First")
        let next = QueuedFollowUp(prompt: "Next")
        #expect(FollowUpQueueReconciliation.merge(remote: [], local: [first, next],
            acknowledged: [first.id]).map(\.id) == [next.id])
        #expect(FollowUpQueueReconciliation.merge(remote: [], local: [first],
            completedID: first.id.uuidString.lowercased()).isEmpty)
    }

    @Test func staleSnapshotCannotRevertSelectionOrQueue() {
        let newer = snapshot(generation: 5, runID: "next")
        let older = snapshot(generation: 4, runID: "previous")
        #expect(FollowUpQueueReconciliation.newest(older, previous: newer)?.runId == "next")
        #expect(FollowUpQueueReconciliation.newest(newer, previous: older)?.generation == 5)
    }

    @Test func acknowledgedRemovalIsNotUndoneButFailedLocalMessageRemains() {
        var accepted = QueuedFollowUp(prompt: "Removed elsewhere")
        accepted.daemonAcknowledged = true
        var failed = QueuedFollowUp(prompt: "Keep me")
        failed.paused = true
        failed.lastError = "Retry after correcting the destination"
        #expect(FollowUpQueueReconciliation.merge(remote: [], local: [accepted, failed]) == [failed])
    }

    private func snapshot(generation: Int, runID: String) -> RemoteFollowUpQueue {
        RemoteFollowUpQueue(workspaceId: "workspace", runId: runID, items: [], selectedAgent: 2,
            state: "waiting", deadlineEpochMs: nil, lastError: nil, generation: generation,
            lastCompletedItemId: nil)
    }

    private func pausedQueue(attempt: Int = 0, error: String? = nil,
                             provenance: String? = nil, state: String = "paused") -> RemoteFollowUpQueue {
        let item = RemoteFollowUpQueueItem(id: "4fba1ec5-0781-4a0d-bf72-47f611573a0b",
            prompt: "Keep the original message", settings: RunSettings(), attempt: attempt, failureCount: 0,
            retryAtEpochMs: nil,
            lastError: error ?? "The conversation changed before this queued message could be sent. Open the queue to review it.",
            paused: true, enqueuedRunId: provenance)
        return RemoteFollowUpQueue(workspaceId: "workspace", runId: "old", items: [item],
            selectedAgent: 7, state: state, deadlineEpochMs: nil, lastError: item.lastError,
            generation: 31, lastCompletedItemId: "consumed", conversationId: "conversation")
    }

    @Test func admissionRecoveryKeepsTheOriginalUUIDSettingsAndAttempt() throws {
        let queue = pausedQueue()
        let original = try #require(FollowUpAdmissionRecovery.originalItem(in: queue, currentRunID: "current"))
        #expect(original.id.uuidString.lowercased() == queue.items[0].id)
        #expect(original.prompt == queue.items[0].prompt && original.settings == queue.items[0].settings)
        #expect(original.attempt == 0 && original.daemonAcknowledged == true)
        #expect(FollowUpAdmissionRecovery.originalItem(in: pausedQueue(provenance: "current"), currentRunID: "current") != nil)
    }

    @Test func admissionRecoveryNeverRecreatesARetryOrOverridesOtherPauses() {
        for queue in [pausedQueue(attempt: 1), pausedQueue(error: "Queue paused after stopping this turn."),
                      pausedQueue(provenance: "different"), pausedQueue(state: "dispatching")] {
            #expect(FollowUpAdmissionRecovery.originalItem(in: queue, currentRunID: "current") == nil)
        }
        #expect(FollowUpAdmissionRecovery.originalItem(in: pausedQueue(), currentRunID: "old") == nil)
    }
}
