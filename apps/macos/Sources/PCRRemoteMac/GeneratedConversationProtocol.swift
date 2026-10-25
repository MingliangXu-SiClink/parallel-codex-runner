import Foundation

// Generated from remote/protocol/pcr-conversation-v1.schema.json. Do not edit.

let generatedConversationSchemaVersion = 1

enum WorkOperationKind: String, Codable, CaseIterable, Sendable {
    case fileRead = "file_read"
    case fileSearch = "file_search"
    case directoryList = "directory_list"
    case command
    case fileChange = "file_change"
    case webSearch = "web_search"
    case unknown
}

enum WorkOperationStatus: String, Codable, CaseIterable, Sendable {
    case running
    case completed
    case failed
    case cancelled
    case unknown
}

enum WorkFileChangeKind: String, Codable, CaseIterable, Sendable {
    case create
    case update
    case delete
    case rename
    case unknown
}

enum TurnPhase: String, Codable, CaseIterable, Sendable {
    case idle
    case preparing
    case primaryRunning = "primary_running"
    case synthesisRunning = "synthesis_running"
    case review
    case finalizing
    case finalized
    case stopped
    case failed
    case discarded
    case expired
    case cleanupFailed = "cleanup_failed"
    case syncAmbiguous = "sync_ambiguous"
    case unknown
}

enum CandidateRole: String, Codable, CaseIterable, Sendable {
    case candidate
    case synthesis
}

enum CandidateStatus: String, Codable, CaseIterable, Sendable {
    case queued
    case copying
    case starting
    case resuming
    case submitting
    case waitingForOutput = "waiting_for_output"
    case running
    case waitingForModels = "waiting_for_models"
    case stopping
    case success
    case failed
    case error
    case killed
    case stopped
    case cancelled
    case canceled
    case interrupted
    case discarded
    case unknown
}

struct WorkOperationTarget: Codable, Hashable, Sendable {
    let path: String
    let change: WorkFileChangeKind
    let added: Int?
    let deleted: Int?

    init(
        path: String = "",
        change: WorkFileChangeKind = .unknown,
        added: Int? = nil,
        deleted: Int? = nil
    ) {
        self.path = path
        self.change = change
        self.added = added
        self.deleted = deleted
    }
}

struct WorkOperationRecord: Codable, Hashable, Sendable {
    let id: String
    let eventId: Int
    let kind: WorkOperationKind
    let status: WorkOperationStatus
    let command: String?
    let targets: [WorkOperationTarget]
    let query: String?
    let durationMs: Int?
    let durationSource: String?
    let plannedDurationMs: Int?
    let waitUntil: String?
    let outputBytes: Int?
    let exitCode: Int?
    let detailsAvailable: Bool

    init(
        id: String = "",
        eventId: Int = 0,
        kind: WorkOperationKind = .unknown,
        status: WorkOperationStatus = .unknown,
        command: String? = nil,
        targets: [WorkOperationTarget] = [],
        query: String? = nil,
        durationMs: Int? = nil,
        durationSource: String? = nil,
        plannedDurationMs: Int? = nil,
        waitUntil: String? = nil,
        outputBytes: Int? = nil,
        exitCode: Int? = nil,
        detailsAvailable: Bool = false
    ) {
        self.id = id
        self.eventId = eventId
        self.kind = kind
        self.status = status
        self.command = command
        self.targets = targets
        self.query = query
        self.durationMs = durationMs
        self.durationSource = durationSource
        self.plannedDurationMs = plannedDurationMs
        self.waitUntil = waitUntil
        self.outputBytes = outputBytes
        self.exitCode = exitCode
        self.detailsAvailable = detailsAvailable
    }
}

struct WorkOperationDetailPage: Codable, Equatable, Sendable {
    let text: String
    let nextCursor: Int
    let hasMore: Bool
    let totalBytes: Int
    let availability: String

    init(
        text: String = "",
        nextCursor: Int = 0,
        hasMore: Bool = false,
        totalBytes: Int = 0,
        availability: String = ""
    ) {
        self.text = text
        self.nextCursor = nextCursor
        self.hasMore = hasMore
        self.totalBytes = totalBytes
        self.availability = availability
    }
}

struct AgentActivityRecord: Codable, Hashable, Sendable {
    let state: String
    let observedAt: String?
    let lastOutputAt: String?
    let waitUntil: String?
    let activeOperationCount: Int
    let observedAtDate: Date?
    let lastOutputAtDate: Date?
    let waitUntilDate: Date?

    init(
        state: String = "",
        observedAt: String? = nil,
        lastOutputAt: String? = nil,
        waitUntil: String? = nil,
        activeOperationCount: Int = 0
    ) {
        self.state = state
        self.observedAt = observedAt
        self.lastOutputAt = lastOutputAt
        self.waitUntil = waitUntil
        self.activeOperationCount = activeOperationCount
        self.observedAtDate = observedAt.flatMap(parseActivityDate)
        self.lastOutputAtDate = lastOutputAt.flatMap(parseActivityDate)
        self.waitUntilDate = waitUntil.flatMap(parseActivityDate)
    }

    private enum CodingKeys: String, CodingKey {
        case state
        case observedAt
        case lastOutputAt
        case waitUntil
        case activeOperationCount
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            state: try values.decode(String.self, forKey: .state),
            observedAt: try values.decodeIfPresent(String.self, forKey: .observedAt),
            lastOutputAt: try values.decodeIfPresent(String.self, forKey: .lastOutputAt),
            waitUntil: try values.decodeIfPresent(String.self, forKey: .waitUntil),
            activeOperationCount: try values.decode(Int.self, forKey: .activeOperationCount)
        )
    }
}

struct RunSettingsRecord: Codable, Equatable, Sendable {
    let numAgents: Int
    let synthesisAgents: Int
    let maxParallel: Int?
    let serial: Bool
    let recommendBy: String
    let model: String?
    let effort: String?
    let fast: Bool?
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFast: Bool?
    let followUpDelay: Int
    let subagents: Bool
    let subagentsLimit: Int
    let syncBack: Bool
    let keepWorkspaces: Bool
    let resumeSessionId: String?
    let runsDir: String?
    let codexBin: String

    init(
        numAgents: Int = 0,
        synthesisAgents: Int = 0,
        maxParallel: Int? = nil,
        serial: Bool = false,
        recommendBy: String = "",
        model: String? = nil,
        effort: String? = nil,
        fast: Bool? = nil,
        synthesisModel: String? = nil,
        synthesisEffort: String? = nil,
        synthesisFast: Bool? = nil,
        followUpDelay: Int = 0,
        subagents: Bool = false,
        subagentsLimit: Int = 0,
        syncBack: Bool = false,
        keepWorkspaces: Bool = false,
        resumeSessionId: String? = nil,
        runsDir: String? = nil,
        codexBin: String = ""
    ) {
        self.numAgents = numAgents
        self.synthesisAgents = synthesisAgents
        self.maxParallel = maxParallel
        self.serial = serial
        self.recommendBy = recommendBy
        self.model = model
        self.effort = effort
        self.fast = fast
        self.synthesisModel = synthesisModel
        self.synthesisEffort = synthesisEffort
        self.synthesisFast = synthesisFast
        self.followUpDelay = followUpDelay
        self.subagents = subagents
        self.subagentsLimit = subagentsLimit
        self.syncBack = syncBack
        self.keepWorkspaces = keepWorkspaces
        self.resumeSessionId = resumeSessionId
        self.runsDir = runsDir
        self.codexBin = codexBin
    }
}

struct MachineRecord: Codable, Equatable, Sendable {
    let machineId: String
    let name: String
    let platform: String
    let online: Bool
    let updatedAt: String

    init(
        machineId: String = "",
        name: String = "",
        platform: String = "",
        online: Bool = false,
        updatedAt: String = ""
    ) {
        self.machineId = machineId
        self.name = name
        self.platform = platform
        self.online = online
        self.updatedAt = updatedAt
    }
}

struct WorkspaceRecord: Codable, Equatable, Sendable {
    let workspaceId: String
    let machineId: String
    let name: String
    let path: String
    let available: Bool
    let updatedAt: String

    init(
        workspaceId: String = "",
        machineId: String = "",
        name: String = "",
        path: String = "",
        available: Bool = false,
        updatedAt: String = ""
    ) {
        self.workspaceId = workspaceId
        self.machineId = machineId
        self.name = name
        self.path = path
        self.available = available
        self.updatedAt = updatedAt
    }
}

struct ConversationRecord: Codable, Equatable, Sendable {
    let conversationId: String
    let workspaceId: String
    let title: String
    let customTitle: String?
    let titleRevision: Int?
    let createdAt: String
    let updatedAt: String
    let activeTurnId: String?
    let resumeSessionId: String?
    let turnIds: [String]

    init(
        conversationId: String = "",
        workspaceId: String = "",
        title: String = "",
        customTitle: String? = nil,
        titleRevision: Int? = nil,
        createdAt: String = "",
        updatedAt: String = "",
        activeTurnId: String? = nil,
        resumeSessionId: String? = nil,
        turnIds: [String] = []
    ) {
        self.conversationId = conversationId
        self.workspaceId = workspaceId
        self.title = title
        self.customTitle = customTitle
        self.titleRevision = titleRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.activeTurnId = activeTurnId
        self.resumeSessionId = resumeSessionId
        self.turnIds = turnIds
    }
}

struct TurnRecord: Codable, Equatable, Sendable {
    let turnId: String
    let conversationId: String
    let runId: String
    let turnIndex: Int
    let prompt: String
    let phase: TurnPhase
    let settings: RunSettingsRecord
    let createdAt: String
    let updatedAt: String
    let continuedFromTurnId: String?
    let continuedFromCandidateId: String?
    let recommendedCandidateId: String?
    let finalizedCandidateId: String?
    let candidateIds: [String]

    init(
        turnId: String = "",
        conversationId: String = "",
        runId: String = "",
        turnIndex: Int = 0,
        prompt: String = "",
        phase: TurnPhase = .unknown,
        settings: RunSettingsRecord = RunSettingsRecord(),
        createdAt: String = "",
        updatedAt: String = "",
        continuedFromTurnId: String? = nil,
        continuedFromCandidateId: String? = nil,
        recommendedCandidateId: String? = nil,
        finalizedCandidateId: String? = nil,
        candidateIds: [String] = []
    ) {
        self.turnId = turnId
        self.conversationId = conversationId
        self.runId = runId
        self.turnIndex = turnIndex
        self.prompt = prompt
        self.phase = phase
        self.settings = settings
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.continuedFromTurnId = continuedFromTurnId
        self.continuedFromCandidateId = continuedFromCandidateId
        self.recommendedCandidateId = recommendedCandidateId
        self.finalizedCandidateId = finalizedCandidateId
        self.candidateIds = candidateIds
    }
}

struct AgentCandidateRecord: Codable, Equatable, Sendable {
    let candidateId: String
    let turnId: String
    let agentIndex: Int
    let displayName: String
    let role: CandidateRole
    let status: CandidateStatus
    let seconds: Double?
    let startedAt: String?
    let finishedAt: String?
    let activity: AgentActivityRecord?
    let reasoningTokens: Int64?
    let reasoningTokenCounts: [String: Int]
    let codexThreadId: String?
    let error: String?
    let rejected: Bool
    let stale: Bool
    let recommended: Bool
    let finalized: Bool
    let workspaceDir: String?
    let workspaceAvailable: Bool
    let canAccept: Bool
    let canContinue: Bool
    let canRetry: Bool

    init(
        candidateId: String = "",
        turnId: String = "",
        agentIndex: Int = 0,
        displayName: String = "",
        role: CandidateRole = .candidate,
        status: CandidateStatus = .unknown,
        seconds: Double? = nil,
        startedAt: String? = nil,
        finishedAt: String? = nil,
        activity: AgentActivityRecord? = nil,
        reasoningTokens: Int64? = nil,
        reasoningTokenCounts: [String: Int] = [:],
        codexThreadId: String? = nil,
        error: String? = nil,
        rejected: Bool = false,
        stale: Bool = false,
        recommended: Bool = false,
        finalized: Bool = false,
        workspaceDir: String? = nil,
        workspaceAvailable: Bool = false,
        canAccept: Bool = false,
        canContinue: Bool = false,
        canRetry: Bool = false
    ) {
        self.candidateId = candidateId
        self.turnId = turnId
        self.agentIndex = agentIndex
        self.displayName = displayName
        self.role = role
        self.status = status
        self.seconds = seconds
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.activity = activity
        self.reasoningTokens = reasoningTokens
        self.reasoningTokenCounts = reasoningTokenCounts
        self.codexThreadId = codexThreadId
        self.error = error
        self.rejected = rejected
        self.stale = stale
        self.recommended = recommended
        self.finalized = finalized
        self.workspaceDir = workspaceDir
        self.workspaceAvailable = workspaceAvailable
        self.canAccept = canAccept
        self.canContinue = canContinue
        self.canRetry = canRetry
    }
}

struct ConversationSnapshot: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let machine: MachineRecord
    let workspace: WorkspaceRecord
    let conversation: ConversationRecord
    let turn: TurnRecord
    let candidates: [AgentCandidateRecord]

    init(
        schemaVersion: Int = 0,
        machine: MachineRecord = MachineRecord(),
        workspace: WorkspaceRecord = WorkspaceRecord(),
        conversation: ConversationRecord = ConversationRecord(),
        turn: TurnRecord = TurnRecord(),
        candidates: [AgentCandidateRecord] = []
    ) {
        self.schemaVersion = schemaVersion
        self.machine = machine
        self.workspace = workspace
        self.conversation = conversation
        self.turn = turn
        self.candidates = candidates
    }
}
