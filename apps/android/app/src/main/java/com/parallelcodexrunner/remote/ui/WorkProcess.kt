package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.generated.*
import kotlinx.coroutines.CancellationException
import java.util.Locale

internal object WorkOperationPresentation {
    fun displayPath(path: String): String = if (path.startsWith("/"))
        path.split('/').takeLast(3).joinToString("/") else path

    fun summary(operation: WorkOperationRecord, language: AppLanguage): String {
        val running = operation.status == WorkOperationStatus.RUNNING
        val target = operation.targets.singleOrNull()?.path?.let(::displayPath)
            ?: language.text("${operation.targets.size} 个文件", "${operation.targets.size} files")
        val (verb, objectText) = when (operation.kind) {
            WorkOperationKind.FILE_READ -> language.text(if (running) "正在读取" else "已读取", if (running) "Reading" else "Read") to target
            WorkOperationKind.FILE_SEARCH -> language.text(if (running) "正在搜索" else "已搜索", if (running) "Searching" else "Searched") to "$target · ${operation.query.orEmpty()}"
            WorkOperationKind.DIRECTORY_LIST -> language.text(if (running) "正在查看" else "已查看", if (running) "Listing" else "Listed") to target
            WorkOperationKind.FILE_CHANGE -> {
                val action = when (operation.targets.singleOrNull()?.change) {
                    WorkFileChangeKind.CREATE -> language.text(if (running) "正在创建" else "已创建", if (running) "Creating" else "Created")
                    WorkFileChangeKind.DELETE -> language.text(if (running) "正在删除" else "已删除", if (running) "Deleting" else "Deleted")
                    WorkFileChangeKind.RENAME -> language.text(if (running) "正在重命名" else "已重命名", if (running) "Renaming" else "Renamed")
                    else -> language.text(if (running) "正在修改" else "已修改", if (running) "Editing" else "Edited")
                }
                action to target
            }
            WorkOperationKind.WEB_SEARCH -> language.text(if (running) "正在搜索网页" else "已搜索网页", if (running) "Searching the web" else "Searched the web") to operation.query.orEmpty()
            else -> language.text(if (running) "正在运行" else "已运行", if (running) "Running" else "Ran") to MobileTranscriptFormatter.stripShellWrapper(operation.command.orEmpty())
        }
        val prefix = when (operation.status) {
            WorkOperationStatus.FAILED -> when (operation.kind) {
                WorkOperationKind.FILE_READ -> language.text("读取失败", "Read failed")
                WorkOperationKind.FILE_SEARCH, WorkOperationKind.WEB_SEARCH -> language.text("搜索失败", "Search failed")
                WorkOperationKind.DIRECTORY_LIST -> language.text("查看目录失败", "Listing failed")
                WorkOperationKind.FILE_CHANGE -> language.text("文件修改失败", "File change failed")
                else -> language.text("运行失败", "Command failed")
            }
            WorkOperationStatus.CANCELLED -> language.text("已取消", "Cancelled")
            WorkOperationStatus.UNKNOWN -> language.text("状态待确认", "Status unknown")
            else -> verb
        }
        return buildString {
            append("$prefix ${objectText.replace('\n', ' ')}")
            operation.durationMs?.takeIf { !running }?.let {
                val seconds = if (it < 1000) "<1" else String.format(Locale.ROOT, "%.1f", it / 1000.0)
                append(language.text(" · $seconds 秒", " · $seconds s"))
            }
            if (operation.kind == WorkOperationKind.FILE_CHANGE && operation.targets.isNotEmpty() &&
                operation.targets.all { it.added != null && it.deleted != null }) {
                append(" · +${operation.targets.sumOf { it.added ?: 0 }} -${operation.targets.sumOf { it.deleted ?: 0 }}")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WorkOperationRow(
    operation: WorkOperationRecord,
    onOpenLink: (String) -> Unit,
    onLoad: suspend (WorkOperationRecord, Int) -> WorkOperationDetailPage,
) {
    var expanded by rememberSaveable(operation.id) { mutableStateOf(false) }
    val language = LocalAppLanguage.current
    val summary = WorkOperationPresentation.summary(operation, language)
    val color = when (operation.status) {
        WorkOperationStatus.FAILED -> MaterialTheme.colorScheme.error
        WorkOperationStatus.RUNNING -> MaterialTheme.colorScheme.onSurface
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(enabled = operation.detailsAvailable) { expanded = true }.padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(when (operation.kind) {
            WorkOperationKind.FILE_READ -> Icons.Default.Description
            WorkOperationKind.FILE_SEARCH, WorkOperationKind.WEB_SEARCH -> Icons.Default.Search
            WorkOperationKind.DIRECTORY_LIST -> Icons.Default.Folder
            WorkOperationKind.FILE_CHANGE -> Icons.Default.Edit
            else -> Icons.Default.Terminal
        }, contentDescription = null, tint = color, modifier = Modifier.size(26.dp).padding(4.dp))
        Spacer(Modifier.width(9.dp))
        Text(summary, color = color, style = MaterialTheme.typography.bodyMedium,
            maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (operation.detailsAvailable) Icon(Icons.Default.ChevronRight, contentDescription = appText("查看详情", "View details"),
            tint = color, modifier = Modifier.size(18.dp))
    }
    if (!expanded) return
    ModalBottomSheet(onDismissRequest = { expanded = false }) {
        var page by remember(operation.id, operation.eventId) { mutableStateOf<WorkOperationDetailPage?>(null) }
        var error by remember(operation.id, operation.eventId) { mutableStateOf<String?>(null) }
        var cursor by remember(operation.id, operation.eventId) { mutableIntStateOf(0) }
        var retry by remember { mutableIntStateOf(0) }
        var loading by remember { mutableStateOf(false) }
        LaunchedEffect(operation.id, operation.eventId, cursor, retry) {
            if (!operation.detailsAvailable) return@LaunchedEffect
            loading = true
            error = null
            try {
                val next = onLoad(operation, cursor)
                check(next.availability != "removed") { language.text("操作记录无法读取。", "Unable to read the operation record.") }
                page = next.copy(text = (if (cursor > 0) page?.text.orEmpty() else "") + next.text)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                error = failure.localizedMessage
            } finally { loading = false }
        }
        Column(Modifier.fillMaxWidth().fillMaxHeight(0.8f).verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(summary, style = MaterialTheme.typography.titleSmall, color = color)
            operation.command?.let {
                SelectionContainer { Text(it, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
            }
            operation.targets.forEach { target ->
                if (operation.kind == WorkOperationKind.FILE_READ || operation.kind == WorkOperationKind.FILE_CHANGE) {
                    TextButton(onClick = { expanded = false; onOpenLink(target.path) }) {
                        Icon(Icons.Default.Description, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(appText("当前文件：${target.path}", "Current file: ${target.path}"))
                    }
                } else Text(target.path, style = MaterialTheme.typography.bodySmall)
            }
            operation.exitCode?.let { Text(appText("退出码：$it", "Exit code: $it"), style = MaterialTheme.typography.labelSmall) }
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (page?.text?.isNotEmpty() == true) {
                SelectionContainer { Text(page!!.text, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
            }
            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error)
                TextButton(onClick = { retry++ }) { Text(appText("重试", "Retry")) }
            }
            if (page?.hasMore == true) TextButton(enabled = !loading, onClick = { cursor = page!!.nextCursor }) {
                Text(appText("加载更多输出", "Load more output"))
            }
        }
    }
}
