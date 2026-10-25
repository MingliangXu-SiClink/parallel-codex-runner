package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.parallelcodexrunner.remote.data.DirectoryEntry
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun DirectoryBrowser(state: MainUiState, viewModel: MainViewModel) {
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = {
                    Text(appText("选择工作区", "Choose workspace"), fontWeight = FontWeight.SemiBold)
                },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeBrowser) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = appText("返回", "Back"),
                        )
                    }
                },
                actions = {
                    IconButton(
                        onClick = viewModel::requestFolderCreation,
                        enabled = state.directory?.writable == true &&
                            !state.directoryLoading &&
                            !state.folderCreating,
                    ) {
                        Icon(
                            Icons.Default.CreateNewFolder,
                            contentDescription = appText(
                                "在目标设备的当前路径新建文件夹",
                                "Create a folder in the current location",
                            ),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        bottomBar = {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                border = BorderStroke(
                    0.5.dp,
                    MaterialTheme.colorScheme.outline.copy(alpha = 0.45f),
                ),
            ) {
                Button(
                    onClick = viewModel::addCurrentDirectory,
                    enabled = state.directory?.readable == true &&
                        !state.busy &&
                        !state.directoryLoading,
                    modifier = Modifier
                        .fillMaxWidth()
                        .navigationBarsPadding()
                        .imePadding()
                        .padding(12.dp)
                        .height(48.dp),
                ) {
                    Text(appText("添加当前文件夹", "Add this folder"))
                }
            }
        },
    ) { padding ->
        BoxWithConstraints(
            Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            val wide = maxWidth >= 720.dp
            Column(Modifier.fillMaxSize()) {
                BrowserPathBar(state, viewModel)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.45f))
                if (wide) {
                    Row(Modifier.fillMaxSize()) {
                        LocationsColumn(
                            state = state,
                            viewModel = viewModel,
                            modifier = Modifier
                                .width(220.dp)
                                .fillMaxHeight(),
                        )
                        androidx.compose.foundation.layout.Box(
                            Modifier
                                .fillMaxHeight()
                                .width(1.dp)
                                .background(MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)),
                        )
                        DirectoryEntries(
                            state = state,
                            viewModel = viewModel,
                            modifier = Modifier
                                .weight(1f)
                                .fillMaxHeight(),
                        )
                    }
                } else {
                    Column(Modifier.fillMaxSize()) {
                        LocationsRow(state, viewModel)
                        DirectoryEntries(
                            state = state,
                            viewModel = viewModel,
                            modifier = Modifier
                                .weight(1f)
                                .fillMaxWidth(),
                        )
                    }
                }
            }
        }
    }

    state.folderCreationParent?.let { parent ->
        CreateRemoteFolderDialog(
            parent = parent,
            name = state.folderCreationName,
            creating = state.folderCreating,
            error = state.folderCreationError,
            onNameChange = viewModel::setFolderCreationName,
            onCreate = viewModel::confirmFolderCreation,
            onDismiss = viewModel::cancelFolderCreation,
        )
    }
}

@Composable
private fun CreateRemoteFolderDialog(
    parent: String,
    name: String,
    creating: Boolean,
    error: String?,
    onNameChange: (String) -> Unit,
    onCreate: () -> Unit,
    onDismiss: () -> Unit,
) {
    val normalizedName = name.trim()
    val target = if (parent.endsWith('/')) {
        parent + normalizedName.ifEmpty { "…" }
    } else {
        "$parent/${normalizedName.ifEmpty { "…" }}"
    }
    AlertDialog(
        onDismissRequest = { if (!creating) onDismiss() },
        title = { Text(appText("新建文件夹", "New folder")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = onNameChange,
                    enabled = !creating,
                    singleLine = true,
                    label = { Text(appText("文件夹名称", "Folder name")) },
                    isError = error != null,
                    supportingText = error?.let { message ->
                        { Text(message) }
                    },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { onCreate() }),
                    modifier = Modifier.fillMaxWidth(),
                )
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(
                        "将在这台设备上创建",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        target,
                        style = MaterialTheme.typography.bodySmall.copy(
                            fontFamily = FontFamily.Monospace,
                        ),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onCreate,
                enabled = !creating && normalizedName.isNotEmpty(),
            ) {
                if (creating) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text(appText("创建", "Create"))
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !creating) {
                Text(appText("取消", "Cancel"))
            }
        },
    )
}

@Composable
private fun BrowserPathBar(state: MainUiState, viewModel: MainViewModel) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(
            onClick = viewModel::browseParent,
            enabled = state.directory?.parent != null && !state.directoryLoading,
        ) {
            Icon(
                Icons.AutoMirrored.Filled.ArrowBack,
                contentDescription = appText("上级目录", "Parent folder"),
            )
        }
        OutlinedTextField(
            value = state.browserPath,
            onValueChange = viewModel::setBrowserPath,
            enabled = !state.directoryLoading,
            singleLine = true,
            label = { Text(appText("设备路径", "Computer path")) },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = { viewModel.browse() }),
            modifier = Modifier.weight(1f),
            textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
            trailingIcon = {
                IconButton(
                    onClick = { viewModel.browse() },
                    enabled = !state.directoryLoading,
                ) {
                    Icon(
                        Icons.Default.ChevronRight,
                        contentDescription = appText("打开路径", "Open path"),
                    )
                }
            },
        )
    }
}

@Composable
private fun LocationsRow(state: MainUiState, viewModel: MainViewModel) {
    Column(Modifier.padding(vertical = 8.dp)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "位置",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            Text(appText("隐藏文件", "Hidden files"), style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = state.showHidden,
                onCheckedChange = viewModel::setShowHidden,
                enabled = !state.directoryLoading,
            )
        }
        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(state.browserRoots?.roots.orEmpty(), key = { it.path }) { root ->
                OutlinedButton(
                    onClick = { viewModel.browse(root.path) },
                    enabled = !state.directoryLoading,
                ) {
                    Text(root.name, maxLines = 1)
                }
            }
        }
    }
}

@Composable
private fun LocationsColumn(
    state: MainUiState,
    viewModel: MainViewModel,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .background(MaterialTheme.colorScheme.surface)
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            "位置",
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
        )
        state.browserRoots?.roots.orEmpty().forEach { root ->
            TextButton(
                onClick = { viewModel.browse(root.path) },
                enabled = !state.directoryLoading,
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp),
            ) {
                Icon(Icons.Default.Folder, contentDescription = null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(root.name, modifier = Modifier.weight(1f), maxLines = 1)
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                appText("隐藏文件", "Hidden files"),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium,
            )
            Switch(
                checked = state.showHidden,
                onCheckedChange = viewModel::setShowHidden,
                enabled = !state.directoryLoading,
            )
        }
    }
}

@Composable
private fun DirectoryEntries(
    state: MainUiState,
    viewModel: MainViewModel,
    modifier: Modifier = Modifier,
) {
    LazyColumn(modifier = modifier) {
        if (state.directoryLoading && state.directory == null) {
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text(
                        "正在加载文件夹…",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else if (state.directory?.entries.isNullOrEmpty()) {
            item { EmptyListRow("此文件夹为空", Icons.Default.Folder) }
        } else {
            items(state.directory.entries, key = { it.path }) { entry ->
                DirectoryRow(entry) {
                    if (entry.isDirectory && entry.readable) viewModel.browse(entry.path)
                }
                GroupDivider(startPadding = 52.dp)
            }
        }
        if (state.directory?.hasMore == true) {
            item {
                if (state.directoryLoading) {
                    LinearProgressIndicator(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 18.dp),
                    )
                } else {
                    OutlinedButton(
                        onClick = viewModel::browseMore,
                        enabled = !state.busy,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 12.dp),
                    ) {
                        Text(appText("加载更多", "Load more"))
                    }
                }
            }
        }
    }
}

@Composable
private fun DirectoryRow(entry: DirectoryEntry, onOpen: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = entry.isDirectory && entry.readable, onClick = onOpen)
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            if (entry.isDirectory) Icons.Default.Folder else Icons.Default.Description,
            contentDescription = null,
            tint = if (entry.readable) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
        Spacer(Modifier.width(12.dp))
        Text(
            entry.name,
            color = if (entry.readable) {
                MaterialTheme.colorScheme.onSurface
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        directoryModifiedLabel(entry.modifiedAt).takeIf(String::isNotEmpty)?.let { label ->
            Spacer(Modifier.width(8.dp))
            Text(
                label,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
            )
        }
        if (entry.isDirectory) {
            Icon(
                Icons.Default.ChevronRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

internal fun directoryModifiedLabel(
    value: String?,
    now: Instant = Instant.now(),
    zoneId: ZoneId = ZoneId.systemDefault(),
): String {
    val modified = runCatching { Instant.parse(value.orEmpty()) }.getOrNull() ?: return ""
    val local = modified.atZone(zoneId)
    val today = now.atZone(zoneId).toLocalDate()
    return when {
        local.toLocalDate() == today -> local.format(DateTimeFormatter.ofPattern("HH:mm"))
        local.toLocalDate() == today.minusDays(1) -> "昨天"
        local.year == today.year -> local.format(DateTimeFormatter.ofPattern("M月d日"))
        else -> local.format(DateTimeFormatter.ofPattern("yyyy年M月d日"))
    }
}
