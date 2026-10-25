package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.LocalOverscrollFactory
import androidx.compose.foundation.BorderStroke
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import com.parallelcodexrunner.remote.data.CodexModelOptions
import com.parallelcodexrunner.remote.data.CodexOption
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.ResumeSession

private data class SettingChoice(
    val value: String,
    val label: String,
)

private data class RunSettingsActions(
    val agents: (Int) -> Unit,
    val synthesisAgents: (Int) -> Unit,
    val execution: (String) -> Unit,
    val automaticMaxParallel: (Boolean) -> Unit,
    val maxParallel: (Int) -> Unit,
    val recommendBy: (String) -> Unit,
    val followUpDelay: (Int) -> Unit,
    val model: (String) -> Unit,
    val effort: (String) -> Unit,
    val fastMode: (String) -> Unit,
    val synthesisModel: (String) -> Unit,
    val synthesisEffort: (String) -> Unit,
    val synthesisFastMode: (String) -> Unit,
    val subagents: (Boolean) -> Unit,
    val subagentsLimit: (Int) -> Unit,
    val syncBack: (Boolean) -> Unit,
    val keepWorkspaces: (Boolean) -> Unit,
)

private data class WorkspaceSettingsActions(
    val sessions: List<ResumeSession>,
    val resumeLoading: Boolean,
    val resumeError: String?,
    val resumeEditable: Boolean,
    val busy: Boolean,
    val resumeSession: (String) -> Unit,
    val reloadResumeSessions: () -> Unit,
    val includeNonInteractive: (Boolean) -> Unit,
    val codexExecutable: (String) -> Unit,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RunSettingsSheet(
    state: MainUiState,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val settings = state.runSettings
    val synthesisEditable = state.visibleRunId == null ||
        isTerminalRunStatus(state.runStatus) ||
        state.canUpdateSynthesis
    val resumeEditable = state.visibleRunId == null ||
        isTerminalRunStatus(state.runStatus)
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        sheetGesturesEnabled = false,
        dragHandle = null,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .navigationBarsPadding(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 20.dp, top = 8.dp, end = 12.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        appText("运行设置", "Run settings"),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        appText(
                            "${settings.agents} 个候选 · ${settings.synthesisAgents} 个综合 Agent",
                            "${settings.agents} candidates · ${settings.synthesisAgents} synthesis Agents",
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onDismiss) { Text(appText("完成", "Done")) }
            }
            if (state.settingsOptionsLoading) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            } else {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f))
            }

            state.settingsOptionsError?.takeIf(String::isNotBlank)?.let { error ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 20.dp, end = 10.dp, top = 4.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        error,
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    TextButton(onClick = { viewModel.refreshRunSettingsOptions() }) {
                        Text(appText("重新加载", "Reload"))
                    }
                }
            }

            CompositionLocalProvider(LocalOverscrollFactory provides null) {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 20.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    runSettingsItems(
                        settings = settings,
                        maximumAgentCount = state.maximumAgentCount,
                        primaryOptions = state.primaryModelOptions,
                        synthesisOptions = state.synthesisModelOptions,
                        synthesisEditable = synthesisEditable,
                        actions = RunSettingsActions(
                            agents = viewModel::setAgents,
                            synthesisAgents = viewModel::setSynthesisAgents,
                            execution = viewModel::setExecution,
                            automaticMaxParallel = viewModel::setAutomaticMaxParallel,
                            maxParallel = viewModel::setMaxParallel,
                            recommendBy = viewModel::setRecommendBy,
                            followUpDelay = viewModel::setFollowUpDelay,
                            model = viewModel::setModel,
                            effort = viewModel::setEffort,
                            fastMode = viewModel::setFastMode,
                            synthesisModel = viewModel::setSynthesisModel,
                            synthesisEffort = viewModel::setSynthesisEffort,
                            synthesisFastMode = viewModel::setSynthesisFastMode,
                            subagents = viewModel::setSubagents,
                            subagentsLimit = viewModel::setSubagentsLimit,
                            syncBack = viewModel::setSyncBack,
                            keepWorkspaces = viewModel::setKeepWorkspaces,
                        ),
                        workspace = WorkspaceSettingsActions(
                            sessions = state.resumeSessions,
                            resumeLoading = state.resumeSessionsLoading,
                            resumeError = state.resumeSessionsError,
                            resumeEditable = resumeEditable,
                            busy = state.runActionInProgress,
                            resumeSession = viewModel::setResumeSessionId,
                            reloadResumeSessions = viewModel::refreshResumeSessions,
                            includeNonInteractive = viewModel::setResumeIncludeNonInteractive,
                            codexExecutable = viewModel::setCodexExecutable,
                        ),
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GeneralConversationSettingsSheet(
    state: MainUiState,
    viewModel: MainViewModel,
    macName: String,
    onDismiss: () -> Unit,
) {
    val settings = state.defaultRunSettings
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val update: (
        Boolean,
        (RemoteRunSettings) -> RemoteRunSettings,
    ) -> Unit = { refreshOptions, transform ->
        viewModel.updateDefaultConversationSettings(refreshOptions, transform)
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        sheetGesturesEnabled = false,
        dragHandle = null,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .navigationBarsPadding(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 20.dp, top = 8.dp, end = 12.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        appText("默认对话设置", "Default conversation settings"),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        appText(
                            "$macName · 仅影响这台设备以后添加的工作区",
                            "$macName · Applies to workspaces added later",
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onDismiss) { Text(appText("完成", "Done")) }
            }
            when {
                state.defaultSettingsLoading -> {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                state.defaultSettingsSaving -> {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                else -> {
                    HorizontalDivider(
                        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)
                    )
                }
            }

            state.defaultSettingsError?.takeIf(String::isNotBlank)?.let { error ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 20.dp, end = 10.dp, top = 4.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        error,
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    TextButton(onClick = viewModel::retryDefaultConversationSettings) {
                        Text(appText("重试", "Retry"))
                    }
                }
            }

            state.defaultSettingsOptionsError
                ?.takeIf(String::isNotBlank)
                ?.let { error ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 20.dp, end = 10.dp, top = 2.dp, bottom = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            error,
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        TextButton(onClick = viewModel::reloadDefaultConversationModelOptions) {
                            Text(appText("重新加载", "Reload"))
                        }
                    }
                }

            CompositionLocalProvider(LocalOverscrollFactory provides null) {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 20.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    runSettingsItems(
                        settings = settings,
                        maximumAgentCount = state.maximumAgentCount,
                        primaryOptions = state.defaultPrimaryModelOptions,
                        synthesisOptions = state.defaultSynthesisModelOptions,
                        synthesisEditable = true,
                        actions = RunSettingsActions(
                            agents = { value -> update(false) { it.copy(agents = value) } },
                            synthesisAgents = { value ->
                                update(false) { it.copy(synthesisAgents = value) }
                            },
                            execution = { value ->
                                update(false) { it.copy(execution = value) }
                            },
                            automaticMaxParallel = { value ->
                                update(false) { it.copy(automaticMaxParallel = value) }
                            },
                            maxParallel = { value ->
                                update(false) { it.copy(maxParallel = value) }
                            },
                            recommendBy = { value ->
                                update(false) { it.copy(recommendBy = value) }
                            },
                            followUpDelay = { value ->
                                update(false) { it.copy(followUpDelay = value) }
                            },
                            model = { value ->
                                update(true) {
                                    val changed = it.model != value
                                    it.copy(
                                        model = value,
                                        effort = if (changed) "" else it.effort,
                                        fastMode = if (changed) "automatic" else it.fastMode,
                                    )
                                }
                            },
                            effort = { value -> update(false) { it.copy(effort = value) } },
                            fastMode = { value ->
                                update(false) { it.copy(fastMode = value) }
                            },
                            synthesisModel = { value ->
                                update(true) {
                                    val changed = it.synthesisModel != value
                                    it.copy(
                                        synthesisModel = value,
                                        synthesisEffort = if (changed) {
                                            ""
                                        } else {
                                            it.synthesisEffort
                                        },
                                        synthesisFastMode = if (changed) {
                                            "automatic"
                                        } else {
                                            it.synthesisFastMode
                                        },
                                    )
                                }
                            },
                            synthesisEffort = { value ->
                                update(false) { it.copy(synthesisEffort = value) }
                            },
                            synthesisFastMode = { value ->
                                update(false) { it.copy(synthesisFastMode = value) }
                            },
                            subagents = { value ->
                                update(false) { it.copy(subagents = value) }
                            },
                            subagentsLimit = { value ->
                                update(false) { it.copy(subagentsLimit = value) }
                            },
                            syncBack = { value ->
                                update(false) { it.copy(syncBack = value) }
                            },
                            keepWorkspaces = { value ->
                                update(false) { it.copy(keepWorkspaces = value) }
                            },
                        ),
                        workspace = null,
                    )
                }
            }
        }
    }
}

private fun LazyListScope.runSettingsItems(
    settings: RemoteRunSettings,
    maximumAgentCount: Int,
    primaryOptions: CodexModelOptions?,
    synthesisOptions: CodexModelOptions?,
    synthesisEditable: Boolean,
    actions: RunSettingsActions,
    workspace: WorkspaceSettingsActions?,
) {
    item { SettingsSectionTitle(appText("运行", "Run")) }
    item {
        SettingsStepperRow(
            label = appText("候选 Agent", "Candidate Agents"),
            value = settings.agents,
            range = 1..maximumAgentCount,
            onValue = actions.agents,
        )
    }
    item {
        SettingsStepperRow(
            label = appText("综合 Agent", "Synthesis Agents"),
            value = settings.synthesisAgents,
            range = 0..maximumAgentCount,
            enabled = synthesisEditable,
            onValue = actions.synthesisAgents,
        )
    }
    item {
        SettingsSegmentedRow(
            label = appText("执行方式", "Execution"),
            value = settings.execution,
            options = listOf(
                SettingChoice("parallel", appText("并行", "Parallel")),
                SettingChoice("serial", appText("串行", "Serial")),
            ),
            onValue = actions.execution,
        )
    }
    item {
        MaxParallelSetting(
            automatic = settings.automaticMaxParallel,
            value = settings.maxParallel,
            maximumAgentCount = maximumAgentCount,
            serial = settings.execution == "serial",
            onAutomatic = actions.automaticMaxParallel,
            onValue = actions.maxParallel,
        )
    }
    item {
        SettingsSegmentedRow(
            label = appText("推荐依据", "Recommend by"),
            value = settings.recommendBy,
            options = listOf(
                SettingChoice("reasoning_tokens", "Tokens"),
                SettingChoice("duration", appText("耗时", "Duration")),
            ),
            onValue = actions.recommendBy,
        )
    }
    item {
        SettingsNumberRow(
            label = appText("后续提问等待", "Follow-up delay"),
            value = settings.followUpDelay,
            range = 0..3_600,
            suffix = appText("秒", "sec"),
            onValue = actions.followUpDelay,
        )
    }

    item { SettingsSectionTitle("Codex") }
    item {
        SettingsChoiceRow(
            label = appText("模型", "Model"),
            value = settings.model,
            options = modelChoices(
                primaryOptions,
                synthesisOptions,
                settings.model,
                LocalAppLanguage.current,
            ),
            onValue = actions.model,
        )
    }
    item {
        SettingsChoiceRow(
            label = appText("推理强度", "Effort"),
            value = settings.effort,
            options = effortChoices(primaryOptions, settings.effort, LocalAppLanguage.current),
            onValue = actions.effort,
        )
    }
    item {
        SettingsChoiceRow(
            label = appText("Fast 模式", "Fast mode"),
            value = settings.fastMode,
            options = fastChoices(primaryOptions, LocalAppLanguage.current),
            onValue = actions.fastMode,
        )
    }

    item { SettingsSectionTitle(appText("综合阶段", "Synthesis")) }
    item {
        SettingsChoiceRow(
            label = appText("模型", "Model"),
            value = settings.synthesisModel,
            options = modelChoices(
                synthesisOptions,
                primaryOptions,
                settings.synthesisModel,
                LocalAppLanguage.current,
            ),
            enabled = synthesisEditable,
            onValue = actions.synthesisModel,
        )
    }
    item {
        SettingsChoiceRow(
            label = appText("推理强度", "Effort"),
            value = settings.synthesisEffort,
            options = effortChoices(
                synthesisOptions,
                settings.synthesisEffort,
                LocalAppLanguage.current,
            ),
            enabled = synthesisEditable,
            onValue = actions.synthesisEffort,
        )
    }
    item {
        SettingsChoiceRow(
            label = appText("Fast 模式", "Fast mode"),
            value = settings.synthesisFastMode,
            options = fastChoices(synthesisOptions, LocalAppLanguage.current),
            enabled = synthesisEditable,
            onValue = actions.synthesisFastMode,
        )
    }

    item { SettingsSectionTitle(appText("子 Agent", "Subagents")) }
    item {
        SettingsSwitchRow(
            label = appText("允许嵌套 Agent", "Allow nested Agents"),
            checked = settings.subagents,
            onCheckedChange = actions.subagents,
        )
    }
    if (settings.subagents) {
        item {
            SettingsStepperRow(
                label = appText("子 Agent 上限", "Subagent limit"),
                value = settings.subagentsLimit,
                range = 1..maximumAgentCount,
                onValue = actions.subagentsLimit,
            )
        }
    }

    item {
        SettingsSectionTitle(
            if (workspace == null) appText("工作区行为", "Workspace behavior")
            else appText("工作区", "Workspace")
        )
    }
    if (workspace != null) {
        if (workspace.resumeLoading) {
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                    Text(
                        appText(
                            "正在加载之前的对话，其他设置仍可修改",
                            "Loading previous conversations; other settings remain editable",
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else if (!workspace.resumeError.isNullOrBlank()) {
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        appText(
                            "之前的对话暂时无法加载",
                            "Previous conversations are temporarily unavailable",
                        ),
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    TextButton(onClick = workspace.reloadResumeSessions) {
                        Text(appText("重试", "Retry"))
                    }
                }
            }
        }
        item {
            SettingsChoiceRow(
                label = appText("继续对话", "Continue conversation"),
                value = settings.resumeSessionId.ifBlank { "NO" },
                options = resumeChoices(
                    workspace.sessions,
                    settings.resumeSessionId,
                    LocalAppLanguage.current,
                ),
                enabled = workspace.resumeEditable && !workspace.busy,
                onValue = workspace.resumeSession,
            )
        }
        item {
            SettingsSwitchRow(
                label = appText("包含非交互对话", "Include non-interactive conversations"),
                checked = settings.resumeIncludeNonInteractive,
                enabled = workspace.resumeEditable,
                onCheckedChange = workspace.includeNonInteractive,
            )
        }
    }
    item {
        SettingsSwitchRow(
            label = appText("同步回原工作区", "Sync back to original workspace"),
            checked = settings.syncBack,
            onCheckedChange = actions.syncBack,
        )
    }
    item {
        SettingsSwitchRow(
            label = appText("保留候选工作区", "Keep candidate workspaces"),
            checked = settings.keepWorkspaces,
            onCheckedChange = actions.keepWorkspaces,
        )
    }
    if (workspace != null) {
        item {
            SettingsTextRow(
                label = appText("Codex 命令", "Codex command"),
                value = settings.codexBin,
                placeholder = "codex",
                onValue = workspace.codexExecutable,
            )
        }
    }
    item { Spacer(Modifier.height(12.dp)) }
}

@Composable
private fun SettingsSectionTitle(title: String) {
    Text(
        title,
        modifier = Modifier.padding(top = 14.dp, bottom = 4.dp),
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        fontWeight = FontWeight.SemiBold,
    )
}

@Composable
private fun SettingsNumberRow(
    label: String,
    value: Int,
    range: IntRange,
    onValue: (Int) -> Unit,
    suffix: String = "",
    enabled: Boolean = true,
) {
    var text by remember(value) { mutableStateOf(value.toString()) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            fontWeight = FontWeight.Medium,
            color = settingTextColor(enabled),
        )
        OutlinedTextField(
            value = text,
            onValueChange = { raw ->
                if (raw.length <= 5 && raw.all(Char::isDigit)) {
                    text = raw
                    raw.toIntOrNull()?.takeIf { it in range }?.let(onValue)
                }
            },
            enabled = enabled,
            singleLine = true,
            suffix = suffix.takeIf(String::isNotEmpty)?.let { value ->
                { Text(value) }
            },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier
                .width(if (suffix.isEmpty()) 92.dp else 112.dp)
                .onFocusChanged { focus ->
                    val parsed = text.toIntOrNull()
                    if (!focus.isFocused && (parsed == null || parsed !in range)) {
                        text = value.toString()
                    }
                },
        )
    }
}

@Composable
private fun SettingsStepperRow(
    label: String,
    value: Int,
    range: IntRange,
    onValue: (Int) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            fontWeight = FontWeight.Medium,
            color = settingTextColor(enabled),
        )
        Surface(
            shape = MaterialTheme.shapes.small,
            color = MaterialTheme.colorScheme.surface,
            contentColor = settingTextColor(enabled),
            border = BorderStroke(
                1.dp,
                MaterialTheme.colorScheme.outline.copy(alpha = if (enabled) 0.72f else 0.34f),
            ),
        ) {
            Row(
                modifier = Modifier.height(42.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(
                    onClick = { onValue((value - 1).coerceIn(range)) },
                    enabled = enabled && value > range.first,
                    modifier = Modifier.size(42.dp),
                ) {
                    Icon(
                        Icons.Default.Remove,
                        contentDescription = appText("减少$label", "Decrease $label"),
                        modifier = Modifier.size(18.dp),
                    )
                }
                Text(
                    text = value.toString(),
                    modifier = Modifier.width(38.dp),
                    style = MaterialTheme.typography.titleMedium,
                    color = settingTextColor(enabled),
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                )
                IconButton(
                    onClick = { onValue((value + 1).coerceIn(range)) },
                    enabled = enabled && value < range.last,
                    modifier = Modifier.size(42.dp),
                ) {
                    Icon(
                        Icons.Default.Add,
                        contentDescription = appText("增加$label", "Increase $label"),
                        modifier = Modifier.size(18.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun MaxParallelSetting(
    automatic: Boolean,
    value: Int,
    maximumAgentCount: Int,
    serial: Boolean,
    onAutomatic: (Boolean) -> Unit,
    onValue: (Int) -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        SettingsSwitchRow(
            label = appText("最大并行自动设置", "Automatic max parallel"),
            checked = automatic,
            enabled = !serial,
            onCheckedChange = onAutomatic,
        )
        if (!automatic && !serial) {
            SettingsStepperRow(
                label = appText("最大并行数", "Max parallel"),
                value = value,
                range = 1..maximumAgentCount,
                onValue = onValue,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsSegmentedRow(
    label: String,
    value: String,
    options: List<SettingChoice>,
    onValue: (String) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, modifier = Modifier.weight(1f), fontWeight = FontWeight.Medium)
        SingleChoiceSegmentedButtonRow(Modifier.widthIn(max = 220.dp)) {
            options.forEachIndexed { index, option ->
                SegmentedButton(
                    selected = value == option.value,
                    onClick = { onValue(option.value) },
                    shape = SegmentedButtonDefaults.itemShape(index, options.size),
                    label = {
                        Text(option.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    },
                )
            }
        }
    }
}

@Composable
private fun SettingsSwitchRow(
    label: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 50.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            fontWeight = FontWeight.Medium,
            color = settingTextColor(enabled),
        )
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
        )
    }
}

@Composable
private fun SettingsTextRow(
    label: String,
    value: String,
    placeholder: String,
    onValue: (String) -> Unit,
    enabled: Boolean = true,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            label,
            fontWeight = FontWeight.Medium,
            color = settingTextColor(enabled),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValue,
            enabled = enabled,
            singleLine = true,
            placeholder = { Text(placeholder) },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun SettingsChoiceRow(
    label: String,
    value: String,
    options: List<SettingChoice>,
    onValue: (String) -> Unit,
    enabled: Boolean = true,
) {
    var expanded by remember(label) { mutableStateOf(false) }
    val selected = options.firstOrNull { it.value == value }
        ?: SettingChoice(value, value.ifBlank { "默认" })
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 54.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            fontWeight = FontWeight.Medium,
            color = settingTextColor(enabled),
        )
        Box {
            OutlinedButton(
                onClick = { expanded = true },
                enabled = enabled && options.isNotEmpty(),
                contentPadding = PaddingValues(horizontal = 11.dp, vertical = 6.dp),
                modifier = Modifier.widthIn(min = 142.dp, max = 230.dp),
            ) {
                Text(
                    selected.label,
                    modifier = Modifier.weight(1f, fill = false),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.width(4.dp))
                Icon(
                    Icons.Default.KeyboardArrowDown,
                    contentDescription = appText("选择 $label", "Choose $label"),
                    modifier = Modifier.size(18.dp),
                )
            }
            DropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                options.forEach { option ->
                    DropdownMenuItem(
                        text = {
                            Text(
                                option.label,
                                modifier = Modifier.widthIn(max = 290.dp),
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        },
                        trailingIcon = if (option.value == value) {
                            {
                                Icon(
                                    Icons.Default.Check,
                                    contentDescription = appText("当前选择", "Selected"),
                                )
                            }
                        } else {
                            null
                        },
                        onClick = {
                            expanded = false
                            onValue(option.value)
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun settingTextColor(enabled: Boolean) = if (enabled) {
    MaterialTheme.colorScheme.onSurface
} else {
    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
}

private fun modelChoices(
    options: CodexModelOptions?,
    fallbackOptions: CodexModelOptions?,
    current: String,
    language: AppLanguage = AppLanguage.CHINESE,
): List<SettingChoice> = buildChoices(
    dynamic = options?.models?.takeIf { it.isNotEmpty() }
        ?: fallbackOptions?.models.orEmpty(),
    current = current,
    fallback = SettingChoice(
        "",
        (options?.effectiveModel ?: fallbackOptions?.effectiveModel)
            ?.takeIf(String::isNotBlank)
            ?.let { language.text("默认（$it）", "Default ($it)") }
            ?: language.text("默认", "Default"),
    ),
)

private fun effortChoices(
    options: CodexModelOptions?,
    current: String,
    language: AppLanguage = AppLanguage.CHINESE,
): List<SettingChoice> = buildChoices(
    dynamic = options?.efforts.orEmpty(),
    current = current,
    fallback = SettingChoice(
        "",
        localizedAutomaticLabel(options?.effectiveEffort, language),
    ),
)

private fun fastChoices(
    options: CodexModelOptions?,
    language: AppLanguage = AppLanguage.CHINESE,
): List<SettingChoice> = listOf(
    SettingChoice("automatic", localizedAutomaticLabel(options?.effectiveFast, language)),
    SettingChoice("on", language.text("开启", "On")),
    SettingChoice("off", language.text("关闭", "Off")),
)

private fun resumeChoices(
    sessions: List<ResumeSession>,
    current: String,
    language: AppLanguage = AppLanguage.CHINESE,
): List<SettingChoice> = buildList {
    add(SettingChoice("NO", language.text("新对话", "New conversation")))
    sessions.forEach { session ->
        if (none { it.value == session.sessionId }) {
            add(SettingChoice(session.sessionId, session.displayTitle))
        }
    }
    if (!isNoResumeSession(current) && none { it.value == current }) {
        add(SettingChoice(current, language.text("当前对话", "Current conversation")))
    }
}

private fun buildChoices(
    dynamic: List<CodexOption>,
    current: String,
    fallback: SettingChoice,
): List<SettingChoice> = buildList {
    add(fallback)
    dynamic.forEach { option ->
        if (none { it.value == option.value }) {
            add(
                SettingChoice(
                    option.value,
                    option.label.ifBlank { option.value },
                )
            )
        }
    }
    if (current.isNotBlank() && none { it.value == current }) {
        add(SettingChoice(current, current))
    }
}

private fun localizedAutomaticLabel(
    rawValue: String?,
    language: AppLanguage = AppLanguage.CHINESE,
): String {
    val raw = rawValue.orEmpty().trim()
    if (raw.isEmpty()) return language.text("自动", "Automatic")
    val effective = raw.substringAfter('(', raw).substringBeforeLast(')', raw).trim()
    val localized = when (effective.uppercase()) {
        "AUTO" -> language.text("自动", "Automatic")
        "FAST", "YES", "ON" -> language.text("开启", "On")
        "STANDARD", "NO", "OFF" -> language.text("关闭", "Off")
        else -> effective
    }
    val automatic = language.text("自动", "Automatic")
    return if (localized.isBlank() || localized == automatic) {
        automatic
    } else {
        language.text("自动（$localized）", "Automatic ($localized)")
    }
}
