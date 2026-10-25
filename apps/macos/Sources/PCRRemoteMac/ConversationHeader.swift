import SwiftUI

struct ConversationHeader<Actions: View>: View {
    @EnvironmentObject private var model: AppModel
    @State private var titleHovered = false
    var leadingInset: CGFloat = 24
    @ViewBuilder let actions: () -> Actions

    var body: some View {
        HStack(spacing: 12) {
            Button { model.requestConversationRename() } label: {
                HStack(spacing: 8) {
                    Text(title)
                        .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.title)
                        .foregroundStyle(ParallelCodexVisualStyle.primaryText)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Image(systemName: "pencil")
                        .appScaledSystemFont(size: 12)
                        .foregroundStyle(.secondary)
                        .opacity(titleHovered && model.selectedConversation != nil ? 1 : 0)
                }
                .padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(model.selectedConversation == nil)
            .help(model.appLanguage.text("重命名对话", "Rename Conversation") + ": " + title)
            .accessibilityIdentifier("conversation.title")
            .accessibilityLabel(title)
            .accessibilityHint(model.appLanguage.text("重命名对话", "Rename Conversation"))
            .onHover { titleHovered = $0 }
            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
            let allWaiting = model.activeRun.map { run in
                let running = run.agents.filter(\.isRunning)
                return run.workerActive && run.normalizedStatus == "running"
                    && !running.isEmpty && running.allSatisfy(\.isObservedWaiting)
                    && model.daemonOnline && model.connectionErrorMessage == nil
                    && model.liveConversationWarning == nil && !model.machineSelectionInProgress
                    && model.pendingPromptSubmission == nil
                    && model.isViewingLatestConversationTurn
            } ?? false
            let status = allWaiting ? model.appLanguage.text("定时等待", "Waiting")
                : model.conversationSurfaceState?.toolbarStatusLabel(language: model.appLanguage)
            let executionActive = ExecutionActivityState.isWorking(
                run: model.activeRun,
                isLatestTurn: model.isViewingLatestConversationTurn,
                hasPendingSubmission: model.pendingPromptSubmission != nil,
                connectionConfirmed: model.daemonOnline && model.connectionErrorMessage == nil
                    && model.liveConversationWarning == nil && !model.machineSelectionInProgress
            )
            HStack(spacing: 6) {
                ExecutionActivityIndicator(isWorking: executionActive)
                    .frame(width: 12 * model.appTextSize.scale, height: 12 * model.appTextSize.scale)
                    .opacity(executionActive ? 1 : 0)
                    .accessibilityHidden(true)
                Text(status ?? "")
                    .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.caption)
                    .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
                    .lineLimit(1)
            }
            .frame(width: 118 * model.appTextSize.scale, alignment: .trailing)
            .accessibilityHidden(status == nil)
            .transaction { $0.animation = nil }
            .layoutPriority(1)
            actions().fixedSize().layoutPriority(1)
        }
        .padding(.leading, leadingInset)
        .padding(.trailing, 24)
        .frame(minHeight: ConversationWindowChrome.headerHeight)
        .background(ParallelCodexVisualStyle.canvas)
        .background(ConversationWindowDragRegion())
        .overlay(alignment: .bottom) {
            Rectangle().fill(ParallelCodexVisualStyle.separator).frame(height: 0.5)
        }
    }

    private var title: String {
        let value = model.conversationSurfaceState?.title ?? AppIdentity.productName
        return value == "Untitled conversation"
            ? model.appLanguage.text("未命名对话", "Untitled Conversation") : value
    }
}

struct ConversationRenameView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool
    let request: ConversationRenameRequest
    @State private var title: String
    @State private var saving = false
    @State private var error: String?

    init(request: ConversationRenameRequest) {
        self.request = request
        _title = State(initialValue: request.conversation.title)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(model.appLanguage.text("重命名对话", "Rename Conversation"))
                .appScaledSystemFont(size: 17, weight: .semibold)
            TextField(model.appLanguage.text("对话名称", "Conversation Name"), text: $title)
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .disabled(saving)
                .accessibilityIdentifier("conversation.rename.title")
                .onSubmit { save() }
            if ConversationTitles.normalized(title).unicodeScalars.count > 200 {
                Text(model.appLanguage.text("名称最多 200 个字符", "Names can contain up to 200 characters"))
                    .foregroundStyle(.secondary).appScaledTextStyle(.callout)
            }
            if let error {
                Text(error).foregroundStyle(.red).appScaledTextStyle(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button(model.appLanguage.text("取消", "Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction).disabled(saving)
                Button(model.appLanguage.text("保存", "Save"), action: save)
                    .keyboardShortcut(.defaultAction).disabled(!valid || saving)
            }
        }
        .padding(24).frame(width: 420)
        .onAppear { focused = true }
    }

    private var valid: Bool {
        let normalized = ConversationTitles.normalized(title)
        return !normalized.isEmpty && normalized.unicodeScalars.count <= 200
    }

    private func save() {
        guard valid, !saving else { return }
        saving = true
        error = nil
        Task {
            do {
                try await model.renameConversation(request, title: title)
                dismiss()
            } catch {
                self.error = error.localizedDescription
                saving = false
            }
        }
    }
}
