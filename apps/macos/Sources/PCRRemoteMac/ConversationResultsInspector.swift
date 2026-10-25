import SwiftUI

enum ResultInspectorLayout {
    static let sectionsAutosaveName = "ParallelCodex.ResultSections.v1"

    static func overlaySize(available: CGSize, textScale: CGFloat) -> CGSize {
        CGSize(
            width: min(max(1, available.width - 32), 440 * textScale),
            height: min(max(1, available.height - 32), 600 * textScale)
        )
    }
}

struct ConversationResultsInspector: View {
    @EnvironmentObject private var model: AppModel
    let run: RunSummary?
    var close: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 0) {
            inspectorHeader
            Rectangle().fill(ParallelCodexVisualStyle.separator).frame(height: 1)
            if let run, let agent = model.selectedAgentSummary {
                agentContext(agent, run: run)
                ConversationResultDetails(
                    liveMetrics: model.activeRunMetrics,
                    run: run,
                    agent: agent,
                    diffSummary: model.selectedAgentDiffSummary,
                    diffLoading: model.isSelectedAgentDiffSummaryLoading,
                    showDiff: { Task { await model.showSelectedAgentDiff() } },
                    showFileDiff: { path in Task { await model.showSelectedAgentDiff(file: path) } }
                )
                .accessibilityElement(children: .contain)
                .accessibilityLabel(model.appLanguage.text("结果信息", "Result information"))
            } else {
                VStack(spacing: 12) {
                    if model.pendingPromptSubmission != nil || model.isSelectedWorkspaceContextLoading {
                        ProgressView()
                        Text(model.appLanguage.text("正在准备本轮结果", "Preparing this turn"))
                    } else {
                        Image(systemName: "text.bubble")
                        Text(model.appLanguage.text("暂无结果", "No result yet"))
                    }
                }
                .foregroundStyle(.secondary)
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .appScaledTextStyle(.body)
        .background(ParallelCodexVisualStyle.canvas)
    }

    private var inspectorHeader: some View {
        HStack(spacing: 12) {
            Text(model.appLanguage.text("结果详情", "Result details"))
                .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.section, weight: .medium)
            Spacer(minLength: 8)
            if run != nil && !model.isViewingLatestConversationTurn {
                Image(systemName: "lock")
                    .foregroundStyle(.secondary)
                    .help(model.appLanguage.text("较早轮次仅供查看", "Earlier turns are read-only"))
            }
            if run?.mayAddAgents == true && model.isViewingLatestWorkspaceRun {
                Button {
                    model.additionalAgentCount = 1
                    model.addAgentsPresented = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(ParallelCodexIconButtonStyle())
                .disabled(model.isRunOperationInProgress)
                .help(model.appLanguage.text("追加 Agent", "Add Agents"))
            }
            if let close {
                Button(action: close) {
                    Image(systemName: "xmark")
                }
                .buttonStyle(ParallelCodexIconButtonStyle())
                .keyboardShortcut(.cancelAction)
                .foregroundStyle(.secondary)
                .help(model.appLanguage.text("关闭结果详情", "Close result details"))
                .accessibilityLabel(model.appLanguage.text("关闭结果详情", "Close result details"))
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    private func agentContext(_ agent: RunAgentSummary, run: RunSummary) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(run.displayName(forAgent: agent.agent, language: model.appLanguage))
                    .appScaledSystemFont(size: 15, weight: .medium)
                Spacer(minLength: 8)
                ConversationResultActions(run: run)
                    .disabled(model.pendingAgentSelection != nil)
            }
            Text(model.continuationSelectionContextText ?? ResultAgentContext.text(run: run, viewedAgent: agent.agent,
                                         isLatestTurn: model.isViewingLatestConversationTurn,
                                         selectionPending: model.continuationSelectionNeedsConfirmation,
                                         continuationAgent: model.remoteFollowUpSelection(runID: run.runId),
                                         submissionPlan: model.promptSubmissionPlan,
                                         language: model.appLanguage))
                .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.caption)
                .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("result.agent-context")
    }
}

enum ResultAgentContext {
    static func text(run: RunSummary, viewedAgent: Int, isLatestTurn: Bool = true,
                     selectionPending: Bool = false,
                     continuationAgent: Int? = nil,
                     submissionPlan: PromptSubmissionPlan? = nil,
                     language: AppLanguage) -> String {
        if !isLatestTurn {
            return language.text("正在查看较早轮次", "Viewing an earlier turn")
        }
        if let adopted = run.finalizedAgent {
            let name = run.displayName(forAgent: adopted, language: language)
            return language.text("续问使用 \(name) · 已保存", "Continue from \(name) · Saved")
        }
        if run.isWorking {
            return language.text("正在查看此 Agent 的工作", "Viewing this Agent's work")
        }
        if run.isStoppedWithoutResult {
            let plan = submissionPlan ?? .start(resuming: run.savedConversationSessionID != nil)
            switch plan {
            case .start(resuming: true):
                return language.text("本轮已停止；下一条消息从已保存的对话继续",
                                     "This turn stopped; the next message continues the saved conversation")
            case .start(resuming: false):
                return language.text("本轮已停止；下一条消息将开始新对话",
                                     "This turn stopped; the next message starts a new conversation")
            case .enqueue:
                return language.text("本轮已停止；后续消息保留在队列中，等待处理",
                                     "This turn stopped; queued messages are retained for review")
            case .loading, .blocked, .continueFrom:
                return plan.visibleContextText(language: language) ?? ""
            }
        }
        if selectionPending {
            return language.text("正在确认续问来源", "Confirming the continuation source")
        }
        let source = continuationAgent ?? viewedAgent
        guard run.agents.contains(where: { $0.agent == source && $0.mayContinue }) else {
            if !run.agents.contains(where: \.mayContinue) {
                return language.text("本轮没有可续问的 Agent 结果", "This turn has no Agent result to continue")
            }
            return language.text("此结果无法续问，请选择其他 Agent", "Choose another Agent to continue")
        }
        let name = run.displayName(forAgent: source, language: language)
        return language.text("续问使用 \(name)", "Continue from \(name)")
    }
}

struct ResultStatusGlyph: View {
    @Environment(\.appTextSize) private var textSize
    let status: String
    var animatesExecution = false

    var body: some View {
        Group {
            if status.lowercased() == "running" {
                ExecutionActivityIndicator(isWorking: animatesExecution)
            } else if status.lowercased() == "success" {
                Circle()
                    .fill(Color.green)
                    .frame(width: 8 * textSize.scale, height: 8 * textSize.scale)
            } else {
                Image(systemName: Self.symbol(for: status))
                    .appScaledSystemFont(size: 11, weight: .medium)
                    .foregroundStyle(Self.color(for: status))
            }
        }
        .frame(width: 12 * textSize.scale, height: 12 * textSize.scale)
    }

    // Execution state is not a content-loading operation.
    static func symbol(for status: String) -> String {
        switch status.lowercased() {
        case "running": return "circle.fill"
        case "starting", "copying", "resuming", "submitting": return "gearshape"
        case "queued", "waiting_for_models", "waiting_for_output": return "clock"
        case "stopping": return "stop.circle"
        case "success": return "circle.fill"
        case "failed", "error": return "xmark.circle.fill"
        case "unavailable": return "exclamationmark.circle.fill"
        case "killed", "cancelled", "canceled", "interrupted", "stopped":
            return "stop.circle.fill"
        default: return "circle"
        }
    }

    static func color(for status: String) -> Color {
        switch status.lowercased() {
        case "running": return .blue
        case "success": return .green
        case "failed", "error": return .red
        case "unavailable": return .orange
        case "stopping", "killed", "cancelled", "canceled", "interrupted", "stopped":
            return .orange
        default: return .secondary
        }
    }
}
