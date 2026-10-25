package com.parallelcodexrunner.remote.data

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.parallelcodexrunner.remote.MainActivity
import com.parallelcodexrunner.remote.PCRRemoteApplication
import com.parallelcodexrunner.remote.R
import com.parallelcodexrunner.remote.ui.AppLanguage
import com.parallelcodexrunner.remote.ui.text
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class RunCompletionScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val workManager by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        WorkManager.getInstance(applicationContext)
    }

    fun enqueue(macID: String, workspaceID: String, workspaceName: String, runID: String) {
        if (macID.isBlank() || workspaceID.isBlank() || runID.isBlank()) return
        val request = OneTimeWorkRequestBuilder<RunCompletionWorker>()
            .setInputData(
                workDataOf(
                    RunCompletionWorker.KEY_MAC_ID to macID,
                    RunCompletionWorker.KEY_WORKSPACE_ID to workspaceID,
                    RunCompletionWorker.KEY_WORKSPACE_NAME to workspaceName,
                    RunCompletionWorker.KEY_RUN_ID to runID,
                )
            )
            .setInitialDelay(15, TimeUnit.SECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS)
            .addTag(TAG_ALL)
            .addTag(macTag(macID))
            .addTag(workspaceTag(macID, workspaceID))
            .build()
        workManager.enqueueUniqueWork(
            workName(macID, workspaceID, runID),
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun cancelForMac(macID: String) = workManager.cancelAllWorkByTag(macTag(macID))

    fun cancelForWorkspace(macID: String, workspaceID: String) =
        workManager.cancelAllWorkByTag(workspaceTag(macID, workspaceID))

    fun cancel(macID: String, workspaceID: String, runID: String) =
        workManager.cancelUniqueWork(workName(macID, workspaceID, runID))

    fun cancelAll() = workManager.cancelAllWorkByTag(TAG_ALL)

    private companion object {
        const val TAG_ALL = "pcr-run-completion"

        fun workName(macID: String, workspaceID: String, runID: String) =
            "pcr-completion-$macID-$workspaceID-$runID"

        fun macTag(macID: String) = "pcr-completion-mac-$macID"

        fun workspaceTag(macID: String, workspaceID: String) =
            "pcr-completion-workspace-$macID-$workspaceID"
    }
}

class RunCompletionWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? PCRRemoteApplication ?: return Result.failure()
        val macID = inputData.getString(KEY_MAC_ID).orEmpty()
        val workspaceName = inputData.getString(KEY_WORKSPACE_NAME).orEmpty()
        val runID = inputData.getString(KEY_RUN_ID).orEmpty()
        if (macID.isBlank() || runID.isBlank()) return Result.failure()

        return try {
            val mac = app.repository.macs().firstOrNull { it.deviceId == macID }
                // A successful device refresh that no longer contains this Mac
                // means it was removed. Retrying cannot make this old watcher
                // useful again and needlessly wakes the app for hours.
                ?: return Result.success()
            repeat(POLLS_PER_ATTEMPT) {
                val run = app.repository.run(mac, runID)
                val failed = completedRunFailedAgentCount(run)
                if (failed != null) {
                    val workspaceID = inputData.getString(KEY_WORKSPACE_ID).orEmpty()
                    app.mobileStateStore.update { state ->
                        state.copy(
                            unreadRunCompletions = state.unreadRunCompletions +
                                runCompletionPreferenceKey(macID, workspaceID, runID),
                        )
                    }
                    app.followUpQueueScheduler.enqueue(
                        macID = macID,
                        workspaceID = workspaceID,
                        runID = runID,
                        wake = true,
                    )
                    // Completion monitoring is successful even when Android or
                    // an OEM notification service refuses to post a banner.
                    // A notification failure must not restart hours of polling.
                    runCatching {
                        RunCompletionNotifications.show(
                            applicationContext,
                            macID = macID,
                            workspaceID = workspaceID,
                            runID = runID,
                            workspaceName = workspaceName,
                            failedAgents = failed,
                        )
                    }
                    return Result.success()
                }
                delay(POLL_INTERVAL_MILLIS)
            }
            Result.retry()
        } catch (error: CancellationException) {
            throw error
        } catch (error: RemoteCommandException) {
            if (error.code in PERMANENT_REMOTE_ERRORS) Result.failure()
            else Result.retry()
        } catch (_: Throwable) {
            Result.retry()
        }
    }

    companion object {
        const val KEY_MAC_ID = "mac_id"
        const val KEY_WORKSPACE_ID = "workspace_id"
        const val KEY_WORKSPACE_NAME = "workspace_name"
        const val KEY_RUN_ID = "run_id"

        private const val POLL_INTERVAL_MILLIS = 30_000L
        private const val POLLS_PER_ATTEMPT = 16
        private val PERMANENT_REMOTE_ERRORS = setOf(
            "run_not_found",
            "unknown_run",
            "unauthorized",
            "unsupported_action",
            "incompatible_protocol",
        )
    }
}

internal fun runCompletionPreferenceKey(
    macID: String,
    workspaceID: String,
    runID: String,
): String = "$macID\u001f$workspaceID\u001f$runID"

internal fun runCompletionNotificationTag(
    macID: String,
    workspaceID: String,
    runID: String,
): String = "pcr-run-completed-$macID-$workspaceID-$runID"

internal fun completionNotificationPermissionSatisfied(
    sdkInt: Int,
    permissionGranted: Boolean,
): Boolean = sdkInt < Build.VERSION_CODES.TIRAMISU || permissionGranted

internal fun runCompletionNotificationText(
    failedAgents: Int,
    language: AppLanguage,
): String = if (failedAgents > 0) {
    language.text(
        "所有 Agent 已结束，其中 $failedAgents 个未完成",
        "All Agents finished; $failedAgents did not complete",
    )
} else {
    language.text("所有 Agent 已完成", "All Agents completed")
}

internal fun completedRunFailedAgentCount(run: kotlinx.serialization.json.JsonObject): Int? {
    val status = run["status"]?.jsonPrimitive?.contentOrNull.orEmpty().lowercase()
    if (status !in TERMINAL_RUN_STATUSES) return null
    val agents = run["agents"] as? JsonArray
    val agentStatuses = agents.orEmpty().map { item ->
        item.jsonObject["status"]?.jsonPrimitive?.contentOrNull
            .orEmpty()
            .lowercase()
    }
    if (agentStatuses.isEmpty() || agentStatuses.any { it !in TERMINAL_AGENT_STATUSES }) {
        return null
    }
    return agentStatuses.count { it in UNSUCCESSFUL_AGENT_STATUSES }
}

object RunCompletionNotifications {
    private const val CHANNEL_ID = "run-completions"

    fun createChannel(context: Context) {
        val language = AppLanguage.load(context)
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                language.text("任务完成", "Task completion"),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = language.text(
                    "所有 Agent 结束后提醒",
                    "Notifies you after every Agent has finished",
                )
            }
        )
    }

    @SuppressLint("InlinedApi")
    fun show(
        context: Context,
        macID: String,
        workspaceID: String,
        runID: String,
        workspaceName: String,
        failedAgents: Int,
    ) {
        val language = AppLanguage.load(context)
        val permissionGranted = context.checkSelfPermission(
            Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
        if (!completionNotificationPermissionSatisfied(Build.VERSION.SDK_INT, permissionGranted)) {
            return
        }
        createChannel(context)
        val openApp = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            data = Uri.Builder()
                .scheme("pcr")
                .authority("run")
                .appendQueryParameter("mac_id", macID)
                .appendQueryParameter("workspace_id", workspaceID)
                .appendQueryParameter("run_id", runID)
                .build()
        }
        val notificationTag = runCompletionNotificationTag(macID, workspaceID, runID)
        val pendingIntent = PendingIntent.getActivity(
            context,
            notificationTag.hashCode(),
            openApp,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val title = workspaceName.trim().ifEmpty { "Parallel Codex" }
        val text = runCompletionNotificationText(failedAgents, language)
        val notification = android.app.Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setCategory(android.app.Notification.CATEGORY_STATUS)
            .build()
        context.getSystemService(NotificationManager::class.java)
            .notify(notificationTag, 0, notification)
    }
}
