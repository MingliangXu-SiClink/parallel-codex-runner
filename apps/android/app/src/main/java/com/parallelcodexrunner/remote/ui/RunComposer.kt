package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.PendingPromptSubmission

@Composable
internal fun RunComposer(state: MainUiState, viewModel: MainViewModel) {
    val waitingForCurrentRun = runAwaitingAgentResults(state)
    val continuesImmediately = continuationStopsRunningAgents(state)
    val controlsBusy = state.runActionInProgress
    val continuationBlockReason = promptContinuationBlockReason(state)
    val acceptableAgent = currentAcceptableAgent(state)
    var settingsPresented by rememberSaveable { mutableStateOf(false) }
    var queuePresented by rememberSaveable { mutableStateOf(false) }
    val openQueue = {
        viewModel.beginQueuedFollowUpEditing()
        queuePresented = true
    }
    val closeQueue = {
        queuePresented = false
        viewModel.finishQueuedFollowUpEditing()
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f))
        if (continuationBlockReason != null && state.pendingSubmission == null &&
            !state.isVisibleConversationReadBlocked()
        ) {
            Row(
                modifier = Modifier
                    .widthIn(max = 800.dp)
                    .fillMaxWidth()
                    .padding(start = 14.dp, end = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    continuationBlockReason,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton(
                    onClick = viewModel::newWorkspaceConversation,
                    enabled = !controlsBusy,
                ) { Text(appText("新对话", "New conversation")) }
            }
        }
        state.pendingSubmission?.let { submission ->
            PendingSubmissionRow(
                submission = submission,
                onRetry = viewModel::retryPendingSubmission,
                onEdit = viewModel::editPendingSubmission,
                modifier = Modifier
                    .widthIn(max = 800.dp)
                    .fillMaxWidth(),
            )
        }
        if (state.followUpQueue.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .widthIn(max = 800.dp)
                    .padding(start = 10.dp, end = 10.dp, top = 8.dp)
                    .fillMaxWidth()
                    .background(
                        MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.72f),
                        RoundedCornerShape(8.dp),
                    )
                    .clickable(enabled = !controlsBusy, onClick = openQueue)
                    .padding(horizontal = 11.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Default.Schedule,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(7.dp))
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            appText(
                                "已排队 ${state.followUpQueue.size} 条",
                                "${state.followUpQueue.size} queued",
                            ),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            if (state.followUpQueue.first().paused) {
                                appText("需要处理", "Needs attention")
                            } else state.followUpSecondsRemaining?.let { seconds ->
                                if (seconds > 0) {
                                    appText("${seconds}s 后继续", "Continue in ${seconds}s")
                                } else {
                                    appText("即将继续", "Continuing soon")
                                }
                            } ?: if (waitingForCurrentRun) {
                                appText("等待当前 Agents", "Waiting for current Agents")
                            } else {
                                appText("等待可继续的 Agent", "Waiting for an available Agent")
                            },
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                    Text(
                        state.followUpQueue.first().prompt
                            .replace('\n', ' ')
                            .replace('\r', ' '),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Icon(
                    Icons.Default.ChevronRight,
                    contentDescription = appText("编辑排队中的提问", "Edit queued prompts"),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Row(
            modifier = Modifier
                .widthIn(max = 800.dp)
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 9.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(7.dp),
        ) {
            IconButton(
                onClick = {
                    settingsPresented = true
                    viewModel.refreshRunSettingsOptions()
                },
                enabled = !controlsBusy,
                modifier = Modifier.size(42.dp),
            ) {
                Icon(
                    Icons.Default.Settings,
                    contentDescription = appText("运行设置", "Run settings"),
                )
            }
            BasicTextField(
                value = state.prompt,
                onValueChange = viewModel::setPrompt,
                minLines = 1,
                maxLines = 6,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(
                    onSend = {
                        if (canSubmitComposer(state)) {
                            viewModel.startRun()
                        }
                    },
                ),
                textStyle = MaterialTheme.typography.bodyLarge.copy(
                    color = MaterialTheme.colorScheme.onSurface,
                ),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 44.dp, max = 138.dp)
                    .background(
                        MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(8.dp),
                    )
                    .border(
                        1.dp,
                        MaterialTheme.colorScheme.outline.copy(alpha = 0.62f),
                        RoundedCornerShape(8.dp),
                    )
                    .padding(horizontal = 11.dp, vertical = 10.dp),
                decorationBox = { innerTextField ->
                    Box(Modifier.fillMaxWidth()) {
                        if (state.prompt.isEmpty()) {
                            Text(
                                when {
                                    continuesImmediately -> appText(
                                        "继续后将停止其他 Agents",
                                        "Continuing will stop the other Agents",
                                    )
                                    waitingForCurrentRun -> appText(
                                        "输入后将加入队列",
                                        "Your prompt will be queued",
                                    )
                                    continuationBlockReason != null -> appText("草稿", "Draft")
                                    state.visibleRunId == null -> appText("输入任务", "Enter a task")
                                    else -> appText("继续提问", "Continue the conversation")
                                },
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.bodyLarge,
                            )
                        }
                        innerTextField()
                    }
                },
            )
            acceptableAgent?.let { agent ->
                IconButton(
                    onClick = { viewModel.acceptAgent(agent) },
                    enabled = !controlsBusy,
                    modifier = Modifier.size(42.dp),
                ) {
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = appText("采用当前回复", "Use current reply"),
                        tint = if (controlsBusy) {
                            MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                        } else {
                            SystemGreen
                        },
                        modifier = Modifier.size(28.dp),
                    )
                }
            }
            FilledIconButton(
                onClick = { viewModel.startRun() },
                enabled = canSubmitComposer(state),
                modifier = Modifier.size(44.dp),
            ) {
                if (state.pendingSubmission != null) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp,
                    )
                } else {
                    Icon(
                        Icons.AutoMirrored.Filled.Send,
                        contentDescription = if (continuesImmediately) {
                            appText("停止其他 Agents 并继续", "Stop other Agents and continue")
                        } else {
                            appText("发送任务", "Send task")
                        },
                    )
                }
            }
        }
    }

    if (queuePresented) {
        FollowUpQueueDialog(
            state = state,
            viewModel = viewModel,
            onDismiss = closeQueue,
        )
    }

    if (settingsPresented) {
        RunSettingsSheet(
            state = state,
            viewModel = viewModel,
            onDismiss = { settingsPresented = false },
        )
    }
}

@Composable
private fun PendingSubmissionRow(
    submission: PendingPromptSubmission,
    onRetry: () -> Unit,
    onEdit: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val failed = submission.status == PendingPromptSubmission.STATUS_FAILED
    val status = pendingSubmissionLabel(submission.status, LocalAppLanguage.current)
    Row(
        modifier = modifier
            .padding(start = 14.dp, end = 8.dp, top = 7.dp, bottom = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (!failed) {
            CircularProgressIndicator(
                modifier = Modifier.size(15.dp),
                strokeWidth = 2.dp,
            )
            Spacer(Modifier.width(8.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(
                status,
                style = MaterialTheme.typography.labelMedium,
                color = if (failed) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.primary,
            )
            val detail = submission.error
                .takeIf(String::isNotBlank)
                ?.let { friendlyStoredError(it, LocalAppLanguage.current) }
                ?: submission.prompt
            Text(
                detail.replace('\n', ' ').replace('\r', ' '),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (failed) {
            TextButton(onClick = onEdit) { Text(appText("编辑", "Edit")) }
            TextButton(onClick = onRetry) { Text(appText("重试", "Retry")) }
        }
    }
}

@Composable
private fun FollowUpQueueDialog(
    state: MainUiState,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(appText("排队中的提问", "Queued prompts")) },
        text = {
            LazyColumn(
                modifier = Modifier.heightIn(max = 420.dp),
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(10.dp),
            ) {
                items(
                    items = state.followUpQueue,
                    key = { item -> item.id },
                ) { item ->
                    val index = state.followUpQueue.indexOfFirst { it.id == item.id }
                    if (index < 0) return@items
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.Top,
                    ) {
                        Column(Modifier.weight(1f)) {
                            OutlinedTextField(
                                value = item.prompt,
                                onValueChange = { viewModel.updateQueuedFollowUp(item.id, it) },
                                modifier = Modifier.fillMaxWidth(),
                                label = {
                                    Text(appText("第 ${index + 1} 条", "Prompt ${index + 1}"))
                                },
                                minLines = 1,
                                maxLines = 5,
                            )
                            if (item.lastError.isNotBlank()) {
                                val error = friendlyStoredError(
                                    item.lastError,
                                    LocalAppLanguage.current,
                                )
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(start = 4.dp, top = 4.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text(
                                        if (item.paused) {
                                            appText(
                                                "自动重试已暂停：$error",
                                                "Automatic retry paused: $error",
                                            )
                                        } else {
                                            appText(
                                                "暂未发送：$error",
                                                "Not sent yet: $error",
                                            )
                                        },
                                        modifier = Modifier.weight(1f),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = if (item.paused) {
                                            MaterialTheme.colorScheme.error
                                        } else {
                                            MaterialTheme.colorScheme.onSurfaceVariant
                                        },
                                        maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    if (item.paused) {
                                        TextButton(
                                            onClick = {
                                                viewModel.retryQueuedFollowUp(item.id)
                                            },
                                        ) { Text(appText("重试", "Retry")) }
                                    }
                                }
                            }
                        }
                        Column {
                            IconButton(
                                onClick = { viewModel.moveQueuedFollowUp(item.id, -1) },
                                enabled = index > 0,
                            ) {
                                Icon(
                                    Icons.Default.KeyboardArrowUp,
                                    contentDescription = appText("上移", "Move up"),
                                )
                            }
                            IconButton(
                                onClick = { viewModel.moveQueuedFollowUp(item.id, 1) },
                                enabled = index < state.followUpQueue.lastIndex,
                            ) {
                                Icon(
                                    Icons.Default.KeyboardArrowDown,
                                    contentDescription = appText("下移", "Move down"),
                                )
                            }
                            IconButton(onClick = { viewModel.removeQueuedFollowUp(item.id) }) {
                                Icon(
                                    Icons.Default.Delete,
                                    contentDescription = appText("删除", "Delete"),
                                    tint = SystemRed,
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(appText("完成", "Done")) }
        },
        dismissButton = {
            TextButton(
                onClick = {
                    viewModel.clearQueuedFollowUps()
                    onDismiss()
                },
            ) { Text(appText("清空全部", "Clear all"), color = SystemRed) }
        },
    )
}

@Composable
internal fun AddAgentsDialog(
    viewModel: MainViewModel,
    maximumAgentCount: Int,
    onDismiss: () -> Unit,
) {
    var countText by rememberSaveable { mutableStateOf("1") }
    val count = countText.toIntOrNull()?.takeIf { it in 1..maximumAgentCount }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(appText("追加 Agent", "Add Agents")) },
        text = {
            OutlinedTextField(
                value = countText,
                onValueChange = { value -> countText = value.filter(Char::isDigit).take(4) },
                label = {
                    Text(appText("数量（1-$maximumAgentCount）", "Count (1-$maximumAgentCount)"))
                },
                singleLine = true,
            )
        },
        confirmButton = {
            TextButton(
                onClick = {
                    count?.let(viewModel::addMoreAgents)
                    onDismiss()
                },
                enabled = count != null,
            ) { Text(appText("追加", "Add")) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text(appText("取消", "Cancel")) }
        },
    )
}

@Composable
internal fun DiscardRunDialog(
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(appText("放弃当前任务？", "Discard this task?")) },
        text = {
            Text(
                appText(
                    "当前结果不会同步回工作区，并会按保留副本设置清理运行文件。",
                    "The current result will not be synced back. Run files will be cleaned according to your workspace retention setting.",
                )
            )
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onDismiss()
                    viewModel.discardCurrentRun()
                },
            ) { Text(appText("放弃并清理", "Discard and clean up"), color = SystemRed) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text(appText("取消", "Cancel")) }
        },
    )
}
