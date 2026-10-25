package com.parallelcodexrunner.remote.data

import android.content.Context
import android.util.Base64
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.parallelcodexrunner.remote.PCRRemoteApplication
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException

class FeedbackSubmissionScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val workManager by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        WorkManager.getInstance(applicationContext)
    }

    fun enqueue(submission: PendingFeedbackSubmission) {
        val request = OneTimeWorkRequestBuilder<FeedbackSubmissionWorker>()
            .setInputData(workDataOf(FeedbackSubmissionWorker.KEY_ID to submission.id))
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
            .addTag(TAG_ALL)
            .build()
        workManager.enqueueUniqueWork(
            workName(submission.id),
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun retry(submission: PendingFeedbackSubmission) {
        val request = OneTimeWorkRequestBuilder<FeedbackSubmissionWorker>()
            .setInputData(workDataOf(FeedbackSubmissionWorker.KEY_ID to submission.id))
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
            .addTag(TAG_ALL)
            .build()
        workManager.enqueueUniqueWork(
            workName(submission.id),
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun cancel(id: String) = workManager.cancelUniqueWork(workName(id))

    fun cancelAll() = workManager.cancelAllWorkByTag(TAG_ALL)

    private companion object {
        const val TAG_ALL = "pcr-feedback-submission"
        fun workName(id: String) = "pcr-feedback-$id"
    }
}

class FeedbackSubmissionWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? PCRRemoteApplication ?: return Result.failure()
        val id = inputData.getString(KEY_ID).orEmpty()
        if (id.isBlank()) return Result.failure()
        val submission = app.mobileStateStore.load().feedbackOutbox[id]
            ?.takeIf { it.id == id }
            ?: return Result.success()
        if (submission.status == PendingFeedbackSubmission.STATUS_FAILED && runAttemptCount == 0) {
            return Result.success()
        }
        update(app, submission) {
            it.copy(status = PendingFeedbackSubmission.STATUS_RETRYING)
        }
        return try {
            var remote = try {
                submit(app, submission, submission.relatedDeviceId)
            } catch (error: RelayRequestException) {
                if (submission.relatedDeviceId.isNotBlank() &&
                    error.statusCode == 403 &&
                    error.message.orEmpty().contains(
                        "related device is not available",
                        ignoreCase = true,
                    )
                ) {
                    submit(app, submission, "")
                } else {
                    throw error
                }
            }
            update(app, submission) { it.copy(feedbackId = remote.feedbackId) }
            if (app.mobileStateStore.load().feedbackOutbox[id] == null) {
                runCatching { app.repository.withdrawFeedback(remote.feedbackId) }
                return Result.success()
            }
            val uploaded = remote.attachments.map(FeedbackAttachment::sha256).toMutableSet()
            for (attachment in submission.attachments) {
                if (app.mobileStateStore.load().feedbackOutbox[id] == null) {
                    runCatching { app.repository.withdrawFeedback(remote.feedbackId) }
                    return Result.success()
                }
                if (attachment.sha256 in uploaded) continue
                val content = attachmentFile(app, submission, attachment).readBytes()
                check(content.size.toLong() == attachment.sizeBytes) {
                    "The pending feedback image is corrupted"
                }
                val digest = MessageDigest.getInstance("SHA-256")
                    .digest(content)
                    .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
                check(digest == attachment.sha256) {
                    "The pending feedback image failed verification"
                }
                remote = app.repository.addFeedbackAttachment(
                    feedbackId = remote.feedbackId,
                    contentType = attachment.contentType,
                    encoded = Base64.encodeToString(content, Base64.NO_WRAP),
                    digest = attachment.sha256,
                )
                uploaded += attachment.sha256
            }
            if (app.mobileStateStore.load().feedbackOutbox[id] == null) {
                runCatching { app.repository.withdrawFeedback(remote.feedbackId) }
                return Result.success()
            }
            app.mobileStateStore.update { state ->
                state.copy(feedbackOutbox = state.feedbackOutbox - submission.id)
            }
            deleteSubmissionFiles(app, submission.id)
            Result.success()
        } catch (error: RemoteCommandException) {
            val permanent = error.code in setOf(
                "feedback_error",
                "invalid_argument",
                "unsupported_action",
                "incompatible_protocol",
                "not_allowed",
            )
            if (permanent) {
                update(app, submission) {
                    it.copy(
                        status = PendingFeedbackSubmission.STATUS_FAILED,
                        error = error.message.orEmpty().take(500),
                    )
                }
                Result.failure()
            } else {
                retryOrStop(app, submission, error.message.orEmpty())
            }
        } catch (error: RelayRequestException) {
            if (error.statusCode in setOf(400, 401, 403, 404, 413, 422)) {
                update(app, submission) {
                    it.copy(
                        status = PendingFeedbackSubmission.STATUS_FAILED,
                        error = when (error.statusCode) {
                            401, 403 -> "The feedback connection has expired"
                            404 -> "The feedback record no longer exists"
                            else -> "The feedback could not be submitted"
                        },
                    )
                }
                Result.failure()
            } else {
                retryOrStop(
                    app,
                    submission,
                    if (error.statusCode >= 500) {
                        "The feedback service is temporarily unavailable"
                    } else {
                        error.message.orEmpty()
                    },
                )
            }
        } catch (error: IllegalStateException) {
            update(app, submission) {
                it.copy(
                    status = PendingFeedbackSubmission.STATUS_FAILED,
                    error = error.message.orEmpty().take(500),
                )
            }
            Result.failure()
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            retryOrStop(
                app,
                submission,
                "The network is temporarily unavailable",
            )
        }
    }

    private suspend fun submit(
        app: PCRRemoteApplication,
        submission: PendingFeedbackSubmission,
        relatedDeviceId: String,
    ): FeedbackItem = app.repository.submitFeedback(
        clientFeedbackId = submission.id,
        kind = submission.kind,
        description = submission.description,
        diagnostics = submission.diagnostics,
        relatedDeviceId = relatedDeviceId,
    )

    private fun retryOrStop(
        app: PCRRemoteApplication,
        submission: PendingFeedbackSubmission,
        message: String,
    ): Result {
        val retry = feedbackShouldRetryAutomatically(runAttemptCount)
        val detail = message.trim().trimEnd('.')
        update(app, submission) {
            it.copy(
                status = if (retry) PendingFeedbackSubmission.STATUS_RETRYING
                else PendingFeedbackSubmission.STATUS_FAILED,
                error = if (retry) {
                    "$detail. Automatic retry is scheduled."
                } else {
                    "$detail. Check the connection and tap Retry."
                }.take(500),
            )
        }
        return if (retry) Result.retry() else Result.failure()
    }

    private fun update(
        app: PCRRemoteApplication,
        expected: PendingFeedbackSubmission,
        transform: (PendingFeedbackSubmission) -> PendingFeedbackSubmission,
    ) {
        app.mobileStateStore.update { state ->
            val current = state.feedbackOutbox[expected.id]
            if (current?.id != expected.id) state else state.copy(
                feedbackOutbox = state.feedbackOutbox + (expected.id to transform(current))
            )
        }
    }

    private fun attachmentFile(
        app: PCRRemoteApplication,
        submission: PendingFeedbackSubmission,
        attachment: PendingFeedbackAttachment,
    ): File {
        val directory = File(app.filesDir, "feedback-outbox/${submission.id}").canonicalFile
        val file = File(directory, attachment.fileName).canonicalFile
        check(file.parentFile == directory && file.isFile) {
            "The pending feedback image is missing"
        }
        return file
    }

    private fun deleteSubmissionFiles(app: PCRRemoteApplication, id: String) {
        val root = File(app.filesDir, "feedback-outbox").canonicalFile
        val directory = File(root, id).canonicalFile
        if (directory.parentFile != root || !directory.isDirectory) return
        directory.listFiles().orEmpty().forEach { file ->
            if (file.isFile && file.parentFile == directory) file.delete()
        }
        directory.delete()
    }

    companion object {
        const val KEY_ID = "feedback_id"
    }
}

internal fun feedbackShouldRetryAutomatically(runAttemptCount: Int): Boolean =
    runAttemptCount < MAX_AUTOMATIC_FEEDBACK_RETRY_ATTEMPTS - 1

private const val MAX_AUTOMATIC_FEEDBACK_RETRY_ATTEMPTS = 8
