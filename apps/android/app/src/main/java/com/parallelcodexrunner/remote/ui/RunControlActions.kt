package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

internal fun MainViewModel.refreshRun() {
    val (mac, key) = currentRunKey() ?: return
    viewModelScope.launch {
        runPollingByContext.remove(key)?.cancelAndJoin()
        conversationReadStore.removeWhere { it == key }
        loadedRunHistories.remove(key)
        beginPolling(mac, key)
    }
}

internal fun MainViewModel.selectAgent(agent: Int, userInitiated: Boolean = true) {
    val snapshot = state.value
    val runId = snapshot.visibleRunId ?: return
    if (snapshot.agentSummaries.none { it.number == agent }) return
    if (snapshot.pendingAgentSelection == agent) return
    if (snapshot.selectedAgent == agent) {
        agentSelectionJob?.cancel()
        agentSelectionJob = null
        mutableState.update { it.copy(pendingAgentSelection = null) }
        return
    }
    val context = currentRunKey(runId) ?: return
    agentSelectionJob?.cancel()

    val cachedSnapshot = runStateStore[context.second]
    val wasWorking = !isTerminalRunStatus(snapshot.runStatus) || cachedSnapshot?.workerActive == true
    val hasCachedTranscript = cachedSnapshot
        ?.agentTranscripts
        ?.containsKey(agent) == true
    mutableState.update { it.copy(pendingAgentSelection = agent) }
    agentSelectionJob = viewModelScope.launch {
        try {
            if (userInitiated && !wasWorking &&
                snapshot.finalizedAgent == null && snapshot.canMutateVisibleRun() &&
                snapshot.agentSummaries.any { it.number == agent && it.canContinue } &&
                repository.systemCapabilities(context.first).supports("followups.select_agent")
            ) {
                val response = repository.selectFollowUpAgent(
                    context.first, context.second.workspaceId, runId, agent,
                )
                applyDaemonFollowUpQueue(context.first.deviceId, context.second.workspaceId, response.queue)
            }
            if (!isVisibleRun(context.second) || state.value.pendingAgentSelection != agent) return@launch
            if (hasCachedTranscript) {
                prewarmVisibleMarkdown(checkNotNull(cachedSnapshot).copy(selectedAgent = agent))
                if (!isVisibleRun(context.second) ||
                    state.value.pendingAgentSelection != agent
                ) {
                    return@launch
                }
                if (wasWorking && runStateStore[context.second]?.reviewSelectionEstablished == true) {
                    mutableState.update { it.copy(pendingAgentSelection = null) }
                    return@launch
                }
                applyAgentSelection(context.second, agent, userInitiated)
            }
            loadAgent(context.first, context.second, agent)
            if (!hasCachedTranscript &&
                isVisibleRun(context.second) &&
                state.value.pendingAgentSelection == agent
            ) {
                if (wasWorking && runStateStore[context.second]?.reviewSelectionEstablished == true) {
                    mutableState.update { it.copy(pendingAgentSelection = null) }
                    return@launch
                }
                applyAgentSelection(context.second, agent, userInitiated)
            }
            if (isVisibleRun(context.second) && state.value.selectedAgent == agent) {
                beginPolling(runId)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (isVisibleRun(context.second) &&
                (state.value.selectedAgent == agent ||
                    state.value.pendingAgentSelection == agent)
            ) {
                mutableState.update { it.copy(pendingAgentSelection = null) }
                showError(friendlyError(error, state.value.appLanguage))
            }
        }
    }
}

private fun MainViewModel.applyAgentSelection(key: WorkspaceRunKey, agent: Int, userInitiated: Boolean) {
    if (!isVisibleRun(key)) return
    if (state.value.selectedAgent != agent) closeWorkspaceFilesForContextChange()
    selectedAgentsByContext[key.encoded] = agent
    val updated = runStateStore.update(key) { it.copy(
        selectedAgent = agent,
        reviewSelectionEstablished = isTerminalRunStatus(it.status) && !it.workerActive,
    ) }
    scheduleMobileStateSave()
    mutableState.update { current ->
        if (isVisibleRun(key)) {
            current.projectRun(updated).copy(pendingAgentSelection = null)
        } else {
            current
        }
    }
    markAgentCompletionViewed(agent)
    if (userInitiated && isTerminalRunStatus(state.value.runStatus) &&
        state.value.followUpQueue.isNotEmpty()
    ) {
        // Switching agents changes the continuation source but preserves the
        // countdown. Restarting the timer here would extend the delay by the
        // full follow_up_delay every time the user pressed the agent switcher.
        reconcileFollowUpQueue(resetCountdown = false)
    }
}

internal fun MainViewModel.stopCurrentRun() {
    if (!state.value.canMutateVisibleRun()) return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        repository.stopRun(it.mac, it.runId)
        if (isCurrentRunMutation(it)) loadRun(it.runId)
    }
}

internal fun MainViewModel.acceptAgent(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    val available = snapshot.agentSummaries.any {
        it.number == agent && it.canAccept
    }
    if (!available) return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        val result = repository.acceptAgent(
            it.mac,
            it.runId,
            agent,
            it.settings,
        )
        if (isCurrentRunMutation(it)) {
            result["workspace_revision"]?.jsonPrimitive?.intOrNull?.let { revision ->
                mutableState.update { current ->
                    current.copy(workspaceRevision = revision)
                }
            }
            promotedSessionId(result)?.let(::applyPromotedSession)
            loadRun(it.runId)
        }
    }
}

internal fun MainViewModel.killAgent(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    val stoppable = snapshot.agentSummaries.any {
        it.number == agent && isAgentStopAvailable(it.status)
    }
    if (!stoppable) return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        repository.killAgent(it.mac, it.runId, agent)
        if (isCurrentRunMutation(it)) loadRun(it.runId)
    }
}

internal fun MainViewModel.retryAgent(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    if (snapshot.agentSummaries.none { it.number == agent && it.canRetry }) return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        repository.retryAgent(it.mac, it.runId, agent)
        if (isCurrentRunMutation(it)) resetAgentCompletionViewed(agent)
        val runKey = WorkspaceRunKey(it.mac.deviceId, it.workspaceId, it.runId)
        val requestKey = runKey to agent
        agentRequestIds[requestKey] = (agentRequestIds[requestKey] ?: 0) + 1
        runStateStore.update(runKey) { stored ->
            stored.copy(agentTranscripts = stored.agentTranscripts - agent)
        }
        mutableState.update {
            if (isCurrentRunMutation(context)) {
                it.copy(agentTranscripts = it.agentTranscripts - agent)
            } else {
                it
            }
        }
        if (isCurrentRunMutation(it)) {
            loadRun(it.runId)
            beginPolling(it.runId)
        }
    }
}

internal fun MainViewModel.toggleAgentRejection(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun() || snapshot.finalizedAgent != null) return
    val selected = snapshot.agentSummaries.firstOrNull { it.number == agent }
        ?: return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        repository.rejectAgent(
            it.mac,
            it.runId,
            agent,
            rejected = !selected.rejected,
        )
        if (isCurrentRunMutation(it)) loadRun(it.runId)
    }
}

internal fun MainViewModel.addMoreAgents(count: Int) {
    val maximum = state.value.maximumAgentCount
    if (count !in 1..maximum) {
        showError(state.value.appLanguage.text(
            "追加数量必须在 1-$maximum 之间。",
            "The additional Agent count must be between 1 and $maximum.",
        ))
        return
    }
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun() || !snapshot.canAddAgents) return
    val context = currentRunMutationContext() ?: return
    val storageApproved = hasLargeStorageApproval()
    executeRunMutation(context) {
        val result = repository.addAgents(
            mac = it.mac,
            runId = it.runId,
            count = count,
            confirmLargeRun = storageApproved,
        )
        if (result["status"]?.jsonPrimitive?.content == "confirmation_required") {
            val message = result["message"]
                ?.jsonPrimitive
                ?.content
                ?: "追加 Agent 需要确认存储空间。"
            pendingStorageActions.put(
                PendingStorageAction.More(
                    count = count,
                    context = it,
                    message = message,
                )
            )
            if (isCurrentRunMutation(it)) {
                mutableState.update { current ->
                    current.copy(storageConfirmation = message)
                }
            }
            return@executeRunMutation
        }
        if (isCurrentRunMutation(it)) {
            loadRun(it.runId)
            beginPolling(it.runId)
        }
    }
}

internal fun MainViewModel.discardCurrentRun() {
    if (!state.value.canMutateVisibleRun()) return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        repository.discardRun(
            mac = it.mac,
            runId = it.runId,
            keepWorkspaces = it.settings.keepWorkspaces,
        )
        clearQueuedFollowUps(it)
        if (isCurrentRunMutation(it)) loadRun(it.runId)
    }
}

internal fun MainViewModel.requestAcceptAgent(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    val available = snapshot.agentSummaries.any {
        it.number == agent && it.canAccept
    }
    if (!available) return
    mutableState.update { it.copy(runDestructiveAction = RunDestructiveAction.Accept(agent)) }
}

internal fun MainViewModel.requestKillAgent(agent: Int) {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    val stoppable = snapshot.agentSummaries.any {
        it.number == agent && isAgentStopAvailable(it.status)
    }
    if (!stoppable) return
    mutableState.update { it.copy(runDestructiveAction = RunDestructiveAction.Kill(agent)) }
}

internal fun MainViewModel.cancelRunDestructiveAction() = mutableState.update {
    it.copy(runDestructiveAction = null)
}

internal fun MainViewModel.confirmRunDestructiveAction() {
    val action = state.value.runDestructiveAction ?: return
    mutableState.update { it.copy(runDestructiveAction = null) }
    when (action) {
        is RunDestructiveAction.Accept -> acceptAgent(action.agent)
        is RunDestructiveAction.Kill -> killAgent(action.agent)
    }
}

internal fun MainViewModel.requestFinalizationRecovery() {
    val snapshot = state.value
    if (snapshot.runStatus.equals("sync_ambiguous", ignoreCase = true) &&
        snapshot.finalizedAgent != null &&
        snapshot.canMutateVisibleRun()
    ) {
        mutableState.update { it.copy(recoveryConfirmation = true) }
    }
}

internal fun MainViewModel.cancelFinalizationRecovery() = mutableState.update {
    it.copy(recoveryConfirmation = false)
}

internal fun MainViewModel.recoverFinalization(syncWasApplied: Boolean? = null) {
    if (!state.value.canMutateVisibleRun()) return
    val context = currentRunMutationContext() ?: return
    mutableState.update { it.copy(recoveryConfirmation = false) }
    executeRunMutation(context) {
        try {
            var result = repository.recoverFinalization(
                it.mac,
                it.runId,
                syncWasApplied,
            )
            if (syncWasApplied != false) {
                val agent = result["finalized_agent"]?.jsonPrimitive?.intOrNull
                    ?: error("The interrupted finalization has no selected Agent")
                result = repository.finishFinalizationCleanup(
                    it.mac,
                    it.runId,
                    agent,
                )
            }
            if (isCurrentRunMutation(it)) {
                applyWorkspaceRevision(result)
                promotedSessionId(result)?.let(::applyPromotedSession)
            }
        } finally {
            if (isCurrentRunMutation(it)) runCatching { loadRun(it.runId) }
        }
    }
}

internal fun MainViewModel.retryFinalizationCleanup() {
    val snapshot = state.value
    if (!snapshot.canMutateVisibleRun()) return
    val agent = snapshot.finalizedAgent ?: return
    val context = currentRunMutationContext() ?: return
    executeRunMutation(context) {
        try {
            val result = repository.finishFinalizationCleanup(
                it.mac,
                it.runId,
                agent,
            )
            if (isCurrentRunMutation(it)) {
                applyWorkspaceRevision(result)
                promotedSessionId(result)?.let(::applyPromotedSession)
            }
        } finally {
            if (isCurrentRunMutation(it)) runCatching { loadRun(it.runId) }
        }
    }
}

private fun MainViewModel.applyWorkspaceRevision(result: JsonObject) {
    val revision = result["workspace_revision"]?.jsonPrimitive?.intOrNull ?: return
    mutableState.update { current ->
        if (revision > current.workspaceRevision) {
            current.copy(workspaceRevision = revision)
        } else {
            current
        }
    }
}
