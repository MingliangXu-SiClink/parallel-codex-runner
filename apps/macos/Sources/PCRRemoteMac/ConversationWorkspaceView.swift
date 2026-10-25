import SwiftUI

struct WorkspaceWorkbench: View {
    var headerLeadingInset: CGFloat = 24
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var discardConfirmation = false
    @State private var conversationFindPresented = false
    @State private var conversationFindCommandRequest = ConversationFindCommandRequest.idle
    @State private var inlineAgentSwitchPosition = TranscriptInlineControlPosition.visible

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                ConversationHeader(leadingInset: headerLeadingInset) {
                    if let workspace = model.selectedWorkspace {
                        ConversationToolbar(
                            workspace: workspace,
                            resultsInspectorVisible: $model.resultsInspectorVisible,
                            discard: { discardConfirmation = true },
                            findInConversation: { requestConversationFind(.open) }
                        )
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                .zIndex(1)
                if let workspace = model.selectedWorkspace {
                    conversationBody(
                        workspace: workspace,
                        availableWidth: geometry.size.width
                    )
                } else if model.connectionLoading {
                    VStack(spacing: 12) {
                        ProgressView()
                            .controlSize(.large)
                        Text(model.appLanguage.text("正在连接设备", "Connecting to your computer"))
                            .appScaledTextStyle(.headline)
                        Text(model.appLanguage.text(
                            "正在加载工作区和对话",
                            "Loading workspaces and conversations"
                        ))
                            .appScaledTextStyle(.body)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    EmptyState(
                        title: model.appLanguage.text("选择一个工作区", "Select a Workspace"),
                        systemImage: "folder"
                    )
                }
            }
            .clipped()
        }
        .background(ParallelCodexVisualStyle.canvas)
        .onAppear { model.refreshExpandedWorkOperations() }
        .onChange(of: model.selectedTranscriptBlocks) { _ in
            model.refreshExpandedWorkOperations()
        }
        .onChange(of: model.selectedWorkspaceID) { _ in
            conversationFindPresented = false
            inlineAgentSwitchPosition = .visible
        }
        .onChange(of: model.activeRun?.runId) { _ in
            conversationFindPresented = false
            inlineAgentSwitchPosition = .visible
        }
        .confirmationDialog(
            model.appLanguage.text("丢弃本轮任务？", "Discard this turn?"),
            isPresented: $discardConfirmation,
            titleVisibility: .visible
        ) {
            Button(model.appLanguage.text("丢弃本轮", "Discard Turn"), role: .destructive) {
                Task { await model.discardCurrentRun() }
            }
            Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {}
        } message: {
            Text(
                model.settings.keepWorkspaces
                    ? model.appLanguage.text(
                        "任务会关闭，但会保留候选工作区。",
                        "The turn will close, but candidate workspaces will be kept."
                    )
                    : model.appLanguage.text(
                        "候选工作区及运行数据会被清理。",
                        "Candidate workspaces and their run data will be cleaned up."
                    )
            )
        }
    }

    @ViewBuilder
    private func conversationBody(
        workspace: RemoteWorkspace,
        availableWidth: CGFloat
    ) -> some View {
        let scale = model.appTextSize.scale
        let showsReview = model.reviewColumnPresented
        let layout = WorkbenchColumnsLayout(
            availableWidth: availableWidth, scale: scale,
            showsResults: model.resultsInspectorVisible, showsReview: showsReview
        )

        GeometryReader { viewport in
            // Keep all three surfaces mounted. On small windows the columns
            // scroll horizontally instead of covering or replacing each other.
            WorkbenchHorizontalScroll(
                size: CGSize(width: layout.contentWidth, height: viewport.size.height),
                showsReview: showsReview
            ) {
                WorkbenchSplitColumns(
                    layout: layout,
                    showsResults: model.resultsInspectorVisible,
                    showsReview: showsReview,
                    conversation: { columnEnvironment(conversationColumn(workspace: workspace)) },
                    results: { columnEnvironment(
                        ConversationResultsInspector(
                            run: model.activeRun,
                            close: { model.resultsInspectorVisible = false }
                        )
                    ) },
                    review: { columnEnvironment(reviewContent) }
                )
            }
            .clipped()
        }
        .clipped()
        .focusedSceneValue(
            \.conversationFindActions,
            ConversationFindActions(
                isEnabled: !model.selectedTimelineItems.isEmpty,
                open: { requestConversationFind(.open) },
                next: { requestConversationFind(.next) },
                previous: { requestConversationFind(.previous) }
            )
        )
        .focusedSceneValue(
            \.conversationRunActions,
            ConversationRunActions(
                canSwitchAgent: (model.activeRun?.agents.count ?? 0) > 1
                    && !model.settingsPresented && !model.addAgentsPresented,
                canStop: model.activeRun?.isWorking == true
                    && !model.isRunOperationInProgress
                    && !model.settingsPresented && !model.addAgentsPresented,
                selectPreviousAgent: { Task { await model.selectAdjacentAgent(-1) } },
                selectNextAgent: { Task { await model.selectAdjacentAgent(1) } },
                stop: { Task { await model.stopCurrentRun() } }
            )
        )
    }

    private func columnEnvironment<Content: View>(_ content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .environmentObject(model)
            .environment(\.locale, model.appLanguage.locale)
            .environment(\.appLanguage, model.appLanguage)
            .environment(\.appTextSize, model.appTextSize)
            .environment(\.appAccentColor, model.appAccentColor)
            .environment(\.dynamicTypeSize, model.appTextSize.dynamicTypeSize)
            .preferredColorScheme(model.appColorMode.colorScheme)
            .tint(model.appAccentColor.color)
    }

    @ViewBuilder private var reviewContent: some View {
        VStack(spacing: 0) {
        if let issue = model.workspaceFileIssue {
            WorkspaceFileIssueView(issue: issue)
            Divider()
        }
        if let document = model.workspaceDocument {
            WorkspaceDocumentView(document: document).id(document.id)
        } else if model.diffPresented {
            AgentDiffView()
        } else {
            Spacer(minLength: 0)
        }
        }
        .overlay(alignment: .top) {
            if model.workspaceFileLoading {
                ProgressView(value: model.workspaceFileProgress)
                    .progressViewStyle(.linear)
                    .accessibilityLabel(model.appLanguage.text("正在打开文件", "Opening file"))
            }
        }
    }

    private func conversationColumn(workspace: RemoteWorkspace) -> some View {
        VStack(spacing: 0) {
            AgentTranscriptView(
                questionAnswers: model.questionAnswers,
                workspace: workspace,
                resultsInspectorVisible: $model.resultsInspectorVisible,
                findPresented: $conversationFindPresented,
                findCommandRequest: conversationFindCommandRequest,
                inlineAgentSwitchPosition: $inlineAgentSwitchPosition
            )
            PromptComposer()
            // Keep the editor in one stable position in the view hierarchy.
            // Reparenting it for the empty state would reset marked text/focus.
            Spacer(minLength: 0)
                .frame(maxHeight: centersInitialComposer ? .infinity : 0)
                .animation(
                    reduceMotion ? nil : .easeInOut(duration: ParallelCodexVisualStyle.Motion.standard),
                    value: centersInitialComposer
                )
        }
        .overlay(alignment: .top) {
            ConversationTransientNotices(
                warning: model.liveConversationWarning,
                error: model.errorMessage
            )
            // Keep transient notices below the Agent switcher without taking
            // space away from the transcript. Inline banners used to resize
            // the collection view and visibly move the reader's position.
            .padding(
                .top,
                showsStickyAgentSwitcher ? stickyAgentSwitcherHeight + 2 : 10
            )
            .zIndex(4)
        }
    }

    private var centersInitialComposer: Bool {
        model.isComposingNewConversation
            && model.activeRun == nil
            && model.pendingPromptSubmission == nil
            && model.selectedTimelineItems.isEmpty
            && model.followUpQueue.isEmpty
            && !model.isSelectedWorkspaceContextLoading
    }

    private var showsStickyAgentSwitcher: Bool {
        (model.currentConversationAgentSwitchState?.agents.count ?? 0) > 1
            && inlineAgentSwitchPosition == .aboveViewport
    }

    private var stickyAgentSwitcherHeight: CGFloat {
        ConversationAgentSwitchBar.stickyHeight(for: model.appTextSize)
    }

    private func requestConversationFind(_ command: ConversationFindCommand) {
        guard !model.selectedTimelineItems.isEmpty else { return }
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            conversationFindPresented = true
        }
        conversationFindCommandRequest = conversationFindCommandRequest.advanced(
            to: command
        )
    }

}

// SwiftUI's horizontal ScrollView extends under a unified titlebar even when
// nested below our header. Own the native viewport so each column starts below it.
struct WorkbenchHorizontalScroll<Content: View>: NSViewRepresentable {
    let size: CGSize
    var showsReview = false
    @ViewBuilder let content: Content

    func makeNSView(context: Context) -> WorkbenchColumnScrollView {
        let scroll = WorkbenchColumnScrollView()
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasHorizontalScroller = true
        scroll.hasVerticalScroller = false
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.automaticallyAdjustsContentInsets = false
        let host = NSHostingView(rootView: content)
        host.sizingOptions = []
        if #available(macOS 13.3, *) { host.safeAreaRegions = [] }
        host.frame = NSRect(origin: .zero, size: size)
        scroll.documentView = host
        scroll.updateReviewVisibility(showsReview)
        return scroll
    }

    func updateNSView(_ scroll: WorkbenchColumnScrollView, context: Context) {
        guard let host = scroll.documentView as? NSHostingView<Content> else { return }
        host.rootView = content
        host.setFrameSize(size)
        scroll.updateReviewVisibility(showsReview)
    }
}

final class WorkbenchColumnScrollView: NSScrollView {
    private var showsReview = false
    private var revealPending = false
    private let scrollMotion = NativeScrollMotion()

    override func scrollWheel(with event: NSEvent) {
        scrollMotion.cancel()
        super.scrollWheel(with: event)
    }

    func updateReviewVisibility(_ visible: Bool) {
        if visible && !showsReview { revealPending = true }
        if !visible { revealPending = false }
        showsReview = visible
        needsLayout = true
    }

    override func layout() {
        super.layout()
        guard contentSize.width > 0, let documentView else { return }
        let maximumX = max(0, documentView.frame.width - contentSize.width)
        let targetX = revealPending ? maximumX : min(maximumX, max(0, contentView.bounds.minX))
        let reveal = revealPending
        revealPending = false
        // A newly opened file must be visible even when three columns do not fit.
        // Closing a pane also releases its previous horizontal scroll offset.
        guard abs(contentView.bounds.minX - targetX) > 0.5 else { return }
        scrollMotion.move(to: NSPoint(x: targetX, y: 0), in: self, animated: reveal)
    }
}

struct WorkbenchColumnsLayout {
    let conversationMinimum: CGFloat
    let resultsMinimum: CGFloat
    let reviewMinimum: CGFloat
    let contentWidth: CGFloat

    init(availableWidth: CGFloat, scale: CGFloat, showsResults: Bool, showsReview: Bool) {
        conversationMinimum = 360 * scale
        resultsMinimum = 260 * scale
        reviewMinimum = 320 * scale
        let minimum = conversationMinimum
            + (showsResults ? resultsMinimum + 1 : 0)
            + (showsReview ? reviewMinimum + 1 : 0)
        contentWidth = max(availableWidth, minimum)
    }
}

private struct ConversationTransientNotices: View {
    let warning: String?
    let error: String?

    private var hasContent: Bool {
        warning?.isEmpty == false || error?.isEmpty == false
    }

    var body: some View {
        if hasContent {
            VStack(spacing: ParallelCodexVisualStyle.Spacing.small) {
                if let warning, !warning.isEmpty {
                    LiveConversationWarning(message: warning)
                }
                if let error, !error.isEmpty {
                    ConversationOperationNotice(message: error)
                }
            }
            .frame(maxWidth: ParallelCodexVisualStyle.Layout.transcriptMaximumWidth)
            .padding(.horizontal, ParallelCodexVisualStyle.Spacing.xlarge)
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}

private struct ConversationOperationNotice: View {
    @EnvironmentObject private var model: AppModel
    let message: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(Color.orange)
                .padding(.top, 1)
            Text(message)
                .appScaledTextStyle(.callout)
                .foregroundStyle(.primary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                model.errorMessage = nil
            } label: {
                Image(systemName: "xmark")
                    .appScaledTextStyle(.caption, weight: .semibold)
            }
            .buttonStyle(.borderless)
            .help(model.appLanguage.text("关闭", "Dismiss"))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            ParallelCodexVisualStyle.subtleSurface,
            in: RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.panel,
                style: .continuous
            )
        )
        .overlay {
            RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.panel,
                style: .continuous
            )
            .stroke(ParallelCodexVisualStyle.separator, lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.09), radius: 10, y: 4)
    }
}

private struct LiveConversationWarning: View {
    @EnvironmentObject private var model: AppModel
    let message: String

    var body: some View {
        HStack(spacing: 9) {
            Label(message, systemImage: "wifi.exclamationmark")
                .appScaledTextStyle(.caption)
                .foregroundStyle(.secondary)
            Spacer(minLength: 8)
            if model.liveConversationNeedsAction {
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
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(
            ParallelCodexVisualStyle.subtleSurface,
            in: RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.panel,
                style: .continuous
            )
        )
        .overlay {
            RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.panel,
                style: .continuous
            )
            .stroke(ParallelCodexVisualStyle.separator, lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.08), radius: 10, y: 4)
        .help(message)
    }
}

private struct ConversationToolbar: View {
    @EnvironmentObject private var model: AppModel
    let workspace: RemoteWorkspace
    @Binding var resultsInspectorVisible: Bool
    let discard: () -> Void
    let findInConversation: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            conversationMenu.buttonStyle(.borderless).help(workspace.path)
            if let state = model.currentConversationAgentSwitchState, state.agents.count > 1 {
                adjacentAgentButton(state: state, delta: -1)
                adjacentAgentButton(state: state, delta: 1)
            }
            Button { resultsInspectorVisible.toggle() } label: {
                Image(systemName: "sidebar.right")
                    .foregroundStyle(resultsInspectorVisible ? Color.primary : .secondary)
                    .appScaledSystemFont(size: 14)
                    .frame(width: 30 * model.appTextSize.scale, height: 30 * model.appTextSize.scale)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(model.activeRun == nil)
            .help(model.appLanguage.text("显示或隐藏结果详情", "Toggle result details"))
            .accessibilityLabel(model.appLanguage.text("结果详情", "Result details"))
            .accessibilityValue(resultsInspectorVisible
                ? model.appLanguage.text("已显示", "Visible")
                : model.appLanguage.text("已隐藏", "Hidden"))
            .accessibilityIdentifier("conversation.results")
        }
    }

    private func adjacentAgentButton(state: ConversationAgentSwitchState, delta: Int) -> some View {
        let target = state.adjacentAgent(delta: delta)
        let title = delta < 0 ? model.appLanguage.text("上一个 Agent", "Previous Agent")
            : model.appLanguage.text("下一个 Agent", "Next Agent")
        return Button {
            if let target { Task { await model.selectAgent(target) } }
        } label: {
            Image(systemName: delta < 0 ? "chevron.left" : "chevron.right")
                .appScaledSystemFont(size: 13)
                .frame(width: 28 * model.appTextSize.scale, height: 30 * model.appTextSize.scale)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(target == nil || !state.selectionEnabled || target == state.pendingAgent)
        .help(title)
        .accessibilityLabel(title)
        .accessibilityIdentifier(delta < 0 ? "conversation.previous-agent" : "conversation.next-agent")
    }

    private var conversationMenu: some View {
        Menu {
            Button {
                model.requestConversationRename()
            } label: {
                Label(model.appLanguage.text("重命名", "Rename"), systemImage: "pencil")
            }
            .disabled(model.selectedConversation == nil)
            Divider()
            Button(action: findInConversation) {
                Label(
                    model.appLanguage.text(
                        "在当前对话中查找",
                        "Find in Conversation"
                    ),
                    systemImage: "magnifyingglass"
                )
            }
            .disabled(model.selectedTimelineItems.isEmpty)

            Divider()

            Button {
                Task { await model.refreshCurrentRun() }
            } label: {
                Label(
                    model.appLanguage.text("刷新对话", "Refresh Conversation"),
                    systemImage: "arrow.clockwise"
                )
            }
            .disabled(model.isRunOperationInProgress)

            if model.activeRun != nil {
                Button {
                    ConversationClipboard.copyConversation(model.selectedTranscriptBlocks)
                } label: {
                    Label(
                        model.appLanguage.text("复制对话", "Copy Conversation"),
                        systemImage: "doc.on.doc"
                    )
                }
                .disabled(model.selectedTranscriptBlocks.isEmpty)
            }

            if model.activeRun?.isWorking == true {
                Divider()
                Button(role: .destructive) {
                    Task { await model.stopCurrentRun() }
                } label: {
                    Label(
                        model.appLanguage.text("停止当前回合", "Stop Current Turn"),
                        systemImage: "stop.fill"
                    )
                }
                .disabled(model.isRunOperationInProgress)
            } else if let run = model.activeRun,
                      model.isViewingLatestWorkspaceRun,
                      run.finalizedAgent == nil,
                      !["finalized", "discarded"].contains(run.normalizedStatus) {
                Divider()
                Button(role: .destructive, action: discard) {
                    Label(
                        model.appLanguage.text("丢弃本轮", "Discard Turn"),
                        systemImage: "trash"
                    )
                }
                .disabled(model.isRunOperationInProgress)
            }
            Divider()
            if let record = model.visibleDeletedConversation {
                Button {
                    do { try model.restoreDeletedConversation(record) }
                    catch { model.errorMessage = model.appLanguage.userMessage(for: error) }
                } label: {
                    Label(model.appLanguage.text("恢复对话", "Restore Conversation"), systemImage: "arrow.uturn.backward")
                }
            } else {
                Button(role: .destructive) { model.requestConversationDelete() } label: {
                    Label(model.appLanguage.text("删除对话…", "Delete Conversation…"), systemImage: "trash")
                }
                .disabled(model.selectedConversation == nil)
            }
        } label: {
            Image(systemName: "ellipsis")
                .appScaledSystemFont(size: 15, weight: .medium)
                .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .tint(ParallelCodexVisualStyle.secondaryText)
        .frame(
            width: ParallelCodexVisualStyle.Control.standard,
            height: ParallelCodexVisualStyle.Control.standard
        )
        .help(model.appLanguage.text("对话操作", "Conversation actions"))
        .accessibilityLabel(model.appLanguage.text("对话操作", "Conversation actions"))
        .accessibilityIdentifier("conversation.actions")
    }

}

struct AgentTranscriptView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.appTextSize) private var appTextSize
    @State private var followsLatestByContext: [String: Bool] = [:]
    @State private var jumpToLatestRequest = 0
    @State private var findQuery = ""
    @State private var findSelection = 0
    @State private var findNavigationRequest = 0
    @State private var findFocusRestoration: ConversationSearchFocus?
    @FocusState private var findFieldFocused: Bool
    @ObservedObject var questionAnswers: AgentAnswerOutbox
    let workspace: RemoteWorkspace
    @Binding var resultsInspectorVisible: Bool
    @Binding var findPresented: Bool
    let findCommandRequest: ConversationFindCommandRequest
    @Binding var inlineAgentSwitchPosition: TranscriptInlineControlPosition

    private var stickyAgentSwitcherHeight: CGFloat {
        ConversationAgentSwitchBar.stickyHeight(for: appTextSize)
    }

    private var visibleConversationContextID: String {
        transcriptConversationContextID(
            selectedConversationID: model.selectedConversationID,
            activeRunID: model.activeRun?.runId,
            workspaceLastRunID: workspace.lastRunId
        )
    }

    private var restoredAgentContext: Int? {
        let run = model.activeRun ?? model.selectedConversation.flatMap { conversation in
            conversation.runs.first { $0.runId == conversation.latestRunID }
        }
        if let run {
            let selectionKey = model.machineRunContextKey(
                workspaceID: workspace.workspaceId,
                runID: run.runId
            )
            let rememberedAgent = run.isWorking
                || !model.selectedAgentContextsWhileWorking.contains(selectionKey)
                ? model.selectedAgentsByContext[selectionKey]
                : nil
            if let restored = selectedAgentForRestoredRun(
                explicitSelection: rememberedAgent,
                cachedSelection: nil,
                run: run
            ) {
                return restored
            }
        }
        let conversationContext = "\(model.currentMachineContextID)\u{1f}\(workspace.workspaceId)\u{1f}\(visibleConversationContextID)"
        return model.transcriptScrollMemory.mostRecentlyUsedAgent(
            for: conversationContext,
            runID: visibleRunContextID
        )
    }

    private var visibleRunContextID: String {
        model.activeRun?.runId ?? model.selectedConversation?.latestRunID ?? "history"
    }

    private var scrollContext: String {
        let agent = transcriptAgentContextID(
            selectedAgent: model.selectedAgent,
            restoredAgent: restoredAgentContext
        )
        return transcriptScrollContextID(
            machineID: model.currentMachineContextID,
            workspaceID: workspace.workspaceId,
            conversationID: visibleConversationContextID,
            runID: visibleRunContextID,
            agentID: agent
        )
    }

    private var followsLatest: Bool {
        transcriptFollowsLatest(
            liveValue: followsLatestByContext[scrollContext],
            persistedValue: model.transcriptScrollMemory.snapshot(for: scrollContext)?.followsLatest
        )
    }

    private var followsLatestBinding: Binding<Bool> {
        let context = scrollContext
        return Binding(
            get: {
                transcriptFollowsLatest(
                    liveValue: followsLatestByContext[context],
                    persistedValue: model.transcriptScrollMemory.snapshot(for: context)?.followsLatest
                )
            },
            set: { followsLatestByContext[context] = $0 }
        )
    }

    private var historyContext: String {
        "\(model.currentMachineContextID)\u{1f}\(workspace.workspaceId)\u{1f}\(visibleConversationContextID)"
    }

    private var findMatches: [ConversationFindMatch] {
        ConversationFindIndex.matches(
            in: model.selectedTimelineItems,
            query: findQuery
        )
    }

    private var selectedFindMatch: ConversationFindMatch? {
        guard !findMatches.isEmpty else { return nil }
        return findMatches[
            ConversationFindIndex.normalizedSelection(
                findSelection,
                count: findMatches.count
            )
        ]
    }

    var body: some View {
        let linkTarget = model.client.currentTarget()
        let linkConversation = model.selectedConversationID
        if model.activeRun == nil,
           model.pendingPromptSubmission == nil,
           model.selectedTimelineItems.isEmpty {
            EmptyWorkspaceRunView(
                workspace: workspace,
                loading: model.isSelectedWorkspaceContextLoading,
                serverOnline: model.daemonOnline,
                composingNewConversation: model.isComposingNewConversation
            )
        } else {
            ZStack(alignment: .bottom) {
                ConversationTimelineView(
                    language: model.appLanguage,
                    textSize: model.appTextSize,
                    accentColor: model.appAccentColor,
                    appearance: TranscriptAppearance(colorScheme),
                    contextID: scrollContext,
                    items: model.selectedTimelineItems,
                    contentIsLoading: model.isSelectedTimelineLoading,
                    jumpToLatestRequest: jumpToLatestRequest,
                    findQuery: findPresented ? findQuery : "",
                    findMatch: findPresented ? selectedFindMatch : nil,
                    findNavigationRequest: findNavigationRequest,
                    memory: model.transcriptScrollMemory,
                    resultsInspectorVisible: resultsInspectorVisible,
                    openURL: { url, block in
                        model.openTranscriptLink(url, reviewContext: ReviewContext(
                            target: linkTarget, workspaceID: workspace.workspaceId,
                            runID: block.linkOrigin?.runID, agent: block.linkOrigin?.agent,
                            conversationID: linkConversation))
                    },
                    activateBlock: { block in
                        model.activateWorkBlock(block)
                    },
                    selectAgent: { agent in
                        Task { await model.selectAgent(agent) }
                    },
                    toggleResults: {
                        resultsInspectorVisible.toggle()
                    },
                    itemsRevision: model.conversationTimelineItemsProjectionGeneration,
                    questionActions: AgentQuestionActions.answering(
                        using: questionAnswers, target: linkTarget, workspaceID: workspace.workspaceId,
                        toggleDetails: { model.toggleQuestionDetails($0) }
                    ),
                    onUserNavigation: { model.workspaceEntryDefaultNavigationRequestID = nil },
                    isNearBottom: followsLatestBinding,
                    inlineAgentSwitchPosition: $inlineAgentSwitchPosition
                )

                if let switchState = model.currentConversationAgentSwitchState,
                   switchState.agents.count > 1,
                   inlineAgentSwitchPosition == .aboveViewport {
                    GeometryReader { proxy in
                        ConversationAgentSwitchBar(
                            language: model.appLanguage,
                            textSize: model.appTextSize,
                            accentColor: model.appAccentColor.color,
                            state: switchState,
                            resultsInspectorVisible: resultsInspectorVisible,
                            accessibilityHidden: false,
                            selectAgent: { agent in
                                Task { await model.selectAgent(agent) }
                            },
                            toggleResults: {
                                resultsInspectorVisible.toggle()
                            }
                        )
                        .padding(.horizontal, 14)
                        .frame(
                            maxWidth: ParallelCodexVisualStyle.Layout.transcriptMaximumWidth
                        )
                        .padding(.vertical, 8)
                        .frame(width: proxy.size.width)
                        .background(ParallelCodexVisualStyle.canvas)
                        .overlay(alignment: .bottom) {
                            Rectangle()
                                .fill(ParallelCodexVisualStyle.separator)
                                .frame(height: 1)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    .zIndex(2)
                }

                if !followsLatest {
                    Button {
                        jumpToLatestRequest += 1
                    } label: {
                        Image(systemName: "arrow.down")
                            .appScaledSystemFont(size: 12, weight: .semibold)
                            .foregroundStyle(Color.primary.opacity(0.92))
                            .frame(width: 32, height: 32)
                            .background(
                                ParallelCodexVisualStyle.floatingControlSurface,
                                in: Circle()
                            )
                            .overlay {
                                Circle()
                                    .stroke(
                                        ParallelCodexVisualStyle.floatingControlStroke,
                                        lineWidth: 1
                                    )
                            }
                            .shadow(
                                color: .black.opacity(colorScheme == .dark ? 0.34 : 0.16),
                                radius: 7,
                                y: 3
                            )
                    }
                    .buttonStyle(.plain)
                    .contentShape(Circle())
                    .help(model.appLanguage.text("跳到最新内容", "Jump to latest"))
                    .accessibilityLabel(model.appLanguage.text("跳到最新内容", "Jump to latest"))
                    .accessibilityIdentifier("conversation.jump-to-latest")
                    .padding(.bottom, 8)
                }

                if findPresented {
                    conversationFindBar
                        .padding(
                            .top,
                            inlineAgentSwitchPosition == .aboveViewport
                                ? stickyAgentSwitcherHeight + 6
                                : 10
                        )
                        .padding(.trailing, 14)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                        .zIndex(5)
                }
            }
            .task(id: historyContext) {
                await model.ensureVisibleConversationHistory()
            }
            .task(id: model.conversationSearchNavigation?.id) {
                guard let request = model.conversationSearchNavigation,
                      request.machineID == model.currentMachineContextID,
                      request.workspaceID == model.selectedWorkspaceID,
                      request.conversationID == model.selectedConversationID else { return }
                // Result navigation transfers attention from the sidebar to this
                // conversation, not to the hosting view of the clicked result.
                findFocusRestoration = ConversationSearchFocus(
                    window: NSApp.keyWindow, restoringInitialResponder: true
                )
                findQuery = request.query
                findSelection = 0
                findNavigationRequest += 1
                openFind()
                model.conversationSearchNavigation = nil
            }
            .onChange(of: scrollContext) { _ in
                closeFind(restoringFocus: false)
            }
            .onChange(of: historyContext + "\u{1f}" + visibleRunContextID) { _ in
                // Wait for the collection view to measure the new inline
                // switcher before deciding whether its sticky copy is needed.
                inlineAgentSwitchPosition = .visible
            }
            .onChange(of: findCommandRequest) { request in
                guard request.sequence > 0 else { return }
                switch request.command {
                case .open:
                    focusFindField()
                case .next:
                    advanceFind(by: 1)
                case .previous:
                    advanceFind(by: -1)
                }
            }
            .onChange(of: findQuery) { _ in
                findSelection = 0
                findNavigationRequest += 1
            }
            .onChange(of: findMatches) { matches in
                findSelection = ConversationFindIndex.normalizedSelection(
                    findSelection,
                    count: matches.count
                )
            }
            .onChange(of: findPresented) { presented in
                if presented {
                    focusFindField()
                } else {
                    findFocusRestoration = nil
                    findFieldFocused = false
                    findQuery = ""
                    findSelection = 0
                }
            }
            .onDisappear { findFocusRestoration = nil }
        }
    }

    private var conversationFindBar: some View {
        HStack(spacing: 5) {
            Image(systemName: "magnifyingglass")
                .appScaledTextStyle(.caption)
                .foregroundStyle(.secondary)

            TextField(
                model.appLanguage.text("查找", "Find"),
                text: $findQuery
            )
            .textFieldStyle(.plain)
            .appScaledTextStyle(.callout)
            .focused($findFieldFocused)
            .onSubmit { advanceFind(by: 1) }

            Text(findResultLabel)
                .appScaledTextStyle(.caption, design: .monospaced)
                .foregroundStyle(.secondary)
                .frame(minWidth: 38 * appTextSize.scale, alignment: .trailing)

            findButton(
                systemImage: "chevron.up",
                help: model.appLanguage.text("上一个结果", "Previous Result")
            ) {
                advanceFind(by: -1)
            }
            findButton(
                systemImage: "chevron.down",
                help: model.appLanguage.text("下一个结果", "Next Result")
            ) {
                advanceFind(by: 1)
            }
            findButton(
                systemImage: "xmark",
                help: model.appLanguage.text("关闭查找", "Close Find")
            ) {
                closeFind()
            }
        }
        .padding(.horizontal, 9 * appTextSize.scale)
        .frame(
            width: 330 * appTextSize.scale,
            height: 34 * appTextSize.scale
        )
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(ParallelCodexVisualStyle.separator, lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
        .onExitCommand { closeFind() }
    }

    private var findResultLabel: String {
        guard !findQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return ""
        }
        guard !findMatches.isEmpty else { return "0/0" }
        let index = ConversationFindIndex.normalizedSelection(
            findSelection,
            count: findMatches.count
        )
        return "\(index + 1)/\(findMatches.count)"
    }

    private func findButton(
        systemImage: String,
        help: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .appScaledSystemFont(size: 10, weight: .semibold)
                .frame(
                    width: 22 * appTextSize.scale,
                    height: 22 * appTextSize.scale
                )
        }
        .buttonStyle(.plain)
        .disabled(findMatches.isEmpty && systemImage != "xmark")
        .help(help)
    }

    private func advanceFind(by delta: Int) {
        guard findPresented, !findMatches.isEmpty else {
            openFind()
            return
        }
        findSelection = ConversationFindIndex.advancedSelection(
            findSelection,
            count: findMatches.count,
            delta: delta
        )
        findNavigationRequest += 1
    }

    private func openFind() {
        findPresented = true
        focusFindField()
    }

    private func focusFindField() {
        if findFocusRestoration == nil {
            findFocusRestoration = ConversationSearchFocus(window: NSApp.keyWindow)
        }
        let context = scrollContext
        DispatchQueue.main.async {
            guard findPresented, scrollContext == context else { return }
            findFieldFocused = true
        }
    }

    private func closeFind(restoringFocus: Bool = true) {
        let restoration = findFocusRestoration
        let context = scrollContext
        let window = NSApp.keyWindow
        findFocusRestoration = nil
        findPresented = false
        findFieldFocused = false
        findQuery = ""
        findSelection = 0
        guard restoringFocus else { return }
        // Restore after SwiftUI removes the field, without moving the draft's
        // selection or sending focus into a different conversation/window.
        DispatchQueue.main.async {
            guard !findPresented, scrollContext == context,
                  NSApp.keyWindow === window else { return }
            restoration?.restore()
        }
    }
}

private struct EmptyWorkspaceRunView: View {
    @Environment(\.appLanguage) private var appLanguage
    let workspace: RemoteWorkspace
    let loading: Bool
    let serverOnline: Bool
    let composingNewConversation: Bool

    @ViewBuilder
    var body: some View {
        if loading {
            ProgressView()
                .controlSize(.small)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if !composingNewConversation, !workspace.lastRunId.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Label(
                    serverOnline
                        ? appLanguage.text(
                            "暂时无法打开这段对话",
                            "This conversation is temporarily unavailable"
                        )
                        : appLanguage.text(
                            "连接设备后可打开这段对话",
                            "Reconnect to open this conversation"
                        ),
                    systemImage: serverOnline ? "arrow.clockwise" : "wifi.slash"
                )
                .appScaledTextStyle(.headline)
                Text(serverOnline
                    ? appLanguage.text("请稍后重试。", "Try again shortly.")
                    : appLanguage.text("请在设置中检查连接。", "Check the connection in Settings."))
                    .appScaledTextStyle(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: 540, alignment: .leading)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .padding(.horizontal, ParallelCodexVisualStyle.Spacing.xlarge)
            .padding(.top, 48)
        } else {
            Color.clear
        }
    }
}
