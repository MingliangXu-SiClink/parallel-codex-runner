package com.parallelcodexrunner.remote.ui

import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.PCRRemoteApplication
import com.parallelcodexrunner.remote.data.FeedbackItem
import com.parallelcodexrunner.remote.data.PendingFeedbackAttachment
import com.parallelcodexrunner.remote.data.PendingFeedbackSubmission
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RelayRequestException
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

data class FeedbackUiState(
    val relatedDeviceId: String? = null,
    val relatedDeviceName: String = "",
    val items: List<FeedbackItem> = emptyList(),
    val pending: List<PendingFeedbackSubmission> = emptyList(),
    val loading: Boolean = false,
    val submitting: Boolean = false,
    val actioningId: String? = null,
    val lastQueuedSubmissionId: String? = null,
    val nextCursor: String = "",
    val hasMore: Boolean = false,
    val loadingMore: Boolean = false,
    val error: String? = null,
)

data class FeedbackDiagnosticContext(
    val workspaceId: String = "",
    val workspaceName: String = "",
    val runId: String = "",
    val agent: Int? = null,
    val errorCode: String = "",
    val errorSummary: String = "",
)

class FeedbackCoordinator(
    private val app: PCRRemoteApplication,
    private val scope: CoroutineScope,
) {
    private val mutableState = MutableStateFlow(FeedbackUiState())
    val state: StateFlow<FeedbackUiState> = mutableState.asStateFlow()
    private var refreshJob: Job? = null
    private var previousOutboxIds = app.mobileStateStore.load().feedbackOutbox.keys

    init {
        scope.launch {
            app.mobileStateStore.state.drop(1).collectLatest { persisted ->
                val currentDevice = mutableState.value.relatedDeviceId
                val pending = persisted.feedbackOutbox.values
                    .sortedByDescending(PendingFeedbackSubmission::id)
                val completed = previousOutboxIds - persisted.feedbackOutbox.keys
                previousOutboxIds = persisted.feedbackOutbox.keys
                mutableState.update { it.copy(pending = pending) }
                if (currentDevice != null && completed.isNotEmpty()) refresh(currentDevice)
            }
        }
    }

    fun open(mac: RelayDevice?) {
        val pending = app.mobileStateStore.load().feedbackOutbox.values
            .sortedByDescending(PendingFeedbackSubmission::id)
        mutableState.value = FeedbackUiState(
            relatedDeviceId = mac?.deviceId.orEmpty(),
            relatedDeviceName = mac?.name.orEmpty(),
            pending = pending,
            loading = true,
        )
        pending.filter {
            it.status == PendingFeedbackSubmission.STATUS_RETRYING && it.error.isNotBlank()
        }.forEach(app.feedbackSubmissionScheduler::retry)
        refresh(mac?.deviceId.orEmpty())
    }

    fun dismiss() {
        refreshJob?.cancel()
        refreshJob = null
        mutableState.value = FeedbackUiState()
    }

    fun refresh(
        relatedDeviceId: String? = mutableState.value.relatedDeviceId,
        append: Boolean = false,
    ) {
        val target = relatedDeviceId ?: return
        if (append && (!mutableState.value.hasMore || mutableState.value.loadingMore)) return
        refreshJob?.cancel()
        refreshJob = scope.launch {
            mutableState.update {
                if (append) it.copy(loadingMore = true, error = null)
                else it.copy(loading = true, loadingMore = false, error = null)
            }
            try {
                val cursor = if (append) mutableState.value.nextCursor else ""
                val page = app.repository.feedback(cursor)
                if (mutableState.value.relatedDeviceId == target) {
                    mutableState.update {
                        val merged = if (append) {
                            (it.items + page.items).distinctBy(FeedbackItem::feedbackId)
                        } else {
                            page.items
                        }
                        it.copy(
                            items = merged,
                            loading = false,
                            loadingMore = false,
                            nextCursor = page.nextCursor,
                            hasMore = page.hasMore,
                            error = null,
                        )
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (mutableState.value.relatedDeviceId == target) {
                    mutableState.update {
                        it.copy(
                            loading = false,
                            loadingMore = false,
                            error = feedbackError(error),
                        )
                    }
                }
            }
        }
    }

    fun loadMore() = refresh(append = true)

    fun submit(
        kind: String,
        description: String,
        imageUris: List<Uri>,
        context: FeedbackDiagnosticContext,
        includedDiagnosticKeys: Set<String>,
    ) {
        val relatedDeviceId = mutableState.value.relatedDeviceId ?: return
        if (description.isBlank() || mutableState.value.submitting) return
        scope.launch {
            mutableState.update { it.copy(submitting = true, error = null) }
            try {
                val id = UUID.randomUUID().toString()
                val attachments = prepareAttachments(id, imageUris.take(3))
                val submission = PendingFeedbackSubmission(
                    id = id,
                    relatedDeviceId = relatedDeviceId,
                    kind = kind,
                    description = description.trim(),
                    diagnostics = diagnostics(context, includedDiagnosticKeys),
                    attachments = attachments,
                )
                app.mobileStateStore.update { state ->
                    state.copy(feedbackOutbox = state.feedbackOutbox + (id to submission))
                }
                app.feedbackSubmissionScheduler.enqueue(submission)
                mutableState.update {
                    it.copy(
                        submitting = false,
                        lastQueuedSubmissionId = id,
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                mutableState.update {
                    it.copy(submitting = false, error = feedbackError(error))
                }
            }
        }
    }

    fun diagnosticPreview(context: FeedbackDiagnosticContext): Map<String, String> =
        diagnostics(context).mapValues { (_, value) ->
            (value as? JsonPrimitive)?.contentOrNull.orEmpty()
        }

    fun retry(id: String) {
        val submission = app.mobileStateStore.load().feedbackOutbox[id] ?: return
        val retried = submission.copy(
            status = PendingFeedbackSubmission.STATUS_PENDING,
            error = "",
        )
        app.mobileStateStore.update { state ->
            state.copy(feedbackOutbox = state.feedbackOutbox + (id to retried))
        }
        app.feedbackSubmissionScheduler.retry(retried)
    }

    fun removePending(id: String) {
        val submission = app.mobileStateStore.load().feedbackOutbox[id] ?: return
        app.feedbackSubmissionScheduler.cancel(id)
        app.mobileStateStore.update { state ->
            state.copy(feedbackOutbox = state.feedbackOutbox - id)
        }
        deleteSubmissionFiles(submission.id)
        if (submission.feedbackId.isNotBlank()) {
            scope.launch {
                runCatching {
                    app.repository.withdrawFeedback(submission.feedbackId)
                    if (mutableState.value.relatedDeviceId == submission.relatedDeviceId) {
                        refresh(submission.relatedDeviceId)
                    }
                }
            }
        }
    }

    fun withdraw(feedbackId: String) {
        val relatedDeviceId = mutableState.value.relatedDeviceId ?: return
        scope.launch {
            mutableState.update { it.copy(actioningId = feedbackId, error = null) }
            try {
                app.repository.withdrawFeedback(feedbackId)
                refresh(relatedDeviceId)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                mutableState.update { it.copy(error = feedbackError(error)) }
            } finally {
                mutableState.update { current ->
                    if (current.actioningId == feedbackId) current.copy(actioningId = null)
                    else current
                }
            }
        }
    }

    fun addNote(
        feedbackId: String,
        text: String,
        onSuccess: () -> Unit = {},
    ) {
        val relatedDeviceId = mutableState.value.relatedDeviceId ?: return
        val note = text.trim()
        if (note.isEmpty()) return
        scope.launch {
            mutableState.update { it.copy(actioningId = feedbackId, error = null) }
            try {
                app.repository.addFeedbackNote(
                    feedbackId = feedbackId,
                    text = note,
                    commandId = "feedback-note-$feedbackId-${UUID.randomUUID()}",
                )
                onSuccess()
                refresh(relatedDeviceId)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                mutableState.update { it.copy(error = feedbackError(error)) }
            } finally {
                mutableState.update { current ->
                    if (current.actioningId == feedbackId) current.copy(actioningId = null)
                    else current
                }
            }
        }
    }

    private suspend fun prepareAttachments(
        id: String,
        imageUris: List<Uri>,
    ): List<PendingFeedbackAttachment> = withContext(Dispatchers.IO) {
        if (imageUris.isEmpty()) return@withContext emptyList()
        val language = AppLanguage.load(app)
        val directory = File(app.filesDir, "feedback-outbox/$id")
        check(directory.mkdirs() || directory.isDirectory) {
            language.text("无法保存反馈截图。", "The feedback image could not be saved.")
        }
        imageUris.mapIndexed { index, uri ->
            val bytes = runCatching {
                val source = ImageDecoder.createSource(app.contentResolver, uri)
                ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                    decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                    val longest = maxOf(info.size.width, info.size.height)
                    if (longest > MAX_IMAGE_EDGE) {
                        val scale = MAX_IMAGE_EDGE.toFloat() / longest.toFloat()
                        decoder.setTargetSize(
                            (info.size.width * scale).toInt().coerceAtLeast(1),
                            (info.size.height * scale).toInt().coerceAtLeast(1),
                        )
                    }
                }
            }.getOrNull()
                ?.let(::encodeScreenshot)
                ?: error(language.text(
                    "无法读取第 ${index + 1} 张截图。",
                    "Feedback image ${index + 1} could not be read.",
                ))
            val digest = sha256(bytes)
            val fileName = "$digest.jpg"
            val file = File(directory, fileName)
            file.outputStream().use { it.write(bytes) }
            PendingFeedbackAttachment(
                fileName = fileName,
                contentType = "image/jpeg",
                sha256 = digest,
                sizeBytes = bytes.size.toLong(),
            )
        }
    }

    private fun encodeScreenshot(source: Bitmap): ByteArray {
        val language = AppLanguage.load(app)
        var bitmap = source
        try {
            while (true) {
                var quality = 86
                do {
                    val encoded = ByteArrayOutputStream().use { output ->
                        check(bitmap.compress(Bitmap.CompressFormat.JPEG, quality, output)) {
                            language.text(
                                "无法压缩反馈截图。",
                                "The feedback image could not be compressed.",
                            )
                        }
                        output.toByteArray()
                    }
                    if (encoded.size <= MAX_ATTACHMENT_BYTES) return encoded
                    quality -= 8
                } while (quality >= 46)

                val longest = maxOf(bitmap.width, bitmap.height)
                check(longest > MIN_IMAGE_EDGE) {
                    language.text(
                        "截图过大，请裁剪后重试。",
                        "The image is too large. Crop it and try again.",
                    )
                }
                val nextLongest = maxOf(MIN_IMAGE_EDGE, (longest * 0.75f).toInt())
                val scale = nextLongest.toFloat() / longest.toFloat()
                val scaled = Bitmap.createScaledBitmap(
                    bitmap,
                    (bitmap.width * scale).toInt().coerceAtLeast(1),
                    (bitmap.height * scale).toInt().coerceAtLeast(1),
                    true,
                )
                if (bitmap !== source) bitmap.recycle()
                bitmap = scaled
            }
        } finally {
            if (bitmap !== source) bitmap.recycle()
            if (!source.isRecycled) source.recycle()
        }
    }

    private fun diagnostics(
        context: FeedbackDiagnosticContext,
        includedKeys: Set<String>? = null,
    ): JsonObject = buildJsonObject {
        fun allowed(key: String): Boolean = includedKeys == null || key in includedKeys
        fun include(key: String, value: String) {
            if (allowed(key) && value.isNotBlank()) put(key, value)
        }
        include("app_platform", "android")
        include("app_version", BuildConfig.VERSION_NAME)
        if (allowed("app_build")) put("app_build", BuildConfig.VERSION_CODE)
        include(
            "os_version",
            "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
        )
        include("device_model", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
        include("mac_device_id", mutableState.value.relatedDeviceId.orEmpty())
        include("mac_name", mutableState.value.relatedDeviceName)
        include("workspace_id", context.workspaceId)
        include("workspace_name", context.workspaceName)
        include("run_id", context.runId)
        if (allowed("agent")) context.agent?.let { put("agent", it) }
        include("error_code", context.errorCode)
        if (allowed("error_summary") && context.errorSummary.isNotBlank()) {
            put("error_summary", context.errorSummary.take(500))
        }
    }

    private fun deleteSubmissionFiles(id: String) {
        val root = File(app.filesDir, "feedback-outbox").canonicalFile
        val directory = File(root, id).canonicalFile
        if (directory.parentFile != root || !directory.isDirectory) return
        directory.listFiles().orEmpty().forEach { if (it.isFile) it.delete() }
        directory.delete()
    }

    private fun feedbackError(error: Throwable): String {
        val language = AppLanguage.load(app)
        if (error is RelayRequestException) {
            return when (error.statusCode) {
                401, 403 -> language.text(
                    "当前连接已失效，请重新连接后提交。",
                    "The connection has expired. Reconnect before submitting feedback.",
                )
                429 -> language.text(
                    "提交次数较多，请稍后再试。",
                    "Too many feedback requests. Try again shortly.",
                )
                in 400..499 -> language.text(
                    "反馈内容无法提交，请检查后重试。",
                    "This feedback could not be submitted. Review it and try again.",
                )
                else -> language.text(
                    "反馈服务暂时不可用，已保存的内容会在连接恢复后重试。",
                    "The feedback service is temporarily unavailable. Your feedback is saved and will retry when the connection returns.",
                )
            }
        }
        val message = error.message.orEmpty().trim()
        return when {
            message.contains("unsupported", ignoreCase = true) ->
                language.text(
                    "当前版本暂时无法提交反馈，请更新应用后重试。",
                    "This app version cannot submit feedback. Update the app and try again.",
                )
            message.isBlank() -> language.text(
                "反馈暂时无法提交，请稍后重试。",
                "Feedback could not be submitted. Try again shortly.",
            )
            else -> friendlyStoredError(message, language)
        }
    }

    private fun sha256(content: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(content)
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private companion object {
        const val MAX_IMAGE_EDGE = 1_600
        const val MIN_IMAGE_EDGE = 700
        const val MAX_ATTACHMENT_BYTES = 512 * 1024
    }
}
