#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$ROOT/.build/pcr-macos-contract-tests"

compile_swift() {
  if [ -n "${PCR_MAC_SDK:-}" ]; then
    swiftc -sdk "$PCR_MAC_SDK" "$@"
  else
    swiftc "$@"
  fi
}

if ! rg -n 'ComposerExecutionConfigurationSummary\(isEditing:' \
    "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" >/dev/null \
    || ! sed -n '/private var composerInput:/,/private var runSettingsButton:/p' \
        "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" | rg -q 'ComposerExecutionConfigurationSummary' \
    || ! rg -F -n 'ExecutionConfigurationAnchor(isPresented: $isPresented' \
        "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift" >/dev/null; then
  printf '%s\n' "Execution configuration must open from the compact composer footer without embedding the editor in the input." >&2
  exit 1
fi

if sed -n '/private var composerInput:/,/private var runSettingsButton:/p' \
    "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" | rg -q 'ComposerExecutionConfiguration\('; then
  printf '%s\n' "The full execution editor must remain in its independent upward surface." >&2
  exit 1
fi

if rg -n 'showsSecondaryStop|composer\.stop-run' \
    "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" "$ROOT/Sources/PCRRemoteMac/ComposerPrimaryAction.swift" \
    || sed -n '/struct ExecutionConfigurationPopover:/,/struct ComposerExecutionConfiguration:/p' \
        "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift" | rg -q 'Button\(|Text\('; then
  printf '%s\n' "The composer must use one primary action; execution configuration needs no redundant title or close button." >&2
  exit 1
fi

if rg -n '\.popover\(|Apply count|应用数量' "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift"; then
  printf '%s\n' "Execution editors must stay in the upward window-bounded surface and counts must apply directly." >&2
  exit 1
fi

if rg -n '\.truncationMode\(|\.lineLimit\(1\)|…' "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift"; then
  printf '%s\n' "Execution configuration must show full labels by wrapping, never by truncating or adding omission marks." >&2
  exit 1
fi

if sed -n '/struct ComposerExecutionConfigurationSummary:/,/struct ExecutionSummaryButtonStyle:/p' \
    "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift" | rg -q 'VStack|VStackLayout'; then
  printf '%s\n' "The collapsed execution summary must stay on one line at every window width." >&2
  exit 1
fi

if rg -n 'Text\(scope\)' "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift"; then
  printf '%s\n' "Execution scope belongs in help/accessibility, not a permanent configuration label." >&2
  exit 1
fi

if rg -n 'ExecutionStageEditor|ExecutionOptionEditor|ExecutionEditorSelection' \
    "$ROOT/Sources/PCRRemoteMac/ComposerExecutionConfiguration.swift"; then
  printf '%s\n' "Selecting an execution option must open a local menu, not expand the configuration panel." >&2
  exit 1
fi

if rg -n 'RemovedQueuedMessage|removedQueuedMessage|undoQueuedMessageRemoval|Queued message removed|Undo removal|已移除排队消息|撤销移除' \
    "$ROOT/Sources/PCRRemoteMac" -g '*.swift'; then
  printf '%s\n' "Removing a queued message must not retain an undo record or offer to reinsert it." >&2
  exit 1
fi

if ! rg -U -n 'let presentationChanged[\s\S]{0,180}self.parent.language != parent.language' \
    "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null; then
  printf '%s\n' "Changing the app language must refresh existing transcript cells and measurements." >&2
  exit 1
fi

if ! rg -U -n 'func dismissRunSettings\(\)[\s\S]{0,300}rememberCurrentSettings\(\)\s+settingsPresented = false' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+RunSettings.swift" >/dev/null \
    || ! rg -F -n '.onExitCommand(perform: dismissSettings)' \
    "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null; then
  printf '%s\n' "Settings edits must be saved before the sheet is dismissed, including Escape." >&2
  exit 1
fi

if rg -n 'remoteTurnTransition\s*=' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+FollowUps.swift"; then
  printf '%s\n' "Reading a workspace queue must not suspend conversation navigation or input." >&2
  exit 1
fi

if ! rg -U -n 'defer \{[\s\S]{0,100}remoteTurnTransition\?\.requestID == requestID[\s\S]{0,100}remoteTurnTransition = nil' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null; then
  printf '%s\n' "Remote Turn transitions must release their own refresh on every exit path." >&2
  exit 1
fi

if rg -n 'import (Security|LocalAuthentication)|\bSecItem[A-Za-z]*\b|\bkSec[A-Za-z]+\b' \
    "$ROOT/Sources/PCRRemoteMac"; then
  printf '%s\n' "The macOS client must not access the login Keychain at runtime." >&2
  exit 1
fi

if sed -n '/func applicationShouldTerminate(/,/private func flushPersistentState/p' \
    "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" | \
    rg -q 'prepareVisibleResultForContextTransition|acceptSelectedAgent|selectedAgent|activeRun|syncBack|terminateLater|Task[[:space:]]*\{'; then
  printf '%s\n' "Client exit must persist the view without resolving, adopting or stopping a result." >&2
  exit 1
fi

if rg -n '\.font\(\.system\(size:' "$ROOT/Sources/PCRRemoteMac"; then
  printf '%s\n' \
    "Fixed system fonts must use appScaledSystemFont so display zoom applies consistently." >&2
  exit 1
fi

if rg -U -n 'set:\s*\{[^}]*cancelStorageUsage' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift"; then
  printf '%s\n' "Dismissing the storage dialog must not cancel its pending prompt." >&2
  exit 1
fi

if ! rg -n 'SidebarSectionsSplit' "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -n 'VSplitView' "$ROOT/Sources/PCRRemoteMac/SidebarLayout.swift" >/dev/null \
    || ! rg -n 'saveDividerPosition' "$ROOT/Sources/PCRRemoteMac/SidebarLayout.swift" >/dev/null \
    || rg -n 'NavigationSplitView\(' "$ROOT/Sources/PCRRemoteMac/ContentView.swift"; then
  printf '%s\n' "The docked sidebar must preserve user-resizable workspace and conversation panes." >&2
  exit 1
fi

if ! rg -n 'ParallelCodex\.MainWindowFrame\.v1' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -n 'initialWindowSize = MainWindowFrameStore\.initialSize' \
    "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null \
    || ! rg -n 'window\.frameDescriptor' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' "The main window must persist its size under a stable key." >&2
  exit 1
fi

if ! rg -U -n 'onWindowAttached:[\s\S]{0,180}resumeAfterViewTeardown' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -F -n 'onWindowWillClose: persistVisibleWindowState' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'func persistVisibleWindowState\(\)[\s\S]{0,500}suspendForViewTeardown' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Closing and reopening the main window must preserve the last transcript position." >&2
  exit 1
fi

if ! rg -n 'conversationSection|workspaceSection|MachinePickerPopover' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' "The sidebar must keep conversations primary and computers in a picker." >&2
  exit 1
fi

if rg -n 'MachineWorkspaceGroup' "$ROOT/Sources/PCRRemoteMac/ContentView.swift"; then
  printf '%s\n' "Computers, workspaces, and conversations must not form a nested control tree." >&2
  exit 1
fi

if rg -U -n 'machineSelectionInProgress[\s\S]{0,240}allowsHitTesting' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift"; then
  printf '%s\n' "Switching computers must not cover or disable the entire window." >&2
  exit 1
fi

if ! rg -U -n 'func selectMachine[\s\S]{0,2400}system\.health[\s\S]{0,2400}setRelayMachineTarget' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null; then
  printf '%s\n' "Computer switching must preflight before atomically changing context." >&2
  exit 1
fi

if ! rg -n 'textView\.placeholder = accessibilityPlaceholder' \
    "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" >/dev/null \
    || ! rg -U -n 'override func draw[\s\S]{0,500}textContainerOrigin' \
        "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" >/dev/null; then
  printf '%s\n' \
    "The prompt placeholder must be drawn by the native editor at its insertion origin." >&2
  exit 1
fi

if ! rg -n '@Environment\(\\\.appTextSize\)' \
    "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" >/dev/null \
    || ! rg -n 'textView\.font = editorFont' \
    "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" >/dev/null \
    || ! rg -n 'fontSize \* appTextSize\.scale' \
    "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale the native prompt text and placeholder together." >&2
  exit 1
fi

if ! rg -U -n 'submitButton[\s\S]{0,2600}accessibilityLabel\(primaryActionHelp\)' \
    "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" >/dev/null; then
  printf '%s\n' "The icon-only primary action must expose its current action to assistive technology." >&2
  exit 1
fi

if ! rg -U -n 'agentTab[\s\S]{0,4200}accessibilityAddTraits\(selected \? \.isSelected : \[\]\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null; then
  printf '%s\n' "The Agent candidate selector must expose its selected state." >&2
    exit 1
fi

if ! rg -U -n 'func selectAgent[\s\S]{0,5000}maxPages: 1,[\s\S]{0,180}initialEventLimit: 100[\s\S]{0,2800}commitVisibleAgentSelection\(agent, keepPending: false, selectedWhileWorking: run.isWorking\)[\s\S]{0,1000}Task \{ \[weak self\][\s\S]{0,1400}loadAgentDetail\(agent, maxPages: 20\)' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+RunControls.swift" >/dev/null \
    || ! rg -U -n 'func selectAgent[\s\S]{0,1800}errorMessage = nil[\s\S]{0,160}pendingAgentSelection = agent' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+RunControls.swift" >/dev/null \
    || rg -U -n 'selectionEnabled:[^\n]*[\s\S]{0,80}!isRunOperationInProgress' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Presentation.swift" >/dev/null; then
  printf '%s\n' \
    "Agent navigation must stay interactive, clear stale failures, and finish pagination after first-paint hydration." >&2
  exit 1
fi

if ! rg -F -n 'let inPlaceAgentSwitchIndices = plan.changedIndices.filter' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n '!inPlaceAgentSwitchIndices.contains($0)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n 'refreshVisibleAgentSwitch(at: index)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n '!changesOnlyFixedAgentSwitch' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n 'isWorkDisclosureChange: toggledWorkDisclosureID != nil' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n 'AgentSwitchTrackingHostingView(' \
      "$ROOT/Sources/PCRRemoteMac/TranscriptCollectionItem.swift" >/dev/null \
    || ! rg -F -n 'interactionChanged?(true)' \
      "$ROOT/Sources/PCRRemoteMac/TranscriptCollectionItem.swift" >/dev/null \
    || ! rg -F -n 'if agentSwitchInteractionInProgress {' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || ! rg -F -n 'deferredInteractionUpdate = parent' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null \
    || rg -F -n 'hostingView.rootView =' \
      "$ROOT/Sources/PCRRemoteMac/TranscriptCollectionItem.swift" >/dev/null; then
  printf '%s\n' \
    "Live Agent updates must keep one hosted switcher alive and defer collection mutations during a click." >&2
  exit 1
fi

if ! rg -F -n 'let commitsBeforeLoading = agentSelectionCommitsBeforeHydration(' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+RunControls.swift" >/dev/null; then
  printf '%s\n' \
    "Queued, pending, and running Agent branches must become visible before transcript hydration." >&2
  exit 1
fi

if ! rg -U -n 'if let run, let agent = model.selectedAgentSummary[\s\S]{0,260}ConversationResultDetails\([\s\S]{0,160}agent: agent' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null \
    || rg -n 'Task\.sleep|LazyVStack|ScrollViewReader' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null; then
  printf '%s\n' \
    "Result details must show the committed Agent directly, without a delayed candidate-list reveal." >&2
  exit 1
fi

if ! rg -U -n 'Button\(action: close\)[\s\S]{0,260}keyboardShortcut\(\.cancelAction\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null; then
  printf '%s\n' \
    "Result details must close with the standard Escape command." >&2
  exit 1
fi

if ! rg -U -n 'task\(id: revealRequestID\(viewportWidth: viewportWidth\)\)[\s\S]{0,700}revealSelectedAgent\([\s\S]{0,220}viewportWidth: viewportWidth' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n '"\(state.runID)|\(state.highlightedAgent ?? -1)|' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n '.id("agent-tabs-scroll-\(state.runID)-\(textSize.rawValue)")' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'private func leadingAgentIndex(viewportWidth: CGFloat)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'proxy.scrollTo(target, anchor: .leading)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n '.padding(.trailing, trailingRevealSpace(viewportWidth: viewportWidth))' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must keep the selected Agent candidate in view." >&2
  exit 1
fi

if ! rg -F -n 'scrollableAgentTabs(viewportWidth: geometry.size.width)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'revealRequestID(viewportWidth: viewportWidth)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -U -n 'onChange\(of: viewportWidth\)[\s\S]{0,520}revealSelectedAgent\([\s\S]{0,180}viewportWidth: viewportWidth[\s\S]{0,120}animated: false' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n '.layoutPriority(1)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null; then
  printf '%s\n' \
    "Window resizing must keep the selected Agent candidate visible." >&2
  exit 1
fi

if ! rg -U -n 'static func preferredHeight\(for textSize: AppTextSize\)[\s\S]{0,220}ConversationAgentSwitchBar\.minimumHeight\(for: textSize\)[\s\S]{0,80}6 \* textSize\.scale' \
      "$ROOT/Sources/PCRRemoteMac/TranscriptCollectionItem.swift" >/dev/null \
    || ! rg -F -n 'AgentSwitchCollectionItem.preferredHeight(for: parent.textSize)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationTimelineView.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must reserve enough height for the inline Agent selector." >&2
  exit 1
fi

if ! rg -U -n 'static func stickyHeight\(for textSize: AppTextSize\)[\s\S]{0,120}minimumHeight\(for: textSize\) \+ 16' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'ConversationAgentSwitchBar.stickyHeight(for: model.appTextSize)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'ConversationAgentSwitchBar.stickyHeight(for: appTextSize)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'showsStickyAgentSwitcher ? stickyAgentSwitcherHeight + 2 : 10' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -U -n 'inlineAgentSwitchPosition == \.aboveViewport[\s\S]{0,100}stickyAgentSwitcherHeight \+ 6' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must keep notices and conversation search below the sticky Agent selector." >&2
  exit 1
fi

if ! rg -F -n '@State private var inlineAgentSwitchPosition = TranscriptInlineControlPosition.visible' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -U -n 'AgentTranscriptView\([\s\S]{0,500}inlineAgentSwitchPosition: \$inlineAgentSwitchPosition' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -U -n 'private var showsStickyAgentSwitcher[\s\S]{0,220}inlineAgentSwitchPosition == \.aboveViewport' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'showsStickyAgentSwitcher ? stickyAgentSwitcherHeight + 2 : 10' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n '@Binding var inlineAgentSwitchPosition: TranscriptInlineControlPosition' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Conversation notices must only reserve space when the sticky Agent selector is visible." >&2
  exit 1
fi

if ! rg -U -n 'Text\(displayName\(agent\)\)[\s\S]{0,220}appScaledSystemFont[\s\S]{0,1300}frame\(height: 30 \* textSize\.scale\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'minHeight: Self.minimumHeight(for: textSize)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null \
    || ! rg -F -n 'frame(width: 28 * model.appTextSize.scale, height: 30 * model.appTextSize.scale)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale the Agent selector text and its stable control geometry together." >&2
  exit 1
fi

if rg -U -n 'struct ResultStatusGlyph[\s\S]*ProgressView' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null \
    || ! rg -U -n 'Text\(navigatorStatus\(agent\)\)[\s\S]{0,90}appScaledSystemFont[\s\S]{0,200}frame\(width: statusLabelWidth' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null; then
  printf '%s\n' "Agent execution states must use stable glyphs and visible phase labels, not loading controls." >&2
  exit 1
fi

if ! rg -U -n 'adjacentAgentButton\(state: state, delta: -1\)[\s\S]{0,80}adjacentAgentButton\(state: state, delta: 1\)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -U -n 'private func adjacentAgentButton\([\s\S]{0,500}selectAgent\(target\)' \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || rg -n 'adjacentAgentButton|info.circle' \
      "$ROOT/Sources/PCRRemoteMac/ConversationResultNavigator.swift" >/dev/null; then
  printf '%s\n' \
    "Agent navigation must live in the top toolbar, without duplicate inline controls." >&2
  exit 1
fi

if ! rg -U -n 'func setAppTextSize[\s\S]{0,320}disablesAnimations = true[\s\S]{0,180}withTransaction' \
    "$ROOT/Sources/PCRRemoteMac/AppModel.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must update atomically instead of animating clipped intermediate layouts." >&2
  exit 1
fi

if ! rg -U -n '@FocusedObject private var model: AppModel\?[\s\S]{0,1200}keyboardShortcut\("\+", modifiers: \.command\)[\s\S]{0,400}keyboardShortcut\("-", modifiers: \.command\)[\s\S]{0,400}keyboardShortcut\("0", modifiers: \.command\)' \
      "$ROOT/Sources/PCRRemoteMac/ClientWindows.swift" >/dev/null \
    || ! rg -F -n 'ClientWindowDisplayCommands()' \
      "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null; then
  printf '%s\n' \
    "The View menu must keep Command+, Command-, and Command-0 display controls." >&2
  exit 1
fi

if ! rg -U -n 'if !installedCachedProjection[\s\S]{0,500}installIndexedConversationProjection\([\s\S]{0,240}workspaceContextLoadingID' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Conversations.swift" >/dev/null \
    || ! rg -U -n 'if !installedCachedProjection,[\s\S]{0,300}conversation\.latestRunID == visibleRunID[\s\S]{0,300}installIndexedConversationProjection' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+WorkspaceContext.swift" >/dev/null; then
  printf '%s\n' \
    "Uncached conversation navigation must paint its indexed Turn before transcript hydration." >&2
  exit 1
fi

if ! rg -F -n 'Window(AppIdentity.productName, id: "main")' \
      "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null \
    || ! rg -F -n 'ClientWindowContent(windowID: id, delegate: appDelegate)' \
      "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null \
    || ! rg -F -n '_model = StateObject(wrappedValue: AppModel(windowID: windowID))' \
      "$ROOT/Sources/PCRRemoteMac/ClientWindows.swift" >/dev/null \
    || ! rg -F -n '.focusedSceneObject(model)' \
      "$ROOT/Sources/PCRRemoteMac/ClientWindows.swift" >/dev/null \
    || rg -U -n 'WindowGroup\([\s\S]{0,240}environmentObject\(model\)' \
      "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null; then
  printf '%s\n' \
    "The shared desktop model must not be exposed through mirrored multi-window sessions." >&2
  exit 1
fi

if rg -F -n '.disabled(!workspace.available)' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'if workspace\.available( \|\| model\.workspaceDirectoryIsCached)? \{[\s\S]{0,180}selectWorkspace\(workspace\.workspaceId\)[\s\S]{0,180}else \{[\s\S]{0,120}workspacePendingRemoval = workspace' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -F -n '"Folder moved or unavailable"' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Unavailable workspaces must remain actionable so users can remove stale bookmarks." >&2
  exit 1
fi

if ! rg -F -n 'case localMachine(String)' \
      "$ROOT/Sources/PCRRemoteMac/PcrdClient.swift" >/dev/null \
    || ! rg -U -n 'machine\.deviceId == resolvedLocalID[\s\S]{0,120}targetStore\.set\(\.localMachine\(machine\.deviceId\)\)' \
      "$ROOT/Sources/PCRRemoteMac/PcrdClient.swift" >/dev/null \
    || ! rg -U -n 'selectRelayMachine\([\s\S]{0,160}localMachineID: localDaemonMachineID' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null \
    || ! rg -F -n 'if resolved == localDaemonMachineID { return .localMachine(resolved) }' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+MachineContext.swift" >/dev/null; then
  printf '%s\n' \
    "The desktop app must use the local socket when the selected computer is this Mac." >&2
  exit 1
fi

if ! rg -U -n 'shouldUseLocalMachineFirst[\s\S]{0,360}activateLocalMachine\(localStatus\)[\s\S]{0,160}scheduleMachineDirectoryRefresh' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null \
    || ! rg -U -n 'scheduleMachineDirectoryRefresh[\s\S]{0,900}client\.relayMachines\(\)[\s\S]{0,700}local app remains fully usable when the Relay is offline' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null \
    || ! rg -F -n 'machineDirectoryRefreshTask: Task<Void, Never>?' \
      "$ROOT/Sources/PCRRemoteMac/AppModel.swift" >/dev/null; then
  printf '%s\n' \
    "Local startup must not wait for the public Relay machine directory." >&2
  exit 1
fi

if ! rg -U -n 'let requestedMachineID = forceDirectoryRefresh[\s\S]{0,320}shouldUseLocalMachineFirst[\s\S]{0,260}activateLocalMachine\(localStatus\)[\s\S]{0,420}Date\(\) < relayBootstrapRetryAfter' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null \
    || rg -U -n 'activateLocalMachine[\s\S]{0,1100}pruneRunPollingContexts' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+Relay.swift" >/dev/null; then
  printf '%s\n' \
    "Relay backoff and partial machine lists must never block or prune a healthy local session." >&2
  exit 1
fi

if ! rg -U -n 'private struct WorkspaceNavigationSidebar[\s\S]{0,2200}dynamicTypeSize\(\.large\)' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must keep the sidebar on a stable Dynamic Type category." >&2
    exit 1
fi

if ! rg -F -n '@Environment(\.scenePhase)' "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'task\(id: scenePhase\)[\s\S]{0,180}scenePhase == \.active[\s\S]{0,500}Task\.sleep\(for: visibleContextRefreshDelay\)' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'visibleContextRefreshDelay[\s\S]{0,900}activeRun\?\.isWorking == true[\s\S]{0,320}seconds\(needsFrequentRefresh \? 5 : 15\)' \
      "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Idle or background conversation windows must not perform a full five-second refresh loop." >&2
  exit 1
fi

if ! rg -F -n '.appScaledTextStyle(.callout)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'width: 330 * appTextSize.scale' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'height: 34 * appTextSize.scale' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'width: 22 * appTextSize.scale' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale the conversation find text and its control geometry together." >&2
    exit 1
fi

if ! rg -F -n '@Environment(\.appTextSize) private var appTextSize' \
    "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null \
    || ! rg -F -n '.controlSize(appTextSize.controlSize)' \
    "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null \
    || ! rg -F -n 'verticalSpacing: 10 * appTextSize.scale' \
    "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null \
    || ! rg -F -n 'frame(minHeight: 28 * appTextSize.scale)' \
    "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null \
    || ! rg -F -n '.appScaledTextStyle(.title2, weight: .semibold)' \
    "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null \
    || ! rg -F -n '.appScaledTextStyle(.callout)' \
    "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale Run Settings typography, controls, rows, and shared sheet headers together." >&2
    exit 1
fi

if ! rg -F -n '.controlSize(appTextSize.controlSize)' \
    "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null \
    || ! rg -F -n '.appScaledTextStyle(.callout, design: .monospaced)' \
    "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null \
    || ! rg -F -n '.frame(minHeight: 22 * appTextSize.scale)' \
    "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale the workspace browser text, controls, and path together." >&2
    exit 1
fi

if ! rg -U -n 'private struct MachinePairingView[\s\S]{0,2800}appScaledTextStyle\(\.title2, weight: \.semibold\)[\s\S]{0,1800}appScaledTextStyle\(\.body, design: \.monospaced\)[\s\S]{0,1400}frame\(minHeight: 92 \* appTextSize\.scale\)[\s\S]{0,1400}controlSize\(appTextSize\.controlSize\)' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || rg -n '\.font\(' "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale both computer and phone pairing sheets." >&2
    exit 1
fi

if rg -n '\.font\(' \
      "$ROOT/Sources/PCRRemoteMac/ConnectionViews.swift" \
      "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" \
      "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" \
      "$ROOT/Sources/PCRRemoteMac/WorkspaceFiles.swift" \
      | rg -v 'AppAppearance\.swift' >/dev/null \
    || ! rg -U -n 'struct WorkspaceDocumentView[\s\S]{0,300}@Environment\(\\\.appTextSize\)' \
      "$ROOT/Sources/PCRRemoteMac/WorkspaceFiles.swift" >/dev/null \
    || ! rg -U -n 'Markdown\(document\.content\)[\s\S]{0,300}markdownTextStyle[\s\S]{0,180}FontSize\(16 \* appTextSize\.scale\)[\s\S]{0,1800}controlSize\(appTextSize\.controlSize\)' \
      "$ROOT/Sources/PCRRemoteMac/WorkspaceFiles.swift" >/dev/null \
    || ! rg -F -n '.frame(height: (expanded ? 76 : ComposerQueueView.rowHeight) * textSize.scale)' \
      "$ROOT/Sources/PCRRemoteMac/ComposerQueueView.swift" >/dev/null \
    || ! rg -U -n 'struct FollowUpQueueView[\s\S]{0,10000}controlSize\(appTextSize\.controlSize\)' \
      "$ROOT/Sources/PCRRemoteMac/RunConfigurationViews.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must cover conversation states, result tools, queues, and workspace files." >&2
  exit 1
fi

if ! rg -U -n 'struct SettingsRootView[\s\S]{0,2600}appScaledTextStyle\(\.body\)' \
    "$ROOT/Sources/PCRRemoteMac/SettingsRootView.swift" >/dev/null \
    || rg -n '\.font\(' \
        "$ROOT/Sources/PCRRemoteMac/SettingsRootView.swift" \
        "$ROOT/Sources/PCRRemoteMac/AppPreferencesView.swift" \
        "$ROOT/Sources/PCRRemoteMac/ComputerSettingsView.swift" \
        "$ROOT/Sources/PCRRemoteMac/GeneralSettingsView.swift" \
        "$ROOT/Sources/PCRRemoteMac/StorageSettingsView.swift" \
        "$ROOT/Sources/PCRRemoteMac/FeedbackSettingsView.swift"; then
  printf '%s\n' \
    "Settings text must inherit or explicitly use app-scaled fonts so display zoom applies throughout the window." >&2
  exit 1
fi

if ! rg -U -n 'await model\.refreshVisibleContext\(\)\s*refreshWindowAfterInitialHydration\(\)' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'func refreshWindowAfterInitialHydration[\s\S]{0,700}layoutSubtreeIfNeeded\(\)[\s\S]{0,300}window\.display\(\)' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Initial conversation hydration must redraw the split view before polling begins." >&2
  exit 1
fi

if ! rg -U -n 'private struct WorkspaceSidebarRow[\s\S]{0,2800}appScaledSystemFont' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null \
    || ! rg -U -n 'private struct WorkspaceConversationSidebarRow[\s\S]{0,2600}appScaledSystemFont' \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Sidebar row labels must use app-scaled fonts without changing Dynamic Type categories." >&2
  exit 1
fi

if ! rg -U -n 'WorkbenchHorizontalScroll\([\s\S]{0,300}WorkbenchSplitColumns[\s\S]{0,800}ConversationResultsInspector[\s\S]{0,600}reviewContent' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || rg -n 'inspectorCoversConversation|\.inspector\(' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" \
    "$ROOT/Sources/PCRRemoteMac/ContentView.swift" >/dev/null; then
  printf '%s\n' \
    "Results and file review must be adjacent scrollable columns, never cover conversation controls." >&2
  exit 1
fi

if rg -n 'jumpToLatestControlLaneHeight' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n '.contentShape(Circle())' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -F -n 'hit === scroll || hit.isDescendant(of: scroll)' \
    "$ROOT/Tests/TranscriptLayout/TranscriptJumpOverlayTests.swift" >/dev/null; then
  printf '%s\n' \
    "The circular jump overlay must not reserve a footer or intercept adjacent transcript input." >&2
  exit 1
fi

if ! rg -U -n 'private func flushPersistentState\(\)[\s\S]{0,240}NativeMultilineEditorCommitRegistry\.shared\.commitPendingText\(\)[\s\S]{0,400}flushPromptDraftPersistence' \
    "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null; then
  printf '%s\n' "Termination must commit the live native editor before saving drafts." >&2
  exit 1
fi

if ! rg -U -n 'applicationShouldSaveSecureApplicationState[\s\S]{0,800}false[\s\S]{0,240}applicationShouldRestoreSecureApplicationState[\s\S]{0,160}false' \
    "$ROOT/Sources/PCRRemoteMac/PCRRemoteMacApp.swift" >/dev/null; then
  printf '%s\n' \
    "AppKit window restoration must not flash a stale transcript before cache hydration." >&2
  exit 1
fi

if ! rg -U -n 'focusedSceneValue\([\s\S]{0,160}conversationFindActions[\s\S]{0,420}requestConversationFind\(\.open\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Conversation find commands must remain available while results and files are open." >&2
  exit 1
fi

if ! rg -U -n 'ConversationFindActions\([\s\S]{0,180}isEnabled: !model\.selectedTimelineItems\.isEmpty' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null \
    || ! rg -U -n 'requestConversationFind[\s\S]{0,180}guard !model\.selectedTimelineItems\.isEmpty' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "Find commands must stay disabled when the selected conversation has no searchable content." >&2
  exit 1
fi

if ! rg -U -n 'arrow\.down[\s\S]{0,500}floatingControlSurface[\s\S]{0,500}floatingControlStroke' \
    "$ROOT/Sources/PCRRemoteMac/ConversationWorkspaceView.swift" >/dev/null; then
  printf '%s\n' \
    "The jump-to-latest button must keep a distinct surface and border in both appearances." >&2
  exit 1
fi

if ! rg -U -n 'case \.used:[\s\S]{0,420}EmptyView\(\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultActions.swift" >/dev/null \
    || ! rg -F -n 'conversationAgentDisplayStatus(run: run, agent: agent, language: language)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultDetails.swift" >/dev/null; then
  printf '%s\n' \
    "A used result must not repeat the same status in its header, metadata, and action lane." >&2
  exit 1
fi

if ! rg -F -n 'ResultDetailMetrics(agent: metricsAgent)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultDetails.swift" >/dev/null; then
  printf '%s\n' \
    "Result details must separate aggregate reasoning metrics from the usage breakdown." >&2
  exit 1
fi

if rg -n '\.font\(' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultActions.swift" \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultDetails.swift" \
    || ! rg -U -n 'struct ConversationResultsInspector[\s\S]{0,3400}appScaledTextStyle\(\.body\)' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null \
    || ! rg -U -n 'struct ResultStatusGlyph[\s\S]{0,300}@Environment\(\\\.appTextSize\)[\s\S]{0,900}textSize\.scale' \
    "$ROOT/Sources/PCRRemoteMac/ConversationResultsInspector.swift" >/dev/null; then
  printf '%s\n' \
    "Display zoom must scale result navigation, result details, and status glyphs together." >&2
  exit 1
fi

if ! rg -U -n 'private func beginConversationNavigationRequest\([\s\S]{0,300}conversationNavigationRequestID \+= 1[\s\S]{0,950}resumeHistoryRequestID \+= 1' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Conversations.swift" >/dev/null; then
  printf '%s\n' \
    "Conversation switches must invalidate stale Run and history requests together." >&2
  exit 1
fi

if [ "$(rg -c 'navigationAcceptsRun\(runID, requestID: expectedNavigationRequestID\)' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+RunState.swift")" -ne 3 ] \
    || ! rg -U -n 'func navigationAcceptsRun[\s\S]{0,250}requestID == conversationNavigationRequestID[\s\S]{0,1400}expected == runID' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Conversations.swift" >/dev/null \
    || ! rg -U -n 'guard let destination = conversationNavigationTarget else[\s\S]{0,400}!\$0.opensResumeHistory && \$0.latestRunID == runID' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Conversations.swift" >/dev/null; then
  printf '%s\n' \
    "Run loading must reject stale conversation navigation responses before applying them." >&2
  exit 1
fi

if ! rg -U -n 'if let selectedAgent,[\s\S]{0,240}selectedAgentDetailNeedsRefresh\([\s\S]{0,500}loadAgentDetail\(' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+RunState.swift" >/dev/null \
  || ! rg -U -n 'publishesLoadingState = selectedAgent == agent[\s\S]{0,220}if publishesLoadingState[\s\S]{0,500}if publishesLoadingState' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+RunState.swift" >/dev/null; then
  printf '%s\n' \
    "Live polling must fetch Agent detail only when it advanced and must not flash loading state over an existing transcript." >&2
  exit 1
fi

if ! rg -U -n 'recordRunInConversationIndex[\s\S]{0,1800}if workspaceConversations != nextConversations' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Conversations.swift" >/dev/null \
  || ! rg -U -n 'updateWorkspaceRunSummary[\s\S]{0,500}if workspaces != nextWorkspaces' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+RunState.swift" >/dev/null; then
  printf '%s\n' \
    "Unchanged run polls must not republish sidebar collections and redraw the conversation." >&2
  exit 1
fi

if ! rg -U -n 'selectedTimelineBlocks[\s\S]{0,700}transcriptTimelineProjectionInput == input[\s\S]{0,1100}transcriptTimelineProjectionGeneration = presentationProjectionSequence' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Presentation.swift" >/dev/null \
  || ! rg -U -n 'selectedTimelineItems[\s\S]{0,500}conversationTimelineItemsProjectionInput == input' \
    "$ROOT/Sources/PCRRemoteMac/AppModel+Presentation.swift" >/dev/null; then
  printf '%s\n' \
    "Stable SwiftUI redraws must reuse the formatted conversation projection." >&2
  exit 1
fi

if ! rg -U -n 'static func == \(lhs: Self, rhs: Self\)[\s\S]{0,1800}guard lhs\.isTerminal \|\| rhs\.isTerminal else \{ return true \}' \
      "$ROOT/Sources/PCRRemoteMac/Transcript.swift" >/dev/null \
  || ! rg -U -n 'commitsBeforeLoading = agentSelectionCommitsBeforeHydration\([\s\S]{0,180}agent: targetAgent[\s\S]{0,700}commitVisibleAgentSelection\(agent, keepPending: true, selectedWhileWorking: run.isWorking\)[\s\S]{0,1800}allowInactiveSelection: true' \
      "$ROOT/Sources/PCRRemoteMac/AppModel+RunControls.swift" >/dev/null; then
  printf '%s\n' \
    "Unfinished Agent selection must commit immediately and ignore volatile switcher counters." >&2
  exit 1
fi

mkdir -p "$(dirname -- "$OUTPUT")"
if rg -U -n 'agentPresentationMatches[\s\S]{0,1600}(AgentActivityPresentation\.clock|parseInternetDate)' \
    "$ROOT/Sources/PCRRemoteMac/Transcript.swift" >/dev/null \
    || rg -n '@Published var prompt( =|History)' "$ROOT/Sources/PCRRemoteMac/AppModel.swift" >/dev/null \
    || ! rg -F -n '@ObservedObject var draft: PromptDraftState' "$ROOT/Sources/PCRRemoteMac/PromptComposer.swift" >/dev/null; then
  printf '%s\n' "Typing must not publish the reader; Agent equality must not parse or format dates." >&2
  exit 1
fi
compile_swift \
  "$ROOT/Sources/PCRRemoteMac/AppLanguage.swift" \
  "$ROOT/Sources/PCRRemoteMac/AppAppearance.swift" \
  "$ROOT/Sources/PCRRemoteMac/ParallelCodexVisualStyle.swift" \
  "$ROOT/Sources/PCRRemoteMac/ExecutionActivityIndicator.swift" \
  "$ROOT/Sources/PCRRemoteMac/MachineContext.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConnectionModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/WorkspaceModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/RunSettingsModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/GeneratedConversationProtocol.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentActivityReaderState.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentQuestions.swift" \
  "$ROOT/Sources/PCRRemoteMac/Models.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentDisplayName.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentDiffSummary.swift" \
  "$ROOT/Sources/PCRRemoteMac/RunProtocolModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/FollowUpProtocolModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/DesktopState.swift" \
  "$ROOT/Sources/PCRRemoteMac/Transcript.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationFind.swift" \
  "$ROOT/Sources/PCRRemoteMac/TranscriptLayoutMetricsCache.swift" \
  "$ROOT/Sources/PCRRemoteMac/TranscriptScrollSupport.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationHistory.swift" \
  "$ROOT/Sources/PCRRemoteMac/LatexMarkdown.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationPresentation.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationContextTransition.swift" \
  "$ROOT/Sources/PCRRemoteMac/InternetDate.swift" \
  "$ROOT/Sources/PCRRemoteMac/WorkspaceConversations.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationSearch.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConversationSurfaceState.swift" \
  "$ROOT/Sources/PCRRemoteMac/PcrdClient.swift" \
  "$ROOT/Sources/PCRRemoteMac/ClientBlockingIO.swift" \
  "$ROOT/Sources/PCRRemoteMac/PromptSubmissionPlan.swift" \
  "$ROOT/Sources/PCRRemoteMac/WorkspaceRunState.swift" \
  "$ROOT/Sources/PCRRemoteMac/DomainStateRepositories.swift" \
  "$ROOT/Sources/PCRRemoteMac/NativeMultilineEditor.swift" \
  "$ROOT/Sources/PCRRemoteMac/WorkspaceLink.swift" \
  "$ROOT/Tests/ProtocolContract/main.swift" \
  -o "$OUTPUT"
"$OUTPUT"

RELAY_OUTPUT="$ROOT/.build/pcr-macos-desktop-state-tests"
compile_swift \
  -parse-as-library \
  "$ROOT/Sources/PCRRemoteMac/InternetDate.swift" \
  "$ROOT/Sources/PCRRemoteMac/AppLanguage.swift" \
  "$ROOT/Sources/PCRRemoteMac/MachineContext.swift" \
  "$ROOT/Sources/PCRRemoteMac/ConnectionModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/WorkspaceModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/RunSettingsModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/GeneratedConversationProtocol.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentActivityReaderState.swift" \
  "$ROOT/Sources/PCRRemoteMac/AgentQuestions.swift" \
  "$ROOT/Sources/PCRRemoteMac/Models.swift" \
  "$ROOT/Sources/PCRRemoteMac/RunProtocolModels.swift" \
  "$ROOT/Sources/PCRRemoteMac/DesktopState.swift" \
  "$ROOT/Sources/PCRRemoteMac/PromptSubmissionPlan.swift" \
  "$ROOT/Sources/PCRRemoteMac/PcrdClient.swift" \
  "$ROOT/Sources/PCRRemoteMac/ClientBlockingIO.swift" \
  "$ROOT/Tests/RelayBootstrap/main.swift" \
  -o "$RELAY_OUTPUT"
"$RELAY_OUTPUT"

bash "$ROOT/scripts/test-transcript-layout.sh"
bash "$ROOT/scripts/test-app-icon.sh"
