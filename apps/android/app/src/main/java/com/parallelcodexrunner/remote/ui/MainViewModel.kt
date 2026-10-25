package com.parallelcodexrunner.remote.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.WorkInfo
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.BackgroundConnectionService
import com.parallelcodexrunner.remote.InternetAvailability
import com.parallelcodexrunner.remote.PCRRemoteApplication
import com.parallelcodexrunner.remote.data.AndroidUpdateInfo
import com.parallelcodexrunner.remote.data.AndroidUpdateInstaller
import com.parallelcodexrunner.remote.data.AcceptedPromptResult
import com.parallelcodexrunner.remote.data.DirectoryPage
import com.parallelcodexrunner.remote.data.FileSystemRoots
import com.parallelcodexrunner.remote.data.InstallPermissionRequired
import com.parallelcodexrunner.remote.data.MAX_AGENT_COUNT
import com.parallelcodexrunner.remote.data.PairingCode
import com.parallelcodexrunner.remote.data.PCRRepository
import com.parallelcodexrunner.remote.data.PersistedFollowUpDispatch
import com.parallelcodexrunner.remote.data.PromptSubmissionWorker
import com.parallelcodexrunner.remote.data.isTerminalAgentStatusValue
import com.parallelcodexrunner.remote.data.isTerminalRunStatusValue
import com.parallelcodexrunner.remote.data.isPermanentRemoteCommandRejection
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.ResumeSession
import com.parallelcodexrunner.remote.data.WorkspaceSettingFields
import com.parallelcodexrunner.remote.data.changedFields
import com.parallelcodexrunner.remote.data.forRetry
import com.parallelcodexrunner.remote.data.normalized
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.UUID

private const val FIRST_FRAME_WAIT_TIMEOUT_MILLIS = 5_000L
private const val NETWORK_LOSS_DEBOUNCE_MILLIS = 750L

class MainViewModel(application: Application) : AndroidViewModel(application) {
    internal val app = application as PCRRemoteApplication
    private val firstFrameDrawn = CompletableDeferred<Unit>()
    internal val repository: PCRRepository =
        app.repository
    internal val mobileStateStore = app.mobileStateStore
    private val restoredMobileState = mobileStateStore.load()
    internal val domainRepositories = ClientDomainRepositories(
        restored = restoredMobileState,
        diskCache = app.workspaceRunCache,
    )
    internal val updateInstaller = AndroidUpdateInstaller(application)
    internal val mutableState = MutableStateFlow(
        DisplayPreferences.load(application).let { display -> MainUiState(
            appLanguage = AppLanguage.load(application),
            colorMode = display.colorMode,
            textSize = display.textSize,
            accentColor = display.accentColor,
            workExpandedByDefault = display.workExpandedByDefault,
            backgroundConnectionEnabled = display.backgroundConnectionEnabled,
            internetAvailability = app.networkConnectivityMonitor.availability.value,
            signedIn = repository.signedIn,
            relayUrl = repository.relayApi.baseUrl.ifBlank { BuildConfig.DEFAULT_RELAY_URL },
            // Presence is live Relay state. Treat a persisted value as unknown
            // until the first refresh instead of briefly painting the device
            // as offline over an otherwise usable cached conversation.
            macs = cachedDevicesForColdStart(restoredMobileState.cachedDevices),
            selectedMacId = restoredMobileState.selectedMacId,
            workExpansionStates = restoredMobileState.workExpansionStates,
            viewedAgentCompletions = restoredMobileState.viewedAgentCompletions,
            unreadRunCompletions = restoredMobileState.unreadRunCompletions,
            message = app.secureStoreRecoveryNotice,
            messageIsError = app.secureStoreRecoveryNotice != null,
        ) }
    )
    val state: StateFlow<MainUiState> = mutableState.asStateFlow()

    fun setAppLanguage(language: AppLanguage) {
        if (mutableState.value.appLanguage == language) return
        AppLanguage.save(getApplication(), language)
        mutableState.update { it.copy(appLanguage = language) }
    }

    fun setColorMode(mode: AppColorMode) {
        if (mutableState.value.colorMode == mode) return
        DisplayPreferences.saveColorMode(getApplication(), mode)
        mutableState.update { it.copy(colorMode = mode) }
    }

    fun setTextSize(size: AppTextSize) {
        if (mutableState.value.textSize == size) return
        DisplayPreferences.saveTextSize(getApplication(), size)
        mutableState.update { it.copy(textSize = size) }
    }

    fun setAccentColor(color: AppAccentColor) {
        if (mutableState.value.accentColor == color) return
        DisplayPreferences.saveAccentColor(getApplication(), color)
        mutableState.update { it.copy(accentColor = color) }
    }

    fun setWorkExpandedByDefault(expanded: Boolean) {
        if (mutableState.value.workExpandedByDefault == expanded) return
        DisplayPreferences.saveWorkExpandedByDefault(getApplication(), expanded)
        mutableState.update { it.copy(workExpandedByDefault = expanded) }
    }

    fun setBackgroundConnectionEnabled(enabled: Boolean) {
        if (mutableState.value.backgroundConnectionEnabled == enabled) return
        DisplayPreferences.saveBackgroundConnectionEnabled(getApplication(), enabled)
        if (!BackgroundConnectionService.applySetting(getApplication(), enabled)) {
            DisplayPreferences.saveBackgroundConnectionEnabled(getApplication(), !enabled)
            showError(
                if (mutableState.value.appLanguage == AppLanguage.CHINESE) {
                    "系统未允许启动后台连接。"
                } else {
                    "Android did not allow the background connection to start."
                }
            )
            return
        }
        mutableState.update { it.copy(backgroundConnectionEnabled = enabled) }
    }
    private val feedbackCoordinator = FeedbackCoordinator(app, viewModelScope)
    val feedbackState: StateFlow<FeedbackUiState> = feedbackCoordinator.state
    private var pendingPairing: String? = repository.pendingPairing()
    private var pairingJob: Job? = null
    private var resumeRefreshJob: Job? = null
    internal var workspaceOverviewRefreshJob: Job? = null
    internal var machinePresenceRefreshJob: Job? = null
    private var settingsModelOptionsJob: Job? = null
    private var resumeSessionsJob: Job? = null
    internal var workspaceFileJob: Job? = null
    internal val runStateStore: WorkspaceRunStateStore
        get() = domainRepositories.turns.runState
    internal val conversationReadStore = ConversationReadStateStore { reads ->
        mutableState.update { it.copy(conversationReads = reads) }
    }
    internal val pendingStorageActions = PendingStorageActionStore()
    internal val runPollingByContext = mutableMapOf<WorkspaceRunKey, Job>()
    internal val runPollingPermits = Semaphore(MAX_CONCURRENT_RUN_POLL_REQUESTS)
    internal val runHistoryRecoveryMutex = Mutex()
    internal var followUpTask: Job? = null
    internal var followUpCoordinatorTask: Job? = null
    internal val followUpRemoteMutationMutex = Mutex()
    internal var followUpRemoteMutationRequestId = 0
    internal val daemonFollowUpSnapshots = mutableMapOf<String, com.parallelcodexrunner.remote.data.RemoteFollowUpQueue>()
    internal val backgroundFollowUpInFlight = mutableSetOf<String>()
    internal val backgroundFollowUpJobs = mutableMapOf<String, Job>()
    internal var uiForeground = true
    internal var followUpEditing = false
    internal var queuedRunId: String? = null
    internal var pendingFolderCreation: PendingFolderCreation? = null
    internal var pendingRunNavigation: PendingRunNavigation? = null
    internal var contextGeneration = 0
    internal var relayAuthorizationGeneration = 0
    private var busyOperations = 0
    internal var initialSyncRequestId = 0
    internal val activeRunMutations = mutableSetOf<String>()
    internal var macsRequestId = 0
    internal var nextMacDirectoryRefreshAtMillis = 0L
    internal var workspacesRequestId = 0
    internal val runRequestIds = mutableMapOf<WorkspaceRunKey, Int>()
    internal var nextConversationRequestId = 0
    internal var directoryRequestId = 0
    internal var resumeHistoryRequestId = 0
    internal var conversationListRequestId = 0
    internal var conversationOpenRequestId = 0
    private var settingsOptionsRequestId = 0
    private var resumeSessionsRequestId = 0
    internal var updateRequestId = 0
    internal var updateHistoryRequestId = 0
    internal var diffRequestId = 0
    internal var agentSelectionJob: Job? = null
    internal val diffSummaryPrefetchJobs = mutableMapOf<WorkspaceRunKey, Job>()
    internal val agentRequestIds = mutableMapOf<Pair<WorkspaceRunKey, Int>, Int>()
    internal val runHistoryRequestIds = mutableMapOf<WorkspaceRunKey, Int>()
    internal val loadedRunHistories = mutableSetOf<WorkspaceRunKey>()
    internal val settingsSaveJobs = mutableMapOf<String, Job>()
    internal val settingsSaveRequestIds = mutableMapOf<String, Int>()
    internal val localSettingsByWorkspace
        get() = domainRepositories.workspaces.settings
    internal val dirtyWorkspaceSettingFields
        get() = domainRepositories.workspaces.dirtySettingFields
    internal val promptDraftsByContext
        get() = domainRepositories.conversations.promptDrafts
    internal val followUpQueuesByContext
        get() = domainRepositories.conversations.followUpQueues
    internal val followUpDeadlinesByContext
        get() = domainRepositories.conversations.followUpDeadlines
    internal val promptOutboxByContext
        get() = domainRepositories.conversations.promptOutbox
    internal val acceptedPromptResultsByContext
        get() = domainRepositories.conversations.acceptedPromptResults
    internal val selectedAgentsByContext
        get() = domainRepositories.candidates.selectedByContext
    internal val workExpansionStatesByWorkspace
        get() = domainRepositories.candidates.workExpansionStates
    internal val viewedAgentCompletions
        get() = domainRepositories.candidates.viewedCompletions
    internal val unreadRunCompletions
        get() = domainRepositories.turns.unreadCompletions
    internal val selectedWorkspaceByMac
        get() = domainRepositories.workspaces.selectedByMachine
    internal val detachedConversationContexts
        get() = domainRepositories.conversations.detachedContexts
    internal val cachedWorkspacesByMac
        get() = domainRepositories.workspaces.cachedByMachine
    internal var cachedDevices: List<RelayDevice>
        get() = domainRepositories.machines.cachedDevices
        set(value) {
            domainRepositories.machines.cachedDevices = value
        }
    internal val submittingPromptIds = mutableSetOf<String>()
    internal val acceptedPromptAdoptionsInFlight = mutableSetOf<String>()
    internal val scheduledCompletionRuns = mutableSetOf<String>()
    internal var mobileStateSaveJob: Job? = null
    internal val mobileStateSaveRevision = java.util.concurrent.atomic.AtomicLong()
    internal val approvedLargeStorageWorkspaces = mutableSetOf<String>()
    internal val settingsSaveFailures = mutableSetOf<String>()
    internal val reportedSettingsSaveFailures = mutableSetOf<String>()
    internal var synthesisSettingsUpdateJob: Job? = null
    internal var defaultSettingsRequestId = 0
    internal var storageRequestId = 0
    internal val defaultSettingsSaveRequestIds = mutableMapOf<String, Int>()
    internal val defaultSettingsSaveJobs = mutableMapOf<String, Job>()
    internal val defaultSettingsAutoRetryAfter = mutableMapOf<String, Long>()
    internal val defaultSettingsByMac
        get() = domainRepositories.machines.defaultSettings
    internal val defaultPrimaryOptionsByMac
        get() = domainRepositories.machines.defaultPrimaryOptions
    internal val defaultSynthesisOptionsByMac
        get() = domainRepositories.machines.defaultSynthesisOptions
    internal val defaultSettingsErrorsByMac
        get() = domainRepositories.machines.defaultSettingsErrors
    internal val defaultOptionsErrorsByMac
        get() = domainRepositories.machines.defaultOptionsErrors
    internal val dirtyDefaultSettingFieldsByMac
        get() = domainRepositories.machines.dirtyDefaultSettingFields

    init {
        viewModelScope.launch {
            app.networkConnectivityMonitor.availability.collectLatest { availability ->
                if (availability == InternetAvailability.UNAVAILABLE) {
                    // Ignore brief network hand-offs so the toolbar does not flash offline.
                    delay(NETWORK_LOSS_DEBOUNCE_MILLIS)
                }
                mutableState.update { it.copy(internetAvailability = availability) }
            }
        }
        viewModelScope.launch {
            mobileStateStore.state.drop(1).collect(::synchronizePersistedBackgroundState)
        }
        if (pendingPairing != null) {
            connectPendingPairing()
        } else if (repository.signedIn) {
            selectedMac()?.let(::restoreCachedWorkspaces)
            refreshMacs(userInitiated = false)
        }
    }

    fun onFirstFrameDrawn() {
        firstFrameDrawn.complete(Unit)
    }

    internal suspend fun awaitFirstFrameDrawn() {
        withTimeoutOrNull(FIRST_FRAME_WAIT_TIMEOUT_MILLIS) {
            firstFrameDrawn.await()
        }
    }

    fun onAppResumed() {
        uiForeground = true
        synchronizePersistedBackgroundState(mobileStateStore.load())
        ensureFollowUpCoordinator()
        val snapshot = state.value
        if (snapshot.updatePermissionPending &&
            !snapshot.updateDownloading &&
            snapshot.availableUpdate != null &&
            updateInstaller.canInstallPackages()
        ) {
            mutableState.update { it.copy(updatePermissionPending = false) }
            installAndroidUpdate()
            return
        }
        if (!snapshot.signedIn || snapshot.busy ||
            snapshot.macSelectionId != null || snapshot.workspaceSelectionId != null ||
            pendingPairing != null
        ) {
            // onStop cancels live polling. Even when skipping the full refresh due to
            // an in-progress selection, restart polling for the current visible run
            // so it doesn't remain frozen until the next lifecycle event.
            restartVisibleRunPolling()
            return
        }
        val generation = contextGeneration
        resumeRefreshJob?.cancel()
        resumeRefreshJob = viewModelScope.launch {
            try {
                // onStop cancels live polling. Always fetch the visible run once
                // after returning to the foreground, even when the workspace
                // summary already says it is terminal. Otherwise the cached
                // per-Agent states remain frozen at the moment the app left.
                loadMacs(generation, forceVisibleRunRefresh = true)
            } catch (error: CancellationException) {
                throw error
            } catch (_: Throwable) {
                // Foreground refresh is best-effort; active polling keeps retrying run state.
            }
        }
    }

    fun onAppStopped() {
        uiForeground = false
        captureVisibleRunState()
        app.workspaceRunCache.flushScheduledWrites()
        workspaceOverviewRefreshJob?.cancel()
        workspaceOverviewRefreshJob = null
        machinePresenceRefreshJob?.cancel()
        machinePresenceRefreshJob = null
        followUpTask?.cancel()
        followUpTask = null
        followUpCoordinatorTask?.cancel()
        followUpCoordinatorTask = null
        backgroundFollowUpJobs.values.toList().forEach { it.cancel() }
        backgroundFollowUpJobs.clear()
        backgroundFollowUpInFlight.clear()
        runPollingByContext.values.toList().forEach { it.cancel() }
        runPollingByContext.clear()
        settingsModelOptionsJob?.cancel()
        settingsModelOptionsJob = null
        resumeSessionsJob?.cancel()
        resumeSessionsJob = null
        mutableState.update {
            it.copy(
                settingsOptionsLoading = false,
                resumeSessionsLoading = false,
            )
        }
        mobileStateSaveJob?.cancel()
        mobileStateSaveJob = null
        val snapshot = mobileStateSnapshot()
        runCatching {
            mobileStateStore.update { current ->
                snapshot.copy(
                    promptOutbox = current.promptOutbox,
                    acceptedPromptResults = current.acceptedPromptResults,
                    feedbackOutbox = current.feedbackOutbox,
                )
            }
        }
        followUpQueuesByContext.keys.mapNotNull(::parseFollowUpContext).forEach { context ->
            // A daemon-owned projection may also contain a just-submitted item
            // that has not been acknowledged yet. The worker safely reconciles
            // stable item IDs and lets pcrd retain delivery ownership.
            app.followUpQueueScheduler.enqueue(
                context.macId,
                context.workspaceId,
                context.runId,
                wake = true,
            )
        }
    }

    private fun restartVisibleRunPolling() {
        val visibleRunId = state.value.visibleRunId ?: return
        val (mac, key) = currentRunKey(visibleRunId) ?: return
        if (runPollingByContext[key]?.isActive == true) return
        beginPolling(mac, key)
    }

    fun openRunFromNotification(macId: String, workspaceId: String, runId: String) {
        if (macId.isBlank() || workspaceId.isBlank() || runId.isBlank()) return
        val navigation = PendingRunNavigation(macId, workspaceId, runId)
        domainRepositories.conversations.attach(workspaceSettingsKey(macId, workspaceId))
        pendingRunNavigation = navigation
        if (!repository.signedIn) {
            scheduleMobileStateSave()
            return
        }
        val generation = stagePendingRunNavigation(navigation)
        scheduleMobileStateSave()
        viewModelScope.launch {
            try {
                loadMacs(generation)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        }
    }

    fun consumeWorkspaceOpenRequest(workspaceId: String) {
        mutableState.update { current ->
            if (current.requestedWorkspaceOpenId == workspaceId) {
                current.copy(requestedWorkspaceOpenId = null)
            } else {
                current
            }
        }
    }

    fun setResumeSessionId(value: String) {
        if (state.value.runActionInProgress) {
            showError(state.value.appLanguage.text(
                "当前任务操作完成后再切换对话。",
                "Wait for the current task action to finish before switching conversations.",
            ))
            return
        }
        if (state.value.pendingSubmission != null) {
            showError(state.value.appLanguage.text(
                "请先等待当前消息提交完成，或处理发送失败的消息。",
                "Wait for the current message to finish sending, or resolve the failed message first.",
            ))
            return
        }
        if (state.value.followUpQueue.isNotEmpty()) {
            showError(state.value.appLanguage.text(
                "请先完成或清空排队中的提问，再切换对话。",
                "Finish or clear the queued prompts before switching conversations.",
            ))
            return
        }
        if (state.value.visibleRunId != null &&
            !isTerminalRunStatus(state.value.runStatus)
        ) {
            showError(state.value.appLanguage.text(
                "请等待当前任务完成后再切换对话。",
                "Wait for the current task to finish before switching conversations.",
            ))
            return
        }
        if (state.value.runStatus.lowercase() in setOf("sync_ambiguous", "cleanup_failed")) {
            showError(state.value.appLanguage.text(
                "请先确认工作区同步状态并完成清理，再切换对话。",
                "Confirm workspace sync and finish cleanup before switching conversations.",
            ))
            return
        }
        val normalizedValue = value.trim().ifBlank { "NO" }
        val snapshot = state.value
        val runId = snapshot.visibleRunId
        if (runId != null && snapshot.finalizedAgent == null) {
            val reviewable = snapshot.agentSummaries.any(AgentSummary::canAccept)
            val selected = snapshot.agentSummaries.firstOrNull {
                it.number == snapshot.selectedAgent && it.canAccept
            }
            if (reviewable && selected == null) {
                showError(state.value.appLanguage.text(
                    "请先切换到一个可采用的 Agent。",
                    "Switch to an Agent that can be accepted first.",
                ))
                return
            }
            if (selected != null) {
                val context = currentRunMutationContext() ?: return
                executeRunMutation(context) {
                    val result = repository.acceptAgent(
                        it.mac,
                        it.runId,
                        selected.number,
                        it.settings,
                    )
                    if (isCurrentRunMutation(it)) {
                        result["workspace_revision"]?.jsonPrimitive?.intOrNull?.let { revision ->
                            mutableState.update { current ->
                                current.copy(workspaceRevision = revision)
                            }
                        }
                        try {
                            loadRun(it.runId)
                        } catch (error: CancellationException) {
                            throw error
                        } catch (error: Throwable) {
                            showError(state.value.appLanguage.text(
                                "Agent 已采用，但任务状态暂时无法刷新：",
                                "The Agent was accepted, but the task status could not be refreshed: ",
                            ) + friendlyError(error, state.value.appLanguage))
                        }
                    }
                    applyResumeSession(it, normalizedValue)
                }
                return
            }
        }
        applyResumeSession(normalizedValue)
    }

    private fun applyResumeSession(value: String) {
        captureVisibleRunState()
        val snapshot = state.value
        val conversationContext = snapshot.selectedMacId?.let { macId ->
            snapshot.selectedWorkspaceId?.let { workspaceId ->
                workspaceSettingsKey(macId, workspaceId)
            }
        }
        rememberPromptDraft()
        conversationContext?.let { domainRepositories.conversations.detach(it, value) }
        updateRunSettings {
            it.copy(resumeSessionId = value)
        }
        resumeHistoryRequestId += 1
        mutableState.update { current ->
            current.withoutVisibleConversation().copy(
                prompt = current.selectedMacId?.let { macId ->
                    current.selectedWorkspaceId?.let { workspaceId ->
                        restorePromptDraft(
                            macId = macId,
                            workspaceId = workspaceId,
                            conversationId = null,
                            resumeSessionId = value,
                        )
                    }
                }.orEmpty(),
            )
        }
        scheduleMobileStateSave()
        val mac = selectedMac()
        val workspaceId = state.value.selectedWorkspaceId
        if (mac != null && workspaceId != null && !isNoResumeSession(value)) {
            loadResumeHistory(
                mac = mac,
                workspaceId = workspaceId,
                sessionId = value,
                expectedGeneration = contextGeneration,
            )
        }
    }

    private fun applyResumeSession(context: RunMutationContext, value: String) {
        if (isCurrentRunMutation(context)) {
            applyResumeSession(value)
            return
        }
        val key = workspaceSettingsKey(context.mac.deviceId, context.workspaceId)
        domainRepositories.conversations.detach(key, value)
        val settings = (localSettingsByWorkspace[key] ?: context.settings)
            .copy(configured = true, resumeSessionId = value)
            .normalized()
        scheduleWorkspaceSettingsSave(
            mac = context.mac,
            workspaceId = context.workspaceId,
            settings = settings,
            changedFields = setOf(WorkspaceSettingFields.RESUME_SESSION_ID),
        )
        scheduleMobileStateSave()
    }

    internal fun applyPromotedSession(value: String) {
        if (isNoResumeSession(value) || settingsSnapshot(state.value).resumeSessionId == value) {
            return
        }
        updateRunSettings { it.copy(resumeSessionId = value) }
    }

    internal fun updateRunSettings(
        updateActiveSynthesis: Boolean = false,
        transform: (RemoteRunSettings) -> RemoteRunSettings,
    ) {
        var previousSettings = settingsSnapshot(state.value)
        var pendingFields = emptySet<String>()
        mutableState.update { current ->
            previousSettings = settingsSnapshot(current)
            val settings = transform(previousSettings)
                .copy(configured = true)
                .normalized()
            pendingFields = settings.changedFields(previousSettings)
            current.copy(runSettings = settings)
        }
        val snapshot = state.value
        val macId = snapshot.selectedMacId
        val workspaceId = snapshot.selectedWorkspaceId
        if (macId != null && workspaceId != null && pendingFields.isNotEmpty()) {
            val key = workspaceSettingsKey(macId, workspaceId)
            localSettingsByWorkspace[key] = settingsSnapshot(snapshot)
            dirtyWorkspaceSettingFields
                .getOrPut(key, ::mutableSetOf)
                .addAll(pendingFields)
            settingsSaveFailures.remove(key)
            reportedSettingsSaveFailures.remove(key)
            scheduleWorkspaceSettingsSave()
        }
        if (updateActiveSynthesis) scheduleActiveSynthesisSettingsUpdate()
    }

    internal fun settingsSnapshot(current: MainUiState): RemoteRunSettings =
        current.runSettings.normalized()

    fun refreshRunSettingsOptions(loadResumeSessions: Boolean = true) {
        val mac = selectedMac() ?: return
        val workspaceId = state.value.selectedWorkspaceId ?: return
        val generation = contextGeneration
        val requestId = ++settingsOptionsRequestId
        val settings = settingsSnapshot(state.value)
        settingsModelOptionsJob?.cancel()
        mutableState.update {
            it.copy(settingsOptionsLoading = true, settingsOptionsError = null)
        }
        settingsModelOptionsJob = viewModelScope.launch {
            val (primaryResult, synthesisResult) = coroutineScope {
                val primary = async {
                    remoteResult {
                        repository.modelOptions(mac, settings.model, settings.effort)
                    }
                }
                val synthesis = async {
                    remoteResult {
                        repository.modelOptions(
                            mac,
                            settings.synthesisModel,
                            settings.synthesisEffort,
                        )
                    }
                }
                primary.await() to synthesis.await()
            }
            if (requestId != settingsOptionsRequestId ||
                generation != contextGeneration ||
                state.value.selectedMacId != mac.deviceId ||
                state.value.selectedWorkspaceId != workspaceId
            ) return@launch
            val primary = primaryResult.getOrNull()
            val synthesis = synthesisResult.getOrNull()
            val optionsError = buildList {
                primaryResult.exceptionOrNull()?.let {
                    add(state.value.appLanguage.text("主阶段：", "Agents: ") +
                        friendlyError(it, state.value.appLanguage))
                }
                synthesisResult.exceptionOrNull()?.let {
                    add(state.value.appLanguage.text("综合阶段：", "Synthesis: ") +
                        friendlyError(it, state.value.appLanguage))
                }
            }.takeIf { it.isNotEmpty() }?.joinToString(
                prefix = state.value.appLanguage.text("部分选项未加载（", "Some options did not load ("),
                postfix = state.value.appLanguage.text("）", ")"),
                separator = state.value.appLanguage.text("；", "; "),
            )
            val currentSettings = settingsSnapshot(state.value)
            val clearPrimaryEffort = primary != null &&
                currentSettings.effort.isNotBlank() &&
                primary.efforts.none { it.value == currentSettings.effort }
            val clearSynthesisEffort = synthesis != null &&
                currentSettings.synthesisEffort.isNotBlank() &&
                synthesis.efforts.none { it.value == currentSettings.synthesisEffort }
            mutableState.update { current ->
                current.copy(
                    primaryModelOptions = primary ?: current.primaryModelOptions,
                    synthesisModelOptions = synthesis ?: current.synthesisModelOptions,
                    settingsOptionsLoading = false,
                    settingsOptionsError = optionsError,
                )
            }
            if (clearPrimaryEffort || clearSynthesisEffort) {
                updateRunSettings(updateActiveSynthesis = clearSynthesisEffort) { current ->
                    current.copy(
                        effort = if (clearPrimaryEffort) "" else current.effort,
                        synthesisEffort = if (clearSynthesisEffort) {
                            ""
                        } else {
                            current.synthesisEffort
                        },
                    )
                }
            }
        }
        if (loadResumeSessions) {
            startResumeSessionsRefresh(mac, workspaceId, generation, settings)
        }
    }

    fun refreshResumeSessions() {
        val mac = selectedMac() ?: return
        val workspaceId = state.value.selectedWorkspaceId ?: return
        startResumeSessionsRefresh(
            mac,
            workspaceId,
            contextGeneration,
            settingsSnapshot(state.value),
        )
    }

    private fun startResumeSessionsRefresh(
        mac: RelayDevice,
        workspaceId: String,
        generation: Int,
        settings: RemoteRunSettings,
    ) {
        val requestId = ++resumeSessionsRequestId
        resumeSessionsJob?.cancel()
        mutableState.update {
            it.copy(resumeSessionsLoading = true, resumeSessionsError = null)
        }
        resumeSessionsJob = viewModelScope.launch {
            val result = remoteResult {
                repository.resumeSessions(
                    mac,
                    workspaceId,
                    includeNonInteractive = settings.resumeIncludeNonInteractive,
                ).sessions
            }
            if (requestId != resumeSessionsRequestId ||
                generation != contextGeneration ||
                state.value.selectedMacId != mac.deviceId ||
                state.value.selectedWorkspaceId != workspaceId
            ) return@launch
            mutableState.update { current ->
                current.copy(
                    resumeSessions = result.getOrNull() ?: current.resumeSessions,
                    resumeSessionsLoading = false,
                    resumeSessionsError = result.exceptionOrNull()?.let(::friendlyError),
                )
            }
        }
    }

    fun setBrowserPath(value: String) = mutableState.update { it.copy(browserPath = value) }

    fun handlePairingCode(value: String) {
        runCatching {
            val normalized = PairingCode.normalize(value)
            PairingCode.parse(normalized) to normalized
        }
            .onSuccess { link ->
                pendingPairing = link.second
                repository.rememberPendingPairing(link.second)
                mutableState.update { it.copy(relayUrl = link.first.relayUrl) }
                connectPendingPairing()
            }
            .onFailure { showError(friendlyError(it, state.value.appLanguage)) }
    }

    private fun connectPendingPairing() {
        pairingJob?.cancel()
        relayAuthorizationGeneration += 1
        val initialSyncId = beginInitialSync()
        pairingJob = execute {
            try {
                val pairing = pendingPairing ?: return@execute
                val link = PairingCode.parse(pairing)
                val pairingResult = repository.pair(pairing)
                clearTransientRelayConnectionMessage()
                updateInitialSync(initialSyncId, InitialSyncStage.DEVICES)
                if (!pairingResult.retainedAccount) {
                    clearPersistedMobileState()
                    resetDefaultSettingsContext(cancelSaves = true)
                    mutableState.update {
                        it.copy(
                            signedIn = true,
                            relayUrl = link.relayUrl,
                            macs = emptyList(),
                            workspaces = emptyList(),
                            selectedMacId = null,
                            macSelectionId = null,
                            maximumAgentCount = MAX_AGENT_COUNT,
                            machinePlatform = "",
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
                        )
                    }
                } else {
                    mutableState.update { it.copy(signedIn = true, relayUrl = link.relayUrl) }
                }
                repository.clearPendingPairing()
                pendingPairing = null
                val generation = if (pairingResult.retainedAccount) {
                    contextGeneration
                } else {
                    advanceContext()
                }
                loadMacs(generation, initialSyncId = initialSyncId)
            } finally {
                finishInitialSync(initialSyncId)
            }
        }
    }

    fun openFeedback(macId: String?) {
        val mac = state.value.macs.firstOrNull { it.deviceId == macId }
        feedbackCoordinator.open(mac)
    }

    fun dismissFeedback() = feedbackCoordinator.dismiss()

    fun refreshFeedback() = feedbackCoordinator.refresh()

    fun loadMoreFeedback() = feedbackCoordinator.loadMore()

    fun submitFeedback(
        kind: String,
        description: String,
        images: List<Uri>,
        includedDiagnosticKeys: Set<String>,
    ) {
        feedbackCoordinator.submit(
            kind = kind,
            description = description,
            imageUris = images,
            context = currentFeedbackDiagnosticContext(),
            includedDiagnosticKeys = includedDiagnosticKeys,
        )
    }

    fun feedbackDiagnosticPreview(): Map<String, String> =
        feedbackCoordinator.diagnosticPreview(currentFeedbackDiagnosticContext())

    private fun currentFeedbackDiagnosticContext(): FeedbackDiagnosticContext {
        val snapshot = state.value
        val workspace = snapshot.workspaces.firstOrNull {
            it.workspaceId == snapshot.selectedWorkspaceId
        }
        return FeedbackDiagnosticContext(
            workspaceId = workspace?.workspaceId.orEmpty(),
            workspaceName = workspace?.name.orEmpty(),
            runId = snapshot.visibleRunId.orEmpty(),
            agent = snapshot.selectedAgent,
        )
    }

    fun retryFeedback(id: String) = feedbackCoordinator.retry(id)

    fun removePendingFeedback(id: String) = feedbackCoordinator.removePending(id)

    fun withdrawFeedback(id: String) = feedbackCoordinator.withdraw(id)

    fun addFeedbackNote(id: String, text: String, onSuccess: () -> Unit) =
        feedbackCoordinator.addNote(id, text, onSuccess)


    internal fun execute(block: suspend () -> Unit): Job {
        // Claim the UI operation before scheduling it. Otherwise two quick taps
        // can both observe busy=false and submit the same remote mutation.
        beginOperation()
        val authorizationGeneration = relayAuthorizationGeneration
        return viewModelScope.launch {
            try {
                block()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (authorizationGeneration == relayAuthorizationGeneration) {
                    showError(friendlyError(error, state.value.appLanguage))
                }
            } finally {
                endOperation()
            }
        }
    }

    internal fun currentRunMutationContext(): RunMutationContext? {
        val snapshot = state.value
        val macId = snapshot.selectedMacId ?: return null
        val workspaceId = snapshot.selectedWorkspaceId ?: return null
        val runId = snapshot.visibleRunId ?: return null
        val mac = snapshot.macs.firstOrNull { it.deviceId == macId } ?: return null
        return RunMutationContext(
            mac = mac,
            workspaceId = workspaceId,
            runId = runId,
            settings = settingsSnapshot(snapshot),
        )
    }

    internal fun pendingStorageActionForCurrentRun(): PendingStorageAction? =
        currentRunMutationContext()?.let(pendingStorageActions::get)

    internal fun isCurrentRunMutation(context: RunMutationContext): Boolean {
        val snapshot = state.value
        return snapshot.selectedMacId == context.mac.deviceId &&
            snapshot.selectedWorkspaceId == context.workspaceId &&
            snapshot.visibleRunId == context.runId
    }

    internal fun refreshVisibleRunMutationState() {
        val snapshot = state.value
        val key = listOfNotNull(
            snapshot.selectedMacId,
            snapshot.selectedWorkspaceId,
            snapshot.visibleRunId,
        ).takeIf { it.size == 3 }?.joinToString("\u001f")
        mutableState.update {
            it.copy(runActionInProgress = key != null && key in activeRunMutations)
        }
    }

    internal fun executeRunMutation(
        context: RunMutationContext,
        block: suspend (RunMutationContext) -> Unit,
    ): Job? {
        if (isCurrentRunMutation(context) && state.value.pendingSubmission != null) {
            return null
        }
        if (!activeRunMutations.add(context.key)) return null
        refreshVisibleRunMutationState()
        return viewModelScope.launch {
            try {
                block(context)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (isCurrentRunMutation(context)) {
                    showError(friendlyError(error, state.value.appLanguage))
                }
            } finally {
                activeRunMutations.remove(context.key)
                refreshVisibleRunMutationState()
            }
        }
    }

    internal fun advanceContext(
        preserveFollowUps: Boolean = false,
        retainStoredFollowUps: Boolean = false,
    ): Int {
        closeWorkspaceFilesForContextChange()
        captureVisibleRunState()
        if (!preserveFollowUps && !retainStoredFollowUps &&
            state.value.followUpQueue.isNotEmpty()
        ) {
            persistFollowUpQueue(
                queuedRunId ?: state.value.visibleRunId,
                emptyList(),
            )
        }
        contextGeneration += 1
        followUpTask?.cancel()
        followUpTask = null
        followUpEditing = false
        if (!preserveFollowUps) {
            queuedRunId = null
        }
        synthesisSettingsUpdateJob?.cancel()
        synthesisSettingsUpdateJob = null
        agentSelectionJob?.cancel()
        agentSelectionJob = null
        diffRequestId += 1
        directoryRequestId += 1
        resumeHistoryRequestId += 1
        conversationListRequestId += 1
        conversationOpenRequestId += 1
        settingsOptionsRequestId += 1
        resumeSessionsRequestId += 1
        settingsModelOptionsJob?.cancel()
        settingsModelOptionsJob = null
        resumeSessionsJob?.cancel()
        resumeSessionsJob = null
        mutableState.update {
            it.copy(
                followUpQueue = if (preserveFollowUps) {
                    it.followUpQueue
                } else {
                    emptyList()
                },
                followUpSecondsRemaining = null,
                storageConfirmation = if (preserveFollowUps) {
                    it.storageConfirmation
                } else {
                    null
                },
                diffText = null,
                diffLoading = false,
                recoveryConfirmation = false,
                pendingSubmission = null,
                pendingAgentSelection = null,
                agentDiffSummaries = emptyMap(),
                conversationListLoading = false,
                conversationListError = null,
                conversationOpeningId = null,
                settingsOptionsLoading = false,
                resumeSessionsLoading = false,
                resumeSessionsError = null,
            )
        }
        return contextGeneration
    }

    internal fun requireNoQueuedFollowUps(action: String) {
        check(state.value.followUpQueue.isEmpty()) {
            "请先完成或清空排队中的提问，再$action。"
        }
    }

    internal suspend fun resolveCurrentRunBeforeContextChange(action: String) {
        val snapshot = state.value
        check(snapshot.pendingSubmission == null) {
            "请先等待当前消息提交完成，或处理发送失败的消息。"
        }
        val runId = snapshot.visibleRunId ?: return
        check(snapshot.runStatus.lowercase() !in setOf("sync_ambiguous", "cleanup_failed")) {
            "请先确认工作区同步状态并完成清理，再$action。"
        }
        if (snapshot.finalizedAgent != null ||
            snapshot.runStatus.lowercase() in setOf("finalized", "discarded")
        ) return
        check(isTerminalRunStatus(snapshot.runStatus)) {
            "请先停止当前任务，再$action。"
        }
        val selected = snapshot.agentSummaries.firstOrNull {
            it.number == snapshot.selectedAgent && it.canAccept
        }
        checkNotNull(selected) {
            "请先切换到一个可采用的 Agent，或放弃当前任务，再$action。"
        }
        val mac = selectedMac() ?: error("Choose a Mac first")
        val result = repository.acceptAgent(
            mac = mac,
            runId = runId,
            agent = selected.number,
            settings = settingsSnapshot(state.value),
        )
        result["workspace_revision"]?.jsonPrimitive?.intOrNull?.let { revision ->
            mutableState.update { it.copy(workspaceRevision = revision) }
        }
        promotedSessionId(result)?.let(::applyPromotedSession)
        loadRun(runId)
        check(state.value.finalizedAgent != null) {
            "未能采用当前 Agent，已取消$action。"
        }
    }

    override fun onCleared() {
        captureVisibleRunState()
        app.workspaceRunCache.flushScheduledWrites()
        mobileStateSaveJob?.cancel()
        workspaceFileJob?.cancel()
        followUpCoordinatorTask?.cancel()
        runCatching { persistMobileStateSnapshot(mobileStateSnapshot()) }
        super.onCleared()
    }

    private fun beginOperation() {
        busyOperations += 1
        mutableState.update { it.copy(busy = true, message = null, messageIsError = false) }
    }

    private fun endOperation() {
        busyOperations = (busyOperations - 1).coerceAtLeast(0)
        mutableState.update { it.copy(busy = busyOperations > 0) }
    }

    internal fun showError(message: String) {
        mutableState.update { it.copy(message = message, messageIsError = true) }
    }

    internal fun clearTransientRelayConnectionMessage() {
        mutableState.update { current ->
            if (current.message in setOf(
                    TRANSIENT_RELAY_CONNECTION_MESSAGE,
                    TRANSIENT_RELAY_CONNECTION_MESSAGE_ENGLISH,
                ) + recoveredRelayMessages(current.appLanguage)
            ) {
                current.copy(message = null, messageIsError = false)
            } else {
                current
            }
        }
    }

}
