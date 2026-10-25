package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.generated.WorkOperationRecord
import com.parallelcodexrunner.remote.data.generated.WorkOperationDetailPage
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.math.roundToInt

@Composable
internal fun AgentConversation(
    state: MainUiState,
    modifier: Modifier = Modifier,
    scrollable: Boolean = false,
    viewportStore: ConversationViewportStore,
    onOpenLink: (String) -> Unit = {},
    onSelectAgent: (Int) -> Unit = {},
    onOpenAgentDetails: (Int) -> Unit = {},
    onMarkAgentViewed: (Int) -> Unit = {},
    onSetWorkExpanded: (Boolean) -> Unit = {},
    onSetWorkSectionExpanded: (String, Boolean) -> Unit = { _, _ -> },
    onLoadWorkOperation: suspend (WorkOperationRecord, Int) -> WorkOperationDetailPage,
) {
    val selected = state.selectedAgent
    val density = LocalDensity.current
    val viewportTextScale = conversationViewportTextScale(
        appTextScale = state.textSize.scale,
        systemFontScale = density.fontScale,
    )
    val viewportWidthDp = with(density) {
        LocalWindowInfo.current.containerSize.width.toDp().value
            .roundToInt()
            .coerceAtLeast(1)
    }
    val selectedSummary = state.agentSummaries.firstOrNull { it.number == selected }
    val rawBlocks = conversationBlocks(state, LocalAppLanguage.current)
    val workStateKey = state.workspaceWorkExpansionPreferenceKey()
    val workExpanded = state.isWorkspaceWorkExpanded()
    LaunchedEffect(workStateKey) {
        if (workStateKey != null && workStateKey !in state.workExpansionStates) {
            onSetWorkExpanded(workExpanded)
        }
    }
    LaunchedEffect(state.visibleRunId, selected, selectedSummary?.status) {
        if (selected != null && selectedSummary?.let { isTerminalAgentStatus(it.status) } == true) {
            onMarkAgentViewed(selected)
        }
    }
    val blocks = if (selected == null) {
        rawBlocks
    } else {
        MobileConversationPresentation.visibleBlocks(
            blocks = rawBlocks,
            runId = state.visibleRunId ?: "run",
            agent = selected,
            workExpanded = workExpanded || selectedSummary?.let { isActiveAgentStatus(it.status) } == true,
            language = LocalAppLanguage.current,
            sectionOverrides = state.workExpansionStates.filterKeys { it.startsWith(state.workSectionPrefix()) }
                .mapKeys { it.key.removePrefix(state.workSectionPrefix()) },
        )
    }
    val workSections = blocks.filter { it.activityType?.startsWith("work_summary") == true }
    LaunchedEffect(state.workSectionPrefix(), workSections.map { it.id }) {
        workSections.forEach { block ->
            if (state.workSectionPrefix() + block.id !in state.workExpansionStates) {
                onSetWorkSectionExpanded(block.id, block.activityType == "work_summary_expanded")
            }
        }
    }
    val switchState = conversationAgentSwitchState(state)
    val timeline = MobileConversationTimelineProjection.make(blocks, switchState)
    val viewportAnchors = timeline.map(MobileConversationTimelineItem::viewportAnchor)
    // Agent selection changes the vertical conversation viewport key. Keep the
    // horizontal selectors outside that key so they do not jump back to Agent 1.
    val inlineAgentSwitchState = rememberLazyListState()

    if (timeline.isEmpty()) {
        Box(modifier) {
            if (state.agentSummaries.firstOrNull { it.number == selected }
                    ?.status
                    ?.let(::isActiveAgentStatus) == true
            ) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        }
        return
    }

    if (!scrollable) {
        Column(
            modifier = modifier,
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            timeline.forEach { item ->
                ConversationTimelineItem(
                    item = item,
                    onOpenLink = onOpenLink,
                    onSelectAgent = onSelectAgent,
                    onOpenAgentDetails = onOpenAgentDetails,
                    agentSwitchListState = inlineAgentSwitchState,
                    onToggleWork = { onSetWorkSectionExpanded(it.id, it.activityType != "work_summary_expanded") },
                    onLoadWorkOperation = onLoadWorkOperation,
                )
            }
        }
    } else {
        val viewportKey = conversationViewportKey(
            macId = state.selectedMacId,
            workspaceId = state.selectedWorkspaceId,
            runId = state.visibleRunId,
            agent = state.selectedAgent,
        )

        // Keep one composed transcript surface while swapping the per-agent viewport.
        // Re-keying this whole subtree briefly removed the LazyColumn on every agent
        // switch, which produced a visible blank frame before the next answer appeared.
        run {
            val viewport = remember(viewportKey, viewportTextScale, viewportWidthDp) {
                viewportStore.get(
                    key = viewportKey,
                    blocks = viewportAnchors,
                    textScale = viewportTextScale,
                    layoutWidthDp = viewportWidthDp,
                )
            }
            val listState = viewport.listState
            var followLatest by viewport.followLatest
            val pendingRestore = viewport.pendingRestore.value
            val currentAnchors = rememberUpdatedState(viewportAnchors)
            val lastAnchor = viewportAnchors.lastOrNull()
            var viewportHeight by remember(viewportKey) { mutableIntStateOf(0) }
            val showJumpToLatest by remember(listState) {
                derivedStateOf {
                    listState.scrollIndicatorState?.let { indicator ->
                        conversationShouldShowJumpToLatest(
                            scrollOffset = indicator.scrollOffset,
                            contentSize = indicator.contentSize,
                            viewportSize = indicator.viewportSize,
                        )
                    } ?: false
                }
            }
            SideEffect {
                viewport.blocks = viewportAnchors
                viewport.textScale = viewportTextScale
                viewport.layoutWidthDp = viewportWidthDp
            }

            DisposableEffect(viewportKey, listState) {
                onDispose {
                    viewportStore.save(viewportKey, viewport, currentAnchors.value)
                }
            }

            LaunchedEffect(viewportKey, pendingRestore, viewportAnchors) {
                val restore = pendingRestore ?: return@LaunchedEffect
                val index = restoredConversationIndex(restore, viewportAnchors)
                    ?: restore.anchorIndex.coerceIn(0, viewportAnchors.size)
                viewport.programmaticScroll.value = true
                try {
                    val estimatedOffset = rescaledConversationOffset(
                        restore,
                        viewportTextScale,
                    )
                    listState.requestScrollToItem(index, estimatedOffset)
                    val widthChanged = conversationViewportWidthChanged(
                        savedWidthDp = restore.layoutWidthDp,
                        currentWidthDp = viewportWidthDp,
                    )
                    val expectsResize = (
                        !restore.textScale.approximatelyEquals(viewportTextScale) ||
                            widthChanged
                        ) && restore.anchorItemSize != null
                    val currentGeometry = withTimeoutOrNull(500) {
                        snapshotFlow {
                            val layout = listState.layoutInfo
                            val itemSize = layout.visibleItemsInfo
                                .firstOrNull { it.index == index }
                                ?.size ?: return@snapshotFlow null
                            val viewportSize = layout.viewportEndOffset - layout.viewportStartOffset
                            itemSize to viewportSize.takeIf { it > 0 }
                        }
                            .filterNotNull()
                            .first { (itemSize, _) ->
                                !expectsResize || itemSize != restore.anchorItemSize
                            }
                    } ?: listState.layoutInfo.let { layout ->
                        layout.visibleItemsInfo
                            .firstOrNull { it.index == index }
                            ?.size
                            ?.let { itemSize ->
                                val viewportSize = layout.viewportEndOffset - layout.viewportStartOffset
                                itemSize to viewportSize.takeIf { it > 0 }
                            }
                    }
                    var currentItemSize = currentGeometry?.first
                    var currentViewportSize = currentGeometry?.second
                    val blockId = viewportAnchors.getOrNull(index)?.id
                    var currentTextLayout = restore.anchorTextOffset?.let {
                        if (blockId == null) {
                            null
                        } else {
                            withTimeoutOrNull(500) {
                                snapshotFlow { viewport.textLayouts[blockId] }
                                    .filterNotNull()
                                    .first { layout ->
                                        val oldSize = restore.anchorTextSizePx
                                        widthChanged || !expectsResize || oldSize == null ||
                                            !layout.textSizePx.approximatelyEquals(oldSize)
                                    }
                            }
                        }
                    }
                    var restoredOffset = currentTextLayout?.let { layout ->
                        restoredConversationTextOffset(
                            record = restore,
                            layout = layout,
                            currentItemSize = currentItemSize,
                            currentViewportSize = currentViewportSize,
                        )
                    }
                        ?: rescaledConversationOffset(
                            restore,
                            viewportTextScale,
                            currentItemSize,
                            currentViewportSize,
                        )
                    if (restoredOffset != estimatedOffset) {
                        listState.requestScrollToItem(index, restoredOffset)
                    }
                    if (expectsResize && currentTextLayout != null && blockId != null) {
                        // Markwon's formula spans can trigger another TextView measure after
                        // the first text-size layout. Re-anchor once that second measure has
                        // settled instead of leaving the answer at an intermediate position.
                        delay(220)
                        val settledLayout = viewport.textLayouts[blockId]
                        val settledGeometry = listState.layoutInfo.let { layout ->
                            layout.visibleItemsInfo
                                .firstOrNull { it.index == index }
                                ?.size
                                ?.let { itemSize ->
                                    val viewportSize =
                                        layout.viewportEndOffset - layout.viewportStartOffset
                                    itemSize to viewportSize.takeIf { it > 0 }
                                }
                        }
                        val settledOffset = settledLayout?.let { layout ->
                            restoredConversationTextOffset(
                                record = restore,
                                layout = layout,
                                currentItemSize = settledGeometry?.first,
                                currentViewportSize = settledGeometry?.second,
                            )
                        }
                        if (settledOffset != null && settledOffset != restoredOffset) {
                            listState.requestScrollToItem(index, settledOffset)
                            restoredOffset = settledOffset
                        }
                        currentTextLayout = settledLayout ?: currentTextLayout
                        currentItemSize = settledGeometry?.first ?: currentItemSize
                        currentViewportSize = settledGeometry?.second ?: currentViewportSize
                    }
                    followLatest = false
                    viewport.latestRecord = restore.copy(
                        anchorIndex = index,
                        anchorOffset = restoredOffset,
                        anchorItemSize = currentItemSize,
                        anchorViewportSize = currentViewportSize,
                        anchorTextSizePx = currentTextLayout?.textSizePx
                            ?: restore.anchorTextSizePx,
                        textScale = viewportTextScale,
                        layoutWidthDp = viewportWidthDp,
                        followLatest = false,
                    )
                    viewport.pendingRestore.value = null
                } finally {
                    viewport.programmaticScroll.value = false
                }
            }

            LaunchedEffect(listState) {
                snapshotFlow {
                    Triple(
                        listState.isScrollInProgress,
                        viewport.programmaticScroll.value,
                        conversationIsAtEnd(listState),
                    )
                }.collect { (scrolling, programmatic, atEnd) ->
                    if (scrolling && !programmatic) {
                        viewport.pendingRestore.value = null
                        followLatest = false
                    } else if (viewport.pendingRestore.value == null) {
                        followLatest = nextConversationFollowLatest(
                            current = followLatest,
                            scrolling = scrolling,
                            programmatic = programmatic,
                            atEnd = atEnd,
                        )
                    }
                }
            }

            LaunchedEffect(viewportKey, listState) {
                snapshotFlow {
                    if (listState.isScrollInProgress ||
                        viewport.programmaticScroll.value ||
                        viewport.pendingRestore.value != null
                    ) {
                        null
                    } else {
                        Triple(
                            listState.firstVisibleItemIndex,
                            listState.firstVisibleItemScrollOffset,
                            followLatest,
                        )
                    }
                }
                    .filterNotNull()
                    .distinctUntilChanged()
                    .collect {
                        viewportStore.save(viewportKey, viewport, currentAnchors.value)
                    }
            }

            // Keep bottom-following conversations pinned when composer controls or the IME resize them.
            LaunchedEffect(
                timeline.size,
                lastAnchor?.id,
                lastAnchor?.text?.length,
                lastAnchor?.status,
                viewportHeight,
                followLatest,
                pendingRestore,
            ) {
                if (followLatest && pendingRestore == null) {
                    viewport.programmaticScroll.value = true
                    try {
                        listState.requestScrollToItem(timeline.size)
                    } finally {
                        viewport.programmaticScroll.value = false
                    }
                }
            }

            Box(
                modifier = modifier.onSizeChanged { size ->
                    viewportHeight = size.height
                },
            ) {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    state = listState,
                    verticalArrangement = Arrangement.spacedBy(18.dp),
                ) {
                    timeline.forEach { timelineItem ->
                        when (timelineItem) {
                            is MobileConversationTimelineItem.AgentSwitch -> stickyHeader(
                                key = timelineItem.id,
                            ) {
                                Box(
                                    Modifier
                                        .fillMaxWidth()
                                        .background(MaterialTheme.colorScheme.background)
                                        .padding(vertical = 3.dp),
                                ) {
                                    ConversationAgentSwitchBar(
                                        state = timelineItem.state,
                                        listState = inlineAgentSwitchState,
                                        onSelectAgent = onSelectAgent,
                                        onOpenAgentDetails = onOpenAgentDetails,
                                    )
                                }
                            }
                            is MobileConversationTimelineItem.Message -> item(
                                key = timelineItem.id,
                            ) {
                                ConversationTimelineItem(
                                    item = timelineItem,
                                    onOpenLink = onOpenLink,
                                    onSelectAgent = onSelectAgent,
                                    onOpenAgentDetails = onOpenAgentDetails,
                                    agentSwitchListState = inlineAgentSwitchState,
                                    onTextLayout = { blockId, layout ->
                                        if (viewport.textLayouts[blockId]
                                                ?.hasSameGeometry(layout) != true
                                        ) {
                                            viewport.textLayouts[blockId] = layout
                                        }
                                    },
                                    onToggleWork = { block ->
                                        followLatest = false
                                        onSetWorkSectionExpanded(block.id, block.activityType != "work_summary_expanded")
                                    },
                                    onLoadWorkOperation = onLoadWorkOperation,
                                )
                            }
                        }
                    }
                    item(key = "transcript-tail") {
                        Spacer(Modifier.height(1.dp))
                    }
                }
                if (!followLatest && showJumpToLatest) {
                    FilledIconButton(
                        onClick = {
                            viewport.pendingRestore.value = null
                            followLatest = true
                        },
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(8.dp),
                    ) {
                        Icon(
                            Icons.Default.KeyboardArrowDown,
                            contentDescription = appText("跳到最新内容", "Jump to latest"),
                        )
                    }
                }
            }
        }
    }

}

private fun Float.approximatelyEquals(other: Float): Boolean =
    kotlin.math.abs(this - other) < 0.0001f

internal fun conversationAgentSwitchState(
    state: MainUiState,
): MobileConversationAgentSwitchState? = state.visibleRunId
    ?.takeIf { state.pendingSubmission == null && state.agentSummaries.size > 1 }
    ?.let {
        MobileConversationAgentSwitchState(
            runId = it,
            agents = state.agentSummaries,
            selectedAgent = state.selectedAgent,
            pendingAgentSelection = state.pendingAgentSelection,
            recommendedAgent = state.recommendedAgent,
            finalizedAgent = state.finalizedAgent,
            selectionEnabled = true,
        )
    }

@Composable
private fun ConversationTimelineItem(
    item: MobileConversationTimelineItem,
    onOpenLink: (String) -> Unit,
    onSelectAgent: (Int) -> Unit,
    onOpenAgentDetails: (Int) -> Unit,
    agentSwitchListState: LazyListState,
    onTextLayout: (String, ConversationTextLayout) -> Unit = { _, _ -> },
    onToggleWork: (MobileTranscriptBlock) -> Unit,
    onLoadWorkOperation: suspend (WorkOperationRecord, Int) -> WorkOperationDetailPage,
) {
    when (item) {
        is MobileConversationTimelineItem.Message -> TranscriptBlock(
            block = item.block,
            onOpenLink = onOpenLink,
            onTextLayout = onTextLayout,
            onActivate = { onToggleWork(item.block) },
            onLoadWorkOperation = onLoadWorkOperation,
        )
        is MobileConversationTimelineItem.AgentSwitch -> ConversationAgentSwitchBar(
            state = item.state,
            listState = agentSwitchListState,
            onSelectAgent = onSelectAgent,
            onOpenAgentDetails = onOpenAgentDetails,
        )
    }
}

internal fun conversationBlocks(
    state: MainUiState,
    language: AppLanguage = AppLanguage.CHINESE,
): List<MobileTranscriptBlock> {
    val selected = state.selectedAgent
    val pending = state.pendingSubmission
    if (pending != null) {
        val accepted = acceptedConversation(
            history = state.conversationHistory,
            prompt = state.runPrompt,
            transcript = selected?.let { state.agentTranscripts[it] ?: MobileAgentTranscript() },
        )
        return accepted + MobileTranscriptBlock(
            id = "current-prompt",
            kind = MobileTranscriptKind.USER,
            text = pending.prompt,
            status = pendingSubmissionLabel(pending.status, language),
        )
    }
    // A newly accepted run has a durable prompt before the server has created
    // its first Agent summary. Keep rendering that prompt while selectedAgent
    // is still null instead of briefly replacing the conversation with history.
    return MobileTranscriptFormatter.visibleBlocks(
        history = state.conversationHistory,
        prompt = state.runPrompt,
        transcript = selected?.let { state.agentTranscripts[it] } ?: MobileAgentTranscript(),
    )
}

@Composable
private fun TranscriptBlock(
    block: MobileTranscriptBlock,
    onOpenLink: (String) -> Unit,
    onTextLayout: (String, ConversationTextLayout) -> Unit = { _, _ -> },
    onActivate: () -> Unit = {},
    onLoadWorkOperation: suspend (WorkOperationRecord, Int) -> WorkOperationDetailPage,
) {
    if (block.operation != null) {
        WorkOperationRow(block.operation, onOpenLink, onLoadWorkOperation)
        return
    }
    if (block.activityType?.startsWith("work_summary") == true) {
        WorkDisclosureBlock(block, onActivate)
        return
    }
    val color = transcriptColor(block)
    if (block.kind == MobileTranscriptKind.USER) {
        Box(Modifier.fillMaxWidth()) {
            TranscriptBody(
                block = block,
                color = color,
                onOpenLink = onOpenLink,
                onTextLayout = onTextLayout,
                modifier = Modifier
                    .fillMaxWidth(0.88f)
                    .align(Alignment.CenterEnd),
            )
        }
    } else {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
        ) {
            if (block.kind == MobileTranscriptKind.OUTPUT) {
                BrandTile(26.dp)
            } else {
                Box(
                    modifier = Modifier
                        .size(26.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        transcriptIcon(block),
                        contentDescription = null,
                        tint = color,
                        modifier = Modifier.size(15.dp),
                    )
                }
            }
            Spacer(Modifier.width(9.dp))
            TranscriptBody(
                block = block,
                color = color,
                onOpenLink = onOpenLink,
                onTextLayout = onTextLayout,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun WorkDisclosureBlock(
    block: MobileTranscriptBlock,
    onActivate: () -> Unit,
) {
    val expanded = block.activityType == "work_summary_expanded"
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable(onClick = onActivate)
            .padding(vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.Terminal,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(26.dp).padding(4.dp),
        )
        Spacer(Modifier.width(9.dp))
        Text(
            appText("工作过程", "Work"),
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurface,
            fontWeight = FontWeight.Medium,
        )
        Spacer(Modifier.width(8.dp))
        Text(
            block.text,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
        Icon(
            if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
            contentDescription = if (expanded) {
                appText("收起工作过程", "Collapse work")
            } else {
                appText("展开工作过程", "Expand work")
            },
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(19.dp),
        )
    }
}

@Composable
private fun TranscriptBody(
    block: MobileTranscriptBlock,
    color: Color,
    onOpenLink: (String) -> Unit,
    onTextLayout: (String, ConversationTextLayout) -> Unit,
    modifier: Modifier = Modifier,
) {
    val framed = block.kind in setOf(
        MobileTranscriptKind.USER,
        MobileTranscriptKind.WARNING,
        MobileTranscriptKind.ERROR,
    )
    val background = when (block.kind) {
        MobileTranscriptKind.USER -> MaterialTheme.colorScheme.primary.copy(alpha = 0.11f)
        MobileTranscriptKind.OUTPUT -> Color.Transparent
        MobileTranscriptKind.COMMAND -> Color.Transparent
        MobileTranscriptKind.WARNING -> Color(0xFFFF9F0A).copy(alpha = 0.09f)
        MobileTranscriptKind.ERROR -> MaterialTheme.colorScheme.error.copy(alpha = 0.07f)
        else -> Color.Transparent
    }
    val shape = RoundedCornerShape(8.dp)

    Column(
        modifier = modifier
            .background(background, shape)
            .padding(if (framed) 13.dp else 0.dp),
        verticalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        transcriptTitle(block)?.takeUnless {
            block.kind in setOf(MobileTranscriptKind.THOUGHT, MobileTranscriptKind.COMMAND, MobileTranscriptKind.ACTIVITY)
        }?.let { title ->
            Text(
                title,
                style = MaterialTheme.typography.labelMedium,
                color = color,
                fontWeight = FontWeight.Medium,
            )
        }
        val bodyStyle = when (block.kind) {
            MobileTranscriptKind.COMMAND -> MaterialTheme.typography.bodySmall.copy(
                fontFamily = FontFamily.Monospace,
            )
            MobileTranscriptKind.THOUGHT -> MaterialTheme.typography.bodyMedium
            MobileTranscriptKind.ACTIVITY -> MaterialTheme.typography.bodyMedium
            else -> MaterialTheme.typography.bodyLarge
        }
        val bodyColor = if (block.kind in setOf(
                MobileTranscriptKind.THOUGHT,
                MobileTranscriptKind.ACTIVITY,
            )
        ) {
            MaterialTheme.colorScheme.onSurfaceVariant
        } else {
            MaterialTheme.colorScheme.onSurface
        }
        val displayText = when (block.activityType) {
            "turn_interrupted" -> appText("回复已中断", "Response interrupted")
            "missing_execution_path" -> appText(
                "命令未能启动，因为所需路径已不存在。",
                "The command could not start because a required path no longer exists.",
            )
            else -> block.text
        }
        if (block.kind in setOf(
                MobileTranscriptKind.USER,
                MobileTranscriptKind.THOUGHT,
                MobileTranscriptKind.OUTPUT,
                MobileTranscriptKind.WARNING,
                MobileTranscriptKind.ERROR,
            )
        ) {
            MarkdownTranscript(
                content = displayText,
                textColor = bodyColor,
                baseStyle = bodyStyle,
                onOpenLink = onOpenLink,
                layoutKey = block.id,
                onTextLayout = onTextLayout,
                preserveLineBreaks = block.kind == MobileTranscriptKind.USER,
            )
        } else {
            SelectionContainer {
                Text(
                    text = displayText,
                    style = bodyStyle,
                    color = bodyColor,
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                )
            }
        }
        block.status?.let { status ->
            Text(
                displayTranscriptStatus(status),
                style = MaterialTheme.typography.labelSmall,
                color = transcriptStatusColor(status),
            )
        }
    }
}

@Composable
private fun transcriptStatusColor(status: String): Color = when {
    status.startsWith("已完成") || status.startsWith("Completed") -> SystemGreen
    status.startsWith("运行中") || status.startsWith("Running") ||
        status == "正在提交" || status == "Submitting" -> SystemBlue
    status.startsWith("等待") || status.startsWith("Waiting") -> Color(0xFFE67E00)
    else -> SystemRed
}

@Composable
private fun transcriptTitle(block: MobileTranscriptBlock): String? = when (block.kind) {
    MobileTranscriptKind.USER -> null
    MobileTranscriptKind.THOUGHT -> appText("思考", "Reasoning")
    MobileTranscriptKind.OUTPUT -> null
    MobileTranscriptKind.COMMAND -> appText("命令", "Command")
    MobileTranscriptKind.ACTIVITY -> when (block.activityType) {
        "web_search" -> appText("网页搜索", "Web search")
        "file_change" -> appText("文件修改", "File changes")
        "todo_list" -> appText("工作计划", "Plan")
        else -> null
    }
    MobileTranscriptKind.WARNING -> appText("提醒", "Notice")
    MobileTranscriptKind.ERROR -> appText("错误", "Error")
}

@Composable
private fun displayTranscriptStatus(status: String): String = when {
    status.startsWith("已完成") -> appText(status, status.replaceFirst("已完成", "Completed"))
    status.startsWith("运行中") -> appText(status, status.replaceFirst("运行中", "Running"))
    status.startsWith("失败（退出码 ") -> appText(
        status,
        status.replaceFirst("失败（退出码 ", "Failed (exit code ").replace("）", ")"),
    )
    status == "失败" -> appText(status, "Failed")
    status == "已取消" -> appText(status, "Cancelled")
    status == "收起" -> appText(status, "Collapse")
    status == "展开" -> appText(status, "Expand")
    status == "正在提交" -> appText(status, "Submitting")
    status.startsWith("等待") -> appText(status, status.replaceFirst("等待", "Waiting"))
    else -> status
}

private fun transcriptIcon(block: MobileTranscriptBlock): ImageVector = when (block.kind) {
    MobileTranscriptKind.USER -> Icons.Default.Person
    MobileTranscriptKind.THOUGHT -> Icons.Default.AutoAwesome
    MobileTranscriptKind.OUTPUT -> Icons.Default.ChatBubble
    MobileTranscriptKind.COMMAND -> Icons.Default.Terminal
    MobileTranscriptKind.ACTIVITY -> when (block.activityType) {
        "web_search" -> Icons.Default.Search
        "file_change" -> Icons.Default.Description
        "turn_interrupted" -> Icons.Default.StopCircle
        else -> Icons.Default.CheckCircle
    }
    MobileTranscriptKind.WARNING -> Icons.Default.Warning
    MobileTranscriptKind.ERROR -> Icons.Default.Warning
}

private fun transcriptColor(block: MobileTranscriptBlock): Color = when (block.kind) {
    MobileTranscriptKind.USER -> SystemBlue
    MobileTranscriptKind.OUTPUT -> SystemBlue
    MobileTranscriptKind.THOUGHT -> SystemTeal
    MobileTranscriptKind.COMMAND -> SystemGray
    MobileTranscriptKind.ACTIVITY -> when (block.activityType) {
        "web_search" -> SystemBlue
        "file_change" -> SystemGreen
        "turn_interrupted" -> SystemGray
        else -> SystemTeal
    }
    MobileTranscriptKind.WARNING -> Color(0xFFE67E00)
    MobileTranscriptKind.ERROR -> SystemRed
}
