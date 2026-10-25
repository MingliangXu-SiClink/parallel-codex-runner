import Foundation

enum PromptComposerRecoveryAction: Equatable, Sendable {
    case openLatestWorkspaceTurn
    case openLatestConversationTurn
    case discardCurrentTurn
    case startNewConversation
}

enum PromptSubmissionPlan: Equatable, Sendable {
    case loading
    case start(resuming: Bool)
    case enqueue
    case continueFrom(
        runID: String,
        agent: Int,
        agentName: String,
        stopsRemainingAgents: Bool
    )
    case blocked(String)

    static func make(
        composingNewConversation: Bool = false,
        run: RunSummary?,
        selectedAgent: RunAgentSummary?,
        agentDisplayName: String? = nil,
        hasQueuedFollowUps: Bool,
        syncBack: Bool,
        hasSelectedResumeSession: Bool
    ) -> Self {
        if composingNewConversation {
            return .start(resuming: false)
        }
        if hasQueuedFollowUps {
            return syncBack
                ? .enqueue
                : .blocked("Follow-up queue requires workspace sync-back")
        }
        guard let run else {
            return .start(resuming: hasSelectedResumeSession)
        }
        if run.isWorking {
            guard syncBack else {
                return .blocked("Follow-up queue requires workspace sync-back")
            }
            // A candidate finishing must not turn the queue button into an
            // interrupt action while the user is composing the next message.
            // Early acceptance remains an explicit action on the result.
            return .enqueue
        }
        if run.hasUnresolvedFinalization {
            return syncBack ? .enqueue : .blocked("Follow-up queue requires workspace sync-back")
        }
        if run.isReviewable {
            guard syncBack else {
                return .blocked("Continuing from a result requires workspace sync-back")
            }
            guard let selectedAgent, selectedAgent.mayContinue else {
                return .blocked("Select a successful Agent to continue")
            }
            return .continueFrom(
                runID: run.runId,
                agent: selectedAgent.agent,
                agentName: agentDisplayName ?? selectedAgent.name,
                stopsRemainingAgents: false
            )
        }
        if run.normalizedStatus == "finalized" {
            guard let selectedAgent,
                  selectedAgent.agent == run.finalizedAgent,
                  selectedAgent.mayContinue else {
                return .blocked("The applied result cannot be continued")
            }
            return .continueFrom(
                runID: run.runId,
                agent: selectedAgent.agent,
                agentName: agentDisplayName ?? selectedAgent.name,
                stopsRemainingAgents: false
            )
        }
        if run.isStoppedWithoutResult {
            return .start(resuming: run.savedConversationSessionID != nil)
        }
        if run.artifactsAvailable == false {
            return .blocked("The working files for this turn are no longer available")
        }
        return .blocked("Discard this run before starting another task")
    }

    var isAvailable: Bool {
        if self == .loading { return false }
        if case .blocked = self { return false }
        return true
    }

    var recoveryAction: PromptComposerRecoveryAction? {
        guard case .blocked(let reason) = self else { return nil }
        switch reason {
        case "Open the latest turn in this conversation to continue":
            return .openLatestConversationTurn
        case "A newer turn is still running in this workspace",
             "A newer turn is waiting for review":
            return .openLatestWorkspaceTurn
        case "Discard this run before starting another task":
            return .discardCurrentTurn
        case "The working files for this turn are no longer available":
            return .startNewConversation
        default:
            return nil
        }
    }

    func settingsForSubmission(_ settings: RunSettings, run: RunSummary? = nil) -> RunSettings {
        var snapshot = settings
        if self == .start(resuming: false) {
            snapshot.resumeSessionID = "NO"
        } else if self == .start(resuming: true), let run, run.isStoppedWithoutResult {
            snapshot.resumeSessionID = run.savedConversationSessionID ?? "NO"
        }
        return snapshot
    }

    func blockedReason(language: AppLanguage) -> String? {
        guard case .blocked(let reason) = self else { return nil }
        return localizedBlockReason(reason, language: language)
    }

    var stopsRemainingAgents: Bool {
        if case .continueFrom(_, _, _, true) = self { return true }
        return false
    }

    var contextText: String? {
        contextText(language: .english)
    }

    func contextText(language: AppLanguage) -> String? {
        switch self {
        case .loading:
            return language.text("正在同步对话", "Syncing conversation")
        case .start(resuming: true):
            return language.text(
                "在此对话中开始新一轮并行任务",
                "Continue this conversation with a parallel turn"
            )
        case .start(resuming: false):
            return language.text("开始新一轮并行任务", "Start a new parallel turn")
        case .enqueue:
            return language.text("当前轮完成后发送", "Send after the current turn finishes")
        case .continueFrom(_, _, let agentName, let stopsRemainingAgents):
            return language.text(
                stopsRemainingAgents
                    ? "停止其余 Agent 并从 \(agentName) 继续"
                    : "从 \(agentName) 继续",
                stopsRemainingAgents
                    ? "Stop remaining Agents and continue from \(agentName)"
                    : "Continue from \(agentName)"
            )
        case .blocked(let reason):
            return localizedBlockReason(reason, language: language)
        }
    }

    var visibleContextText: String? {
        visibleContextText(language: .english)
    }

    func visibleContextText(language: AppLanguage) -> String? {
        switch self {
        case .loading:
            return nil
        case .start(resuming: true):
            return nil
        case .start(resuming: false):
            return nil
        case .enqueue:
            return language.text("此消息将在当前轮结束后发送", "This message will send after the current turn")
        case .continueFrom(_, _, let agentName, true):
            return language.text(
                "立即发送将停止其余 Agent，并从 \(agentName) 继续",
                "Sending now stops the remaining Agents and continues from \(agentName)"
            )
        case .continueFrom(_, _, let agentName, false):
            return language.text(
                "下一条消息将从 \(agentName) 继续",
                "The next message continues from \(agentName)"
            )
        case .blocked(let reason):
            return localizedBlockReason(reason, language: language)
        }
    }

    var promptPlaceholder: String {
        promptPlaceholder(language: .english)
    }

    func promptPlaceholder(language: AppLanguage) -> String {
        switch self {
        case .loading:
            return language.text("消息", "Message")
        case .start(resuming: true):
            return language.text("继续提问", "Ask a follow-up")
        case .start(resuming: false):
            return language.text("描述你希望 Codex 完成的任务", "Describe what you want Codex to do")
        case .enqueue:
            return language.text("输入下一条消息", "Write the next message")
        case .continueFrom:
            return language.text("继续提问", "Ask a follow-up")
        case .blocked where recoveryAction == .startNewConversation:
            return language.text("这段对话仅供查看", "This conversation is read-only")
        case .blocked:
            return language.text("请先处理当前任务", "Resolve the current task before sending")
        }
    }

    var contextSystemImage: String {
        switch self {
        case .loading: return "arrow.trianglehead.2.clockwise.rotate.90"
        case .start(resuming: true): return "clock.arrow.circlepath"
        case .start(resuming: false): return "arrow.up.circle"
        case .enqueue: return "text.append"
        case .continueFrom: return "arrow.triangle.branch"
        case .blocked: return "exclamationmark.circle"
        }
    }

    var submitSystemImage: String {
        switch self {
        case .enqueue:
            return "clock.fill"
        case .continueFrom(_, _, _, true):
            return "arrow.up.forward.circle.fill"
        case .loading, .start, .continueFrom, .blocked:
            return "arrow.up.circle.fill"
        }
    }

    private func localizedBlockReason(
        _ reason: String,
        language: AppLanguage
    ) -> String {
        switch reason {
        case "Follow-up queue requires workspace sync-back":
            return language.text("排队提问需要开启同步回原工作区", reason)
        case "Resolve the previous result before continuing":
            return language.text("请先处理上一轮结果", reason)
        case "Continuing from a result requires workspace sync-back":
            return language.text("从结果继续需要开启同步回原工作区", reason)
        case "Select a successful Agent to continue":
            return language.text("请选择一个成功的 Agent 后继续", reason)
        case "The applied result cannot be continued":
            return language.text("已采用的结果无法继续", reason)
        case "Discard this run before starting another task":
            return language.text("请先放弃当前任务，再开始新任务", reason)
        case "The working files for this turn are no longer available":
            return language.text("本轮运行文件已不可用，无法从此结果继续", reason)
        case "Open the latest turn in this conversation to continue":
            return language.text("请先打开此对话的最新一轮", reason)
        case "A newer turn is still running in this workspace":
            return language.text("此工作区有更新一轮仍在运行", reason)
        case "A newer turn is waiting for review":
            return language.text("此工作区有更新一轮等待选择结果", reason)
        case "Connect to this computer before sending":
            return language.text("请先连接此电脑，草稿会保留", reason)
        case "This turn is being discarded. Your draft is retained":
            return language.text("正在丢弃本轮，草稿会保留", reason)
        case "The continuation source is being updated. Your draft is retained":
            return language.text("正在更新续问来源，草稿会保留", reason)
        default:
            return reason
        }
    }
}

func earlierConversationContinuationBlockReason(
    run: RunSummary,
    workspace: RemoteWorkspace,
    isLatestConversationTurn: Bool
) -> String? {
    guard !workspace.lastRunId.isEmpty,
          run.runId != workspace.lastRunId else { return nil }
    guard isLatestConversationTurn else {
        return "Open the latest turn in this conversation to continue"
    }
    if workspace.removalLooksActive {
        return "A newer turn is still running in this workspace"
    }
    if workspace.hasUnresolvedResultSelection {
        return "A newer turn is waiting for review"
    }
    return nil
}

enum PromptSubmissionRetryPolicy {
    static func isLegacyHistoryCompatibilityFailure(code: String?, message: String) -> Bool {
        code == "invalid_argument" && message.hasPrefix(
            "Unsupported Codex history schema; no global database was copied: "
        )
    }

    static func outcomeIsUncertain(_ error: Error) -> Bool {
        guard case .remote(let code, let message) = error as? PcrdClientError else { return false }
        if isLegacyHistoryCompatibilityFailure(code: code, message: message) { return true }
        return ["command_in_doubt", "command_conflict", "journal_corrupt", "internal_error",
                "pcr_error", "filesystem_error", "command_failed"].contains(code)
    }

    static func errorCode(_ error: Error) -> String? {
        guard case .remote(let code, _) = error as? PcrdClientError else { return nil }
        return code
    }

    static func shouldRetry(_ error: Error) -> Bool {
        guard let clientError = error as? PcrdClientError else { return true }
        if let status = clientError.relayHTTPStatus {
            return status == 408 || status == 425 || status == 429 || status >= 500
        }
        switch clientError {
        case .missingToken, .connection, .invalidResponse:
            return true
        case .socketPathTooLong:
            return false
        case .remote(let code, _):
            return [
                "relay_error", "relay_unreachable", "controller_relay_error", "relay_timeout",
                "command_in_progress", "pcr_operation_pending", "workspace_busy",
            ].contains(code)
        }
    }

    static func delay(after failureCount: Int) -> TimeInterval {
        min(30, pow(2, Double(min(max(1, failureCount), 5))))
    }
}
