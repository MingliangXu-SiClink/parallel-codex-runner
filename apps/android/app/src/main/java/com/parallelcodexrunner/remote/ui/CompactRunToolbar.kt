package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.InternetAvailability
import java.util.Locale

@Composable
internal fun CompactRunToolbar(
    state: MainUiState,
    viewModel: MainViewModel,
    workspaceName: String?,
    onOpenConversations: () -> Unit,
    onOpenAgentDetails: (Int) -> Unit,
) {
    val controlsBusy = state.runActionInProgress
    val detailsAgent = state.selectedAgent?.takeIf { selected ->
        state.agentSummaries.any { it.number == selected }
    } ?: state.agentSummaries.singleOrNull()?.number
    var actionsPresented by rememberSaveable(state.visibleRunId) { mutableStateOf(false) }
    var addAgentsPresented by rememberSaveable(state.visibleRunId) { mutableStateOf(false) }
    var discardPresented by rememberSaveable(state.visibleRunId) { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 54.dp)
                .padding(start = 15.dp, end = 5.dp, top = 7.dp, bottom = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier
                    .weight(1f)
                    .clickable(onClick = onOpenConversations)
                    .padding(vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(
                    modifier = Modifier.weight(1f),
                    horizontalAlignment = Alignment.Start,
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(
                        state.visibleConversationTitle.takeIf(String::isNotBlank)
                            ?: workspaceName?.takeIf(String::isNotBlank)
                            ?: appText("对话", "Conversation"),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        val liveUpdateInterrupted = state.isVisibleRunUpdateInterrupted()
                        val summaryStatus = if (liveUpdateInterrupted) {
                            "reconnecting"
                        } else {
                            runSummaryStatus(state)
                        }
                        StatusDot(
                            status = summaryStatus,
                            color = if (liveUpdateInterrupted) {
                                SystemOrange
                            } else {
                                runSummaryColor(state)
                            },
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (liveUpdateInterrupted) {
                                appText("连接中断，正在重试", "Reconnecting")
                            } else {
                                compactRunProgressLabel(state, LocalAppLanguage.current)
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        when (runToolbarConnectionIssue(state)) {
                            RunToolbarConnectionIssue.INTERNET_UNAVAILABLE -> {
                                Spacer(Modifier.width(8.dp))
                                Icon(
                                    Icons.Default.WifiOff,
                                    contentDescription = appText(
                                        "手机网络不可用",
                                        "Phone network unavailable",
                                    ),
                                    tint = SystemOrange,
                                    modifier = Modifier.size(14.dp),
                                )
                            }
                            RunToolbarConnectionIssue.DEVICE_OFFLINE -> {
                                Spacer(Modifier.width(8.dp))
                                Icon(
                                    Icons.Default.Warning,
                                    contentDescription = appText("设备离线", "Device offline"),
                                    tint = MaterialTheme.colorScheme.error,
                                    modifier = Modifier.size(14.dp),
                                )
                            }
                            null -> Unit
                        }
                    }
                }
                Icon(
                    Icons.Default.KeyboardArrowDown,
                    contentDescription = appText("切换对话", "Switch conversation"),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(20.dp),
                )
            }
            if (state.visibleRunId != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box {
                        IconButton(
                            onClick = { actionsPresented = true },
                            enabled = !controlsBusy,
                            modifier = Modifier.size(40.dp),
                        ) {
                            Icon(
                                Icons.Default.MoreVert,
                                contentDescription = appText("任务操作", "Task actions"),
                            )
                        }
                        DropdownMenu(
                            expanded = actionsPresented,
                            onDismissRequest = { actionsPresented = false },
                        ) {
                            detailsAgent?.let { selected ->
                                DropdownMenuItem(
                                    text = {
                                        Text(appText("Agent 详情", "Agent details"))
                                    },
                                    leadingIcon = { Icon(Icons.Default.Description, null) },
                                    onClick = {
                                        actionsPresented = false
                                        onOpenAgentDetails(selected)
                                    },
                                )
                                HorizontalDivider()
                            }
                            DropdownMenuItem(
                                text = { Text(appText("刷新任务", "Refresh task")) },
                                leadingIcon = { Icon(Icons.Default.Refresh, null) },
                                onClick = {
                                    actionsPresented = false
                                    viewModel.refreshRun()
                                },
                            )
                            if (!isTerminalStatus(state.runStatus)) {
                                DropdownMenuItem(
                                    text = {
                                        Text(appText("停止本轮", "Stop turn"), color = SystemRed)
                                    },
                                    leadingIcon = {
                                        Icon(Icons.Default.Stop, null, tint = SystemRed)
                                    },
                                    onClick = {
                                        actionsPresented = false
                                        viewModel.stopCurrentRun()
                                    },
                                )
                            }
                            if (state.canAddAgents) {
                                DropdownMenuItem(
                                    text = { Text(appText("追加 Agent", "Add Agents")) },
                                    leadingIcon = { Icon(Icons.Default.Add, null) },
                                    onClick = {
                                        actionsPresented = false
                                        addAgentsPresented = true
                                    },
                                )
                            }
                            if (state.runStatus.equals("sync_ambiguous", ignoreCase = true) &&
                                state.finalizedAgent != null
                            ) {
                                DropdownMenuItem(
                                    text = {
                                        Text(appText("确认工作区同步状态", "Confirm workspace sync"))
                                    },
                                    leadingIcon = { Icon(Icons.Default.Warning, null) },
                                    onClick = {
                                        actionsPresented = false
                                        viewModel.requestFinalizationRecovery()
                                    },
                                )
                            } else if (
                                state.runStatus.equals("cleanup_failed", ignoreCase = true) &&
                                state.finalizedAgent != null
                            ) {
                                DropdownMenuItem(
                                    text = {
                                        Text(appText("重试运行文件清理", "Retry run cleanup"))
                                    },
                                    leadingIcon = { Icon(Icons.Default.Refresh, null) },
                                    onClick = {
                                        actionsPresented = false
                                        viewModel.retryFinalizationCleanup()
                                    },
                                )
                            }
                            if (state.finalizedAgent == null &&
                                state.runStatus.lowercase() !in setOf("finalized", "discarded")
                            ) {
                                HorizontalDivider()
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            appText("放弃并清理", "Discard and clean up"),
                                            color = SystemRed,
                                        )
                                    },
                                    leadingIcon = {
                                        Icon(Icons.Default.Delete, null, tint = SystemRed)
                                    },
                                    onClick = {
                                        actionsPresented = false
                                        discardPresented = true
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
    if (addAgentsPresented) {
        AddAgentsDialog(
            viewModel = viewModel,
            maximumAgentCount = state.maximumAgentCount,
            onDismiss = { addAgentsPresented = false },
        )
    }
    if (discardPresented) {
        DiscardRunDialog(viewModel = viewModel, onDismiss = { discardPresented = false })
    }
}

internal enum class RunToolbarConnectionIssue {
    INTERNET_UNAVAILABLE,
    DEVICE_OFFLINE,
}

internal fun runToolbarConnectionIssue(state: MainUiState): RunToolbarConnectionIssue? {
    if (state.internetAvailability == InternetAvailability.UNAVAILABLE) {
        return RunToolbarConnectionIssue.INTERNET_UNAVAILABLE
    }
    val selectedMacOnline = state.macs.firstOrNull {
        it.deviceId == state.selectedMacId
    }?.online
    return if (selectedMacOnline == false) RunToolbarConnectionIssue.DEVICE_OFFLINE else null
}

@Composable
internal fun CompactConversationToolbar(
    workspaceName: String?,
    conversationTitle: String,
    onOpenConversations: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp)
                .padding(horizontal = 15.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier
                    .weight(1f)
                    .clickable(onClick = onOpenConversations)
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    conversationTitle.takeIf(String::isNotBlank)
                        ?: workspaceName?.takeIf(String::isNotBlank)
                        ?: appText("对话", "Conversation"),
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Icon(
                    Icons.Default.KeyboardArrowDown,
                    contentDescription = appText("切换对话", "Switch conversation"),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

@Composable
internal fun StatusDot(status: String, color: Color = statusColor(status)) {
    val description = statusLabel(status, LocalAppLanguage.current)
    Box(
        Modifier
            .size(9.dp)
            .semantics { contentDescription = description }
            .background(color, RoundedCornerShape(50)),
    )
}

@Composable
internal fun GroupDivider(startPadding: androidx.compose.ui.unit.Dp = 0.dp) {
    HorizontalDivider(
        modifier = Modifier.padding(start = startPadding),
        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f),
    )
}

@Composable
internal fun EmptyListRow(text: String, icon: androidx.compose.ui.graphics.vector.ImageVector) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 22.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(12.dp))
        Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

internal fun statusLabel(
    status: String,
    language: AppLanguage = AppLanguage.CHINESE,
): String = when (status.lowercase()) {
    "active" -> language.text("已配对", "Paired")
    "preparing" -> language.text("准备中", "Preparing")
    "starting" -> language.text("启动中", "Starting")
    "copying" -> language.text("准备工作区", "Preparing workspace")
    "submitting" -> language.text("正在提交", "Submitting")
    "awaiting_confirmation" -> language.text("等待空间确认", "Awaiting storage confirmation")
    "queued" -> language.text("排队中", "Queued")
    "running" -> language.text("运行中", "Running")
    "waiting_for_models" -> language.text("正在获取模型", "Loading models")
    "stopping" -> language.text("正在停止", "Stopping")
    "finalizing" -> language.text("正在采用", "Applying result")
    "success" -> language.text("已完成", "Completed")
    "completed" -> language.text("全部完成", "All completed")
    "finalized" -> language.text("已采用", "Applied")
    "failed", "error" -> language.text("失败", "Failed")
    "killed", "stopped", "cancelled", "canceled", "interrupted" ->
        language.text("已停止", "Stopped")
    "expired" -> language.text("已过期", "Expired")
    "discarded" -> language.text("已放弃", "Discarded")
    "cleanup_failed" -> language.text("清理失败", "Cleanup failed")
    "sync_ambiguous" -> language.text("同步待确认", "Sync needs confirmation")
    "" -> language.text("等待中", "Waiting")
    else -> status.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

internal fun statusColor(status: String): Color = when (status.lowercase()) {
    "online" -> SystemGreen
    "success", "completed", "finalized" -> SystemGreen
    "failed", "error", "cleanup_failed" -> SystemRed
    "killed", "stopped", "cancelled", "canceled", "interrupted", "expired",
    "discarded", "sync_ambiguous" -> SystemOrange
    "preparing", "running", "queued", "copying", "starting", "stopping", "finalizing",
    "submitting" ->
        SystemBlue
    "awaiting_confirmation" -> SystemOrange
    else -> SystemGray
}

internal fun compactRunProgressLabel(
    state: MainUiState,
    language: AppLanguage = AppLanguage.CHINESE,
): String {
    state.pendingSubmission?.let { submission ->
        return pendingSubmissionLabel(submission.status, language)
    }
    val total = state.agentSummaries.size
    val statuses = state.agentSummaries.map { it.status.trim().lowercase() }
    val finished = statuses.count(::isTerminalAgentStatus)
    if (state.runStatus.equals("finalizing", ignoreCase = true) &&
        total > 0 && finished == total
    ) {
        return language.text("正在采用结果", "Applying result")
    }
    val active = statuses.any(::isActiveAgentStatus) ||
        runAwaitingAgentResults(state) ||
        (total == 0 && isActiveAgentStatus(state.runStatus))

    if (active || (!isTerminalStatus(state.runStatus) && finished < total)) {
        return if (total > 0 && finished > 0) {
            language.text("正在运行中 · $finished/$total 完成", "Running · $finished/$total finished")
        } else {
            language.text("正在运行中", "Running")
        }
    }

    when (state.runStatus.trim().lowercase()) {
        "finalized" -> return state.finalizedAgent?.let { agent ->
            val name = agentDisplayName(agent, state.agentSummaries, language)
            language.text(
                "结果已采用 · $name",
                "Result applied · $name",
            )
        } ?: language.text("结果已采用", "Result applied")
        "sync_ambiguous" -> return language.text("同步待确认", "Sync needs confirmation")
        "cleanup_failed" -> return language.text("清理失败", "Cleanup failed")
        "discarded" -> return language.text("任务已放弃", "Task discarded")
        "failed", "error" -> return language.text("任务失败", "Task failed")
        "stopped", "cancelled", "canceled", "interrupted", "expired" ->
            return language.text("任务已停止", "Task stopped")
    }

    if (total == 0) return statusLabel(state.runStatus, language)
    if (finished < total) return language.text("$finished/$total 完成", "$finished/$total finished")

    val success = statuses.count { it == "success" }
    if (success == total) return language.text("全部完成", "All completed")

    val failed = statuses.count { it == "failed" || it == "error" }
    val stopped = statuses.count {
        it in setOf(
            "killed", "stopped", "cancelled", "canceled", "interrupted", "discarded"
        )
    }
    val parts = buildList {
        if (success > 0) add(language.text("$success 成功", "$success succeeded"))
        if (failed > 0) add(language.text("$failed 失败", "$failed failed"))
        if (stopped > 0) add(language.text("$stopped 已停止", "$stopped stopped"))
    }
    return if (parts.isEmpty()) {
        language.text("全部完成", "All completed")
    } else {
        language.text("已完成 · ${parts.joinToString(" · ")}", "Finished · ${parts.joinToString(" · ")}")
    }
}

internal fun runSummaryStatus(state: MainUiState): String {
    val pendingStatus = state.pendingSubmission?.status ?: return state.runStatus
    return when (pendingStatus) {
        PendingPromptSubmission.STATUS_FAILED -> "failed"
        PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION -> "awaiting_confirmation"
        else -> "submitting"
    }
}

internal fun runSummaryColor(state: MainUiState): Color {
    if (state.pendingSubmission != null) return statusColor(runSummaryStatus(state))
    if (state.runStatus.equals("finalizing", ignoreCase = true)) {
        return statusColor("finalizing")
    }
    val statuses = state.agentSummaries.map { it.status.trim().lowercase() }
    if (statuses.any(::isActiveAgentStatus) ||
        (!isTerminalStatus(state.runStatus) && statuses.any { !isTerminalAgentStatus(it) })
    ) {
        return SystemBlue
    }
    return when {
        state.runStatus.equals("cleanup_failed", ignoreCase = true) -> SystemRed
        state.runStatus.equals("sync_ambiguous", ignoreCase = true) -> SystemOrange
        statuses.any { it == "failed" || it == "error" } -> SystemOrange
        statuses.any {
            it in setOf("killed", "cancelled", "canceled", "interrupted", "discarded")
        } -> SystemOrange
        statuses.isNotEmpty() && statuses.all { it == "success" } -> SystemGreen
        else -> statusColor(state.runStatus)
    }
}

internal fun isTerminalStatus(status: String): Boolean = isTerminalRunStatus(status)
