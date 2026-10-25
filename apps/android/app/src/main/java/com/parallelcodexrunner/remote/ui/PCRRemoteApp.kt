package com.parallelcodexrunner.remote.ui

import android.app.Activity
import com.parallelcodexrunner.remote.BackgroundConnectionService
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
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
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
import androidx.core.view.WindowCompat
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
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

internal const val PRODUCT_NAME = "Parallel Codex"
private const val MOBILE_ROLE = "移动工作台"
internal const val PANE_WORKSPACES = "workspaces"
internal const val PANE_RUN = "run"
internal const val PANE_SETTINGS = "settings"

internal const val TRANSIENT_MESSAGE_TIMEOUT_MILLIS = 2_000L

internal fun nextConversationFollowLatest(
    current: Boolean,
    scrolling: Boolean,
    programmatic: Boolean,
    atEnd: Boolean,
): Boolean = when {
    scrolling && !programmatic -> false
    !scrolling && atEnd -> true
    else -> current
}

internal fun conversationIsAtEnd(
    totalItemsCount: Int,
    lastVisibleIndex: Int?,
    lastVisibleEnd: Int?,
    viewportEndOffset: Int,
): Boolean = totalItemsCount > 0 &&
    lastVisibleIndex == totalItemsCount - 1 &&
    lastVisibleEnd != null &&
    lastVisibleEnd <= viewportEndOffset + 2

internal fun conversationIsAtEnd(listState: LazyListState): Boolean {
    val layout = listState.layoutInfo
    val lastVisible = layout.visibleItemsInfo.lastOrNull()
    return conversationIsAtEnd(
        totalItemsCount = layout.totalItemsCount,
        lastVisibleIndex = lastVisible?.index,
        lastVisibleEnd = lastVisible?.let { it.offset + it.size },
        viewportEndOffset = layout.viewportEndOffset,
    )
}

internal fun conversationShouldShowJumpToLatest(
    scrollOffset: Int,
    contentSize: Int,
    viewportSize: Int,
): Boolean {
    if (viewportSize <= 0 || contentSize <= viewportSize) return false
    val maximumOffset = (contentSize - viewportSize).coerceAtLeast(0)
    val distanceFromEnd = maximumOffset - scrollOffset.coerceIn(0, maximumOffset)
    return distanceFromEnd > viewportSize / 2
}

@Composable
internal fun BrandTile(size: Dp) {
    Image(
        painter = painterResource(R.drawable.parallel_codex_brand_ui),
        contentDescription = null,
        modifier = Modifier.size(size),
        contentScale = ContentScale.Fit,
    )
}

@Composable
internal fun InitialSyncIndicator(
    progress: InitialSyncProgress,
    modifier: Modifier = Modifier,
) {
    val label = when (progress.stage) {
        InitialSyncStage.CONNECTING -> appText(
            "正在建立安全连接",
            "Establishing a secure connection",
        )
        InitialSyncStage.DEVICES -> appText("正在同步设备", "Syncing computers")
        InitialSyncStage.COMPUTER -> appText("正在读取设备信息", "Reading computer details")
        InitialSyncStage.WORKSPACES -> appText("正在同步工作区", "Syncing workspaces")
        InitialSyncStage.COMPLETE -> appText("同步完成", "Sync complete")
    }
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                label,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${progress.stage.currentStep}/${InitialSyncProgress.TOTAL_STEPS}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        LinearProgressIndicator(
            progress = { progress.fraction },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
fun PCRRemoteApp(viewModel: MainViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val context = LocalContext.current

    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        viewModel.onAppResumed()
    }

    LaunchedEffect(state.message, state.messageIsError) {
        val message = state.message ?: return@LaunchedEffect
        snackbarHostState.currentSnackbarData?.dismiss()
        if (state.messageIsError) {
            snackbarHostState.showSnackbar(
                message = message,
                withDismissAction = true,
                duration = SnackbarDuration.Indefinite,
            )
            viewModel.clearMessage(message)
        } else {
            val snackbarJob = launch {
                snackbarHostState.showSnackbar(
                    message = message,
                    withDismissAction = false,
                    duration = SnackbarDuration.Indefinite,
                )
            }
            delay(TRANSIENT_MESSAGE_TIMEOUT_MILLIS)
            snackbarHostState.currentSnackbarData?.dismiss()
            snackbarJob.join()
            viewModel.clearMessage(message)
        }
    }

    LaunchedEffect(
        state.backgroundConnectionEnabled,
        state.signedIn,
        state.appLanguage,
    ) {
        BackgroundConnectionService.applySetting(
            context,
            state.backgroundConnectionEnabled && state.signedIn,
        )
    }

    ParallelCodexTheme(
        colorMode = state.colorMode,
        textSize = state.textSize,
        accentColor = state.accentColor,
    ) {
        CompositionLocalProvider(LocalAppLanguage provides state.appLanguage) {
        val view = LocalView.current
        val useLightSystemIcons = MaterialTheme.colorScheme.background.luminance() < 0.5f
        SideEffect {
            (view.context as? Activity)?.window?.let { window ->
                WindowCompat.getInsetsController(window, view).apply {
                    isAppearanceLightStatusBars = !useLightSystemIcons
                    isAppearanceLightNavigationBars = !useLightSystemIcons
                }
            }
        }
        Box(Modifier.fillMaxSize()) {
            Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.background,
            ) {
                if (state.signedIn) {
                    WorkspaceScreen(state, viewModel)
                } else {
                    SignInScreen(state, viewModel)
                }
                AppDialogs(state, viewModel)
            }
            SnackbarHost(
                hostState = snackbarHostState,
                modifier = Modifier
                    .align(Alignment.Center)
                    .padding(horizontal = 16.dp)
                    .widthIn(max = 560.dp)
                    .fillMaxWidth(),
            )
        }
        }
    }
}

@Composable
private fun SignInScreen(state: MainUiState, viewModel: MainViewModel) {
    val scanPairing = rememberPairingScanAction(viewModel)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 420.dp)
                .fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            BrandTile(82.dp)
            Spacer(Modifier.height(20.dp))
            Text(
                PRODUCT_NAME,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                appText(MOBILE_ROLE, "Mobile workspace"),
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                appText(
                    "连接运行 Parallel Codex 的设备",
                    "Connect to a computer running Parallel Codex",
                ),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(30.dp))
            Button(
                onClick = scanPairing,
                enabled = !state.busy,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
            ) {
                Icon(Icons.Default.QrCodeScanner, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text(appText("扫描配对码", "Scan pairing code"))
            }
            val initialSyncProgress = state.initialSyncProgress
            if (initialSyncProgress != null) {
                Spacer(Modifier.height(14.dp))
                InitialSyncIndicator(initialSyncProgress)
            } else if (state.busy) {
                Spacer(Modifier.height(14.dp))
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        }
    }
}


@Composable
internal fun CompactRunPane(
    state: MainUiState,
    viewModel: MainViewModel,
    viewportStore: ConversationViewportStore,
) {
    val workspaceName = state.workspaces.firstOrNull {
        it.workspaceId == state.selectedWorkspaceId
    }?.name
    val conversationContentLoading = state.isVisibleConversationContentLoading()
    val conversationReadIssue = state.visibleConversationReadIssue()
    val hasConversationContent = state.hasVisibleConversationContent()
    var agentDetailsNumber by rememberSaveable(state.visibleRunId) {
        mutableStateOf<Int?>(null)
    }
    var conversationPickerPresented by rememberSaveable { mutableStateOf(false) }
    var requestedConversationId by rememberSaveable { mutableStateOf<String?>(null) }
    val openConversationPicker = {
        requestedConversationId = null
        conversationPickerPresented = true
        viewModel.refreshWorkspaceConversations()
    }
    LaunchedEffect(
        requestedConversationId,
        state.conversationOpeningId,
        state.visibleConversationId,
    ) {
        val requested = requestedConversationId ?: return@LaunchedEffect
        if (state.conversationOpeningId == null && state.visibleConversationId == requested) {
            requestedConversationId = null
            conversationPickerPresented = false
        }
    }
    val detailsAgent = state.agentSummaries.firstOrNull {
        it.number == agentDetailsNumber
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
    ) {
        if (state.visibleRunId == null && state.conversationHistory.isEmpty()) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    BrandTile(44.dp)
                    Spacer(Modifier.height(12.dp))
                    Text(
                        appText("新对话", "New conversation"),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    workspaceName?.takeIf(String::isNotBlank)?.let { name ->
                        Spacer(Modifier.height(4.dp))
                        Text(
                            name,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Spacer(Modifier.height(10.dp))
                    TextButton(onClick = openConversationPicker) {
                        Text(appText("打开历史对话", "Open conversation history"))
                        Spacer(Modifier.width(2.dp))
                        Icon(Icons.Default.KeyboardArrowDown, contentDescription = null)
                    }
                }
            }
        } else {
            if (state.visibleRunId != null) {
                CompactRunToolbar(
                    state = state,
                    viewModel = viewModel,
                    workspaceName = workspaceName,
                    onOpenConversations = openConversationPicker,
                    onOpenAgentDetails = { agentDetailsNumber = it },
                )
                if (!conversationContentLoading &&
                    conversationReadIssue == null &&
                    state.agentSummaries.isEmpty() &&
                    !isTerminalStatus(state.runStatus)
                ) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
            } else {
                CompactConversationToolbar(
                    workspaceName = workspaceName,
                    conversationTitle = state.visibleConversationTitle,
                    onOpenConversations = openConversationPicker,
                )
            }
            if (conversationReadIssue != null && hasConversationContent) {
                ConversationReadNotice(
                    issue = conversationReadIssue,
                    hasContent = true,
                    onRetry = viewModel::refreshRun,
                    onOpenHistory = openConversationPicker,
                )
            }
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentAlignment = if (hasConversationContent) Alignment.TopCenter else Alignment.Center,
            ) {
                if (conversationReadIssue != null && !hasConversationContent) {
                    ConversationReadNotice(
                        issue = conversationReadIssue,
                        hasContent = false,
                        onRetry = viewModel::refreshRun,
                        onOpenHistory = openConversationPicker,
                    )
                } else if (conversationContentLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    AgentConversation(
                        state = state,
                        modifier = Modifier
                            .widthIn(max = 760.dp)
                            .fillMaxWidth()
                            .fillMaxHeight()
                            .padding(horizontal = 16.dp, vertical = 12.dp),
                        scrollable = true,
                        viewportStore = viewportStore,
                        onOpenLink = viewModel::openTranscriptLink,
                        onSelectAgent = viewModel::selectAgent,
                        onOpenAgentDetails = { agentDetailsNumber = it },
                        onMarkAgentViewed = viewModel::markAgentCompletionViewed,
                        onSetWorkExpanded = viewModel::setWorkspaceWorkExpanded,
                        onSetWorkSectionExpanded = viewModel::setWorkSectionExpanded,
                        onLoadWorkOperation = { operation, cursor ->
                            val mac = state.macs.first { it.deviceId == state.selectedMacId }
                            viewModel.repository.workOperation(mac, requireNotNull(state.visibleRunId),
                                requireNotNull(state.selectedAgent), operation.eventId, cursor)
                        },
                    )
                }
            }
        }

        RunComposer(state, viewModel)
    }
    if (detailsAgent != null) {
        AgentResultDialog(
            state = state,
            agent = detailsAgent,
            onDismiss = { agentDetailsNumber = null },
            onRequestAccept = viewModel::requestAcceptAgent,
            onRequestKill = viewModel::requestKillAgent,
            onRetry = viewModel::retryAgent,
            onToggleRejection = viewModel::toggleAgentRejection,
        )
    }
    if (conversationPickerPresented) {
        ConversationPickerSheet(
            state = state,
            workspaceName = workspaceName,
            onDismiss = {
                requestedConversationId = null
                conversationPickerPresented = false
                viewModel.cancelConversationOpen()
            },
            onRefresh = { viewModel.refreshWorkspaceConversations(force = true) },
            onNewConversation = {
                requestedConversationId = null
                conversationPickerPresented = false
                viewModel.newWorkspaceConversation()
            },
            onSelect = { conversationId ->
                requestedConversationId = conversationId
                viewModel.openWorkspaceConversation(conversationId)
            },
        )
    }
}
