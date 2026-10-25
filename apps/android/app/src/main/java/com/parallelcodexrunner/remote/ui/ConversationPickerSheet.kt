package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipAnchorPosition
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ConversationPickerSheet(
    state: MainUiState,
    workspaceName: String?,
    onDismiss: () -> Unit,
    onRefresh: () -> Unit,
    onNewConversation: () -> Unit,
    onSelect: (String) -> Unit,
) {
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
                .fillMaxHeight(0.86f)
                .navigationBarsPadding(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 20.dp, top = 8.dp, end = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        appText("对话", "Conversations"),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    workspaceName?.takeIf(String::isNotBlank)?.let { name ->
                        Text(
                            name,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                TooltipBox(
                    positionProvider = TooltipDefaults.rememberTooltipPositionProvider(TooltipAnchorPosition.Above),
                    tooltip = { PlainTooltip { Text(appText("新对话", "New conversation")) } },
                    state = rememberTooltipState(),
                ) {
                    IconButton(
                        onClick = onNewConversation,
                        enabled = !state.runActionInProgress && state.pendingSubmission == null,
                    ) {
                        Icon(
                            Icons.Outlined.AddComment,
                            contentDescription = appText("新对话", "New conversation"),
                        )
                    }
                }
                IconButton(
                    onClick = onRefresh,
                    enabled = !state.conversationListLoading &&
                        state.conversationOpeningId == null,
                ) {
                    Icon(Icons.Default.Refresh, contentDescription = appText("刷新", "Refresh"))
                }
                TextButton(onClick = onDismiss) { Text(appText("完成", "Done")) }
            }
            if (state.conversationListLoading) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            } else {
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f))
            }
            state.conversationListError?.takeIf(String::isNotBlank)?.let { error ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 20.dp, end = 8.dp, top = 6.dp, bottom = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        error,
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    TextButton(onClick = onRefresh) { Text(appText("重试", "Retry")) }
                }
            }
            if (state.workspaceConversations.isEmpty() && !state.conversationListLoading) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        appText("这个工作区还没有对话", "No conversations in this workspace yet"),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                LazyColumn(Modifier.weight(1f)) {
                    items(
                        items = state.workspaceConversations,
                        key = ConversationRecord::conversationId,
                    ) { conversation ->
                        ConversationPickerRow(
                            conversation = conversation,
                            selected = conversation.conversationId == state.visibleConversationId,
                            opening = conversation.conversationId == state.conversationOpeningId,
                            enabled = canOpenWorkspaceConversation(
                                visibleConversationId = state.visibleConversationId,
                                openingConversationId = state.conversationOpeningId,
                                requestedConversationId = conversation.conversationId,
                            ),
                            onClick = { onSelect(conversation.conversationId) },
                        )
                        HorizontalDivider(
                            modifier = Modifier.padding(start = 62.dp),
                            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.38f),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ConversationPickerRow(
    conversation: ConversationRecord,
    selected: Boolean,
    opening: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                if (selected) MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.34f)
                else MaterialTheme.colorScheme.surface,
            )
            .clickable(enabled = enabled && !selected, onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.ChatBubble,
            contentDescription = null,
            tint = if (selected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(22.dp),
        )
        Spacer(Modifier.width(20.dp))
        Column(Modifier.weight(1f)) {
            Text(
                conversation.title.trim().ifBlank {
                    appText("未命名对话", "Untitled conversation")
                },
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                buildList {
                    formatConversationDate(conversation.updatedAt, LocalAppLanguage.current)?.let(::add)
                    conversation.turnIds.size.takeIf { it > 0 }?.let { count ->
                        add(appText("$count 轮", "$count turns"))
                    }
                    if (selected) add(appText("当前", "Current"))
                }.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        when {
            opening -> CircularProgressIndicator(
                modifier = Modifier.size(20.dp),
                strokeWidth = 2.dp,
            )
            selected -> Icon(
                Icons.Default.Check,
                contentDescription = appText("当前对话", "Current conversation"),
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(21.dp),
            )
        }
    }
}

internal fun formatConversationDate(value: String, language: AppLanguage): String? {
    val instant = runCatching { Instant.parse(value) }.getOrNull() ?: return null
    val zone = ZoneId.systemDefault()
    val dateTime = instant.atZone(zone)
    val today = LocalDate.now(zone)
    return when {
        dateTime.toLocalDate() == today -> DateTimeFormatter.ofPattern("HH:mm")
            .format(dateTime)
        dateTime.year == today.year -> DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM)
            .withLocale(if (language == AppLanguage.CHINESE) Locale.SIMPLIFIED_CHINESE else Locale.ENGLISH)
            .format(dateTime)
        else -> DateTimeFormatter.ofPattern("yyyy/M/d").format(dateTime)
    }
}
