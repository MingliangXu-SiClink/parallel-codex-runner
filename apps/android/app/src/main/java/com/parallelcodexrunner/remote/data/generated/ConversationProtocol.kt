package com.parallelcodexrunner.remote.data.generated

// Generated from remote/protocol/pcr-conversation-v1.schema.json. Do not edit.

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

const val CONVERSATION_SCHEMA_VERSION = 1

@Serializable
enum class WorkOperationKind {
    @SerialName("file_read")
    FILE_READ,
    @SerialName("file_search")
    FILE_SEARCH,
    @SerialName("directory_list")
    DIRECTORY_LIST,
    @SerialName("command")
    COMMAND,
    @SerialName("file_change")
    FILE_CHANGE,
    @SerialName("web_search")
    WEB_SEARCH,
    @SerialName("unknown")
    UNKNOWN;
}

@Serializable
enum class WorkOperationStatus {
    @SerialName("running")
    RUNNING,
    @SerialName("completed")
    COMPLETED,
    @SerialName("failed")
    FAILED,
    @SerialName("cancelled")
    CANCELLED,
    @SerialName("unknown")
    UNKNOWN;
}

@Serializable
enum class WorkFileChangeKind {
    @SerialName("create")
    CREATE,
    @SerialName("update")
    UPDATE,
    @SerialName("delete")
    DELETE,
    @SerialName("rename")
    RENAME,
    @SerialName("unknown")
    UNKNOWN;
}

@Serializable
enum class TurnPhase {
    @SerialName("idle")
    IDLE,
    @SerialName("preparing")
    PREPARING,
    @SerialName("primary_running")
    PRIMARY_RUNNING,
    @SerialName("synthesis_running")
    SYNTHESIS_RUNNING,
    @SerialName("review")
    REVIEW,
    @SerialName("finalizing")
    FINALIZING,
    @SerialName("finalized")
    FINALIZED,
    @SerialName("stopped")
    STOPPED,
    @SerialName("failed")
    FAILED,
    @SerialName("discarded")
    DISCARDED,
    @SerialName("expired")
    EXPIRED,
    @SerialName("cleanup_failed")
    CLEANUP_FAILED,
    @SerialName("sync_ambiguous")
    SYNC_AMBIGUOUS,
    @SerialName("unknown")
    UNKNOWN;
}

@Serializable
enum class CandidateRole {
    @SerialName("candidate")
    CANDIDATE,
    @SerialName("synthesis")
    SYNTHESIS;
}

@Serializable
enum class CandidateStatus {
    @SerialName("queued")
    QUEUED,
    @SerialName("copying")
    COPYING,
    @SerialName("starting")
    STARTING,
    @SerialName("resuming")
    RESUMING,
    @SerialName("submitting")
    SUBMITTING,
    @SerialName("waiting_for_output")
    WAITING_FOR_OUTPUT,
    @SerialName("running")
    RUNNING,
    @SerialName("waiting_for_models")
    WAITING_FOR_MODELS,
    @SerialName("stopping")
    STOPPING,
    @SerialName("success")
    SUCCESS,
    @SerialName("failed")
    FAILED,
    @SerialName("error")
    ERROR,
    @SerialName("killed")
    KILLED,
    @SerialName("stopped")
    STOPPED,
    @SerialName("cancelled")
    CANCELLED,
    @SerialName("canceled")
    CANCELED,
    @SerialName("interrupted")
    INTERRUPTED,
    @SerialName("discarded")
    DISCARDED,
    @SerialName("unknown")
    UNKNOWN;
}

@Serializable
data class WorkOperationTarget(
    val path: String = "",
    val change: WorkFileChangeKind = WorkFileChangeKind.UNKNOWN,
    val added: Int? = null,
    val deleted: Int? = null,
)

@Serializable
data class WorkOperationRecord(
    val id: String = "",
    @SerialName("event_id")
    val eventId: Int = 0,
    val kind: WorkOperationKind = WorkOperationKind.UNKNOWN,
    val status: WorkOperationStatus = WorkOperationStatus.UNKNOWN,
    val command: String? = null,
    val targets: List<WorkOperationTarget> = emptyList(),
    val query: String? = null,
    @SerialName("duration_ms")
    val durationMs: Int? = null,
    @SerialName("duration_source")
    val durationSource: String? = null,
    @SerialName("planned_duration_ms")
    val plannedDurationMs: Int? = null,
    @SerialName("wait_until")
    val waitUntil: String? = null,
    @SerialName("output_bytes")
    val outputBytes: Int? = null,
    @SerialName("exit_code")
    val exitCode: Int? = null,
    @SerialName("details_available")
    val detailsAvailable: Boolean = false,
)

@Serializable
data class WorkOperationDetailPage(
    val text: String = "",
    @SerialName("next_cursor")
    val nextCursor: Int = 0,
    @SerialName("has_more")
    val hasMore: Boolean = false,
    @SerialName("total_bytes")
    val totalBytes: Int = 0,
    val availability: String = "",
)

@Serializable
data class AgentActivityRecord(
    val state: String = "",
    @SerialName("observed_at")
    val observedAt: String? = null,
    @SerialName("last_output_at")
    val lastOutputAt: String? = null,
    @SerialName("wait_until")
    val waitUntil: String? = null,
    @SerialName("active_operation_count")
    val activeOperationCount: Int = 0,
)

@Serializable
data class RunSettingsRecord(
    @SerialName("num_agents")
    val numAgents: Int = 0,
    @SerialName("synthesis_agents")
    val synthesisAgents: Int = 0,
    @SerialName("max_parallel")
    val maxParallel: Int? = null,
    val serial: Boolean = false,
    @SerialName("recommend_by")
    val recommendBy: String = "",
    val model: String? = null,
    val effort: String? = null,
    val fast: Boolean? = null,
    @SerialName("synthesis_model")
    val synthesisModel: String? = null,
    @SerialName("synthesis_effort")
    val synthesisEffort: String? = null,
    @SerialName("synthesis_fast")
    val synthesisFast: Boolean? = null,
    @SerialName("follow_up_delay")
    val followUpDelay: Int = 0,
    val subagents: Boolean = false,
    @SerialName("subagents_limit")
    val subagentsLimit: Int = 0,
    @SerialName("sync_back")
    val syncBack: Boolean = false,
    @SerialName("keep_workspaces")
    val keepWorkspaces: Boolean = false,
    @SerialName("resume_session_id")
    val resumeSessionId: String? = null,
    @SerialName("runs_dir")
    val runsDir: String? = null,
    @SerialName("codex_bin")
    val codexBin: String = "",
)

@Serializable
data class MachineRecord(
    @SerialName("machine_id")
    val machineId: String = "",
    val name: String = "",
    val platform: String = "",
    val online: Boolean = false,
    @SerialName("updated_at")
    val updatedAt: String = "",
)

@Serializable
data class WorkspaceRecord(
    @SerialName("workspace_id")
    val workspaceId: String = "",
    @SerialName("machine_id")
    val machineId: String = "",
    val name: String = "",
    val path: String = "",
    val available: Boolean = false,
    @SerialName("updated_at")
    val updatedAt: String = "",
)

@Serializable
data class ConversationRecord(
    @SerialName("conversation_id")
    val conversationId: String = "",
    @SerialName("workspace_id")
    val workspaceId: String = "",
    val title: String = "",
    @SerialName("custom_title")
    val customTitle: String? = null,
    @SerialName("title_revision")
    val titleRevision: Int? = null,
    @SerialName("created_at")
    val createdAt: String = "",
    @SerialName("updated_at")
    val updatedAt: String = "",
    @SerialName("active_turn_id")
    val activeTurnId: String? = null,
    @SerialName("resume_session_id")
    val resumeSessionId: String? = null,
    @SerialName("turn_ids")
    val turnIds: List<String> = emptyList(),
)

@Serializable
data class TurnRecord(
    @SerialName("turn_id")
    val turnId: String = "",
    @SerialName("conversation_id")
    val conversationId: String = "",
    @SerialName("run_id")
    val runId: String = "",
    @SerialName("turn_index")
    val turnIndex: Int = 0,
    val prompt: String = "",
    val phase: TurnPhase = TurnPhase.UNKNOWN,
    val settings: RunSettingsRecord = RunSettingsRecord(),
    @SerialName("created_at")
    val createdAt: String = "",
    @SerialName("updated_at")
    val updatedAt: String = "",
    @SerialName("continued_from_turn_id")
    val continuedFromTurnId: String? = null,
    @SerialName("continued_from_candidate_id")
    val continuedFromCandidateId: String? = null,
    @SerialName("recommended_candidate_id")
    val recommendedCandidateId: String? = null,
    @SerialName("finalized_candidate_id")
    val finalizedCandidateId: String? = null,
    @SerialName("candidate_ids")
    val candidateIds: List<String> = emptyList(),
)

@Serializable
data class AgentCandidateRecord(
    @SerialName("candidate_id")
    val candidateId: String = "",
    @SerialName("turn_id")
    val turnId: String = "",
    @SerialName("agent_index")
    val agentIndex: Int = 0,
    @SerialName("display_name")
    val displayName: String = "",
    val role: CandidateRole = CandidateRole.CANDIDATE,
    val status: CandidateStatus = CandidateStatus.UNKNOWN,
    val seconds: Double? = null,
    @SerialName("started_at")
    val startedAt: String? = null,
    @SerialName("finished_at")
    val finishedAt: String? = null,
    val activity: AgentActivityRecord? = null,
    @SerialName("reasoning_tokens")
    val reasoningTokens: Long? = null,
    @SerialName("reasoning_token_counts")
    val reasoningTokenCounts: Map<String, Int> = emptyMap(),
    @SerialName("codex_thread_id")
    val codexThreadId: String? = null,
    val error: String? = null,
    val rejected: Boolean = false,
    val stale: Boolean = false,
    val recommended: Boolean = false,
    val finalized: Boolean = false,
    @SerialName("workspace_dir")
    val workspaceDir: String? = null,
    @SerialName("workspace_available")
    val workspaceAvailable: Boolean = false,
    @SerialName("can_accept")
    val canAccept: Boolean = false,
    @SerialName("can_continue")
    val canContinue: Boolean = false,
    @SerialName("can_retry")
    val canRetry: Boolean = false,
)

@Serializable
data class ConversationSnapshot(
    @SerialName("schema_version")
    val schemaVersion: Int = 0,
    val machine: MachineRecord = MachineRecord(),
    val workspace: WorkspaceRecord = WorkspaceRecord(),
    val conversation: ConversationRecord = ConversationRecord(),
    val turn: TurnRecord = TurnRecord(),
    val candidates: List<AgentCandidateRecord> = emptyList(),
)
