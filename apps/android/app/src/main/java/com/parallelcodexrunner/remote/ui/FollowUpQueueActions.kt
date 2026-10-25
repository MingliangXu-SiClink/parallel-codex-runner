package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.PersistedFollowUpDispatch
import com.parallelcodexrunner.remote.data.PersistedQueuedFollowUp
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteFollowUpQueue
import com.parallelcodexrunner.remote.data.RemoteFollowUpQueueItem
import com.parallelcodexrunner.remote.data.RemoteFollowUpQueueResponse
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.isPermanentRemoteCommandRejection
import com.parallelcodexrunner.remote.data.normalized
import com.parallelcodexrunner.remote.data.newestFollowUpSnapshot
import com.parallelcodexrunner.remote.data.reconcileFollowUpItems
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.UUID

private const val FOLLOW_UP_REPLACE_ACTION = "followups.replace"
private const val FOLLOW_UP_ENQUEUE_ACTION = "followups.enqueue"

internal fun MainViewModel.cachedDaemonOwnsFollowUpQueue(macId: String): Boolean? =
    repository.cachedSupports(macId, FOLLOW_UP_REPLACE_ACTION)

private suspend fun MainViewModel.daemonOwnsFollowUpQueue(mac: RelayDevice): Boolean =
    repository.systemCapabilities(mac).supports(FOLLOW_UP_REPLACE_ACTION)

private fun MainViewModel.remoteFollowUpSelection(snapshot: MainUiState): Int? {
    if (!isTerminalRunStatus(snapshot.runStatus)) return null
    val eligible = snapshot.agentSummaries.filter(AgentSummary::canContinue)
    return snapshot.finalizedAgent?.takeIf { finalized ->
        eligible.any { it.number == finalized }
    } ?: snapshot.selectedAgent?.takeIf { selected ->
        eligible.any { it.number == selected }
    }
}

private fun RemoteFollowUpQueueItem.asPersistedItem(): PersistedQueuedFollowUp =
    PersistedQueuedFollowUp(
        id = id,
        prompt = prompt,
        settings = settings.asRunSettings(),
        attempt = attempt.coerceAtLeast(0),
        dispatching = false,
        failureCount = failureCount.coerceAtLeast(0),
        retryAtMillis = retryAtEpochMillis ?: 0L,
        lastError = lastError,
        paused = paused,
        daemonAcknowledged = true,
    )

private fun MainViewModel.persistedQueueForWorkspace(
    macId: String,
    workspaceId: String,
    preferredRunId: String?,
): Pair<String, List<PersistedQueuedFollowUp>>? {
    val candidates = followUpQueuesByContext.mapNotNull { (key, items) ->
        val context = parseFollowUpContext(key) ?: return@mapNotNull null
        if (context.macId != macId || context.workspaceId != workspaceId || items.isEmpty()) {
            return@mapNotNull null
        }
        context.runId to items
    }
    return candidates.firstOrNull { it.first == preferredRunId } ?: candidates.firstOrNull()
}

internal fun MainViewModel.applyDaemonFollowUpQueue(
    macId: String,
    workspaceId: String,
    incoming: RemoteFollowUpQueue?,
    acknowledged: Set<String> = emptySet(),
) {
    val snapshotKey = workspaceSettingsKey(macId, workspaceId)
    val remote = newestFollowUpSnapshot(incoming, daemonFollowUpSnapshots[snapshotKey])
    if (remote != null) daemonFollowUpSnapshots[snapshotKey] = remote
    val savedQueues = followUpQueuesByContext.toMap()
    val removedContexts = followUpQueuesByContext.keys.mapNotNull(::parseFollowUpContext)
        .filter { it.macId == macId && it.workspaceId == workspaceId }
    removedContexts.forEach { context ->
        followUpQueuesByContext.remove(context.key)
        followUpDeadlinesByContext.remove(context.key)
    }

    removedContexts.filter { remote != null && it.runId != remote.runId }.forEach { context ->
        val pending = reconcileFollowUpItems(emptyList(), savedQueues[context.key].orEmpty(),
            acknowledged, remote?.lastCompletedItemId)
        if (pending.isNotEmpty()) followUpQueuesByContext[context.key] = pending
    }
    val remoteRunId = remote?.runId ?: removedContexts.firstOrNull()?.runId.orEmpty()
    val local = removedContexts.filter { it.runId == remoteRunId }
        .flatMap { savedQueues[it.key].orEmpty() }
    val items = reconcileFollowUpItems(remote?.items.orEmpty().map(RemoteFollowUpQueueItem::asPersistedItem),
        local, acknowledged, remote?.lastCompletedItemId)
    val queueKey = remoteRunId.takeIf { it.isNotBlank() && items.isNotEmpty() }?.let {
        followUpQueueKey(macId, workspaceId, it)
    }
    if (queueKey != null) {
        followUpQueuesByContext[queueKey] = items
        remote?.deadlineEpochMillis?.let { deadline ->
            followUpDeadlinesByContext[queueKey] = deadline
        }
    }
    removedContexts.forEach { context ->
        if (followUpQueuesByContext[context.key].orEmpty().none { it.daemonAcknowledged != true }) {
            app.followUpQueueScheduler.cancelContext(macId, workspaceId, context.runId)
        }
    }
    scheduleMobileStateSave()

    if (state.value.selectedMacId != macId ||
        state.value.selectedWorkspaceId != workspaceId
    ) return
    val visiblePending = state.value.visibleRunId?.takeIf { it != remoteRunId }?.let {
        followUpQueuesByContext[followUpQueueKey(macId, workspaceId, it)]
    }.orEmpty()
    val visibleItems = visiblePending.ifEmpty { items }
    queuedRunId = if (visiblePending.isNotEmpty()) state.value.visibleRunId else remoteRunId.takeIf { items.isNotEmpty() }
    mutableState.update { current ->
        current.copy(
            workspaceQueueCounts = workspaceQueueCounts(macId, current.workspaces),
            workspacePromptStates = workspacePromptStates(macId, current.workspaces),
            followUpQueue = visibleItems.map(PersistedQueuedFollowUp::asUiItem),
            followUpSecondsRemaining = remote?.deadlineEpochMillis?.let { deadline ->
                followUpCountdownSeconds(deadline, System.currentTimeMillis())
            }?.takeIf { items.isNotEmpty() && visiblePending.isEmpty() },
        )
    }
    if (remote?.runId == state.value.visibleRunId &&
        remote?.selectionSource in setOf("manual", "automatic") &&
        isTerminalRunStatus(state.value.runStatus) && state.value.finalizedAgent == null &&
        state.value.pendingAgentSelection == null
    ) {
        remote?.selectedAgent?.takeIf { it != state.value.selectedAgent }?.let {
            selectAgent(it, userInitiated = false)
        }
    }
}

internal suspend fun MainViewModel.refreshDaemonFollowUpQueue(
    mac: RelayDevice,
    workspaceId: String,
    fallbackRunId: String?,
    migrateLocalQueue: Boolean = true,
): String? {
    if (!daemonOwnsFollowUpQueue(mac)) return null
    val local = persistedQueueForWorkspace(mac.deviceId, workspaceId, fallbackRunId)
    val response = repository.followUpQueue(mac, workspaceId)
    val pendingLocal = local?.second.orEmpty().filter {
        it.daemonAcknowledged != true && !it.paused
    }
    var resolved = response.queue
    val acknowledged = mutableSetOf<String>()
    if (resolved == null && pendingLocal.isNotEmpty()) {
        if (!migrateLocalQueue) return fallbackRunId
        if (repository.cachedSupports(mac.deviceId, FOLLOW_UP_ENQUEUE_ACTION) == true) {
            pendingLocal.forEach { item ->
                resolved = repository.enqueueFollowUp(mac, workspaceId,
                    local?.first ?: fallbackRunId.orEmpty(), item, null).queue ?: resolved
                acknowledged += item.id
            }
        } else resolved = repository.replaceFollowUpQueue(
            mac = mac,
            workspaceId = workspaceId,
            runId = local?.first ?: fallbackRunId.orEmpty(),
            items = pendingLocal,
            selectedAgent = if (state.value.selectedMacId == mac.deviceId &&
                state.value.selectedWorkspaceId == workspaceId
            ) remoteFollowUpSelection(state.value) else null,
            resetCountdown = false,
        ).queue
    } else if (resolved != null) {
        val currentRemote = resolved
        val missing = pendingLocal.filter { pending ->
            currentRemote.items.none { it.id == pending.id } && currentRemote.lastCompletedItemId != pending.id
        }
        if (missing.isNotEmpty()) {
            if (!migrateLocalQueue) return currentRemote.runId
            if (repository.cachedSupports(mac.deviceId, FOLLOW_UP_ENQUEUE_ACTION) == true) {
                missing.forEach { item ->
                    resolved = repository.enqueueFollowUp(
                        mac = mac,
                        workspaceId = workspaceId,
                        runId = local?.first ?: fallbackRunId ?: currentRemote.runId,
                        item = item,
                        selectedAgent = resolved?.selectedAgent
                            ?: remoteFollowUpSelection(state.value),
                    ).queue ?: resolved
                    acknowledged += item.id
                }
            } else {
                resolved = repository.replaceFollowUpQueue(
                    mac = mac,
                    workspaceId = workspaceId,
                    runId = local?.first ?: fallbackRunId ?: currentRemote.runId,
                    items = currentRemote.items.map(
                        RemoteFollowUpQueueItem::asPersistedItem
                    ) + missing,
                    selectedAgent = currentRemote.selectedAgent
                        ?: remoteFollowUpSelection(state.value),
                    resetCountdown = false,
                ).queue
            }
        }
    }
    applyDaemonFollowUpQueue(mac.deviceId, workspaceId, resolved, acknowledged)
    if (!resolved?.items.isNullOrEmpty()) {
        ensureDaemonFollowUpMonitor(mac, workspaceId)
    }
    return resolved?.runId
}

private fun MainViewModel.ensureDaemonFollowUpMonitor(
    mac: RelayDevice,
    workspaceId: String,
) {
    if (followUpTask?.isActive == true) return
    followUpTask = viewModelScope.launch {
        try {
            while (state.value.selectedMacId == mac.deviceId &&
                state.value.selectedWorkspaceId == workspaceId &&
                state.value.followUpQueue.isNotEmpty() &&
                cachedDaemonOwnsFollowUpQueue(mac.deviceId) == true
            ) {
                delay(1_000)
                val previousRunId = queuedRunId
                val nextRunId = try {
                    refreshDaemonFollowUpQueue(
                        mac = mac,
                        workspaceId = workspaceId,
                        fallbackRunId = previousRunId ?: state.value.visibleRunId,
                        migrateLocalQueue = false,
                    )
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Throwable) {
                    null
                }
                if (!previousRunId.isNullOrBlank() &&
                    !nextRunId.isNullOrBlank() &&
                    previousRunId != nextRunId
                ) {
                    domainRepositories.conversations.advanceViewedRun(
                        workspaceSettingsKey(mac.deviceId, workspaceId),
                        previousRunId,
                        nextRunId,
                    )
                    loadWorkspaces(
                        mac = mac,
                        expectedGeneration = contextGeneration,
                        preferredWorkspaceId = workspaceId,
                        forceVisibleRunRefresh = true,
                    )
                }
            }
        } finally {
            followUpTask = null
        }
    }
}

private fun MainViewModel.completeDaemonFollowUpEnqueue(
    mac: RelayDevice,
    workspaceId: String,
    itemId: String,
    response: RemoteFollowUpQueueResponse,
) {
    applyDaemonFollowUpQueue(mac.deviceId, workspaceId, response.queue, acknowledged = setOf(itemId))
    // The draft was cleared when the item was persisted. A receipt must not
    // clear the next draft, even when the user typed the same text again.
    ensureDaemonFollowUpMonitor(mac, workspaceId)
}

private suspend fun MainViewModel.recoverDaemonFollowUpEnqueue(
    mac: RelayDevice,
    workspaceId: String,
    runId: String,
    snapshot: MainUiState,
    item: QueuedFollowUp,
    queued: List<QueuedFollowUp>,
): RemoteFollowUpQueueResponse? {
    // A timeout can happen after pcrd commits the message. Repeating the same
    // command id lets the daemon journal return that result instead of making a
    // second user tap enqueue a duplicate prompt.
    delay(200)
    val repeated = try {
        val capabilities = repository.systemCapabilities(mac)
        followUpRemoteMutationMutex.withLock {
            if (capabilities.supports(FOLLOW_UP_ENQUEUE_ACTION)) {
                repository.enqueueFollowUp(
                    mac = mac,
                    workspaceId = workspaceId,
                    runId = runId,
                    item = item.asPersistedItem(),
                    selectedAgent = remoteFollowUpSelection(snapshot),
                )
            } else {
                repository.replaceFollowUpQueue(
                    mac = mac,
                    workspaceId = workspaceId,
                    runId = runId,
                    items = queued.map(QueuedFollowUp::asPersistedItem),
                    selectedAgent = remoteFollowUpSelection(snapshot),
                    resetCountdown = false,
                )
            }
        }
    } catch (error: CancellationException) {
        throw error
    } catch (_: Throwable) {
        null
    }
    if (repeated != null) return repeated

    val remote = try {
        repository.followUpQueue(mac, workspaceId)
    } catch (error: CancellationException) {
        throw error
    } catch (_: Throwable) {
        return null
    }
    return remote.takeIf { response ->
        response.queue?.items?.any { it.id == item.id } == true
    }
}

internal fun MainViewModel.enqueueFollowUp(prompt: String) {
    val snapshot = state.value
    val runId = snapshot.visibleRunId ?: return
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    val targetRunId = queuedRunId ?: runId
    val contextKey = followUpQueueKey(mac.deviceId, workspaceId, targetRunId)
    val newItem = QueuedFollowUp(
        id = UUID.randomUUID().toString(),
        prompt = prompt,
        settings = settingsSnapshot(snapshot),
        daemonAcknowledged = false,
    )
    val queued = snapshot.followUpQueue + newItem
    val draftKey = promptDraftKey(snapshot)
    // Persist before clearing the editor: the debounced UI save may not run
    // before Android stops this process.
    try {
        mobileStateSaveRevision.incrementAndGet()
        mobileStateSaveJob?.cancel()
        mobileStateStore.update { saved ->
            val persisted = saved.followUpQueueItems[contextKey].orEmpty()
            saved.copy(
                followUpQueueItems = saved.followUpQueueItems +
                    (contextKey to (persisted.filterNot { it.id == newItem.id } + newItem.asPersistedItem())),
                promptDrafts = draftKey?.let { saved.promptDrafts - it } ?: saved.promptDrafts,
            )
        }
    } catch (error: Throwable) {
        showError(friendlyError(error, snapshot.appLanguage))
        return
    }
    queuedRunId = targetRunId
    promptDraftKey(snapshot)?.let(promptDraftsByContext::remove)
    mutableState.update { current ->
        current.copy(
            followUpQueue = queued,
            prompt = if (current.prompt.trim() == prompt.trim()) "" else current.prompt,
        )
    }
    persistFollowUpQueue(targetRunId, queued)
    // The local copy is an outbox until pcrd acknowledges it. WorkManager takes
    // over if the app is backgrounded or killed during the Relay request.
    app.followUpQueueScheduler.enqueue(
        mac.deviceId,
        workspaceId,
        targetRunId,
        wake = true,
    )
    viewModelScope.launch {
        try {
            val response = followUpRemoteMutationMutex.withLock {
                val capabilities = repository.systemCapabilities(mac)
                if (!capabilities.supports(FOLLOW_UP_REPLACE_ACTION)) {
                    error("The target device must be updated before messages can be queued.")
                }
                if (capabilities.supports(FOLLOW_UP_ENQUEUE_ACTION)) {
                    repository.enqueueFollowUp(
                        mac = mac,
                        workspaceId = workspaceId,
                        runId = targetRunId,
                        item = newItem.asPersistedItem(),
                        selectedAgent = remoteFollowUpSelection(snapshot),
                    )
                } else {
                    repository.replaceFollowUpQueue(
                        mac = mac,
                        workspaceId = workspaceId,
                        runId = targetRunId,
                        items = queued.map(QueuedFollowUp::asPersistedItem),
                        selectedAgent = remoteFollowUpSelection(snapshot),
                        resetCountdown = false,
                    )
                }
            }
            completeDaemonFollowUpEnqueue(
                mac = mac,
                workspaceId = workspaceId,
                itemId = newItem.id,
                response = response,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (isPermanentRemoteCommandRejection(error)) {
                val failed = followUpQueuesByContext[contextKey].orEmpty().map {
                    if (it.id == newItem.id && it.daemonAcknowledged != true)
                        it.copy(paused = true, lastError = friendlyError(error, snapshot.appLanguage)) else it
                }
                followUpQueuesByContext[contextKey] = failed
                scheduleMobileStateSave()
                if (state.value.selectedMacId == mac.deviceId &&
                    state.value.selectedWorkspaceId == workspaceId && queuedRunId == targetRunId) {
                    mutableState.update { current ->
                        current.copy(
                            followUpQueue = failed.map(PersistedQueuedFollowUp::asUiItem),
                        )
                    }
                }
                return@launch
            }
            val recovered = recoverDaemonFollowUpEnqueue(
                mac = mac,
                workspaceId = workspaceId,
                runId = targetRunId,
                snapshot = snapshot,
                item = newItem,
                queued = queued,
            )
            if (recovered != null) {
                completeDaemonFollowUpEnqueue(
                    mac = mac,
                    workspaceId = workspaceId,
                    itemId = newItem.id,
                    response = recovered,
                )
            }
        }
    }
}

private fun MainViewModel.syncQueuedFollowUpMutation(
    runId: String,
    previous: List<QueuedFollowUp>,
    desired: List<QueuedFollowUp>,
    resetCountdown: Boolean,
) {
    val snapshot = state.value
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    if (cachedDaemonOwnsFollowUpQueue(mac.deviceId) == false) {
        persistFollowUpQueue(runId, desired)
        reconcileFollowUpQueue(resetCountdown = resetCountdown)
        return
    }
    persistFollowUpQueue(runId, desired)
    val requestId = ++followUpRemoteMutationRequestId
    viewModelScope.launch {
        try {
            if (!daemonOwnsFollowUpQueue(mac)) {
                persistFollowUpQueue(runId, desired)
                reconcileFollowUpQueue(resetCountdown = resetCountdown)
                return@launch
            }
            val response = followUpRemoteMutationMutex.withLock {
                if (requestId != followUpRemoteMutationRequestId) return@withLock null
                if (desired.isEmpty()) {
                    repository.clearFollowUpQueue(mac, workspaceId, runId)
                } else {
                    repository.replaceFollowUpQueue(
                        mac = mac,
                        workspaceId = workspaceId,
                        runId = runId,
                        items = desired.map(QueuedFollowUp::asPersistedItem),
                        selectedAgent = remoteFollowUpSelection(state.value),
                        resetCountdown = resetCountdown,
                    )
                }
            } ?: return@launch
            if (requestId == followUpRemoteMutationRequestId) {
                applyDaemonFollowUpQueue(mac.deviceId, workspaceId, response.queue)
                if (!response.queue?.items.isNullOrEmpty()) {
                    ensureDaemonFollowUpMonitor(mac, workspaceId)
                }
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (requestId == followUpRemoteMutationRequestId &&
                state.value.selectedMacId == mac.deviceId &&
                state.value.selectedWorkspaceId == workspaceId &&
                (queuedRunId == null || queuedRunId == runId)
            ) {
                val editedIDs = (previous + desired).map { it.id }.toSet()
                val retained = previous + state.value.followUpQueue.filter { it.id !in editedIDs }
                queuedRunId = runId.takeIf { retained.isNotEmpty() }
                mutableState.update { current ->
                    current.copy(
                        followUpQueue = retained,
                        followUpSecondsRemaining = null,
                    )
                }
                persistFollowUpQueue(runId, retained)
                showError(friendlyError(error, state.value.appLanguage))
            }
        }
    }
}

internal fun MainViewModel.clearQueuedFollowUps() {
    val previous = state.value.followUpQueue
    val runId = queuedRunId ?: state.value.visibleRunId
    if (runId == null) return
    queuedRunId = null
    followUpTask?.cancel()
    followUpTask = null
    mutableState.update {
        it.copy(
            followUpQueue = emptyList(),
            followUpSecondsRemaining = null,
            storageConfirmation = if (pendingStorageActionForCurrentRun() == null) {
                null
            } else {
                it.storageConfirmation
            },
        )
    }
    syncQueuedFollowUpMutation(
        runId = runId,
        previous = previous,
        desired = emptyList(),
        resetCountdown = true,
    )
}

internal fun MainViewModel.clearQueuedFollowUps(context: RunMutationContext) {
    if (isCurrentRunMutation(context)) {
        clearQueuedFollowUps()
        return
    }
    val key = followUpQueueKey(
        context.mac.deviceId,
        context.workspaceId,
        context.runId,
    )
    followUpQueuesByContext.remove(key)
    followUpDeadlinesByContext.remove(key)
    app.followUpQueueScheduler.cancelContext(
        context.mac.deviceId,
        context.workspaceId,
        context.runId,
    )
    scheduleMobileStateSave()
    viewModelScope.launch {
        runCatching {
            if (daemonOwnsFollowUpQueue(context.mac)) {
                repository.clearFollowUpQueue(
                    context.mac,
                    context.workspaceId,
                    context.runId,
                )
            }
        }
    }
}

internal fun MainViewModel.beginQueuedFollowUpEditing() {
    followUpEditing = true
    followUpTask?.cancel()
    followUpTask = null
    removeFollowUpDeadline(queuedRunId ?: state.value.visibleRunId)
    mutableState.update { it.copy(followUpSecondsRemaining = null) }
}

internal fun MainViewModel.finishQueuedFollowUpEditing() {
    followUpEditing = false
    if (state.value.followUpQueue.isNotEmpty()) {
        reconcileFollowUpQueue(resetCountdown = true)
    }
}

internal fun MainViewModel.updateQueuedFollowUp(itemId: String, prompt: String) {
    val previous = state.value.followUpQueue
    if (previous.none { it.id == itemId }) return
    mutableState.update { current ->
        current.copy(
            followUpQueue = current.followUpQueue.map { item ->
                if (item.id == itemId) {
                    item.copy(
                        prompt = prompt,
                        attempt = 0,
                        dispatching = false,
                        dispatch = null,
                        failureCount = 0,
                        retryAtMillis = 0,
                        lastError = "",
                        paused = false,
                    )
                } else {
                    item
                }
            },
            storageConfirmation = null,
        )
    }
    val runId = queuedRunId ?: state.value.visibleRunId ?: return
    syncQueuedFollowUpMutation(runId, previous, state.value.followUpQueue, true)
}

internal fun MainViewModel.removeQueuedFollowUp(itemId: String) {
    val previous = state.value.followUpQueue
    if (previous.none { it.id == itemId }) return
    val runId = queuedRunId ?: state.value.visibleRunId
    if (runId == null) return
    mutableState.update { current ->
        current.copy(
            followUpQueue = current.followUpQueue.filterNot { it.id == itemId },
            storageConfirmation = null,
        )
    }
    if (state.value.followUpQueue.isEmpty()) queuedRunId = null
    syncQueuedFollowUpMutation(runId, previous, state.value.followUpQueue, true)
}

internal fun MainViewModel.retryQueuedFollowUp(itemId: String) {
    val previous = state.value.followUpQueue
    if (previous.none { it.id == itemId && it.paused }) return
    mutableState.update { current ->
        current.copy(
            followUpQueue = current.followUpQueue.map { item ->
                if (item.id == itemId) {
                    item.copy(
                        attempt = item.attempt + 1,
                        failureCount = 0,
                        retryAtMillis = 0,
                        lastError = "",
                        paused = false,
                    )
                } else {
                    item
                }
            },
        )
    }
    val runId = queuedRunId ?: state.value.visibleRunId ?: return
    syncQueuedFollowUpMutation(runId, previous, state.value.followUpQueue, true)
}

internal fun MainViewModel.moveQueuedFollowUp(itemId: String, offset: Int) {
    val previous = state.value.followUpQueue
    val index = state.value.followUpQueue.indexOfFirst { it.id == itemId }
    val target = index + offset
    if (index !in state.value.followUpQueue.indices ||
        target !in state.value.followUpQueue.indices
    ) return
    mutableState.update { current ->
        val queue = current.followUpQueue.toMutableList()
        val item = queue.removeAt(index)
        queue.add(target, item)
        current.copy(followUpQueue = queue, storageConfirmation = null)
    }
    val runId = queuedRunId ?: state.value.visibleRunId ?: return
    syncQueuedFollowUpMutation(runId, previous, state.value.followUpQueue, true)
}

internal fun MainViewModel.reconcileFollowUpQueue(resetCountdown: Boolean) {
    val snapshot = state.value
    val runId = snapshot.visibleRunId
    if (followUpEditing) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    if (runId == null || snapshot.followUpQueue.isEmpty()) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    val mac = selectedMac()
    val workspaceId = snapshot.selectedWorkspaceId
    if (mac != null && workspaceId != null &&
        cachedDaemonOwnsFollowUpQueue(mac.deviceId) != false
    ) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        viewModelScope.launch {
            try {
                if (!daemonOwnsFollowUpQueue(mac)) {
                    reconcileFollowUpQueue(resetCountdown)
                    return@launch
                }
                val remoteRunId = if (resetCountdown && !followUpEditing) {
                    val response = followUpRemoteMutationMutex.withLock {
                        repository.replaceFollowUpQueue(
                            mac = mac,
                            workspaceId = workspaceId,
                            runId = queuedRunId ?: runId,
                            items = state.value.followUpQueue.map(
                                QueuedFollowUp::asPersistedItem
                            ),
                            selectedAgent = remoteFollowUpSelection(state.value),
                            resetCountdown = true,
                        )
                    }
                    applyDaemonFollowUpQueue(mac.deviceId, workspaceId, response.queue)
                    response.queue?.runId
                } else {
                    refreshDaemonFollowUpQueue(
                        mac = mac,
                        workspaceId = workspaceId,
                        fallbackRunId = queuedRunId ?: runId,
                    )
                }
                if (!remoteRunId.isNullOrBlank() && remoteRunId != runId) {
                    loadWorkspaces(
                        mac = mac,
                        expectedGeneration = contextGeneration,
                        preferredWorkspaceId = workspaceId,
                        forceVisibleRunRefresh = true,
                    )
                }
                if (state.value.followUpQueue.isNotEmpty()) {
                    ensureDaemonFollowUpMonitor(mac, workspaceId)
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Throwable) {
                // pcrd owns an acknowledged queue. A later poll will restore its
                // projection; never race it with the legacy in-app dispatcher.
            }
        }
        return
    }
    if (!isTerminalRunStatus(snapshot.runStatus)) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    if (snapshot.runStatus.lowercase() in setOf("sync_ambiguous", "cleanup_failed")) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    if (snapshot.followUpQueue.first().prompt.isBlank()) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    if (snapshot.followUpQueue.first().paused) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }

    if (snapshot.finalizedAgent != null) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        dispatchFollowUp()
        return
    }

    val source = continuationSourceAgent(snapshot)
    if (source == null) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
        return
    }
    if (snapshot.selectedAgent != source.number) {
        mutableState.update { it.copy(selectedAgent = source.number) }
        viewModelScope.launch { loadAgent(runId, source.number) }
    }
    if (!resetCountdown && followUpTask?.isActive == true) return
    startFollowUpCountdown(runId, resetDeadline = resetCountdown)
}

private fun MainViewModel.startFollowUpCountdown(
    runId: String,
    minimumDelaySeconds: Int = 0,
    resetDeadline: Boolean = true,
) {
    followUpTask?.cancel()
    val configuredDelay = maxOf(
        minimumDelaySeconds,
        state.value.runSettings.followUpDelay.coerceAtLeast(0),
    )
    val key = currentFollowUpQueueKey(runId)
    val now = System.currentTimeMillis()
    val retryAt = state.value.followUpQueue.firstOrNull()?.retryAtMillis ?: 0L
    val rememberedDeadline = if (!resetDeadline && key != null) {
        followUpDeadlinesByContext[key]
    } else {
        null
    }
    val newDeadline = if (retryAt > now) {
        now
    } else {
        now + configuredDelay.toLong() * 1_000L
    }
    val deadline = maxOf(
        rememberedDeadline ?: newDeadline,
        retryAt,
    ).also { value ->
        if (key != null) {
            followUpDeadlinesByContext[key] = value
            scheduleMobileStateSave()
        }
    }
    val delaySeconds = followUpCountdownSeconds(deadline, now)
    if (delaySeconds == 0) {
        mutableState.update { it.copy(followUpSecondsRemaining = 0) }
        dispatchFollowUp()
        return
    }
    mutableState.update { it.copy(followUpSecondsRemaining = delaySeconds) }
    followUpTask = viewModelScope.launch {
        for (remaining in (delaySeconds - 1) downTo 0) {
            delay(1_000)
            if (state.value.visibleRunId != runId ||
                state.value.followUpQueue.isEmpty() ||
                !isTerminalRunStatus(state.value.runStatus)
            ) return@launch
            mutableState.update { it.copy(followUpSecondsRemaining = remaining) }
        }
        dispatchFollowUp()
    }
}

private fun MainViewModel.dispatchFollowUp() {
    val snapshot = state.value
    val queuedItem = snapshot.followUpQueue.firstOrNull() ?: return
    val item = queuedItem.prompt.trim()
    val runId = snapshot.visibleRunId ?: return
    if (item.isEmpty() || !isTerminalRunStatus(snapshot.runStatus)) return
    if (queuedItem.paused || queuedItem.retryAtMillis > System.currentTimeMillis()) {
        reconcileFollowUpQueue(resetCountdown = false)
        return
    }
    if (snapshot.runStatus.lowercase() in setOf("sync_ambiguous", "cleanup_failed")) return
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    val workspace = snapshot.workspaces.firstOrNull {
        it.workspaceId == workspaceId
    } ?: return
    val contextKey = currentFollowUpQueueKey(runId) ?: return
    if (snapshot.runActionInProgress || contextKey in backgroundFollowUpInFlight) {
        followUpTask = viewModelScope.launch {
            delay(1_000)
            reconcileFollowUpQueue(resetCountdown = false)
        }
        return
    }
    val runSettings = queuedItem.settings ?: settingsSnapshot(snapshot)
    val savedDispatch = queuedItem.dispatch
    val dispatch = if (
        savedDispatch == null ||
        (savedDispatch.mode == PersistedFollowUpDispatch.MODE_START &&
            snapshot.finalizedAgent != null)
    ) {
        val source = continuationSourceAgent(snapshot) ?: return
        PersistedFollowUpDispatch(
            mode = PersistedFollowUpDispatch.MODE_CONTINUE,
            sourceRunId = runId,
            sourceAgent = source.number,
            settings = runSettings,
        )
    } else savedDispatch
    val dispatchingItem = queuedItem.copy(
        dispatching = true,
        dispatch = dispatch,
        retryAtMillis = 0,
    )
    mutableState.update { current ->
        if (current.followUpQueue.firstOrNull()?.id != queuedItem.id) current
        else current.copy(
            followUpQueue = listOf(dispatchingItem) + current.followUpQueue.drop(1)
        )
    }
    persistFollowUpQueue(runId, state.value.followUpQueue)
    followUpTask = null
    followUpDeadlinesByContext[contextKey] = System.currentTimeMillis()
    scheduleMobileStateSave()
    mutableState.update { it.copy(followUpSecondsRemaining = null) }
    val context = FollowUpContext(
        key = contextKey,
        macId = mac.deviceId,
        workspaceId = workspaceId,
        runId = runId,
    )
    if (!backgroundFollowUpInFlight.add(contextKey)) return
    backgroundFollowUpJobs[contextKey] = viewModelScope.launch {
        try {
            processBackgroundFollowUp(context, mac, workspace)
        } catch (error: CancellationException) {
            throw error
        } finally {
            backgroundFollowUpInFlight.remove(contextKey)
            backgroundFollowUpJobs.remove(contextKey)
            if (state.value.selectedMacId == mac.deviceId &&
                state.value.selectedWorkspaceId == workspaceId &&
                state.value.visibleRunId == runId
            ) {
                val restored = followUpQueuesByContext[contextKey]
                    .orEmpty()
                    .map { it.asUiItem() }
                queuedRunId = runId.takeIf { restored.isNotEmpty() }
                mutableState.update {
                    it.copy(followUpQueue = restored)
                }
                if (restored.isNotEmpty() &&
                    isTerminalRunStatus(state.value.runStatus)
                ) {
                    reconcileFollowUpQueue(resetCountdown = false)
                }
            }
        }
    }
}

internal fun MainViewModel.ensureFollowUpCoordinator() {
    val hasLegacyQueue = followUpQueuesByContext.keys
        .mapNotNull(::parseFollowUpContext)
        .any { cachedDaemonOwnsFollowUpQueue(it.macId) == false }
    if (!uiForeground || !state.value.signedIn || !hasLegacyQueue ||
        followUpCoordinatorTask?.isActive == true
    ) return
    followUpCoordinatorTask = viewModelScope.launch {
        try {
            while (followUpQueuesByContext.keys
                    .mapNotNull(::parseFollowUpContext)
                    .any { cachedDaemonOwnsFollowUpQueue(it.macId) == false }
            ) {
                val visibleKey = (queuedRunId ?: state.value.visibleRunId)
                    ?.let(::currentFollowUpQueueKey)
                val contexts = followUpQueuesByContext.keys
                    .mapNotNull(::parseFollowUpContext)
                    .filter {
                        it.key != visibleKey &&
                            cachedDaemonOwnsFollowUpQueue(it.macId) == false
                    }
                    .groupBy(FollowUpContext::macId)
                for ((macId, macContexts) in contexts) {
                    val mac = state.value.macs.firstOrNull { it.deviceId == macId }
                        ?: continue
                    val workspaces = try {
                        repository.workspaces(mac).workspaces.associateBy {
                            it.workspaceId
                        }
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Throwable) {
                        continue
                    }
                    for (context in macContexts) {
                        val workspace = workspaces[context.workspaceId] ?: continue
                        if (!backgroundFollowUpInFlight.add(context.key)) continue
                        backgroundFollowUpJobs[context.key] = viewModelScope.launch {
                            try {
                                processBackgroundFollowUp(context, mac, workspace)
                            } finally {
                                backgroundFollowUpInFlight.remove(context.key)
                                backgroundFollowUpJobs.remove(context.key)
                            }
                        }
                    }
                }
                delay(5_000)
            }
        } finally {
            followUpCoordinatorTask = null
            if (uiForeground && followUpQueuesByContext.keys
                    .mapNotNull(::parseFollowUpContext)
                    .any { cachedDaemonOwnsFollowUpQueue(it.macId) == false }
            ) {
                ensureFollowUpCoordinator()
            }
        }
    }
}

private suspend fun MainViewModel.processBackgroundFollowUp(
    context: FollowUpContext,
    mac: RelayDevice,
    workspace: RemoteWorkspace,
) {
    var queue = followUpQueuesByContext[context.key].orEmpty()
    var head = queue.firstOrNull() ?: return
    if (head.paused || head.retryAtMillis > System.currentTimeMillis()) return
    val latestRunId = workspace.lastRunId.trim()
    if (latestRunId.isEmpty()) return

    if (latestRunId != context.runId && !head.dispatching) {
        val nextKey = followUpQueueKey(context.macId, context.workspaceId, latestRunId)
        val existing = followUpQueuesByContext[nextKey].orEmpty()
        val merged = mergeFollowUpQueues(queue, existing)
        val deadline = followUpDeadlinesByContext.remove(context.key)
        followUpQueuesByContext.remove(context.key)
        followUpQueuesByContext[nextKey] = merged
        if (deadline != null) followUpDeadlinesByContext[nextKey] = deadline
        scheduleMobileStateSave()
        return
    }

    val now = System.currentTimeMillis()
    var dispatch = head.dispatch
    if (!head.dispatching || dispatch == null ||
        dispatch.mode == PersistedFollowUpDispatch.MODE_START
    ) {
        val run = repository.run(mac, context.runId)
        val status = run["status"]?.jsonPrimitive?.content.orEmpty()
        if (!isTerminalRunStatus(status) ||
            status.lowercase() in setOf("sync_ambiguous", "cleanup_failed")
        ) return
        val settingsKey = workspaceSettingsKey(context.macId, context.workspaceId)
        val runSettings = head.settings
            ?: localSettingsByWorkspace[settingsKey]
            ?: workspace.settings?.normalized()
            ?: RemoteRunSettings()
        val agents = run["agents"]?.jsonArray.orEmpty().mapNotNull { raw ->
            val agent = raw.jsonObject
            val number = agent["agent"]?.jsonPrimitive?.intOrNull
                ?: agent["idx"]?.jsonPrimitive?.intOrNull
                ?: return@mapNotNull null
            number to (agent["can_continue"]?.jsonPrimitive?.booleanOrNull == true)
        }
        val eligible = agents.filter { it.second }.map(Pair<Int, Boolean>::first)
        val finalizedAgent = run["finalized_agent"]?.jsonPrimitive?.intOrNull
        val recommended = run["recommended_agent"]?.jsonPrimitive?.intOrNull
        val remembered = selectedAgentsByContext[context.key]
        val sourceAgent = finalizedAgent?.takeIf { it in eligible }
            ?: remembered?.takeIf { it in eligible }
            ?: recommended?.takeIf { it in eligible }
            ?: eligible.firstOrNull()
            ?: return
        dispatch = PersistedFollowUpDispatch(
            mode = PersistedFollowUpDispatch.MODE_CONTINUE,
            sourceRunId = context.runId,
            sourceAgent = sourceAgent,
            settings = runSettings,
        )
    }

    val deadline = followUpDeadlinesByContext[context.key] ?: run {
        val waitSeconds = if (head.dispatching) 5 else {
            dispatch.settings.followUpDelay.coerceAtLeast(0)
        }
        val value = now + waitSeconds.toLong() * 1_000L
        followUpDeadlinesByContext[context.key] = value
        scheduleMobileStateSave()
        value
    }
    if (deadline > now) return

    if (!activeRunMutations.add(context.key)) return
    refreshVisibleRunMutationState()

    head = head.copy(dispatching = true, dispatch = dispatch, retryAtMillis = 0)
    queue = listOf(head) + queue.drop(1)
    followUpQueuesByContext[context.key] = queue
    followUpDeadlinesByContext.remove(context.key)
    scheduleMobileStateSave()

    try {
        val result = if (dispatch.mode == PersistedFollowUpDispatch.MODE_CONTINUE) {
            repository.continueRun(
                mac = mac,
                runId = dispatch.sourceRunId,
                agent = requireNotNull(dispatch.sourceAgent),
                prompt = head.prompt,
                agents = dispatch.settings.agents,
                synthesisAgents = dispatch.settings.synthesisAgents,
                settings = dispatch.settings,
                confirmLargeRun = true,
                commandId = head.commandId,
            )
        } else {
            repository.startRun(
                mac = mac,
                workspaceId = context.workspaceId,
                prompt = head.prompt,
                agents = dispatch.settings.agents,
                synthesisAgents = dispatch.settings.synthesisAgents,
                settings = dispatch.settings,
                resumeSessionId = dispatch.resumeSessionId,
                confirmLargeRun = true,
                commandId = head.commandId,
            )
        }
        val nextRunId = result["run_id"]?.jsonPrimitive?.content
            ?: error("Target machine did not return a run id")
        app.runCompletionScheduler.enqueue(
            macID = context.macId,
            workspaceID = context.workspaceId,
            workspaceName = workspace.name,
            runID = nextRunId,
        )
        completeBackgroundFollowUp(context, head.id, nextRunId, mac)
    } catch (error: CancellationException) {
        throw error
    } catch (error: Throwable) {
        val current = followUpQueuesByContext[context.key].orEmpty()
        if (current.firstOrNull()?.id == head.id) {
            val retry = head.afterDispatchFailure(
                remoteRejected = isPermanentRemoteCommandRejection(error),
                errorMessage = error.message.orEmpty().ifBlank { "Follow-up dispatch failed" },
            )
            followUpQueuesByContext[context.key] = listOf(retry) + current.drop(1)
            if (retry.paused) {
                followUpDeadlinesByContext.remove(context.key)
            } else {
                followUpDeadlinesByContext[context.key] = retry.retryAtMillis
            }
            scheduleMobileStateSave()
        }
        if (state.value.selectedMacId == context.macId &&
            state.value.selectedWorkspaceId == context.workspaceId
        ) {
            showError(friendlyError(error, state.value.appLanguage))
        }
    } finally {
        activeRunMutations.remove(context.key)
        refreshVisibleRunMutationState()
    }
}

private suspend fun MainViewModel.completeBackgroundFollowUp(
    context: FollowUpContext,
    itemId: String,
    nextRunId: String,
    mac: RelayDevice,
) {
    val current = followUpQueuesByContext[context.key].orEmpty()
    if (current.firstOrNull()?.id != itemId) return
    val completed = current.first()
    val remaining = current.drop(1)
    val sourceKey = WorkspaceRunKey(context.macId, context.workspaceId, context.runId)
    val sourceWasVisible = isVisibleRun(sourceKey)
    if (sourceWasVisible) captureVisibleRunState()
    val carriedConversation = runStateStore[sourceKey]?.let { source ->
        continuationConversation(source, completed.dispatch?.sourceAgent)
    }.orEmpty()
    val nextKey = WorkspaceRunKey(context.macId, context.workspaceId, nextRunId)
    runStateStore.put(
        nextKey,
        WorkspaceRunSnapshot(
            status = "starting",
            prompt = completed.prompt,
            conversationHistory = carriedConversation,
        ),
    )
    onWorkspaceRunStarted(context.macId, context.workspaceId, nextRunId)
    domainRepositories.conversations.advanceViewedRun(
        workspaceSettingsKey(context.macId, context.workspaceId),
        context.runId,
        nextRunId,
    )
    followUpQueuesByContext.remove(context.key)
    followUpDeadlinesByContext.remove(context.key)
    if (remaining.isNotEmpty()) {
        val nextKey = followUpQueueKey(context.macId, context.workspaceId, nextRunId)
        val existing = followUpQueuesByContext[nextKey].orEmpty()
        followUpQueuesByContext[nextKey] = mergeFollowUpQueues(remaining, existing)
    }
    scheduleMobileStateSave()
    if (sourceWasVisible) {
        advanceContext(preserveFollowUps = true)
        queuedRunId = nextRunId.takeIf { remaining.isNotEmpty() }
        val settings = completed.dispatch?.settings ?: state.value.runSettings
        mutableState.update { currentState ->
            currentState.copy(
                visibleRunId = nextRunId,
                runStatus = "starting",
                canAddAgents = false,
                canUpdateSynthesis = false,
                runPrompt = completed.prompt,
                agentSummaries = emptyList(),
                selectedAgent = null,
                recommendedAgent = null,
                finalizedAgent = null,
                conversationHistory = carriedConversation,
                agentTranscripts = emptyMap(),
                runSettings = settings,
                followUpQueue = remaining.map { it.asUiItem() },
                followUpSecondsRemaining = null,
                storageConfirmation = null,
            )
        }
        beginPolling(mac, nextKey)
    }
}


internal fun MainViewModel.followUpQueueKey(macId: String, workspaceId: String, runId: String): String =
    "${workspaceSettingsKey(macId, workspaceId)}\u001f$runId"

internal fun MainViewModel.parseFollowUpContext(key: String): FollowUpContext? {
    val parts = key.split('\u001f', limit = 3)
    if (parts.size != 3 || parts.any(String::isBlank)) return null
    return FollowUpContext(key, parts[0], parts[1], parts[2])
}

internal fun PersistedQueuedFollowUp.asUiItem(): QueuedFollowUp = QueuedFollowUp(
    id = id,
    prompt = prompt,
    settings = settings,
    attempt = attempt,
    dispatching = dispatching,
    dispatch = dispatch,
    failureCount = failureCount,
    retryAtMillis = retryAtMillis,
    lastError = lastError,
    paused = paused,
    automaticRecoveryAttempted = automaticRecoveryAttempted,
    daemonAcknowledged = daemonAcknowledged,
)

private fun QueuedFollowUp.asPersistedItem(): PersistedQueuedFollowUp =
    PersistedQueuedFollowUp(
        id = id,
        prompt = prompt,
        settings = settings,
        attempt = attempt,
        dispatching = dispatching,
        dispatch = dispatch,
        failureCount = failureCount,
        retryAtMillis = retryAtMillis,
        lastError = lastError,
        paused = paused,
        automaticRecoveryAttempted = automaticRecoveryAttempted,
        daemonAcknowledged = daemonAcknowledged,
    )

private fun MainViewModel.currentFollowUpQueueKey(runId: String): String? {
    val macId = state.value.selectedMacId ?: return null
    val workspaceId = state.value.selectedWorkspaceId ?: return null
    return followUpQueueKey(macId, workspaceId, runId)
}

private fun MainViewModel.removeFollowUpDeadline(runId: String?) {
    val key = runId?.let(::currentFollowUpQueueKey) ?: return
    if (followUpDeadlinesByContext.remove(key) != null) {
        scheduleMobileStateSave()
    }
}

internal fun MainViewModel.hasFollowUpDeadline(runId: String): Boolean =
    currentFollowUpQueueKey(runId)?.let(followUpDeadlinesByContext::containsKey) == true

internal fun MainViewModel.restoredFollowUpQueue(
    macId: String,
    workspaceId: String,
    runId: String?,
): List<QueuedFollowUp> {
    if (runId.isNullOrBlank()) {
        return emptyList()
    }
    val currentKey = followUpQueueKey(macId, workspaceId, runId)
    val restored = followUpQueuesByContext[currentKey].orEmpty()
    return restored.map { it.asUiItem() }
}

internal fun MainViewModel.persistFollowUpQueue(runId: String?, items: List<QueuedFollowUp>) {
    val macId = state.value.selectedMacId ?: return
    val workspaceId = state.value.selectedWorkspaceId ?: return
    if (runId.isNullOrBlank()) return
    val key = followUpQueueKey(macId, workspaceId, runId)
    val normalized = items.filter { it.prompt.isNotBlank() }
        .map { it.asPersistedItem() }
    if (normalized.isEmpty()) {
        followUpQueuesByContext.remove(key)
        followUpDeadlinesByContext.remove(key)
        app.followUpQueueScheduler.cancelContext(macId, workspaceId, runId)
    } else {
        followUpQueuesByContext[key] = normalized
        if (cachedDaemonOwnsFollowUpQueue(macId) == false) {
            app.followUpQueueScheduler.enqueue(macId, workspaceId, runId)
        } else {
            app.followUpQueueScheduler.cancelContext(macId, workspaceId, runId)
        }
    }
    scheduleMobileStateSave()
    if (cachedDaemonOwnsFollowUpQueue(macId) == false) ensureFollowUpCoordinator()
}

internal fun MainViewModel.storeFollowUpQueue(
    key: String,
    items: List<PersistedQueuedFollowUp>,
) {
    if (items.isEmpty()) {
        followUpQueuesByContext.remove(key)
        followUpDeadlinesByContext.remove(key)
        parseFollowUpContext(key)?.let { context ->
            app.followUpQueueScheduler.cancelContext(
                context.macId,
                context.workspaceId,
                context.runId,
            )
        }
    } else {
        followUpQueuesByContext[key] = items
        parseFollowUpContext(key)?.let { context ->
            if (cachedDaemonOwnsFollowUpQueue(context.macId) == false) {
                app.followUpQueueScheduler.enqueue(
                    context.macId,
                    context.workspaceId,
                    context.runId,
                )
            } else {
                app.followUpQueueScheduler.cancelContext(
                    context.macId,
                    context.workspaceId,
                    context.runId,
                )
            }
        }
    }
    val visibleKey = (queuedRunId ?: state.value.visibleRunId)
        ?.let(::currentFollowUpQueueKey)
    if (visibleKey == key) {
        mutableState.update { current ->
            current.copy(
                followUpQueue = items.map { it.asUiItem() },
                followUpSecondsRemaining = followUpDeadlinesByContext[key]?.let {
                    deadline -> followUpCountdownSeconds(
                        deadline,
                        System.currentTimeMillis(),
                    )
                },
            )
        }
    }
    scheduleMobileStateSave()
    if (parseFollowUpContext(key)?.let {
            cachedDaemonOwnsFollowUpQueue(it.macId) == false
        } == true
    ) ensureFollowUpCoordinator()
}
