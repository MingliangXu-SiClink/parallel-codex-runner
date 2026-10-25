package com.parallelcodexrunner.remote.data

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.parallelcodexrunner.remote.PCRRemoteApplication
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class PromptSubmissionScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val workManager by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        WorkManager.getInstance(applicationContext)
    }

    fun enqueue(submission: PendingPromptSubmission): UUID {
        val request = OneTimeWorkRequestBuilder<PromptSubmissionWorker>()
            .setInputData(
                workDataOf(
                    PromptSubmissionWorker.KEY_CONTEXT to
                        PromptSubmissionWorker.contextKey(submission),
                    PromptSubmissionWorker.KEY_SUBMISSION_ID to submission.id,
                    PromptSubmissionWorker.KEY_COMMAND_ID to submission.commandId,
                )
            )
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
            .addTag(TAG_ALL)
            .addTag(macTag(submission.macId))
            .addTag(workspaceTag(submission.macId, submission.workspaceId))
            .build()
        workManager.enqueueUniqueWork(
            workName(submission),
            // A user retry may intentionally reuse the same remote command ID
            // so pcrd can return its idempotent result. The previous WorkInfo
            // is already terminal at that point; REPLACE guarantees that the
            // retry receives a fresh WorkRequest ID instead of observing the
            // old failed record forever.
            ExistingWorkPolicy.REPLACE,
            request,
        )
        return request.id
    }

    fun workInfo(workID: UUID): Flow<WorkInfo?> =
        workManager.getWorkInfoByIdFlow(workID)

    fun cancelForMac(macID: String) = workManager.cancelAllWorkByTag(macTag(macID))

    fun cancelForWorkspace(macID: String, workspaceID: String) =
        workManager.cancelAllWorkByTag(workspaceTag(macID, workspaceID))

    fun cancelAll() = workManager.cancelAllWorkByTag(TAG_ALL)

    private fun workName(submission: PendingPromptSubmission): String =
        "pcr-prompt-${submission.commandId}"

    private companion object {
        const val TAG_ALL = "pcr-prompt-submission"

        fun macTag(macID: String) = "pcr-prompt-mac-$macID"

        fun workspaceTag(macID: String, workspaceID: String) =
            "pcr-prompt-workspace-$macID-$workspaceID"
    }
}

class PromptSubmissionWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? PCRRemoteApplication
            ?: return Result.failure(failureData("Background submission service is unavailable"))
        val contextKey = inputData.getString(KEY_CONTEXT).orEmpty()
        val submissionID = inputData.getString(KEY_SUBMISSION_ID).orEmpty()
        val commandID = inputData.getString(KEY_COMMAND_ID).orEmpty()
        if (contextKey.isEmpty() || submissionID.isEmpty() || commandID.isEmpty()) {
            return Result.failure(failureData("The pending message is invalid"))
        }
        val submission = app.mobileStateStore.load().promptOutbox[contextKey]
            ?.takeIf { it.id == submissionID && it.commandId == commandID }
            ?: return Result.success(workDataOf(KEY_RESULT_STATUS to STATUS_CANCELLED))
        if (runAttemptCount > 0) {
            updatePersistedSubmission(app, contextKey, submission) {
                it.copy(
                    status = PendingPromptSubmission.STATUS_RETRYING,
                    error = "",
                )
            }
        }

        return try {
            val mac = app.repository.macs().firstOrNull { it.deviceId == submission.macId }
                ?: run {
                    val message = "The target device was removed"
                    updatePersistedSubmission(app, contextKey, submission) {
                        it.copy(
                            status = PendingPromptSubmission.STATUS_FAILED,
                            error = message,
                        )
                    }
                    return Result.failure(failureData(message))
                }
            val response = if (
                submission.sourceRunId.isNotBlank() && submission.sourceAgent != null
            ) {
                app.repository.continueRun(
                    mac = mac,
                    runId = submission.sourceRunId,
                    agent = submission.sourceAgent,
                    prompt = submission.prompt,
                    agents = submission.settings.agents,
                    synthesisAgents = submission.settings.synthesisAgents,
                    settings = submission.settings,
                    confirmLargeRun = submission.confirmLargeRun,
                    commandId = submission.commandId,
                )
            } else {
                app.repository.startRun(
                    mac = mac,
                    workspaceId = submission.workspaceId,
                    prompt = submission.prompt,
                    agents = submission.settings.agents,
                    synthesisAgents = submission.settings.synthesisAgents,
                    settings = submission.settings,
                    resumeSessionId = submission.resumeSessionId,
                    confirmLargeRun = submission.confirmLargeRun,
                    commandId = submission.commandId,
                )
            }
            if (response["status"]?.jsonPrimitive?.content == "confirmation_required") {
                val message = response["message"]?.jsonPrimitive?.content.orEmpty()
                    .ifBlank { "This task requires storage confirmation" }
                updatePersistedSubmission(app, contextKey, submission) {
                    it.copy(
                        status = PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION,
                        error = message,
                    )
                }
                Result.success(
                    workDataOf(
                        KEY_RESULT_STATUS to STATUS_CONFIRMATION_REQUIRED,
                        KEY_MESSAGE to message,
                    )
                )
            } else {
                val runID = response["run_id"]?.jsonPrimitive?.contentOrNull.orEmpty()
                if (runID.isBlank()) {
                    val message = "Target machine did not return a run id"
                    updatePersistedSubmission(app, contextKey, submission) {
                        it.copy(
                            status = PendingPromptSubmission.STATUS_FAILED,
                            error = message,
                            retryRequiresNewCommandId = true,
                        )
                    }
                    Result.failure(failureData(message, requiresNewCommandID = true))
                } else {
                    runCatching {
                        app.runCompletionScheduler.enqueue(
                            macID = submission.macId,
                            workspaceID = submission.workspaceId,
                            workspaceName = submission.workspaceName,
                            runID = runID,
                        )
                    }
                    val continuedSessionID = response["continued_from"]
                        ?.jsonObject
                        ?.get("session_id")
                        ?.jsonPrimitive
                        ?.contentOrNull
                        .orEmpty()
                    val workspaceRevision = response["workspace_revision"]
                        ?.jsonPrimitive
                        ?.intOrNull ?: -1
                    recordAcceptedSubmission(
                        app = app,
                        contextKey = contextKey,
                        expected = submission,
                        result = AcceptedPromptResult(
                            submission = submission,
                            runId = runID,
                            continuedSessionId = continuedSessionID,
                            workspaceRevision = workspaceRevision,
                        ),
                    )
                    Result.success(
                        workDataOf(
                            KEY_RESULT_STATUS to STATUS_ACCEPTED,
                            KEY_RUN_ID to runID,
                            KEY_CONTINUED_SESSION_ID to continuedSessionID,
                            KEY_WORKSPACE_REVISION to workspaceRevision,
                        )
                    )
                }
            }
        } catch (error: RemoteCommandException) {
            val message = error.message ?: "The target device rejected this message"
            if (!isPermanentRemoteCommandRejection(error)) {
                updatePersistedSubmission(app, contextKey, submission) {
                    it.copy(
                        status = PendingPromptSubmission.STATUS_RETRYING,
                        error = "",
                    )
                }
                return Result.retry()
            }
            updatePersistedSubmission(app, contextKey, submission) {
                it.copy(
                    status = PendingPromptSubmission.STATUS_FAILED,
                    error = message,
                    retryRequiresNewCommandId = true,
                )
            }
            Result.failure(
                failureData(
                    message,
                    requiresNewCommandID = true,
                )
            )
        } catch (error: IllegalArgumentException) {
            val message = error.message ?: "The pending message is invalid"
            updatePersistedSubmission(app, contextKey, submission) {
                it.copy(
                    status = PendingPromptSubmission.STATUS_FAILED,
                    error = message,
                    retryRequiresNewCommandId = true,
                )
            }
            Result.failure(failureData(message, requiresNewCommandID = true))
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            updatePersistedSubmission(app, contextKey, submission) {
                it.copy(
                    status = PendingPromptSubmission.STATUS_RETRYING,
                    error = "",
                )
            }
            Result.retry()
        }
    }

    private fun updatePersistedSubmission(
        app: PCRRemoteApplication,
        contextKey: String,
        expected: PendingPromptSubmission,
        transform: (PendingPromptSubmission) -> PendingPromptSubmission,
    ) {
        app.mobileStateStore.update { state ->
            val current = state.promptOutbox[contextKey]
            if (current?.id != expected.id || current.commandId != expected.commandId) {
                state
            } else {
                state.copy(promptOutbox = state.promptOutbox + (contextKey to transform(current)))
            }
        }
    }

    private fun recordAcceptedSubmission(
        app: PCRRemoteApplication,
        contextKey: String,
        expected: PendingPromptSubmission,
        result: AcceptedPromptResult,
    ) {
        app.mobileStateStore.update { state ->
            state.recordingAcceptedPromptResult(contextKey, expected, result)
        }
    }

    private fun failureData(
        message: String,
        requiresNewCommandID: Boolean = false,
    ): Data = workDataOf(
        KEY_RESULT_STATUS to STATUS_FAILED,
        KEY_MESSAGE to message.take(1_000),
        KEY_REQUIRES_NEW_COMMAND_ID to requiresNewCommandID,
    )

    companion object {
        const val KEY_CONTEXT = "context"
        const val KEY_SUBMISSION_ID = "submission_id"
        const val KEY_COMMAND_ID = "command_id"
        const val KEY_RESULT_STATUS = "result_status"
        const val KEY_MESSAGE = "message"
        const val KEY_RUN_ID = "run_id"
        const val KEY_CONTINUED_SESSION_ID = "continued_session_id"
        const val KEY_WORKSPACE_REVISION = "workspace_revision"
        const val KEY_REQUIRES_NEW_COMMAND_ID = "requires_new_command_id"

        const val STATUS_ACCEPTED = "accepted"
        const val STATUS_CONFIRMATION_REQUIRED = "confirmation_required"
        const val STATUS_FAILED = "failed"
        const val STATUS_CANCELLED = "cancelled"

        fun contextKey(submission: PendingPromptSubmission): String =
            "${submission.macId}\u001f${submission.workspaceId}"
    }
}
