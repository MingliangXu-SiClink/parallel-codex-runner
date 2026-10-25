import SwiftUI

struct PromptComposer: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        PromptComposerContent(draft: model.promptDraft)
    }
}

struct ComposerActionSymbol: View {
    @Environment(\.appTextSize) private var textSize
    let kind: ComposerPrimaryAction.Kind

    var body: some View {
        Group {
            if kind == .sending {
                ProgressView().controlSize(.small)
            } else if kind == .queue {
                QueueIcon().frame(width: 16 * textSize.scale, height: 16 * textSize.scale)
            } else {
                Image(systemName: kind == .stop ? "stop.fill" : "arrow.up")
                    .appScaledSystemFont(size: kind == .stop ? 12 : 16, weight: .regular)
            }
        }
        .frame(width: ParallelCodexVisualStyle.Composer.controlSize * textSize.scale,
               height: ParallelCodexVisualStyle.Composer.controlSize * textSize.scale)
        .fixedSize()
        .contentShape(Circle())
    }
}

struct ComposerActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused
    @State private var hovered = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(!enabled ? Color.primary.opacity(0.24)
                : ParallelCodexVisualStyle.canvas)
            .background(Color.primary.opacity(enabled ? (hovered ? 1 : 0.92) : 0.08),
                        in: Circle())
            .overlay(Circle().strokeBorder(Color.accentColor.opacity(enabled && focused ? 0.65 : 0), lineWidth: 2))
            .opacity(configuration.isPressed && enabled ? 0.75 : 1)
            .onHover { hovered = $0 }
    }
}

private struct PromptComposerContent: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var draft: PromptDraftState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.appTextSize) private var appTextSize
    @State private var promptFilePresented = false
    @State private var discardConfirmation = false
    @State private var stopAndContinueConfirmation = false
    @State private var promptEditorHeight = ParallelCodexVisualStyle.Composer.minimumEditorHeight
    @State private var promptEditorFocused = false
    @State private var configurationEditing = false

    private var promptBinding: Binding<String> {
        let context = model.promptDraftContext
        return Binding(
            get: { model.promptDraftContext == context ? model.prompt : model.promptDraftsByContext[context] ?? "" },
            set: { if model.promptDraftContext == context { model.updatePrompt($0) } }
        )
    }

    private var selectionChanged: (PromptEditorSelection) -> Void {
        let context = model.promptDraftContext
        return { selection in
            guard !context.isEmpty, model.promptDraftContext == context,
                  model.promptSelectionsByContext[context] != selection else { return }
            model.promptSelectionsByContext[context] = selection
            model.rememberPromptDraft()
        }
    }

    var body: some View {
        Group {
            if let record = model.visibleDeletedConversation {
                HStack(spacing: 11) {
                    Label(model.appLanguage.text("此对话已删除", "This conversation was deleted"), systemImage: "trash")
                        .appScaledTextStyle(.callout).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    Button {
                        do { try model.restoreDeletedConversation(record) }
                        catch { model.errorMessage = model.appLanguage.userMessage(for: error) }
                    } label: {
                        Label(model.appLanguage.text("恢复对话", "Restore Conversation"), systemImage: "arrow.uturn.backward")
                    }
                    .accessibilityIdentifier("conversation.deleted.restore")
                }
                .padding(.horizontal, 16).padding(.vertical, 12)
                .background(ParallelCodexVisualStyle.surface)
            } else if showsHistoricalFooter {
                previousConversationFooter
            } else {
                activeComposer
            }
        }
        .confirmationDialog(
            model.appLanguage.text("丢弃本轮？", "Discard this turn?"),
            isPresented: $discardConfirmation,
            titleVisibility: .visible
        ) {
            Button(model.appLanguage.text("丢弃本轮", "Discard Turn"), role: .destructive) {
                Task { await model.discardCurrentRun() }
            }
            Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {}
        } message: {
            Text(model.appLanguage.text(
                "不可用的 Agent 结果会被清理，原工作区不会被删除。",
                "Unusable Agent results will be cleared. The original workspace is not deleted."
            ))
        }
        .confirmationDialog(
            model.appLanguage.text(
                "停止其他 Agent 并继续？",
                "Stop the remaining Agents and continue?"
            ),
            isPresented: $stopAndContinueConfirmation,
            titleVisibility: .visible
        ) {
            Button(model.appLanguage.text("停止并继续", "Stop & Continue")) {
                Task { await model.submitPrompt() }
            }
            Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {}
        } message: {
            Text(model.appLanguage.text(
                "将采用 \(model.selectedAgentDisplayName ?? "当前结果")，停止尚未完成的 Agent，并从该结果继续。",
                "This will use \(model.selectedAgentDisplayName ?? "the selected result"), stop unfinished Agents, and continue from that result."
            ))
        }
    }

    private var showsHistoricalFooter: Bool {
        if model.promptSubmissionPlan == .loading { return false }
        guard model.activeRun != nil else { return false }
        return model.isViewingEarlierConversationTurn
    }

    private var previousConversationFooter: some View {
        return HStack(spacing: 11) {
            Image(systemName: "clock.arrow.circlepath")
                .foregroundStyle(Color.secondary)
            Text(model.appLanguage.text("较早的对话轮次", "Earlier turn"))
                .appScaledTextStyle(.callout, weight: .semibold)
            Spacer()
            Button(model.appLanguage.text("最新一轮", "Latest Turn")) {
                Task { await model.openLatestSelectedConversationTurn() }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(ParallelCodexVisualStyle.surface)
    }

    private var activeComposer: some View {
        let queueContext = model.followUpQueueContext
        return VStack(alignment: .leading, spacing: ParallelCodexVisualStyle.Spacing.small) {
            if let context = model.currentRunOperationContext(), let issue = model.runOperationIssues[context.key] {
                HStack {
                    Text(issue.message).foregroundStyle(.secondary).lineLimit(3).textSelection(.enabled)
                    Spacer()
                    Button { model.runOperationIssues.removeValue(forKey: context.key) } label: { Image(systemName: "xmark") }
                        .help(model.appLanguage.localized("Close"))
                }.appScaledTextStyle(.caption)
            }
            if let submission = model.pendingPromptSubmission,
               let presentation = promptSubmissionPresentation(
                   submission.presentationStatus.rawValue,
                   language: model.appLanguage,
                   needsConnectionRecovery: submission.needsConnectionRecovery,
                   errorCode: submission.errorCode
               ) {
                HStack(spacing: 10) {
                    Image(systemName: presentation.icon)
                        .foregroundStyle(presentation.color)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(submission.allowsManualRecovery || submission.status != .failed ? presentation.title
                             : model.appLanguage.text("正在核对消息接收", "Checking message delivery"))
                            .appScaledTextStyle(.caption, weight: .semibold)
                            .foregroundStyle(presentation.color)
                        Text(promptSubmissionDetail(
                            submission,
                            language: model.appLanguage
                        ))
                            .appScaledTextStyle(.callout)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    if submission.status == .awaitingConfirmation {
                        Button {
                            model.cancelStorageUsage(expectedCommandID: submission.commandID)
                        } label: {
                            Label(model.appLanguage.text("返回编辑", "Return to Draft"), systemImage: "pencil")
                        }
                        .buttonStyle(.borderless)
                        .disabled(!model.prompt.isEmpty && model.prompt != submission.prompt)
                        .accessibilityIdentifier("prompt.storage.return")
                        Button {
                            Task { await model.confirmStorageUsage(expectedCommandID: submission.commandID) }
                        } label: {
                            Label(model.appLanguage.text("确认并继续", "Confirm & Continue"), systemImage: "arrow.right")
                        }
                        .buttonStyle(.borderless)
                        .accessibilityIdentifier("prompt.storage.confirm")
                    } else if submission.needsConnectionRecovery {
                        if #available(macOS 14.0, *) {
                            SettingsLink { Label(model.appLanguage.text("连接设置", "Connection Settings"), systemImage: "gearshape") }
                                .buttonStyle(.borderless)
                        } else {
                            Button {
                                NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil)
                            } label: {
                                Label(model.appLanguage.text("连接设置", "Connection Settings"), systemImage: "gearshape")
                            }
                            .buttonStyle(.borderless)
                        }
                    } else if submission.status == .openingFailed {
                        Button { Task { await model.openLatestWorkspaceConversation() } } label: {
                            Label(model.appLanguage.text("查看最近任务", "View Recent Task"), systemImage: "clock.arrow.circlepath")
                        }
                        .buttonStyle(.borderless)
                    }
                    if submission.allowsManualRecovery {
                        Button(model.appLanguage.text("编辑", "Edit")) {
                            model.editPendingPrompt()
                        }
                            .buttonStyle(.borderless)
                            .disabled(!model.prompt.isEmpty && model.prompt != submission.prompt)
                            .help(model.appLanguage.text("将未发送的消息放回输入框，不覆盖已有草稿", "Return the unsent message without replacing another draft"))
                        Button(model.appLanguage.text("重试", "Retry")) {
                            Task { await model.retryPendingPrompt() }
                        }
                            .buttonStyle(.borderless)
                    }
                }
                .padding(.horizontal, ParallelCodexVisualStyle.Spacing.small)
            }

            if model.promptComposerContextIsRelevant,
               let context = model.promptComposerContextText,
               !model.promptSubmissionContextAvailable || model.isPreviewingUnusedAgentResult {
                HStack(spacing: 10) {
                    Label(
                        context,
                        systemImage: model.promptComposerContextSystemImage
                    )
                    Spacer(minLength: 8)
                    recoveryAction
                }
                .appScaledTextStyle(.caption, weight: .medium)
                .foregroundStyle(
                    model.promptSubmissionContextAvailable
                        && !model.promptSubmissionStopsRemainingAgents
                        ? Color.secondary
                        : Color.orange
                )
            }

            VStack(spacing: 0) {
                if let issue = model.queueIssue {
                    HStack {
                        Text(issue).foregroundStyle(.secondary).lineLimit(3)
                        Spacer()
                        Button { model.queueIssue = nil } label: { Image(systemName: "xmark") }
                            .help(model.appLanguage.localized("Close"))
                    }.appScaledTextStyle(.caption).padding(.horizontal, 16)
                }
                if !model.composerQueuedMessages.isEmpty {
                    ComposerQueueView(
                        items: model.composerQueuedMessages,
                        status: model.visibleDeferredPrompts.isEmpty ? model.followUpStatusText
                            : model.appLanguage.text("排队消息", "Queued messages"),
                        dispatching: model.followUpIsDispatching,
                        context: queueContext,
                        change: { id, change in await model.changeQueuedFollowUp(id, change: change, expectedContext: queueContext) },
                        edit: { model.queueEditRequest = $0 },
                        manageQueue: { model.presentFollowUpQueue() }
                    )
                    .id("\(model.currentMachineContextID):\(model.selectedWorkspaceID ?? ""): \(model.selectedConversationID ?? "")")
                    .padding(.horizontal, 14)
                    .padding(.bottom, -16)
                }
                composerInput
                    .zIndex(1)
            }
        }
        .frame(
            maxWidth: ParallelCodexVisualStyle.Layout.composerMaximumWidth,
            alignment: .leading
        )
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.horizontal, ParallelCodexVisualStyle.Spacing.xlarge)
        .padding(.top, ParallelCodexVisualStyle.Spacing.small)
        .padding(.bottom, ParallelCodexVisualStyle.Spacing.medium)
        .background(ParallelCodexVisualStyle.canvas.opacity(0.96))
        .sheet(item: $model.queueEditRequest) { item in
            QueuedFollowUpEditor(item: item,
                save: { await model.changeQueuedFollowUp(item.id, change: $0, expectedContext: queueContext) },
                close: { model.queueEditRequest = nil })
                .environment(\.appLanguage, model.appLanguage)
        }
        .fileImporter(
            isPresented: $promptFilePresented,
            allowedContentTypes: [.plainText, .text],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first { model.importPromptFile(url) }
            case .failure(let error):
                model.errorMessage = model.appLanguage.userMessage(for: error)
            }
        }
    }

    private var composerInput: some View {
        VStack(alignment: .leading, spacing: ParallelCodexVisualStyle.Spacing.small) {
            NativeMultilineEditor(
                text: promptBinding,
                measuredHeight: $promptEditorHeight,
                minimumHeight: ParallelCodexVisualStyle.Composer.minimumEditorHeight,
                maximumHeight: ParallelCodexVisualStyle.Composer.maximumEditorHeight,
                fontSize: ParallelCodexVisualStyle.Composer.fontSize,
                accessibilityLabel: model.appLanguage.text(
                    "消息输入",
                    "Message"
                ),
                accessibilityPlaceholder: model.promptComposerPlaceholder,
                isInitialFirstResponder: true,
                focusRequestSequence: model.promptFocusRequestSequence,
                onSubmit: requestSubmit,
                onFocusChange: { promptEditorFocused = $0 },
                initialSelection: model.promptSelectionsByContext[model.promptDraftContext],
                onSelectionChange: selectionChanged
            )
            .id(model.promptDraftContext)
            .frame(height: promptEditorHeight)
            .frame(maxWidth: .infinity)
            .contextMenu { promptHistoryMenu }

            HStack(spacing: ParallelCodexVisualStyle.Spacing.medium) {
                runSettingsButton
                Spacer(minLength: ParallelCodexVisualStyle.Spacing.small)
                ComposerExecutionConfigurationSummary(isEditing: $configurationEditing)
                    .layoutPriority(1)
                submitButton
            }
            .accessibilityIdentifier("composer.footer")
        }
        .padding(.horizontal, ParallelCodexVisualStyle.Spacing.small)
        .padding(.top, ParallelCodexVisualStyle.Spacing.medium)
        .padding(.bottom, ParallelCodexVisualStyle.Spacing.small)
        .background(
            ParallelCodexVisualStyle.composerSurface,
            in: RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.composer,
                style: .continuous
            )
        )
        .overlay {
            RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.composer,
                style: .continuous
            )
            .stroke(
                promptEditorFocused
                    ? ParallelCodexVisualStyle.composerFocusStroke
                    : ParallelCodexVisualStyle.composerStroke,
                lineWidth: 1
            )
        }
        .animation(
            reduceMotion ? nil : .easeOut(duration: ParallelCodexVisualStyle.Motion.quick),
            value: promptEditorFocused
        )
        .accessibilityIdentifier("composer.input")
    }

    private var runSettingsButton: some View {
        Menu {
            Button {
                model.settingsPresented = true
            } label: {
                Label(model.appLanguage.text("运行设置", "Run Settings"), systemImage: "slider.horizontal.3")
            }
            .disabled(model.isRunOperationInProgress)
            Divider()
            promptHistoryMenu
        } label: {
            Image(systemName: "plus")
                .appScaledSystemFont(size: 16, weight: .regular)
                .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .tint(.secondary)
        .frame(
            width: ParallelCodexVisualStyle.Composer.controlSize * appTextSize.scale,
            height: ParallelCodexVisualStyle.Composer.controlSize * appTextSize.scale
        )
        .accessibilityIdentifier("composer.more-options")
        .accessibilityLabel(model.appLanguage.text(
            "更多输入选项",
            "More input options"
        ))
        .help(model.appLanguage.text("更多输入选项", "More input options"))
    }

    @ViewBuilder
    private var promptHistoryMenu: some View {
        Group {
            Button {
                model.navigatePromptHistory(-1)
            } label: {
                Label(
                    model.appLanguage.text("上一条输入", "Previous Prompt"),
                    systemImage: "chevron.up"
                )
            }
            .keyboardShortcut("p", modifiers: .control)
            .disabled(!model.canNavigateToOlderPrompt)

            Button {
                model.navigatePromptHistory(1)
            } label: {
                Label(
                    model.appLanguage.text("下一条输入", "Next Prompt"),
                    systemImage: "chevron.down"
                )
            }
            .keyboardShortcut("n", modifiers: .control)
            .disabled(!model.canNavigateToNewerPrompt)

            Divider()

            Button {
                promptFilePresented = true
            } label: {
                Label(
                    model.appLanguage.text("从文件载入输入", "Load Prompt from File"),
                    systemImage: "doc.badge.plus"
                )
            }
        }
    }

    private var submitButton: some View {
        Button {
            switch composerAction.kind {
            case .stop: requestStop()
            case .send, .queue: requestSubmit()
            case .sending: break
            }
        } label: {
            ComposerActionSymbol(kind: composerAction.kind)
        }
        .buttonStyle(ComposerActionButtonStyle())
        .disabled(!composerAction.enabled)
        .accessibilityLabel(primaryActionHelp)
        .accessibilityIdentifier("composer.primary-action")
        .help(primaryActionHelp)
    }

    private var primaryActionHelp: String {
        if case .stop = composerAction.kind { return stopActionHelp }
        if composerAction.kind == .sending {
            return model.pendingPromptSubmission?.status == .opening
                ? model.appLanguage.text("正在打开", "Opening")
                : model.appLanguage.text("正在发送", "Sending")
        }
        return model.promptSubmissionActionDescription
            ?? (model.activeRun?.isWorking == true
                ? model.appLanguage.text("加入队列", "Queue follow-up")
                : model.appLanguage.text("开始并行任务", "Start parallel task"))
    }

    private var runIsStoppable: Bool {
        guard model.isViewingLatestWorkspaceRun,
              model.isViewingLatestConversationTurn,
              !model.isComposingNewConversation,
              model.activeRun?.isWorking == true else { return false }
        return true
    }

    private var canStopRun: Bool {
        model.daemonOnline && !model.isRunOperationInProgress
            && model.pendingAgentSelection == nil
            && !model.remoteTurnTransitionInProgress
    }

    private var composerAction: ComposerPrimaryAction {
        ComposerPrimaryAction.make(
            hasDraft: !model.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            canSend: model.canSubmitPrompt,
            runIsStoppable: runIsStoppable,
            queuesMessage: model.promptSubmissionPlan == .enqueue,
            canStop: canStopRun,
            submissionInFlight: false
        )
    }

    private var stopActionHelp: String {
        model.appLanguage.text("停止本轮回复并暂停队列", "Stop this turn and pause the queue")
    }

    private func requestStop() {
        guard canStopRun, runIsStoppable else { return }
        Task { await model.stopCurrentRun() }
    }

    private func requestSubmit() {
        guard !configurationEditing, model.canSubmitPrompt else { return }
        if model.promptSubmissionStopsRemainingAgents {
            stopAndContinueConfirmation = true
        } else {
            Task { await model.submitPrompt() }
        }
    }

    @ViewBuilder
    private var recoveryAction: some View {
        switch model.promptComposerRecoveryAction {
        case .openLatestWorkspaceTurn:
            Button(model.appLanguage.text("查看工作区当前任务", "View Workspace Task")) {
                Task { await model.openLatestWorkspaceConversation() }
            }
            .buttonStyle(.borderless)
        case .openLatestConversationTurn:
            Button(model.appLanguage.text("打开最新一轮", "Open Latest Turn")) {
                Task { await model.openLatestSelectedConversationTurn() }
            }
            .buttonStyle(.borderless)
        case .discardCurrentTurn:
            Button(model.appLanguage.text("丢弃本轮", "Discard Turn")) {
                discardConfirmation = true
            }
                .buttonStyle(.borderless)
        case .startNewConversation:
            Button(model.appLanguage.text("新对话", "New Conversation")) {
                Task { await model.beginNewConversation() }
            }
            .buttonStyle(.borderless)
        case nil:
            EmptyView()
        }
    }
}

func promptSubmissionPresentation(
    _ rawStatus: String,
    language: AppLanguage,
    needsConnectionRecovery: Bool = false,
    errorCode: String? = nil
) -> (title: String, icon: String, color: Color)? {
    if needsConnectionRecovery {
        return (language.text("请重新连接设备", "Reconnect the device"), "link", .orange)
    }
    if errorCode == "prompt_destination_conflict" {
        return (language.text("任务身份校验失败", "Task identity check failed"), "exclamationmark.shield", .red)
    }
    switch rawStatus {
    case PromptSubmissionStatus.awaitingConfirmation.rawValue:
        return (language.text("等待容量确认，尚未开始下一轮", "Storage confirmation needed; next turn not started"),
                "externaldrive.badge.exclamationmark", .orange)
    case PromptSubmissionStatus.failed.rawValue:
        return (
            language.text("发送失败", "Could not submit"),
            "exclamationmark.circle.fill",
            .red
        )
    default:
        return nil
    }
}

func promptSubmissionDetail(
    _ submission: PersistedPromptSubmission,
    language: AppLanguage
) -> String {
    if submission.status == .awaitingConfirmation {
        if let storage = submission.storageEstimate {
            let total = storage.total ?? storage.totalBytes.map { ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .binary) }
            let free = storage.free ?? storage.freeBytes.map { ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .binary) }
            if let total, let free {
                if storage.fits == false {
                    return language.text(
                        "预计需要 \(total)，当时可用 \(free)，空间不足。原消息尚未启动；腾出空间后可重新检查。",
                        "Estimated \(total); \(free) available at the check, which is insufficient. Your message has not started; free space before checking again.")
                }
                let threshold = storage.warningThreshold ?? storage.warningThresholdBytes.map {
                    ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .binary)
                }
                if storage.fits == true, let threshold {
                    return language.text(
                        "预计需要 \(total)，超过 \(threshold) 的容量确认阈值。当时可用 \(free)，空间足够。原消息尚未启动，确认后将重新检查容量并继续。",
                        "Estimated \(total), above the \(threshold) storage confirmation threshold. \(free) was available, which is sufficient. Your message has not started; confirmation rechecks storage before continuing.")
                }
                return language.text(
                    "预计需要 \(total)，当时可用 \(free)。原消息未启动下一轮，确认后将重新检查容量。",
                    "Estimated \(total); \(free) available at the check. No next turn was started. Confirmation rechecks storage."
                )
            }
        }
        return language.text(
            "下一轮需要较多本机空间，尚未启动。原消息和续问来源已保留，确认后才会继续。",
            "The next turn needs substantial local storage and has not started. The original message and source are retained until you confirm."
        )
    }
    if submission.status == .openingFailed {
        if submission.errorCode == "prompt_destination_conflict" {
            return language.text("原消息和任务记录已保留。目标身份不一致，后续消息未继续执行。",
                "The original message and task record are retained. Their identities do not match; later messages have not been dispatched.")
        }
        return language.text(
            submission.needsConnectionRecovery ? "消息已发送。重新连接设备后将自动打开。"
                : "消息已发送，任务记录已保留。可以在最近任务中查看。",
            submission.needsConnectionRecovery ? "Your message was sent. It will open after the device reconnects."
                : "Your message was sent and its task record is retained. You can view it in recent tasks."
        )
    }
    if submission.needsConnectionRecovery {
        return language.text(
            submission.allowsManualRecovery ? "请重新连接设备，消息内容仍保留。"
                : "接收状态尚未确认，内容已保留。重新连接设备后将自动确认。",
            submission.allowsManualRecovery ? "Reconnect the device. Your message is retained."
                : "Receipt is not yet confirmed. Your message is retained and will be checked after reconnection."
        )
    }
    if !submission.allowsManualRecovery {
        if submission.status == .receiptUnresolved {
            return language.text(
                "暂时无法确认接收，已暂停再次发送。应用会在后台核对原消息。",
                "Delivery cannot be confirmed yet. Sending is held while the app checks the original message in the background."
            )
        }
        return language.text(
            "消息已保存，应用会自动核对发送结果，不会重复创建任务。",
            "Your message is saved. The app checks delivery automatically without creating a duplicate task."
        )
    }
    switch submission.errorCode {
    case "finalization_blocked":
        return language.text("上一轮的结果尚未安全保存，消息内容仍保留。",
                             "The previous result has not been saved safely. Your message is retained.")
    case "workspace_unavailable", "workspace_not_found":
        return language.text("工作区目前不可用，消息内容仍保留。",
                             "The workspace is unavailable. Your message is retained.")
    case "unknown_action", "unsupported_action", "incompatible_protocol":
        return language.text("请更新应用和连接设备上的服务，消息内容仍保留。",
                             "Update the app and the connected device's service. Your message is retained.")
    default:
        return language.text("消息尚未发送，内容仍保留。可以编辑后再发送。",
                             "The message was not sent and is retained. You can edit it and send again.")
    }
}
