import Foundation

struct WorkspaceList: Decodable, Sendable {
    let revision: Int
    let selectedWorkspaceId: String?
    let workspaces: [RemoteWorkspace]
}

struct RemoteWorkspace: Decodable, Identifiable, Hashable, Sendable {
    let workspaceId: String
    let path: String
    let name: String
    let lastRunId: String
    let lastResumeSessionId: String
    let runStatus: String?
    let runWorkerActive: Bool?
    let runAgentsTotal: Int?
    let runAgentsFinished: Int?
    let runAgentsFailed: Int?
    let selected: Bool
    let available: Bool
    let readable: Bool
    let writable: Bool
    let settings: RemoteRunSettings?

    var id: String { workspaceId }

    func replacingRunSummary(with run: RunSummary, startingNewTurn: Bool = false) -> RemoteWorkspace {
        guard startingNewTurn || lastRunId == run.runId else { return self }
        return RemoteWorkspace(
            workspaceId: workspaceId,
            path: path,
            name: name,
            lastRunId: run.runId,
            lastResumeSessionId: lastResumeSessionId,
            runStatus: run.status,
            runWorkerActive: run.workerActive,
            runAgentsTotal: run.agents.count,
            runAgentsFinished: run.agents.count(where: \.isTerminal),
            runAgentsFailed: run.agents.count {
                $0.isTerminal && !$0.isSuccessful
            },
            selected: selected,
            available: available,
            readable: readable,
            writable: writable,
            settings: settings
        )
    }

    var removalLooksActive: Bool {
        runWorkerActive == true || [
            "preparing", "starting", "running", "stopping", "finalizing",
        ].contains((runStatus ?? "").lowercased())
    }

    var removalMayDiscardResults: Bool {
        guard !lastRunId.isEmpty, !removalLooksActive else { return false }
        let status = (runStatus ?? "").lowercased()
        if ["finalized", "discarded", "expired"].contains(status) { return false }
        if [
            "completed", "failed", "error", "stopped", "cancelled", "canceled",
            "interrupted",
        ].contains(status), summarizedSuccessfulAgentCount == 0 {
            return false
        }
        return true
    }

    var hasUnresolvedResultSelection: Bool {
        guard !lastRunId.isEmpty else { return false }
        let status = (runStatus ?? "").lowercased()
        if ["finalized", "discarded", "expired"].contains(status) {
            return false
        }
        if [
            "completed", "failed", "error", "stopped", "cancelled", "canceled",
            "interrupted",
        ].contains(status), let successful = summarizedSuccessfulAgentCount {
            return successful > 0
        }
        return true
    }

    var runStatusLabel: String? {
        runStatusLabel(language: .english)
    }

    func runStatusLabel(language: AppLanguage) -> String? {
        guard !lastRunId.isEmpty else { return nil }
        let status = (runStatus ?? "").lowercased()
        let total = runAgentsTotal ?? 0
        let finished = runAgentsFinished ?? 0
        let failed = runAgentsFailed ?? 0
        let successful = summarizedSuccessfulAgentCount ?? 0
        let progress = total > 0 ? " \(finished)/\(total)" : ""
        if ["preparing", "starting"].contains(status) {
            return language.text("排队中\(progress)", "Queued\(progress)")
        }
        if runWorkerActive == true || ["running", "stopping", "finalizing"].contains(status) {
            return language.text("运行中\(progress)", "Running\(progress)")
        }
        if [
            "completed", "failed", "error", "stopped", "cancelled", "canceled",
            "interrupted",
        ].contains(status), successful > 0 {
            return language.text(
                "等待选择 · \(successful) 个结果",
                successful == 1 ? "Ready to review · 1 result" : "Ready to review · \(successful) results"
            )
        }
        if ["cancelled", "canceled", "stopped"].contains(status) {
            return language.text("已停止", "Stopped")
        }
        if status == "expired" { return language.text("已过期", "Expired") }
        if ["failed", "error", "interrupted"].contains(status) {
            return language.text("失败", "Failed")
        }
        if ["sync_ambiguous", "cleanup_failed"].contains(status) {
            return language.text("需要处理", "Needs attention")
        }
        if status == "discarded" { return language.text("已放弃", "Discarded") }
        if ["completed", "finalized"].contains(status), total > 0, failed >= total {
            return language.text("失败", "Failed")
        }
        if ["completed", "finalized"].contains(status), failed > 0 {
            return language.text("已完成 · \(failed) 个失败", "Completed · \(failed) failed")
        }
        if ["completed", "finalized"].contains(status) {
            return language.text("已完成", "Completed")
        }
        return nil
    }

    private var summarizedSuccessfulAgentCount: Int? {
        guard let finished = runAgentsFinished, finished > 0, let failed = runAgentsFailed else {
            return nil
        }
        return max(0, finished - failed)
    }
}

struct RemoteRunSettings: Decodable, Hashable, Sendable {
    let configured: Bool
    let agents: Int
    let synthesisAgents: Int
    let followUpDelay: Int
    let execution: RunExecution
    let automaticMaxParallel: Bool
    let maxParallel: Int
    let subagents: Bool
    let subagentsLimit: Int
    let recommendBy: String
    let model: String
    let effort: String
    let fastMode: String
    let synthesisModel: String
    let synthesisEffort: String
    let synthesisFastMode: String
    let syncBack: Bool
    let keepWorkspaces: Bool
    let resumeSessionId: String
    let resumeIncludeNonInteractive: Bool?
    let runsDir: String?
    let codexBin: String?
}

struct DeviceList: Decodable, Sendable {
    let revision: Int
    let macEndpointId: String
    let devices: [RemoteDevice]
    let pendingPairings: Int
}

struct RemoteDevice: Decodable, Identifiable, Equatable, Sendable {
    let deviceId: String
    let name: String
    let kind: String
    let lastSeenAt: String
    let active: Bool

    var id: String { deviceId }
}

struct FileSystemRoots: Decodable, Sendable {
    let roots: [FileSystemRoot]
    let home: String
}

func resolvedDirectoryBrowserPath(_ input: String, current: String?, home: String?) -> String? {
    let path = input.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !path.isEmpty else { return nil }
    if path == "~" { return home }
    if path.hasPrefix("~/") {
        guard let home else { return nil }
        return home + String(path.dropFirst())
    }
    if path.hasPrefix("/") { return path }
    guard let base = current ?? home else { return nil }
    return base.hasSuffix("/") ? base + path : base + "/" + path
}

struct FileSystemRoot: Decodable, Identifiable, Sendable {
    let name: String
    let path: String
    let readable: Bool
    let writable: Bool
    let kind: String

    var id: String { path }
}

struct DirectoryPage: Decodable, Sendable {
    let name: String
    let path: String
    let parent: String?
    let readable: Bool
    let writable: Bool
    let isGitRepository: Bool
    let cursor: Int
    let nextCursor: Int
    let hasMore: Bool
    let total: Int
    let entries: [FileSystemEntry]
}

struct FileSystemEntry: Decodable, Identifiable, Sendable {
    let name: String
    let path: String
    let kind: String
    let isSymlink: Bool
    let readable: Bool
    let writable: Bool
    let hidden: Bool
    let modifiedAt: String?

    var id: String { path }
    var isDirectory: Bool { kind == "directory" }
}

struct ListDirectoryBody: Encodable, Sendable {
    let path: String
    let cursor: Int
    let limit: Int
    let includeHidden: Bool
    let directoriesOnly: Bool
    let sort: String
}

struct AddWorkspaceBody: Encodable, Sendable {
    let path: String
    let select: Bool
}

struct WorkspaceMutation: Decodable, Sendable {
    let revision: Int
    let workspace: RemoteWorkspace
}

struct SelectWorkspaceBody: Encodable, Sendable {
    let workspaceId: String
}

enum WorkspaceSettingField: String, CaseIterable, Codable, Hashable, Sendable {
    case agents
    case synthesisAgents = "synthesis_agents"
    case followUpDelay = "follow_up_delay"
    case execution
    case automaticMaxParallel = "automatic_max_parallel"
    case maxParallel = "max_parallel"
    case subagents
    case subagentsLimit = "subagents_limit"
    case recommendBy = "recommend_by"
    case model
    case effort
    case fastMode = "fast_mode"
    case synthesisModel = "synthesis_model"
    case synthesisEffort = "synthesis_effort"
    case synthesisFastMode = "synthesis_fast_mode"
    case syncBack = "sync_back"
    case keepWorkspaces = "keep_workspaces"
    case resumeSessionID = "resume_session_id"
    case resumeIncludeNonInteractive = "resume_include_non_interactive"
    case runsDirectory = "runs_dir"
    case codexExecutable = "codex_bin"

    static let all = Set(allCases)
    static let defaultConversation = all.subtracting([
        .resumeSessionID,
        .resumeIncludeNonInteractive,
        .runsDirectory,
        .codexExecutable,
    ])

    static func normalized(
        _ fields: Set<WorkspaceSettingField>
    ) -> Set<WorkspaceSettingField> {
        var normalized = fields
        if normalized.contains(.automaticMaxParallel) || normalized.contains(.maxParallel) {
            normalized.insert(.automaticMaxParallel)
            normalized.insert(.maxParallel)
        }
        return normalized
    }
}

struct DefaultSettingsContextState: Codable, Equatable, Sendable {
    var settings = RunSettings()
    var baseline: RunSettings?
    var dirtyFields: Set<WorkspaceSettingField> = []
    var loaded = false
    var needsSave = false
    var error: String?
}

struct WorkspaceSettingsUpdateBody: Encodable, Sendable {
    let workspaceId: String
    let agents: Int?
    let synthesisAgents: Int?
    let followUpDelay: Int?
    let execution: String?
    let automaticMaxParallel: Bool?
    let maxParallel: Int?
    let subagents: Bool?
    let subagentsLimit: Int?
    let recommendBy: String?
    let model: String?
    let effort: String?
    let fastMode: String?
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFastMode: String?
    let syncBack: Bool?
    let keepWorkspaces: Bool?
    let resumeSessionId: String?
    let resumeIncludeNonInteractive: Bool?
    let runsDir: String?
    let codexBin: String?
}

struct DefaultConversationSettingsUpdateBody: Encodable, Sendable {
    let agents: Int?
    let synthesisAgents: Int?
    let followUpDelay: Int?
    let execution: String?
    let automaticMaxParallel: Bool?
    let maxParallel: Int?
    let subagents: Bool?
    let subagentsLimit: Int?
    let recommendBy: String?
    let model: String?
    let effort: String?
    let fastMode: String?
    let synthesisModel: String?
    let synthesisEffort: String?
    let synthesisFastMode: String?
    let syncBack: Bool?
    let keepWorkspaces: Bool?
}

struct WorkspaceSettingsResponse: Decodable, Sendable {
    let workspaceId: String?
    let settings: RemoteRunSettings
}

struct WorkspaceRemoval: Decodable, Sendable {
    let revision: Int
    let removedWorkspaceId: String
    let selectedWorkspaceId: String?
}
