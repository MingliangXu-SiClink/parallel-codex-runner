import AppKit
import SwiftUI

enum MainWindowFrameStore {
    static let defaultsKey = "ParallelCodex.MainWindowFrame.v1"
    static let fallbackSize = CGSize(width: 1_040, height: 680)

    static var savedFrame: CGRect? {
        guard let descriptor = UserDefaults.standard.string(forKey: defaultsKey) else {
            return nil
        }
        let values = descriptor.split(whereSeparator: \.isWhitespace).compactMap {
            Double($0)
        }
        guard values.count >= 4,
              values[2].isFinite,
              values[3].isFinite,
              values[2] >= 920,
              values[3] >= 640 else {
            return nil
        }
        return CGRect(
            x: values[0],
            y: values[1],
            width: values[2],
            height: values[3]
        )
    }

    static var initialSize: CGSize {
        savedFrame?.size ?? fallbackSize
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var workspacePendingRemoval: RemoteWorkspace?
    @State private var sidebarVisible = true
    @State private var conversationSearchRequestSequence = 0
    @StateObject private var windowPresentation = ConversationWindowPresentation()

    var body: some View {
        navigation
        .foregroundStyle(ParallelCodexVisualStyle.primaryText)
        .navigationTitle(windowTitle)
        .focusedSceneValue(\.sidebarVisibility, $sidebarVisible)
        .onChange(of: sidebarVisible) { visible in
            if !visible { conversationSearchRequestSequence = 0 }
        }
        .focusedSceneValue(
            \.conversationSidebarSearchActions,
            ConversationSidebarSearchActions(
                isEnabled: model.selectedWorkspace != nil,
                open: openConversationSearch
            )
        )
        .frame(minWidth: 920, minHeight: 640)
        .safeAreaInset(edge: .top, spacing: 0) {
            if model.macOSPreparedUpdate != nil && !model.macOSUpdateReminderDismissed {
                MacOSUpdateReadyBanner().environmentObject(model)
            }
        }
        .task(id: "\(model.daemonOnline)|\(model.selectedMachineID ?? "")") {
            guard model.daemonOnline, model.selectedMachineID != nil,
                  !model.automaticMacOSUpdateCheckStarted, !AppLaunchMode.isUISmokeTest else { return }
            model.automaticMacOSUpdateCheckStarted = true
            await model.checkForMacOSUpdate(silent: true)
        }
        .background(WindowLiveResizeConfiguration(
            presentation: windowPresentation,
            onWindowAttached: {
                model.transcriptScrollMemory.resumeAfterViewTeardown()
            },
            onWindowWillClose: persistVisibleWindowState
        ).allowsHitTesting(false).accessibilityHidden(true))
        .overlay(alignment: .bottomTrailing) {
            if model.workspaceFileLoading && !model.reviewColumnPresented {
                WorkspaceFileProgressOverlay()
                    .environmentObject(model)
            }
        }
        .sheet(isPresented: $model.browserPresented, onDismiss: model.closeBrowser) {
            DirectoryBrowser()
                .environmentObject(model)
                .frame(minWidth: 760, minHeight: 520)
        }
        .sheet(isPresented: $model.pairingPresented) {
            RelayPairingView()
                .environmentObject(model)
                .frame(minWidth: 500, minHeight: 540)
        }
        .sheet(isPresented: $model.machinePairingPresented) {
            MachinePairingView()
                .environmentObject(model)
                .frame(minWidth: 540, minHeight: 410)
        }
        .sheet(isPresented: $model.settingsPresented) {
            RunSettingsView()
                .environmentObject(model)
                .frame(minWidth: 660, minHeight: 690)
        }
        .sheet(item: $model.comparisonRequest) { request in
            AgentComparisonView(request: request)
                .environmentObject(model)
                .frame(minWidth: 860, minHeight: 600)
        }
        .sheet(isPresented: $model.addAgentsPresented) {
            AddAgentsView()
                .environmentObject(model)
                .frame(minWidth: 380, minHeight: 210)
        }
        .sheet(isPresented: $model.queuePresented) {
            FollowUpQueueView()
                .environmentObject(model)
                .frame(minWidth: 580)
        }
        .sheet(item: $model.conversationRenameRequest) { request in
            ConversationRenameView(request: request).environmentObject(model)
        }
        .sheet(item: $model.conversationDeleteRequest) { request in
            ConversationDeleteView(request: request).environmentObject(model)
        }
        .confirmationDialog(
            model.appLanguage.text(
                "移除 \(workspacePendingRemoval?.name ?? "工作区")？",
                "Remove \(workspacePendingRemoval?.name ?? "Workspace")?"
            ),
            isPresented: Binding(
                get: { workspacePendingRemoval != nil },
                set: { if !$0 { workspacePendingRemoval = nil } }
            ),
            titleVisibility: .visible
        ) {
            if workspacePendingRemoval?.removalLooksActive != true {
                Button(
                    workspacePendingRemoval?.removalMayDiscardResults == true
                        ? model.appLanguage.text("丢弃结果并移除", "Discard Results and Remove")
                        : model.appLanguage.text("移除工作区", "Remove Workspace"),
                    role: .destructive
                ) {
                    guard let workspace = workspacePendingRemoval else { return }
                    workspacePendingRemoval = nil
                    Task {
                        await model.removeWorkspace(
                            workspace.workspaceId,
                            discardReviewableResults: workspace.removalMayDiscardResults
                        )
                    }
                }
            }
            Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {
                workspacePendingRemoval = nil
            }
        } message: {
            if workspacePendingRemoval?.removalLooksActive == true {
                Text(model.appLanguage.text(
                    "请先停止这个工作区正在运行的任务。",
                    "Stop this workspace's running task before removing it."
                ))
            } else if workspacePendingRemoval?.removalMayDiscardResults == true {
                Text(model.appLanguage.text(
                    "未采用的 Agent 结果及其副本会被丢弃，原工作区及其中的文件不会被删除。",
                    "Unaccepted Agent results and their retained copies will be discarded. "
                        + "The original workspace folder and its files will not be deleted."
                ))
            } else {
                Text(model.appLanguage.text(
                    "工作区文件夹及其中的文件不会被删除。",
                    "The workspace folder and its files will not be deleted."
                ))
            }
        }
        .confirmationDialog(
            model.appLanguage.text("继续运行？", "Continue with this run?"),
            isPresented: Binding(
                get: { model.storageConfirmationMessage != nil
                    && model.pendingPromptSubmission?.status != .awaitingConfirmation },
                // SwiftUI sets this binding to false before invoking a dialog
                // button. Cancelling here erased the pending prompt before the
                // Continue action could confirm it.
                set: { _ in }
            ),
            titleVisibility: .visible
        ) {
            Button(model.appLanguage.text("继续", "Continue")) {
                Task { await model.confirmStorageUsage() }
            }
            Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {
                model.cancelStorageUsage()
            }
        } message: {
            Text(model.storageConfirmationMessage ?? "")
        }
        .task(id: scenePhase) {
            guard !AppLaunchMode.isUISmokeTest, scenePhase == .active else { return }
            await model.refreshVisibleContext()
            refreshWindowAfterInitialHydration()
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: visibleContextRefreshDelay)
                } catch {
                    return
                }
                await model.refreshVisibleContext()
            }
        }
    }

    private var navigation: some View {
        NavigationSplitColumns(showsSidebar: sidebarVisible) {
            navigationPane(
                VStack(spacing: 0) {
                    Color.clear
                        .frame(height: ConversationWindowChrome.headerHeight)
                        .background(ConversationWindowDragRegion())
                    WorkspaceNavigationSidebar(
                        workspacePendingRemoval: $workspacePendingRemoval,
                        searchRequestSequence: conversationSearchRequestSequence
                    )
                }
                .background(ParallelCodexVisualStyle.sidebar)
                .background(SidebarSplitPersistence(
                    name: SidebarLayout.columnsAutosaveName,
                    isVertical: true
                ))
                .ignoresSafeArea(.container, edges: .top)
            )
        } content: {
            navigationPane(
                WorkspaceWorkbench(headerLeadingInset: sidebarVisible ? 24
                    : (windowPresentation.isFullScreen ? 64 : 144))
                    .frame(minWidth: 540, maxWidth: .infinity, maxHeight: .infinity)
                    .ignoresSafeArea(.container, edges: .top)
            )
        }
        // Render the header once in the content view. A transparent NSToolbar
        // creates a second fullscreen surface that can cover this row in white.
        .ignoresSafeArea(.container, edges: .top)
        .overlay(alignment: .topLeading) {
            // One fixed control, rather than two buttons trading places while
            // their containing panes are still moving.
            sidebarControl.frame(width: windowPresentation.isFullScreen ? 56 : 128)
                .ignoresSafeArea(.container, edges: .top)
        }
    }

    private func navigationPane<Pane: View>(_ pane: Pane) -> some View {
        pane
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

    private var sidebarControl: some View {
        ConversationSidebarControl(
            isFullScreen: windowPresentation.isFullScreen,
            title: sidebarToggleTitle,
            toggle: { sidebarVisible.toggle() }
        )
    }

    private var visibleContextRefreshDelay: Duration {
        let needsFrequentRefresh = model.connectionLoading
            || !model.daemonOnline
            || model.machineSelectionInProgress
            || model.workspaceContextLoadingID != nil
            || model.remoteTurnTransitionInProgress
            || model.isRunOperationInProgress
            || model.pendingPromptSubmission != nil
            || model.activeRun?.isWorking == true
            || !model.followUpQueue.isEmpty
            || model.workspaces.contains(where: \.removalLooksActive)
        return .seconds(needsFrequentRefresh ? 5 : 15)
    }

    private var windowTitle: String {
        let title = model.conversationSurfaceState?.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let title, !title.isEmpty else {
            return model.selectedWorkspace?.name ?? AppIdentity.productName
        }
        if title == "Untitled conversation" {
            return model.appLanguage.text("未命名对话", "Untitled Conversation")
        }
        return title
    }

    private var sidebarToggleTitle: String {
        model.appLanguage.text(
            sidebarVisible ? "隐藏边栏" : "显示边栏",
            sidebarVisible ? "Hide Sidebar" : "Show Sidebar"
        )
    }

    private func openConversationSearch() {
        sidebarVisible = true
        conversationSearchRequestSequence &+= 1
    }

    private func persistVisibleWindowState() {
        NativeMultilineEditorCommitRegistry.shared.commitPendingText()
        model.flushPromptDraftPersistence()
        model.cacheVisibleRunViewState()
        // AppKit reports provisional scroll bounds while dismantling a closed
        // window. Flush the last live position first, then ignore those
        // teardown callbacks until the main window is created again.
        model.transcriptScrollMemory.suspendForViewTeardown()
        model.runViewStateStore.flushPendingWrites()
        model.desktopStateStore.flushPendingWrites()
    }

    private func refreshWindowAfterInitialHydration() {
        // Give SwiftUI one render pass to install the hydrated detail before
        // asking AppKit to redraw the split view. Without this final redraw,
        // macOS can retain the narrow clip used by the launch placeholder
        // until the next polling refresh.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            guard let window = NSApp.keyWindow ?? NSApp.mainWindow,
                  let contentView = window.contentView else { return }
            contentView.needsLayout = true
            contentView.layoutSubtreeIfNeeded()
            contentView.needsDisplay = true
            contentView.displayIfNeeded()
            window.display()
        }
    }
}

private struct WindowLiveResizeConfiguration: NSViewRepresentable {
    let presentation: ConversationWindowPresentation
    let onWindowAttached: () -> Void
    let onWindowWillClose: () -> Void

    func makeNSView(context: Context) -> WindowConfigurationView {
        WindowConfigurationView(
            presentation: presentation,
            onWindowAttached: onWindowAttached,
            onWindowWillClose: onWindowWillClose
        )
    }

    func updateNSView(_ view: WindowConfigurationView, context: Context) {
        view.onWindowAttached = onWindowAttached
        view.onWindowWillClose = onWindowWillClose
        view.configureWindow()
    }
}

private final class WindowConfigurationView: NSView {
    let presentation: ConversationWindowPresentation
    var onWindowAttached: () -> Void
    var onWindowWillClose: () -> Void
    private var configuredWindowIdentifier: ObjectIdentifier?
    private var windowObservers: [NSObjectProtocol] = []
    private var pendingFrameSave: DispatchWorkItem?
    private var didNotifyWindowClose = false

    init(
        presentation: ConversationWindowPresentation,
        onWindowAttached: @escaping () -> Void,
        onWindowWillClose: @escaping () -> Void
    ) {
        self.presentation = presentation
        self.onWindowAttached = onWindowAttached
        self.onWindowWillClose = onWindowWillClose
        super.init(frame: .zero)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        configureWindow()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        configureWindow()
    }

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        if newWindow == nil, window != nil {
            presentation.attach(to: nil)
            notifyWindowWillCloseIfNeeded()
            tearDownFramePersistence()
            configuredWindowIdentifier = nil
        }
        super.viewWillMove(toWindow: newWindow)
    }

    func configureWindow() {
        guard let window else { return }
        presentation.attach(to: window)
        configureFramePersistenceIfNeeded(for: window)
        if !window.preservesContentDuringLiveResize {
            window.preservesContentDuringLiveResize = true
        }
        ConversationWindowChrome.configure(window, appearance: effectiveAppearance)
    }

    private func configureFramePersistenceIfNeeded(for window: NSWindow) {
        guard !AppLaunchMode.isUISmokeTest else { return }
        let identifier = ObjectIdentifier(window)
        guard configuredWindowIdentifier != identifier else { return }
        configuredWindowIdentifier = identifier
        didNotifyWindowClose = false

        tearDownFramePersistence()
        // The scene is created at the persisted size. Do not mutate the window
        // frame after SwiftUI has attached its split view: even an origin-only
        // frame update can leave the sidebar rendered with a stale narrow clip.
        installFramePersistenceObservers(for: window)
        scheduleFrameSave(for: window)
        onWindowAttached()
    }

    private func installFramePersistenceObservers(for window: NSWindow) {
        let center = NotificationCenter.default
        for name in [
            NSWindow.didMoveNotification,
            NSWindow.didResizeNotification,
            NSWindow.didChangeScreenNotification,
        ] {
            windowObservers.append(
                center.addObserver(
                    forName: name,
                    object: window,
                    queue: .main
                ) { [weak self, weak window] _ in
                    MainActor.assumeIsolated {
                        guard let self, let window else { return }
                        self.scheduleFrameSave(for: window)
                    }
                }
            )
        }
        windowObservers.append(
            center.addObserver(
                forName: NSWindow.willCloseNotification,
                object: window,
                queue: .main
            ) { [weak self, weak window] _ in
                MainActor.assumeIsolated {
                    guard let self, let window else { return }
                    self.notifyWindowWillCloseIfNeeded()
                    self.saveFrame(for: window)
                }
            }
        )
        windowObservers.append(
            center.addObserver(
                forName: NSApplication.willTerminateNotification,
                object: nil,
                queue: .main
            ) { [weak self, weak window] _ in
                MainActor.assumeIsolated {
                    guard let self, let window else { return }
                    self.saveFrame(for: window)
                }
            }
        )
    }

    private func notifyWindowWillCloseIfNeeded() {
        guard !didNotifyWindowClose else { return }
        didNotifyWindowClose = true
        onWindowWillClose()
    }

    private func scheduleFrameSave(for window: NSWindow) {
        pendingFrameSave?.cancel()
        let work = DispatchWorkItem { [weak self, weak window] in
            guard let self, let window else { return }
            self.saveFrame(for: window)
        }
        pendingFrameSave = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
    }

    private func saveFrame(for window: NSWindow) {
        pendingFrameSave?.cancel()
        pendingFrameSave = nil
        UserDefaults.standard.set(
            window.frameDescriptor,
            forKey: MainWindowFrameStore.defaultsKey
        )
    }

    private func tearDownFramePersistence() {
        pendingFrameSave?.cancel()
        pendingFrameSave = nil
        windowObservers.forEach(NotificationCenter.default.removeObserver)
        windowObservers.removeAll()
    }
}

private struct WorkspaceNavigationSidebar: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var workspacePendingRemoval: RemoteWorkspace?
    let searchRequestSequence: Int
    @State private var searchText = ""
    @State private var searchPresented = false
    @State private var searchFocusRestoration: ConversationSearchFocus?
    @State private var machinePickerPresented = false
    @FocusState private var searchFieldFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            sidebarHeader
            newConversationButton
            if searchPresented {
                conversationSearchField
                    .transition(.opacity)
            }
            SidebarSectionsSplit {
                conversationSection
            } workspaces: {
                workspaceSection
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            Divider()
            SidebarSettingsFooter()
        }
        .frame(
            minWidth: ParallelCodexVisualStyle.Layout.sidebarMinimum,
            maxWidth: .infinity,
            maxHeight: .infinity,
            alignment: .topLeading
        )
        // Keep section geometry stable while scaling fonts with AppTextSize.
        .dynamicTypeSize(.large)
        .background(ParallelCodexVisualStyle.sidebar.ignoresSafeArea(.container, edges: [.top, .bottom]))
        .onChange(of: model.selectedWorkspaceID) { _ in
            searchText = ""
        }
        .onChange(of: searchRequestSequence) { _ in
            if searchRequestSequence > 0 { openSearch() }
        }
        .onAppear {
            if searchRequestSequence > 0 { openSearch() }
        }
        .onExitCommand {
            guard searchPresented else { return }
            if searchText.isEmpty {
                closeSearch()
            } else {
                searchText = ""
            }
        }
    }

    private var sidebarHeader: some View {
        HStack(spacing: 10) {
            machineMenu
            Spacer(minLength: 4)
            Button {
                searchPresented ? closeSearch() : openSearch()
            } label: {
                Image(systemName: searchPresented ? "xmark" : "magnifyingglass")
                    .appScaledSystemFont(size: 17, weight: .regular)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(ParallelCodexIconButtonStyle(
                size: ParallelCodexVisualStyle.Control.compact
            ))
            .disabled(model.selectedWorkspace == nil)
            .accessibilityIdentifier("sidebar.search")
            .help(searchPresented
                ? model.appLanguage.text("关闭搜索", "Close search")
                : model.appLanguage.text("搜索当前工作区的对话", "Search conversations"))
        }
        .padding(.horizontal, 18)
        .frame(height: ParallelCodexVisualStyle.Control.toolbarHeight)
        // Anchor the picker to the full header rather than the product-name
        // button. The button sits close to the window edge, so a fixed-width
        // popover otherwise opens partly outside narrow or left-aligned windows.
        .popover(isPresented: $machinePickerPresented, arrowEdge: .bottom) {
            MachinePickerPopover(dismiss: { machinePickerPresented = false })
                .environmentObject(model)
        }
    }

    private var machineMenu: some View {
        Button {
            machinePickerPresented.toggle()
        } label: {
            HStack(spacing: 5) {
                Text(AppIdentity.productName)
                    .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.title, weight: .medium)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                if model.machineSelectionInProgress {
                    ProgressView()
                        .controlSize(.mini)
                } else {
                    Image(systemName: "chevron.down")
                        .appScaledSystemFont(size: 11, weight: .medium)
                        .foregroundStyle(.secondary)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        // Let the brand truncate before it pushes Search outside a narrow sidebar.
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityIdentifier("sidebar.machine")
        .help(activeMachineHelp)
    }

    private var newConversationButton: some View {
        Button {
            Task { await model.beginNewConversation() }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "square.and.pencil")
                    .appScaledSystemFont(size: 14, weight: .medium)
                    .frame(width: 24)
                Text(model.appLanguage.text("新对话", "New conversation"))
                    .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.sidebar)
                Spacer()
            }
            .padding(.horizontal, 10)
            .frame(height: 36)
            .contentShape(Rectangle())
            .parallelCodexSidebarRow(selected: model.isComposingNewConversation)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .disabled(model.selectedWorkspace == nil)
        .keyboardShortcut("n", modifiers: .command)
        .help(
            model.newConversationBlockReason
                ?? model.appLanguage.text("在当前工作区新建对话", "New conversation in this workspace")
        )
        .padding(.horizontal, 8)
        .padding(.bottom, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var conversationSearchField: some View {
        HStack(spacing: 7) {
            Image(systemName: "magnifyingglass")
                .appScaledSystemFont(size: 11)
                .foregroundStyle(.secondary)
            TextField(
                model.appLanguage.text("搜索对话", "Search conversations"),
                text: $searchText
            )
            .textFieldStyle(.plain)
            .appScaledSystemFont(size: 13)
            .focused($searchFieldFocused)
            if !searchText.isEmpty {
                Button {
                    searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .appScaledSystemFont(size: 11)
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help(model.appLanguage.text("清除搜索", "Clear search"))
            }
        }
        .padding(.horizontal, 9)
        .frame(height: 30)
        .background(
            ParallelCodexVisualStyle.subtleSurface,
            in: RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.selection,
                style: .continuous
            )
        )
        .padding(.horizontal, 10)
        .padding(.bottom, 4)
    }

    private func openSearch() {
        if !searchPresented {
            searchFocusRestoration = ConversationSearchFocus(window: NSApp.keyWindow)
        }
        withAnimation(
            reduceMotion
                ? nil
                : .easeOut(duration: ParallelCodexVisualStyle.Motion.quick)
        ) {
            searchPresented = true
        }
        DispatchQueue.main.async {
            searchFieldFocused = true
        }
    }

    private func closeSearch() {
        let restoration = searchFocusRestoration
        searchFocusRestoration = nil
        searchFieldFocused = false
        searchText = ""
        withAnimation(
            reduceMotion
                ? nil
                : .easeOut(duration: ParallelCodexVisualStyle.Motion.quick)
        ) {
            searchPresented = false
        }
        DispatchQueue.main.async {
            guard !searchPresented else { return }
            restoration?.restore()
        }
    }

    private var conversationSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            SidebarSectionHeader(
                title: model.appLanguage.text("最近对话", "Recent"),
                detail: nil
            )
            .padding(.horizontal, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    if model.selectedWorkspace != nil {
                        WorkspaceConversationInlineList(searchText: searchText)
                    } else {
                        SidebarEmptyRow(
                            icon: "bubble.left",
                            title: model.appLanguage.text(
                                "选择工作区后显示对话",
                                "Choose a workspace to see conversations"
                            )
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.bottom, 12)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var workspaceSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            SidebarSectionHeader(
                title: model.appLanguage.text("工作区", "Workspaces"),
                detail: nil
            ) {
                Button {
                    Task { await model.openBrowser() }
                } label: {
                    Image(systemName: "plus")
                        .appScaledSystemFont(size: 12, weight: .semibold)
                        .frame(width: 24, height: 24)
                }
                .buttonStyle(.plain)
                .disabled(model.selectedMachine == nil || model.selectedMachine?.online == false)
                .help(model.appLanguage.text("添加工作区", "Add workspace"))
            }
            .padding(.horizontal, 8)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    workspaceRows
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.bottom, 12)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var workspaceRows: some View {
        if model.connectionLoading && model.workspaces.isEmpty {
            SidebarEmptyRow(
                icon: "arrow.clockwise",
                title: model.appLanguage.text("正在加载工作区", "Loading workspaces")
            )
        } else if model.selectedMachine?.online == false && model.workspaces.isEmpty {
            SidebarEmptyRow(
                icon: "wifi.slash",
                title: model.appLanguage.text("设备未连接", "Computer offline")
            )
        } else if model.workspaces.isEmpty && !model.workspaceDirectoryLoaded {
            SidebarEmptyRow(
                icon: "wifi.slash",
                title: model.appLanguage.text("工作区暂时无法加载", "Workspaces temporarily unavailable")
            )
        } else if model.workspaces.isEmpty {
            SidebarEmptyRow(
                icon: "folder",
                title: model.appLanguage.text("暂无工作区", "No workspaces")
            )
        } else {
            ForEach(model.workspaces, id: \.workspaceId) { workspace in
                Button {
                    if workspace.available || model.workspaceDirectoryIsCached {
                        Task { await model.selectWorkspace(workspace.workspaceId) }
                    } else {
                        workspacePendingRemoval = workspace
                    }
                } label: {
                    WorkspaceSidebarRow(
                        workspace: workspace,
                        promptStatus: model.promptSubmissionStatus(
                            for: workspace.workspaceId
                        ),
                        showsLocation: model.workspaces.filter {
                            $0.name == workspace.name
                        }.count > 1,
                        selected: workspace.workspaceId == model.selectedWorkspaceID,
                        loading: model.workspaceContextLoadingID == workspace.workspaceId
                            && (workspace.workspaceId != model.selectedWorkspaceID
                                || model.isSelectedWorkspaceContextLoading)
                    )
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contextMenu {
                    Button(
                        model.appLanguage.text("移除工作区", "Remove Workspace"),
                        role: .destructive
                    ) {
                        workspacePendingRemoval = workspace
                    }
                }
            }
        }
    }

    private var activeMachineHelp: String {
        guard let machine = model.selectedMachine else {
            return model.appLanguage.text("选择设备", "Choose a computer")
        }
        return machine.name
    }
}

private struct SidebarSectionHeader<Action: View>: View {
    let title: String
    let detail: String?
    let action: Action

    init(
        title: String,
        detail: String?,
        @ViewBuilder action: @escaping () -> Action
    ) {
        self.title = title
        self.detail = detail
        self.action = action()
    }

    var body: some View {
        HStack(spacing: 6) {
            Text(title)
                .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.caption)
                .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
            if let detail, !detail.isEmpty {
                Text(detail)
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            action
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .frame(minHeight: 24)
    }
}

private extension SidebarSectionHeader where Action == EmptyView {
    init(title: String, detail: String?) {
        self.init(title: title, detail: detail) { EmptyView() }
    }
}

private struct SidebarEmptyRow: View {
    let icon: String
    let title: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .frame(width: 28)
            Text(title)
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .appScaledSystemFont(size: 13)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 10)
        .frame(minHeight: 36)
    }
}

private struct MachinePickerPopover: View {
    @EnvironmentObject private var model: AppModel
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.appLanguage.text("设备", "Computers"))
                .appScaledSystemFont(size: 11, weight: .semibold)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8)

            if model.machines.isEmpty {
                SidebarEmptyRow(
                    icon: "desktopcomputer",
                    title: model.appLanguage.text("尚未连接设备", "No computers connected")
                )
            } else {
                ForEach(model.machines, id: \.deviceId) { machine in
                    Button {
                        dismiss()
                        Task { await model.selectMachine(machine.deviceId) }
                    } label: {
                        HStack(spacing: 10) {
                            ZStack(alignment: .bottomTrailing) {
                                Image(systemName: "desktopcomputer")
                                    .appScaledSystemFont(size: 14, weight: .medium)
                                    .frame(width: 22, height: 22)
                                ParallelCodexStatusDot(
                                    color: machine.online == false ? .secondary : .green,
                                    size: 5
                                )
                                .offset(x: 1, y: 1)
                            }
                            Text(machine.name)
                                .appScaledSystemFont(size: 13)
                                .lineLimit(1)
                            Spacer(minLength: 8)
                            if model.selectedMachineID == machine.deviceId {
                                Image(systemName: "checkmark")
                                    .appScaledSystemFont(size: 11, weight: .semibold)
                                    .foregroundStyle(model.appAccentColor.color)
                            }
                        }
                        .padding(.horizontal, 8)
                        .frame(minHeight: 34)
                        .contentShape(Rectangle())
                        .parallelCodexSidebarRow(
                            selected: model.selectedMachineID == machine.deviceId
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(model.machineSelectionInProgress)
                }
            }

            Divider()

            Button {
                dismiss()
                DispatchQueue.main.async {
                    model.machinePairingPresented = true
                }
            } label: {
                Label(
                    model.appLanguage.text("连接另一台设备", "Connect Another Computer"),
                    systemImage: "plus"
                )
                .appScaledSystemFont(size: 13)
                .padding(.horizontal, 8)
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(10)
        .frame(width: 268)
    }
}

private struct WorkspaceSidebarRow: View {
    @EnvironmentObject private var model: AppModel
    let workspace: RemoteWorkspace
    let promptStatus: PromptSubmissionStatus?
    let showsLocation: Bool
    let selected: Bool
    let loading: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: workspaceIcon)
                .appScaledSystemFont(size: 13, weight: .regular)
                .foregroundStyle(selected ? model.appAccentColor.color : Color.secondary)
                .frame(width: 18, height: 20)
            Text(displayName)
                .appScaledSystemFont(
                    size: ParallelCodexVisualStyle.Typography.sidebar,
                    weight: selected ? .medium : .regular
                )
                .foregroundStyle(.primary)
                .lineLimit(1)
            Spacer(minLength: 4)
            if loading {
                ProgressView()
                    .controlSize(.mini)
            } else if showsStatusIndicator {
                ParallelCodexStatusDot(
                    color: statusColor,
                    isPulsing: ExecutionActivityState.isProcessing(
                        workspace: workspace,
                        hasPendingSubmission: promptStatus != nil,
                        connectionConfirmed: model.daemonOnline && model.connectionErrorMessage == nil
                            && model.selectedMachine?.online != false && !model.machineSelectionInProgress
                    )
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .frame(minHeight: 36)
        .parallelCodexSidebarRow(selected: false)
        .contentShape(Rectangle())
        .help(workspaceHelp)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var workspaceIcon: String {
        guard workspace.available || model.workspaceDirectoryIsCached else { return "folder.badge.questionmark" }
        return selected ? "folder.fill" : "folder"
    }

    private var displayName: String {
        guard showsLocation else { return workspace.name }
        let parent = ((workspace.path as NSString).deletingLastPathComponent as NSString)
            .lastPathComponent
        return parent.isEmpty ? workspace.name : "\(workspace.name) — \(parent)"
    }

    private var workspaceHelp: String {
        [statusText, workspace.path].compactMap { $0 }.joined(separator: "\n")
    }

    private var showsStatusIndicator: Bool {
        guard !model.workspaceDirectoryIsCached else { return false }
        guard statusText != nil else { return false }
        if promptStatus != nil || workspace.removalLooksActive { return true }
        return [
            "failed", "error", "interrupted", "sync_ambiguous", "cleanup_failed",
        ].contains((workspace.runStatus ?? "").lowercased())
    }

    private var statusText: String? {
        if model.workspaceDirectoryIsCached {
            return model.appLanguage.text("已保存的工作区 · 状态未核实", "Saved workspace · status not verified")
        }
        guard workspace.available else {
            return model.appLanguage.text(
                "文件夹已移动或不可访问",
                "Folder moved or unavailable"
            )
        }
        if let promptStatus {
            switch promptStatus {
            case .pending: return model.appLanguage.text("正在发送", "Sending")
            case .retrying: return model.appLanguage.text("正在发送", "Sending")
            case .checkingReceipt: return model.appLanguage.text("正在确认发送", "Checking delivery")
            case .receiptUnresolved: return model.appLanguage.text("消息已保留", "Message retained")
            case .waitingForPreviousTurn: return model.appLanguage.text("等待上一轮收尾", "Waiting for previous turn")
            case .opening: return model.appLanguage.text("正在打开", "Opening")
            case .openingFailed: return model.appLanguage.text("已发送", "Sent")
            case .awaitingConfirmation:
                return model.appLanguage.text("等待容量确认", "Storage confirmation needed")
            case .failed: return model.appLanguage.text("发送失败", "Not sent")
            }
        }
        return workspace.runStatusLabel(language: model.appLanguage)
    }

    private var statusColor: Color {
        if promptStatus == .failed || ["failed", "error", "interrupted"].contains(
            (workspace.runStatus ?? "").lowercased()
        ) {
            return .red
        }
        if promptStatus == .awaitingConfirmation
            || ["sync_ambiguous", "cleanup_failed"].contains(
                (workspace.runStatus ?? "").lowercased()
            ) {
            return .orange
        }
        if promptStatus != nil || workspace.removalLooksActive {
            return model.appAccentColor.color
        }
        return .green
    }
}

private struct MachinePairingView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize
    private let pairingCommand = "pcrd pair-device"

    var body: some View {
        VStack(alignment: .leading, spacing: 20 * appTextSize.scale) {
            HStack(spacing: 14 * appTextSize.scale) {
                Image(systemName: "desktopcomputer.and.arrow.down")
                    .appScaledSystemFont(size: 30, weight: .semibold)
                    .foregroundStyle(model.appAccentColor.color)
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.appLanguage.text("连接设备", "Connect a Computer"))
                        .appScaledTextStyle(.title2, weight: .semibold)
                    Text(model.appLanguage.text(
                        "请先在另一台设备上生成连接码，再粘贴到这里。",
                        "Generate a connection code on the other computer, then paste it here."
                    ))
                        .appScaledTextStyle(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            VStack(alignment: .leading, spacing: 9 * appTextSize.scale) {
                Text(model.appLanguage.text(
                    "在另一台设备上打开终端并运行：",
                    "On the other computer, open Terminal and run:"
                ))
                    .appScaledTextStyle(.callout)
                    .foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    Text(pairingCommand)
                        .appScaledTextStyle(.body, design: .monospaced)
                        .textSelection(.enabled)
                    Spacer()
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(pairingCommand, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .help(model.appLanguage.text("复制命令", "Copy command"))
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 38 * appTextSize.scale)
                .background(.quaternary.opacity(0.42), in: RoundedRectangle(cornerRadius: 7))
                Text(model.appLanguage.text(
                    "终端会显示一段连接码。将完整连接码粘贴到下方即可。",
                    "Terminal will show a connection code. Paste the complete code below."
                ))
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
            }

            TextEditor(text: $model.machinePairingCode)
                .appScaledTextStyle(.body, design: .monospaced)
                .frame(minHeight: 92 * appTextSize.scale)
                .padding(8)
                .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 8))
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(.separator.opacity(0.55), lineWidth: 1)
                }

            if let error = model.machinePairingError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .appScaledTextStyle(.callout)
            }

            Spacer()
            HStack {
                Spacer()
                Button(model.appLanguage.text("取消", "Cancel")) {
                    model.machinePairingPresented = false
                    model.machinePairingError = nil
                }
                Button(model.appLanguage.text("连接", "Connect")) {
                    Task { await model.connectMachine() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    model.machinePairingCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || model.machinePairingInProgress
                )
            }
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .padding(24 * appTextSize.scale)
    }
}

private struct WorkspaceConversationInlineList: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let searchText: String
    @State private var showsAllConversations = false
    @AppStorage("parallel-codex.macos.pinned-conversations-v1")
    private var pinnedConversationPayload = ""

    var body: some View {
        LazyVStack(spacing: 1) {
            if model.conversationSearchLoading {
                ProgressView().controlSize(.small).padding(8)
            }
            if let error = model.conversationSearchError, !searchText.isEmpty {
                Text(error).appScaledSystemFont(size: 11).foregroundStyle(.secondary).padding(8)
            }
            if model.conversationTrash.loadError != nil {
                Text(appLanguage.text("无法读取本地对话状态", "Local conversation state could not be read"))
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 10)
            } else if model.conversationIndexLoading && model.workspaceConversations.isEmpty {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.mini)
                    Text(appLanguage.text("正在加载对话", "Loading conversations"))
                        .foregroundStyle(.secondary)
                }
                .appScaledSystemFont(size: 11)
                .frame(maxWidth: .infinity, alignment: .leading)
                .frame(minHeight: 30)
            } else if visibleConversations.isEmpty {
                if !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                  if !model.conversationSearchLoading && model.conversationSearchError == nil {
                    Text(appLanguage.text("没有匹配的对话", "No matching conversations"))
                        .appScaledSystemFont(size: 11)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .frame(minHeight: 30)
                        .padding(.horizontal, 10)
                  }
                } else {
                    Text(appLanguage.text("还没有对话", "No conversations yet"))
                        .appScaledSystemFont(size: 11)
                        .foregroundStyle(.tertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .frame(minHeight: 30)
                        .padding(.horizontal, 10)
                }
            } else {
                ForEach(visibleConversations) { conversation in
                    Button {
                        Task {
                            if searchText.isEmpty { await model.selectConversation(conversation.id) }
                            else { await model.openConversationSearchResult(conversation, query: searchText) }
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                          WorkspaceConversationSidebarRow(
                            conversation: conversation,
                            selected: model.selectedConversationID == conversation.id,
                            opening: model.openingConversationID == conversation.id,
                            pinned: isPinned(conversation),
                            showsUpdatedTime: duplicateConversationIDs.contains(conversation.id)
                          )
                          if !searchText.isEmpty, let excerpt = model.searchExcerpt(conversation, query: searchText) {
                            Text(excerpt).appScaledSystemFont(size: 11).foregroundStyle(.secondary)
                                .lineLimit(3).multilineTextAlignment(.leading)
                                .padding(.leading, 35).padding(.trailing, 10).padding(.bottom, 6)
                          }
                        }
                        .parallelCodexSidebarRow(selected: model.selectedConversationID == conversation.id)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .disabled(model.openingConversationID == conversation.id)
                    .accessibilityLabel(conversationAccessibilityLabel(conversation))
                    .accessibilityHint(searchText.isEmpty ? "" : model.searchExcerpt(conversation, query: searchText) ?? "")
                    .accessibilityAddTraits(
                        model.selectedConversationID == conversation.id ? .isSelected : []
                    )
                    .contextMenu {
                        Button {
                            model.requestConversationRename(conversation)
                        } label: {
                            Label(appLanguage.text("重命名", "Rename"), systemImage: "pencil")
                        }
                        Button {
                            togglePinned(conversation)
                        } label: {
                            Label(
                                isPinned(conversation)
                                    ? appLanguage.text("取消固定", "Unpin")
                                    : appLanguage.text("固定对话", "Pin Conversation"),
                                systemImage: isPinned(conversation) ? "pin.slash" : "pin"
                            )
                        }
                        Divider()
                        Button(role: .destructive) {
                            model.requestConversationDelete(conversation)
                        } label: {
                            Label(appLanguage.text("删除对话…", "Delete Conversation…"), systemImage: "trash")
                        }
                    }
                }
                if orderedConversations.count > collapsedConversationLimit,
                   searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Button {
                        withAnimation(
                            reduceMotion
                                ? nil
                                : .easeOut(duration: ParallelCodexVisualStyle.Motion.quick)
                        ) {
                            showsAllConversations.toggle()
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: showsAllConversations ? "chevron.up" : "ellipsis")
                                .appScaledSystemFont(size: 10, weight: .semibold)
                                .frame(width: 16)
                            Text(
                                showsAllConversations
                                    ? appLanguage.text("收起", "Show Less")
                                    : appLanguage.text(
                                        "显示更多对话",
                                        "Show More Conversations"
                                    )
                            )
                            Spacer()
                        }
                        .appScaledSystemFont(size: 13)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 10)
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if let record = model.lastDeletedConversation,
               model.isCurrentMachine(record.machineID), model.selectedWorkspaceID == record.workspaceID {
                HStack(spacing: 8) {
                    Text(appLanguage.text("已删除对话", "Conversation deleted"))
                        .appScaledSystemFont(size: 11).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    Button(appLanguage.text("撤销", "Undo")) {
                        do { try model.restoreDeletedConversation(record) }
                        catch { model.errorMessage = appLanguage.userMessage(for: error) }
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("conversation.delete.undo")
                    Button { model.lastDeletedConversation = nil } label: {
                        Image(systemName: "xmark").appScaledSystemFont(size: 10)
                    }
                    .buttonStyle(.plain).help(appLanguage.text("关闭", "Close"))
                }
                .padding(.horizontal, 10).frame(minHeight: 32)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 2)
        .task(id: "\(model.currentMachineContextID):\(model.selectedWorkspaceID ?? ""):\(searchText)") {
            await model.searchSavedConversationText(searchText)
        }
        .onChange(of: model.selectedWorkspaceID) { _ in
            showsAllConversations = false
        }
    }

    private var visibleConversations: [WorkspaceConversationSummary] {
        let conversations = orderedConversations
        guard searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !showsAllConversations,
              conversations.count > collapsedConversationLimit else {
            return conversations
        }
        let collapsed = Array(conversations.prefix(collapsedConversationLimit))
        guard let selectedID = model.selectedConversationID,
              conversations.contains(where: { $0.id == selectedID }),
              !collapsed.contains(where: { $0.id == selectedID }) else {
            return collapsed
        }
        return collapsed + conversations.filter { $0.id == selectedID }
    }

    private var orderedConversations: [WorkspaceConversationSummary] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        let filtered = query.isEmpty
            ? model.workspaceConversations
            : model.workspaceConversations.filter {
                model.searchExcerpt($0) != nil || model.workspaceConversationMatchesSearch($0, query: query)
            }
        return filtered.enumerated().sorted { left, right in
            let leftPinned = isPinned(left.element)
            let rightPinned = isPinned(right.element)
            if leftPinned != rightPinned { return leftPinned }
            return left.offset < right.offset
        }.map(\.element)
    }

    private var collapsedConversationLimit: Int { 8 }

    private var duplicateConversationIDs: Set<String> {
        let groups = Dictionary(grouping: model.workspaceConversations) {
            $0.title.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        }
        return Set(groups.values.filter { $0.count > 1 }.flatMap { $0.map(\.id) })
    }

    private func conversationAccessibilityLabel(
        _ conversation: WorkspaceConversationSummary
    ) -> String {
        conversationTitleAccessibilityLabel(
            title: localizedConversationTitle(
                conversation.title,
                language: appLanguage
            ),
            status: duplicateConversationIDs.contains(conversation.id)
                ? conversation.relativeUpdatedText(language: appLanguage)
                : nil
        )
    }

    private var pinnedConversationKeys: Set<String> {
        Set(pinnedConversationPayload.split(separator: "\n").map(String.init))
    }

    private func pinKey(_ conversation: WorkspaceConversationSummary) -> String {
        let machineID = model.selectedMachineID ?? "local"
        let workspaceID = model.selectedWorkspaceID ?? "unknown"
        return "\(machineID)\u{1f}\(workspaceID)\u{1f}\(conversation.id)"
    }

    private func isPinned(_ conversation: WorkspaceConversationSummary) -> Bool {
        pinnedConversationKeys.contains(pinKey(conversation))
    }

    private func togglePinned(_ conversation: WorkspaceConversationSummary) {
        var keys = pinnedConversationKeys
        let key = pinKey(conversation)
        if keys.remove(key) == nil { keys.insert(key) }
        pinnedConversationPayload = keys.sorted().joined(separator: "\n")
    }
}

private struct WorkspaceConversationSidebarRow: View {
    @EnvironmentObject private var model: AppModel
    let conversation: WorkspaceConversationSummary
    let selected: Bool
    let opening: Bool
    let pinned: Bool
    let showsUpdatedTime: Bool

    var body: some View {
        HStack(spacing: 9) {
            Group {
                if opening {
                    ProgressView()
                        .controlSize(.mini)
                } else {
                    Image(systemName: "bubble.left")
                        .appScaledSystemFont(size: 13, weight: .regular)
                        .foregroundStyle(selected ? Color.primary : Color.secondary)
                }
            }
            .frame(width: 16, height: 18)
            Text(conversationTitle)
                .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.sidebar)
                .lineLimit(1)
                .foregroundStyle(.primary)
            Spacer(minLength: 0)
            if showsUpdatedTime {
                Text(conversation.relativeUpdatedText(language: model.appLanguage))
                    .appScaledSystemFont(size: 10)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
            if pinned {
                Image(systemName: "pin.fill")
                    .appScaledSystemFont(size: 10)
                    .foregroundStyle(.secondary)
            }
            if showsStatusIndicator {
                ParallelCodexStatusDot(color: statusColor, size: 5, isPulsing: executionActive)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .frame(minHeight: 36)
    }

    private var showsStatusIndicator: Bool {
        guard let latestRun = conversation.latestRun,
              !conversation.opensResumeHistory else { return false }
        switch ConversationRunPresentation.make(run: latestRun).tone {
        case .accent, .warning, .failure: return true
        case .neutral, .success: return false
        }
    }

    private var statusColor: Color {
        guard let latestRun = conversation.latestRun,
              !conversation.opensResumeHistory else { return .secondary }
        switch ConversationRunPresentation.make(run: latestRun).tone {
        case .accent: return model.appAccentColor.color
        case .success: return .green
        case .warning: return .orange
        case .failure: return .red
        case .neutral: return .secondary
        }
    }

    private var executionActive: Bool {
        guard !conversation.opensResumeHistory,
              !conversation.unavailableRunIDs.contains(conversation.latestRunID) else { return false }
        return ExecutionActivityState.isWorking(
            run: conversation.latestRun,
            isLatestTurn: true,
            hasPendingSubmission: model.selectedWorkspaceID.flatMap {
                model.promptSubmissionStatus(for: $0)
            } != nil,
            connectionConfirmed: model.daemonOnline && model.connectionErrorMessage == nil
                && model.selectedMachine?.online != false && !model.machineSelectionInProgress
        )
    }

    private var conversationTitle: String {
        localizedConversationTitle(conversation.title, language: model.appLanguage)
    }
}

private struct SidebarSettingsFooter: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 0) {
                settingsButton
                SidebarCodexUsage()
            }
            .padding(.horizontal, 8)
            if let errorMessage = model.connectionErrorMessage {
                Text(errorMessage)
                    .appScaledSystemFont(size: 10)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .padding(.horizontal, 18)
            }
        }
    }

    @ViewBuilder
    private var settingsButton: some View {
        if #available(macOS 14.0, *) {
            SettingsLink {
                settingsLabel
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)
            .help(settingsHelp)
        } else {
            Button {
                NSApp.sendAction(
                    Selector(("showPreferencesWindow:")),
                    to: nil,
                    from: nil
                )
            } label: {
                settingsLabel
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)
            .help(settingsHelp)
        }
    }

    private var settingsLabel: some View {
        HStack(spacing: 10) {
            Image(systemName: "gearshape")
                .appScaledSystemFont(size: 14, weight: .medium)
                .frame(width: 24)
            Text(model.appLanguage.text("设置", "Settings"))
                .appScaledSystemFont(size: 13)
            Spacer(minLength: 4)
            if model.connectionLoading {
                ProgressView()
                    .controlSize(.mini)
            } else if !model.daemonOnline {
                Image(systemName: "wifi.exclamationmark")
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(Color.orange)
            }
        }
        .padding(.horizontal, 10)
        .frame(height: 38)
        .contentShape(Rectangle())
    }

    private var settingsHelp: String {
        model.connectionErrorMessage
            ?? model.appLanguage.text("应用、设备与连接设置", "App, computer and connection settings")
    }
}
