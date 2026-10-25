import Foundation

func localizedConversationTitle(_ title: String, language: AppLanguage) -> String {
    title == "Previous conversation"
        ? language.text("之前的对话", "Previous conversation")
        : title
}

func conversationTitleAccessibilityLabel(title: String, status: String?) -> String {
    let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
    let status = status?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    switch (title.isEmpty, status.isEmpty) {
    case (false, false): return "\(title), \(status)"
    case (false, true): return title
    case (true, false): return status
    case (true, true): return ""
    }
}

enum RunOperationKind: Equatable, Sendable {
    case stopTurn
    case discardTurn
    case recoverFinalization
    case retryCleanup
    case stopAgent(Int)
    case retryAgent(Int)
    case updateRecommendation(Int)
    case useResult(Int)
    case addAgents
}

enum ConversationBranchPrimaryAction: Equatable, Sendable {
    case progress(String)
    case resolveSync
    case retryCleanup
    case used
    case previewUsed(Int)
    case enableSyncBack
    case useResult(stopsOthers: Bool)
    case stopAgent
    case retryAgent
    case none
}

func conversationBranchPrimaryAction(
    run: RunSummary,
    agent: RunAgentSummary,
    isLatestWorkspaceRun: Bool,
    syncBack: Bool,
    operation: RunOperationKind?,
    hasPendingSubmission: Bool = false
) -> ConversationBranchPrimaryAction {
    // A submitted continuation owns the branch until its handoff completes.
    guard !hasPendingSubmission else { return .none }
    switch operation {
    case .useResult(let value) where value == agent.agent:
        return .progress("Using Result")
    case .stopAgent(let value) where value == agent.agent:
        return .progress("Stopping Agent")
    case .retryAgent(let value) where value == agent.agent:
        return .progress("Retrying Agent")
    case .updateRecommendation(let value) where value == agent.agent:
        return .progress(agent.rejected ? "Including Result" : "Excluding Result")
    case .recoverFinalization:
        return .progress("Resolving Sync")
    case .retryCleanup:
        return .progress("Cleaning Temporary Files")
    default:
        break
    }

    if isLatestWorkspaceRun,
       run.normalizedStatus == "sync_ambiguous",
       run.finalizedAgent != nil {
        return .resolveSync
    }
    if isLatestWorkspaceRun,
       run.normalizedStatus == "cleanup_failed",
       run.finalizedAgent != nil {
        return .retryCleanup
    }
    if run.normalizedStatus == "finalizing",
       run.finalizedAgent == agent.agent {
        return .progress("Applying Result")
    }
    if run.normalizedStatus == "finalized" {
        if agent.finalized { return .used }
        if let finalized = run.finalizedAgent { return .previewUsed(finalized) }
        return .none
    }
    guard isLatestWorkspaceRun else { return .none }
    if agent.mayAccept {
        guard syncBack else { return .enableSyncBack }
        return .useResult(stopsOthers: run.isWorking)
    }
    if agent.canStop { return .stopAgent }
    if agent.mayRetry { return .retryAgent }
    return .none
}

enum ConversationTone: Equatable, Sendable {
    case neutral
    case accent
    case success
    case warning
    case failure
}

enum ConversationRunPhase: Equatable, Sendable {
    case preparing
    case primary(completed: Int, total: Int)
    case synthesis(completed: Int, total: Int)
    case stopping
    case finishing
    case finalizing
    case review
    case finalized
    case syncAttention
    case cleanupAttention
    case discarded
    case expired
    case stopped
    case failed
    case finished
}

struct ConversationRunPresentation: Equatable, Sendable {
    let phase: ConversationRunPhase
    let label: String
    let systemImage: String
    let tone: ConversationTone
    let animated: Bool

    var navigationLabel: String {
        navigationLabel(language: .english)
    }

    func navigationLabel(language: AppLanguage) -> String {
        switch phase {
        case .primary(let completed, let total):
            return language.text(
                total > 0 ? "运行中 \(completed)/\(total)" : "运行中",
                total > 0 ? "Working \(completed)/\(total)" : "Working"
            )
        case .synthesis(let completed, let total):
            return language.text(
                total > 0 ? "综合中 \(completed)/\(total)" : "综合中",
                total > 0 ? "Synthesizing \(completed)/\(total)" : "Synthesizing"
            )
        case .preparing: return language.text("准备中", "Preparing")
        case .stopping: return language.text("正在停止", "Stopping")
        case .finishing: return language.text("正在收尾", "Finishing")
        case .finalizing: return language.text("正在采用结果", "Applying result")
        case .review: return language.text("等待选择", "Review")
        case .finalized: return language.text("已采用结果", "Result used")
        case .syncAttention: return language.text("同步需要处理", "Sync needs attention")
        case .cleanupAttention: return language.text("清理需要处理", "Cleanup needs attention")
        case .discarded: return language.text("已放弃", "Discarded")
        case .expired: return language.text("已过期", "Expired")
        case .stopped: return language.text("已停止", "Stopped")
        case .failed: return language.text("失败", "Failed")
        case .finished: return language.text("已结束", "Finished")
        }
    }

    static func make(run: RunSummary) -> Self {
        make(run: run, language: .english)
    }

    static func make(run: RunSummary, language: AppLanguage) -> Self {
        let normalized = run.normalizedStatus
        if run.isWorking {
            switch normalized {
            case "preparing":
                return value(
                    .preparing,
                    language.text("正在准备工作区", "Preparing workspaces"),
                    "shippingbox", .accent, true
                )
            case "stopping":
                return value(
                    .stopping,
                    language.text("正在停止任务", "Stopping task"),
                    "stop.circle", .warning, true
                )
            case "finalizing":
                return value(
                    .finalizing,
                    language.text("正在采用所选结果", "Applying selected result"),
                    "arrow.triangle.merge", .accent, true
                )
            default:
                break
            }

            if run.agents.isEmpty {
                return value(
                    .preparing,
                    language.text("正在启动 Agent", "Starting Agents"),
                    "person.2", .accent, true
                )
            }

            let primary = run.agents.filter { !$0.isSynthesis }
            let synthesis = run.agents.filter(\.isSynthesis)
            let expectedSynthesis = run.config.synthesisAgents ?? 0
            if !synthesis.isEmpty,
               !primary.isEmpty,
               primary.allSatisfy(\.isTerminal),
               !synthesis.allSatisfy(\.isTerminal) {
                let completed = synthesis.filter(\.isTerminal).count
                return value(
                    .synthesis(completed: completed, total: synthesis.count),
                    progressLabel(
                        language.text("正在综合", "Synthesizing"),
                        completed: completed,
                        total: synthesis.count
                    ),
                    "wand.and.stars",
                    .accent,
                    true
                )
            }

            if !primary.isEmpty,
               primary.allSatisfy(\.isTerminal),
               synthesis.isEmpty,
               expectedSynthesis > 0 {
                return value(
                    .synthesis(completed: 0, total: expectedSynthesis),
                    language.text("正在准备综合 Agent", "Preparing synthesis"),
                    "wand.and.stars",
                    .accent,
                    true
                )
            }

            if run.agents.allSatisfy(\.isTerminal) {
                return value(
                    .finishing,
                    language.text("正在完成任务", "Finishing task"),
                    "checkmark.circle",
                    .accent,
                    true
                )
            }

            let candidates = primary.isEmpty ? run.agents : primary
            let completed = candidates.filter(\.isTerminal).count
            return value(
                .primary(completed: completed, total: candidates.count),
                progressLabel(
                    language.text("Agent 正在工作", "Agents working"),
                    completed: completed,
                    total: candidates.count
                ),
                "person.2.wave.2",
                .accent,
                true
            )
        }

        switch normalized {
        case "discarded":
            return value(
                .discarded,
                language.text("已放弃结果", "Result discarded"),
                "archivebox", .neutral, false
            )
        case "expired":
            return value(
                .expired,
                language.text("结果已过期", "Result expired"),
                "clock", .neutral, false
            )
        case "finalized":
            return value(
                .finalized,
                language.text("已采用结果", "Result used"),
                "checkmark.seal.fill", .success, false
            )
        case "sync_ambiguous":
            return value(
                .syncAttention,
                language.text("请确认工作区同步状态", "Confirm workspace sync"),
                "exclamationmark.triangle.fill",
                .warning,
                false
            )
        case "cleanup_failed":
            return value(
                .cleanupAttention,
                language.text("清理需要处理", "Cleanup needs attention"),
                "exclamationmark.triangle.fill",
                .warning,
                false
            )
        default:
            break
        }

        if run.isReviewable {
            let successful = run.agents.filter(\.mayAccept).count
            let failed = run.agents.filter {
                $0.isTerminal && !$0.isSuccessful
            }.count
            let unavailable = run.agents.filter {
                $0.isSuccessful && !$0.mayAccept
            }.count
            let label: String
            if unavailable > 0 {
                label = language.text(
                    "等待选择 · \(successful) 个可用 · \(unavailable) 个不可用",
                    "Ready to review · \(successful) usable · \(unavailable) unavailable"
                )
            } else if failed > 0 {
                label = language.text(
                    "等待选择 · \(successful) 个可用 · \(failed) 个失败",
                    "Ready to review · \(successful) usable · \(failed) failed"
                )
            } else {
                label = language.text(
                    "等待选择 · \(successful) 个结果",
                    successful == 1
                        ? "Ready to review · 1 result"
                        : "Ready to review · \(successful) results"
                )
            }
            return value(.review, label, "checkmark.circle.fill", .success, false)
        }

        let completedResults = run.agents.filter(\.isSuccessful)
        if run.finalizedAgent == nil,
           !completedResults.isEmpty,
           !run.agents.contains(where: \.mayAccept) {
            return value(
                .failed,
                language.text("结果不可用", "Results unavailable"),
                "exclamationmark.circle.fill",
                .warning,
                false
            )
        }

        switch normalized {
        case "failed", "error":
            return value(
                .failed,
                language.text("任务失败", "Task failed"),
                "xmark.circle.fill", .failure, false
            )
        case "stopped", "cancelled", "canceled", "interrupted":
            return value(
                .stopped,
                language.text("任务已停止", "Task stopped"),
                "stop.circle.fill", .warning, false
            )
        default:
            break
        }

        let terminal = run.agents.filter(\.isTerminal).count
        let failed = run.agents.filter {
            ["failed", "error"].contains($0.normalizedStatus)
        }.count
        if terminal == run.agents.count, terminal > 0 {
            let successful = run.agents.filter(\.isSuccessful).count
            if successful == 0 {
                return value(
                    failed > 0 ? .failed : .stopped,
                    language.text("没有可用结果", "No usable results"),
                    failed > 0 ? "xmark.circle.fill" : "stop.circle.fill",
                    failed > 0 ? .failure : .warning,
                    false
                )
            }
        }
        if failed > 0 {
            return value(
                .finished,
                language.text("已结束 · \(failed) 个失败", "Finished with \(failed) failed"),
                "exclamationmark.circle.fill",
                .warning,
                false
            )
        }
        return value(
            .finished,
            language.text("任务已结束", "Task finished"),
            "checkmark.circle.fill", .success, false
        )
    }

    private static func progressLabel(
        _ title: String,
        completed: Int,
        total: Int
    ) -> String {
        guard total > 0 else { return title }
        return "\(title) · \(completed)/\(total)"
    }

    private static func value(
        _ phase: ConversationRunPhase,
        _ label: String,
        _ systemImage: String,
        _ tone: ConversationTone,
        _ animated: Bool
    ) -> Self {
        Self(
            phase: phase,
            label: label,
            systemImage: systemImage,
            tone: tone,
            animated: animated
        )
    }
}

struct ConversationBranchPresentation: Equatable, Sendable {
    let name: String
    let position: String
    let status: String
    let detail: String
    let recommended: Bool
    let synthesis: Bool

    static func make(
        agent: RunAgentSummary,
        agents: [RunAgentSummary]
    ) -> Self {
        let ordered = agents.sorted { $0.agent < $1.agent }
        let index = ordered.firstIndex { $0.agent == agent.agent }.map { $0 + 1 } ?? 1
        var detailParts: [String] = []
        if let seconds = agent.seconds, agent.isTerminal {
            detailParts.append(seconds.formatted(.number.precision(.fractionLength(1))) + "s")
        }
        if let reasoning = agent.reasoningDisplay {
            detailParts.append(reasoning)
        }
        return Self(
            name: agent.name,
            position: "\(index) of \(max(1, ordered.count))",
            status: agent.compactStatusLabel,
            detail: detailParts.joined(separator: " · "),
            recommended: agent.recommended,
            synthesis: agent.isSynthesis
        )
    }
}

extension RunAgentSummary {
    var compactStatusLabel: String {
        compactStatusLabel(language: .english)
    }

    func compactStatusLabel(language: AppLanguage) -> String {
        let english: String
        if isSuccessful, workspaceAvailable == false {
            return language.text("不可用", "Unavailable")
        }
        switch normalizedStatus {
        case "starting", "copying": english = "Preparing"
        case "resuming": return language.text("恢复对话", "Restoring conversation")
        case "submitting": return language.text("提交中", "Submitting")
        case "waiting_for_output": return language.text("等待回应", "Waiting for response")
        case "waiting_for_models": return language.text("等待模型", "Waiting for model")
        case "running":
            switch activity?.state {
            case "waiting": return language.text("定时等待", "Waiting")
            case "executing": return language.text("执行中", "Executing")
            case "thinking": return language.text("思考中", "Thinking")
            case "responding": return language.text("输出中", "Responding")
            case "awaiting_event": return language.text("等待下一步", "Awaiting activity")
            default: english = "Working"
            }
        case "stopping": english = "Stopping"
        case "success": english = "Completed"
        case "failed", "error": english = "Failed"
        case "killed", "cancelled", "canceled", "interrupted", "stopped": english = "Stopped"
        case "queued": english = "Queued"
        default: return status.replacingOccurrences(of: "_", with: " ").capitalized
        }
        switch english {
        case "Preparing": return language.text("准备中", english)
        case "Working": return language.text("工作中", english)
        case "Stopping": return language.text("正在停止", english)
        case "Completed": return language.text("已完成", english)
        case "Failed": return language.text("失败", english)
        case "Stopped": return language.text("已停止", english)
        case "Queued": return language.text("排队中", english)
        default: return english
        }
    }

    var accessibilityLabel: String {
        var parts = [name, compactStatusLabel]
        if recommended { parts.append("Recommended") }
        if isSynthesis { parts.append("Synthesis Agent") }
        if rejected { parts.append("Excluded from recommendation") }
        return parts.joined(separator: ", ")
    }

}

func conversationAgentWasUsed(
    run: RunSummary,
    agent: RunAgentSummary
) -> Bool {
    run.normalizedStatus == "finalized" && run.finalizedAgent == agent.agent
}

func conversationAgentDisplayStatus(
    run: RunSummary,
    agent: RunAgentSummary,
    language: AppLanguage = .english
) -> String {
    if conversationAgentWasUsed(run: run, agent: agent) {
        return language.text("已采用", "Used")
    }
    if run.normalizedStatus == "finalized",
       agent.isSuccessful,
       agent.workspaceAvailable == false {
        return language.text("已完成 · 只读", "Completed · Read only")
    }
    return agent.compactStatusLabel(language: language)
}

func conversationAgentGlyphStatus(
    run: RunSummary,
    agent: RunAgentSummary
) -> String {
    if conversationAgentWasUsed(run: run, agent: agent) { return "success" }
    if run.normalizedStatus == "finalized", agent.isSuccessful { return "success" }
    if agent.isRunning { return agent.status }
    return agent.workspaceAvailable == false ? "unavailable" : agent.status
}

func conversationAgentAccessibilityLabel(
    run: RunSummary,
    agent: RunAgentSummary,
    language: AppLanguage = .english
) -> String {
    var parts = [
        run.displayName(forAgent: agent.agent, language: language),
        conversationAgentDisplayStatus(run: run, agent: agent, language: language),
    ]
    if agent.recommended { parts.append(language.text("推荐", "Recommended")) }
    if agent.isSynthesis { parts.append(language.text("综合 Agent", "Synthesis Agent")) }
    if agent.rejected { parts.append(language.text("不参与推荐", "Excluded from recommendation")) }
    return parts.joined(separator: ", ")
}

func selectedAgentAfterRunUpdate(
    previousRun: RunSummary?,
    currentSelection: Int?,
    updatedRun: RunSummary,
    explicitSelection: Int? = nil
) -> Int? {
    if let explicitSelection,
       updatedRun.agents.contains(where: { $0.agent == explicitSelection }) {
        return explicitSelection
    }
    let selectionStillExists = currentSelection.map { selected in
        updatedRun.agents.contains { $0.agent == selected }
    } ?? false
    let enteredReview = previousRun?.runId == updatedRun.runId
        && previousRun?.isWorking == true
        && updatedRun.isReviewable

    if enteredReview, let preferred = preferredReviewAgent(in: updatedRun) {
        return preferred
    }
    if selectionStillExists { return currentSelection }
    return updatedRun.finalizedAgent
        ?? updatedRun.continuationAgent
        ?? updatedRun.recommendedAgent
        ?? updatedRun.agents.first?.agent
}

func selectedAgentForRestoredRun(
    explicitSelection: Int?,
    cachedSelection: Int?,
    run: RunSummary
) -> Int? {
    func available(_ agent: Int?) -> Int? {
        agent.flatMap { value in
            run.agents.contains { $0.agent == value } ? value : nil
        }
    }

    if let explicit = available(explicitSelection) { return explicit }
    if let finalized = available(run.finalizedAgent) { return finalized }
    if run.isReviewable, let preferred = preferredReviewAgent(in: run) {
        return preferred
    }
    return available(cachedSelection)
        ?? available(run.continuationAgent)
        ?? available(run.recommendedAgent)
        ?? run.agents.first?.agent
}

private func preferredReviewAgent(in run: RunSummary) -> Int? {
    if let recommended = run.continuationAgent ?? run.recommendedAgent,
       run.agents.contains(where: { $0.agent == recommended && $0.mayAccept }) {
        return recommended
    }
    return run.agents
        .filter(\.mayAccept)
        .sorted { $0.agent < $1.agent }
        .first?.agent
}
