package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
internal fun ConversationReadNotice(
    issue: ConversationReadIssue,
    hasContent: Boolean,
    onRetry: () -> Unit,
    onOpenHistory: () -> Unit,
) {
    if (hasContent) {
        Row(
            modifier = Modifier.fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceContainerLow)
                .padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(Icons.Outlined.Info, null, Modifier.size(18.dp), MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                issue.displayMessage(LocalAppLanguage.current),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            RetryConversationButton(onRetry)
        }
    } else {
        Column(
            modifier = Modifier.widthIn(max = 440.dp).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(Icons.Outlined.Info, null, Modifier.size(28.dp), MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                appText("暂时无法加载对话", "Conversation unavailable"),
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                issue.displayMessage(LocalAppLanguage.current),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                RetryConversationButton(onRetry)
                TextButton(onClick = onOpenHistory) {
                    Text(appText("其他对话", "Other conversations"))
                }
            }
        }
    }
}

@Composable
private fun RetryConversationButton(onRetry: () -> Unit) {
    TextButton(onClick = onRetry) {
        Icon(Icons.Outlined.Refresh, null, Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(appText("重新加载", "Reload"))
    }
}
