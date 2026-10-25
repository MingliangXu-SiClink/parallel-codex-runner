package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

@Composable
internal fun AppDialogs(state: MainUiState, viewModel: MainViewModel) {
    val workspaceDocumentState = rememberSaveableStateHolder()
    if (state.workspaceFileLoading) {
        AlertDialog(
            onDismissRequest = viewModel::cancelWorkspaceFileDownload,
            title = { Text(appText("正在打开文件", "Opening file")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    LinearProgressIndicator(
                        progress = { (state.workspaceFileProgress ?: 0) / 100f },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        state.workspaceFileProgress?.let { "$it%" }
                            ?: appText("正在从工作区读取", "Reading from workspace"),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = viewModel::cancelWorkspaceFileDownload) {
                    Text(appText("取消", "Cancel"))
                }
            },
        )
    }

    state.workspaceDocument?.let { document ->
        Dialog(
            onDismissRequest = viewModel::navigateBackWorkspaceDocument,
            properties = DialogProperties(usePlatformDefaultWidth = false),
        ) {
            Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.background,
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .statusBarsPadding()
                        .navigationBarsPadding(),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(56.dp)
                            .padding(horizontal = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            document.name,
                            modifier = Modifier
                                .weight(1f)
                                .padding(start = 8.dp),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                        )
                        IconButton(onClick = viewModel::openWorkspaceDocumentExternally) {
                            Icon(
                                Icons.AutoMirrored.Filled.OpenInNew,
                                contentDescription = appText(
                                    "使用其他应用打开",
                                    "Open with another app",
                                ),
                            )
                        }
                        IconButton(onClick = viewModel::dismissWorkspaceDocument) {
                            Icon(
                                Icons.Default.Close,
                                contentDescription = appText("关闭文件", "Close file"),
                            )
                        }
                    }
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    workspaceDocumentState.SaveableStateProvider(
                        "${document.source.absolutePath}:${document.line ?: 0}",
                    ) {
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .verticalScroll(rememberScrollState())
                                .padding(horizontal = 18.dp, vertical = 16.dp),
                        ) {
                            MarkdownTranscript(
                                content = document.content,
                                textColor = MaterialTheme.colorScheme.onBackground,
                                baseStyle = MaterialTheme.typography.bodyMedium,
                                onOpenLink = viewModel::openWorkspaceDocumentLink,
                            )
                        }
                    }
                }
            }
        }
    }

    state.storageConfirmation?.let { message ->
        AlertDialog(
            onDismissRequest = viewModel::cancelLargeRun,
            confirmButton = {
                TextButton(onClick = viewModel::confirmLargeRun) {
                    Text(appText("继续", "Continue"))
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelLargeRun) {
                    Text(appText("取消", "Cancel"))
                }
            },
            title = { Text(appText("确认存储空间", "Confirm storage space")) },
            text = { Text(message) },
        )
    }

    state.workspaceRemoval?.let { workspaceId ->
        val workspace = state.workspaces.firstOrNull { it.workspaceId == workspaceId }
        val mayDiscardResults = workspace?.let(::workspaceRemovalMayDiscardResults) == true
        AlertDialog(
            onDismissRequest = viewModel::cancelWorkspaceRemoval,
            confirmButton = {
                TextButton(onClick = viewModel::confirmWorkspaceRemoval) {
                    Text(
                        if (mayDiscardResults) {
                            appText("放弃结果并移除", "Discard results and remove")
                        } else {
                            appText("移除", "Remove")
                        }
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelWorkspaceRemoval) {
                    Text(appText("取消", "Cancel"))
                }
            },
            title = { Text(appText("移除工作区？", "Remove workspace?")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(workspace?.path ?: workspaceId)
                    Text(
                        if (mayDiscardResults) {
                            appText(
                                "尚未采用的 Agent 结果会被放弃并清理；目标设备上的原工作区文件不会删除。",
                                "Unapplied Agent results will be discarded and cleaned up. The original workspace files will not be deleted.",
                            )
                        } else {
                            appText(
                                "不会删除目标设备上的文件。",
                                "Files on the target computer will not be deleted.",
                            )
                        },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            },
        )
    }

    state.macRevocation?.let { deviceId ->
        val mac = state.macs.firstOrNull { it.deviceId == deviceId }
        AlertDialog(
            onDismissRequest = viewModel::cancelMacRevocation,
            confirmButton = {
                TextButton(onClick = viewModel::confirmMacRevocation) {
                    Text(appText("移除", "Remove"))
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelMacRevocation) {
                    Text(appText("取消", "Cancel"))
                }
            },
            title = { Text(appText("移除设备？", "Remove computer?")) },
            text = { Text(mac?.name ?: deviceId) },
        )
    }

    state.runDestructiveAction?.let { action ->
        val (title, text) = when (action) {
            is RunDestructiveAction.Accept -> appText("采用 Agent？", "Use Agent result?") to appText(
                "采用此 Agent 的结果会写入你的开发机仓库，并停止其它 Agent。",
                "Using this Agent's result will write to your development machine repository and stop other Agents."
            )
            is RunDestructiveAction.Kill -> appText("停止 Agent？", "Stop Agent?") to appText(
                "停止此 Agent 会终止其运行，但保留已完成的结果。",
                "Stopping this Agent will terminate it but keep any completed results."
            )
        }
        AlertDialog(
            onDismissRequest = viewModel::cancelRunDestructiveAction,
            confirmButton = {
                TextButton(onClick = viewModel::confirmRunDestructiveAction) {
                    Text(
                        when (action) {
                            is RunDestructiveAction.Accept -> appText("采用", "Use")
                            is RunDestructiveAction.Kill -> appText("停止", "Stop")
                        }
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelRunDestructiveAction) {
                    Text(appText("取消", "Cancel"))
                }
            },
            title = { Text(title) },
            text = { Text(text) },
        )
    }

    if (state.diffText != null || state.diffLoading) {
        AlertDialog(
            onDismissRequest = viewModel::clearAgentDiff,
            title = {
                Text(state.diffTitle.ifBlank { appText("Agent 修改", "Agent changes") })
            },
            text = {
                if (state.diffLoading) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(120.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        CircularProgressIndicator()
                    }
                } else {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 460.dp),
                    ) {
                        SelectionContainer {
                            Text(
                                state.diffText.orEmpty(),
                                style = MaterialTheme.typography.bodySmall.copy(
                                    fontFamily = FontFamily.Monospace,
                                ),
                                modifier = Modifier
                                    .verticalScroll(rememberScrollState())
                                    .horizontalScroll(rememberScrollState()),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = viewModel::clearAgentDiff) {
                    Text(appText("关闭", "Close"))
                }
            },
        )
    }

    if (state.recoveryConfirmation) {
        AlertDialog(
            onDismissRequest = viewModel::cancelFinalizationRecovery,
            title = { Text(appText("恢复中断的保存", "Resume interrupted save")) },
            text = {
                Text(
                    appText(
                        "优先核对文件，确认保存完整后继续，不会重新覆盖文件。旧任务没有核对记录时，才需要检查原文件夹并手动选择。",
                        "Verify the saved files and continue without overwriting them. Older tasks without a verification record require inspecting the original folder before choosing manually.",
                    )
                )
            },
            confirmButton = {
                Column(horizontalAlignment = Alignment.End) {
                    if (state.selectedMacId?.let {
                        viewModel.repository.cachedSupports(it, "runs.verify_finalization")
                    } == true) {
                        TextButton(onClick = { viewModel.recoverFinalization() }) {
                            Text(appText("核对文件并继续", "Verify Files and Continue"))
                        }
                    }
                    TextButton(
                        onClick = { viewModel.recoverFinalization(syncWasApplied = true) },
                    ) { Text(appText("我已确认文件完整，继续", "I checked: files are complete")) }
                    TextButton(
                        onClick = { viewModel.recoverFinalization(syncWasApplied = false) },
                    ) { Text(appText("文件不完整，重新选择结果", "Files are incomplete; choose again")) }
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelFinalizationRecovery) {
                    Text(appText("稍后处理", "Not now"))
                }
            },
        )
    }
}
