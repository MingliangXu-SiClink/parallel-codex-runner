package com.parallelcodexrunner.remote.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.Json
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

@Serializable
data class PersistedMobileState(
    val selectedMacId: String? = null,
    val cachedDevices: List<RelayDevice> = emptyList(),
    val selectedWorkspaceByMac: Map<String, String> = emptyMap(),
    val viewedRunIdsByWorkspace: Map<String, String> = emptyMap(),
    val detachedConversationContexts: Set<String> = emptySet(),
    val detachedResumeSessions: Map<String, String> = emptyMap(),
    val cachedWorkspacesByMac: Map<String, List<RemoteWorkspace>> = emptyMap(),
    val promptDrafts: Map<String, String> = emptyMap(),
    // Serialized migration input for versions through 0.1.47.
    val followUpQueues: Map<String, List<String>> = emptyMap(),
    val followUpQueueItems: Map<String, List<PersistedQueuedFollowUp>> = emptyMap(),
    val followUpDeadlines: Map<String, Long> = emptyMap(),
    val workspaceSettings: Map<String, RemoteRunSettings> = emptyMap(),
    val dirtyWorkspaceSettingFields: Map<String, Set<String>> = emptyMap(),
    val defaultSettingsByMac: Map<String, RemoteRunSettings> = emptyMap(),
    val dirtyDefaultSettingFieldsByMac: Map<String, Set<String>> = emptyMap(),
    val promptOutbox: Map<String, PendingPromptSubmission> = emptyMap(),
    val acceptedPromptResults: Map<String, AcceptedPromptResult> = emptyMap(),
    val feedbackOutbox: Map<String, PendingFeedbackSubmission> = emptyMap(),
    val selectedAgents: Map<String, Int> = emptyMap(),
    val workExpansionStates: Map<String, Boolean> = emptyMap(),
    val viewedAgentCompletions: Set<String> = emptySet(),
    val unreadRunCompletions: Set<String> = emptySet(),
    // Kept only as a serialized migration input for versions through 0.1.36.
    val dirtyDefaultSettingsMacIds: Set<String> = emptySet(),
)

@Serializable
data class PersistedQueuedFollowUp(
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

@Serializable
data class PersistedFollowUpDispatch(
    val mode: String,
    val sourceRunId: String = "",
    val sourceAgent: Int? = null,
    val settings: RemoteRunSettings,
    val resumeSessionId: String = "",
) {
    companion object {
        const val MODE_START = "start"
        const val MODE_CONTINUE = "continue"
        val validModes = setOf(MODE_START, MODE_CONTINUE)
    }
}

@Serializable
data class PendingPromptSubmission(
    val id: String,
    val macId: String,
    val workspaceId: String,
    val workspaceName: String = "",
    val prompt: String,
    val settings: RemoteRunSettings,
    val sourceRunId: String = "",
    val sourceAgent: Int? = null,
    val resumeSessionId: String = "",
    val confirmLargeRun: Boolean = false,
    val attempt: Int = 0,
    val status: String = STATUS_PENDING,
    val error: String = "",
    val retryRequiresNewCommandId: Boolean = false,
) {
    val commandId: String
        get() = buildString {
            append(id)
            append("-attempt-")
            append(attempt.coerceAtLeast(0))
            if (confirmLargeRun) append("-confirmed")
        }

    companion object {
        const val STATUS_PENDING = "pending"
        const val STATUS_RETRYING = "retrying"
        const val STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
        const val STATUS_FAILED = "failed"
        val validStatuses = setOf(
            STATUS_PENDING,
            STATUS_RETRYING,
            STATUS_AWAITING_CONFIRMATION,
            STATUS_FAILED,
        )
    }
}

@Serializable
data class AcceptedPromptResult(
    val submission: PendingPromptSubmission,
    val runId: String,
    val continuedSessionId: String = "",
    val workspaceRevision: Int = -1,
) {
    val commandId: String
        get() = submission.commandId
}

@Serializable
data class PendingFeedbackAttachment(
    val fileName: String,
    val contentType: String,
    val sha256: String,
    val sizeBytes: Long,
)

@Serializable
data class PendingFeedbackSubmission(
    val id: String,
    @SerialName("macId") val relatedDeviceId: String = "",
    val kind: String,
    val description: String,
    val diagnostics: kotlinx.serialization.json.JsonObject,
    val attachments: List<PendingFeedbackAttachment> = emptyList(),
    val feedbackId: String = "",
    val status: String = STATUS_PENDING,
    val error: String = "",
) {
    companion object {
        const val STATUS_PENDING = "pending"
        const val STATUS_RETRYING = "retrying"
        const val STATUS_FAILED = "failed"
        val validStatuses = setOf(STATUS_PENDING, STATUS_RETRYING, STATUS_FAILED)
    }
}

internal fun PendingPromptSubmission.forRetry(): PendingPromptSubmission = copy(
    attempt = attempt + if (retryRequiresNewCommandId) 1 else 0,
    status = PendingPromptSubmission.STATUS_RETRYING,
    error = "",
    retryRequiresNewCommandId = false,
)

internal fun PersistedMobileState.recordingAcceptedPromptResult(
    contextKey: String,
    expected: PendingPromptSubmission,
    result: AcceptedPromptResult,
): PersistedMobileState {
    val current = promptOutbox[contextKey]
    if (current?.id != expected.id || current.commandId != expected.commandId) return this
    return copy(
        promptOutbox = promptOutbox - contextKey,
        acceptedPromptResults = acceptedPromptResults +
            (contextKey to result.copy(submission = current)),
    )
}

private fun normalizedPromptSubmission(
    raw: PendingPromptSubmission,
): PendingPromptSubmission? {
    val submission = raw.copy(
        id = raw.id.trim(),
        macId = raw.macId.trim(),
        workspaceId = raw.workspaceId.trim(),
        prompt = raw.prompt,
        settings = raw.settings.normalized(),
        sourceRunId = raw.sourceRunId.trim(),
        sourceAgent = raw.sourceAgent?.takeIf { it > 0 },
        resumeSessionId = raw.resumeSessionId.trim(),
        attempt = raw.attempt.coerceAtLeast(0),
        status = raw.status.takeIf(PendingPromptSubmission.validStatuses::contains)
            ?: PendingPromptSubmission.STATUS_PENDING,
        error = raw.error.trim(),
    )
    return submission.takeIf {
        it.id.isNotEmpty() && it.macId.isNotEmpty() &&
            it.workspaceId.isNotEmpty() && it.prompt.isNotBlank()
    }
}

internal fun PersistedMobileState.normalized(): PersistedMobileState {
    val normalizedFollowUps = followUpQueueItems
        .filterKeys(String::isNotBlank)
        .mapValues { (_, items) ->
            items.mapNotNull { raw ->
                val prompt = raw.prompt.takeIf(String::isNotBlank) ?: return@mapNotNull null
                val legacyIdleExpiryPause = raw.paused &&
                    !raw.automaticRecoveryAttempted &&
                    raw.lastError.trim() == LEGACY_NO_CONTINUABLE_AGENT_MESSAGE
                PersistedQueuedFollowUp(
                    id = raw.id.trim().ifEmpty { UUID.randomUUID().toString() },
                    prompt = prompt,
                    settings = raw.settings?.normalized(),
                    attempt = raw.attempt.coerceAtLeast(0),
                    dispatching = raw.dispatching && raw.dispatch != null,
                    failureCount = raw.failureCount.coerceAtLeast(0),
                    retryAtMillis = raw.retryAtMillis.coerceAtLeast(0),
                    lastError = if (legacyIdleExpiryPause) "" else raw.lastError.trim(),
                    paused = raw.paused && !legacyIdleExpiryPause,
                    automaticRecoveryAttempted = raw.automaticRecoveryAttempted ||
                        legacyIdleExpiryPause,
                    daemonAcknowledged = raw.daemonAcknowledged,
                    dispatch = raw.dispatch?.takeIf {
                        it.mode in PersistedFollowUpDispatch.validModes &&
                            (it.mode != PersistedFollowUpDispatch.MODE_CONTINUE ||
                                (it.sourceRunId.isNotBlank() && it.sourceAgent != null))
                    }?.copy(
                        sourceRunId = raw.dispatch.sourceRunId.trim(),
                        sourceAgent = raw.dispatch.sourceAgent?.takeIf { it > 0 },
                        settings = raw.dispatch.settings.normalized(),
                        resumeSessionId = raw.dispatch.resumeSessionId.trim(),
                    ),
                )
            }
        }
        .filterValues { it.isNotEmpty() }
        .toMutableMap()
    followUpQueues
        .filterKeys(String::isNotBlank)
        .forEach { (key, prompts) ->
            normalizedFollowUps.putIfAbsent(
                key,
                prompts.filter(String::isNotBlank).map { prompt ->
                    PersistedQueuedFollowUp(
                        id = UUID.randomUUID().toString(),
                        prompt = prompt,
                    )
                },
            )
        }
    normalizedFollowUps.entries.removeAll { it.value.isEmpty() }
    val defaultDirtyFields = dirtyDefaultSettingFieldsByMac
        .filterKeys(String::isNotBlank)
        .mapValues { (_, fields) ->
            WorkspaceSettingFields.normalized(fields)
                .intersect(WorkspaceSettingFields.defaultConversation)
        }
        .filterValues { it.isNotEmpty() }
        .toMutableMap()
    dirtyDefaultSettingsMacIds.filter(String::isNotBlank).forEach { macId ->
        defaultDirtyFields.putIfAbsent(macId, WorkspaceSettingFields.defaultConversation)
    }
    val normalizedFeedbackOutbox = feedbackOutbox.mapNotNull { (key, raw) ->
        val id = raw.id.trim().takeIf(String::isNotBlank) ?: return@mapNotNull null
        val relatedDeviceId = raw.relatedDeviceId.trim()
        val description = raw.description.trim().takeIf(String::isNotBlank)
            ?: return@mapNotNull null
        val kind = raw.kind.trim().lowercase()
            .takeIf { it in FEEDBACK_KINDS } ?: return@mapNotNull null
        key.trim().takeIf(String::isNotBlank)?.let {
            id to raw.copy(
                id = id,
                relatedDeviceId = relatedDeviceId,
                kind = kind,
                description = description,
                attachments = raw.attachments.filter { attachment ->
                    attachment.fileName.isNotBlank() &&
                        '/' !in attachment.fileName &&
                        '\\' !in attachment.fileName &&
                        attachment.contentType in FEEDBACK_ATTACHMENT_TYPES &&
                        attachment.sha256.matches(SHA256_PATTERN) &&
                        attachment.sizeBytes in 1..MAX_FEEDBACK_ATTACHMENT_BYTES
                }.take(3),
                feedbackId = raw.feedbackId.trim(),
                status = raw.status.takeIf(
                    PendingFeedbackSubmission.validStatuses::contains
                ) ?: PendingFeedbackSubmission.STATUS_PENDING,
                error = raw.error.trim().take(500),
            )
        }
    }.toMap()
    return copy(
        selectedMacId = selectedMacId?.trim()?.takeIf(String::isNotEmpty),
        cachedDevices = cachedDevices
            .mapNotNull { device ->
                val deviceId = device.deviceId.trim()
                val name = device.name.trim()
                val publicKey = device.publicKey.trim()
                if (deviceId.isEmpty() || name.isEmpty() || publicKey.isEmpty()) {
                    return@mapNotNull null
                }
                device.copy(
                    deviceId = deviceId,
                    kind = device.kind.trim(),
                    name = name,
                    publicKey = publicKey,
                    status = device.status.trim(),
                    lastSeenAt = device.lastSeenAt.trim(),
                )
            }
            .distinctBy(RelayDevice::deviceId),
        selectedWorkspaceByMac = selectedWorkspaceByMac
            .mapNotNull { (macId, workspaceId) ->
                val normalizedMacId = macId.trim()
                val normalizedWorkspaceId = workspaceId.trim()
                if (normalizedMacId.isEmpty() || normalizedWorkspaceId.isEmpty()) null
                else normalizedMacId to normalizedWorkspaceId
            }
            .toMap(),
        detachedConversationContexts = detachedConversationContexts
            .map(String::trim)
            .filter { context ->
                val parts = context.split('\u001f')
                parts.size == 2 && parts.none(String::isBlank)
            }
            .toSet(),
        viewedRunIdsByWorkspace = viewedRunIdsByWorkspace
            .filter { (key, runId) ->
                val parts = key.split('\u001f')
                parts.size == 2 && parts.none(String::isBlank) && runId.isNotBlank() &&
                    key !in detachedConversationContexts
            }
            .mapValues { (_, runId) -> runId.trim() },
        detachedResumeSessions = detachedResumeSessions
            .filterKeys { it in detachedConversationContexts }
            .mapValues { (_, session) -> session.trim().ifBlank { "NO" } },
        cachedWorkspacesByMac = cachedWorkspacesByMac
            .mapNotNull { (macId, workspaces) ->
                val normalizedMacId = macId.trim()
                if (normalizedMacId.isEmpty()) return@mapNotNull null
                val seen = mutableSetOf<String>()
                val normalized = workspaces.mapNotNull { workspace ->
                    val workspaceId = workspace.workspaceId.trim()
                    val path = workspace.path.trim()
                    if (workspaceId.isEmpty() || path.isEmpty() || !seen.add(workspaceId)) {
                        return@mapNotNull null
                    }
                    workspace.copy(
                        workspaceId = workspaceId,
                        name = workspace.name.trim().ifBlank {
                            path.substringAfterLast('/').ifBlank { path }
                        },
                        path = path,
                        lastRunId = workspace.lastRunId.trim(),
                        lastResumeSessionId = workspace.lastResumeSessionId.trim(),
                        runStatus = workspace.runStatus.trim(),
                        settings = workspace.settings?.normalized(),
                    )
                }
                normalizedMacId to normalized
            }
            .filter { (_, workspaces) -> workspaces.isNotEmpty() }
            .toMap(),
        promptDrafts = promptDrafts
            .filterKeys(String::isNotBlank)
            .filterValues(String::isNotEmpty),
        followUpQueues = emptyMap(),
        followUpQueueItems = normalizedFollowUps,
        followUpDeadlines = followUpDeadlines
            .filterKeys(String::isNotBlank)
            .filterValues { it > 0L },
        workspaceSettings = workspaceSettings
            .filterKeys(String::isNotBlank)
            .mapValues { (_, settings) -> settings.normalized() },
        dirtyWorkspaceSettingFields = dirtyWorkspaceSettingFields
            .filterKeys(String::isNotBlank)
            .mapValues { (_, fields) -> WorkspaceSettingFields.normalized(fields) }
            .filterValues { it.isNotEmpty() },
        defaultSettingsByMac = defaultSettingsByMac
            .filterKeys(String::isNotBlank)
            .mapValues { (_, settings) -> settings.normalized() },
        dirtyDefaultSettingFieldsByMac = defaultDirtyFields,
        promptOutbox = promptOutbox
            .filterKeys(String::isNotBlank)
            .mapNotNull { (key, raw) ->
                normalizedPromptSubmission(raw)?.let { key to it }
            }
            .toMap(),
        acceptedPromptResults = acceptedPromptResults
            .filterKeys(String::isNotBlank)
            .mapNotNull { (key, raw) ->
                val submission = normalizedPromptSubmission(raw.submission)
                    ?: return@mapNotNull null
                val runId = raw.runId.trim()
                val expectedKey = "${submission.macId}\u001f${submission.workspaceId}"
                if (runId.isEmpty() || key != expectedKey) return@mapNotNull null
                key to raw.copy(
                    submission = submission,
                    runId = runId,
                    continuedSessionId = raw.continuedSessionId.trim(),
                    workspaceRevision = raw.workspaceRevision.coerceAtLeast(-1),
                )
            }
            .toMap(),
        feedbackOutbox = normalizedFeedbackOutbox,
        selectedAgents = selectedAgents
            .filterKeys(String::isNotBlank)
            .filterValues { it > 0 },
        workExpansionStates = workExpansionStates
            .filterKeys(::isValidWorkExpansionContextKey),
        viewedAgentCompletions = viewedAgentCompletions
            .filterTo(mutableSetOf(), ::isValidAgentContextKey),
        unreadRunCompletions = unreadRunCompletions
            .filterTo(mutableSetOf(), ::isValidRunContextKey),
        dirtyDefaultSettingsMacIds = emptySet(),
    )
}

private const val LEGACY_NO_CONTINUABLE_AGENT_MESSAGE =
    "没有可继续的 Agent，排队消息已暂停。"
private val FEEDBACK_KINDS = setOf("bug", "feature", "experience")
private val FEEDBACK_ATTACHMENT_TYPES = setOf("image/jpeg", "image/png", "image/webp")
private val SHA256_PATTERN = Regex("[0-9a-f]{64}")
private const val MAX_FEEDBACK_ATTACHMENT_BYTES = 512L * 1024L

internal fun PersistedMobileState.withoutMac(macId: String): PersistedMobileState {
    val normalizedMacId = macId.trim()
    if (normalizedMacId.isEmpty()) return this
    val contextPrefix = "$normalizedMacId\u001f"
    return copy(
        selectedMacId = selectedMacId?.takeUnless { it == normalizedMacId },
        cachedDevices = cachedDevices.filterNot { it.deviceId == normalizedMacId },
        selectedWorkspaceByMac = selectedWorkspaceByMac - normalizedMacId,
        viewedRunIdsByWorkspace = viewedRunIdsByWorkspace.filterKeys {
            !it.startsWith(contextPrefix)
        },
        detachedConversationContexts = detachedConversationContexts.filterNotTo(
            mutableSetOf()
        ) { it.startsWith(contextPrefix) },
        detachedResumeSessions = detachedResumeSessions.filterKeys { !it.startsWith(contextPrefix) },
        cachedWorkspacesByMac = cachedWorkspacesByMac - normalizedMacId,
        promptDrafts = promptDrafts.filterKeys { !it.startsWith(contextPrefix) },
        followUpQueues = followUpQueues.filterKeys { !it.startsWith(contextPrefix) },
        followUpQueueItems = followUpQueueItems.filterKeys {
            !it.startsWith(contextPrefix)
        },
        followUpDeadlines = followUpDeadlines.filterKeys {
            !it.startsWith(contextPrefix)
        },
        workspaceSettings = workspaceSettings.filterKeys {
            !it.startsWith(contextPrefix)
        },
        dirtyWorkspaceSettingFields = dirtyWorkspaceSettingFields.filterKeys {
            !it.startsWith(contextPrefix)
        },
        defaultSettingsByMac = defaultSettingsByMac - normalizedMacId,
        dirtyDefaultSettingFieldsByMac = dirtyDefaultSettingFieldsByMac - normalizedMacId,
        promptOutbox = promptOutbox.filterKeys { !it.startsWith(contextPrefix) },
        acceptedPromptResults = acceptedPromptResults.filterKeys {
            !it.startsWith(contextPrefix)
        },
        // Product feedback is stored by Relay. Keep queued submissions even if
        // their optional related machine is removed before connectivity returns.
        feedbackOutbox = feedbackOutbox,
        selectedAgents = selectedAgents.filterKeys { !it.startsWith(contextPrefix) },
        workExpansionStates = workExpansionStates.filterKeys {
            !it.startsWith(contextPrefix)
        },
        viewedAgentCompletions = viewedAgentCompletions.filterNotTo(mutableSetOf()) {
            it.startsWith(contextPrefix)
        },
        unreadRunCompletions = unreadRunCompletions.filterNotTo(mutableSetOf()) {
            it.startsWith(contextPrefix)
        },
        dirtyDefaultSettingsMacIds = dirtyDefaultSettingsMacIds - normalizedMacId,
    )
}

private fun isValidAgentContextKey(value: String): Boolean {
    val parts = value.split('\u001f')
    return parts.size == 4 && parts.take(3).none(String::isBlank) &&
        (parts.last().toIntOrNull() ?: 0) > 0
}

private fun isValidWorkExpansionContextKey(value: String): Boolean {
    val parts = value.split('\u001f')
    return (parts.size == 2 && parts.none(String::isBlank)) ||
        isValidAgentContextKey(value)
}

private fun isValidRunContextKey(value: String): Boolean {
    val parts = value.split('\u001f')
    return parts.size == 3 && parts.none(String::isBlank)
}

class MobileStateStore(private val secureStore: SecureStore) {
    private val json = Json { ignoreUnknownKeys = true }
    private val lock = Any()
    private val mutableState = MutableStateFlow(readPersistedState())
    val state: StateFlow<PersistedMobileState> = mutableState.asStateFlow()

    fun load(): PersistedMobileState = mutableState.value

    fun save(state: PersistedMobileState) = synchronized(lock) {
        persist(state.normalized())
    }

    fun update(
        transform: (PersistedMobileState) -> PersistedMobileState,
    ): PersistedMobileState = synchronized(lock) {
        val updated = transform(mutableState.value).normalized()
        persist(updated)
        updated
    }

    fun clear() = synchronized(lock) {
        secureStore.remove(STATE_KEY)
        mutableState.value = PersistedMobileState()
    }

    private fun readPersistedState(): PersistedMobileState = secureStore.getString(STATE_KEY)
        ?.let { encoded ->
            runCatching { json.decodeFromString<PersistedMobileState>(encoded) }.getOrNull()
        }
        ?.normalized()
        ?: PersistedMobileState()

    private fun persist(state: PersistedMobileState) {
        secureStore.putString(STATE_KEY, json.encodeToString(state))
        mutableState.value = state
    }

    private companion object {
        const val STATE_KEY = "mobile-client-state-v1"
    }
}
