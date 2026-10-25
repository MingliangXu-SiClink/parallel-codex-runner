import Foundation

struct TranscriptPresentationKey: Hashable {
    let machineID: String
    let workspaceID: String
    let conversationID: String
    let runID: String
    let agent: Int
}

struct TranscriptPresentationSnapshot {
    let input: TranscriptProjectionInput
    let blocks: [TranscriptBlock]
    let generation: UInt64
    let timelineInput: TranscriptTimelineProjectionInput?
    let timelineBlocks: [TranscriptBlock]
    let timelineGeneration: UInt64
    let itemsInput: ConversationTimelineItemsProjectionInput?
    let items: [ConversationTimelineItem]
    let itemsGeneration: UInt64

    var cost: Int {
        let textBytes = input.history.reduce(0) { $0 + $1.text.utf8.count }
            + input.transcript.blocks.reduce(0) { $0 + $1.text.utf8.count }
            + (input.transcript.conversation?.items.reduce(0) { $0 + $1.block.text.utf8.count } ?? 0)
            + input.transcript.finalMessage.utf8.count
            + blocks.reduce(0) { $0 + $1.text.utf8.count + ($1.image?.data.count ?? 0) }
            + timelineBlocks.reduce(0) { $0 + $1.text.utf8.count + ($1.image?.data.count ?? 0) }
        // Items may retain another projection of the same strings. Account
        // conservatively even where Swift's copy-on-write storage is shared.
        return max(1024, textBytes * 2 + (blocks.count + timelineBlocks.count + items.count) * 1024)
    }
}

struct TranscriptPresentationCache {
    private var entries: [TranscriptPresentationKey: TranscriptPresentationSnapshot] = [:]
    private var order: [TranscriptPresentationKey] = []
    private var costs: [TranscriptPresentationKey: Int] = [:]
    private var readyRuns: Set<WorkspaceRunViewKey> = []
    private(set) var totalCost = 0
    let capacity: Int
    let maximumCost: Int

    init(capacity: Int = .max, maximumCost: Int = 32 * 1024 * 1024) {
        self.capacity = capacity
        self.maximumCost = maximumCost
    }

    mutating func value(for key: TranscriptPresentationKey,
                        matching input: TranscriptProjectionInput) -> TranscriptPresentationSnapshot? {
        let key = cacheKey(key)
        guard let entry = entries[key], entry.input == input else { return nil }
        order.removeAll { $0 == key }
        order.append(key)
        return entry
    }

    mutating func put(_ snapshot: TranscriptPresentationSnapshot, for key: TranscriptPresentationKey) {
        let key = cacheKey(key)
        if let existing = entries[key], existing.generation > snapshot.generation { return }
        if entries[key]?.generation == snapshot.generation,
           entries[key]?.timelineGeneration == snapshot.timelineGeneration,
           entries[key]?.itemsGeneration == snapshot.itemsGeneration { return }
        totalCost -= costs.removeValue(forKey: key) ?? 0
        entries.removeValue(forKey: key)
        order.removeAll { $0 == key }
        let cost = snapshot.cost
        guard capacity > 0, cost <= maximumCost || isReady(key) else { return }
        entries[key] = snapshot
        costs[key] = cost
        totalCost += cost
        order.append(key)
        evictHistoryIfNeeded()
    }

    mutating func retainReadyRuns(_ runs: Set<WorkspaceRunViewKey>) {
        readyRuns = runs
        evictHistoryIfNeeded()
    }

    private func cacheKey(_ key: TranscriptPresentationKey) -> TranscriptPresentationKey {
        // A physical run/Agent already identifies its body. Conversation aliases
        // may change during index reconciliation without changing that body.
        TranscriptPresentationKey(machineID: key.machineID, workspaceID: key.workspaceID,
            conversationID: key.runID.isEmpty ? key.conversationID : "", runID: key.runID, agent: key.agent)
    }

    private func isReady(_ key: TranscriptPresentationKey) -> Bool {
        readyRuns.contains(WorkspaceRunViewKey(machineID: key.machineID,
            workspaceID: key.workspaceID, runID: key.runID))
    }

    private mutating func evictHistoryIfNeeded() {
        // Keep one current projection per active branch; only historical views
        // compete for the disposable LRU budget. Finished readers release this pin.
        while order.count > capacity || totalCost > maximumCost {
            guard let index = order.firstIndex(where: { !isReady($0) }) else { break }
            let oldest = order.remove(at: index)
            entries.removeValue(forKey: oldest)
            totalCost -= costs.removeValue(forKey: oldest) ?? 0
        }
    }
}

extension TranscriptFormatter {
    static func preparedConversationBlocks(_ input: TranscriptProjectionInput) -> [TranscriptBlock] {
        var blocks: [TranscriptBlock]
        if let pending = input.pendingSubmission {
            // Derive the carried branch only on a cache miss, never while
            // constructing the cache key during SwiftUI body evaluation.
            let history = input.runID == nil ? input.history : branchConversationHistory(
                history: input.history, prompt: input.prompt, transcript: input.transcript)
            blocks = pendingTurnBlocks(history: history, prompt: pending.prompt,
                status: pending.conversationLabel)
        } else if input.runID != nil || !input.history.isEmpty {
            blocks = Self.blocks(history: input.history, prompt: input.prompt, transcript: input.transcript)
        } else {
            blocks = []
        }
        var seenErrors = Set(blocks.filter { $0.kind == .error }.map {
            $0.text.trimmingCharacters(in: .whitespacesAndNewlines)
        })
        if input.pendingSubmission == nil, input.runID != nil,
           input.runStatus?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() != "discarded" {
            for (id, raw) in [("run-error", input.runError), ("worker-error", input.workerError)] {
                let message = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if !message.isEmpty, seenErrors.insert(message).inserted {
                    blocks.append(TranscriptBlock(id: id, kind: .error, text: message))
                }
            }
        }
        if input.pendingSubmission == nil {
            blocks = AgentQuestionTranscript.project(blocks, questions: input.questions)
        }
        var seen = Set<String>()
        let projected = blocks.enumerated().map { index, block in
            guard !seen.insert(block.id).inserted else { return block }
            return TranscriptBlock(id: "\(block.id)-visible-\(index)", kind: block.kind,
                text: block.text, status: block.status, operationId: block.operationId,
                activityType: block.activityType, operation: block.operation,
                isHistorical: block.isHistorical, isFinalAnswer: block.isFinalAnswer,
                linkOrigin: block.linkOrigin, image: block.image, question: block.question)
        }
        return coalescedForPresentation(removingInternalAssistantMetadata(from: projected))
    }
}
