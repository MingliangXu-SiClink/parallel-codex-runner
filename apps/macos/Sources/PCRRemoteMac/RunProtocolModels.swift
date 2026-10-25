import Foundation

struct TranscriptLinkOrigin: Codable, Hashable, Sendable {
    let runID: String
    let agent: Int
}

struct CodexOption: Decodable, Identifiable, Hashable, Sendable {
    let label: String
    let value: String
    let selected: Bool

    var id: String { value }
}

func codexOptionChoices(
    dynamic: [CodexOption]?,
    current: String,
    fallbackLabel: String
) -> [CodexOption] {
    var result = [
        CodexOption(label: fallbackLabel, value: "", selected: current.isEmpty)
    ]
    for option in dynamic ?? [] where !result.contains(where: { $0.value == option.value }) {
        result.append(option)
    }
    if !current.isEmpty, !result.contains(where: { $0.value == current }) {
        result.append(CodexOption(label: current, value: current, selected: true))
    }
    return result
}

func localizedDefaultModelLabel(
    _ effectiveModel: String?,
    language: AppLanguage
) -> String {
    let effective = effectiveModel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return effective.isEmpty
        ? language.text("默认", "Default")
        : language.text("默认（\(effective)）", "Default (\(effective))")
}

func localizedCodexEffortLabel(_ value: String, language: AppLanguage) -> String {
    switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "none": language.text("无", "None")
    case "minimal": language.text("最低", "Minimal")
    case "low": language.text("低", "Low")
    case "medium": language.text("中", "Medium")
    case "high": language.text("高", "High")
    case "xhigh": language.text("极高", "Extra high")
    case "max": language.text("最高", "Maximum")
    case "ultra": language.text("超强", "Ultra")
    default: value
    }
}

func codexEffortChoices(
    dynamic: [CodexOption]?,
    current: String,
    effective: String?,
    language: AppLanguage
) -> [CodexOption] {
    codexOptionChoices(
        dynamic: dynamic,
        current: current,
        fallbackLabel: localizedAutomaticCodexLabel(effective, language: language)
    ).map {
        CodexOption(
            label: localizedCodexEffortLabel($0.label, language: language),
            value: $0.value,
            selected: $0.selected
        )
    }
}

func localizedAutomaticCodexLabel(
    _ rawValue: String?,
    language: AppLanguage
) -> String {
    let raw = rawValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard !raw.isEmpty else { return language.text("自动", "Automatic") }

    let opening = raw.firstIndex(of: "(")
    let closing = raw.lastIndex(of: ")")
    let effective: String
    if let opening, let closing, opening < closing {
        effective = String(raw[raw.index(after: opening)..<closing])
            .trimmingCharacters(in: .whitespacesAndNewlines)
    } else {
        effective = raw
    }

    let localized = switch effective.uppercased() {
    case "AUTO": language.text("自动", "Automatic")
    case "FAST", "YES", "ON": language.text("开启", "On")
    case "STANDARD", "NO", "OFF": language.text("关闭", "Off")
    default: localizedCodexEffortLabel(effective, language: language)
    }
    let automatic = language.text("自动", "Automatic")
    guard !localized.isEmpty, localized != automatic else { return automatic }
    return language.text("自动（\(localized)）", "Automatic (\(localized))")
}

struct CodexModelOptions: Decodable, Sendable {
    let configuredModel: String?
    let configuredEffort: String?
    let configuredServiceTier: String?
    let effectiveModel: String?
    let effectiveEffort: String?
    let effectiveFast: String?
    let models: [CodexOption]
    let efforts: [CodexOption]
}

struct ResumeSessionList: Decodable, Sendable {
    let workspace: String
    let sessions: [ResumeSession]
    let total: Int
}

struct ResumeSession: Decodable, Identifiable, Hashable, Sendable {
    let sessionId: String
    let title: String
    let cwd: String
    let updatedAt: Int?
    let createdAt: Int?
    let source: String
    let model: String
    let rolloutPath: String
    let preview: String
    let tokensUsed: Int?

    var id: String { sessionId }
    var displayTitle: String {
        let value = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty { return value }
        let previewValue = preview.trimmingCharacters(in: .whitespacesAndNewlines)
        return previewValue.isEmpty ? "Previous conversation" : previewValue
    }
}

struct ResumeHistoryPage: Decodable, Sendable {
    let workspace: String
    let session: ResumeSession?
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let totalEntries: Int
    let entries: [ResumeHistoryEntry]
}

struct ResumeHistoryEntry: Codable, Hashable, Sendable {
    let category: String
    let text: String
    var id: String = ""
    var turnId: String = ""
    var linkOrigin: TranscriptLinkOrigin? = nil
    var question: AgentQuestionPresentation? = nil
    var questions: [AgentQuestionPresentation]? = nil
}

struct GetRunBody: Encodable, Sendable {
    let runId: String
    let includeEvents: Bool
    let cursor: Int
    let eventLimit: Int
    let eventMaxBytes: Int = 512 * 1024
    var workspaceId: String? = nil
}

struct ListRunsBody: Encodable, Sendable {
    let limit: Int
    let cursor: String?
    let maxBytes: Int
    let workspace: String?

    init(
        limit: Int,
        cursor: String? = nil,
        maxBytes: Int = 512 * 1024,
        workspace: String? = nil
    ) {
        self.limit = limit
        self.cursor = cursor
        self.maxBytes = maxBytes
        self.workspace = workspace
    }
}

struct GetAgentBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let includeDiff: Bool
    let includeEvents: Bool
    let cursor: Int
    let eventLimit: Int
    let eventMaxBytes: Int = 512 * 1024
    let transcriptItemsOnly: Bool
    let conversationItemsOnly: Bool
    let finalMessageCursor: Int
    let finalMessageMaxBytes: Int = 192 * 1024
}

struct RunStartBody: Encodable, Sendable {
    let workspaceId: String
    let prompt: String
    let numAgents: Int
    let maxParallel: Int?
    let serial: Bool
    let recommendBy: String
    let model: String?
    let effort: String?
    let resumeSessionId: String?
    let syncBack: Bool
    let keepWorkspaces: Bool
    let confirmLargeRun: Bool
    let fast: Bool?
    let synthesisAgents: Int
    let followUpDelay: Int
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFast: Bool?
    let subagents: Bool
    let subagentsLimit: Int
    let runsDir: String?
    let codexBin: String
}

struct PromptCommandReceiptBody<Body: Encodable & Sendable>: Encodable, Sendable {
    let commandId: String
    let action: String
    let body: Body
}

struct PromptCommandReceipt: Decodable, Sendable {
    let commandId: String
    let status: String
    let result: RunStartResponse?
    let error: PromptCommandReceiptError?
    let receiptStoreId: String?
    let recoveryOwnedByDaemon: Bool?
}

struct PromptCommandReceiptError: Decodable, Sendable {
    let code: String?
    let message: String?
}

struct RunContinueBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let prompt: String
    let numAgents: Int
    let maxParallel: Int?
    let automaticMaxParallel: Bool
    let serial: Bool
    let recommendBy: String
    let model: String
    let effort: String
    let fastMode: String
    let synthesisAgents: Int
    let followUpDelay: Int
    let synthesisModel: String
    let synthesisEffort: String
    let synthesisFastMode: String
    let syncBack: Bool
    let keepWorkspaces: Bool
    let subagents: Bool
    let subagentsLimit: Int
    let runsDir: String
    let codexBin: String
    let confirmLargeRun: Bool
}

struct RunStopBody: Encodable, Sendable {
    let runId: String
    let waitSeconds: Double
}

struct RunDiscardBody: Encodable, Sendable {
    let runId: String
    let keepWorkspaces: Bool
    let waitSeconds: Double
}

struct RecoverFinalizationBody: Encodable, Sendable {
    let runId: String
    let syncWasApplied: Bool
}

struct AgentActionBody: Encodable, Sendable {
    let runId: String
    let agent: Int
}

struct AcceptAgentBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let waitSeconds: Double
    let keepWorkspaces: Bool
    let syncBack: Bool
}

struct RejectAgentBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let rejected: Bool
}

struct AddAgentsBody: Encodable, Sendable {
    let runId: String
    let count: Int
    let confirmLargeRun: Bool
}

struct UpdateSynthesisBody: Encodable, Sendable {
    let runId: String
    let synthesisAgents: Int
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFast: Bool?
}

struct DiffBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let cursor: Int
    let limit: Int
    var fullContext = false
    var snapshotId: String? = nil
}

struct ModelOptionsBody: Encodable, Sendable {
    let model: String?
    let effort: String?
}

struct ResumeSessionsBody: Encodable, Sendable {
    let workspaceId: String
    let includeNonInteractive: Bool
    let limit: Int
}

struct ResumeHistoryBody: Encodable, Sendable {
    let workspaceId: String
    let sessionId: String
    let cursor: Int
    let limit: Int
    let maxBytes: Int
    let conversationItemsOnly: Bool
}

struct RunHistoryBody: Encodable, Sendable {
    let runId: String
    let cursor: Int
    let limit: Int
    let maxBytes: Int
    let conversationItemsOnly: Bool
}

struct PromptHistoryListBody: Encodable, Sendable {
    let workspaceId: String
    let sessionId: String?
}

struct PromptHistoryAppendBody: Encodable, Sendable {
    let workspaceId: String
    let sessionId: String?
    let prompt: String
}

struct PromptHistoryResponse: Decodable, Sendable {
    let entries: [String]
}
