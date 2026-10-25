package com.parallelcodexrunner.remote.ui

import android.graphics.ImageDecoder
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AddPhotoAlternate
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.FeedbackItem
import com.parallelcodexrunner.remote.data.PendingFeedbackSubmission
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FeedbackSheet(
    state: FeedbackUiState,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var kind by remember(state.relatedDeviceId) { mutableStateOf("bug") }
    var description by remember(state.relatedDeviceId) { mutableStateOf("") }
    var images by remember(state.relatedDeviceId) { mutableStateOf<List<Uri>>(emptyList()) }
    var handledSubmissionId by remember(state.relatedDeviceId) { mutableStateOf<String?>(null) }
    var expandedFeedback by remember(state.relatedDeviceId) { mutableStateOf<Set<String>>(emptySet()) }
    var noteDrafts by remember(state.relatedDeviceId) { mutableStateOf<Map<String, String>>(emptyMap()) }
    var diagnosticsExpanded by remember(state.relatedDeviceId) { mutableStateOf(false) }
    var excludedDiagnosticKeys by remember(state.relatedDeviceId) {
        mutableStateOf<Set<String>>(emptySet())
    }
    val diagnostics = viewModel.feedbackDiagnosticPreview()
    val imagePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(3),
    ) { selected ->
        images = (images + selected).distinct().take(3)
    }

    LaunchedEffect(state.lastQueuedSubmissionId) {
        val queued = state.lastQueuedSubmissionId ?: return@LaunchedEffect
        if (queued != handledSubmissionId) {
            handledSubmissionId = queued
            description = ""
            images = emptyList()
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .heightIn(max = 720.dp),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            appText("反馈与建议", "Feedback"),
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            state.relatedDeviceName.takeIf(String::isNotBlank)?.let {
                                appText("相关设备：$it", "Related computer: $it")
                            } ?: appText("与设备无关", "Not linked to a computer"),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    IconButton(
                        onClick = viewModel::refreshFeedback,
                        enabled = !state.loading,
                    ) {
                        if (state.loading) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(
                                Icons.Default.Refresh,
                                contentDescription = appText("刷新反馈", "Refresh feedback"),
                            )
                        }
                    }
                }
            }

            item {
                Text(
                    appText("告诉我们发生了什么", "Tell us what happened"),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf(
                        "bug" to appText("问题", "Problem"),
                        "feature" to appText("功能需求", "Feature request"),
                        "experience" to appText("体验建议", "Experience"),
                    ).forEach { (value, label) ->
                        FilterChip(
                            selected = kind == value,
                            onClick = { kind = value },
                            label = { Text(label) },
                        )
                    }
                }
            }

            item {
                OutlinedTextField(
                    value = description,
                    onValueChange = { description = it.take(8_000) },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 4,
                    maxLines = 8,
                    label = { Text(appText("描述", "Description")) },
                    placeholder = {
                        Text(appText(
                            "请描述你遇到的问题或希望改进的地方",
                            "Describe the problem or what you would like to improve",
                        ))
                    },
                    supportingText = {
                        Text("${description.length}/8000")
                    },
                )
            }

            if (images.isNotEmpty()) {
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        images.forEachIndexed { index, uri ->
                            FeedbackImagePreview(
                                uri = uri,
                                label = appText(
                                    "截图 ${index + 1}",
                                    "Screenshot ${index + 1}",
                                ),
                                onRemove = { images = images - uri },
                            )
                        }
                    }
                }
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(
                        onClick = {
                            imagePicker.launch(
                                PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                            )
                        },
                        enabled = images.size < 3 && !state.submitting,
                    ) {
                        Icon(Icons.Default.AddPhotoAlternate, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text(if (images.isEmpty()) {
                            appText("添加截图", "Add screenshots")
                        } else {
                            appText("继续添加", "Add more")
                        })
                    }
                    Spacer(Modifier.weight(1f))
                    Button(
                        onClick = {
                            viewModel.submitFeedback(
                                kind,
                                description,
                                images,
                                diagnostics.keys - excludedDiagnosticKeys,
                            )
                        },
                        enabled = description.isNotBlank() && !state.submitting,
                    ) {
                        if (state.submitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null)
                        }
                        Spacer(Modifier.width(6.dp))
                        Text(if (state.submitting) {
                            appText("正在保存", "Saving")
                        } else {
                            appText("提交", "Submit")
                        })
                    }
                }
            }

            item {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { diagnosticsExpanded = !diagnosticsExpanded }
                            .padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            appText("随附信息", "Included information"),
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.labelLarge,
                        )
                        Text(
                            appText(
                                "${(diagnostics.keys - excludedDiagnosticKeys).size} 项",
                                "${(diagnostics.keys - excludedDiagnosticKeys).size} items",
                            ),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Icon(
                            if (diagnosticsExpanded) Icons.Default.ExpandLess
                            else Icons.Default.ExpandMore,
                            contentDescription = if (diagnosticsExpanded) {
                                appText("收起", "Collapse")
                            } else {
                                appText("展开", "Expand")
                            },
                        )
                    }
                    if (diagnosticsExpanded) {
                        diagnostics.forEach { (key, value) ->
                            val included = key !in excludedDiagnosticKeys
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        excludedDiagnosticKeys = if (included) {
                                            excludedDiagnosticKeys + key
                                        } else {
                                            excludedDiagnosticKeys - key
                                        }
                                    },
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Checkbox(
                                    checked = included,
                                    onCheckedChange = { checked ->
                                        excludedDiagnosticKeys = if (checked) {
                                            excludedDiagnosticKeys - key
                                        } else {
                                            excludedDiagnosticKeys + key
                                        }
                                    },
                                )
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        feedbackDiagnosticLabel(key),
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                    Text(
                                        value,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                    }
                    Text(
                        appText(
                            "不会上传提问、对话、源码、密钥或完整本地路径。",
                            "Prompts, conversations, source code, secrets, and full local paths are never uploaded.",
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            state.error?.takeIf(String::isNotBlank)?.let { message ->
                item {
                    Text(
                        message,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }

            if (state.pending.isNotEmpty()) {
                item {
                    FeedbackSectionHeader(appText("等待发送", "Waiting to send"))
                }
                items(state.pending, key = PendingFeedbackSubmission::id) { pending ->
                    PendingFeedbackRow(pending, viewModel)
                    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
                }
            }

            item {
                FeedbackSectionHeader(appText("我的反馈", "My feedback"))
            }
            if (!state.loading && state.items.isEmpty()) {
                item {
                    Text(
                        appText("还没有提交过反馈。", "No feedback has been submitted yet."),
                        modifier = Modifier.padding(vertical = 8.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            items(state.items, key = FeedbackItem::feedbackId) { feedback ->
                val expanded = feedback.feedbackId in expandedFeedback
                FeedbackHistoryRow(
                    feedback = feedback,
                    expanded = expanded,
                    note = noteDrafts[feedback.feedbackId].orEmpty(),
                    actioning = state.actioningId == feedback.feedbackId,
                    onToggle = {
                        expandedFeedback = if (expanded) {
                            expandedFeedback - feedback.feedbackId
                        } else {
                            expandedFeedback + feedback.feedbackId
                        }
                    },
                    onNoteChange = { value ->
                        noteDrafts = noteDrafts + (feedback.feedbackId to value.take(4_000))
                    },
                    onAddNote = {
                        val note = noteDrafts[feedback.feedbackId].orEmpty()
                        if (note.isNotBlank()) {
                            viewModel.addFeedbackNote(feedback.feedbackId, note) {
                                noteDrafts = noteDrafts - feedback.feedbackId
                            }
                        }
                    },
                    onWithdraw = { viewModel.withdrawFeedback(feedback.feedbackId) },
                )
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f))
            }
            if (state.hasMore) {
                item {
                    TextButton(
                        onClick = viewModel::loadMoreFeedback,
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !state.loadingMore,
                    ) {
                        if (state.loadingMore) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                            )
                            Spacer(Modifier.width(7.dp))
                        }
                        Text(appText("加载更早反馈", "Load older feedback"))
                    }
                }
            }
        }
    }
}

@Composable
private fun FeedbackImagePreview(
    uri: Uri,
    label: String,
    onRemove: () -> Unit,
) {
    val context = LocalContext.current
    val preview by produceState<ImageBitmap?>(initialValue = null, uri) {
        value = withContext(Dispatchers.IO) {
            runCatching {
                val source = ImageDecoder.createSource(context.contentResolver, uri)
                ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                    val longest = maxOf(info.size.width, info.size.height)
                    if (longest > 320) {
                        val scale = 320f / longest.toFloat()
                        decoder.setTargetSize(
                            (info.size.width * scale).toInt().coerceAtLeast(1),
                            (info.size.height * scale).toInt().coerceAtLeast(1),
                        )
                    }
                }.asImageBitmap()
            }.getOrNull()
        }
    }
    Box(
        modifier = Modifier
            .size(84.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        preview?.let { bitmap ->
            Image(
                bitmap = bitmap,
                contentDescription = label,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        }
        Surface(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(4.dp),
            shape = CircleShape,
            color = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f),
        ) {
            IconButton(
                onClick = onRemove,
                modifier = Modifier.size(28.dp),
            ) {
                Icon(
                    Icons.Default.Close,
                    contentDescription = appText("移除$label", "Remove $label"),
                    modifier = Modifier.size(17.dp),
                )
            }
        }
    }
}

@Composable
private fun FeedbackSectionHeader(title: String) {
    Text(
        title,
        modifier = Modifier.padding(top = 8.dp),
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
    )
}

@Composable
private fun PendingFeedbackRow(
    pending: PendingFeedbackSubmission,
    viewModel: MainViewModel,
) {
    val language = LocalAppLanguage.current
    val failed = pending.status == PendingFeedbackSubmission.STATUS_FAILED
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                feedbackKindLabel(pending.kind),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                when (pending.status) {
                    PendingFeedbackSubmission.STATUS_FAILED -> appText("提交失败", "Failed")
                    PendingFeedbackSubmission.STATUS_RETRYING -> appText(
                        "正在重试",
                        "Retrying",
                    )
                    else -> appText("等待网络", "Waiting for network")
                },
                style = MaterialTheme.typography.labelMedium,
                color = if (failed) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            pending.description,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
        )
        if (pending.attachments.isNotEmpty()) {
            Text(
                appText(
                    "${pending.attachments.size} 张截图已安全保存在本机",
                    "${pending.attachments.size} screenshots are safely stored on this device",
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        pending.error.takeIf(String::isNotBlank)?.let { error ->
            Text(
                friendlyStoredError(error, language),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        Row(modifier = Modifier.align(Alignment.End)) {
            if (failed) {
                TextButton(onClick = { viewModel.retryFeedback(pending.id) }) {
                    Text(appText("重试", "Retry"))
                }
            }
            TextButton(onClick = { viewModel.removePendingFeedback(pending.id) }) {
                Text(appText("删除", "Delete"))
            }
        }
    }
}

@Composable
private fun FeedbackHistoryRow(
    feedback: FeedbackItem,
    expanded: Boolean,
    note: String,
    actioning: Boolean,
    onToggle: () -> Unit,
    onNoteChange: (String) -> Unit,
    onAddNote: () -> Unit,
    onWithdraw: () -> Unit,
) {
    val language = LocalAppLanguage.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle)
            .padding(vertical = 11.dp),
        verticalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    feedback.description.lineSequence().firstOrNull().orEmpty(),
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    "${feedbackKindLabel(feedback.kind)} · " +
                        formatFeedbackDate(feedback.updatedAt, language),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                feedbackStatusLabel(feedback.status),
                style = MaterialTheme.typography.labelMedium,
                color = when (feedback.status) {
                    "fixed" -> MaterialTheme.colorScheme.primary
                    "declined", "withdrawn" -> MaterialTheme.colorScheme.onSurfaceVariant
                    else -> MaterialTheme.colorScheme.tertiary
                },
            )
            Spacer(Modifier.width(5.dp))
            Icon(
                if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = if (expanded) {
                    appText("收起", "Collapse")
                } else {
                    appText("展开", "Expand")
                },
                modifier = Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (expanded) {
            Text(feedback.description)
            Text(
                appText(
                    "反馈编号 ${feedback.feedbackId.removePrefix("feedback-").take(8).uppercase()}",
                    "Feedback ${feedback.feedbackId.removePrefix("feedback-").take(8).uppercase()}",
                ),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (feedback.attachments.isNotEmpty()) {
                Text(
                    appText(
                        "已附带 ${feedback.attachments.size} 张截图",
                        "${feedback.attachments.size} screenshots attached",
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            feedback.developerReply.takeIf(String::isNotBlank)?.let { reply ->
                Surface(
                    color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.55f),
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            appText("开发回复", "Developer reply"),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(reply, modifier = Modifier.padding(top = 3.dp))
                        feedback.fixedVersion.takeIf(String::isNotBlank)?.let { version ->
                            Text(
                                appText("已在 $version 修复", "Fixed in $version"),
                                modifier = Modifier.padding(top = 6.dp),
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                    }
                }
            }
            feedback.notes.forEach { feedbackNote ->
                Text(
                    appText(
                        "补充：${feedbackNote.text}",
                        "Additional note: ${feedbackNote.text}",
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            val statusHistory = feedback.activity.filter { it.eventType == "status" }
            if (statusHistory.isNotEmpty()) {
                Text(
                    appText("处理记录", "Status history"),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                statusHistory.forEach { activity ->
                    Text(
                        "${feedbackStatusLabel(activity.status)} · " +
                            formatFeedbackDate(activity.createdAt, language),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (feedback.status !in setOf("withdrawn", "declined", "fixed")) {
                OutlinedTextField(
                    value = note,
                    onValueChange = onNoteChange,
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                    maxLines = 5,
                    label = { Text(appText("补充信息", "Additional information")) },
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (feedback.status == "submitted") {
                        TextButton(onClick = onWithdraw, enabled = !actioning) {
                            Text(appText("撤回", "Withdraw"))
                        }
                    }
                    TextButton(
                        onClick = onAddNote,
                        enabled = note.isNotBlank() && !actioning,
                    ) {
                        if (actioning) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                            )
                            Spacer(Modifier.width(6.dp))
                        }
                        Text(appText("补充", "Add note"))
                    }
                }
            }
        }
    }
}

@Composable
private fun feedbackKindLabel(value: String): String = when (value) {
    "feature" -> appText("功能需求", "Feature request")
    "experience" -> appText("体验建议", "Experience")
    else -> appText("问题", "Problem")
}

@Composable
private fun feedbackStatusLabel(value: String): String = when (value) {
    "confirmed" -> appText("已确认", "Confirmed")
    "in_progress" -> appText("修复中", "In progress")
    "fixed" -> appText("已修复", "Fixed")
    "declined" -> appText("不处理", "Declined")
    "withdrawn" -> appText("已撤回", "Withdrawn")
    else -> appText("已提交", "Submitted")
}

private fun formatFeedbackDate(value: String, language: AppLanguage): String = runCatching {
    DateTimeFormatter.ofPattern(language.text("M月d日 HH:mm", "MMM d, HH:mm"))
        .format(Instant.parse(value).atZone(ZoneId.systemDefault()))
}.getOrDefault(language.text("刚刚", "Just now"))

@Composable
private fun feedbackDiagnosticLabel(value: String): String = when (value) {
    "app_platform" -> appText("应用平台", "App platform")
    "app_version" -> appText("应用版本", "App version")
    "app_build" -> appText("构建版本", "Build")
    "os_version" -> appText("系统版本", "System version")
    "device_model" -> appText("手机型号", "Phone model")
    "mac_device_id" -> appText("设备标识", "Computer ID")
    "mac_name" -> appText("设备名称", "Computer name")
    "workspace_id" -> appText("工作区标识", "Workspace ID")
    "workspace_name" -> appText("工作区名称", "Workspace name")
    "run_id" -> appText("任务标识", "Task ID")
    "agent" -> "Agent"
    "error_code" -> appText("错误代码", "Error code")
    "error_summary" -> appText("错误摘要", "Error summary")
    else -> value.replace('_', ' ')
}
