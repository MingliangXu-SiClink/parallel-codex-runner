package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.util.Locale

internal fun currentAcceptableAgent(state: MainUiState): Int? {
    if (!state.canMutateVisibleRun() || state.finalizedAgent != null) return null
    val selected = state.selectedAgent ?: return null
    return state.agentSummaries.firstOrNull {
        it.number == selected &&
            it.status.equals("success", ignoreCase = true) &&
            it.canAccept
    }?.number
}

@Composable
internal fun ConversationAgentSwitchBar(
    state: MobileConversationAgentSwitchState,
    listState: LazyListState,
    onSelectAgent: (Int) -> Unit,
    onOpenAgentDetails: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val selectedIndex = state.agents.indexOfFirst { it.number == state.selectedAgent }
    val compact = state.agents.size > 7
    LaunchedEffect(state.selectedAgent, state.agents.map(AgentSummary::number)) {
        if (selectedIndex < 0) return@LaunchedEffect
        val layout = listState.layoutInfo
        val selectedItem = layout.visibleItemsInfo.firstOrNull { it.index == selectedIndex }
        val fullyVisible = selectedItem != null &&
            selectedItem.offset >= layout.viewportStartOffset &&
            selectedItem.offset + selectedItem.size <= layout.viewportEndOffset
        if (!fullyVisible) listState.requestScrollToItem(selectedIndex)
    }

    Surface(
        modifier = modifier.fillMaxWidth(),
        color = Color.Transparent,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 42.dp)
                .padding(vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LazyRow(
                modifier = Modifier
                    .weight(1f),
                state = listState,
                horizontalArrangement = Arrangement.spacedBy(3.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                items(state.agents, key = AgentSummary::number) { agent ->
                    val selected = agent.number == state.selectedAgent
                    val loading = agent.number == state.pendingAgentSelection
                    val language = LocalAppLanguage.current
                    AgentChoice(
                        agent = agent,
                        selected = selected,
                        loading = loading,
                        enabled = state.selectionEnabled,
                        label = agentDisplayName(agent, state.agents, language, compact),
                        accessibleName = agentDisplayName(agent, state.agents, language),
                        onClick = {
                            if (selected) onOpenAgentDetails(agent.number)
                            else onSelectAgent(agent.number)
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun AgentChoice(
    agent: AgentSummary,
    selected: Boolean,
    loading: Boolean,
    enabled: Boolean,
    label: String,
    accessibleName: String,
    onClick: () -> Unit,
) {
    val selectedColor = MaterialTheme.colorScheme.primary
    val statusDescription = statusLabel(agent.status, LocalAppLanguage.current)
    val recommendedDescription = if (agent.recommended) {
        appText("，推荐", ", recommended")
    } else ""
    val rejectedDescription = if (agent.rejected) {
        appText("，已排除", ", excluded")
    } else ""
    val description = "$accessibleName $statusDescription" +
        recommendedDescription + rejectedDescription
    val accessibilityAction = if (selected) {
        appText("查看 $accessibleName 详情", "View $accessibleName details")
    } else {
        appText("切换到 $accessibleName", "Switch to $accessibleName")
    }
    Surface(
            modifier = Modifier
                .then(if (label.length <= 3) Modifier.widthIn(min = 60.dp) else Modifier)
                .clickable(enabled = enabled, onClick = onClick)
                .clearAndSetSemantics {
                    contentDescription = description
                    this.selected = selected
                    role = Role.Tab
                    if (enabled) {
                        onClick(label = accessibilityAction) {
                            onClick()
                            true
                        }
                    }
                },
            color = if (selected) {
                selectedColor.copy(alpha = 0.13f)
            } else {
                Color.Transparent
            },
            shape = RoundedCornerShape(6.dp),
    ) {
        Row(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier.size(12.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    if (loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(12.dp),
                            strokeWidth = 1.5.dp,
                        )
                    } else {
                        StatusDot(agent.status)
                    }
                }
                Spacer(Modifier.width(6.dp))
                Text(
                    label,
                    style = MaterialTheme.typography.labelMedium,
                    color = if (selected) {
                        selectedColor
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                    maxLines = 1,
                )
                if (agent.recommended) {
                    Spacer(Modifier.width(4.dp))
                    Icon(
                        Icons.Default.Star,
                        contentDescription = appText("推荐 Agent", "Recommended Agent"),
                        tint = if (selected) {
                            selectedColor
                        } else {
                            SystemOrange
                        },
                        modifier = Modifier.size(14.dp),
                    )
                }
                if (agent.rejected) {
                    Spacer(Modifier.width(4.dp))
                    Text(
                        if (label.length <= 3) "−" else appText("已排除", "Excluded"),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (selected) {
                            selectedColor
                        } else {
                            SystemOrange
                        },
                        maxLines = 1,
                    )
                }
        }
    }
}

@Composable
internal fun AgentResultDialog(
    state: MainUiState,
    agent: AgentSummary,
    onDismiss: () -> Unit,
    onRequestAccept: (Int) -> Unit,
    onRequestKill: (Int) -> Unit,
    onRetry: (Int) -> Unit,
    onToggleRejection: (Int) -> Unit,
) {
    var tokenDetailsExpanded by remember(agent.number, agent.reasoningTokenCounts) {
        mutableStateOf(false)
    }
    val runActionsAvailable = state.canMutateVisibleRun()
    val canAccept = runActionsAvailable &&
        agent.status.equals("success", ignoreCase = true) &&
        agent.canAccept &&
        state.finalizedAgent == null
    val hasMutableActions = runActionsAvailable && (
        agent.canRetry || state.finalizedAgent == null || isAgentStopAvailable(agent.status)
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    agentDisplayName(
                        agent,
                        state.agentSummaries,
                        LocalAppLanguage.current,
                    ),
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (agent.recommended) {
                    Icon(
                        Icons.Default.Star,
                        contentDescription = appText("推荐 Agent", "Recommended Agent"),
                        tint = SystemOrange,
                        modifier = Modifier.size(19.dp),
                    )
                }
            }
        },
        text = {
            Column(
                modifier = Modifier
                    .heightIn(max = 480.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                AgentResultMetric(
                    appText("状态", "Status"),
                    statusLabel(agent.status, LocalAppLanguage.current),
                )
                agent.seconds?.let {
                    AgentResultMetric(
                        appText("耗时", "Duration"),
                        String.format(Locale.US, appText("%.2f 秒", "%.2f seconds"), it),
                    )
                }
                agent.reasoningTokens?.takeIf { it > 0 }?.let {
                    AgentResultMetric(
                        appText("推理用量", "Reasoning tokens"),
                        String.format(Locale.US, "%,d", it),
                    )
                }
                if (agent.reasoningTokenCounts.isNotEmpty()) {
                    val steps = agent.reasoningTokenCounts.values.sum()
                    TextButton(
                        onClick = { tokenDetailsExpanded = !tokenDetailsExpanded },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            appText("分步推理用量（$steps 次）", "Per-step usage ($steps)"),
                            modifier = Modifier.weight(1f),
                        )
                        Icon(
                            if (tokenDetailsExpanded) {
                                Icons.Default.KeyboardArrowUp
                            } else {
                                Icons.Default.KeyboardArrowDown
                            },
                            contentDescription = null,
                        )
                    }
                    if (tokenDetailsExpanded) {
                        Column(
                            modifier = Modifier.fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(7.dp),
                        ) {
                            agent.reasoningTokenCounts.entries
                                .sortedWith(
                                    compareByDescending<Map.Entry<Long, Int>> {
                                        it.key * it.value
                                    }.thenByDescending { it.key }
                                )
                                .forEach { (step, count) ->
                                    AgentResultMetric(
                                        appText("单步 +", "Step +") +
                                            String.format(Locale.US, "%,d", step),
                                        appText(
                                            "$count 次 · ${String.format(Locale.US, "%,d", step * count)}",
                                            "$count times · ${String.format(Locale.US, "%,d", step * count)}",
                                        ),
                                    )
                                }
                        }
                    }
                }
                if (agent.rejected) {
                    AgentResultMetric(appText("推荐范围", "Recommendation"), appText("已排除", "Excluded"))
                }
                if (state.finalizedAgent == agent.number) {
                    AgentResultMetric(appText("工作区", "Workspace"), appText("已采用", "Applied"))
                }

                state.agentDiffSummaries[agent.number]
                    ?.takeUnless { it.unavailable }
                    ?.let { summary ->
                        AgentFileChangeList(summary)
                    }

                if (hasMutableActions) {
                    HorizontalDivider(Modifier.padding(top = 2.dp))
                }
                if (runActionsAvailable && agent.canRetry) {
                    ResultAction(
                        label = appText("重新运行此 Agent", "Retry this Agent"),
                        icon = Icons.Default.Refresh,
                        onClick = {
                            onDismiss()
                            onRetry(agent.number)
                        },
                    )
                }
                if (runActionsAvailable && state.finalizedAgent == null) {
                    ResultAction(
                        label = if (agent.rejected) {
                            appText("恢复推荐资格", "Include in recommendations")
                        } else {
                            appText("排除出推荐", "Exclude from recommendations")
                        },
                        icon = Icons.Default.Star,
                        onClick = {
                            onDismiss()
                            onToggleRejection(agent.number)
                        },
                    )
                }
                if (runActionsAvailable && isAgentStopAvailable(agent.status)) {
                    ResultAction(
                        label = appText("停止此 Agent", "Stop this Agent"),
                        icon = Icons.Default.Stop,
                        color = SystemRed,
                        onClick = {
                            onDismiss()
                            onRequestKill(agent.number)
                        },
                    )
                }
            }
        },
        confirmButton = {
            if (canAccept) {
                TextButton(
                    onClick = {
                        onDismiss()
                        onRequestAccept(agent.number)
                    },
                ) {
                    Icon(Icons.Default.Check, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text(appText("采用", "Use result"))
                }
            } else {
                TextButton(onClick = onDismiss) { Text(appText("完成", "Done")) }
            }
        },
        dismissButton = if (
            canAccept
        ) {
            { TextButton(onClick = onDismiss) { Text(appText("关闭", "Close")) } }
        } else {
            null
        },
    )
}

@Composable
private fun AgentFileChangeList(summary: AgentDiffSummary) {
    HorizontalDivider(Modifier.padding(top = 2.dp))
    Text(
        appText("文件修改（${summary.totalFiles}）", "File changes (${summary.totalFiles})"),
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.SemiBold,
    )
    if (summary.changes.isEmpty()) {
        Text(
            appText("没有文件修改", "No file changes"),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    summary.changes.forEach { change ->
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
        ) {
            Text(
                change.status,
                modifier = Modifier.width(22.dp),
                style = MaterialTheme.typography.labelMedium,
                color = when (change.status) {
                    "A" -> SystemGreen
                    "D" -> SystemRed
                    "T" -> SystemOrange
                    else -> MaterialTheme.colorScheme.primary
                },
                fontWeight = FontWeight.Bold,
            )
            SelectionContainer(modifier = Modifier.weight(1f)) {
                Text(
                    change.path,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
    if (summary.truncated) {
        val remaining = (summary.totalFiles - summary.changes.size).coerceAtLeast(0)
        Text(
            if (remaining > 0) {
                appText("另有 $remaining 个文件", "$remaining more files")
            } else {
                appText("文件列表仅显示前一部分", "Showing the first part of the file list")
            },
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun AgentResultMetric(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            modifier = Modifier.weight(1.25f),
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun ResultAction(
    label: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    color: Color = MaterialTheme.colorScheme.primary,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(10.dp))
        Text(label, color = color, style = MaterialTheme.typography.bodyMedium)
    }
}
