import Foundation

enum RunExecution: String, CaseIterable, Codable, Identifiable, Sendable {
    case parallel
    case serial

    var id: String { rawValue }
    var title: String { rawValue.capitalized }

    func title(language: AppLanguage) -> String {
        switch self {
        case .parallel: return language.text("并行", "Parallel")
        case .serial: return language.text("串行", "Serial")
        }
    }
}

enum RunRecommendation: String, CaseIterable, Codable, Identifiable, Sendable {
    case reasoningTokens = "reasoning_tokens"
    case duration

    var id: String { rawValue }
    var title: String {
        switch self {
        case .reasoningTokens: return "Reasoning tokens"
        case .duration: return "Duration"
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .reasoningTokens: return language.text("推理用量", "Reasoning tokens")
        case .duration: return language.text("运行时间", "Duration")
        }
    }
}

enum RunFastMode: String, CaseIterable, Codable, Identifiable, Sendable {
    case automatic
    case on
    case off

    var id: String { rawValue }
    var title: String {
        switch self {
        case .automatic: return "Automatic"
        case .on: return "On"
        case .off: return "Off"
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .automatic: return language.text("自动", "Automatic")
        case .on: return language.text("开启", "On")
        case .off: return language.text("关闭", "Off")
        }
    }

    var requestValue: Bool? {
        switch self {
        case .automatic: return nil
        case .on: return true
        case .off: return false
        }
    }

    init(_ value: Bool?) {
        switch value {
        case true: self = .on
        case false: self = .off
        case nil: self = .automatic
        }
    }
}

struct RunSettings: Codable, Equatable, Sendable {
    var agents = 4
    var synthesisAgents = 2
    var followUpDelay = 60
    var execution = RunExecution.parallel
    var automaticMaxParallel = true
    var maxParallel = 4
    var subagents = false
    var subagentsLimit = 8
    var recommendation = RunRecommendation.reasoningTokens
    var model = ""
    var effort = ""
    var fast = RunFastMode.automatic
    var synthesisModel = ""
    var synthesisEffort = ""
    var synthesisFast = RunFastMode.automatic
    var syncBack = true
    var keepWorkspaces = false
    var resumeSessionID = ""
    var resumeIncludeNonInteractive = false
    var runsDirectory = ""
    var codexExecutable = "codex"

    var displayedMaxParallel: Int {
        if execution == .serial { return 1 }
        return max(1, automaticMaxParallel ? agents : maxParallel)
    }

    init() {}

    private enum CodingKeys: String, CodingKey {
        case agents
        case synthesisAgents
        case followUpDelay
        case execution
        case automaticMaxParallel
        case maxParallel
        case subagents
        case subagentsLimit
        case recommendation
        case model
        case effort
        case fast
        case synthesisModel
        case synthesisEffort
        case synthesisFast
        case syncBack
        case keepWorkspaces
        case resumeSessionID
        case resumeIncludeNonInteractive
        case runsDirectory
        case codexExecutable
    }

    init(from decoder: Decoder) throws {
        self.init()
        let container = try decoder.container(keyedBy: CodingKeys.self)
        agents = try container.decodeIfPresent(Int.self, forKey: .agents) ?? agents
        synthesisAgents = try container.decodeIfPresent(Int.self, forKey: .synthesisAgents)
            ?? synthesisAgents
        followUpDelay = try container.decodeIfPresent(Int.self, forKey: .followUpDelay)
            ?? followUpDelay
        execution = try container.decodeIfPresent(RunExecution.self, forKey: .execution)
            ?? execution
        automaticMaxParallel = try container.decodeIfPresent(
            Bool.self,
            forKey: .automaticMaxParallel
        ) ?? automaticMaxParallel
        maxParallel = try container.decodeIfPresent(Int.self, forKey: .maxParallel)
            ?? maxParallel
        subagents = try container.decodeIfPresent(Bool.self, forKey: .subagents) ?? subagents
        subagentsLimit = try container.decodeIfPresent(Int.self, forKey: .subagentsLimit)
            ?? subagentsLimit
        recommendation = try container.decodeIfPresent(
            RunRecommendation.self,
            forKey: .recommendation
        ) ?? recommendation
        model = try container.decodeIfPresent(String.self, forKey: .model) ?? model
        effort = try container.decodeIfPresent(String.self, forKey: .effort) ?? effort
        fast = try container.decodeIfPresent(RunFastMode.self, forKey: .fast) ?? fast
        synthesisModel = try container.decodeIfPresent(String.self, forKey: .synthesisModel)
            ?? synthesisModel
        synthesisEffort = try container.decodeIfPresent(String.self, forKey: .synthesisEffort)
            ?? synthesisEffort
        synthesisFast = try container.decodeIfPresent(
            RunFastMode.self,
            forKey: .synthesisFast
        ) ?? synthesisFast
        syncBack = try container.decodeIfPresent(Bool.self, forKey: .syncBack) ?? syncBack
        keepWorkspaces = try container.decodeIfPresent(Bool.self, forKey: .keepWorkspaces)
            ?? keepWorkspaces
        resumeSessionID = try container.decodeIfPresent(String.self, forKey: .resumeSessionID)
            ?? resumeSessionID
        resumeIncludeNonInteractive = try container.decodeIfPresent(
            Bool.self,
            forKey: .resumeIncludeNonInteractive
        ) ?? resumeIncludeNonInteractive
        runsDirectory = try container.decodeIfPresent(String.self, forKey: .runsDirectory)
            ?? runsDirectory
        codexExecutable = try container.decodeIfPresent(String.self, forKey: .codexExecutable)
            ?? codexExecutable
    }

    mutating func apply(_ configuration: RunConfiguration) {
        if let value = configuration.numAgents { agents = value }
        if let value = configuration.synthesisAgents { synthesisAgents = value }
        if let value = configuration.followUpDelay { followUpDelay = value }
        execution = configuration.serial == true ? .serial : .parallel
        if let value = configuration.maxParallel {
            automaticMaxParallel = false
            maxParallel = value
        } else {
            automaticMaxParallel = true
            maxParallel = max(1, agents)
        }
        if let value = configuration.subagents { subagents = value }
        if let value = configuration.subagentsLimit { subagentsLimit = value }
        if let raw = configuration.recommendBy,
           let value = RunRecommendation(rawValue: raw) {
            recommendation = value
        }
        model = configuration.model ?? ""
        effort = configuration.effort ?? ""
        fast = RunFastMode(configuration.fast)
        synthesisModel = configuration.synthesisModel ?? ""
        synthesisEffort = configuration.synthesisEffort ?? ""
        synthesisFast = RunFastMode(configuration.synthesisFast)
        if let value = configuration.syncBack { syncBack = value }
        if let value = configuration.keepWorkspaces { keepWorkspaces = value }
        resumeSessionID = configuration.resumeSessionId ?? ""
        runsDirectory = configuration.runsDir ?? ""
        codexExecutable = configuration.codexBin ?? "codex"
    }

    mutating func apply(_ remote: RemoteRunSettings) {
        agents = max(1, remote.agents)
        synthesisAgents = max(0, remote.synthesisAgents)
        followUpDelay = max(0, remote.followUpDelay)
        execution = remote.execution
        automaticMaxParallel = remote.automaticMaxParallel
        maxParallel = max(1, remote.maxParallel)
        subagents = remote.subagents
        subagentsLimit = max(1, remote.subagentsLimit)
        if let recommendation = RunRecommendation(rawValue: remote.recommendBy) {
            self.recommendation = recommendation
        }
        model = remote.model
        effort = remote.effort
        fast = RunFastMode(rawValue: remote.fastMode) ?? .automatic
        synthesisModel = remote.synthesisModel
        synthesisEffort = remote.synthesisEffort
        synthesisFast = RunFastMode(rawValue: remote.synthesisFastMode) ?? .automatic
        syncBack = remote.syncBack
        keepWorkspaces = remote.keepWorkspaces
        resumeSessionID = remote.resumeSessionId
        resumeIncludeNonInteractive = remote.resumeIncludeNonInteractive ?? false
        runsDirectory = remote.runsDir ?? ""
        let remoteCodexBin = remote.codexBin ?? ""
        codexExecutable = remoteCodexBin.isEmpty ? "codex" : remoteCodexBin
    }

    func changedFields(from previous: RunSettings) -> Set<WorkspaceSettingField> {
        var fields = Set<WorkspaceSettingField>()
        if agents != previous.agents { fields.insert(.agents) }
        if synthesisAgents != previous.synthesisAgents { fields.insert(.synthesisAgents) }
        if followUpDelay != previous.followUpDelay { fields.insert(.followUpDelay) }
        if execution != previous.execution { fields.insert(.execution) }
        if automaticMaxParallel != previous.automaticMaxParallel
            || maxParallel != previous.maxParallel {
            fields.insert(.automaticMaxParallel)
            fields.insert(.maxParallel)
        }
        if subagents != previous.subagents { fields.insert(.subagents) }
        if subagentsLimit != previous.subagentsLimit { fields.insert(.subagentsLimit) }
        if recommendation != previous.recommendation { fields.insert(.recommendBy) }
        if model != previous.model { fields.insert(.model) }
        if effort != previous.effort { fields.insert(.effort) }
        if fast != previous.fast { fields.insert(.fastMode) }
        if synthesisModel != previous.synthesisModel { fields.insert(.synthesisModel) }
        if synthesisEffort != previous.synthesisEffort { fields.insert(.synthesisEffort) }
        if synthesisFast != previous.synthesisFast { fields.insert(.synthesisFastMode) }
        if syncBack != previous.syncBack { fields.insert(.syncBack) }
        if keepWorkspaces != previous.keepWorkspaces { fields.insert(.keepWorkspaces) }
        if resumeSessionID != previous.resumeSessionID { fields.insert(.resumeSessionID) }
        if resumeIncludeNonInteractive != previous.resumeIncludeNonInteractive {
            fields.insert(.resumeIncludeNonInteractive)
        }
        if runsDirectory != previous.runsDirectory { fields.insert(.runsDirectory) }
        if codexExecutable != previous.codexExecutable { fields.insert(.codexExecutable) }
        return fields
    }

    func merging(
        remote: RemoteRunSettings,
        preserving fields: Set<WorkspaceSettingField>
    ) -> RunSettings {
        let fields = WorkspaceSettingField.normalized(fields)
        var merged = RunSettings()
        merged.apply(remote)
        if fields.contains(.agents) { merged.agents = agents }
        if fields.contains(.synthesisAgents) { merged.synthesisAgents = synthesisAgents }
        if fields.contains(.followUpDelay) { merged.followUpDelay = followUpDelay }
        if fields.contains(.execution) { merged.execution = execution }
        if fields.contains(.automaticMaxParallel) {
            merged.automaticMaxParallel = automaticMaxParallel
        }
        if fields.contains(.maxParallel) { merged.maxParallel = maxParallel }
        if fields.contains(.subagents) { merged.subagents = subagents }
        if fields.contains(.subagentsLimit) { merged.subagentsLimit = subagentsLimit }
        if fields.contains(.recommendBy) { merged.recommendation = recommendation }
        if fields.contains(.model) { merged.model = model }
        if fields.contains(.effort) { merged.effort = effort }
        if fields.contains(.fastMode) { merged.fast = fast }
        if fields.contains(.synthesisModel) { merged.synthesisModel = synthesisModel }
        if fields.contains(.synthesisEffort) { merged.synthesisEffort = synthesisEffort }
        if fields.contains(.synthesisFastMode) { merged.synthesisFast = synthesisFast }
        if fields.contains(.syncBack) { merged.syncBack = syncBack }
        if fields.contains(.keepWorkspaces) { merged.keepWorkspaces = keepWorkspaces }
        if fields.contains(.resumeSessionID) { merged.resumeSessionID = resumeSessionID }
        if fields.contains(.resumeIncludeNonInteractive) {
            merged.resumeIncludeNonInteractive = resumeIncludeNonInteractive
        }
        if fields.contains(.runsDirectory) { merged.runsDirectory = runsDirectory }
        if fields.contains(.codexExecutable) { merged.codexExecutable = codexExecutable }
        return merged
    }

    func remoteUpdateBody(
        workspaceID: String,
        fields: Set<WorkspaceSettingField> = WorkspaceSettingField.all
    ) -> WorkspaceSettingsUpdateBody {
        let fields = WorkspaceSettingField.normalized(fields)
        return WorkspaceSettingsUpdateBody(
            workspaceId: workspaceID,
            agents: fields.contains(.agents) ? max(1, agents) : nil,
            synthesisAgents: fields.contains(.synthesisAgents) ? max(0, synthesisAgents) : nil,
            followUpDelay: fields.contains(.followUpDelay) ? max(0, followUpDelay) : nil,
            execution: fields.contains(.execution) ? execution.rawValue : nil,
            automaticMaxParallel: fields.contains(.automaticMaxParallel)
                ? automaticMaxParallel : nil,
            maxParallel: fields.contains(.maxParallel) ? max(1, maxParallel) : nil,
            subagents: fields.contains(.subagents) ? subagents : nil,
            subagentsLimit: fields.contains(.subagentsLimit) ? max(1, subagentsLimit) : nil,
            recommendBy: fields.contains(.recommendBy) ? recommendation.rawValue : nil,
            model: fields.contains(.model) ? model : nil,
            effort: fields.contains(.effort) ? effort : nil,
            fastMode: fields.contains(.fastMode) ? fast.rawValue : nil,
            synthesisModel: fields.contains(.synthesisModel) ? synthesisModel : nil,
            synthesisEffort: fields.contains(.synthesisEffort) ? synthesisEffort : nil,
            synthesisFastMode: fields.contains(.synthesisFastMode)
                ? synthesisFast.rawValue : nil,
            syncBack: fields.contains(.syncBack) ? syncBack : nil,
            keepWorkspaces: fields.contains(.keepWorkspaces) ? keepWorkspaces : nil,
            resumeSessionId: fields.contains(.resumeSessionID) ? resumeSessionID : nil,
            resumeIncludeNonInteractive: fields.contains(.resumeIncludeNonInteractive)
                ? resumeIncludeNonInteractive : nil,
            runsDir: fields.contains(.runsDirectory) ? runsDirectory : nil,
            codexBin: fields.contains(.codexExecutable)
                ? (codexExecutable.isEmpty ? "codex" : codexExecutable) : nil
        )
    }

    func defaultConversationUpdateBody(
        fields: Set<WorkspaceSettingField> = WorkspaceSettingField.defaultConversation
    ) -> DefaultConversationSettingsUpdateBody {
        let fields = WorkspaceSettingField.normalized(fields)
            .intersection(WorkspaceSettingField.defaultConversation)
        return DefaultConversationSettingsUpdateBody(
            agents: fields.contains(.agents) ? max(1, agents) : nil,
            synthesisAgents: fields.contains(.synthesisAgents)
                ? max(0, synthesisAgents) : nil,
            followUpDelay: fields.contains(.followUpDelay) ? max(0, followUpDelay) : nil,
            execution: fields.contains(.execution) ? execution.rawValue : nil,
            automaticMaxParallel: fields.contains(.automaticMaxParallel)
                ? automaticMaxParallel : nil,
            maxParallel: fields.contains(.maxParallel) ? max(1, maxParallel) : nil,
            subagents: fields.contains(.subagents) ? subagents : nil,
            subagentsLimit: fields.contains(.subagentsLimit) ? max(1, subagentsLimit) : nil,
            recommendBy: fields.contains(.recommendBy) ? recommendation.rawValue : nil,
            model: fields.contains(.model) ? model : nil,
            effort: fields.contains(.effort) ? effort : nil,
            fastMode: fields.contains(.fastMode) ? fast.rawValue : nil,
            synthesisModel: fields.contains(.synthesisModel) ? synthesisModel : nil,
            synthesisEffort: fields.contains(.synthesisEffort) ? synthesisEffort : nil,
            synthesisFastMode: fields.contains(.synthesisFastMode)
                ? synthesisFast.rawValue : nil,
            syncBack: fields.contains(.syncBack) ? syncBack : nil,
            keepWorkspaces: fields.contains(.keepWorkspaces) ? keepWorkspaces : nil
        )
    }
}

func resolvedWorkspaceSettings(
    cached: RunSettings?,
    remote: RemoteRunSettings?,
    preserving pendingFields: Set<WorkspaceSettingField>
) -> RunSettings {
    var resolved = cached ?? RunSettings()
    guard let remote, remote.configured else { return resolved }
    if let cached {
        resolved = cached.merging(remote: remote, preserving: pendingFields)
    } else {
        resolved.apply(remote)
    }
    return resolved
}

func usableResumeSessionID(_ value: String) -> String? {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty, normalized.uppercased() != "NO" else { return nil }
    return normalized
}

struct RunSubmissionCommand: Codable, Equatable, Sendable {
    let clientId: String
    let commandId: String
    let fingerprint: String
}

struct RunConfiguration: Codable, Equatable, Sendable {
    let numAgents: Int?
    let maxParallel: Int?
    let serial: Bool?
    let recommendBy: String?
    let model: String?
    let effort: String?
    let fast: Bool?
    let synthesisAgents: Int?
    let followUpDelay: Int?
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFast: Bool?
    let subagents: Bool?
    let subagentsLimit: Int?
    let resumeSessionId: String?
    let syncBack: Bool?
    let keepWorkspaces: Bool?
    let runsDir: String?
    let codexBin: String?
    var submissionCommand: RunSubmissionCommand? = nil
}

struct QueuedFollowUp: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    var prompt: String
    var settings: RunSettings?
    var attempt: Int?
    var dispatch: QueuedFollowUpDispatch?
    var failureCount: Int?
    var retryAt: Date?
    var lastError: String?
    var paused: Bool?
    var daemonAcknowledged: Bool?

    init(
        id: UUID = UUID(),
        prompt: String,
        settings: RunSettings? = nil,
        attempt: Int = 0
    ) {
        self.id = id
        self.prompt = prompt
        self.settings = settings
        self.attempt = attempt
        dispatch = nil
        failureCount = 0
        retryAt = nil
        lastError = nil
        paused = false
        daemonAcknowledged = nil
    }

    var commandID: String {
        "follow-up-\(id.uuidString.lowercased())-attempt-\(max(0, attempt ?? 0))"
    }

    var isPaused: Bool { paused == true }

    mutating func resetAfterEdit(settings: RunSettings) {
        self.settings = settings
        attempt = max(0, attempt ?? 0) + 1
        dispatch = nil
        failureCount = 0
        retryAt = nil
        lastError = nil
        paused = false
        daemonAcknowledged = false
    }

    mutating func retryManually() {
        attempt = max(0, attempt ?? 0) + 1
        failureCount = 0
        retryAt = nil
        lastError = nil
        paused = false
        daemonAcknowledged = false
    }

    var needsDaemonAcknowledgement: Bool { daemonAcknowledged != true }
}

struct QueuedFollowUpDispatch: Codable, Equatable, Sendable {
    enum Mode: String, Codable, Sendable {
        case start
        case continuation
    }

    let mode: Mode
    let workspaceID: String
    let runID: String?
    let agent: Int?
    let settings: RunSettings
    var machineID: String? = nil
}

enum FollowUpDispatchRoute: Equatable {
    case daemon
    case legacyClient
    case waitForCapabilities

    static func resolve(
        ownershipKnown: Bool,
        daemonOwnsQueue: Bool
    ) -> FollowUpDispatchRoute {
        if daemonOwnsQueue { return .daemon }
        return ownershipKnown ? .legacyClient : .waitForCapabilities
    }
}
