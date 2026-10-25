package com.parallelcodexrunner.remote.data

import android.content.Context
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
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

internal data class FollowUpQueueContext(
    val macID: String,
    val workspaceID: String,
    val runID: String,
) {
    val key: String
        get() = "$macID\u001f$workspaceID\u001f$runID"

    companion object {
        fun parse(value: String): FollowUpQueueContext? {
            val parts = value.split('\u001f', limit = 3)
            if (parts.size != 3 || parts.any(String::isBlank)) return null
            return FollowUpQueueContext(parts[0], parts[1], parts[2])
        }
    }
}

internal object FollowUpWorkerSerializers {
    private val locks = Array(64) { Mutex() }

    suspend fun <T> run(contextKey: String, block: suspend () -> T): T {
        val lock = locks[Math.floorMod(contextKey.hashCode(), locks.size)]
        lock.lock()
        return try {
            block()
        } finally {
            lock.unlock()
        }
    }
}

internal fun pendingDaemonFollowUps(
    remoteItemIDs: Set<String>,
    localQueue: List<PersistedQueuedFollowUp>,
): List<PersistedQueuedFollowUp> = localQueue.filter { item ->
    item.daemonAcknowledged != true && !item.paused && item.id !in remoteItemIDs
}

private fun RemoteFollowUpQueueItem.asPersistedQueuedFollowUp(): PersistedQueuedFollowUp =
    PersistedQueuedFollowUp(
        id = id,
        prompt = prompt,
        settings = settings.asRunSettings(),
        attempt = attempt.coerceAtLeast(0),
        failureCount = failureCount.coerceAtLeast(0),
        retryAtMillis = retryAtEpochMillis ?: 0L,
        lastError = lastError,
        paused = paused,
        daemonAcknowledged = true,
    )

class FollowUpQueueScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val workManager by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {
        WorkManager.getInstance(applicationContext)
    }

    fun enqueue(
        macID: String,
        workspaceID: String,
        runID: String,
        notBeforeMillis: Long = 0L,
        wake: Boolean = false,
    ) {
        val context = FollowUpQueueContext(macID, workspaceID, runID)
        if (macID.isBlank() || workspaceID.isBlank() || runID.isBlank()) return
        val now = System.currentTimeMillis()
        val scheduleSlot = when {
            notBeforeMillis > now -> notBeforeMillis
            wake -> now
            else -> 0L
        }
        val request = OneTimeWorkRequestBuilder<FollowUpQueueWorker>()
            .setInputData(workDataOf(FollowUpQueueWorker.KEY_CONTEXT to context.key))
            .setInitialDelay((scheduleSlot - now).coerceAtLeast(0L), TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS)
            .addTag(TAG_ALL)
            .addTag(macTag(macID))
            .addTag(workspaceTag(macID, workspaceID))
            .addTag(contextTag(context.key))
            .build()
        workManager.enqueueUniqueWork(
            workName(context.key, scheduleSlot),
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun cancelContext(macID: String, workspaceID: String, runID: String) =
        workManager.cancelAllWorkByTag(
            contextTag(FollowUpQueueContext(macID, workspaceID, runID).key)
        )

    fun cancelForMac(macID: String) = workManager.cancelAllWorkByTag(macTag(macID))

    fun cancelForWorkspace(macID: String, workspaceID: String) =
        workManager.cancelAllWorkByTag(workspaceTag(macID, workspaceID))

    fun cancelAll() = workManager.cancelAllWorkByTag(TAG_ALL)

    private companion object {
        const val TAG_ALL = "pcr-follow-up"

        fun stableID(value: String): String =
            UUID.nameUUIDFromBytes(value.toByteArray(Charsets.UTF_8)).toString()

        fun workName(contextKey: String, scheduleSlot: Long) =
            "pcr-follow-up-${stableID(contextKey)}-$scheduleSlot"

        fun contextTag(contextKey: String) = "pcr-follow-up-context-${stableID(contextKey)}"

        fun macTag(macID: String) = "pcr-follow-up-mac-${stableID(macID)}"

        fun workspaceTag(macID: String, workspaceID: String): String {
            return "pcr-follow-up-workspace-" + stableID("$macID\u001f$workspaceID")
        }
    }
}

class FollowUpQueueWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? PCRRemoteApplication ?: return Result.failure()
        val context = FollowUpQueueContext.parse(
            inputData.getString(KEY_CONTEXT).orEmpty()
        ) ?: return Result.failure()
        return FollowUpWorkerSerializers.run(context.key) {
            dispatchNext(app, context)
        }
    }

    private suspend fun dispatchNext(
        app: PCRRemoteApplication,
        context: FollowUpQueueContext,
    ): Result {
        if (app.uiForeground) return Result.retry()

        val initialState = app.mobileStateStore.load()
        var queue = initialState.followUpQueueItems[context.key].orEmpty()
        var head = queue.firstOrNull() ?: return Result.success()
        if (head.paused) return Result.success()
        val now = System.currentTimeMillis()
        if (head.retryAtMillis > now) {
            app.followUpQueueScheduler.enqueue(
                context.macID,
                context.workspaceID,
                context.runID,
                notBeforeMillis = head.retryAtMillis,
            )
            return Result.success()
        }

        return try {
            val macs = app.repository.macs()
            val mac = macs.firstOrNull { it.deviceId == context.macID }
            if (mac == null) {
                // A successful refresh without this Mac means it was removed, not that
                // the network is temporarily unavailable. Retrying would keep this
                // stale queue alive forever while the app remains closed.
                app.mobileStateStore.update { it.withoutMac(context.macID) }
                return Result.success()
            }
            val capabilities = app.repository.systemCapabilities(mac)
            if (capabilities.supports("followups.replace")) {
                return handOffToDaemon(
                    app = app,
                    mac = mac,
                    context = context,
                    state = initialState,
                    localQueue = queue,
                    supportsAtomicEnqueue = capabilities.supports("followups.enqueue"),
                )
            }
            val workspace = app.repository.workspaces(mac).workspaces.firstOrNull {
                it.workspaceId == context.workspaceID
            } ?: return pauseQueue(
                app,
                context,
                head.id,
                "This workspace was removed from the target device",
            )
            val latestRunID = workspace.lastRunId.trim()
            if (latestRunID.isBlank()) return Result.retry()
            if (latestRunID != context.runID && !head.dispatching) {
                val nextContext = context.copy(runID = latestRunID)
                var moved = false
                app.mobileStateStore.update { state ->
                    val current = state.followUpQueueItems[context.key].orEmpty()
                    if (current.firstOrNull()?.id != head.id) return@update state
                    val queues = state.followUpQueueItems.toMutableMap()
                    queues.remove(context.key)
                    queues[nextContext.key] = mergeQueues(
                        current,
                        queues[nextContext.key].orEmpty(),
                    )
                    val deadlines = state.followUpDeadlines.toMutableMap()
                    deadlines.remove(context.key)?.let { deadline ->
                        deadlines.putIfAbsent(nextContext.key, deadline)
                    }
                    moved = true
                    state.copy(
                        followUpQueueItems = queues,
                        followUpDeadlines = deadlines,
                    )
                }
                if (moved) {
                    app.followUpQueueScheduler.enqueue(
                        nextContext.macID,
                        nextContext.workspaceID,
                        nextContext.runID,
                        wake = true,
                    )
                }
                return Result.success()
            }

            val run = app.repository.run(mac, context.runID)
            val runStatus = run["status"]?.jsonPrimitive?.contentOrNull.orEmpty()
            if (!isTerminalRunStatusValue(runStatus)) return Result.retry()
            if (runStatus.lowercase() in UNSAFE_CONTINUATION_STATUSES) {
                return pauseQueue(
                    app,
                    context,
                    head.id,
                    "The target device could not safely finish workspace sync",
                )
            }

            val settings = head.settings
                ?: workspace.settings?.normalized()
                ?: RemoteRunSettings()
            val savedDispatch = head.dispatch
            val finalizedAgent = run["finalized_agent"]?.jsonPrimitive?.intOrNull
            val dispatch = if (
                savedDispatch == null ||
                (savedDispatch.mode == PersistedFollowUpDispatch.MODE_START &&
                    finalizedAgent != null)
            ) buildDispatch(
                state = initialState,
                context = context,
                run = run,
                settings = settings,
            ) else savedDispatch
            if (dispatch == null) return pauseQueue(
                app,
                context,
                head.id,
                "No Agent is available to continue this queued message",
            )
            val currentDeadline = app.mobileStateStore.load().followUpDeadlines[context.key]
            val deadline = currentDeadline ?: (
                now + if (head.dispatching) RECOVERY_DELAY_MILLIS
                else dispatch.settings.followUpDelay.coerceAtLeast(0) * 1_000L
            ).also { value ->
                app.mobileStateStore.update { state ->
                    if (state.followUpQueueItems[context.key]?.firstOrNull()?.id != head.id ||
                        context.key in state.followUpDeadlines
                    ) state else state.copy(
                        followUpDeadlines = state.followUpDeadlines + (context.key to value)
                    )
                }
            }
            if (deadline > System.currentTimeMillis()) {
                app.followUpQueueScheduler.enqueue(
                    context.macID,
                    context.workspaceID,
                    context.runID,
                    notBeforeMillis = deadline,
                )
                return Result.success()
            }
            if (app.uiForeground) return Result.retry()

            var claimed: PersistedQueuedFollowUp? = null
            app.mobileStateStore.update { state ->
                val current = state.followUpQueueItems[context.key].orEmpty()
                val currentHead = current.firstOrNull()
                if (currentHead?.id != head.id || currentHead.paused ||
                    currentHead.retryAtMillis > System.currentTimeMillis()
                ) return@update state
                val claimedHead = currentHead.copy(
                    dispatching = true,
                    dispatch = dispatch,
                    retryAtMillis = 0L,
                )
                claimed = claimedHead
                state.copy(
                    followUpQueueItems = state.followUpQueueItems + (
                        context.key to (listOf(claimedHead) + current.drop(1))
                    ),
                    followUpDeadlines = state.followUpDeadlines - context.key,
                )
            }
            head = claimed ?: return Result.success()
            val claimedDispatch = requireNotNull(head.dispatch)
            val response = if (claimedDispatch.mode == PersistedFollowUpDispatch.MODE_CONTINUE) {
                app.repository.continueRun(
                    mac = mac,
                    runId = claimedDispatch.sourceRunId,
                    agent = requireNotNull(claimedDispatch.sourceAgent),
                    prompt = head.prompt,
                    agents = claimedDispatch.settings.agents,
                    synthesisAgents = claimedDispatch.settings.synthesisAgents,
                    settings = claimedDispatch.settings,
                    confirmLargeRun = true,
                    commandId = head.commandId,
                )
            } else {
                app.repository.startRun(
                    mac = mac,
                    workspaceId = context.workspaceID,
                    prompt = head.prompt,
                    agents = claimedDispatch.settings.agents,
                    synthesisAgents = claimedDispatch.settings.synthesisAgents,
                    settings = claimedDispatch.settings,
                    resumeSessionId = claimedDispatch.resumeSessionId,
                    confirmLargeRun = true,
                    commandId = head.commandId,
                )
            }
            val nextRunID = response["run_id"]?.jsonPrimitive?.contentOrNull.orEmpty()
            if (nextRunID.isBlank()) error("Target machine did not return a run id")
            val nextContext = context.copy(runID = nextRunID)
            var remaining: List<PersistedQueuedFollowUp> = emptyList()
            app.mobileStateStore.update { state ->
                val current = state.followUpQueueItems[context.key].orEmpty()
                if (current.firstOrNull()?.id != head.id) return@update state
                remaining = current.drop(1)
                val queues = state.followUpQueueItems.toMutableMap()
                queues.remove(context.key)
                if (remaining.isNotEmpty()) {
                    queues[nextContext.key] = mergeQueues(
                        remaining,
                        queues[nextContext.key].orEmpty(),
                    )
                }
                state.copy(
                    followUpQueueItems = queues,
                    followUpDeadlines = state.followUpDeadlines - context.key,
                )
            }
            app.runCompletionScheduler.enqueue(
                macID = context.macID,
                workspaceID = context.workspaceID,
                workspaceName = workspace.name,
                runID = nextRunID,
            )
            if (remaining.isNotEmpty()) {
                app.followUpQueueScheduler.enqueue(
                    nextContext.macID,
                    nextContext.workspaceID,
                    nextContext.runID,
                )
            }
            Result.success()
        } catch (error: CancellationException) {
            throw error
        } catch (error: RemoteCommandException) {
            recordFailure(
                app,
                context,
                head.id,
                remoteRejected = isPermanentRemoteCommandRejection(error),
                rawMessage = error.message.orEmpty(),
            )
        } catch (error: Throwable) {
            recordFailure(app, context, head.id, remoteRejected = false, error.message.orEmpty())
        }
    }

    private suspend fun handOffToDaemon(
        app: PCRRemoteApplication,
        mac: RelayDevice,
        context: FollowUpQueueContext,
        state: PersistedMobileState,
        localQueue: List<PersistedQueuedFollowUp>,
        supportsAtomicEnqueue: Boolean,
    ): Result {
        val existing = app.repository.followUpQueue(
            mac = mac,
            workspaceId = context.workspaceID,
        )
        var remote = existing.queue
        val acknowledged = mutableSetOf<String>()
        if (remote == null) {
            val pending = pendingDaemonFollowUps(emptySet(), localQueue)
            if (pending.isNotEmpty()) {
                if (supportsAtomicEnqueue) {
                    pending.forEach { item ->
                        remote = app.repository.enqueueFollowUp(mac, context.workspaceID,
                            context.runID, item, null).queue ?: remote
                        acknowledged += item.id
                    }
                } else remote = app.repository.replaceFollowUpQueue(
                    mac = mac,
                    workspaceId = context.workspaceID,
                    runId = context.runID,
                    items = pending,
                    selectedAgent = state.selectedAgents[context.key],
                    resetCountdown = false,
                ).queue
            }
        } else {
            val currentRemote = remote
            val pending = pendingDaemonFollowUps(
                currentRemote.items.mapTo(mutableSetOf(), RemoteFollowUpQueueItem::id)
                    + listOfNotNull(currentRemote.lastCompletedItemId),
                localQueue,
            )
            if (pending.isNotEmpty()) {
                if (supportsAtomicEnqueue) {
                    pending.forEach { item ->
                        remote = app.repository.enqueueFollowUp(
                            mac = mac,
                            workspaceId = context.workspaceID,
                            runId = context.runID,
                            item = item,
                            selectedAgent = remote?.selectedAgent
                                ?: state.selectedAgents[context.key],
                        ).queue ?: remote
                        acknowledged += item.id
                    }
                } else {
                    val merged = currentRemote.items.map(
                        RemoteFollowUpQueueItem::asPersistedQueuedFollowUp
                    ) + pending
                    remote = app.repository.replaceFollowUpQueue(
                        mac = mac,
                        workspaceId = context.workspaceID,
                        runId = context.runID,
                        items = merged,
                        selectedAgent = currentRemote.selectedAgent
                            ?: state.selectedAgents[context.key],
                        resetCountdown = false,
                    ).queue
                }
            }
        }
        val synchronizedRemote = remote
        app.mobileStateStore.update { current ->
            // Foreground reconciliation now owns the local outbox. Its next
            // idempotent retry will retrieve a fresh daemon receipt.
            if (app.uiForeground) return@update current
            val queues = current.followUpQueueItems.toMutableMap()
            val deadlines = current.followUpDeadlines.toMutableMap()
            queues.keys.mapNotNull(FollowUpQueueContext::parse)
                .filter {
                    it.macID == context.macID && it.workspaceID == context.workspaceID
                }
                .forEach {
                    val pending = reconcileFollowUpItems(emptyList(), queues[it.key].orEmpty(),
                        acknowledged, synchronizedRemote?.lastCompletedItemId)
                    if (pending.isEmpty()) queues.remove(it.key) else queues[it.key] = pending
                    deadlines.remove(it.key)
                }
            if (synchronizedRemote != null &&
                synchronizedRemote.items.isNotEmpty() &&
                synchronizedRemote.runId.isNotBlank()
            ) {
                val remoteContext = context.copy(runID = synchronizedRemote.runId)
                queues[remoteContext.key] = reconcileFollowUpItems(
                    synchronizedRemote.items.map(RemoteFollowUpQueueItem::asPersistedQueuedFollowUp),
                    queues[remoteContext.key].orEmpty(), acknowledged,
                    synchronizedRemote.lastCompletedItemId)
                synchronizedRemote.deadlineEpochMillis?.let { deadline ->
                    deadlines[remoteContext.key] = deadline
                }
            }
            current.copy(
                followUpQueueItems = queues,
                followUpDeadlines = deadlines,
            )
        }
        val remaining = app.mobileStateStore.load().followUpQueueItems[context.key].orEmpty()
        if (remaining.any { it.daemonAcknowledged != true && !it.paused }) return Result.retry()
        return Result.success()
    }

    private fun buildDispatch(
        state: PersistedMobileState,
        context: FollowUpQueueContext,
        run: kotlinx.serialization.json.JsonObject,
        settings: RemoteRunSettings,
    ): PersistedFollowUpDispatch? {
        val eligible = run["agents"]?.jsonArray.orEmpty().mapNotNull { raw ->
            val agent = raw.jsonObject
            val number = agent["agent"]?.jsonPrimitive?.intOrNull
                ?: agent["idx"]?.jsonPrimitive?.intOrNull
                ?: return@mapNotNull null
            number.takeIf {
                agent["can_continue"]?.jsonPrimitive?.booleanOrNull == true
            }
        }
        val finalizedAgent = run["finalized_agent"]?.jsonPrimitive?.intOrNull
        val recommended = run["recommended_agent"]?.jsonPrimitive?.intOrNull
        val sourceAgent = finalizedAgent?.takeIf { it in eligible }
            ?: state.selectedAgents[context.key]?.takeIf { it in eligible }
            ?: recommended?.takeIf { it in eligible }
            ?: eligible.firstOrNull()
            ?: return null
        return PersistedFollowUpDispatch(
            mode = PersistedFollowUpDispatch.MODE_CONTINUE,
            sourceRunId = context.runID,
            sourceAgent = sourceAgent,
            settings = settings,
        )
    }

    private fun pauseQueue(
        app: PCRRemoteApplication,
        context: FollowUpQueueContext,
        itemID: String,
        message: String,
    ): Result {
        app.mobileStateStore.update { state ->
            val queue = state.followUpQueueItems[context.key].orEmpty()
            if (queue.firstOrNull()?.id != itemID) return@update state
            state.copy(
                followUpQueueItems = state.followUpQueueItems + (
                    context.key to (
                        listOf(queue.first().copy(paused = true, lastError = message)) +
                            queue.drop(1)
                    )
                ),
                followUpDeadlines = state.followUpDeadlines - context.key,
            )
        }
        return Result.success()
    }

    private fun recordFailure(
        app: PCRRemoteApplication,
        context: FollowUpQueueContext,
        itemID: String,
        remoteRejected: Boolean,
        rawMessage: String,
    ): Result {
        var retryAt = 0L
        var paused = false
        app.mobileStateStore.update { state ->
            val queue = state.followUpQueueItems[context.key].orEmpty()
            val head = queue.firstOrNull()
            if (head?.id != itemID) return@update state
            val failures = head.failureCount + 1
            val remoteRejections = head.attempt + if (remoteRejected) 1 else 0
            paused = remoteRejected && remoteRejections >= MAX_REMOTE_REJECTIONS
            retryAt = if (paused) 0L else {
                System.currentTimeMillis() + retryDelayMillis(failures)
            }
            val retry = head.copy(
                attempt = remoteRejections,
                dispatching = !remoteRejected,
                failureCount = failures,
                retryAtMillis = retryAt,
                lastError = rawMessage.trim().take(500).ifBlank {
                    "The queued message could not be sent yet"
                },
                paused = paused,
            )
            state.copy(
                followUpQueueItems = state.followUpQueueItems + (
                    context.key to (listOf(retry) + queue.drop(1))
                ),
                followUpDeadlines = if (paused) {
                    state.followUpDeadlines - context.key
                } else {
                    state.followUpDeadlines + (context.key to retryAt)
                },
            )
        }
        if (!paused && retryAt > 0L) {
            app.followUpQueueScheduler.enqueue(
                context.macID,
                context.workspaceID,
                context.runID,
                notBeforeMillis = retryAt,
            )
        }
        return Result.success()
    }

    companion object {
        const val KEY_CONTEXT = "context"
        private const val RECOVERY_DELAY_MILLIS = 5_000L
        private const val MAX_REMOTE_REJECTIONS = 3
        private val UNSAFE_CONTINUATION_STATUSES = setOf("sync_ambiguous", "cleanup_failed")

        private fun retryDelayMillis(failureCount: Int): Long {
            val exponent = (failureCount.coerceAtLeast(1) - 1).coerceAtMost(6)
            return (5_000L shl exponent).coerceAtMost(300_000L)
        }

        private fun mergeQueues(
            queued: List<PersistedQueuedFollowUp>,
            existing: List<PersistedQueuedFollowUp>,
        ): List<PersistedQueuedFollowUp> {
            val knownIDs = queued.mapTo(mutableSetOf(), PersistedQueuedFollowUp::id)
            return queued + existing.filterNot { it.id in knownIDs }
        }
    }
}
