package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.InternetAvailability
import com.parallelcodexrunner.remote.data.AndroidUpdateInfo
import com.parallelcodexrunner.remote.data.CodexModelOptions
import com.parallelcodexrunner.remote.data.DirectoryPage
import com.parallelcodexrunner.remote.data.FileSystemRoots
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.MAX_AGENT_COUNT
import com.parallelcodexrunner.remote.data.PersistedFollowUpDispatch
import com.parallelcodexrunner.remote.data.PersistedQueuedFollowUp
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.ResumeSession
import com.parallelcodexrunner.remote.data.StorageSummary
import com.parallelcodexrunner.remote.data.isTerminalAgentStatusValue
import com.parallelcodexrunner.remote.data.isTerminalRunStatusValue
import com.parallelcodexrunner.remote.data.normalized
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import java.io.File
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.Serializable

@Serializable
data class AgentSummary(
    val number: Int,
    val status: String,
    val reasoningTokens: Long?,
    val seconds: Double?,
    val recommended: Boolean,
    val rejected: Boolean,
    val workspaceAvailable: Boolean,
    val canAccept: Boolean,
    val canContinue: Boolean,
    val canRetry: Boolean = false,
    val reasoningTokenCounts: Map<Long, Int> = emptyMap(),
    val role: String = "candidate",
)

@Serializable
data class AgentFileChange(
    val status: String,
    val path: String,
)

@Serializable
data class AgentDiffSummary(
    val changes: List<AgentFileChange> = emptyList(),
    val totalFiles: Int = changes.size,
    val truncated: Boolean = false,
    val unavailable: Boolean = false,
)

data class QueuedFollowUp(
    val id: String,
    val prompt: String,
    val settings: RemoteRunSettings? = null,
    val attempt: Int = 0,
    val dispatching: Boolean = false,
    val dispatch: PersistedFollowUpDispatch? = null,
    val failureCount: Int = 0,
    val retryAtMillis: Long = 0,
    val lastError: String = "",
    val paused: Boolean = false,
    val automaticRecoveryAttempted: Boolean = false,
    val daemonAcknowledged: Boolean? = null,
) {
    val commandId: String
        get() = "follow-up-$id-attempt-${attempt.coerceAtLeast(0)}"
}

enum class InitialSyncStage(
    val completedSteps: Int,
    val currentStep: Int,
) {
    CONNECTING(completedSteps = 0, currentStep = 1),
    DEVICES(completedSteps = 1, currentStep = 2),
    COMPUTER(completedSteps = 2, currentStep = 3),
    WORKSPACES(completedSteps = 3, currentStep = 4),
    COMPLETE(completedSteps = 4, currentStep = 4),
}

data class InitialSyncProgress(
    val stage: InitialSyncStage,
) {
    val fraction: Float
        get() = stage.completedSteps.toFloat() / TOTAL_STEPS

    companion object {
        const val TOTAL_STEPS = 4
    }
}

data class MainUiState(
    val appLanguage: AppLanguage = AppLanguage.CHINESE,
    val colorMode: AppColorMode = AppColorMode.SYSTEM,
    val textSize: AppTextSize = AppTextSize.STANDARD,
    val accentColor: AppAccentColor = AppAccentColor.BLUE,
    val workExpandedByDefault: Boolean = false,
    val backgroundConnectionEnabled: Boolean = false,
    val internetAvailability: InternetAvailability = InternetAvailability.UNKNOWN,
    val initialSyncProgress: InitialSyncProgress? = null,
    val signedIn: Boolean = false,
    val relayUrl: String = BuildConfig.DEFAULT_RELAY_URL,
    val macs: List<RelayDevice> = emptyList(),
    val selectedMacId: String? = null,
    val macSelectionId: String? = null,
    val macRefreshInProgress: Boolean = false,
    val maximumAgentCount: Int = MAX_AGENT_COUNT,
    val machinePlatform: String = "",
    val workspaces: List<RemoteWorkspace> = emptyList(),
    val workspaceQueueCounts: Map<String, Int> = emptyMap(),
    val workspacePromptStates: Map<String, String> = emptyMap(),
    val selectedWorkspaceId: String? = null,
    val requestedWorkspaceOpenId: String? = null,
    val workspaceSelectionId: String? = null,
    val workspaceRevision: Int = 0,
    val workspaceConversations: List<ConversationRecord> = emptyList(),
    val conversationListWorkspaceId: String? = null,
    val conversationListLoading: Boolean = false,
    val conversationListError: String? = null,
    val conversationOpeningId: String? = null,
    val browserOpen: Boolean = false,
    val browserRoots: FileSystemRoots? = null,
    val directory: DirectoryPage? = null,
    val directoryLoading: Boolean = false,
    val browserPath: String = "",
    val showHidden: Boolean = false,
    val folderCreationParent: String? = null,
    val folderCreationName: String = "",
    val folderCreating: Boolean = false,
    val folderCreationError: String? = null,
    val pendingSubmission: PendingPromptSubmission? = null,
    val prompt: String = "",
    val runSettings: RemoteRunSettings = RemoteRunSettings(),
    val primaryModelOptions: CodexModelOptions? = null,
    val synthesisModelOptions: CodexModelOptions? = null,
    val resumeSessions: List<ResumeSession> = emptyList(),
    val settingsOptionsLoading: Boolean = false,
    val settingsOptionsError: String? = null,
    val resumeSessionsLoading: Boolean = false,
    val resumeSessionsError: String? = null,
    val defaultRunSettings: RemoteRunSettings = RemoteRunSettings(),
    val defaultSettingsMacId: String? = null,
    val defaultPrimaryModelOptions: CodexModelOptions? = null,
    val defaultSynthesisModelOptions: CodexModelOptions? = null,
    val defaultSettingsLoading: Boolean = false,
    val defaultSettingsSaving: Boolean = false,
    val defaultSettingsError: String? = null,
    val defaultSettingsOptionsError: String? = null,
    val storageMacId: String? = null,
    val storageSummary: StorageSummary? = null,
    val storageLoading: Boolean = false,
    val storageError: String? = null,
    val storageCleaningRunId: String? = null,
    val visibleRunId: String? = null,
    val visibleConversationId: String? = null,
    val visibleConversationTitle: String = "",
    val runStatus: String = "",
    val runSynthesisAgents: Int = 0,
    val canAddAgents: Boolean = false,
    val canUpdateSynthesis: Boolean = false,
    val runPrompt: String = "",
    val agentSummaries: List<AgentSummary> = emptyList(),
    val selectedAgent: Int? = null,
    val pendingAgentSelection: Int? = null,
    val workExpansionStates: Map<String, Boolean> = emptyMap(),
    val viewedAgentCompletions: Set<String> = emptySet(),
    val unreadRunCompletions: Set<String> = emptySet(),
    val recommendedAgent: Int? = null,
    val finalizedAgent: Int? = null,
    val liveUpdateInterruptedContext: String? = null,
    val conversationReads: Map<String, ConversationReadState> = emptyMap(),
    val conversationHistory: List<MobileTranscriptBlock> = emptyList(),
    val agentTranscripts: Map<Int, MobileAgentTranscript> = emptyMap(),
    val agentDiffSummaries: Map<Int, AgentDiffSummary> = emptyMap(),
    val storageConfirmation: String? = null,
    val workspaceRemoval: String? = null,
    val macRevocation: String? = null,
    val availableUpdate: AndroidUpdateInfo? = null,
    val updateChecking: Boolean = false,
    val updateDownloading: Boolean = false,
    val updateProgress: Int? = null,
    val updatePermissionPending: Boolean = false,
    val updateHistoryOpen: Boolean = false,
    val updateHistory: List<AndroidUpdateInfo> = emptyList(),
    val updateHistoryLoading: Boolean = false,
    val updateHistoryError: String? = null,
    val updateHistoryNextCursor: Int = 0,
    val updateHistoryHasMore: Boolean = false,
    val followUpQueue: List<QueuedFollowUp> = emptyList(),
    val followUpSecondsRemaining: Int? = null,
    val diffText: String? = null,
    val diffTitle: String = "",
    val diffLoading: Boolean = false,
    val workspaceFileLoading: Boolean = false,
    val workspaceFileProgress: Int? = null,
    val workspaceDocument: WorkspaceDocument? = null,
    val workspaceDocumentHistory: List<WorkspaceDocument> = emptyList(),
    val recoveryConfirmation: Boolean = false,
    val runActionInProgress: Boolean = false,
    val runDestructiveAction: RunDestructiveAction? = null,
    val busy: Boolean = false,
    val message: String? = null,
    val messageIsError: Boolean = false,
)

internal fun cachedDevicesForColdStart(devices: List<RelayDevice>): List<RelayDevice> =
    devices.map { it.copy(online = null) }

sealed class RunDestructiveAction {
    data class Accept(val agent: Int) : RunDestructiveAction()
    data class Kill(val agent: Int) : RunDestructiveAction()
}

internal fun MainUiState.canMutateVisibleRun(): Boolean =
    visibleRunId != null && pendingSubmission == null && !runActionInProgress

internal fun MainUiState.isVisibleConversationContentLoading(): Boolean =
    visibleRunId != null &&
        !hasVisibleConversationContent() &&
        visibleConversationReadIssue() == null &&
        visibleConversationReads().let { reads ->
            reads.metadata.phase == ConversationReadPhase.LOADING ||
                reads.history.phase == ConversationReadPhase.LOADING ||
                reads.agents[selectedAgent]?.phase == ConversationReadPhase.LOADING
        }

data class WorkspaceDocument(
    val name: String,
    val content: String,
    val source: File,
    val relativePath: String,
    val line: Int? = null,
)

internal data class RunMutationContext(
    val mac: RelayDevice,
    val workspaceId: String,
    val runId: String,
    val settings: RemoteRunSettings,
) {
    val key: String
        get() = "${mac.deviceId}\u001f$workspaceId\u001f$runId"
}

internal sealed interface PendingStorageAction {
    val context: RunMutationContext
    val message: String

    data class More(
        val count: Int,
        override val context: RunMutationContext,
        override val message: String,
    ) : PendingStorageAction
}

internal data class PendingFolderCreation(val macId: String, val parent: String)

internal data class FollowUpContext(
    val key: String,
    val macId: String,
    val workspaceId: String,
    val runId: String,
)

internal data class PendingRunNavigation(
    val macId: String,
    val workspaceId: String,
    val runId: String,
)

internal fun isNoResumeSession(value: String): Boolean =
    value.isBlank() || value.equals("NO", ignoreCase = true)

internal fun transientRelayConnectionMessage(
    error: Throwable,
    language: AppLanguage = AppLanguage.CHINESE,
): String? {
    val causes = generateSequence(error as Throwable?) { it.cause }.take(8).toList()
    val messages = causes.joinToString("\n") { it.message.orEmpty().lowercase() }
    val recognizedType = causes.any {
        it is ConnectException || it is NoRouteToHostException ||
            it is SocketTimeoutException || it is UnknownHostException
    }
    return if (recognizedType || TRANSIENT_RELAY_ERROR_MARKERS.any { it in messages }) {
        language.text(
            TRANSIENT_RELAY_CONNECTION_MESSAGE,
            TRANSIENT_RELAY_CONNECTION_MESSAGE_ENGLISH,
        )
    } else {
        null
    }
}

internal fun remoteFileSystemMessage(
    message: String,
    language: AppLanguage = AppLanguage.CHINESE,
): String? {
    val normalized = message.lowercase()
    return when {
        "operating system denied permission to create" in normalized -> language.text(
            "目标设备不允许在此处创建文件夹，请检查权限后重试。",
            "The target device cannot create a folder here. Check its permissions and try again.",
        )
        "operating system denied access" in normalized -> language.text(
            "目标设备拒绝访问这个文件夹，请检查权限后重试。",
            "The target device denied access to this folder. Check its permissions and try again.",
        )
        "directory does not exist" in normalized -> language.text(
            "这个文件夹不存在或已被移动。",
            "This folder no longer exists or has moved.",
        )
        "path is not a directory" in normalized -> language.text(
            "所选路径不是文件夹。",
            "The selected path is not a folder.",
        )
        "cannot list directory" in normalized || "cannot inspect directory" in normalized ->
            language.text(
                "无法读取这个文件夹，请检查权限后重试。",
                "This folder cannot be read. Check its permissions and try again.",
            )
        else -> null
    }
}

internal fun isTerminalRunStatus(value: String): Boolean =
    isTerminalRunStatusValue(value)

internal fun isTerminalAgentStatus(value: String): Boolean =
    isTerminalAgentStatusValue(value)

/**
 * Whether the current turn is still waiting for an Agent result, rather than
 * merely waiting for pcrd to publish or apply results that already finished.
 */
internal fun runAwaitingAgentResults(state: MainUiState): Boolean {
    if (state.visibleRunId == null || isTerminalRunStatus(state.runStatus)) return false
    val agents = state.agentSummaries
    if (agents.isEmpty() || agents.any { !isTerminalAgentStatus(it.status) }) return true
    if (state.runStatus.equals("finalizing", ignoreCase = true)) return false
    return state.runSynthesisAgents > 0 &&
        agents.none { it.role.equals("synthesis", ignoreCase = true) }
}

internal fun workspaceRemovalLooksActive(workspace: RemoteWorkspace): Boolean =
    workspace.runWorkerActive || workspace.runStatus.lowercase() in setOf(
        "preparing", "starting", "running", "stopping", "finalizing",
    )

internal fun workspaceRemovalMayDiscardResults(workspace: RemoteWorkspace): Boolean =
    workspace.lastRunId.isNotBlank() && !workspaceRemovalLooksActive(workspace) &&
        workspace.runStatus.lowercase() !in setOf("finalized", "discarded", "expired")

internal fun workspaceNeedsDetailPolling(
    workspace: RemoteWorkspace,
    contextChanged: Boolean,
    detailMissing: Boolean = false,
    forceRefresh: Boolean = false,
): Boolean = workspace.lastRunId.isNotBlank() && (
    forceRefresh || contextChanged || detailMissing || workspace.runWorkerActive ||
        !isTerminalRunStatus(workspace.runStatus)
)

internal fun workspaceNeedsCompletionMonitoring(workspace: RemoteWorkspace): Boolean =
    workspace.lastRunId.isNotBlank() && (
        workspace.runWorkerActive || !isTerminalRunStatus(workspace.runStatus)
    )

internal fun completionMonitorKey(macId: String, workspaceId: String, runId: String): String =
    "$macId\u001f$workspaceId\u001f$runId"

internal fun isActiveAgentStatus(value: String): Boolean =
    value.trim().lowercase() in ACTIVE_AGENT_STATES

internal fun isAgentStopAvailable(value: String): Boolean =
    value.trim().lowercase() in setOf("running", "waiting_for_models")

internal fun promotedSessionId(result: JsonObject): String? =
    (result["session_promotion"] as? JsonObject)?.get("session_id")
        ?.jsonPrimitive?.contentOrNull?.trim()?.takeIf(String::isNotEmpty)

internal fun followUpCountdownSeconds(deadlineMillis: Long, nowMillis: Long): Int =
    ((deadlineMillis - nowMillis + 999L) / 1_000L)
        .coerceAtLeast(0L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()

internal fun PersistedQueuedFollowUp.afterDispatchFailure(
    remoteRejected: Boolean,
    nowMillis: Long = System.currentTimeMillis(),
    errorMessage: String = "",
): PersistedQueuedFollowUp {
    val nextAttempt = attempt + if (remoteRejected) 1 else 0
    val nextFailureCount = failureCount + 1
    val shouldPause = remoteRejected && nextAttempt >= MAX_AUTOMATIC_REMOTE_RETRIES
    return copy(
        attempt = nextAttempt,
        dispatching = !remoteRejected,
        failureCount = nextFailureCount,
        retryAtMillis = if (shouldPause) 0 else {
            nowMillis + followUpRetryDelayMillis(nextFailureCount)
        },
        lastError = errorMessage.trim(),
        paused = shouldPause,
    )
}

internal fun followUpRetryDelayMillis(failureCount: Int): Long {
    val exponent = (failureCount.coerceAtLeast(1) - 1).coerceAtMost(6)
    return (5_000L shl exponent).coerceAtMost(300_000L)
}

internal fun mergeFollowUpQueues(
    queued: List<PersistedQueuedFollowUp>,
    existing: List<PersistedQueuedFollowUp>,
): List<PersistedQueuedFollowUp> {
    val knownIds = queued.mapTo(mutableSetOf(), PersistedQueuedFollowUp::id)
    return queued + existing.filterNot { it.id in knownIds }
}

internal fun effectiveResumeSessionId(
    settings: RemoteRunSettings,
    lastResumeSessionId: String,
): String = when {
    settings.resumeSessionId.equals("NO", ignoreCase = true) -> ""
    settings.resumeSessionId.isNotBlank() -> settings.resumeSessionId
    else -> lastResumeSessionId
}

internal fun RemoteRunSettings.asDefaultConversationSettings(): RemoteRunSettings = copy(
    configured = true,
    resumeSessionId = "NO",
    resumeIncludeNonInteractive = false,
    runsDir = "",
    codexBin = "codex",
).normalized()

internal fun continuationSourceAgent(state: MainUiState): AgentSummary? {
    if (state.visibleRunId == null) return null
    if (runAwaitingAgentResults(state)) {
        // During an active turn, only an explicitly visible successful branch
        // is a continuation decision. Do not silently fall back to another
        // Agent while the user is reading a running or failed branch.
        return state.agentSummaries.firstOrNull {
            it.number == state.selectedAgent && it.canContinue
        }
    }
    val transitionPending = !isTerminalRunStatus(state.runStatus)
    fun AgentSummary.canContinueFromFinishedTurn(): Boolean = canContinue || (
        transitionPending &&
            status.equals("success", ignoreCase = true) &&
            workspaceAvailable
        )
    state.finalizedAgent?.let { finalized ->
        return state.agentSummaries.firstOrNull {
            it.number == finalized && it.canContinueFromFinishedTurn()
        }
    }
    return state.agentSummaries.firstOrNull {
        it.number == state.selectedAgent && it.canContinueFromFinishedTurn()
    } ?: state.agentSummaries.firstOrNull {
        it.number == state.recommendedAgent && it.canContinueFromFinishedTurn()
    } ?: state.agentSummaries.firstOrNull { it.canContinueFromFinishedTurn() }
}

internal fun continuationStopsRunningAgents(state: MainUiState): Boolean =
    state.followUpQueue.isEmpty() &&
        runAwaitingAgentResults(state) &&
        continuationSourceAgent(state) != null

internal fun promptContinuationBlockReason(state: MainUiState): String? {
    if (state.isVisibleConversationReadBlocked()) return state.appLanguage.text(
        "读取当前对话后才能继续提问。",
        "Load the current conversation before continuing.",
    )
    if (canPersistFollowUp(state) || state.visibleRunId == null || !isTerminalRunStatus(state.runStatus) ||
        continuationSourceAgent(state) != null
    ) return null
    // Workspace defaults may point to a completely different conversation.
    // A terminal turn without a continuation candidate is not a new-run request.
    return state.appLanguage.text(
        "本轮没有可续接的回复",
        "No reply is available to continue from this turn",
    )
}

internal fun canPersistFollowUp(state: MainUiState): Boolean =
    state.visibleRunId != null && state.selectedWorkspaceId != null && state.runSettings.syncBack &&
        !state.isVisibleConversationReadBlocked() &&
        (state.runActionInProgress || state.followUpQueue.isNotEmpty() ||
            !isTerminalRunStatus(state.runStatus) ||
            (state.finalizedAgent != null &&
                state.runStatus.lowercase() in setOf("sync_ambiguous", "cleanup_failed")))

internal fun canSubmitComposer(state: MainUiState): Boolean =
    (!state.runActionInProgress || canPersistFollowUp(state)) &&
        promptContinuationBlockReason(state) == null && state.pendingSubmission == null &&
        state.prompt.isNotBlank() && state.selectedWorkspaceId != null

private const val MAX_AUTOMATIC_REMOTE_RETRIES = 3
internal const val TRANSIENT_RELAY_CONNECTION_MESSAGE =
    "暂时无法连接服务器，请检查网络后重试。"
internal const val TRANSIENT_RELAY_CONNECTION_MESSAGE_ENGLISH =
    "The server is temporarily unavailable. Check your connection and try again."

private val TRANSIENT_RELAY_ERROR_MARKERS = setOf(
    "failed to connect", "connection refused", "network is unreachable",
    "no route to host", "unable to resolve host", "unexpected end of stream",
    "unexpected_eof_while_reading", "software caused connection abort",
)

private val ACTIVE_AGENT_STATES = setOf(
    "preparing", "starting", "copying", "queued", "running", "waiting_for_models",
    "stopping", "finalizing",
)
