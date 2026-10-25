package com.parallelcodexrunner.remote.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.parallelcodexrunner.remote.R
import com.parallelcodexrunner.remote.data.DirectoryEntry
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filterNotNull
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
internal fun WorkspaceScreen(
    state: MainUiState,
    viewModel: MainViewModel,
) {
    val scanPairing = rememberPairingScanAction(viewModel)
    val density = LocalDensity.current
    val applicationContext = LocalContext.current.applicationContext
    val keyboardVisible = WindowInsets.ime.getBottom(density) > 0
    val conversationViewports = remember(applicationContext) {
        ConversationViewportStore(applicationContext)
    }
    val paneStore = remember(applicationContext) {
        WorkspacePaneStore(applicationContext)
    }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) {
        conversationViewports.saveAll()
    }
    var rememberedRunContext by rememberSaveable {
        mutableStateOf(paneStore.rememberedRunContext())
    }
    var selectedPane by rememberSaveable {
        mutableStateOf(
            if (shouldRestoreRunPane(
                    rememberedRunContext,
                    state.selectedMacId,
                    state.selectedWorkspaceId,
                )
            ) {
                PANE_RUN
            } else {
                PANE_WORKSPACES
            },
        )
    }
    var pendingWorkspaceId by rememberSaveable { mutableStateOf<String?>(null) }
    var workspaceSearchPresented by rememberSaveable { mutableStateOf(false) }
    var workspaceSearchText by rememberSaveable { mutableStateOf("") }

    fun openRunPane() {
        selectedPane = PANE_RUN
        rememberedRunContext = paneStore.rememberRun(
            state.selectedMacId,
            state.selectedWorkspaceId,
        )
    }

    fun leaveRunPane(destination: String = PANE_WORKSPACES) {
        rememberedRunContext = null
        paneStore.clearRun()
        selectedPane = destination
    }

    LaunchedEffect(
        rememberedRunContext,
        state.selectedMacId,
        state.selectedWorkspaceId,
        selectedPane,
    ) {
        if (selectedPane == PANE_WORKSPACES && shouldRestoreRunPane(
                rememberedRunContext,
                state.selectedMacId,
                state.selectedWorkspaceId,
            )
        ) {
            openRunPane()
        }
    }

    LaunchedEffect(state.requestedWorkspaceOpenId, state.selectedWorkspaceId) {
        val requested = state.requestedWorkspaceOpenId ?: return@LaunchedEffect
        if (state.selectedWorkspaceId == requested) {
            pendingWorkspaceId = null
            openRunPane()
            viewModel.consumeWorkspaceOpenRequest(requested)
        }
    }

    LaunchedEffect(
        state.selectedWorkspaceId,
        state.workspaceSelectionId,
        pendingWorkspaceId,
    ) {
        val pending = pendingWorkspaceId
        when {
            pending != null && state.selectedWorkspaceId == pending -> {
                pendingWorkspaceId = null
                openRunPane()
            }
            pending != null && state.workspaceSelectionId == null -> {
                pendingWorkspaceId = null
            }
            state.selectedWorkspaceId == null && selectedPane == PANE_RUN -> {
                conversationViewports.saveAll()
                leaveRunPane()
            }
        }
    }

    LaunchedEffect(selectedPane, state.selectedMacId) {
        val interval = workspaceOverviewRefreshInterval(selectedPane) ?: return@LaunchedEffect
        if (state.selectedMacId == null) return@LaunchedEffect
        while (true) {
            // A completed turn stops its event poll, but another client can
            // still append a new turn. Presence alone cannot discover it.
            viewModel.refreshWorkspaceOverview()
            delay(interval)
        }
    }

    LaunchedEffect(
        selectedPane,
        state.selectedWorkspaceId,
        state.visibleRunId,
        state.unreadRunCompletions,
    ) {
        if (selectedPane == PANE_RUN) {
            viewModel.ensureVisibleRunPolling()
            viewModel.markWorkspaceRunCompletionViewed(
                state.selectedWorkspaceId,
                state.visibleRunId,
            )
        }
    }

    BackHandler(enabled = state.browserOpen) {
        viewModel.closeBrowser()
    }
    BackHandler(
        enabled = !state.browserOpen &&
            selectedPane == PANE_WORKSPACES &&
            workspaceSearchPresented,
    ) {
        workspaceSearchPresented = false
        workspaceSearchText = ""
    }
    BackHandler(enabled = !state.browserOpen && selectedPane == PANE_RUN) {
        conversationViewports.saveAll()
        leaveRunPane()
    }
    BackHandler(enabled = !state.browserOpen && selectedPane == PANE_SETTINGS) {
        leaveRunPane()
    }

    if (state.browserOpen) {
        DirectoryBrowser(state, viewModel)
        return
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        bottomBar = {
            if (!keyboardVisible && selectedPane != PANE_RUN) {
                AppNavigationBar(
                    selectedPane = selectedPane,
                    updateAvailable = state.availableUpdate != null,
                    onSelect = {
                        pendingWorkspaceId = null
                        leaveRunPane(it)
                    },
                )
            }
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .statusBarsPadding()
                .then(
                    if (selectedPane == PANE_RUN) Modifier.navigationBarsPadding()
                    else Modifier
                )
                .imePadding(),
        ) {
            Column(Modifier.fillMaxSize()) {
                if (selectedPane == PANE_WORKSPACES) {
                    AppIdentityBar(
                        state = state,
                        viewModel = viewModel,
                        searchPresented = workspaceSearchPresented,
                        searchText = workspaceSearchText,
                        onSearchTextChange = { workspaceSearchText = it },
                        onSearchPresented = {
                            workspaceSearchPresented = it
                            if (!it) workspaceSearchText = ""
                        },
                    )
                }

                when (selectedPane) {
                    PANE_SETTINGS -> Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.TopCenter,
                    ) {
                        SettingsPane(
                            state = state,
                            viewModel = viewModel,
                            scanPairing = scanPairing,
                            modifier = Modifier
                                .widthIn(max = 720.dp)
                                .fillMaxWidth(),
                        )
                    }
                    PANE_RUN -> CompactRunPane(
                        state = state,
                        viewModel = viewModel,
                        viewportStore = conversationViewports,
                    )
                    else -> Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.TopCenter,
                    ) {
                        LazyColumn(
                            modifier = Modifier
                                .widthIn(max = 720.dp)
                                .fillMaxSize(),
                            contentPadding = PaddingValues(
                                start = 16.dp,
                                end = 16.dp,
                                top = 14.dp,
                                bottom = 24.dp,
                            ),
                        ) {
                            item {
                                DeviceWorkspacePane(
                                    state = state,
                                    viewModel = viewModel,
                                    searchText = workspaceSearchText,
                                    onWorkspaceSelected = { workspaceId ->
                                        if (workspaceId == state.selectedWorkspaceId) {
                                            openRunPane()
                                        } else {
                                            rememberedRunContext = null
                                            paneStore.clearRun()
                                            pendingWorkspaceId = workspaceId
                                            viewModel.selectWorkspace(workspaceId)
                                        }
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AppIdentityBar(
    state: MainUiState,
    viewModel: MainViewModel,
    searchPresented: Boolean,
    searchText: String,
    onSearchTextChange: (String) -> Unit,
    onSearchPresented: (Boolean) -> Unit,
) {
    val focusRequester = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current

    LaunchedEffect(searchPresented) {
        if (searchPresented) {
            focusRequester.requestFocus()
            keyboard?.show()
        } else {
            keyboard?.hide()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 58.dp)
                .padding(start = 16.dp, end = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (searchPresented) {
                Icon(
                    Icons.Default.Search,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .padding(start = 8.dp)
                        .size(21.dp),
                )
                Spacer(Modifier.width(12.dp))
                BasicTextField(
                    value = searchText,
                    onValueChange = onSearchTextChange,
                    modifier = Modifier
                        .weight(1f)
                        .focusRequester(focusRequester),
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyLarge.copy(
                        color = MaterialTheme.colorScheme.onSurface,
                    ),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                    decorationBox = { field ->
                        Box(contentAlignment = Alignment.CenterStart) {
                            if (searchText.isEmpty()) {
                                Text(
                                    appText("搜索工作区", "Search workspaces"),
                                    style = MaterialTheme.typography.bodyLarge,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            field()
                        }
                    },
                )
                IconButton(onClick = { onSearchPresented(false) }) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = appText("关闭搜索", "Close search"),
                    )
                }
            } else {
                BrandTile(34.dp)
                Spacer(Modifier.width(10.dp))
                Text(
                    PRODUCT_NAME,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                IconButton(onClick = { onSearchPresented(true) }) {
                    Icon(
                        Icons.Default.Search,
                        contentDescription = appText("搜索工作区", "Search workspaces"),
                    )
                }
                IconButton(
                    onClick = viewModel::refreshMacs,
                    enabled = !state.macRefreshInProgress,
                ) {
                    if (state.macRefreshInProgress) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(
                            Icons.Default.Refresh,
                            contentDescription = appText(
                                "刷新设备和工作区",
                                "Refresh computers and workspaces",
                            ),
                        )
                    }
                }
            }
        }
        state.initialSyncProgress?.let { progress ->
            InitialSyncIndicator(
                progress = progress,
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
            )
        }
    }
}

@Composable
private fun DeviceWorkspacePane(
    state: MainUiState,
    viewModel: MainViewModel,
    searchText: String,
    onWorkspaceSelected: (String) -> Unit,
) {
    var expandedMacId by rememberSaveable { mutableStateOf(state.selectedMacId) }
    val visibleWorkspaces = filterWorkspaces(state.workspaces, searchText)
    val searching = searchText.isNotBlank()

    LaunchedEffect(state.selectedMacId) {
        state.selectedMacId?.let { expandedMacId = it }
    }

    SectionTitle(title = appText("设备与工作区", "Computers and workspaces"))
    GroupedSurface {
        if (state.macs.isEmpty()) {
            EmptyListRow("尚未连接设备", Icons.Default.Computer)
        } else {
            state.macs.forEachIndexed { macIndex, mac ->
                val selected = mac.deviceId == state.selectedMacId
                val expanded = selected && (expandedMacId == mac.deviceId || searching)

                DeviceRow(
                    name = mac.name,
                    online = mac.online,
                    selected = selected,
                    expanded = expanded,
                    loading = state.macSelectionId == mac.deviceId,
                    workspaceCount = if (selected) state.workspaces.size else null,
                    enabled = state.macSelectionId == null,
                    showAddWorkspace = expanded && mac.online != false,
                    onClick = {
                        if (selected) {
                            expandedMacId = if (expanded) null else mac.deviceId
                        } else {
                            expandedMacId = mac.deviceId
                            viewModel.selectMac(mac.deviceId)
                        }
                    },
                    onAddWorkspace = viewModel::openBrowser,
                )

                if (expanded) {
                    GroupDivider(startPadding = 50.dp)
                    val hasWorkspaces = state.workspaces.isNotEmpty()
                    val duplicateWorkspaceNames = state.workspaces
                        .groupingBy { it.name }
                        .eachCount()
                    if (mac.online == false) {
                        OfflineMacRow(hasCachedWorkspaces = hasWorkspaces)
                        if (hasWorkspaces) GroupDivider(startPadding = 66.dp)
                    }
                    when {
                        mac.online == false && !hasWorkspaces -> Unit
                        state.macSelectionId == mac.deviceId && state.workspaces.isEmpty() -> {
                            WorkspaceLoadingRow()
                        }
                        state.workspaces.isEmpty() -> EmptyWorkspaceRow()
                        visibleWorkspaces.isEmpty() -> WorkspaceSearchEmptyRow(searchText)
                        else -> visibleWorkspaces.forEachIndexed { workspaceIndex, workspace ->
                            WorkspaceRow(
                                workspace = workspace,
                                selected = workspace.workspaceId == state.selectedWorkspaceId,
                                loading = workspace.workspaceId == state.workspaceSelectionId,
                                selectionEnabled = workspaceRowSelectionEnabled(
                                    workspaceAvailable = workspace.available,
                                    selectionInProgress = state.workspaceSelectionId != null,
                                ),
                                queuedPrompts = state.workspaceQueueCounts[
                                    workspace.workspaceId
                                ] ?: 0,
                                pendingPromptState = state.workspacePromptStates[
                                    workspace.workspaceId
                                ],
                                showsLocation = (duplicateWorkspaceNames[workspace.name] ?: 0) > 1,
                                actionsEnabled = workspaceRowActionsEnabled(
                                    deviceOnline = mac.online != false,
                                    selectionInProgress = state.workspaceSelectionId != null,
                                ),
                                unread = state.isWorkspaceRunCompletionUnread(workspace),
                                onSelect = { onWorkspaceSelected(workspace.workspaceId) },
                                onRemove = {
                                    viewModel.requestWorkspaceRemoval(workspace.workspaceId)
                                },
                                modifier = Modifier.padding(start = 14.dp),
                            )
                            if (workspaceIndex < visibleWorkspaces.lastIndex) {
                                GroupDivider(startPadding = 66.dp)
                            }
                        }
                    }
                }

                if (macIndex < state.macs.lastIndex) {
                    GroupDivider()
                }
            }
        }
    }
}

@Composable
private fun WorkspaceSearchEmptyRow(query: String) {
    Text(
        appText(
            "没有与“${query.trim()}”匹配的工作区",
            "No workspaces match \"${query.trim()}\"",
        ),
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 64.dp, end = 16.dp, top = 16.dp, bottom = 16.dp),
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

internal fun filterWorkspaces(
    workspaces: List<RemoteWorkspace>,
    query: String,
): List<RemoteWorkspace> {
    val normalized = query.trim()
    if (normalized.isEmpty()) return workspaces
    return workspaces.filter { workspace ->
        workspace.name.contains(normalized, ignoreCase = true) ||
            workspace.path.contains(normalized, ignoreCase = true)
    }
}

@Composable
private fun DeviceRow(
    name: String,
    online: Boolean?,
    selected: Boolean,
    expanded: Boolean,
    loading: Boolean,
    workspaceCount: Int?,
    enabled: Boolean,
    showAddWorkspace: Boolean,
    onClick: () -> Unit,
    onAddWorkspace: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.045f)
                else Color.Transparent,
            )
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 13.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(36.dp)
                .background(
                    if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.11f)
                    else MaterialTheme.colorScheme.surfaceVariant,
                    RoundedCornerShape(8.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Default.Computer,
                contentDescription = null,
                tint = if (selected) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                modifier = Modifier.size(20.dp),
            )
            if (online != null) {
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .size(10.dp)
                        .background(MaterialTheme.colorScheme.surface, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Box(
                        Modifier
                            .size(7.dp)
                            .background(
                                if (online) Color(0xFF22B573)
                                else MaterialTheme.colorScheme.outline,
                                CircleShape,
                            )
                    )
                }
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                name,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                when {
                    loading -> appText("正在加载工作区…", "Loading workspaces…")
                    online == false -> appText("设备未连接", "Computer offline")
                    workspaceCount != null -> appText(
                        "$workspaceCount 个工作区",
                        "$workspaceCount workspaces",
                    )
                    online == true -> appText("在线", "Online")
                    else -> appText("已配对", "Paired")
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier
                    .padding(horizontal = 3.dp)
                    .size(18.dp),
                strokeWidth = 2.dp,
            )
        } else {
            if (showAddWorkspace) {
                IconButton(
                    onClick = onAddWorkspace,
                    enabled = enabled,
                    modifier = Modifier.size(40.dp),
                ) {
                    Icon(
                        Icons.Default.Add,
                        contentDescription = appText(
                            "为 $name 添加工作区",
                            "Add a workspace to $name",
                        ),
                        modifier = Modifier.size(21.dp),
                    )
                }
            }
            Icon(
                if (expanded) Icons.Default.KeyboardArrowUp
                else Icons.Default.KeyboardArrowDown,
                contentDescription = if (expanded) {
                    appText("收起工作区", "Collapse workspaces")
                } else {
                    appText("展开工作区", "Expand workspaces")
                },
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun OfflineMacRow(hasCachedWorkspaces: Boolean) {
    Text(
        if (hasCachedWorkspaces) {
            appText(
                "设备暂未连接，以下为上次同步的工作区。",
                "This computer is offline. Showing the last synced workspaces.",
            )
        } else {
            appText(
                "设备暂未连接，恢复连接后会自动同步工作区。",
                "This computer is offline. Workspaces will sync when it reconnects.",
            )
        },
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 64.dp, end = 16.dp, top = 16.dp, bottom = 16.dp),
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun WorkspaceLoadingRow() {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 64.dp, end = 16.dp, top = 15.dp, bottom = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        Text(
            appText("正在加载工作区…", "Loading workspaces…"),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun EmptyWorkspaceRow() {
    Text(
        appText("尚未添加工作区", "No workspaces added"),
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 64.dp, end = 16.dp, top = 16.dp, bottom = 16.dp),
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun AppNavigationBar(
    selectedPane: String,
    updateAvailable: Boolean,
    onSelect: (String) -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 0.dp,
        shadowElevation = 3.dp,
    ) {
        Column {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .height(54.dp),
            ) {
                CompactNavigationItem(
                    label = appText("工作区", "Workspaces"),
                    icon = if (selectedPane == PANE_WORKSPACES) {
                        Icons.Default.FolderOpen
                    } else {
                        Icons.Default.Folder
                    },
                    selected = selectedPane == PANE_WORKSPACES,
                    onClick = { onSelect(PANE_WORKSPACES) },
                    modifier = Modifier.weight(1f),
                )
                CompactNavigationItem(
                    label = appText("设置", "Settings"),
                    icon = Icons.Default.Settings,
                    selected = selectedPane == PANE_SETTINGS,
                    onClick = { onSelect(PANE_SETTINGS) },
                    badge = updateAvailable,
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun CompactNavigationItem(
    label: String,
    icon: ImageVector,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    badge: Boolean = false,
) {
    val color = if (selected) {
        MaterialTheme.colorScheme.primary
    } else {
        MaterialTheme.colorScheme.onSurfaceVariant
    }
    Column(
        modifier = modifier
            .fillMaxHeight()
            .clickable(onClick = onClick),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            modifier = Modifier
                .width(40.dp)
                .height(24.dp)
                .background(
                    if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
                    else Color.Transparent,
                    RoundedCornerShape(8.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                icon,
                contentDescription = label,
                tint = color,
                modifier = Modifier.size(19.dp),
            )
            if (badge) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .size(7.dp)
                        .background(SystemRed, CircleShape),
                )
            }
        }
        Text(
            label,
            color = color,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
        )
    }
}

@Composable
internal fun rememberPairingScanAction(viewModel: MainViewModel): () -> Unit {
    val launcher = rememberLauncherForActivityResult(ScanContract()) { result ->
        result.contents?.takeIf(String::isNotBlank)?.let(viewModel::handlePairingCode)
    }
    val options = remember {
        ScanOptions().apply {
            setDesiredBarcodeFormats(listOf("QR_CODE"))
            setPrompt("扫描目标设备上显示的配对码")
            setBeepEnabled(false)
            setOrientationLocked(false)
        }
    }
    return { launcher.launch(options) }
}

@Composable
private fun WorkspaceRow(
    workspace: RemoteWorkspace,
    selected: Boolean,
    loading: Boolean,
    selectionEnabled: Boolean,
    queuedPrompts: Int,
    pendingPromptState: String?,
    showsLocation: Boolean,
    actionsEnabled: Boolean,
    unread: Boolean,
    onSelect: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    val language = LocalAppLanguage.current
    val runLabel = workspaceRunLabel(
        workspace = workspace,
        queuedPrompts = queuedPrompts,
        pendingPromptState = pendingPromptState,
        language = language,
    )
    val locationHint = if (workspace.available && showsLocation) {
        workspaceLocationHint(workspace.path)
    } else {
        null
    }
    val secondaryDescription = listOfNotNull(
        runLabel,
        if (workspace.available) workspace.path else language.text(
            "此设备当前无法访问",
            "Unavailable on this computer",
        ),
    ).joinToString("，")
    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(
                if (selected && workspace.available) {
                    MaterialTheme.colorScheme.primary.copy(alpha = 0.075f)
                }
                else Color.Transparent,
            )
            .clickable(enabled = selectionEnabled, onClick = onSelect)
            .semantics {
                contentDescription = listOf(workspace.name, secondaryDescription)
                    .filter(String::isNotEmpty)
                    .joinToString("，")
            }
            .padding(start = 12.dp, top = 10.dp, bottom = 10.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(34.dp)
                .background(
                    MaterialTheme.colorScheme.primary.copy(alpha = 0.085f),
                    RoundedCornerShape(8.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                if (selected) Icons.Default.FolderOpen else Icons.Default.Folder,
                contentDescription = null,
                tint = if (workspace.available) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                modifier = Modifier.size(19.dp),
            )
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    workspace.name,
                    modifier = Modifier.weight(1f),
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (workspace.available) {
                        MaterialTheme.colorScheme.onSurface
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (unread) {
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .background(SystemGreen, CircleShape),
                    )
                }
            }
            if (runLabel != null || locationHint != null || !workspace.available) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    runLabel?.let { label ->
                        Text(
                            label,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Medium,
                            color = workspaceRunColor(workspace, pendingPromptState),
                            maxLines = 1,
                        )
                    }
                    val visibleLocation = when {
                        !workspace.available -> language.text(
                            "此设备当前无法访问",
                            "Unavailable on this computer",
                        )
                        else -> locationHint
                    }
                    visibleLocation?.let { location ->
                        if (runLabel != null) {
                            Text(
                                "  ·  ",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(
                            location,
                            modifier = Modifier.weight(1f, fill = false),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier.size(18.dp),
                strokeWidth = 2.dp,
            )
        } else if (selected) {
            Icon(
                Icons.Default.Check,
                contentDescription = appText("当前工作区", "Current workspace"),
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(20.dp),
            )
        }
        if (actionsEnabled) Box {
            IconButton(
                onClick = { menuExpanded = true },
            ) {
                Icon(
                    Icons.Default.MoreVert,
                    contentDescription = appText("工作区操作", "Workspace actions"),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            DropdownMenu(
                expanded = menuExpanded,
                onDismissRequest = { menuExpanded = false },
            ) {
                DropdownMenuItem(
                    text = {
                        Text(
                            appText("移除工作区", "Remove workspace"),
                            color = MaterialTheme.colorScheme.error,
                        )
                    },
                    leadingIcon = {
                        Icon(
                            Icons.Default.Delete,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.error,
                        )
                    },
                    onClick = {
                        menuExpanded = false
                        onRemove()
                    },
                )
            }
        }
    }
}

internal fun workspaceRowSelectionEnabled(
    workspaceAvailable: Boolean,
    selectionInProgress: Boolean,
): Boolean = workspaceAvailable && !selectionInProgress

internal fun workspaceRowActionsEnabled(
    deviceOnline: Boolean,
    selectionInProgress: Boolean,
): Boolean = deviceOnline && !selectionInProgress

internal fun workspaceLocationHint(path: String): String? {
    val normalized = path.trimEnd('/')
    if (normalized.isEmpty()) return null
    val parent = normalized.substringBeforeLast('/', missingDelimiterValue = "")
        .trimEnd('/')
    if (parent.isEmpty()) return null
    return parent.substringAfterLast('/').ifEmpty { parent }
}

internal fun workspaceRunLabel(
    workspace: RemoteWorkspace,
    queuedPrompts: Int = 0,
    pendingPromptState: String? = null,
    language: AppLanguage = AppLanguage.CHINESE,
): String? {
    when (pendingPromptState) {
        PendingPromptSubmission.STATUS_FAILED -> return language.text("发送失败", "Send failed")
        PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION ->
            return language.text("等待确认", "Waiting for confirmation")
        PendingPromptSubmission.STATUS_PENDING,
        PendingPromptSubmission.STATUS_RETRYING ->
            return language.text("正在提交", "Sending")
    }
    if (queuedPrompts > 0) return language.text("排队 $queuedPrompts", "$queuedPrompts queued")
    if (workspace.lastRunId.isBlank()) return null
    val status = workspace.runStatus.trim().lowercase()
    val progress = if (workspace.runAgentsTotal > 0) {
        " ${workspace.runAgentsFinished}/${workspace.runAgentsTotal}"
    } else {
        ""
    }
    return when {
        status in setOf("preparing", "starting") -> language.text(
            "排队中$progress",
            "Queued$progress",
        )
        workspace.runWorkerActive || status in setOf("running", "stopping", "finalizing") ->
            language.text("运行中$progress", "Running$progress")
        status in setOf("failed", "error") -> language.text("运行失败", "Failed")
        status in setOf("cancelled", "canceled", "stopped", "interrupted") ->
            language.text("已停止", "Stopped")
        status == "expired" -> language.text("已过期", "Expired")
        status in setOf("sync_ambiguous", "cleanup_failed") ->
            language.text("需要处理", "Needs attention")
        status == "discarded" -> language.text("已丢弃", "Discarded")
        status in setOf("completed", "finalized") &&
            workspace.runAgentsTotal > 0 &&
            workspace.runAgentsFailed >= workspace.runAgentsTotal ->
            language.text("运行失败", "Failed")
        status in setOf("completed", "finalized") && workspace.runAgentsFailed > 0 ->
            language.text(
                "已完成 · ${workspace.runAgentsFailed} 失败",
                "Completed · ${workspace.runAgentsFailed} failed",
            )
        status in setOf("completed", "finalized") -> language.text("已完成", "Completed")
        else -> null
    }
}

internal fun pendingSubmissionLabel(
    status: String,
    language: AppLanguage = AppLanguage.CHINESE,
): String = when (status) {
    PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION ->
        language.text("等待空间确认", "Waiting for storage confirmation")
    PendingPromptSubmission.STATUS_FAILED -> language.text("发送失败", "Send failed")
    else -> language.text("正在提交", "Sending")
}

@Composable
private fun workspaceRunColor(
    workspace: RemoteWorkspace,
    pendingPromptState: String?,
): Color = when {
    pendingPromptState == PendingPromptSubmission.STATUS_FAILED -> SystemRed
    pendingPromptState in setOf(
        PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION,
        PendingPromptSubmission.STATUS_PENDING,
        PendingPromptSubmission.STATUS_RETRYING,
    ) -> MaterialTheme.colorScheme.primary
    workspace.runStatus.lowercase() in setOf(
        "failed",
        "error",
        "stopped",
        "cancelled",
        "canceled",
        "interrupted",
        "expired",
        "sync_ambiguous",
        "cleanup_failed",
    ) -> SystemRed
    workspace.runWorkerActive || workspace.runStatus.lowercase() in setOf(
        "preparing",
        "starting",
        "running",
        "stopping",
        "finalizing",
    ) -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}


@Composable
private fun SectionTitle(title: String, action: (@Composable () -> Unit)? = null) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 2.dp, end = 0.dp, bottom = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onBackground,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.weight(1f))
        action?.invoke()
    }
}

@Composable
private fun GroupedSurface(content: @Composable () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 1.dp,
    ) {
        Column { content() }
    }
}
