package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.isPermanentRemoteCommandRejection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException

enum class ConversationReadPhase { IDLE, LOADING, READY, FAILED }

data class ConversationReadIssue(
    val message: String,
    val code: String? = null,
    val unavailable: Boolean = false,
    val retryAutomatically: Boolean = true,
) {
    fun displayMessage(language: AppLanguage): String = when {
        code == "read_timeout" -> language.text(
            "读取对话超时，请重试。", "Loading the conversation timed out. Try again.",
        )
        unavailable -> language.text(
            "目标设备暂时无法提供这段对话记录。",
            "This conversation record is unavailable on the target device.",
        )
        else -> friendlyError(
            code?.let { RemoteCommandException(it, message) } ?: IllegalStateException(message),
            language,
        )
    }
}

data class ConversationRead(
    val phase: ConversationReadPhase = ConversationReadPhase.IDLE,
    val issue: ConversationReadIssue? = null,
    val requestId: Long = 0,
)

data class ConversationReadState(
    val metadata: ConversationRead = ConversationRead(),
    val history: ConversationRead = ConversationRead(),
    val agents: Map<Int, ConversationRead> = emptyMap(),
)

internal sealed interface ConversationReadTarget {
    data object Metadata : ConversationReadTarget
    data object History : ConversationReadTarget
    data class Agent(val number: Int) : ConversationReadTarget
}

internal fun ConversationReadState.read(target: ConversationReadTarget): ConversationRead = when (target) {
    ConversationReadTarget.Metadata -> metadata
    ConversationReadTarget.History -> history
    is ConversationReadTarget.Agent -> agents[target.number] ?: ConversationRead()
}

private fun ConversationReadState.replacing(
    target: ConversationReadTarget,
    read: ConversationRead,
): ConversationReadState = when (target) {
    ConversationReadTarget.Metadata -> copy(metadata = read)
    ConversationReadTarget.History -> copy(history = read)
    is ConversationReadTarget.Agent -> copy(agents = agents + (target.number to read))
}

// Request state is scoped to the same identity as cached content, but is never persisted
// with it. A failed request cannot become an Agent answer or change a paging cursor.
internal class ConversationReadStateStore(
    private val onChange: (Map<String, ConversationReadState>) -> Unit = {},
) {
    private var nextRequestId = 0L
    private val states = mutableMapOf<WorkspaceRunKey, ConversationReadState>()

    operator fun get(key: WorkspaceRunKey): ConversationReadState = states[key] ?: ConversationReadState()

    suspend fun <T> read(
        key: WorkspaceRunKey,
        target: ConversationReadTarget,
        block: suspend () -> T,
    ): T {
        val previous = get(key).read(target)
        val requestId = ++nextRequestId
        update(key, target, ConversationRead(ConversationReadPhase.LOADING, previous.issue, requestId))
        fun finish(read: ConversationRead) {
            if (get(key).read(target).requestId == requestId) update(key, target, read)
        }
        try {
            val result = block()
            finish(ConversationRead(ConversationReadPhase.READY, requestId = requestId))
            return result
        } catch (error: TimeoutCancellationException) {
            finish(ConversationRead(ConversationReadPhase.FAILED, conversationReadIssue(error), requestId))
            throw error
        } catch (error: CancellationException) {
            finish(previous.takeUnless { it.phase == ConversationReadPhase.LOADING } ?: ConversationRead())
            throw error
        } catch (error: Throwable) {
            finish(ConversationRead(ConversationReadPhase.FAILED, conversationReadIssue(error), requestId))
            throw error
        }
    }

    private fun update(key: WorkspaceRunKey, target: ConversationReadTarget, read: ConversationRead) {
        states[key] = get(key).replacing(target, read)
        onChange(states.mapKeys { it.key.encoded })
    }

    fun removeWhere(predicate: (WorkspaceRunKey) -> Boolean) {
        states.keys.removeAll(predicate)
        onChange(states.mapKeys { it.key.encoded })
    }
}

internal fun conversationReadIssue(error: Throwable): ConversationReadIssue {
    val remote = generateSequence(error as Throwable?) { it.cause }
        .take(8).filterIsInstance<RemoteCommandException>().firstOrNull()
    val message = remote?.message ?: error.message.orEmpty()
    val unavailable = isUnavailableConversationRecord(remote?.code, message)
    return ConversationReadIssue(
        message = message,
        code = if (error is TimeoutCancellationException) "read_timeout" else remote?.code,
        unavailable = unavailable,
        retryAutomatically = !unavailable && (remote == null || !isPermanentRemoteCommandRejection(remote)),
    )
}

internal fun isUnavailableConversationRecord(code: String?, message: String): Boolean =
    code in setOf("run_not_found", "unknown_run", "agent_not_found") ||
        message.trim().lowercase().let {
            it.startsWith("unknown pcr run:") ||
                it.startsWith("run artifacts are not available") ||
                it.startsWith("saved agent output could not be verified") ||
                "is not resumable from" in it
        }

internal fun MainUiState.visibleConversationReads(): ConversationReadState {
    val macId = selectedMacId ?: return ConversationReadState()
    val workspaceId = selectedWorkspaceId ?: return ConversationReadState()
    val runId = visibleRunId ?: return ConversationReadState()
    return conversationReads[WorkspaceRunKey(macId, workspaceId, runId).encoded] ?: ConversationReadState()
}

internal fun MainUiState.visibleConversationReadIssue(): ConversationReadIssue? {
    val reads = visibleConversationReads()
    return reads.metadata.issue ?: reads.agents[selectedAgent]?.issue ?: reads.history.issue
}

internal fun MainUiState.hasVisibleConversationContent(): Boolean =
    runPrompt.isNotBlank() || conversationHistory.isNotEmpty() ||
        agentTranscripts[selectedAgent]?.let {
            it.blocks.isNotEmpty() || it.finalMessage.isNotBlank() || it.error.isNotBlank()
        } == true

internal fun MainUiState.isVisibleConversationReadBlocked(): Boolean = visibleRunId != null &&
    (visibleConversationReads().metadata.issue?.unavailable == true ||
        (!hasVisibleConversationContent() && agentSummaries.isEmpty() &&
            visibleConversationReads().metadata.phase != ConversationReadPhase.READY))

internal fun ConversationReadState.needsHistoryRetry(): Boolean =
    history.phase == ConversationReadPhase.LOADING ||
        (history.phase == ConversationReadPhase.FAILED && history.issue?.retryAutomatically == true)

internal const val CONVERSATION_READ_TIMEOUT_MILLIS = 30_000L
