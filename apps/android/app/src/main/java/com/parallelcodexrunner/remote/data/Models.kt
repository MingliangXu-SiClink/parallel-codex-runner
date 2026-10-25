package com.parallelcodexrunner.remote.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import com.parallelcodexrunner.remote.data.generated.CONVERSATION_SCHEMA_VERSION
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot

const val MAX_AGENT_COUNT = 4_096

@Serializable
data class DeviceLoginChallenge(
    val version: Int,
    val purpose: String,
    @SerialName("challenge_id") val challengeId: String,
    @SerialName("device_id") val deviceId: String,
    @SerialName("user_id") val userId: String,
    @SerialName("public_key") val publicKey: String,
    @SerialName("authorization_generation") val authorizationGeneration: Int,
    @SerialName("relay_url") val relayUrl: String,
    @SerialName("server_public_key") val serverPublicKey: String,
    val nonce: String,
    @SerialName("expires_at") val expiresAt: String,
)

@Serializable
data class TokenBundle(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("expires_in") val expiresIn: Long,
    @SerialName("user_id") val userId: String,
    @SerialName("device_id") val deviceId: String,
    // A dynamic default can be omitted when this object is serialized in the
    // same millisecond it was created. Decoding would then reset the issue time
    // on every read and make an expired access token appear fresh forever.
    val issuedAtMillis: Long = 0,
) {
    val accessExpiresAtMillis: Long
        get() = issuedAtMillis + expiresIn * 1_000
}

@Serializable
data class RelayDevice(
    @SerialName("device_id") val deviceId: String,
    val kind: String,
    val name: String,
    @SerialName("public_key") val publicKey: String,
    val status: String,
    val online: Boolean? = null,
    @SerialName("last_seen_at") val lastSeenAt: String = "",
)

@Serializable
data class DeviceListResponse(val devices: List<RelayDevice>)

data class PairingResult(val retainedAccount: Boolean)


@Serializable
data class AndroidUpdateCheckRequest(
    @SerialName("mac_device_id") val macDeviceId: String,
    @SerialName("package_name") val packageName: String,
    @SerialName("current_version_code") val currentVersionCode: Long,
)

@Serializable
data class AndroidUpdateInfo(
    @SerialName("release_id") val releaseId: String,
    @SerialName("package_name") val packageName: String,
    @SerialName("version_code") val versionCode: Long,
    @SerialName("version_name") val versionName: String,
    @SerialName("release_notes") val releaseNotes: String = "",
    val sha256: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    @SerialName("signing_cert_sha256") val signingCertSha256: String? = null,
    @SerialName("published_at") val publishedAt: String = "",
    val available: Boolean = true,
)

@Serializable
data class AndroidUpdateCheckResponse(
    val available: Boolean,
    val update: AndroidUpdateInfo? = null,
)

@Serializable
data class AndroidUpdateHistoryResponse(
    val cursor: Int,
    @SerialName("next_cursor") val nextCursor: Int,
    @SerialName("has_more") val hasMore: Boolean,
    val releases: List<AndroidUpdateInfo>,
)

@Serializable
data class AndroidUpdateTicketRequest(
    @SerialName("mac_device_id") val macDeviceId: String,
    @SerialName("release_id") val releaseId: String,
)

@Serializable
data class AndroidUpdateTicketResponse(
    val release: AndroidUpdateInfo,
    val ticket: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("download_path") val downloadPath: String,
)


@Serializable
data class PairingLoginRequest(
    @SerialName("pairing_id") val pairingId: String,
    @SerialName("approval_token") val approvalToken: String,
    @SerialName("device_name") val deviceName: String,
    @SerialName("public_key") val publicKey: String,
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class PairingApproveRequest(
    @SerialName("pairing_proof") val pairingProof: String,
    @SerialName("approval_token") val approvalToken: String,
)

data class PairingLink(
    val relayUrl: String,
    val pairingId: String,
    val secret: String,
    val approvalToken: String,
    val macDeviceId: String,
    val macPublicKey: String,
)

@Serializable
data class RemoteWorkspace(
    @SerialName("workspace_id") val workspaceId: String,
    val name: String,
    val path: String,
    val available: Boolean = true,
    @SerialName("selected_at") val selectedAt: String = "",
    @SerialName("last_run_id") val lastRunId: String = "",
    @SerialName("last_resume_session_id") val lastResumeSessionId: String = "",
    @SerialName("run_status") val runStatus: String = "",
    @SerialName("run_worker_active") val runWorkerActive: Boolean = false,
    @SerialName("run_agents_total") val runAgentsTotal: Int = 0,
    @SerialName("run_agents_finished") val runAgentsFinished: Int = 0,
    @SerialName("run_agents_failed") val runAgentsFailed: Int = 0,
    val settings: RemoteRunSettings? = null,
)

@Serializable
data class RemoteRunSettings(
    val configured: Boolean = false,
    val agents: Int = 4,
    @SerialName("synthesis_agents") val synthesisAgents: Int = 2,
    @SerialName("follow_up_delay") val followUpDelay: Int = 60,
    val execution: String = "parallel",
    @SerialName("automatic_max_parallel") val automaticMaxParallel: Boolean = true,
    @SerialName("max_parallel") val maxParallel: Int = 4,
    val subagents: Boolean = false,
    @SerialName("subagents_limit") val subagentsLimit: Int = 8,
    @SerialName("recommend_by") val recommendBy: String = "reasoning_tokens",
    val model: String = "",
    val effort: String = "",
    @SerialName("fast_mode") val fastMode: String = "automatic",
    @SerialName("synthesis_model") val synthesisModel: String = "",
    @SerialName("synthesis_effort") val synthesisEffort: String = "",
    @SerialName("synthesis_fast_mode") val synthesisFastMode: String = "automatic",
    @SerialName("sync_back") val syncBack: Boolean = true,
    @SerialName("keep_workspaces") val keepWorkspaces: Boolean = false,
    @SerialName("resume_session_id") val resumeSessionId: String = "",
    @SerialName("resume_include_non_interactive") val resumeIncludeNonInteractive: Boolean = false,
    @SerialName("runs_dir") val runsDir: String = "",
    @SerialName("codex_bin") val codexBin: String = "codex",
)

@Serializable
data class RemoteFollowUpSettings(
    val agents: Int = 4,
    @SerialName("synthesis_agents") val synthesisAgents: Int = 2,
    @SerialName("follow_up_delay") val followUpDelay: Int = 60,
    val execution: String = "parallel",
    @SerialName("automatic_max_parallel") val automaticMaxParallel: Boolean = true,
    @SerialName("max_parallel") val maxParallel: Int = 4,
    val subagents: Boolean = false,
    @SerialName("subagents_limit") val subagentsLimit: Int = 8,
    val recommendation: String = "reasoning_tokens",
    val model: String = "",
    val effort: String = "",
    val fast: String = "automatic",
    @SerialName("synthesis_model") val synthesisModel: String = "",
    @SerialName("synthesis_effort") val synthesisEffort: String = "",
    @SerialName("synthesis_fast") val synthesisFast: String = "automatic",
    @SerialName("sync_back") val syncBack: Boolean = true,
    @SerialName("keep_workspaces") val keepWorkspaces: Boolean = false,
    @SerialName("resume_session_id") val resumeSessionId: String = "",
    @SerialName("resume_include_non_interactive")
    val resumeIncludeNonInteractive: Boolean = false,
    @SerialName("runs_directory") val runsDirectory: String = "",
    @SerialName("codex_executable") val codexExecutable: String = "codex",
) {
    fun asRunSettings(): RemoteRunSettings = RemoteRunSettings(
        agents = agents,
        synthesisAgents = synthesisAgents,
        followUpDelay = followUpDelay,
        execution = execution,
        automaticMaxParallel = automaticMaxParallel,
        maxParallel = maxParallel,
        subagents = subagents,
        subagentsLimit = subagentsLimit,
        recommendBy = recommendation,
        model = model,
        effort = effort,
        fastMode = fast,
        synthesisModel = synthesisModel,
        synthesisEffort = synthesisEffort,
        synthesisFastMode = synthesisFast,
        syncBack = syncBack,
        keepWorkspaces = keepWorkspaces,
        resumeSessionId = resumeSessionId,
        resumeIncludeNonInteractive = resumeIncludeNonInteractive,
        runsDir = runsDirectory,
        codexBin = codexExecutable,
    ).normalized()

    companion object {
        fun from(value: RemoteRunSettings): RemoteFollowUpSettings {
            val settings = value.normalized()
            return RemoteFollowUpSettings(
                agents = settings.agents,
                synthesisAgents = settings.synthesisAgents,
                followUpDelay = settings.followUpDelay,
                execution = settings.execution,
                automaticMaxParallel = settings.automaticMaxParallel,
                maxParallel = settings.maxParallel,
                subagents = settings.subagents,
                subagentsLimit = settings.subagentsLimit,
                recommendation = settings.recommendBy,
                model = settings.model,
                effort = settings.effort,
                fast = settings.fastMode,
                synthesisModel = settings.synthesisModel,
                synthesisEffort = settings.synthesisEffort,
                synthesisFast = settings.synthesisFastMode,
                syncBack = settings.syncBack,
                keepWorkspaces = settings.keepWorkspaces,
                resumeSessionId = settings.resumeSessionId,
                resumeIncludeNonInteractive = settings.resumeIncludeNonInteractive,
                runsDirectory = settings.runsDir,
                codexExecutable = settings.codexBin,
            )
        }
    }
}

@Serializable
data class RemoteFollowUpQueueItem(
    val id: String,
    val prompt: String,
    val settings: RemoteFollowUpSettings = RemoteFollowUpSettings(),
    val attempt: Int = 0,
    @SerialName("failure_count") val failureCount: Int = 0,
    @SerialName("retry_at_epoch_ms") val retryAtEpochMillis: Long? = null,
    @SerialName("last_error") val lastError: String = "",
    val paused: Boolean = false,
)

@Serializable
data class RemoteFollowUpQueue(
    @SerialName("workspace_id") val workspaceId: String,
    @SerialName("run_id") val runId: String,
    val items: List<RemoteFollowUpQueueItem> = emptyList(),
    @SerialName("selected_agent") val selectedAgent: Int? = null,
    @SerialName("selection_source") val selectionSource: String? = null,
    val state: String = "waiting",
    @SerialName("deadline_epoch_ms") val deadlineEpochMillis: Long? = null,
    @SerialName("last_error") val lastError: String = "",
    val generation: Int = 0,
    @SerialName("last_completed_item_id") val lastCompletedItemId: String? = null,
)

@Serializable
data class RemoteFollowUpQueueResponse(
    val revision: Int = 0,
    val queue: RemoteFollowUpQueue? = null,
)

@Serializable
data class RemoteFollowUpQueueListResponse(
    val revision: Int = 0,
    val queues: List<RemoteFollowUpQueue> = emptyList(),
)

internal fun RemoteRunSettings.normalized(): RemoteRunSettings = copy(
    agents = agents.coerceIn(1, MAX_AGENT_COUNT),
    synthesisAgents = synthesisAgents.coerceIn(0, MAX_AGENT_COUNT),
    followUpDelay = followUpDelay.coerceIn(0, 3_600),
    execution = if (execution.lowercase() == "serial") "serial" else "parallel",
    maxParallel = maxParallel.coerceIn(1, MAX_AGENT_COUNT),
    subagentsLimit = subagentsLimit.coerceIn(1, MAX_AGENT_COUNT),
    recommendBy = if (recommendBy.lowercase() == "duration") {
        "duration"
    } else {
        "reasoning_tokens"
    },
    model = model.trim(),
    effort = effort.trim().lowercase(),
    fastMode = normalizedFastMode(fastMode),
    synthesisModel = synthesisModel.trim(),
    synthesisEffort = synthesisEffort.trim().lowercase(),
    synthesisFastMode = normalizedFastMode(synthesisFastMode),
    resumeSessionId = resumeSessionId.trim().let {
        if (it.equals("NO", ignoreCase = true)) "NO" else it
    },
    runsDir = runsDir.trim(),
    codexBin = codexBin.trim().ifBlank { "codex" },
)

private fun normalizedFastMode(value: String): String =
    value.trim().lowercase().takeIf { it in setOf("automatic", "on", "off") }
        ?: "automatic"

@Serializable
data class WorkspaceSettingsResponse(
    @SerialName("workspace_id") val workspaceId: String? = null,
    val settings: RemoteRunSettings,
)

@Serializable
data class DefaultConversationSettingsResponse(
    val settings: RemoteRunSettings,
)

@Serializable
data class RunStorageEntry(
    @SerialName("run_id") val runId: String,
    val status: String = "",
    val workspace: String = "",
    @SerialName("workspace_name") val workspaceName: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    @SerialName("actual_bytes") val actualBytes: Long = 0,
    val actual: String = "0 B",
    @SerialName("reclaimable_bytes") val reclaimableBytes: Long = 0,
    val reclaimable: String = "0 B",
    @SerialName("cleanup_action") val cleanupAction: String? = null,
    @SerialName("finalized_agent") val finalizedAgent: Int? = null,
    @SerialName("keep_workspaces") val keepWorkspaces: Boolean = false,
)

@Serializable
data class StorageSummary(
    @SerialName("managed_bytes") val managedBytes: Long = 0,
    val managed: String = "0 B",
    @SerialName("reclaimable_bytes") val reclaimableBytes: Long = 0,
    val reclaimable: String = "0 B",
    @SerialName("unmanaged_bytes") val unmanagedBytes: Long = 0,
    val unmanaged: String = "0 B",
    @SerialName("unmanaged_count") val unmanagedCount: Int = 0,
    @SerialName("free_bytes") val freeBytes: Long = 0,
    val free: String = "0 B",
    @SerialName("volume_count") val volumeCount: Int = 1,
    val runs: List<RunStorageEntry> = emptyList(),
    @SerialName("total_runs") val totalRuns: Int = 0,
)

@Serializable
data class CodexOption(
    val label: String,
    val value: String,
    val selected: Boolean = false,
)

@Serializable
data class CodexModelOptions(
    @SerialName("configured_model") val configuredModel: String? = null,
    @SerialName("configured_effort") val configuredEffort: String? = null,
    @SerialName("configured_service_tier") val configuredServiceTier: String? = null,
    @SerialName("effective_model") val effectiveModel: String? = null,
    @SerialName("effective_effort") val effectiveEffort: String? = null,
    @SerialName("effective_fast") val effectiveFast: String? = null,
    val models: List<CodexOption> = emptyList(),
    val efforts: List<CodexOption> = emptyList(),
)

@Serializable
data class ResumeSession(
    @SerialName("session_id") val sessionId: String,
    val title: String = "",
    val cwd: String = "",
    @SerialName("updated_at") val updatedAt: Long? = null,
    @SerialName("created_at") val createdAt: Long? = null,
    val source: String = "",
    val model: String = "",
    @SerialName("rollout_path") val rolloutPath: String = "",
    val preview: String = "",
    @SerialName("tokens_used") val tokensUsed: Long? = null,
) {
    val displayTitle: String
        get() = title.trim().ifBlank {
            preview.trim().ifBlank { "之前的对话" }
        }
}

@Serializable
data class ResumeSessionList(
    val workspace: String = "",
    val sessions: List<ResumeSession> = emptyList(),
    val total: Int = 0,
)

@Serializable
data class ResumeHistoryEntry(
    val category: String,
    val text: String,
    val id: String = "",
    @SerialName("turn_id") val turnId: String = "",
)

@Serializable
data class ResumeHistoryPage(
    val cursor: Int,
    @SerialName("next_cursor") val nextCursor: Int,
    @SerialName("has_more") val hasMore: Boolean,
    val entries: List<ResumeHistoryEntry> = emptyList(),
)

@Serializable
data class WorkspaceList(
    val revision: Int,
    @SerialName("selected_workspace_id") val selectedWorkspaceId: String? = null,
    val workspaces: List<RemoteWorkspace> = emptyList(),
)

@Serializable
data class ConversationListResponse(
    val conversations: List<ConversationRecord> = emptyList(),
    val total: Int = 0,
    val cursor: String = "",
    @SerialName("next_cursor") val nextCursor: String = "",
    @SerialName("has_more") val hasMore: Boolean = false,
)

@Serializable
data class ConversationDetailResponse(
    val conversation: ConversationRecord = ConversationRecord(),
    val turns: List<ConversationSnapshot> = emptyList(),
)

@Serializable
data class FileSystemRoot(
    val name: String,
    val path: String,
    val kind: String,
    val readable: Boolean,
)

@Serializable
data class FileSystemRoots(
    val home: String,
    val roots: List<FileSystemRoot>,
)

@Serializable
data class CreatedDirectory(
    val name: String,
    val path: String,
    val parent: String,
    val readable: Boolean,
    val writable: Boolean,
    val created: Boolean,
)

@Serializable
data class DirectoryEntry(
    val name: String,
    val path: String,
    val kind: String,
    val readable: Boolean,
    val hidden: Boolean,
    @SerialName("modified_at") val modifiedAt: String? = null,
) {
    val isDirectory: Boolean
        get() = kind == "directory"
}

@Serializable
data class DirectoryPage(
    val path: String,
    val parent: String? = null,
    val name: String,
    val readable: Boolean,
    val writable: Boolean,
    @SerialName("is_git_repository") val isGitRepository: Boolean,
    val cursor: Int,
    val entries: List<DirectoryEntry>,
    @SerialName("next_cursor") val nextCursor: Int,
    @SerialName("has_more") val hasMore: Boolean,
    val total: Int,
)

@Serializable
data class WorkspaceFilePage(
    val name: String,
    @SerialName("relative_path") val relativePath: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    @SerialName("file_id") val fileId: String,
    val offset: Long,
    @SerialName("next_offset") val nextOffset: Long,
    @SerialName("has_more") val hasMore: Boolean,
    @SerialName("content_base64") val contentBase64: String,
    @SerialName("chunk_sha256") val chunkSha256: String,
    val line: Int? = null,
)

@Serializable
data class RemoteResponse(
    @SerialName("protocol_version") val protocolVersion: Int,
    val kind: String,
    @SerialName("request_id") val requestId: String,
    @SerialName("command_id") val commandId: String,
    val ok: Boolean,
    val body: JsonObject? = null,
    val error: RemoteError? = null,
)

@Serializable
data class RemoteError(
    val code: String,
    val message: String,
    val details: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class FeedbackAttachment(
    @SerialName("attachment_id") val attachmentId: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    val sha256: String,
)

@Serializable
data class FeedbackNote(
    @SerialName("note_id") val noteId: String,
    val author: String,
    val text: String,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class FeedbackActivity(
    @SerialName("activity_id") val activityId: String,
    @SerialName("event_type") val eventType: String,
    val actor: String,
    val status: String = "",
    val text: String = "",
    val version: String = "",
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class FeedbackItem(
    @SerialName("feedback_id") val feedbackId: String,
    val kind: String,
    val description: String,
    val status: String,
    val diagnostics: JsonObject = JsonObject(emptyMap()),
    @SerialName("related_device_id") val relatedDeviceId: String = "",
    val attachments: List<FeedbackAttachment> = emptyList(),
    val notes: List<FeedbackNote> = emptyList(),
    val activity: List<FeedbackActivity> = emptyList(),
    @SerialName("developer_reply") val developerReply: String = "",
    @SerialName("fixed_version") val fixedVersion: String = "",
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
    @SerialName("withdrawn_at") val withdrawnAt: String = "",
)

@Serializable
data class FeedbackResponse(val feedback: FeedbackItem)

@Serializable
data class FeedbackListResponse(
    val items: List<FeedbackItem> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String = "",
    @SerialName("has_more") val hasMore: Boolean = false,
)

@Serializable
data class FeedbackAttachmentPayload(
    @SerialName("attachment_id") val attachmentId: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    val sha256: String,
    val data: String,
)

@Serializable
data class FeedbackAttachmentResponse(val attachment: FeedbackAttachmentPayload)

const val CLIENT_PROTOCOL_VERSION = 1
const val CLIENT_TRANSCRIPT_SCHEMA_VERSION = 1

@Serializable
data class SystemCapabilities(
    @SerialName("daemon_name") val daemonName: String = "pcrd",
    @SerialName("daemon_version") val daemonVersion: String = "",
    @SerialName("protocol_version") val protocolVersion: Int = CLIENT_PROTOCOL_VERSION,
    @SerialName("minimum_client_protocol_version")
    val minimumClientProtocolVersion: Int = CLIENT_PROTOCOL_VERSION,
    @SerialName("supported_protocol_versions")
    val supportedProtocolVersions: List<Int> = listOf(CLIENT_PROTOCOL_VERSION),
    @SerialName("transcript_schema_version")
    val transcriptSchemaVersion: Int = CLIENT_TRANSCRIPT_SCHEMA_VERSION,
    @SerialName("conversation_schema_version")
    val conversationSchemaVersion: Int = CONVERSATION_SCHEMA_VERSION,
    @SerialName("maximum_agent_count")
    val maximumAgentCount: Int = MAX_AGENT_COUNT,
    val transport: String = "relay",
    val platform: String = "",
    val actions: List<String> = emptyList(),
) {
    val compatible: Boolean
        get() = minimumClientProtocolVersion <= CLIENT_PROTOCOL_VERSION &&
            CLIENT_PROTOCOL_VERSION in supportedProtocolVersions &&
            transcriptSchemaVersion <= CLIENT_TRANSCRIPT_SCHEMA_VERSION &&
            conversationSchemaVersion <= CONVERSATION_SCHEMA_VERSION

    fun supports(action: String): Boolean = action in actions

    val clientMaximumAgentCount: Int
        get() = maximumAgentCount.coerceIn(1, MAX_AGENT_COUNT)
}

data class MacEndpoint(
    val device: RelayDevice,
    val workspaces: List<RemoteWorkspace> = emptyList(),
)
