import Foundation

func isTerminalAgentStatus(_ status: String?) -> Bool {
    guard let status else { return false }
    return [
        "success", "failed", "error", "killed", "cancelled", "canceled",
        "stopped", "interrupted", "discarded",
    ].contains(status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
}

struct ReasoningTokenStep: Identifiable, Equatable, Hashable, Sendable {
    let interval: Int
    let count: Int

    var id: Int { interval }
    var contribution: Int64 { Int64(interval) * Int64(count) }
}

struct RunAgentSummary: Codable, Identifiable, Hashable, Sendable {
    let agent: Int
    let name: String
    let role: String?
    var status: String
    var seconds: Double?
    var reasoningTokens: Int?
    var reasoningTokenCounts: [String: Int]
    let codexThreadId: String?
    let error: String?
    let rejected: Bool
    let stale: Bool?
    let recommended: Bool
    let finalized: Bool
    let workspaceAvailable: Bool?
    let canAccept: Bool?
    let canContinue: Bool?
    let canRetry: Bool?
    var startedAt: String? = nil
    var finishedAt: String? = nil
    var phase: String? = nil
    var phaseTimings: [String: Double]? = nil
    var activity: AgentActivityRecord? = nil
    var pendingQuestionCount: Int? = nil
    var blockingQuestionCount: Int? = nil

    var isObservedWaiting: Bool { normalizedStatus == "running" && activity?.state == "waiting" }
    var hasObservedActiveExecution: Bool {
        normalizedStatus == "running" && !isObservedWaiting && activity?.state != "awaiting_event"
            && (blockingQuestionCount ?? 0) == 0
    }

    var id: Int { agent }
    var normalizedStatus: String { status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
    var isSuccessful: Bool { normalizedStatus == "success" }
    var isSynthesis: Bool { role == "synthesis" }
    var isRunning: Bool {
        ["starting", "resuming", "submitting", "waiting_for_output", "copying", "queued", "running", "waiting_for_models", "stopping"]
            .contains(normalizedStatus)
    }
    var isTerminal: Bool {
        isTerminalAgentStatus(normalizedStatus)
    }
    var canStop: Bool { ["starting", "resuming", "submitting", "waiting_for_output", "running", "waiting_for_models"].contains(normalizedStatus) }
    var mayAccept: Bool {
        isSuccessful && workspaceAvailable != false && (canAccept ?? true)
    }
    var mayContinue: Bool { canContinue ?? mayAccept }
    var mayRetry: Bool { canRetry == true }

    var reasoningSteps: [ReasoningTokenStep] {
        reasoningTokenCounts.compactMap { key, value -> ReasoningTokenStep? in
            guard let interval = Int(key), interval > 0, value > 0 else { return nil }
            return ReasoningTokenStep(interval: interval, count: value)
        }
        .sorted { left, right in
            left.contribution == right.contribution
                ? left.interval > right.interval
                : left.contribution > right.contribution
        }
    }

    var reasoningDisplay: String? {
        reasoningDisplay(language: .english)
    }

    func reasoningDisplay(language: AppLanguage) -> String? {
        guard let reasoningTokens else { return nil }
        let steps = reasoningSteps
        guard !steps.isEmpty else {
            return language.text(
                "推理用量 \(reasoningTokens.formatted())",
                "\(reasoningTokens.formatted()) reasoning tokens"
            )
        }
        let runTotal = steps.reduce(Int64(0)) { $0 + $1.contribution }
        let label = language.text(
            "推理用量 \(runTotal.formatted())",
            "\(runTotal.formatted()) reasoning tokens"
        )

        let visible = Array(steps.prefix(4))
        let hiddenCount = steps.dropFirst(visible.count).reduce(0) { $0 + $1.count }
        let otherLabel = language.text("其他", "other")
        if isRunning {
            var values = visible.map { "\($0.interval):\($0.count)" }
            if hiddenCount > 0 { values.append("\(otherLabel):\(hiddenCount)") }
            return "\(label) (\(values.joined(separator: ", ")))"
        }

        let total = steps.reduce(0) { $0 + $1.count }
        var values = visible.map { step in
            "\(step.interval):\(Self.percentage(step.count, of: total))%"
        }
        if hiddenCount > 0 {
            values.append("\(otherLabel):\(Self.percentage(hiddenCount, of: total))%")
        }
        values.append(language.text("共 \(total) 次", "total:\(total)"))
        return "\(label) (\(values.joined(separator: ", ")))"
    }

    var compactReasoningDisplay: String? {
        guard let reasoningDisplay else { return nil }
        return reasoningDisplay.components(separatedBy: " (").first
    }

    func compactReasoningDisplay(language: AppLanguage) -> String? {
        reasoningDisplay(language: language)?.components(separatedBy: " (").first
    }

    private static func percentage(_ value: Int, of total: Int) -> String {
        guard total > 0 else { return "0" }
        let percentage = Double(value) * 100 / Double(total)
        if percentage.rounded() == percentage { return String(Int(percentage)) }
        return String(format: "%.1f", percentage)
    }
}

func agentSelectionCommitsBeforeHydration(
    transcriptReady: Bool,
    agent _: RunAgentSummary
) -> Bool {
    !transcriptReady
}

struct RunSessionPromotion: Codable, Equatable, Sendable {
    let sessionId: String?
    let workspace: String?
    let error: String?
}

struct RunSummary: Codable, Equatable, Sendable {
    let runId: String
    let status: String
    let workspace: String
    let prompt: String
    let conversationId: String?
    let continuedFromRunId: String?
    let continuedFromAgent: Int?
    let turnIndex: Int?
    let createdAt: String
    var updatedAt: String
    let runRoot: String?
    let config: RunConfiguration
    let recommendedAgent: Int?
    let finalizedAgent: Int?
    var finalizingAgent: Int? = nil
    let syncedBack: Bool
    let workspacesDeleted: Bool
    let codexHomesDeleted: Bool
    let artifactsAvailable: Bool?
    let candidateWorkspacesAvailable: Bool?
    let canAddAgents: Bool?
    let canUpdateSynthesis: Bool?
    let workerActive: Bool
    let workerAvailable: Bool
    let workerPid: Int?
    let workerState: String?
    let workerOperation: String?
    let workerDeadline: String?
    let workerError: String?
    let sessionPromotion: RunSessionPromotion?
    let workspaceRevision: Int?
    let error: String?
    var eventCount: Int
    var completionReason: String? = nil
    var agents: [RunAgentSummary]
    var conversationSnapshot: ConversationSnapshot? = nil
    var continuationAgent: Int? = nil
    var phase: String? = nil
    var phaseTimings: [String: Double]? = nil

    var normalizedStatus: String {
        status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    var isWorking: Bool {
        workerActive || ["preparing", "running", "stopping", "finalizing"].contains(normalizedStatus)
    }

    var isExecutionComplete: Bool {
        !isWorking && ["completed", "failed", "error"].contains(normalizedStatus)
            && (completionReason?.isEmpty != false || completionReason == "execution")
            && finalizedAgent == nil
            && finalizingAgent == nil
            && !["finalize", "recover_finalization", "discard"].contains(workerOperation ?? "")
            && !agents.isEmpty && agents.allSatisfy(\.isTerminal)
    }

    func isOlderProjection(than current: RunSummary) -> Bool {
        guard runId == current.runId else { return false }
        if eventCount != current.eventCount { return eventCount < current.eventCount }
        if updatedAt != current.updatedAt,
           let incomingDate = parseInternetDate(updatedAt),
           let currentDate = parseInternetDate(current.updatedAt), incomingDate != currentDate {
            return incomingDate < currentDate
        }
        return isWorking && !current.isWorking
    }

    var isReviewable: Bool {
        !isWorking
            && finalizedAgent == nil
            && normalizedStatus != "discarded"
            && agents.contains(where: \.mayAccept)
    }

    var hasUnresolvedFinalization: Bool {
        ["sync_ambiguous", "cleanup_failed"].contains(normalizedStatus)
    }

    var isStoppedWithoutResult: Bool {
        !isWorking && !isReviewable && !hasUnresolvedFinalization
            && finalizedAgent == nil && workerOperation != "finalize"
            && ["discarded", "cancelled", "canceled", "stopped", "interrupted"].contains(normalizedStatus)
    }

    var savedConversationSessionID: String? {
        guard isStoppedWithoutResult else { return nil }
        // This is the saved input conversation, not the cancelled candidate's private thread.
        return usableResumeSessionID(config.resumeSessionId ?? "")
    }

    var mayAddAgents: Bool { canAddAgents == true }
    var mayUpdateSynthesis: Bool { canUpdateSynthesis == true }

    mutating func reconcileTerminalAgentStates() {
        let terminalRunStates = [
            "completed", "failed", "error", "finalized", "discarded", "stopped",
            "cancelled", "canceled", "interrupted", "expired", "cleanup_failed",
            "sync_ambiguous",
        ]
        guard !isWorking, terminalRunStates.contains(normalizedStatus) else { return }
        let cancelledRunStates = [
            "discarded", "stopped", "cancelled", "canceled", "interrupted", "expired",
        ]
        let fallback = cancelledRunStates.contains(normalizedStatus) ? "cancelled" : "failed"
        for index in agents.indices where !agents[index].isTerminal {
            agents[index].status = fallback
        }
    }

    mutating func preserveTerminalAgentStates(from previous: RunSummary) {
        guard previous.runId == runId else { return }
        // A current working snapshot can start another attempt with the same
        // Agent id. Cached success must not mask a retry or synthesis refresh.
        if isWorking && eventCount >= previous.eventCount { return }
        let previousAgents = Dictionary(uniqueKeysWithValues: previous.agents.map { ($0.agent, $0) })
        for index in agents.indices {
            guard let old = previousAgents[agents[index].agent],
                  old.isTerminal,
                  !agents[index].isTerminal else { continue }
            agents[index].status = old.status
        }
    }
}

struct RunListResponse: Decodable, Sendable {
    let runs: [RunSummary]
    let total: Int
    let cursor: String?
    let nextCursor: String?
    let hasMore: Bool?
    let serializedBytes: Int?
    let byteLimit: Int?
}

struct RunEvent: Decodable, Identifiable, Hashable, Sendable {
    let eventId: Int
    let timestamp: String?
    let type: String
    let idx: Int?
    let stream: String?
    let text: String?
    let message: String?
    let status: String?
    let reasoningTokens: Int?

    var id: Int { eventId }
}

struct RunEventPage: Decodable, Sendable {
    let runId: String
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let events: [RunEvent]
    let total: Int
    let serializedBytes: Int?
    let byteLimit: Int?
    let oversizedEvent: Bool?
}

struct RemoteTranscriptItem: Decodable, Sendable {
    let id: String
    let eventId: Int
    let kind: String
    let text: String
    let status: String?
    let operationId: String?
    let activityType: String?
    var operation: WorkOperationRecord? = nil

    private enum CodingKeys: String, CodingKey {
        case id, eventId, kind, text, status, operationId, activityType, operation
    }

    init(id: String, eventId: Int, kind: String, text: String, status: String?,
         operationId: String?, activityType: String?, operation: WorkOperationRecord? = nil) {
        self.id = id
        self.eventId = eventId
        self.kind = kind
        self.text = text
        self.status = status
        self.operationId = operationId
        self.activityType = activityType
        self.operation = operation
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        eventId = try values.decode(Int.self, forKey: .eventId)
        kind = try values.decode(String.self, forKey: .kind)
        text = try values.decode(String.self, forKey: .text)
        status = try values.decodeIfPresent(String.self, forKey: .status)
        operationId = try values.decodeIfPresent(String.self, forKey: .operationId)
        activityType = try values.decodeIfPresent(String.self, forKey: .activityType)
        operation = try? values.decodeIfPresent(WorkOperationRecord.self, forKey: .operation)
    }
}

struct RemoteTranscriptPage: Decodable, Sendable {
    let schemaVersion: Int
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let total: Int
    let items: [RemoteTranscriptItem]
    let serializedBytes: Int?
    let byteLimit: Int?
    let oversizedEvent: Bool?
}

struct FinalMessagePage: Decodable, Sendable {
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let totalBytes: Int
    let messageId: String
    let text: String
}

struct RunAgentDetail: Decodable, Sendable {
    let agent: Int
    let name: String
    let status: String
    let seconds: Double?
    let reasoningTokens: Int?
    let reasoningTokenCounts: [String: Int]
    let codexThreadId: String?
    let error: String?
    let rejected: Bool
    let recommended: Bool
    let finalized: Bool
    let runId: String
    let stdoutTail: String
    let stderrTail: String
    let workspaceDir: String?
    let finalMessage: String
    let finalMessagePage: FinalMessagePage?
    let artifactError: String?
    var inputRequests: [AgentInputRequest]? = nil
    let eventPage: RunEventPage?
    let transcriptPage: RemoteTranscriptPage?
}

struct RunStorageEstimate: Codable, Equatable, Sendable {
    let totalBytes: Int?
    let total: String?
    let freeBytes: Int?
    let free: String?
    let fits: Bool?
    let confirmationRequired: Bool?
    var warningThresholdBytes: Int? = nil
    var warningThreshold: String? = nil
}

struct RunStorageEntry: Decodable, Identifiable, Sendable {
    let runId: String
    let status: String
    let workspace: String
    let workspaceName: String
    let createdAt: String
    let updatedAt: String
    let actualBytes: Int
    let actual: String
    let reclaimableBytes: Int
    let reclaimable: String
    let cleanupAction: String?
    let finalizedAgent: Int?
    let keepWorkspaces: Bool

    var id: String { runId }
}

struct StorageSummary: Decodable, Sendable {
    let managedBytes: Int
    let managed: String
    let reclaimableBytes: Int
    let reclaimable: String
    let unmanagedBytes: Int
    let unmanaged: String
    let unmanagedCount: Int
    let freeBytes: Int
    let free: String
    let volumeCount: Int?
    let runs: [RunStorageEntry]
    let totalRuns: Int
}

struct StorageCleanupBody: Encodable, Sendable {
    let runId: String
    let discardUnselected: Bool
    let waitSeconds: Int
}

struct RunStartResponse: Decodable, Sendable {
    let status: String
    let message: String?
    let runId: String?
    let storage: RunStorageEstimate?
    let run: RunSummary?
    let workspaceId: String?
    let workspaceRevision: Int?
    let continuedFrom: RunContinuationSource?
    var conversationSnapshot: ConversationSnapshot? = nil
}

struct RunContinuationSource: Decodable, Sendable {
    let runId: String?
    let agent: Int?
    let sessionId: String?
}

struct RunMutationResponse: Decodable, Sendable {
    let status: String
    let message: String?
    let storage: RunStorageEstimate?
    let run: RunSummary?
    let conversationSnapshot: ConversationSnapshot?

    private enum CodingKeys: String, CodingKey {
        case status, message, storage, run, agents, conversationSnapshot
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(String.self, forKey: .status)
        message = try container.decodeIfPresent(String.self, forKey: .message)
        storage = try container.decodeIfPresent(RunStorageEstimate.self, forKey: .storage)
        if container.contains(.agents) {
            run = try RunSummary(from: decoder)
        } else {
            run = try container.decodeIfPresent(RunSummary.self, forKey: .run)
        }
        conversationSnapshot = try container.decodeIfPresent(
            ConversationSnapshot.self,
            forKey: .conversationSnapshot
        ) ?? run?.conversationSnapshot
    }
}

struct RunDiffPage: Decodable, Sendable {
    let runId: String
    let agent: Int
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let totalChars: Int
    let text: String
    var snapshotId: String? = nil
    var live: Bool? = nil
}
