import SwiftUI

/// Agent-scoped actions live beside the selected result so their target stays
/// explicit. Selecting a branch alone never changes the original workspace.
struct ConversationResultActions: View {
    @EnvironmentObject private var model: AppModel
    @State private var stopAndUseConfirmation = false
    @State private var recoveryConfirmation = false
    let run: RunSummary

    var body: some View {
        if let agent = model.selectedAgentSummary {
            HStack(spacing: 8) {
                primaryAction(agent)
                moreMenu(agent)
            }
            .controlSize(.small)
            .appScaledTextStyle(.callout)
            .confirmationDialog(
                model.appLanguage.text(
                    "现在采用 \(displayName(agent))？",
                    "Use \(displayName(agent)) now?"
                ),
                isPresented: $stopAndUseConfirmation,
                titleVisibility: .visible
            ) {
                Button(model.appLanguage.text(
                    "停止其他 Agent 并采用",
                    "Stop Others and Use Result"
                ), role: .destructive) {
                    Task { await model.acceptSelectedAgent() }
                }
                Button(model.appLanguage.text("继续等待", "Keep Waiting"), role: .cancel) {}
            } message: {
                Text(stopAndUseMessage(agent))
            }
            .confirmationDialog(
                model.appLanguage.text(
                    "上次保存中断了，如何继续？",
                    "Saving was interrupted. How would you like to continue?"
                ),
                isPresented: $recoveryConfirmation,
                titleVisibility: .visible
            ) {
                Button(model.appLanguage.text(
                    "原文件夹已包含这份结果，继续使用",
                    "The original folder has this result; continue"
                )) {
                    Task { await model.recoverFinalization(syncWasApplied: true) }
                }
                Button(model.appLanguage.text(
                    "还没保存完整，返回重新选择结果",
                    "Saving is incomplete; choose a result again"
                )) {
                    Task { await model.recoverFinalization(syncWasApplied: false) }
                }
                Button(model.appLanguage.text("暂不处理", "Not now"), role: .cancel) {}
            } message: {
                Text(model.appLanguage.text(
                    "请检查原文件夹：若已包含所选 Agent 的全部修改，可以直接继续；若未保存完整，请返回选择结果后重新保存。重新选择不会还原已经写入的文件。不确定时请先取消。",
                    "Check the original folder. Continue only if it contains all changes from the selected Agent. Otherwise, return to choose and save a result again. Choosing again does not undo files already written. Cancel if you are unsure."
                ))
            }
            .task(id: "\(run.runId):\(agent.agent)") {
                while !Task.isCancelled {
                    await model.prefetchSelectedAgentDiffSummary(refresh: model.selectedAgentSummary?.isTerminal == false)
                    do { try await Task.sleep(for: .seconds(8)) } catch { return }
                }
            }
        }
    }

    @ViewBuilder
    private func primaryAction(_ agent: RunAgentSummary) -> some View {
        switch conversationBranchPrimaryAction(
            run: run,
            agent: agent,
            isLatestWorkspaceRun: model.isViewingLatestWorkspaceRun,
            syncBack: model.settings.syncBack,
            operation: nil,
            hasPendingSubmission: model.pendingPromptSubmission != nil
        ) {
        case .progress:
            Button(model.appLanguage.text("保存结果", "Save Result")) {}
                .buttonStyle(.bordered)
                .disabled(true)
                .accessibilityValue(model.appLanguage.text("正在保存", "Saving"))
        case .resolveSync:
            Button(model.appLanguage.text("恢复中断的保存", "Resume interrupted save")) {
                if model.canVerifyInterruptedSave {
                    Task { await model.recoverFinalization() }
                } else {
                    recoveryConfirmation = true
                }
            }
                .buttonStyle(.borderedProminent)
                .tint(.orange)
                .disabled(model.isRunOperationInProgress)
        case .retryCleanup:
            Button(model.appLanguage.text("重试清理", "Retry Cleanup")) {
                Task { await model.retryFinalizationCleanup() }
            }
            .buttonStyle(.borderedProminent)
            .tint(.orange)
            .disabled(model.isRunOperationInProgress)
        case .used:
            // The selected result header already carries the used badge.
            // Repeating the same state in the action lane makes a completed
            // result look like it still needs a user decision.
            EmptyView()
        case .previewUsed:
            EmptyView()
        case .enableSyncBack:
            Button(model.appLanguage.text("启用同步回原工作区", "Enable Sync Back")) {
                model.settingsPresented = true
            }
                .buttonStyle(.bordered)
                .tint(.orange)
        case .useResult(let stopsOthers):
            Button {
                if stopsOthers {
                    stopAndUseConfirmation = true
                } else {
                    Task { await model.acceptSelectedAgent() }
                }
            } label: {
                Label(
                    model.appLanguage.text("保存结果", "Save Result"),
                    systemImage: "arrow.down.doc"
                )
            }
            .buttonStyle(.bordered)
            .help(model.appLanguage.text("将此 Agent 的修改保存到原工作区", "Save this Agent's changes to the original workspace"))
            .disabled(model.isRunOperationInProgress)
        case .retryAgent:
            Button(model.appLanguage.text("重试 Agent", "Retry Agent")) {
                Task { await model.retrySelectedAgent() }
            }
            .buttonStyle(.bordered)
            .disabled(model.isRunOperationInProgress)
        case .stopAgent:
            EmptyView()
        case .none:
            EmptyView()
        }
    }

    private func moreMenu(_ agent: RunAgentSummary) -> some View {
        Menu {
            if run.normalizedStatus == "sync_ambiguous", model.canVerifyInterruptedSave {
                Button(model.appLanguage.text("手动处理未完成的保存…", "Review interrupted save manually…")) {
                    recoveryConfirmation = true
                }
                Divider()
            }
            if agent.canStop {
                Button(role: .destructive) {
                    Task { await model.stopAgent(agent.agent, in: run.runId) }
                } label: {
                    Label(model.appLanguage.text("仅停止 \(displayName(agent))", "Stop only \(displayName(agent))"),
                          systemImage: "stop.circle")
                }
                .disabled(model.isRunOperationInProgress || !model.daemonOnline)
                Divider()
            }
            if (model.activeRun?.agents.count ?? 0) > 1 {
                Button { model.presentAgentComparison() } label: {
                    Label(model.appLanguage.text("比较 Agent", "Compare Agents"), systemImage: "rectangle.split.2x1")
                }
            }
            if model.selectedAgentWorkItemCount > 0 {
                Button {
                    ConversationClipboard.copyWork(model.selectedTranscriptBlocks)
                } label: {
                    Label(
                        model.appLanguage.text("复制工作过程", "Copy Work Log"),
                        systemImage: "terminal"
                    )
                }
            }

            if agent.mayRetry {
                Divider()
            }
            if agent.mayRetry {
                Button {
                    Task { await model.retrySelectedAgent() }
                } label: {
                    Label(
                        model.appLanguage.text("重试 Agent", "Retry Agent"),
                        systemImage: "arrow.clockwise"
                    )
                }
                .disabled(model.isRunOperationInProgress)
            }

            if model.isViewingLatestWorkspaceRun, run.finalizedAgent == nil {
                Divider()
                Button {
                    Task { await model.toggleSelectedAgentRejection() }
                } label: {
                    Label(
                        agent.rejected
                            ? model.appLanguage.text("重新加入推荐", "Include in recommendation")
                            : model.appLanguage.text("从推荐中排除", "Exclude from recommendation"),
                        systemImage: agent.rejected ? "arrow.uturn.backward" : "hand.thumbsdown"
                    )
                }
                .disabled(model.isRunOperationInProgress)
            }
        } label: {
            Image(systemName: "slider.horizontal.3")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .foregroundStyle(.secondary)
        .frame(width: 30, height: 30)
        .accessibilityLabel(model.appLanguage.text("\(displayName(agent)) 操作", "Actions for \(displayName(agent))"))
        .accessibilityIdentifier("result.agent-actions")
        .help(model.appLanguage.text(
            "\(displayName(agent)) 的更多操作",
            "More actions for \(displayName(agent))"
        ))
    }

    private func stopAndUseMessage(_ agent: RunAgentSummary) -> String {
        let unfinished = run.agents.filter { !$0.isTerminal }.count
        let workspace = model.selectedWorkspace?.name
            ?? model.appLanguage.text("原工作区", "the original workspace")
        return model.appLanguage.text(
            "将停止 \(unfinished) 个尚未完成的 Agent，并把 \(displayName(agent)) 应用到 \(workspace)。",
            "\(unfinished == 1 ? "1 unfinished Agent" : "\(unfinished) unfinished Agents") will be stopped. \(displayName(agent)) will be applied to \(workspace)."
        )
    }

    private func displayName(_ agent: RunAgentSummary) -> String {
        run.displayName(forAgent: agent.agent, language: model.appLanguage)
    }


}

struct ConversationRunStatusLabel: View {
    @Environment(\.appAccentColor) private var appAccentColor
    let presentation: ConversationRunPresentation
    var compact = false

    var body: some View {
        HStack(spacing: 6) {
            if presentation.animated {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: presentation.systemImage)
                    .foregroundStyle(color)
            }
            Text(presentation.label)
                .appScaledTextStyle(
                    compact ? .caption : .callout,
                    weight: compact ? .regular : .medium
                )
                .foregroundStyle(color)
                .lineLimit(1)
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var color: Color {
        switch presentation.tone {
        case .neutral: return .secondary
        case .accent: return appAccentColor.color
        case .success: return .green
        case .warning: return .orange
        case .failure: return .red
        }
    }
}
