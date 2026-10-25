package com.parallelcodexrunner.remote.ui

import android.os.SystemClock
import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.InternetAvailability
import com.parallelcodexrunner.remote.data.MAX_AGENT_COUNT
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.WorkspaceSettingFields
import com.parallelcodexrunner.remote.data.mergingRemote
import com.parallelcodexrunner.remote.data.normalized
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

private const val MAC_DIRECTORY_REFRESH_INTERVAL_MILLIS = 60_000L

internal fun mergeMachinePresence(
    current: List<RelayDevice>,
    refreshed: List<RelayDevice>,
): List<RelayDevice> {
    val remaining = refreshed.associateByTo(linkedMapOf(), RelayDevice::deviceId)
    return buildList {
        current.forEach { device ->
            add(remaining.remove(device.deviceId) ?: device.copy(online = false))
        }
        addAll(remaining.values)
    }
}

fun MainViewModel.refreshSelectedMachinePresence() {
    val snapshot = state.value
    val selectedMacId = snapshot.selectedMacId ?: return
    if (!uiForeground || !snapshot.signedIn ||
        snapshot.internetAvailability == InternetAvailability.UNAVAILABLE ||
        snapshot.macSelectionId != null || machinePresenceRefreshJob?.isActive == true
    ) return

    val expectedGeneration = contextGeneration
    val job = viewModelScope.launch {
        try {
            val refreshed = repository.macs()
            if (!uiForeground || expectedGeneration != contextGeneration ||
                state.value.selectedMacId != selectedMacId
            ) return@launch
            mutableState.update { current ->
                if (current.selectedMacId != selectedMacId) current
                else current.copy(macs = mergeMachinePresence(current.macs, refreshed))
            }
            clearTransientRelayConnectionMessage()
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            // Keep the last known device state. Phone connectivity and live-run
            // failures are surfaced independently without clearing cached content.
        }
    }
    machinePresenceRefreshJob = job
    job.invokeOnCompletion {
        if (machinePresenceRefreshJob === job) machinePresenceRefreshJob = null
    }
}

fun MainViewModel.refreshMacs() = refreshMacs(userInitiated = true)

internal fun MainViewModel.refreshMacs(userInitiated: Boolean) {
    if (state.value.macRefreshInProgress) return
    val generation = contextGeneration
    val authorizationGeneration = relayAuthorizationGeneration
    mutableState.update { it.copy(macRefreshInProgress = true) }
    viewModelScope.launch {
        try {
            loadMacs(generation)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (generation == contextGeneration && authorizationGeneration == relayAuthorizationGeneration &&
                shouldShowMachineRefreshError(error, userInitiated, cachedDevices.isNotEmpty())
            ) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        } finally {
            mutableState.update { it.copy(macRefreshInProgress = false) }
        }
    }
}

internal fun shouldShowMachineRefreshError(
    error: Throwable,
    userInitiated: Boolean,
    hasCachedDevices: Boolean,
): Boolean = userInitiated || !hasCachedDevices || !isLiveUpdateConnectionFailure(error)

internal fun MainViewModel.beginInitialSync(): Int {
    val requestId = ++initialSyncRequestId
    mutableState.update {
        it.copy(initialSyncProgress = InitialSyncProgress(InitialSyncStage.CONNECTING))
    }
    return requestId
}

internal fun MainViewModel.updateInitialSync(
    requestId: Int,
    stage: InitialSyncStage,
) {
    if (requestId != initialSyncRequestId) return
    mutableState.update { current ->
        current.copy(initialSyncProgress = InitialSyncProgress(stage))
    }
}

internal fun MainViewModel.finishInitialSync(requestId: Int) {
    if (requestId != initialSyncRequestId) return
    mutableState.update { current ->
        current.copy(initialSyncProgress = null)
    }
}

fun MainViewModel.refreshWorkspaceOverview() {
    val snapshot = state.value
    if (!uiForeground || workspaceOverviewRefreshJob?.isActive == true || snapshot.busy ||
        snapshot.macSelectionId != null || snapshot.workspaceSelectionId != null
    ) return
    val mac = selectedMac() ?: return
    val generation = contextGeneration
    workspaceOverviewRefreshJob = viewModelScope.launch {
        try {
            if (mac.online == false ||
                SystemClock.elapsedRealtime() >= nextMacDirectoryRefreshAtMillis
            ) {
                loadMacs(generation, checkForUpdates = false)
            } else {
                loadSelectedMacOverview(mac, generation)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            nextMacDirectoryRefreshAtMillis = 0L
            // The visible run poll and the next overview refresh will retry.
        }
    }
}

internal suspend fun MainViewModel.loadMacs(
    expectedGeneration: Int,
    checkForUpdates: Boolean = true,
    forceVisibleRunRefresh: Boolean = false,
    initialSyncId: Int? = null,
) {
    val requestId = ++macsRequestId
    val macs = repository.macs()
    if (requestId != macsRequestId || expectedGeneration != contextGeneration) return
    nextMacDirectoryRefreshAtMillis = SystemClock.elapsedRealtime() +
        MAC_DIRECTORY_REFRESH_INTERVAL_MILLIS
    clearTransientRelayConnectionMessage()
    val availableMacIds = macs.mapTo(mutableSetOf(), RelayDevice::deviceId)
    val cachedMacIds = buildSet {
        addAll(defaultSettingsByMac.keys)
        addAll(defaultPrimaryOptionsByMac.keys)
        addAll(defaultSynthesisOptionsByMac.keys)
        addAll(defaultSettingsSaveJobs.keys)
        addAll(dirtyDefaultSettingFieldsByMac.keys)
    }
    (cachedMacIds - availableMacIds).forEach(::forgetDefaultSettingsForMac)
    val locallyRememberedMacIds = buildSet {
        state.value.selectedMacId?.let(::add)
        cachedDevices.mapTo(this, RelayDevice::deviceId)
        addAll(selectedWorkspaceByMac.keys)
        addAll(cachedWorkspacesByMac.keys)
        promptDraftsByContext.keys.mapTo(this) { it.substringBefore('\u001f') }
        followUpQueuesByContext.keys.mapTo(this) { it.substringBefore('\u001f') }
        localSettingsByWorkspace.keys.mapTo(this) { it.substringBefore('\u001f') }
        dirtyWorkspaceSettingFields.keys.mapTo(this) { it.substringBefore('\u001f') }
        promptOutboxByContext.keys.mapTo(this) { it.substringBefore('\u001f') }
    }
    (locallyRememberedMacIds - availableMacIds).forEach(::forgetMobileStateForMac)
    if (cachedDevices != macs) {
        cachedDevices = macs
        scheduleMobileStateSave()
    }
    val requestedNavigation = pendingRunNavigation
    val requestedMac = requestedNavigation?.macId?.takeIf { id ->
        macs.any { it.deviceId == id }
    }
    if (requestedNavigation != null && requestedMac == null) {
        pendingRunNavigation = null
        showError(state.value.appLanguage.text(
            "发出通知的设备已不在设备列表中。",
            "The device that sent this notification is no longer available.",
        ))
    }
    val selected = requestedMac ?: state.value.selectedMacId?.takeIf { id ->
        macs.any { it.deviceId == id }
    } ?: macs.firstOrNull()?.deviceId
    initialSyncId?.let { updateInitialSync(it, InitialSyncStage.COMPUTER) }
    val macChanged = selected != state.value.selectedMacId
    if (macChanged) {
        rememberPromptDraft()
        resetDefaultSettingsContext()
        updateHistoryRequestId += 1
        advanceContext()
    }
    mutableState.update { current ->
        if (!macChanged) {
            current.copy(macs = macs, selectedMacId = selected)
        } else {
            current.copy(
                macs = macs,
                selectedMacId = selected,
                macSelectionId = null,
                maximumAgentCount = MAX_AGENT_COUNT,
                machinePlatform = "",
                prompt = "",
                workspaces = emptyList(),
                selectedWorkspaceId = null,
                workspaceSelectionId = null,
                runSettings = RemoteRunSettings(),
                primaryModelOptions = null,
                synthesisModelOptions = null,
                resumeSessions = emptyList(),
                settingsOptionsLoading = false,
                settingsOptionsError = null,
                resumeSessionsLoading = false,
                resumeSessionsError = null,
                defaultRunSettings = RemoteRunSettings(),
                defaultSettingsMacId = null,
                defaultPrimaryModelOptions = null,
                defaultSynthesisModelOptions = null,
                defaultSettingsLoading = false,
                defaultSettingsSaving = false,
                defaultSettingsError = null,
                defaultSettingsOptionsError = null,
                visibleRunId = null,
                visibleConversationId = null,
                visibleConversationTitle = "",
                runStatus = "",
                canAddAgents = false,
                canUpdateSynthesis = false,
                runPrompt = "",
                agentSummaries = emptyList(),
                selectedAgent = null,
                recommendedAgent = null,
                finalizedAgent = null,
                conversationHistory = emptyList(),
                agentTranscripts = emptyMap(),
                updateChecking = false,
                updateHistoryOpen = false,
                updateHistory = emptyList(),
                updateHistoryLoading = false,
                updateHistoryError = null,
                updateHistoryNextCursor = 0,
                updateHistoryHasMore = false,
            )
        }
    }
    scheduleMobileStateSave()
    resumeAcceptedPromptResults()
    retryDirtyDefaultSettings(macs)
    val mac = selectedMac()
    if (mac == null) {
        updateRequestId += 1
        mutableState.update {
            it.copy(
                workspaces = emptyList(),
                selectedWorkspaceId = null,
                workspaceSelectionId = null,
                maximumAgentCount = MAX_AGENT_COUNT,
                machinePlatform = "",
                runSettings = RemoteRunSettings(),
                primaryModelOptions = null,
                synthesisModelOptions = null,
                resumeSessions = emptyList(),
                settingsOptionsLoading = false,
                settingsOptionsError = null,
                resumeSessionsLoading = false,
                resumeSessionsError = null,
                defaultRunSettings = RemoteRunSettings(),
                defaultSettingsMacId = null,
                defaultPrimaryModelOptions = null,
                defaultSynthesisModelOptions = null,
                defaultSettingsLoading = false,
                defaultSettingsSaving = false,
                defaultSettingsError = null,
                defaultSettingsOptionsError = null,
                visibleRunId = null,
                visibleConversationId = null,
                visibleConversationTitle = "",
                runStatus = "",
                canAddAgents = false,
                canUpdateSynthesis = false,
                runPrompt = "",
                agentSummaries = emptyList(),
                selectedAgent = null,
                recommendedAgent = null,
                finalizedAgent = null,
                conversationHistory = emptyList(),
                agentTranscripts = emptyMap(),
                updateChecking = false,
                updateHistoryOpen = false,
                updateHistory = emptyList(),
                updateHistoryLoading = false,
                updateHistoryError = null,
                updateHistoryNextCursor = 0,
                updateHistoryHasMore = false,
            )
        }
    } else {
        if (macChanged) restoreCachedWorkspaces(mac)
        val appliedGeneration = contextGeneration
        if (checkForUpdates) startAndroidUpdateCheck(mac, silent = true)
        if (mac.online == false) {
            restoreCachedWorkspaces(mac)
            return
        }
        loadSelectedMacOverview(
            mac,
            appliedGeneration,
            preferredWorkspaceId = pendingRunNavigation
                ?.takeIf { it.macId == mac.deviceId }
                ?.workspaceId,
            forceVisibleRunRefresh = forceVisibleRunRefresh,
            initialSyncId = initialSyncId,
        )
    }
    initialSyncId?.let { updateInitialSync(it, InitialSyncStage.COMPLETE) }
}

private suspend fun MainViewModel.loadSelectedMacOverview(
    mac: RelayDevice,
    expectedGeneration: Int,
    preferredWorkspaceId: String? = null,
    forceVisibleRunRefresh: Boolean = false,
    initialSyncId: Int? = null,
) {
    val capabilities = repository.systemCapabilities(mac)
    if (expectedGeneration != contextGeneration || selectedMac()?.deviceId != mac.deviceId) {
        return
    }
    mutableState.update {
        it.copy(
            maximumAgentCount = capabilities.clientMaximumAgentCount,
            machinePlatform = capabilities.platform,
        )
    }
    initialSyncId?.let { updateInitialSync(it, InitialSyncStage.WORKSPACES) }
    loadWorkspaces(
        mac,
        expectedGeneration,
        preferredWorkspaceId = preferredWorkspaceId,
        forceVisibleRunRefresh = forceVisibleRunRefresh,
    )
}

fun MainViewModel.selectMac(deviceId: String) {
    val snapshot = state.value
    if (deviceId == snapshot.selectedMacId || deviceId == snapshot.macSelectionId) return
    rememberPromptDraft()
    resetDefaultSettingsContext()
    val generation = advanceContext(retainStoredFollowUps = true)
    updateRequestId += 1
    updateHistoryRequestId += 1
    mutableState.update {
        it.copy(
            selectedMacId = deviceId,
            macSelectionId = deviceId,
            maximumAgentCount = MAX_AGENT_COUNT,
            machinePlatform = "",
            prompt = "",
            workspaces = emptyList(),
            selectedWorkspaceId = null,
            workspaceSelectionId = null,
            runSettings = RemoteRunSettings(),
            primaryModelOptions = null,
            synthesisModelOptions = null,
            resumeSessions = emptyList(),
            settingsOptionsLoading = false,
            settingsOptionsError = null,
            resumeSessionsLoading = false,
            resumeSessionsError = null,
            defaultRunSettings = RemoteRunSettings(),
            defaultSettingsMacId = null,
            defaultPrimaryModelOptions = null,
            defaultSynthesisModelOptions = null,
            defaultSettingsLoading = false,
            defaultSettingsSaving = false,
            defaultSettingsError = null,
            defaultSettingsOptionsError = null,
            visibleRunId = null,
            visibleConversationId = null,
            visibleConversationTitle = "",
            runStatus = "",
            canAddAgents = false,
            canUpdateSynthesis = false,
            runPrompt = "",
            agentSummaries = emptyList(),
            selectedAgent = null,
            recommendedAgent = null,
            finalizedAgent = null,
            conversationHistory = emptyList(),
            agentTranscripts = emptyMap(),
            updateHistoryOpen = false,
            updateHistory = emptyList(),
            updateHistoryLoading = false,
            updateHistoryError = null,
            updateHistoryNextCursor = 0,
            updateHistoryHasMore = false,
        )
    }
    scheduleMobileStateSave()
    val mac = selectedMac()
    if (mac == null) {
        mutableState.update { it.copy(macSelectionId = null) }
        return
    }
    // Paint the target computer's last known workspace state immediately.
    // The blocking transition layer keeps it read-only until live data arrives,
    // avoiding the empty-workspace flash caused by clearing the old computer.
    restoreCachedWorkspaces(mac)
    startAndroidUpdateCheck(mac, silent = true)
    if (mac.online == false) {
        mutableState.update { it.copy(macSelectionId = null) }
        return
    }
    viewModelScope.launch {
        try {
            val capabilities = repository.systemCapabilities(mac)
            if (generation != contextGeneration || selectedMac()?.deviceId != deviceId) {
                return@launch
            }
            mutableState.update {
                it.copy(
                    maximumAgentCount = capabilities.clientMaximumAgentCount,
                    machinePlatform = capabilities.platform,
                )
            }
            loadWorkspaces(mac, generation)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (state.value.macSelectionId == deviceId) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        } finally {
            mutableState.update { current ->
                if (current.macSelectionId == deviceId) {
                    current.copy(macSelectionId = null)
                } else {
                    current
                }
            }
        }
    }
}

fun MainViewModel.requestMacRevocation(deviceId: String) = mutableState.update {
    it.copy(macRevocation = deviceId)
}

fun MainViewModel.confirmMacRevocation() = execute {
    val deviceId = state.value.macRevocation ?: return@execute
    if (deviceId == state.value.selectedMacId) {
        requireNoQueuedFollowUps("移除当前设备")
        resolveCurrentRunBeforeContextChange("移除当前设备")
    }
    mutableState.update { it.copy(macRevocation = null) }
    repository.revokeMac(deviceId)
    forgetDefaultSettingsForMac(deviceId)
    forgetMobileStateForMac(deviceId)
    // Removing another Mac must not cancel polling, queued follow-ups, or
    // the reading context of the currently selected Mac. If the selected
    // Mac was removed, loadMacs detects that selection change and resets
    // the context itself.
    loadMacs(contextGeneration)
}

fun MainViewModel.cancelMacRevocation() = mutableState.update { it.copy(macRevocation = null) }

internal fun MainViewModel.restoreCachedWorkspaces(
    mac: RelayDevice,
    preferredWorkspaceId: String? = null,
    preferredRunId: String? = null,
    contextAlreadyAdvanced: Boolean = false,
    allowUnknownPreferredWorkspace: Boolean = false,
) {
    val cached = cachedWorkspacesByMac[mac.deviceId].orEmpty().ifEmpty {
        state.value.workspaces.takeIf { state.value.selectedMacId == mac.deviceId }.orEmpty()
    }
    val previousWorkspaceId = state.value.selectedWorkspaceId
    val selectedWorkspaceId = preferredWorkspaceId
        ?.takeIf { preferred ->
            preferred.isNotBlank() && (
                allowUnknownPreferredWorkspace || cached.any { it.workspaceId == preferred }
            )
        }
        ?: previousWorkspaceId
            ?.takeIf { previous -> cached.any { it.workspaceId == previous } }
        ?: selectedWorkspaceByMac[mac.deviceId]
            ?.takeIf { remembered -> cached.any { it.workspaceId == remembered } }
        ?: cached.firstOrNull()?.workspaceId
    val selectedWorkspace = cached.firstOrNull {
        it.workspaceId == selectedWorkspaceId
    }
    val settingsKey = selectedWorkspaceId?.let {
        workspaceSettingsKey(mac.deviceId, it)
    }
    if (settingsKey != null && !preferredRunId.isNullOrBlank()) {
        domainRepositories.conversations.selectRun(settingsKey, preferredRunId)
    }
    val lastRunId = domainRepositories.conversations.displayedRunId(
        settingsKey,
        latestWorkspaceRunId(selectedWorkspace),
    )
    val runKey = if (selectedWorkspaceId != null && lastRunId != null) {
        WorkspaceRunKey(mac.deviceId, selectedWorkspaceId, lastRunId)
    } else {
        null
    }
    val runSnapshot = runKey?.let(runStateStore::get)
    if (selectedWorkspaceId == null) {
        selectedWorkspaceByMac.remove(mac.deviceId)
    } else {
        selectedWorkspaceByMac[mac.deviceId] = selectedWorkspaceId
    }
    val pendingSubmission = settingsKey?.let(promptOutboxByContext::get)
    val settings = (settingsKey?.let(localSettingsByWorkspace::get)
        ?: selectedWorkspace?.settings
        ?: RemoteRunSettings(
            resumeSessionId = selectedWorkspace?.lastResumeSessionId.orEmpty(),
        )).let { settings ->
            domainRepositories.conversations.settingsForDraft(settingsKey,
                settings.forConversationHistory(lastRunId, selectedWorkspace?.lastResumeSessionId.orEmpty()))
        }
    val contextChanged = contextAlreadyAdvanced || previousWorkspaceId != selectedWorkspaceId ||
        state.value.conversationSourceChanged(lastRunId, settings.resumeSessionId)
    if (contextChanged && !contextAlreadyAdvanced) {
        rememberPromptDraft()
        advanceContext(retainStoredFollowUps = true)
    }
    val pendingRunStorageMessage = if (selectedWorkspaceId != null && lastRunId != null) {
        pendingStorageActions[
            RunMutationContext(mac, selectedWorkspaceId, lastRunId, settings.normalized())
        ]?.message
    } else {
        null
    }
    val restoredQueue = if (contextChanged && selectedWorkspaceId != null) {
        restoredFollowUpQueue(
            macId = mac.deviceId,
            workspaceId = selectedWorkspaceId,
            runId = lastRunId,
        )
    } else {
        state.value.followUpQueue
    }
    queuedRunId = lastRunId?.takeIf { restoredQueue.isNotEmpty() }
    mutableState.update { current ->
        current.copy(
            workspaces = cached,
            workspaceQueueCounts = workspaceQueueCounts(mac.deviceId, cached),
            workspacePromptStates = workspacePromptStates(mac.deviceId, cached),
            selectedWorkspaceId = selectedWorkspaceId,
            workspaceSelectionId = null,
            workspaceRevision = 0,
            prompt = if (contextChanged) {
                selectedWorkspaceId?.let { workspaceId ->
                    restorePromptDraft(
                        macId = mac.deviceId,
                        workspaceId = workspaceId,
                        conversationId = runSnapshot?.conversationId,
                        resumeSessionId = settings.resumeSessionId,
                        runId = lastRunId,
                    )
                }.orEmpty()
            } else {
                current.prompt
            },
            visibleRunId = lastRunId,
            visibleConversationId = if (contextChanged) {
                runSnapshot?.conversationId?.takeIf(String::isNotBlank)
            } else current.visibleConversationId,
            visibleConversationTitle = if (contextChanged) {
                runSnapshot?.conversationTitle.orEmpty()
            } else current.visibleConversationTitle,
            runStatus = if (contextChanged) {
                runSnapshot?.status ?: if (lastRunId != null) {
                    selectedWorkspace?.runStatus.orEmpty()
                } else {
                    ""
                }
            }
            else current.runStatus,
            canAddAgents = if (contextChanged) runSnapshot?.canAddAgents ?: false
            else current.canAddAgents,
            canUpdateSynthesis = if (contextChanged) {
                runSnapshot?.canUpdateSynthesis ?: false
            }
            else current.canUpdateSynthesis,
            runPrompt = if (contextChanged) runSnapshot?.prompt.orEmpty()
            else current.runPrompt,
            agentSummaries = if (contextChanged) runSnapshot?.agents.orEmpty()
            else current.agentSummaries,
            selectedAgent = if (contextChanged) runSnapshot?.selectedAgent
            else current.selectedAgent,
            recommendedAgent = if (contextChanged) runSnapshot?.recommendedAgent
            else current.recommendedAgent,
            finalizedAgent = if (contextChanged) runSnapshot?.finalizedAgent
            else current.finalizedAgent,
            conversationHistory = if (contextChanged) {
                runSnapshot?.conversationHistory.orEmpty()
            }
            else current.conversationHistory,
            agentTranscripts = if (contextChanged) runSnapshot?.agentTranscripts.orEmpty()
            else current.agentTranscripts,
            agentDiffSummaries = if (contextChanged) {
                runSnapshot?.agentDiffSummaries.orEmpty()
            } else current.agentDiffSummaries,
            runSettings = settings.normalized(),
            primaryModelOptions = if (contextChanged) null
            else current.primaryModelOptions,
            synthesisModelOptions = if (contextChanged) null
            else current.synthesisModelOptions,
            resumeSessions = if (contextChanged) emptyList()
            else current.resumeSessions,
            followUpQueue = restoredQueue,
            followUpSecondsRemaining = null,
            pendingSubmission = pendingSubmission,
            storageConfirmation = pendingSubmission
                ?.takeIf {
                    it.status == PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION
                }
                ?.error
                ?: pendingRunStorageMessage,
        )
    }
    scheduleMobileStateSave()
    if (runSnapshot == null) runKey?.let(::hydrateVisibleRunCache)
    if (lastRunId == null && selectedWorkspaceId != null) {
        effectiveResumeSessionId(
            settings,
            selectedWorkspace?.lastResumeSessionId.orEmpty(),
        ).takeIf(String::isNotBlank)?.let { sessionId ->
            hydrateResumeConversationCache(
                macId = mac.deviceId,
                workspaceId = selectedWorkspaceId,
                sessionId = sessionId,
            )
        }
    }
    resumeAcceptedPromptResults()
}

/**
 * Moves the visible state to a notification target before any network or disk wait.
 * This prevents a cold launch from painting the previously selected conversation
 * under the new destination while its cached transcript is being hydrated.
 */
internal fun MainViewModel.stagePendingRunNavigation(
    navigation: PendingRunNavigation,
): Int {
    val current = state.value
    if (!notificationNavigationChangesContext(current, navigation)) {
        return contextGeneration
    }

    rememberPromptDraft()
    val macChanged = current.selectedMacId != navigation.macId
    if (macChanged) {
        resetDefaultSettingsContext()
        updateRequestId += 1
        updateHistoryRequestId += 1
    }
    val generation = advanceContext(retainStoredFollowUps = true)
    val targetMac = current.macs.firstOrNull { it.deviceId == navigation.macId }
        ?: cachedDevices.firstOrNull { it.deviceId == navigation.macId }

    mutableState.update { snapshot ->
        snapshot.copy(
            selectedMacId = navigation.macId,
            macSelectionId = null,
            workspaceSelectionId = null,
            maximumAgentCount = if (macChanged) MAX_AGENT_COUNT else snapshot.maximumAgentCount,
            machinePlatform = if (macChanged) "" else snapshot.machinePlatform,
            defaultRunSettings = if (macChanged) {
                RemoteRunSettings()
            } else {
                snapshot.defaultRunSettings
            },
            defaultSettingsMacId = if (macChanged) null else snapshot.defaultSettingsMacId,
            defaultPrimaryModelOptions = if (macChanged) {
                null
            } else {
                snapshot.defaultPrimaryModelOptions
            },
            defaultSynthesisModelOptions = if (macChanged) {
                null
            } else {
                snapshot.defaultSynthesisModelOptions
            },
            defaultSettingsLoading = if (macChanged) false else snapshot.defaultSettingsLoading,
            defaultSettingsSaving = if (macChanged) false else snapshot.defaultSettingsSaving,
            defaultSettingsError = if (macChanged) null else snapshot.defaultSettingsError,
            defaultSettingsOptionsError = if (macChanged) {
                null
            } else {
                snapshot.defaultSettingsOptionsError
            },
            message = null,
            messageIsError = false,
        )
    }

    if (targetMac != null) {
        restoreCachedWorkspaces(
            mac = targetMac,
            preferredWorkspaceId = navigation.workspaceId,
            preferredRunId = navigation.runId,
            contextAlreadyAdvanced = true,
            allowUnknownPreferredWorkspace = true,
        )
        mutableState.update { snapshot ->
            if (snapshot.selectedMacId == navigation.macId &&
                snapshot.selectedWorkspaceId == navigation.workspaceId &&
                snapshot.visibleRunId == navigation.runId
            ) {
                snapshot.copy(requestedWorkspaceOpenId = navigation.workspaceId)
            } else {
                snapshot
            }
        }
    } else {
        val key = WorkspaceRunKey(
            navigation.macId,
            navigation.workspaceId,
            navigation.runId,
        )
        val cached = runStateStore[key]
        selectedWorkspaceByMac[navigation.macId] = navigation.workspaceId
        mutableState.update { snapshot ->
            snapshot.projectRun(cached).copy(
                workspaces = emptyList(),
                selectedWorkspaceId = navigation.workspaceId,
                requestedWorkspaceOpenId = navigation.workspaceId,
                workspaceRevision = 0,
                prompt = "",
                runSettings = RemoteRunSettings(),
                primaryModelOptions = null,
                synthesisModelOptions = null,
                resumeSessions = emptyList(),
                visibleRunId = navigation.runId,
            )
        }
        if (cached == null) hydrateVisibleRunCache(key)
    }
    return generation
}

internal fun notificationNavigationChangesContext(
    state: MainUiState,
    navigation: PendingRunNavigation,
): Boolean = state.selectedMacId != navigation.macId ||
    state.selectedWorkspaceId != navigation.workspaceId ||
    state.visibleRunId != navigation.runId

internal suspend fun MainViewModel.loadWorkspaces(
    mac: RelayDevice,
    expectedGeneration: Int,
    preferredWorkspaceId: String? = null,
    forceVisibleRunRefresh: Boolean = false,
) {
    state.value.workspaceSelectionId?.let { pendingSelection ->
        if (preferredWorkspaceId != pendingSelection) return
    }
    val requestId = ++workspacesRequestId
    val response = repository.workspaces(mac)
    if (requestId != workspacesRequestId ||
        expectedGeneration != contextGeneration ||
        state.value.selectedMacId != mac.deviceId ||
        state.value.workspaceSelectionId?.let { pendingSelection ->
            preferredWorkspaceId != pendingSelection
        } == true
    ) {
        return
    }
    clearTransientRelayConnectionMessage()
    pruneObsoleteRunContexts(mac.deviceId, response.workspaces)
    if (cachedWorkspacesByMac[mac.deviceId] != response.workspaces) {
        cachedWorkspacesByMac[mac.deviceId] = response.workspaces
        scheduleMobileStateSave()
    }
    val previousWorkspace = state.value.selectedWorkspaceId
    val previousRun = state.value.visibleRunId
    val selectedWorkspaceId = preferredWorkspaceId
        ?.takeIf { preferred ->
            response.workspaces.any { it.workspaceId == preferred }
        }
        ?: previousWorkspace?.takeIf { previous ->
            response.workspaces.any { it.workspaceId == previous }
        }
        ?: selectedWorkspaceByMac[mac.deviceId]?.takeIf { remembered ->
            response.workspaces.any { it.workspaceId == remembered }
        }
        ?: response.selectedWorkspaceId?.takeIf { remote ->
            response.workspaces.any { it.workspaceId == remote }
        }
        ?: response.workspaces.firstOrNull()?.workspaceId
    val selectedWorkspace = response.workspaces
        .firstOrNull { it.workspaceId == selectedWorkspaceId }
    val settingsKey = selectedWorkspaceId?.let {
        workspaceSettingsKey(mac.deviceId, it)
    }
    val requestedNavigation = pendingRunNavigation
        ?.takeIf { it.macId == mac.deviceId }
    if (requestedNavigation != null &&
        response.workspaces.none { it.workspaceId == requestedNavigation.workspaceId }
    ) {
        pendingRunNavigation = null
        showError(state.value.appLanguage.text(
            "发出通知的工作区已被移除。",
            "The workspace from this notification has been removed.",
        ))
    }
    val shouldOpenRequestedWorkspace = requestedNavigation?.workspaceId ==
        selectedWorkspaceId
    val requestedRun = displayedRunIdForNavigation(
        macId = mac.deviceId,
        workspaceId = selectedWorkspaceId,
        latestRunId = null,
        navigation = requestedNavigation,
    )
    if (shouldOpenRequestedWorkspace) {
        pendingRunNavigation = null
        if (settingsKey != null && requestedRun != null) {
            domainRepositories.conversations.selectRun(settingsKey, requestedRun)
        }
    }
    val rememberedWorkspaceChanged = if (selectedWorkspaceId == null) {
        selectedWorkspaceByMac.remove(mac.deviceId) != null
    } else {
        selectedWorkspaceByMac.put(mac.deviceId, selectedWorkspaceId) != selectedWorkspaceId
    }
    val latestRun = latestWorkspaceRunId(selectedWorkspace)
    val viewedRun = domainRepositories.conversations.viewedRunIdsByWorkspace[settingsKey]
    if (requestedRun == null && selectedWorkspaceId != null &&
        viewedRun != null && latestRun != null && viewedRun != latestRun
    ) {
        val viewed = runStateStore.hydrate(
            WorkspaceRunKey(mac.deviceId, selectedWorkspaceId, viewedRun)
        )
        val latest = runStateStore.hydrate(
            WorkspaceRunKey(mac.deviceId, selectedWorkspaceId, latestRun)
        )
        if (requestId != workspacesRequestId || expectedGeneration != contextGeneration ||
            state.value.selectedMacId != mac.deviceId
        ) return
        if (isNextViewedConversationTurn(
                viewedRun, latestRun, viewed?.conversationId, latest?.conversationId,
            )
        ) {
            domainRepositories.conversations.advanceViewedRun(
                checkNotNull(settingsKey), viewedRun, latestRun,
            )
        }
    }
    val latestVisibleRun = domainRepositories.conversations.displayedRunId(
        settingsKey,
        latestRun,
    )
    val lastRun = displayedRunIdForNavigation(
        macId = mac.deviceId,
        workspaceId = selectedWorkspaceId,
        latestRunId = latestVisibleRun,
        navigation = requestedNavigation,
    )
    val runKey = if (selectedWorkspaceId != null && lastRun != null) {
        WorkspaceRunKey(mac.deviceId, selectedWorkspaceId, lastRun)
    } else {
        null
    }
    val runSnapshot = runKey?.let { runStateStore.hydrate(it) }
    if (requestId != workspacesRequestId || expectedGeneration != contextGeneration ||
        state.value.selectedMacId != mac.deviceId
    ) return
    val workspaceChanged = previousWorkspace != selectedWorkspaceId
    val pendingSubmission = settingsKey?.let(promptOutboxByContext::get)
    val localSettings = settingsKey?.let(localSettingsByWorkspace::get)
    val lastResumeSessionId = selectedWorkspace?.lastResumeSessionId.orEmpty()
    val remoteSettings = selectedWorkspace?.settings?.normalized()?.let { settings ->
        if (settings.resumeSessionId.isBlank() && lastResumeSessionId.isNotBlank()) {
            settings.copy(resumeSessionId = lastResumeSessionId).normalized()
        } else {
            settings
        }
    }
    val pendingFields = if (settingsKey != null && localSettings != null) {
        dirtyWorkspaceSettingFields.getOrPut(settingsKey) {
            // Versions before field-level syncing only recorded the full local
            // snapshot. Preserve every field once while migrating that state.
            WorkspaceSettingFields.all.toMutableSet()
        }.toSet()
    } else {
        emptySet()
    }
    val settingsForState = when {
        localSettings != null && remoteSettings != null ->
            localSettings.mergingRemote(remoteSettings, preservingFields = pendingFields)
        localSettings != null -> localSettings
        remoteSettings != null -> remoteSettings
        workspaceChanged -> RemoteRunSettings(
            resumeSessionId = lastResumeSessionId,
        )
        else -> settingsSnapshot(state.value)
    }.normalized().let { settings ->
        domainRepositories.conversations.settingsForDraft(settingsKey,
            settings.forConversationHistory(lastRun, lastResumeSessionId))
    }
    val contextChanged = workspaceChanged ||
        state.value.conversationSourceChanged(lastRun, settingsForState.resumeSessionId)
    if (contextChanged) {
        rememberPromptDraft()
        advanceContext(retainStoredFollowUps = true)
    }
    val pendingRunStorageMessage = if (selectedWorkspaceId != null && lastRun != null) {
        pendingStorageActions[
            RunMutationContext(mac, selectedWorkspaceId, lastRun, settingsForState)
        ]?.message
    } else {
        null
    }
    if (settingsKey != null && localSettings != null) {
        localSettingsByWorkspace[settingsKey] = settingsForState
    }
    val restoredQueue = if (contextChanged && selectedWorkspace != null) {
        restoredFollowUpQueue(
            macId = mac.deviceId,
            workspaceId = selectedWorkspace.workspaceId,
            runId = lastRun,
        )
    } else {
        state.value.followUpQueue
    }
    if (contextChanged) {
        queuedRunId = lastRun?.takeIf { restoredQueue.isNotEmpty() }
    }
    mutableState.update {
        it.copy(
            workspaces = response.workspaces,
            workspaceQueueCounts = workspaceQueueCounts(
                mac.deviceId,
                response.workspaces,
            ),
            workspacePromptStates = workspacePromptStates(
                mac.deviceId,
                response.workspaces,
            ),
            selectedWorkspaceId = selectedWorkspaceId,
            requestedWorkspaceOpenId = if (shouldOpenRequestedWorkspace) {
                selectedWorkspaceId
            } else {
                it.requestedWorkspaceOpenId
            },
            prompt = if (contextChanged) {
                selectedWorkspaceId?.let { workspaceId ->
                    restorePromptDraft(
                        macId = mac.deviceId,
                        workspaceId = workspaceId,
                        conversationId = runSnapshot?.conversationId,
                        resumeSessionId = settingsForState.resumeSessionId,
                        runId = lastRun,
                    )
                }.orEmpty()
            } else {
                it.prompt
            },
            workspaceRevision = response.revision,
            visibleRunId = lastRun,
            visibleConversationId = if (contextChanged) {
                runSnapshot?.conversationId?.takeIf(String::isNotBlank)
            } else it.visibleConversationId,
            visibleConversationTitle = if (contextChanged) {
                runSnapshot?.conversationTitle.orEmpty()
            } else it.visibleConversationTitle,
            runActionInProgress = lastRun?.let { runId ->
                "${mac.deviceId}\u001f${selectedWorkspaceId.orEmpty()}\u001f$runId" in
                    activeRunMutations
            } == true,
            runStatus = if (contextChanged) {
                runSnapshot?.status ?: if (lastRun == latestRun) {
                    selectedWorkspace?.runStatus.orEmpty()
                } else {
                    ""
                }
            } else it.runStatus,
            canAddAgents = if (contextChanged) runSnapshot?.canAddAgents ?: false
            else it.canAddAgents,
            canUpdateSynthesis = if (contextChanged) {
                runSnapshot?.canUpdateSynthesis ?: false
            } else it.canUpdateSynthesis,
            runPrompt = if (contextChanged) runSnapshot?.prompt.orEmpty()
            else it.runPrompt,
            agentSummaries = if (contextChanged) runSnapshot?.agents.orEmpty()
            else it.agentSummaries,
            selectedAgent = if (contextChanged) runSnapshot?.selectedAgent
            else it.selectedAgent,
            recommendedAgent = if (contextChanged) runSnapshot?.recommendedAgent
            else it.recommendedAgent,
            finalizedAgent = if (contextChanged) runSnapshot?.finalizedAgent
            else it.finalizedAgent,
            conversationHistory = if (contextChanged) {
                runSnapshot?.conversationHistory.orEmpty()
            } else it.conversationHistory,
            agentTranscripts = if (contextChanged) runSnapshot?.agentTranscripts.orEmpty()
            else it.agentTranscripts,
            agentDiffSummaries = if (contextChanged) {
                runSnapshot?.agentDiffSummaries.orEmpty()
            } else it.agentDiffSummaries,
            runSettings = settingsForState,
            primaryModelOptions = if (contextChanged) null else it.primaryModelOptions,
            synthesisModelOptions = if (contextChanged) null else it.synthesisModelOptions,
            resumeSessions = if (contextChanged) emptyList() else it.resumeSessions,
            settingsOptionsLoading = if (contextChanged) false else it.settingsOptionsLoading,
            settingsOptionsError = if (contextChanged) null else it.settingsOptionsError,
            resumeSessionsLoading = if (contextChanged) false else it.resumeSessionsLoading,
            resumeSessionsError = if (contextChanged) null else it.resumeSessionsError,
            followUpQueue = restoredQueue,
            followUpSecondsRemaining = if (contextChanged) null
            else it.followUpSecondsRemaining,
            pendingSubmission = pendingSubmission,
            storageConfirmation = pendingSubmission
                ?.takeIf {
                    it.status == PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION
                }
                ?.error
                ?.let { message ->
                    friendlyStoredError(
                        message.ifBlank { "This task requires storage confirmation" },
                        it.appLanguage,
                    )
                }
                ?: pendingRunStorageMessage,
        )
    }
    if (rememberedWorkspaceChanged || contextChanged) scheduleMobileStateSave()
    response.workspaces.forEach { workspace ->
        if (workspaceNeedsCompletionMonitoring(workspace)) {
            scheduleCompletionMonitor(
                macId = mac.deviceId,
                workspaceId = workspace.workspaceId,
                workspaceName = workspace.name,
                runId = workspace.lastRunId,
            )
        } else if (workspace.lastRunId.isNotBlank()) {
            scheduledCompletionRuns.remove(
                completionMonitorKey(
                    mac.deviceId,
                    workspace.workspaceId,
                    workspace.lastRunId,
                )
            )
        }
        val workspaceRunId = workspace.lastRunId.takeIf(String::isNotBlank)
        val latestDetailMissing = workspaceRunId != null && runStateStore[
            WorkspaceRunKey(mac.deviceId, workspace.workspaceId, workspaceRunId)
        ] == null
        if (workspaceRunId != null && workspaceNeedsDetailPolling(
                workspace,
                contextChanged = contextChanged && workspace.workspaceId == selectedWorkspaceId,
                detailMissing = workspace.workspaceId == selectedWorkspaceId &&
                    (latestDetailMissing ||
                        runSnapshot == null ||
                        runSnapshot.agents.isEmpty() ||
                        runSnapshot.selectedAgentDetailMissing()),
                forceRefresh = forceVisibleRunRefresh &&
                    workspace.workspaceId == selectedWorkspaceId &&
                    workspaceRunId == lastRun,
            )
        ) {
            beginPolling(
                mac = mac,
                key = WorkspaceRunKey(mac.deviceId, workspace.workspaceId, workspaceRunId),
            )
        }
    }
    if (lastRun != null && selectedWorkspaceId != null &&
        (requestedRun != null || (lastRun != latestRun &&
            (contextChanged || forceVisibleRunRefresh || runSnapshot == null)))
    ) {
        beginPolling(
            mac = mac,
            key = WorkspaceRunKey(mac.deviceId, selectedWorkspaceId, lastRun),
        )
    }
    if (settingsKey != null && localSettings != null &&
        settingsSaveJobs[settingsKey]?.isActive != true
    ) {
        scheduleWorkspaceSettingsSave(retry = true)
    }
    val sessionId = effectiveResumeSessionId(
        settingsForState,
        lastResumeSessionId,
    )
    if (lastRun == null && selectedWorkspace != null &&
        sessionId.isNotBlank() &&
        (contextChanged || state.value.conversationHistory.isEmpty())
    ) {
        loadResumeHistory(
            mac = mac,
            workspaceId = selectedWorkspace.workspaceId,
            sessionId = sessionId,
            expectedGeneration = contextGeneration,
        )
    }
    if (settingsKey != null &&
        settingsKey in settingsSaveFailures &&
        settingsSaveJobs[settingsKey]?.isActive != true
    ) {
        scheduleWorkspaceSettingsSave(retry = true)
    }
    if (selectedWorkspace != null &&
        cachedDaemonOwnsFollowUpQueue(mac.deviceId) == true
    ) {
        try {
            refreshDaemonFollowUpQueue(
                mac = mac,
                workspaceId = selectedWorkspace.workspaceId,
                fallbackRunId = lastRun,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            // The encrypted projection remains visible while pcrd reconnects.
        }
    }
    if (contextChanged && state.value.followUpQueue.isNotEmpty() && lastRun != null) {
        reconcileFollowUpQueue(
            resetCountdown = !hasFollowUpDeadline(lastRun),
        )
    }
    if (!resumeAcceptedPromptResults()) {
        resumePendingSubmissions(mac, response.workspaces)
    }
    ensureFollowUpCoordinator()
}

private fun MainViewModel.resumePendingSubmissions(
    mac: RelayDevice,
    workspaces: List<RemoteWorkspace>,
) {
    val availableWorkspaceIds = workspaces.mapTo(mutableSetOf()) { it.workspaceId }
    promptOutboxByContext.values
        .filter { submission ->
            submission.macId == mac.deviceId &&
                submission.workspaceId in availableWorkspaceIds &&
                submission.status in setOf(
                    PendingPromptSubmission.STATUS_PENDING,
                    PendingPromptSubmission.STATUS_RETRYING,
                )
        }
        .forEach(::submitPendingSubmission)
}

fun MainViewModel.selectWorkspace(workspaceId: String) {
    val snapshot = state.value
    if (workspaceId == snapshot.selectedWorkspaceId ||
        workspaceId == snapshot.workspaceSelectionId
    ) return
    val mac = selectedMac() ?: return
    if (mac.online == false) {
        rememberPromptDraft()
        restoreCachedWorkspaces(mac, preferredWorkspaceId = workspaceId)
        return
    }
    mutableState.update {
        it.copy(
            workspaceSelectionId = workspaceId,
            message = null,
            messageIsError = false,
        )
    }
    viewModelScope.launch {
        try {
            val cachedWorkspace = cachedWorkspacesByMac[mac.deviceId]
                .orEmpty()
                .ifEmpty {
                    state.value.workspaces.takeIf {
                        state.value.selectedMacId == mac.deviceId
                    }.orEmpty()
                }
                .firstOrNull { it.workspaceId == workspaceId }
            if (cachedWorkspace != null) {
                val settingsKey = workspaceSettingsKey(mac.deviceId, workspaceId)
                val cachedRunId = domainRepositories.conversations.displayedRunId(
                    settingsKey,
                    latestWorkspaceRunId(cachedWorkspace),
                )
                if (cachedRunId != null) {
                    runStateStore.hydrate(
                        WorkspaceRunKey(mac.deviceId, workspaceId, cachedRunId),
                    )
                }
                if (state.value.workspaceSelectionId != workspaceId ||
                    state.value.selectedMacId != mac.deviceId
                ) {
                    return@launch
                }
                restoreCachedWorkspaces(mac, preferredWorkspaceId = workspaceId)
            }
            val refreshedGeneration = contextGeneration
            loadWorkspaces(
                mac,
                refreshedGeneration,
                preferredWorkspaceId = workspaceId,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (state.value.workspaceSelectionId == workspaceId) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        } finally {
            mutableState.update { current ->
                if (current.workspaceSelectionId == workspaceId) {
                    current.copy(workspaceSelectionId = null)
                } else {
                    current
                }
            }
        }
    }
}

fun MainViewModel.requestWorkspaceRemoval(workspaceId: String) {
    val workspace = state.value.workspaces.firstOrNull {
        it.workspaceId == workspaceId
    } ?: return
    if (workspaceRemovalLooksActive(workspace)) {
        showError(state.value.appLanguage.text(
            "请先停止这个工作区正在运行的任务，再移除工作区。",
            "Stop the task running in this workspace before removing it.",
        ))
        return
    }
    mutableState.update { it.copy(workspaceRemoval = workspaceId) }
}

fun MainViewModel.confirmWorkspaceRemoval() = execute {
    val workspaceId = state.value.workspaceRemoval ?: return@execute
    val mac = selectedMac() ?: error("Choose a Mac first")
    val workspaceKey = workspaceSettingsKey(mac.deviceId, workspaceId)
    check(promptOutboxByContext[workspaceKey] == null) {
        "A message is still being submitted for this workspace"
    }
    check(acceptedPromptResultsByContext[workspaceKey] == null) {
        "A newly started task is still syncing for this workspace"
    }
    check(
        followUpQueuesByContext.none {
            (key, items) -> key.startsWith("$workspaceKey\u001f") && items.isNotEmpty()
        }
    ) {
        "Queued prompts must be finished or cleared before removing this workspace"
    }
    // Never accept or finalize an Agent as a side effect of removing a
    // workspace. The daemon rejects active and reviewable runs, while
    // completed runs with no usable candidate remain safe to forget.
    mutableState.update { it.copy(workspaceRemoval = null) }
    // Removing an explicit workspace id is safe without an optimistic
    // revision. Polling and another client may legitimately advance the
    // registry while the confirmation dialog is open.
    val workspace = state.value.workspaces.firstOrNull {
        it.workspaceId == workspaceId
    }
    val runId = workspace?.lastRunId.orEmpty()
    if (runId.isNotBlank()) {
        val run = try {
            repository.run(mac, runId)
        } catch (error: RemoteCommandException) {
            if (error.code in setOf("run_not_found", "unknown_run")) null else throw error
        }
        if (run != null) {
            val status = run["status"]?.jsonPrimitive?.contentOrNull.orEmpty()
            val workerActive = run["worker_active"]?.jsonPrimitive?.booleanOrNull == true
            check(!workerActive && isTerminalRunStatus(status)) {
                "The task must be stopped before removing this workspace"
            }
            check(status.lowercase() !in setOf("sync_ambiguous", "cleanup_failed")) {
                "Workspace sync and cleanup must be resolved before removing this workspace"
            }
            val finalized = run["finalized_agent"]?.jsonPrimitive?.intOrNull != null
            val hasReviewableResult = run["agents"]?.jsonArray.orEmpty().any { raw ->
                raw.jsonObject["can_accept"]?.jsonPrimitive?.booleanOrNull == true
            }
            if (!finalized && hasReviewableResult) {
                repository.discardRun(mac, runId, keepWorkspaces = false)
            }
        }
    }
    repository.removeWorkspace(mac, workspaceId)
    forgetMobileStateForWorkspace(mac.deviceId, workspaceId)
    // Keep the current run alive when a different workspace is removed.
    // loadWorkspaces performs the context reset only when the daemon's
    // selected workspace actually changed.
    loadWorkspaces(mac, contextGeneration)
}

fun MainViewModel.cancelWorkspaceRemoval() = mutableState.update { it.copy(workspaceRemoval = null) }
