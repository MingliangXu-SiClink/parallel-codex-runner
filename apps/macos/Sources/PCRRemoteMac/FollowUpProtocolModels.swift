import Foundation

struct FollowUpQueueGetBody: Encodable, Sendable {
    let workspaceId: String
}

struct FollowUpAgentSelectionBody: Encodable, Sendable {
    let workspaceId: String
    let runId: String
    let agent: Int
}

struct FollowUpQueueClearBody: Encodable, Sendable {
    let workspaceId: String
    let runId: String
}

struct FollowUpQueueItemBody: Encodable, Equatable, Sendable {
    let id: String
    let prompt: String
    let settings: RunSettings
    let attempt: Int

    init(_ item: QueuedFollowUp, fallbackSettings: RunSettings) {
        id = item.id.uuidString.lowercased()
        prompt = item.prompt
        settings = item.settings ?? fallbackSettings
        attempt = max(0, item.attempt ?? 0)
    }
}

struct FollowUpQueueReplacementProof: Sendable {
    let runID: String
    let generation: Int?
    let previousIDs: Set<UUID>
    let items: [FollowUpQueueItemBody]

    func matches(_ queue: RemoteFollowUpQueue?) -> Bool {
        guard let queue, queue.runId == runID,
              generation.map({ (queue.generation ?? -1) > $0 }) ?? true else { return false }
        let received = queue.items.compactMap { $0.localValue() }
        guard received.count == queue.items.count else { return false }
        let desiredIDs = Set(items.map(\.id))
        let removedIDs = Set(previousIDs.map { $0.uuidString.lowercased() }).subtracting(desiredIDs)
        guard !received.contains(where: { removedIDs.contains($0.id.uuidString.lowercased()) }) else { return false }
        let requested = received.filter { desiredIDs.contains($0.id.uuidString.lowercased()) }
            .map { FollowUpQueueItemBody($0, fallbackSettings: RunSettings()) }
        // Unrelated concurrent enqueues are retained, not overwritten by recovery.
        return requested == items
    }
}

struct FollowUpQueueReplaceBody: Encodable, Sendable {
    let workspaceId: String
    let runId: String
    let items: [FollowUpQueueItemBody]
    let selectedAgent: Int?
    let resetCountdown: Bool
    var expectedGeneration: Int? = nil
}

struct FollowUpQueueEnqueueBody: Encodable, Sendable {
    let workspaceId: String
    let runId: String
    let item: FollowUpQueueItemBody
    let selectedAgent: Int?
}

struct FollowUpQueueReconcileBody: Encodable, Sendable {
    let enqueueCommandId: String
    let enqueue: FollowUpQueueEnqueueBody
}

struct RemoteFollowUpQueueResponse: Decodable, Sendable {
    let revision: Int
    let queue: RemoteFollowUpQueue?
}

struct RemoteFollowUpQueue: Decodable, Equatable, Sendable {
    let workspaceId: String
    let runId: String
    let items: [RemoteFollowUpQueueItem]
    let selectedAgent: Int?
    let state: String
    let deadlineEpochMs: Int?
    let lastError: String?
    let generation: Int?
    let lastCompletedItemId: String?
    var selectionSource: String? = nil
    var conversationId: String? = nil

    func belongsToConversation(ownerIDs: Set<String>, runIDs: Set<String>) -> Bool {
        if let owner = conversationId?.trimmingCharacters(in: .whitespacesAndNewlines),
           !owner.isEmpty {
            return ownerIDs.contains(owner)
        }
        return runIDs.contains(runId)
    }
}

struct RemoteFollowUpQueueItem: Decodable, Equatable, Sendable {
    let id: String
    let prompt: String
    let settings: RunSettings
    let attempt: Int?
    let failureCount: Int?
    let retryAtEpochMs: Int?
    let lastError: String?
    let paused: Bool?
    var enqueuedRunId: String? = nil

    func localValue() -> QueuedFollowUp? {
        guard let identifier = UUID(uuidString: id) else { return nil }
        var value = QueuedFollowUp(
            id: identifier,
            prompt: prompt,
            settings: settings,
            attempt: max(0, attempt ?? 0)
        )
        value.failureCount = max(0, failureCount ?? 0)
        value.retryAt = retryAtEpochMs.map {
            Date(timeIntervalSince1970: TimeInterval($0) / 1_000)
        }
        value.lastError = lastError
        value.paused = paused
        value.daemonAcknowledged = true
        return value
    }
}

enum FollowUpAdmissionRecovery {
    static func originalItem(in queue: RemoteFollowUpQueue, currentRunID: String) -> QueuedFollowUp? {
        guard queue.state == "paused", queue.runId != currentRunID,
              queue.generation != nil, queue.lastCompletedItemId != nil,
              queue.items.count == 1, let head = queue.items.first,
              head.paused == true, (head.attempt ?? 0) == 0, (head.failureCount ?? 0) == 0,
              head.lastError == "The conversation changed before this queued message could be sent. Open the queue to review it.",
              head.enqueuedRunId == nil || head.enqueuedRunId == currentRunID else { return nil }
        return head.localValue()
    }
}

enum FollowUpQueueReconciliation {
    static func merge(
        remote: [QueuedFollowUp],
        local: [QueuedFollowUp],
        acknowledged: Set<UUID> = [],
        completedID: String? = nil
    ) -> [QueuedFollowUp] {
        let known = Set(remote.map(\.id)).union(acknowledged)
        return remote + local.filter {
            $0.needsDaemonAcknowledgement && !known.contains($0.id)
                && $0.id.uuidString.lowercased() != completedID?.lowercased()
        }
    }

    static func newest(_ incoming: RemoteFollowUpQueue?, previous: RemoteFollowUpQueue?) -> RemoteFollowUpQueue? {
        guard let previous else { return incoming }
        guard let incoming else { return nil }
        if let old = previous.generation, let new = incoming.generation, new < old {
            return previous
        }
        return incoming
    }
}
