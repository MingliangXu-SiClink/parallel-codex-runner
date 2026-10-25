package com.parallelcodexrunner.remote.ui

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FormatSize
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.NotificationsOff
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.RateReview
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.UnfoldMore
import androidx.compose.material.icons.filled.Language
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.data.AndroidUpdateInfo
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RunStorageEntry
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
internal fun SettingsPane(
    state: MainUiState,
    viewModel: MainViewModel,
    scanPairing: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val feedback by viewModel.feedbackState.collectAsState()
    LazyColumn(
        modifier = modifier.fillMaxHeight(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 18.dp),
    ) {
        item {
            SettingsSectionTitle(appText("设备", "Computers"))
            SettingsGroup {
                if (state.macs.isEmpty()) {
                    SettingsInfoRow(
                        icon = Icons.Default.Computer,
                        title = appText("尚未连接设备", "No computers connected"),
                        detail = "",
                    )
                } else {
                    state.macs.forEachIndexed { index, mac ->
                        MacSettingsRow(
                            mac = mac,
                            selected = mac.deviceId == state.selectedMacId,
                            loading = mac.deviceId == state.macSelectionId,
                            enabled = state.macSelectionId == null,
                            onSelect = { viewModel.selectMac(mac.deviceId) },
                            onSettings = {
                                viewModel.loadDefaultConversationSettings(mac.deviceId)
                            },
                            onStorage = {
                                viewModel.openStorageManagement(mac.deviceId)
                            },
                            onRemove = { viewModel.requestMacRevocation(mac.deviceId) },
                        )
                        if (index < state.macs.lastIndex) SettingsDivider()
                    }
                }
                SettingsDivider()
                SettingsActionRow(
                    icon = Icons.Default.QrCodeScanner,
                    title = appText("连接新设备", "Connect computer"),
                    detail = appText("扫描配对码", "Scan a pairing code"),
                    enabled = !state.busy,
                    onClick = scanPairing,
                )
            }
        }

        item { Spacer(Modifier.height(24.dp)) }

        item {
            SettingsSectionTitle(appText("界面与显示", "Appearance & display"))
            SettingsGroup {
                LanguageSettingsRow(
                    language = state.appLanguage,
                    onLanguage = viewModel::setAppLanguage,
                )
                SettingsDivider()
                ColorModeSettingsRow(
                    mode = state.colorMode,
                    onMode = viewModel::setColorMode,
                )
                SettingsDivider()
                AccentColorSettingsRow(
                    color = state.accentColor,
                    onColor = viewModel::setAccentColor,
                )
                SettingsDivider()
                TextSizeSettingsRow(
                    size = state.textSize,
                    onSize = viewModel::setTextSize,
                )
                SettingsDivider()
                WorkExpansionSettingsRow(
                    expanded = state.workExpandedByDefault,
                    onExpanded = viewModel::setWorkExpandedByDefault,
                )
            }
        }

        item { Spacer(Modifier.height(24.dp)) }

        item {
            SettingsSectionTitle(appText("应用", "App"))
            SettingsGroup {
                NotificationSettingsRow()
                SettingsDivider()
                BackgroundConnectionSettingsRow(
                    enabled = state.backgroundConnectionEnabled,
                    onEnabled = viewModel::setBackgroundConnectionEnabled,
                )
                SettingsDivider()
                AndroidUpdateRow(state, viewModel)
                SettingsDivider()
                val feedbackMac = state.macs.firstOrNull {
                    it.deviceId == state.selectedMacId
                } ?: state.macs.firstOrNull()
                SettingsActionRow(
                    icon = Icons.Default.RateReview,
                    title = appText("反馈与建议", "Feedback"),
                    detail = appText(
                        "提交问题、功能需求或体验建议",
                        "Report a problem or suggest an improvement",
                    ),
                    enabled = true,
                    onClick = {
                        viewModel.openFeedback(feedbackMac?.deviceId)
                    },
                )
            }
        }
    }

    state.defaultSettingsMacId?.let { macId ->
        val mac = state.macs.firstOrNull { it.deviceId == macId }
        GeneralConversationSettingsSheet(
            state = state,
            viewModel = viewModel,
            macName = mac?.name ?: appText("设备", "Computer"),
            onDismiss = viewModel::dismissDefaultConversationSettings,
        )
    }
    if (state.updateHistoryOpen) {
        AndroidUpdateHistorySheet(state, viewModel)
    }
    state.storageMacId?.let { macId ->
        val mac = state.macs.firstOrNull { it.deviceId == macId }
        StorageManagementSheet(
            state = state,
            viewModel = viewModel,
            macName = mac?.name ?: appText("设备", "Computer"),
        )
    }
    if (feedback.relatedDeviceId != null) {
        FeedbackSheet(
            state = feedback,
            viewModel = viewModel,
            onDismiss = viewModel::dismissFeedback,
        )
    }
}

@Composable
private fun ColorModeSettingsRow(
    mode: AppColorMode,
    onMode: (AppColorMode) -> Unit,
) {
    val choices = listOf(
        AppColorMode.SYSTEM to appText("跟随系统", "Use system setting"),
        AppColorMode.LIGHT to appText("浅色", "Light"),
        AppColorMode.DARK to appText("深色", "Dark"),
    )
    ChoiceSettingsRow(
        icon = Icons.Default.DarkMode,
        title = appText("外观", "Appearance"),
        selected = mode,
        selectedLabel = choices.first { it.first == mode }.second,
        choices = choices,
        onSelect = onMode,
    )
}

@Composable
private fun TextSizeSettingsRow(
    size: AppTextSize,
    onSize: (AppTextSize) -> Unit,
) {
    val choices = listOf(
        AppTextSize.SMALL to appText("较小", "Small"),
        AppTextSize.STANDARD to appText("标准", "Standard"),
        AppTextSize.LARGE to appText("较大", "Large"),
    )
    ChoiceSettingsRow(
        icon = Icons.Default.FormatSize,
        title = appText("字体大小", "Text size"),
        selected = size,
        selectedLabel = choices.first { it.first == size }.second,
        choices = choices,
        onSelect = onSize,
    )
}

@Composable
private fun AccentColorSettingsRow(
    color: AppAccentColor,
    onColor: (AppAccentColor) -> Unit,
) {
    val choices = listOf(
        AppAccentColor.BLUE to appText("蓝色", "Blue"),
        AppAccentColor.TEAL to appText("青色", "Teal"),
        AppAccentColor.GREEN to appText("绿色", "Green"),
        AppAccentColor.ORANGE to appText("橙色", "Orange"),
        AppAccentColor.PINK to appText("粉色", "Pink"),
    )
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    var expanded by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { expanded = true }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(Icons.Default.Palette)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(appText("主题色", "Accent color"))
            Text(
                choices.first { it.first == color }.second,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box {
            Box(
                Modifier
                    .size(18.dp)
                    .background(
                        Color(if (dark) color.darkArgb else color.lightArgb),
                        CircleShape,
                    )
            )
            DropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                choices.forEach { (value, label) ->
                    DropdownMenuItem(
                        leadingIcon = {
                            Box(
                                Modifier
                                    .size(16.dp)
                                    .background(
                                        Color(if (dark) value.darkArgb else value.lightArgb),
                                        CircleShape,
                                    )
                            )
                        },
                        text = { Text(label) },
                        trailingIcon = {
                            if (value == color) {
                                Icon(Icons.Default.Check, contentDescription = null)
                            }
                        },
                        onClick = {
                            expanded = false
                            onColor(value)
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun BackgroundConnectionSettingsRow(
    enabled: Boolean,
    onEnabled: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onEnabled(!enabled) }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(Icons.Default.Sync)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(appText("后台保持在线", "Stay connected in background"))
            Text(
                appText(
                    "保持后台连接并显示常驻通知",
                    "Keeps a connection active with a persistent notification",
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(
            checked = enabled,
            onCheckedChange = onEnabled,
        )
    }
}

@Composable
private fun WorkExpansionSettingsRow(
    expanded: Boolean,
    onExpanded: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onExpanded(!expanded) }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(Icons.Default.UnfoldMore)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(appText("默认展开工作过程", "Expand work by default"))
            Text(
                appText(
                    "新工作区会采用此显示方式",
                    "Used when a workspace is opened for the first time",
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = expanded, onCheckedChange = onExpanded)
    }
}

@Composable
private fun <T> ChoiceSettingsRow(
    icon: ImageVector,
    title: String,
    selected: T,
    selectedLabel: String,
    choices: List<Pair<T, String>>,
    onSelect: (T) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { expanded = true }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(icon)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title)
            Text(
                selectedLabel,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box {
            Icon(
                Icons.Default.ChevronRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                choices.forEach { (value, label) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        trailingIcon = {
                            if (value == selected) {
                                Icon(Icons.Default.Check, contentDescription = null)
                            }
                        },
                        onClick = {
                            expanded = false
                            onSelect(value)
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun LanguageSettingsRow(
    language: AppLanguage,
    onLanguage: (AppLanguage) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { expanded = true }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(Icons.Default.Language)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(appText("语言", "Language"))
            Text(
                if (language == AppLanguage.CHINESE) "简体中文" else "English",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box {
            Icon(
                Icons.Default.ChevronRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                DropdownMenuItem(
                    text = { Text("简体中文") },
                    trailingIcon = {
                        if (language == AppLanguage.CHINESE) {
                            Icon(Icons.Default.Check, contentDescription = null)
                        }
                    },
                    onClick = {
                        expanded = false
                        onLanguage(AppLanguage.CHINESE)
                    },
                )
                DropdownMenuItem(
                    text = { Text("English") },
                    trailingIcon = {
                        if (language == AppLanguage.ENGLISH) {
                            Icon(Icons.Default.Check, contentDescription = null)
                        }
                    },
                    onClick = {
                        expanded = false
                        onLanguage(AppLanguage.ENGLISH)
                    },
                )
            }
        }
    }
}

@Composable
private fun NotificationSettingsRow() {
    val context = LocalContext.current
    val manager = remember(context) {
        context.getSystemService(NotificationManager::class.java)
    }
    var enabled by remember(context) { mutableStateOf(manager.areNotificationsEnabled()) }
    val permissionPreferences = remember(context) {
        context.getSharedPreferences("notification-permission", Context.MODE_PRIVATE)
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        enabled = granted && manager.areNotificationsEnabled()
    }

    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        enabled = manager.areNotificationsEnabled()
    }

    SettingsActionRow(
        icon = if (enabled) Icons.Default.Notifications else Icons.Default.NotificationsOff,
        title = appText("任务完成通知", "Completion notifications"),
        detail = if (enabled) {
            appText("所有 Agent 结束后提醒", "Notify when every Agent has finished")
        } else {
            appText("已关闭，点击开启", "Off; tap to enable")
        },
        enabled = true,
    ) {
        val needsRuntimePermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        val requestedBefore = permissionPreferences.getBoolean("requested", false)
        if (needsRuntimePermission && !requestedBefore) {
            permissionPreferences.edit().putBoolean("requested", true).apply()
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            context.startActivity(
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                    putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            )
        }
    }
}

@Composable
private fun MacSettingsRow(
    mac: RelayDevice,
    selected: Boolean,
    loading: Boolean,
    enabled: Boolean,
    onSelect: () -> Unit,
    onSettings: () -> Unit,
    onStorage: () -> Unit,
    onRemove: () -> Unit,
) {
    var menuExpanded by remember(mac.deviceId) { mutableStateOf(false) }
    val online = mac.online
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.07f)
                else MaterialTheme.colorScheme.surface,
            )
            .semantics { this.selected = selected }
            .clickable(enabled = enabled, onClick = onSelect)
            .padding(start = 16.dp, end = 4.dp, top = 11.dp, bottom = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box {
            SettingsIcon(Icons.Default.Computer)
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
                mac.name,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                when {
                    loading -> appText("正在切换设备…", "Switching computer…")
                    online == false && selected -> appText(
                        "当前设备 · 未连接",
                        "Current computer · Offline",
                    )
                    online == false -> appText("已配对 · 未连接", "Paired · Offline")
                    online == true && selected -> appText(
                        "当前设备 · 在线",
                        "Current computer · Online",
                    )
                    online == true -> appText("已配对 · 在线", "Paired · Online")
                    selected -> appText("当前设备", "Current computer")
                    else -> appText("已配对", "Paired")
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier
                    .padding(horizontal = 5.dp)
                    .size(18.dp),
                strokeWidth = 2.dp,
            )
        }
        IconButton(
            onClick = onSettings,
            enabled = enabled,
            modifier = Modifier.size(44.dp),
        ) {
            Icon(
                Icons.Default.Tune,
                contentDescription = appText(
                    "设置 ${mac.name} 的默认对话",
                    "Configure defaults for ${mac.name}",
                ),
                tint = MaterialTheme.colorScheme.primary,
            )
        }
        Box {
            IconButton(
                onClick = { menuExpanded = true },
                enabled = enabled,
                modifier = Modifier.size(44.dp),
            ) {
                Icon(
                    Icons.Default.MoreVert,
                    contentDescription = appText(
                        "${mac.name} 的更多操作",
                        "More actions for ${mac.name}",
                    ),
                )
            }
            DropdownMenu(
                expanded = menuExpanded,
                onDismissRequest = { menuExpanded = false },
            ) {
                DropdownMenuItem(
                    text = { Text(appText("存储空间", "Storage")) },
                    leadingIcon = {
                        Icon(Icons.Default.Storage, contentDescription = null)
                    },
                    onClick = {
                        menuExpanded = false
                        onStorage()
                    },
                    enabled = online != false,
                )
                DropdownMenuItem(
                    text = { Text(appText("移除设备", "Remove computer")) },
                    leadingIcon = {
                        Icon(Icons.Default.Delete, contentDescription = null)
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

@Composable
private fun SettingsActionRow(
    icon: ImageVector,
    title: String,
    detail: String,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(icon)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = MaterialTheme.colorScheme.onSurface)
            if (detail.isNotEmpty()) {
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Icon(
            Icons.Default.ChevronRight,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SettingsInfoRow(icon: ImageVector, title: String, detail: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SettingsIcon(icon)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title)
            if (detail.isNotEmpty()) {
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun AndroidUpdateRow(state: MainUiState, viewModel: MainViewModel) {
    val update = state.availableUpdate
    val detail = when {
        state.updateDownloading -> appText(
            "正在下载 ${state.updateProgress ?: 0}%",
            "Downloading ${state.updateProgress ?: 0}%",
        )
        update != null -> appText("${update.versionName} 可用", "${update.versionName} available")
        state.selectedMacId == null -> appText(
            "连接设备后可检查更新",
            "Connect a computer to check for updates",
        )
        else -> appText(
            "当前版本 ${BuildConfig.VERSION_NAME}",
            "Current version ${BuildConfig.VERSION_NAME}",
        )
    }
    Column(Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(
                    enabled = state.selectedMacId != null,
                    onClick = viewModel::openAndroidUpdateHistory,
                )
                .padding(start = 16.dp, end = 8.dp, top = 11.dp, bottom = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SettingsIcon(Icons.Default.SystemUpdate)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    if (update == null) {
                        appText("应用更新", "App update")
                    } else {
                        "Parallel Codex ${update.versionName}"
                    },
                    fontWeight = if (update == null) FontWeight.Normal else FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (update == null) {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    } else {
                        MaterialTheme.colorScheme.primary
                    },
                )
            }
            if (state.updateChecking || state.updateDownloading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .padding(horizontal = 13.dp)
                        .size(20.dp),
                    strokeWidth = 2.dp,
                )
            } else {
                TextButton(
                    onClick = if (update == null) {
                        viewModel::checkForAndroidUpdate
                    } else {
                        viewModel::installAndroidUpdate
                    },
                    enabled = state.selectedMacId != null,
                ) {
                    Text(
                        if (update == null) appText("检查", "Check")
                        else appText("更新", "Update")
                    )
                }
            }
        }
        update?.releaseNotes?.takeIf(String::isNotBlank)?.let { notes ->
            Text(
                notes,
                modifier = Modifier.padding(start = 60.dp, end = 16.dp, bottom = 11.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (state.updateDownloading) {
            LinearProgressIndicator(
                progress = { (state.updateProgress ?: 0) / 100f },
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StorageManagementSheet(
    state: MainUiState,
    viewModel: MainViewModel,
    macName: String,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var pendingCleanup by remember { mutableStateOf<RunStorageEntry?>(null) }
    val summary = state.storageSummary
    ModalBottomSheet(
        onDismissRequest = viewModel::dismissStorageManagement,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        appText("存储空间", "Storage"),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        macName,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                IconButton(
                    onClick = viewModel::refreshStorageManagement,
                    enabled = !state.storageLoading && state.storageCleaningRunId == null,
                ) {
                    Icon(
                        Icons.Default.Refresh,
                        contentDescription = appText("重新统计", "Recalculate"),
                    )
                }
            }

            if (state.storageLoading && summary == null) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(180.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                }
            } else {
                summary?.let { value ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 18.dp, bottom = 14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        StorageMetric(
                            appText("运行文件", "Run files"),
                            value.managed,
                            Modifier.weight(1f),
                        )
                        StorageMetric(
                            appText("可清理", "Reclaimable"),
                            value.reclaimable,
                            Modifier.weight(1f),
                        )
                        StorageMetric(
                            if (value.volumeCount > 1) {
                                appText("最低可用", "Lowest free")
                            } else {
                                appText("磁盘可用", "Disk free")
                            },
                            value.free,
                            Modifier.weight(1f),
                        )
                    }
                    if (value.volumeCount > 1) {
                        Text(
                            appText(
                                "运行文件分布在 ${value.volumeCount} 个磁盘，显示空间最少的一个。",
                                "Run files span ${value.volumeCount} disks. The disk with the least free space is shown.",
                            ),
                            modifier = Modifier.padding(bottom = 10.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (value.unmanagedCount > 0) {
                        Text(
                            appText(
                                "另有 ${value.unmanagedCount} 个命令行运行占用约 ${value.unmanaged}。" +
                                    "为避免误删，手机端只清理由 Parallel Codex 创建的运行。",
                                "${value.unmanagedCount} command-line runs use about ${value.unmanaged}. " +
                                    "To avoid deleting the wrong data, this app only cleans runs it created.",
                            ),
                            modifier = Modifier.padding(bottom = 14.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                state.storageError?.takeIf(String::isNotBlank)?.let { error ->
                    Text(
                        error,
                        modifier = Modifier.padding(vertical = 10.dp),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                Text(
                    appText("可清理的运行", "Runs that can be cleaned"),
                    modifier = Modifier.padding(top = 6.dp, bottom = 6.dp),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                val cleanable = summary?.runs.orEmpty().filter { it.cleanupAction != null }
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 500.dp),
                    contentPadding = PaddingValues(bottom = 24.dp),
                ) {
                    if (cleanable.isEmpty() && !state.storageLoading) {
                        item {
                            Text(
                                appText(
                                    "目前没有可清理的应用运行。",
                                    "There are no app runs to clean.",
                                ),
                                modifier = Modifier.padding(vertical = 24.dp),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    items(cleanable, key = RunStorageEntry::runId) { entry ->
                        StorageRunRow(
                            entry = entry,
                            cleaning = state.storageCleaningRunId == entry.runId,
                            enabled = state.storageCleaningRunId == null,
                            onCleanup = { pendingCleanup = entry },
                        )
                        HorizontalDivider(
                            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f)
                        )
                    }
                }
            }
        }
    }

    pendingCleanup?.let { entry ->
        val discarding = entry.cleanupAction == "discard"
        AlertDialog(
            onDismissRequest = { pendingCleanup = null },
            title = {
                Text(if (discarding) {
                    appText("放弃并清理运行？", "Discard and clean this run?")
                } else {
                    appText("清理运行副本？", "Clean this run copy?")
                })
            },
            text = {
                Text(
                    if (discarding) {
                        appText(
                            "${entry.workspaceName} 的 Agent 结果尚未采用。清理后不能再选择这些结果。",
                            "The Agent results for ${entry.workspaceName} have not been accepted. They cannot be selected after cleanup.",
                        )
                    } else {
                        appText(
                            "将删除 ${entry.workspaceName} 保留的 Agent 工作区和 Codex 临时数据，" +
                                "已采用的工作区结果不会改变。",
                            "This deletes retained Agent workspaces and temporary Codex data for ${entry.workspaceName}. Accepted workspace changes are not affected.",
                        )
                    }
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        pendingCleanup = null
                        viewModel.cleanupRunStorage(entry)
                    },
                ) {
                    Text(if (discarding) {
                        appText("放弃并清理", "Discard and clean")
                    } else {
                        appText("清理", "Clean")
                    })
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingCleanup = null }) {
                    Text(appText("取消", "Cancel"))
                }
            },
        )
    }
}

@Composable
private fun StorageMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            value,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun StorageRunRow(
    entry: RunStorageEntry,
    cleaning: Boolean,
    enabled: Boolean,
    onCleanup: () -> Unit,
) {
    val language = LocalAppLanguage.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                entry.workspaceName.ifBlank {
                    appText("未命名工作区", "Untitled workspace")
                },
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                buildList {
                    add(storageRunStatus(entry.status, language))
                    formatStorageDate(entry.updatedAt, language)?.let(::add)
                    add(language.text(
                        "可释放约 ${entry.reclaimable}",
                        "About ${entry.reclaimable} reclaimable",
                    ))
                }.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (cleaning) {
            CircularProgressIndicator(
                modifier = Modifier
                    .padding(horizontal = 14.dp)
                    .size(20.dp),
                strokeWidth = 2.dp,
            )
        } else {
            TextButton(onClick = onCleanup, enabled = enabled) {
                Text(if (entry.cleanupAction == "discard") {
                    appText("放弃并清理", "Discard and clean")
                } else {
                    appText("清理", "Clean")
                })
            }
        }
    }
}

private fun storageRunStatus(value: String, language: AppLanguage): String =
    when (value.lowercase()) {
        "finalized" -> language.text("已采用", "Accepted")
        "discarded" -> language.text("已放弃", "Discarded")
        "expired" -> language.text("已过期", "Expired")
        "completed" -> language.text("待选择", "Awaiting selection")
        "failed" -> language.text("运行失败", "Failed")
        "interrupted" -> language.text("运行中断", "Interrupted")
        else -> language.text("已结束", "Finished")
    }

private fun formatStorageDate(value: String, language: AppLanguage): String? = runCatching {
    DateTimeFormatter.ofPattern(language.text("M月d日 HH:mm", "MMM d, HH:mm"))
        .format(Instant.parse(value).atZone(ZoneId.systemDefault()))
}.getOrNull()

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AndroidUpdateHistorySheet(state: MainUiState, viewModel: MainViewModel) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = viewModel::dismissAndroidUpdateHistory,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp),
        ) {
            Text(
                appText("更新记录", "Update history"),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                appText(
                    "当前版本 ${BuildConfig.VERSION_NAME}",
                    "Current version ${BuildConfig.VERSION_NAME}",
                ),
                modifier = Modifier.padding(top = 3.dp, bottom = 14.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            state.updateHistoryError?.let { error ->
                Text(
                    error,
                    modifier = Modifier.padding(bottom = 10.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 580.dp),
                contentPadding = PaddingValues(bottom = 20.dp),
            ) {
                if (state.updateHistory.isEmpty() && !state.updateHistoryLoading) {
                    item {
                        Text(
                            appText("暂无更新记录", "No update history"),
                            modifier = Modifier.padding(vertical = 28.dp),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                items(state.updateHistory, key = AndroidUpdateInfo::releaseId) { release ->
                    AndroidUpdateHistoryRow(release, state.availableUpdate)
                    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
                }
                if (state.updateHistoryLoading) {
                    item {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(22.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            CircularProgressIndicator(modifier = Modifier.size(22.dp), strokeWidth = 2.dp)
                        }
                    }
                } else if (state.updateHistoryHasMore) {
                    item {
                        TextButton(
                            onClick = viewModel::loadMoreAndroidUpdateHistory,
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(appText("加载更早版本", "Load older versions")) }
                    }
                }
            }
        }
    }
}

@Composable
private fun AndroidUpdateHistoryRow(
    release: AndroidUpdateInfo,
    availableUpdate: AndroidUpdateInfo?,
) {
    val marker = when {
        !release.available -> appText("已停止提供", "Unavailable")
        release.versionCode == BuildConfig.VERSION_CODE.toLong() ->
            appText("当前版本", "Current")
        release.releaseId == availableUpdate?.releaseId -> appText("可更新", "Available")
        else -> null
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Parallel Codex ${release.versionName}",
                modifier = Modifier.weight(1f),
                fontWeight = FontWeight.SemiBold,
            )
            marker?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.labelMedium,
                color = if (release.available) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        formatUpdateDate(release.publishedAt, LocalAppLanguage.current)?.let { date ->
            Text(
                date,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            localizedReleaseNotes(release.releaseNotes, LocalAppLanguage.current),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun localizedReleaseNotes(value: String, language: AppLanguage): String =
    value.trim().ifBlank {
        language.text(
            "本版本没有更新说明。",
            "No release notes are available for this version.",
        )
    }

private fun formatUpdateDate(value: String, language: AppLanguage): String? = runCatching {
    DateTimeFormatter.ofPattern(language.text("yyyy年M月d日", "MMM d, yyyy"))
        .format(Instant.parse(value).atZone(ZoneId.systemDefault()))
}.getOrNull()

@Composable
private fun SettingsIcon(icon: ImageVector) {
    Box(
        modifier = Modifier
            .size(34.dp)
            .background(
                MaterialTheme.colorScheme.primary.copy(alpha = 0.10f),
                RoundedCornerShape(8.dp),
            ),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(19.dp),
        )
    }
}

@Composable
private fun SettingsSectionTitle(title: String) {
    Text(
        title,
        modifier = Modifier.padding(start = 2.dp, bottom = 9.dp),
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
    )
}

@Composable
private fun SettingsGroup(content: @Composable () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface,
    ) {
        Column { content() }
    }
}

@Composable
private fun SettingsDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(start = 62.dp),
        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f),
    )
}
