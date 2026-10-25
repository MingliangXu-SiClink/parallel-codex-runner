package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.AcceptedPromptResult
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.PersistedMobileState
import com.parallelcodexrunner.remote.data.PersistedQueuedFollowUp
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.withoutMac
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal fun workspaceSettingsKey(macId: String, workspaceId: String): String =
    "$macId\u001f$workspaceId"

private const val NEW_CONVERSATION_DRAFT_ID = "local:new-conversation"

internal fun promptDraftContextKey(
    macId: String,
    workspaceId: String,
    conversationId: String?,
    resumeSessionId: String,
    runId: String? = null,
): String {
    val conversation = conversationId?.trim().orEmpty()
    val resume = resumeSessionId.trim()
    val contextId = when {
        conversation.isNotEmpty() -> conversation
        !runId.isNullOrBlank() -> "run:$runId"
        !isNoResumeSession(resume) -> "resume:$resume"
        else -> NEW_CONVERSATION_DRAFT_ID
    }
    return "${workspaceSettingsKey(macId, workspaceId)}\u001fconversation\u001f$contextId"
}

internal fun restorePromptDraft(
    drafts: MutableMap<String, String>,
    macId: String,
    workspaceId: String,
    conversationId: String?,
    resumeSessionId: String,
    runId: String? = null,
): String {
    val contextKey = promptDraftContextKey(
        macId = macId,
        workspaceId = workspaceId,
        conversationId = conversationId,
        resumeSessionId = resumeSessionId,
        runId = runId,
    )
    drafts[contextKey]?.let { return it }

    val legacyKey = workspaceSettingsKey(macId, workspaceId)
    val legacyDraft = drafts.remove(legacyKey) ?: return ""
    drafts[contextKey] = legacyDraft
    return legacyDraft
}

internal fun MainViewModel.forgetMobileStateForMac(macId: String) {
    app.promptSubmissionScheduler.cancelForMac(macId)
    app.runCompletionScheduler.cancelForMac(macId)
    app.followUpQueueScheduler.cancelForMac(macId)
    val prefix = "$macId\u001f"
    promptDraftsByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(promptDraftsByContext::remove)
    followUpQueuesByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(followUpQueuesByContext::remove)
    followUpDeadlinesByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(followUpDeadlinesByContext::remove)
    localSettingsByWorkspace.keys
        .filter { it.startsWith(prefix) }
        .forEach(localSettingsByWorkspace::remove)
    dirtyWorkspaceSettingFields.keys
        .filter { it.startsWith(prefix) }
        .forEach(dirtyWorkspaceSettingFields::remove)
    promptOutboxByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(promptOutboxByContext::remove)
    acceptedPromptResultsByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(acceptedPromptResultsByContext::remove)
    selectedAgentsByContext.keys
        .filter { it.startsWith(prefix) }
        .forEach(selectedAgentsByContext::remove)
    workExpansionStatesByWorkspace.keys
        .filter { it.startsWith(prefix) }
        .forEach(workExpansionStatesByWorkspace::remove)
    viewedAgentCompletions.removeAll { it.startsWith(prefix) }
    unreadRunCompletions.removeAll { it.startsWith(prefix) }
    runCatching { mobileStateStore.update { it.withoutMac(macId) } }
    selectedWorkspaceByMac.remove(macId)
    domainRepositories.conversations.viewedRunIdsByWorkspace.keys.removeAll {
        it.startsWith(prefix)
    }
    detachedConversationContexts.removeAll { it.startsWith(prefix) }
    domainRepositories.conversations.detachedResumeSessions.keys.removeAll { it.startsWith(prefix) }
    cachedWorkspacesByMac.remove(macId)
    cachedDevices = cachedDevices.filterNot { it.deviceId == macId }
    scheduledCompletionRuns.removeAll { it.startsWith("$macId\u001f") }
    removeRunContexts(removePersisted = true) { it.belongsToMac(macId) }
    domainRepositories.removeMachine(macId)
    publishConversationPreferences()
    persistPromptStateSnapshot()
    scheduleMobileStateSave()
}

internal fun MainViewModel.forgetMobileStateForWorkspace(macId: String, workspaceId: String) {
    app.promptSubmissionScheduler.cancelForWorkspace(macId, workspaceId)
    app.runCompletionScheduler.cancelForWorkspace(macId, workspaceId)
    app.followUpQueueScheduler.cancelForWorkspace(macId, workspaceId)
    val workspaceKey = workspaceSettingsKey(macId, workspaceId)
    promptDraftsByContext.keys
        .filter { it == workspaceKey || it.startsWith("$workspaceKey\u001f") }
        .forEach(promptDraftsByContext::remove)
    val queuePrefix = "$workspaceKey\u001f"
    followUpQueuesByContext.keys
        .filter { it.startsWith(queuePrefix) }
        .forEach(followUpQueuesByContext::remove)
    followUpDeadlinesByContext.keys
        .filter { it.startsWith(queuePrefix) }
        .forEach(followUpDeadlinesByContext::remove)
    localSettingsByWorkspace.remove(workspaceKey)
    dirtyWorkspaceSettingFields.remove(workspaceKey)
    promptOutboxByContext.remove(workspaceKey)
    acceptedPromptResultsByContext.remove(workspaceKey)
    selectedAgentsByContext.keys
        .filter { it.startsWith("$workspaceKey\u001f") }
        .forEach(selectedAgentsByContext::remove)
    workExpansionStatesByWorkspace.keys
        .filter { it == workspaceKey || it.startsWith("$workspaceKey\u001f") }
        .forEach(workExpansionStatesByWorkspace::remove)
    viewedAgentCompletions.removeAll { it.startsWith("$workspaceKey\u001f") }
    unreadRunCompletions.removeAll { it.startsWith("$workspaceKey\u001f") }
    runCatching {
        mobileStateStore.update { persisted ->
            persisted.copy(
                unreadRunCompletions = persisted.unreadRunCompletions.filterNotTo(
                    mutableSetOf()
                ) { it.startsWith("$workspaceKey\u001f") },
            )
        }
    }
    if (selectedWorkspaceByMac[macId] == workspaceId) {
        selectedWorkspaceByMac.remove(macId)
    }
    domainRepositories.conversations.attach(workspaceKey)
    cachedWorkspacesByMac[macId] = cachedWorkspacesByMac[macId]
        .orEmpty()
        .filterNot { it.workspaceId == workspaceId }
    if (cachedWorkspacesByMac[macId].isNullOrEmpty()) {
        cachedWorkspacesByMac.remove(macId)
    }
    scheduledCompletionRuns.removeAll { it.startsWith("$workspaceKey\u001f") }
    removeRunContexts(removePersisted = true) {
        it.belongsToWorkspace(macId, workspaceId)
    }
    domainRepositories.removeWorkspace(workspaceId)
    publishConversationPreferences()
    persistPromptStateSnapshot()
    scheduleMobileStateSave()
}

internal fun MainViewModel.persistPromptSubmission(
    contextKey: String,
    submission: PendingPromptSubmission?,
) {
    if (submission == null) promptOutboxByContext.remove(contextKey)
    else promptOutboxByContext[contextKey] = submission
    runCatching {
        mobileStateStore.update { state ->
            state.copy(
                promptOutbox = if (submission == null) {
                    state.promptOutbox - contextKey
                } else {
                    state.promptOutbox + (contextKey to submission)
                }
            )
        }
    }
}

internal fun MainViewModel.persistAcceptedPromptResult(
    contextKey: String,
    result: AcceptedPromptResult?,
) {
    if (result == null) acceptedPromptResultsByContext.remove(contextKey)
    else acceptedPromptResultsByContext[contextKey] = result
    runCatching {
        mobileStateStore.update { state ->
            state.copy(
                acceptedPromptResults = if (result == null) {
                    state.acceptedPromptResults - contextKey
                } else {
                    state.acceptedPromptResults + (contextKey to result)
                }
            )
        }
    }
}

private fun MainViewModel.persistPromptStateSnapshot() {
    val outbox = promptOutboxByContext.toMap()
    val accepted = acceptedPromptResultsByContext.toMap()
    runCatching {
        mobileStateStore.update { state ->
            state.copy(
                promptOutbox = outbox,
                acceptedPromptResults = accepted,
            )
        }
    }
}

internal fun MainViewModel.synchronizePersistedBackgroundState(persisted: PersistedMobileState) {
    if (followUpQueuesByContext == persisted.followUpQueueItems &&
        followUpDeadlinesByContext == persisted.followUpDeadlines &&
        promptOutboxByContext == persisted.promptOutbox &&
        acceptedPromptResultsByContext == persisted.acceptedPromptResults &&
        unreadRunCompletions == persisted.unreadRunCompletions
    ) {
        if (uiForeground) resumeAcceptedPromptResults()
        return
    }
    followUpQueuesByContext.clear()
    followUpQueuesByContext.putAll(persisted.followUpQueueItems)
    followUpDeadlinesByContext.clear()
    followUpDeadlinesByContext.putAll(persisted.followUpDeadlines)
    promptOutboxByContext.clear()
    promptOutboxByContext.putAll(persisted.promptOutbox)
    acceptedPromptResultsByContext.clear()
    acceptedPromptResultsByContext.putAll(persisted.acceptedPromptResults)
    unreadRunCompletions.clear()
    unreadRunCompletions.addAll(persisted.unreadRunCompletions)
    val snapshot = state.value
    val visibleKey = snapshot.visibleRunId?.let { runId ->
        val macId = snapshot.selectedMacId ?: return@let null
        val workspaceId = snapshot.selectedWorkspaceId ?: return@let null
        followUpQueueKey(macId, workspaceId, runId)
    }
    val visibleQueue = visibleKey
        ?.let { followUpQueuesByContext[it] }
        .orEmpty()
        .map { it.asUiItem() }
    val workspaceKey = snapshot.selectedMacId?.let { macId ->
        snapshot.selectedWorkspaceId?.let { workspaceId ->
            workspaceSettingsKey(macId, workspaceId)
        }
    }
    val pendingSubmission = workspaceKey?.let(promptOutboxByContext::get)
    queuedRunId = snapshot.visibleRunId?.takeIf { visibleQueue.isNotEmpty() }
    mutableState.update { current ->
        current.copy(
            followUpQueue = visibleQueue,
            followUpSecondsRemaining = visibleKey
                ?.let(followUpDeadlinesByContext::get)
                ?.let { deadline ->
                    followUpCountdownSeconds(deadline, System.currentTimeMillis())
                },
            pendingSubmission = pendingSubmission,
            storageConfirmation = pendingSubmission
                ?.takeIf {
                    it.status == PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION
                }
                ?.error
                ?.let { message ->
                    friendlyStoredError(
                        message.ifBlank { "This task requires storage confirmation" },
                        current.appLanguage,
                    )
                },
            unreadRunCompletions = unreadRunCompletions.toSet(),
        )
    }
    refreshWorkspaceLocalActivity()
    if (uiForeground && visibleQueue.isNotEmpty() &&
        isTerminalRunStatus(state.value.runStatus)
    ) {
        reconcileFollowUpQueue(resetCountdown = false)
    }
    if (uiForeground) {
        resumeAcceptedPromptResults()
        ensureFollowUpCoordinator()
    }
}

internal fun MainViewModel.mobileStateSnapshot(): PersistedMobileState = PersistedMobileState(
    selectedMacId = state.value.selectedMacId,
    cachedDevices = cachedDevices,
    selectedWorkspaceByMac = selectedWorkspaceByMac.toMap(),
    viewedRunIdsByWorkspace = domainRepositories.conversations.viewedRunIdsByWorkspace.toMap(),
    detachedConversationContexts = detachedConversationContexts.toSet(),
    detachedResumeSessions = domainRepositories.conversations.detachedResumeSessions.toMap(),
    cachedWorkspacesByMac = cachedWorkspacesByMac.mapValues {
        (_, workspaces) -> workspaces.toList()
    },
    promptDrafts = promptDraftsByContext.toMap(),
    followUpQueueItems = followUpQueuesByContext.mapValues { it.value.toList() },
    followUpDeadlines = followUpDeadlinesByContext.toMap(),
    workspaceSettings = localSettingsByWorkspace.toMap(),
    dirtyWorkspaceSettingFields = dirtyWorkspaceSettingFields
        .mapValues { (_, fields) -> fields.toSet() },
    defaultSettingsByMac = defaultSettingsByMac.toMap(),
    dirtyDefaultSettingFieldsByMac = dirtyDefaultSettingFieldsByMac
        .mapValues { (_, fields) -> fields.toSet() },
    promptOutbox = promptOutboxByContext.toMap(),
    acceptedPromptResults = acceptedPromptResultsByContext.toMap(),
    selectedAgents = selectedAgentsByContext.toMap(),
    workExpansionStates = workExpansionStatesByWorkspace.toMap(),
    viewedAgentCompletions = viewedAgentCompletions.toSet(),
    unreadRunCompletions = unreadRunCompletions.toSet(),
)

internal fun MainViewModel.scheduleMobileStateSave() {
    refreshWorkspaceLocalActivity()
    val snapshot = mobileStateSnapshot()
    val revision = mobileStateSaveRevision.incrementAndGet()
    mobileStateSaveJob?.cancel()
    mobileStateSaveJob = viewModelScope.launch {
        delay(250)
        runCatching {
            withContext(Dispatchers.IO) { persistMobileStateSnapshot(snapshot, revision) }
        }
    }
}

internal fun MainViewModel.persistMobileStateSnapshot(
    snapshot: PersistedMobileState,
    revision: Long = mobileStateSaveRevision.incrementAndGet(),
) {
    mobileStateStore.update { current ->
        if (revision != mobileStateSaveRevision.get()) return@update current
        snapshot.copy(
            followUpQueueItems = if (uiForeground) {
                snapshot.followUpQueueItems
            } else {
                current.followUpQueueItems
            },
            followUpDeadlines = if (uiForeground) {
                snapshot.followUpDeadlines
            } else {
                current.followUpDeadlines
            },
            promptOutbox = current.promptOutbox,
            acceptedPromptResults = current.acceptedPromptResults,
            feedbackOutbox = current.feedbackOutbox,
            unreadRunCompletions = current.unreadRunCompletions,
        )
    }
}

internal fun MainViewModel.clearPersistedMobileState() {
    app.promptSubmissionScheduler.cancelAll()
    app.runCompletionScheduler.cancelAll()
    app.followUpQueueScheduler.cancelAll()
    app.feedbackSubmissionScheduler.cancelAll()
    mobileStateSaveJob?.cancel()
    mobileStateSaveJob = null
    followUpCoordinatorTask?.cancel()
    followUpCoordinatorTask = null
    backgroundFollowUpInFlight.clear()
    backgroundFollowUpJobs.values.toList().forEach { it.cancel() }
    backgroundFollowUpJobs.clear()
    promptDraftsByContext.clear()
    followUpQueuesByContext.clear()
    followUpDeadlinesByContext.clear()
    localSettingsByWorkspace.clear()
    dirtyWorkspaceSettingFields.clear()
    defaultSettingsByMac.clear()
    dirtyDefaultSettingFieldsByMac.clear()
    promptOutboxByContext.clear()
    acceptedPromptResultsByContext.clear()
    selectedAgentsByContext.clear()
    workExpansionStatesByWorkspace.clear()
    viewedAgentCompletions.clear()
    unreadRunCompletions.clear()
    selectedWorkspaceByMac.clear()
    domainRepositories.conversations.viewedRunIdsByWorkspace.clear()
    detachedConversationContexts.clear()
    domainRepositories.conversations.detachedResumeSessions.clear()
    cachedWorkspacesByMac.clear()
    cachedDevices = emptyList()
    submittingPromptIds.clear()
    acceptedPromptAdoptionsInFlight.clear()
    scheduledCompletionRuns.clear()
    runPollingByContext.values.toList().forEach { it.cancel() }
    runPollingByContext.clear()
    runRequestIds.clear()
    agentRequestIds.clear()
    runHistoryRequestIds.clear()
    loadedRunHistories.clear()
    runStateStore.clear()
    conversationReadStore.removeWhere { true }
    pendingStorageActions.clear()
    runCatching { mobileStateStore.clear() }
    mutableState.update {
        it.copy(
            workExpansionStates = emptyMap(),
            viewedAgentCompletions = emptySet(),
            unreadRunCompletions = emptySet(),
        )
    }
}

private fun MainViewModel.refreshWorkspaceLocalActivity() {
    val macId = state.value.selectedMacId ?: return
    mutableState.update {
        it.copy(
            workspaceQueueCounts = workspaceQueueCounts(macId, it.workspaces),
            workspacePromptStates = workspacePromptStates(macId, it.workspaces),
        )
    }
}

internal fun MainViewModel.workspaceQueueCounts(
    macId: String,
    workspaces: List<RemoteWorkspace>,
): Map<String, Int> = workspaces.mapNotNull { workspace ->
    val prefix = "${workspaceSettingsKey(macId, workspace.workspaceId)}\u001f"
    val count = followUpQueuesByContext
        .filterKeys { it.startsWith(prefix) }
        .values
        .sumOf(List<PersistedQueuedFollowUp>::size)
    count.takeIf { it > 0 }?.let { workspace.workspaceId to it }
}.toMap()

internal fun MainViewModel.workspacePromptStates(
    macId: String,
    workspaces: List<RemoteWorkspace>,
): Map<String, String> = workspaces.mapNotNull { workspace ->
    promptOutboxByContext[
        workspaceSettingsKey(macId, workspace.workspaceId)
    ]?.status?.let { workspace.workspaceId to it }
}.toMap()

internal fun MainViewModel.hasLargeStorageApproval(): Boolean {
    val macId = state.value.selectedMacId ?: return false
    val workspaceId = state.value.selectedWorkspaceId ?: return false
    return workspaceSettingsKey(macId, workspaceId) in approvedLargeStorageWorkspaces
}

internal fun MainViewModel.rememberLargeStorageApproval() {
    val macId = state.value.selectedMacId ?: return
    val workspaceId = state.value.selectedWorkspaceId ?: return
    approvedLargeStorageWorkspaces += workspaceSettingsKey(macId, workspaceId)
}

internal fun MainViewModel.promptDraftKey(current: MainUiState): String? {
    val macId = current.selectedMacId ?: return null
    val workspaceId = current.selectedWorkspaceId ?: return null
    return promptDraftContextKey(
        macId = macId,
        workspaceId = workspaceId,
        conversationId = current.visibleConversationId,
        resumeSessionId = current.runSettings.resumeSessionId,
        runId = current.visibleRunId,
    )
}

internal fun MainViewModel.restorePromptDraft(
    macId: String,
    workspaceId: String,
    conversationId: String?,
    resumeSessionId: String,
    runId: String? = null,
): String {
    return restorePromptDraft(
        drafts = promptDraftsByContext,
        macId = macId,
        workspaceId = workspaceId,
        conversationId = conversationId,
        resumeSessionId = resumeSessionId,
        runId = runId,
    )
}

internal fun MainViewModel.rememberPromptDraft() {
    val current = state.value
    val key = promptDraftKey(current) ?: return
    if (current.visibleRunId == null && !current.visibleConversationId.isNullOrBlank() &&
        !isNoResumeSession(current.runSettings.resumeSessionId)) {
        promptDraftsByContext.remove(promptDraftContextKey(
            checkNotNull(current.selectedMacId), checkNotNull(current.selectedWorkspaceId),
            null, current.runSettings.resumeSessionId,
        ))
    }
    if (current.prompt.isEmpty() && ((current.visibleRunId == null &&
            isNoResumeSession(current.runSettings.resumeSessionId)) ||
            !current.visibleConversationId.isNullOrBlank() || key !in promptDraftsByContext)) {
        promptDraftsByContext.remove(key)
    }
    else promptDraftsByContext[key] = current.prompt
    scheduleMobileStateSave()
}

internal fun MainViewModel.rememberPromptDraft(
    macId: String,
    workspaceId: String,
    conversationId: String?,
    resumeSessionId: String,
    runId: String? = null,
) {
    val value = state.value.prompt
    val key = promptDraftContextKey(
        macId = macId,
        workspaceId = workspaceId,
        conversationId = conversationId,
        resumeSessionId = resumeSessionId,
        runId = runId,
    )
    if (runId == null && !conversationId.isNullOrBlank() && !isNoResumeSession(resumeSessionId)) {
        promptDraftsByContext.remove(promptDraftContextKey(macId, workspaceId, null, resumeSessionId))
    }
    if (value.isEmpty() && ((runId == null && isNoResumeSession(resumeSessionId)) || !conversationId.isNullOrBlank() ||
            key !in promptDraftsByContext)) promptDraftsByContext.remove(key)
    else promptDraftsByContext[key] = value
    scheduleMobileStateSave()
}
