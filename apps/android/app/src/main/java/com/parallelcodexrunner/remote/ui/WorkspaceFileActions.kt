package com.parallelcodexrunner.remote.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.data.WorkspaceFileDownload
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal enum class TranscriptLinkKind {
    WORKSPACE_FILE,
    EXTERNAL,
    UNSUPPORTED,
}

private val commonWorkspaceFileSuffixes = setOf(
    "c", "cc", "cfg", "conf", "cpp", "css", "csv", "go", "h", "hpp",
    "html", "ini", "java", "js", "json", "kt", "kts", "log", "m",
    "markdown", "md", "mm", "pdf", "plist", "py", "rb", "rs", "sh",
    "sql", "swift", "toml", "ts", "tsx", "txt", "xml", "yaml", "yml",
    "avif", "bmp", "gif", "heic", "heif", "ico", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp",
    "doc", "docx", "odt", "ppt", "pptx", "rtf", "xls", "xlsx",
    "aac", "flac", "m4a", "mov", "mp3", "mp4", "ogg", "wav", "webm", "zip",
)

private fun isBareWebAddress(value: String): Boolean {
    if (value.any(Char::isWhitespace)) return false
    val hostAndPort = value.substringBefore('/').substringBefore('?').substringBefore('#')
    val host = hostAndPort.substringBefore(':')
    val labels = host.split('.')
    if (labels.size < 2 || labels.any { label ->
            label.isEmpty() || label.startsWith('-') || label.endsWith('-') ||
                label.any { !it.isLetterOrDigit() && it != '-' }
        }
    ) return false
    val suffix = labels.last().lowercase()
    if (suffix.length < 2 || suffix.any { !it.isLetter() } ||
        suffix in commonWorkspaceFileSuffixes
    ) return false
    return runCatching { java.net.URI("https://$value").host != null }.getOrDefault(false)
}

internal fun transcriptLinkKind(value: String): TranscriptLinkKind {
    val reference = value.trim()
    if (reference.isEmpty() || reference.startsWith('#')) {
        return TranscriptLinkKind.UNSUPPORTED
    }
    if (reference.startsWith('/') || reference.startsWith("./") ||
        reference.startsWith("../") || reference.startsWith("file:", ignoreCase = true)
    ) {
        return TranscriptLinkKind.WORKSPACE_FILE
    }
    val scheme = LINK_SCHEME.find(reference)?.groupValues?.get(1)?.lowercase().orEmpty()
    if (scheme in setOf("http", "https", "mailto", "tel")) {
        return TranscriptLinkKind.EXTERNAL
    }
    if (WORKSPACE_LINE_LINK.matches(reference)) return TranscriptLinkKind.WORKSPACE_FILE
    return if (scheme.isEmpty() && isBareWebAddress(reference)) {
        TranscriptLinkKind.EXTERNAL
    } else if (scheme.isEmpty()) {
        TranscriptLinkKind.WORKSPACE_FILE
    } else {
        TranscriptLinkKind.UNSUPPORTED
    }
}

internal fun externalTranscriptUri(value: String): String? {
    val reference = value.trim()
    if (transcriptLinkKind(reference) != TranscriptLinkKind.EXTERNAL) return null
    return if (LINK_SCHEME.containsMatchIn(reference)) reference else "https://$reference"
}

fun MainViewModel.openTranscriptLink(reference: String) {
    when (transcriptLinkKind(reference)) {
        TranscriptLinkKind.EXTERNAL -> externalTranscriptUri(reference)?.let(::openExternalUri)
        TranscriptLinkKind.UNSUPPORTED -> showError(state.value.appLanguage.text(
            "这个链接无法在手机上安全打开。",
            "This link cannot be opened safely on this phone.",
        ))
        TranscriptLinkKind.WORKSPACE_FILE -> downloadWorkspaceFile(reference)
    }
}

fun MainViewModel.dismissWorkspaceDocument() {
    mutableState.update {
        it.copy(
            workspaceDocument = null,
            workspaceDocumentHistory = emptyList(),
        )
    }
}

fun MainViewModel.navigateBackWorkspaceDocument() {
    mutableState.update(MainUiState::popWorkspaceDocument)
}

internal fun MainUiState.popWorkspaceDocument(): MainUiState {
    val previous = workspaceDocumentHistory.lastOrNull()
    return if (previous == null) {
        copy(
            workspaceDocument = null,
            workspaceDocumentHistory = emptyList(),
        )
    } else {
        copy(
            workspaceDocument = previous,
            workspaceDocumentHistory = workspaceDocumentHistory.dropLast(1),
        )
    }
}

fun MainViewModel.cancelWorkspaceFileDownload() {
    workspaceFileJob?.cancel()
    mutableState.update {
        it.copy(workspaceFileLoading = false, workspaceFileProgress = null)
    }
}

internal fun MainViewModel.closeWorkspaceFilesForContextChange() {
    workspaceFileJob?.cancel()
    mutableState.update {
        it.copy(
            workspaceFileLoading = false,
            workspaceFileProgress = null,
            workspaceDocument = null,
            workspaceDocumentHistory = emptyList(),
        )
    }
}

fun MainViewModel.openWorkspaceDocumentExternally() {
    state.value.workspaceDocument?.source?.let { file ->
        openDownloadedWorkspaceFile(
            WorkspaceFileDownload(
                file = file,
                name = file.name,
                relativePath = state.value.workspaceDocument?.relativePath.orEmpty(),
                contentType = "text/markdown",
                sizeBytes = file.length(),
            )
        )
    }
}

fun MainViewModel.openWorkspaceDocumentLink(reference: String) {
    val document = state.value.workspaceDocument
    val resolved = if (
        document != null &&
        transcriptLinkKind(reference) == TranscriptLinkKind.WORKSPACE_FILE &&
        !reference.startsWith('/') &&
        !reference.startsWith("file:", ignoreCase = true)
    ) {
        val parent = document.relativePath.substringBeforeLast('/', "")
        if (parent.isBlank()) reference else "$parent/$reference"
    } else {
        reference
    }
    when (transcriptLinkKind(resolved)) {
        TranscriptLinkKind.EXTERNAL -> externalTranscriptUri(resolved)?.let(::openExternalUri)
        TranscriptLinkKind.UNSUPPORTED -> showError(state.value.appLanguage.text(
            "这个链接无法在手机上安全打开。",
            "This link cannot be opened safely on this phone.",
        ))
        TranscriptLinkKind.WORKSPACE_FILE -> downloadWorkspaceFile(
            resolved,
            navigationParent = document,
        )
    }
}

private fun MainViewModel.downloadWorkspaceFile(
    reference: String,
    navigationParent: WorkspaceDocument? = null,
) {
    if (workspaceFileJob?.isActive == true) return
    val snapshot = state.value
    val mac = snapshot.macs.firstOrNull { it.deviceId == snapshot.selectedMacId }
    val workspaceId = snapshot.selectedWorkspaceId
    if (mac == null || workspaceId == null) {
        showError(state.value.appLanguage.text(
            "请先选择这个文件所属的设备和工作区。",
            "Select the device and workspace that contain this file first.",
        ))
        return
    }
    val runId = snapshot.visibleRunId.orEmpty()
    val agent = snapshot.selectedAgent
    if (runId.isNotBlank() && agent == null) {
        showError(state.value.appLanguage.text(
            "请先切换到包含这个文件链接的 Agent。",
            "Switch to the Agent that contains this file link first.",
        ))
        return
    }

    workspaceFileJob = viewModelScope.launch {
        mutableState.update {
            it.copy(
                workspaceFileLoading = true,
                workspaceFileProgress = 0,
                message = null,
                messageIsError = false,
            )
        }
        try {
            val directory = withContext(Dispatchers.IO) {
                workspaceFileDirectory(app.cacheDir).also(::cleanWorkspaceFileCache)
            }
            val download = withContext(Dispatchers.IO) {
                repository.downloadWorkspaceFile(
                    mac = mac,
                    workspaceId = workspaceId,
                    runId = runId,
                    agent = agent,
                    reference = reference,
                    destinationDirectory = directory,
                ) { downloaded, total ->
                    val progress = if (total <= 0) 100 else {
                        ((downloaded * 100) / total).coerceIn(0, 100).toInt()
                    }
                    mutableState.update { current ->
                        if (current.selectedMacId == mac.deviceId &&
                            current.selectedWorkspaceId == workspaceId &&
                            current.visibleRunId.orEmpty() == runId &&
                            current.selectedAgent == agent
                        ) {
                            current.copy(workspaceFileProgress = progress)
                        } else {
                            current
                        }
                    }
                }
            }
            val current = state.value
            if (current.selectedMacId != mac.deviceId ||
                current.selectedWorkspaceId != workspaceId ||
                current.visibleRunId.orEmpty() != runId ||
                current.selectedAgent != agent
            ) {
                withContext(Dispatchers.IO) { download.file.delete() }
                return@launch
            }
            if (download.isMarkdown() && download.sizeBytes <= MAX_NATIVE_MARKDOWN_BYTES) {
                val content = withContext(Dispatchers.IO) {
                    download.file.readText(Charsets.UTF_8)
                }
                mutableState.update {
                    val history = if (
                        navigationParent != null &&
                        it.workspaceDocument === navigationParent
                    ) {
                        it.workspaceDocumentHistory + navigationParent
                    } else {
                        it.workspaceDocumentHistory
                    }
                    it.copy(
                        workspaceDocument = WorkspaceDocument(
                            name = download.name,
                            content = content,
                            source = download.file,
                            relativePath = download.relativePath,
                            line = download.line,
                        ),
                        workspaceDocumentHistory = history,
                    )
                }
            } else {
                openDownloadedWorkspaceFile(download)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            val current = state.value
            if (current.selectedMacId == mac.deviceId &&
                current.selectedWorkspaceId == workspaceId &&
                current.visibleRunId.orEmpty() == runId &&
                current.selectedAgent == agent
            ) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        } finally {
            mutableState.update { current ->
                if (current.selectedMacId == mac.deviceId &&
                    current.selectedWorkspaceId == workspaceId &&
                    current.visibleRunId.orEmpty() == runId &&
                    current.selectedAgent == agent
                ) {
                    current.copy(workspaceFileLoading = false, workspaceFileProgress = null)
                } else {
                    current
                }
            }
            workspaceFileJob = null
        }
    }
}

private fun MainViewModel.openExternalUri(value: String) {
    try {
        app.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(value)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    } catch (_: ActivityNotFoundException) {
        showError(state.value.appLanguage.text(
            "手机上没有可以打开这个链接的应用。",
            "No app on this phone can open this link.",
        ))
    }
}

private fun MainViewModel.openDownloadedWorkspaceFile(download: WorkspaceFileDownload) {
    try {
        val uri = FileProvider.getUriForFile(
            app,
            "${BuildConfig.APPLICATION_ID}.workspacefiles",
            download.file,
        )
        app.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, download.contentType)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    } catch (_: ActivityNotFoundException) {
        showError(state.value.appLanguage.text(
            "手机上没有可以打开 ${download.name} 的应用。",
            "No app on this phone can open ${download.name}.",
        ))
    } catch (error: IllegalArgumentException) {
        showError(state.value.appLanguage.text(
            "下载的文件无法安全交给其他应用打开。",
            "The downloaded file cannot be passed safely to another app.",
        ))
    }
}

private fun WorkspaceFileDownload.isMarkdown(): Boolean =
    contentType.equals("text/markdown", ignoreCase = true) ||
        name.substringAfterLast('.', "").lowercase() in setOf("md", "markdown", "mdown", "mkd")

private fun workspaceFileDirectory(cacheRoot: File): File =
    File(cacheRoot, "workspace-files").canonicalFile.apply { mkdirs() }

private fun cleanWorkspaceFileCache(directory: File) {
    val now = System.currentTimeMillis()
    directory.listFiles().orEmpty()
        .filter { it.isFile && (it.name.endsWith(".part") || now - it.lastModified() > CACHE_MAX_AGE_MS) }
        .forEach(File::delete)

    var retained = directory.listFiles().orEmpty().filter(File::isFile)
    var bytes = retained.sumOf(File::length)
    if (bytes <= CACHE_MAX_BYTES) return
    retained = retained.sortedBy(File::lastModified).toMutableList()
    for (file in retained) {
        if (bytes <= CACHE_MAX_BYTES) break
        val size = file.length()
        if (file.delete()) bytes -= size
    }
}

private const val MAX_NATIVE_MARKDOWN_BYTES = 4L * 1024L * 1024L
private const val CACHE_MAX_BYTES = 512L * 1024L * 1024L
private const val CACHE_MAX_AGE_MS = 7L * 24L * 60L * 60L * 1000L
private val LINK_SCHEME = Regex("^([A-Za-z][A-Za-z0-9+.-]*):")
private val WORKSPACE_LINE_LINK = Regex("^.+:[0-9]+(?::[0-9]+)?(?:#.*)?$")
