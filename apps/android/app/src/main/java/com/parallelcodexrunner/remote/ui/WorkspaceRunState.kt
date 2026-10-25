package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import kotlinx.serialization.Serializable

internal data class WorkspaceRunKey(
    val macId: String,
    val workspaceId: String,
    val runId: String,
) {
    init {
        require(macId.isNotBlank()) { "macId must not be blank" }
        require(workspaceId.isNotBlank()) { "workspaceId must not be blank" }
        require(runId.isNotBlank()) { "runId must not be blank" }
    }

    val encoded: String
        get() = "$macId\u001f$workspaceId\u001f$runId"

    fun belongsToMac(value: String): Boolean = macId == value

    fun belongsToWorkspace(mac: String, workspace: String): Boolean =
        macId == mac && workspaceId == workspace

    companion object {
        fun decode(value: String): WorkspaceRunKey? {
            val parts = value.split('\u001f')
            if (parts.size != 3 || parts.any(String::isBlank)) return null
            return WorkspaceRunKey(parts[0], parts[1], parts[2])
        }
    }
}

internal fun resumeConversationCacheKey(
    macId: String,
    workspaceId: String,
    sessionId: String,
): WorkspaceRunKey = WorkspaceRunKey(
    macId = macId,
    workspaceId = workspaceId,
    runId = "resume-session:${sessionId.trim()}",
)

@Serializable
internal data class WorkspaceRunSnapshot(
    val eventCount: Int = 0,
    val conversationId: String = "",
    val conversationTitle: String = "",
    val adoptedSessionId: String = "",
    val recoveredSessionId: String = "",
    val status: String = "",
    val synthesisAgents: Int = 0,
    val canAddAgents: Boolean = false,
    val canUpdateSynthesis: Boolean = false,
    val prompt: String = "",
    val agents: List<AgentSummary> = emptyList(),
    val selectedAgent: Int? = null,
    val recommendedAgent: Int? = null,
    val finalizedAgent: Int? = null,
    val workerActive: Boolean = false,
    val reviewSelectionEstablished: Boolean = false,
    val conversationHistory: List<MobileTranscriptBlock> = emptyList(),
    val agentTranscripts: Map<Int, MobileAgentTranscript> = emptyMap(),
    val agentDiffSummaries: Map<Int, AgentDiffSummary> = emptyMap(),
)

internal fun automaticReviewAgent(
    previous: WorkspaceRunSnapshot,
    terminal: Boolean,
    workerActive: Boolean,
    preferred: Int?,
    finalized: Int?,
): Int? = (finalized ?: preferred)?.takeIf {
    terminal && !workerActive &&
        (!isTerminalRunStatus(previous.status) || !previous.reviewSelectionEstablished)
}

internal fun displayedRunIdForNavigation(
    macId: String,
    workspaceId: String?,
    latestRunId: String?,
    navigation: PendingRunNavigation?,
): String? {
    val requestedRunId = navigation
        ?.takeIf { it.macId == macId && it.workspaceId == workspaceId }
        ?.runId
        ?.takeIf(String::isNotBlank)
    return requestedRunId ?: latestRunId?.takeIf(String::isNotBlank)
}

internal fun latestWorkspaceRunId(
    workspace: RemoteWorkspace?,
    conversationDetached: Boolean = false,
): String? = workspace
    ?.takeUnless { conversationDetached }
    ?.lastRunId
    ?.takeIf(String::isNotBlank)

internal fun isNextViewedConversationTurn(
    viewedRunId: String?,
    latestRunId: String?,
    viewedConversationId: String?,
    latestConversationId: String?,
): Boolean = !viewedRunId.isNullOrBlank() && !latestRunId.isNullOrBlank() &&
    viewedRunId != latestRunId && !viewedConversationId.isNullOrBlank() &&
    viewedConversationId == latestConversationId

internal fun MainUiState.withoutVisibleConversation(): MainUiState = copy(
    visibleRunId = null,
    visibleConversationId = null,
    visibleConversationTitle = "",
    runStatus = "",
    runSynthesisAgents = 0,
    canAddAgents = false,
    canUpdateSynthesis = false,
    runPrompt = "",
    agentSummaries = emptyList(),
    selectedAgent = null,
    pendingAgentSelection = null,
    recommendedAgent = null,
    finalizedAgent = null,
    liveUpdateInterruptedContext = null,
    conversationHistory = emptyList(),
    agentTranscripts = emptyMap(),
    agentDiffSummaries = emptyMap(),
    runActionInProgress = false,
)

internal fun MainUiState.isVisibleRunUpdateInterrupted(): Boolean {
    if (isTerminalRunStatus(runStatus)) return false
    val macId = selectedMacId ?: return false
    val workspaceId = selectedWorkspaceId ?: return false
    val runId = visibleRunId ?: return false
    return liveUpdateInterruptedContext == WorkspaceRunKey(
        macId = macId,
        workspaceId = workspaceId,
        runId = runId,
    ).encoded
}

internal class WorkspaceRunStateStore(
    private val diskCache: WorkspaceRunDiskCache? = null,
) {
    private val snapshots = mutableMapOf<WorkspaceRunKey, WorkspaceRunSnapshot>()

    operator fun get(key: WorkspaceRunKey): WorkspaceRunSnapshot? = snapshots[key]

    suspend fun hydrate(key: WorkspaceRunKey): WorkspaceRunSnapshot? {
        snapshots[key]?.let { return it }
        val cached = diskCache?.load(key) ?: return null
        val current = snapshots[key]
        // Network results always win over an older disk read, even when their
        // event counts match (for example, a newly downloaded final answer).
        if (current != null) return current
        snapshots[key] = cached
        return cached
    }

    suspend fun hydrateConversation(
        macId: String,
        workspaceId: String,
        conversationId: String,
        preferredRunId: String? = null,
    ): CachedWorkspaceConversation? {
        preferredRunId?.takeIf(String::isNotBlank)?.let { runId ->
            val key = WorkspaceRunKey(macId, workspaceId, runId)
            hydrate(key)?.takeIf { it.conversationId == conversationId }?.let { snapshot ->
                return CachedWorkspaceConversation(key, snapshot)
            }
        }
        snapshots.entries
            .asSequence()
            .filter { (key, snapshot) ->
                key.belongsToWorkspace(macId, workspaceId) &&
                    snapshot.conversationId == conversationId
            }
            .maxByOrNull { (key, _) -> key.runId }
            ?.let { (key, snapshot) ->
                return CachedWorkspaceConversation(key, snapshot)
            }
        val cached = diskCache?.loadConversation(macId, workspaceId, conversationId)
            ?: return null
        val current = snapshots[cached.key]
        val snapshot = if (current != null) {
            current
        } else {
            cached.snapshot.also { snapshots[cached.key] = it }
        }
        return CachedWorkspaceConversation(cached.key, snapshot)
    }

    suspend fun hydrateConversationIndex(
        macId: String,
        workspaceId: String,
    ): List<ConversationRecord> = diskCache
        ?.loadConversationIndex(macId, workspaceId)
        .orEmpty()

    suspend fun persistConversationIndex(
        macId: String,
        workspaceId: String,
        conversations: List<ConversationRecord>,
    ) {
        diskCache?.writeConversationIndex(macId, workspaceId, conversations)
    }

    fun put(key: WorkspaceRunKey, snapshot: WorkspaceRunSnapshot): Boolean {
        if (snapshots[key] == snapshot) return false
        snapshots[key] = snapshot
        diskCache?.scheduleWrite(key, snapshot)
        return true
    }

    fun update(
        key: WorkspaceRunKey,
        transform: (WorkspaceRunSnapshot) -> WorkspaceRunSnapshot,
    ): WorkspaceRunSnapshot {
        val current = snapshots[key] ?: WorkspaceRunSnapshot()
        val updated = transform(current)
        if (updated == current) return current
        snapshots[key] = updated
        diskCache?.scheduleWrite(key, updated)
        return updated
    }

    fun capture(key: WorkspaceRunKey, state: MainUiState) {
        val eventCount = snapshots[key]?.eventCount ?: 0
        val snapshot = (snapshots[key] ?: WorkspaceRunSnapshot()).copy(
            eventCount = eventCount,
            conversationId = state.visibleConversationId.orEmpty(),
            conversationTitle = state.visibleConversationTitle,
            status = state.runStatus,
            synthesisAgents = state.runSynthesisAgents,
            canAddAgents = state.canAddAgents,
            canUpdateSynthesis = state.canUpdateSynthesis,
            prompt = state.runPrompt,
            agents = state.agentSummaries,
            selectedAgent = state.selectedAgent,
            recommendedAgent = state.recommendedAgent,
            finalizedAgent = state.finalizedAgent,
            conversationHistory = state.conversationHistory,
            agentTranscripts = state.agentTranscripts,
            agentDiffSummaries = state.agentDiffSummaries,
        )
        if (snapshots[key] == snapshot) return
        snapshots[key] = snapshot
        diskCache?.scheduleWrite(key, snapshot)
    }

    fun removeMac(macId: String) {
        snapshots.keys.removeAll { it.belongsToMac(macId) }
        diskCache?.scheduleRemoveWhere { it.belongsToMac(macId) }
    }

    fun removeWorkspace(macId: String, workspaceId: String) {
        snapshots.keys.removeAll { it.belongsToWorkspace(macId, workspaceId) }
        diskCache?.scheduleRemoveWhere { it.belongsToWorkspace(macId, workspaceId) }
    }

    fun removeWhere(
        predicate: (WorkspaceRunKey) -> Boolean,
        removePersisted: Boolean = false,
    ) {
        snapshots.keys.removeAll(predicate)
        if (removePersisted) diskCache?.scheduleRemoveWhere(predicate)
    }

    fun clear(removePersisted: Boolean = true) {
        snapshots.clear()
        if (removePersisted) diskCache?.scheduleClear()
    }
}

internal fun MainUiState.hasSameRunProjection(snapshot: WorkspaceRunSnapshot): Boolean =
    visibleConversationId.orEmpty() == snapshot.conversationId &&
        visibleConversationTitle == snapshot.conversationTitle &&
        runStatus == snapshot.status &&
        runSynthesisAgents == snapshot.synthesisAgents &&
        canAddAgents == snapshot.canAddAgents &&
        canUpdateSynthesis == snapshot.canUpdateSynthesis &&
        runPrompt == snapshot.prompt &&
        agentSummaries == snapshot.agents &&
        selectedAgent == snapshot.selectedAgent &&
        recommendedAgent == snapshot.recommendedAgent &&
        finalizedAgent == snapshot.finalizedAgent &&
        conversationHistory == snapshot.conversationHistory &&
        agentTranscripts == snapshot.agentTranscripts &&
        agentDiffSummaries == snapshot.agentDiffSummaries

internal fun WorkspaceRunSnapshot.selectedAgentDetailMissing(): Boolean {
    val target = selectedAgent
        ?: recommendedAgent
        ?: agents.firstOrNull { it.status.equals("success", ignoreCase = true) }?.number
        ?: return false
    val summary = agents.firstOrNull { it.number == target } ?: return false
    val shouldHaveAnswer = summary.status.equals("success", ignoreCase = true) ||
        summary.canAccept || summary.canContinue
    if (!shouldHaveAnswer) return false
    val transcript = agentTranscripts[target] ?: return true
    return transcript.blocks.isEmpty() &&
        transcript.finalMessage.isBlank() &&
        transcript.error.isBlank()
}

internal fun MobileAgentTranscript.recoverMissingCachedContent(): MobileAgentTranscript {
    val missingEvents = cursor > 0 && blocks.isEmpty()
    val missingFinalMessage = finalMessageCursor > 0 && finalMessage.isBlank()
    if (!missingEvents && !missingFinalMessage) return this
    return copy(
        cursor = if (missingEvents) 0 else cursor,
        hasMore = if (missingEvents) true else hasMore,
        finalMessageCursor = if (missingFinalMessage) 0 else finalMessageCursor,
        finalMessageHasMore = if (missingFinalMessage) true else finalMessageHasMore,
        finalMessageId = if (missingFinalMessage) "" else finalMessageId,
    )
}

internal fun WorkspaceRunSnapshot.recoverLegacyReadErrors(): WorkspaceRunSnapshot = copy(
    agentTranscripts = agentTranscripts.mapValues { (_, transcript) ->
        val legacyRequestError = !transcript.errorFromAgentResponse &&
            (isUnavailableConversationRecord(null, transcript.error) ||
                transcript.error.trim() in setOf(
                    "这段历史工作过程已经清理，已保留仍可读取的对话内容。",
                    "This historical work log has been cleaned up. Available conversation content is preserved.",
                ))
        if (legacyRequestError) transcript.copy(
            error = "", hasMore = true, finalMessageHasMore = true,
        ) else transcript
    },
)

internal fun mergeConversationHistory(
    current: List<MobileTranscriptBlock>,
    authoritative: List<MobileTranscriptBlock>,
): List<MobileTranscriptBlock> {
    if (authoritative.isEmpty()) return current
    if (current.isEmpty()) return authoritative

    val optimisticConversationCount = current.count {
        it.kind == MobileTranscriptKind.USER || it.kind == MobileTranscriptKind.OUTPUT
    }
    // runs.history is loaded as a complete page sequence before this merge and
    // carries stable rollout identities. Never align it by display text: two
    // legitimate turns may contain the same prompt or answer. If Codex has not
    // flushed the whole optimistic conversation yet, keep the local projection
    // until the next refresh; otherwise replace it atomically with the source of
    // truth and drop attempt-only reasoning and commands from permanent history.
    return if (authoritative.size < optimisticConversationCount) {
        current
    } else {
        authoritative
    }
}

internal fun reconcileTerminalRunAgents(
    runStatus: String,
    agents: List<AgentSummary>,
): List<AgentSummary> {
    if (!isTerminalRunStatus(runStatus)) return agents
    val fallback = if (runStatus.trim().lowercase() in setOf(
            "stopped", "cancelled", "canceled", "interrupted", "discarded", "expired",
        )
    ) {
        "cancelled"
    } else {
        "failed"
    }
    return agents.map { agent ->
        if (isTerminalAgentStatus(agent.status)) agent else agent.copy(status = fallback)
    }
}

internal fun continuationConversation(
    snapshot: WorkspaceRunSnapshot,
    sourceAgent: Int? = null,
): List<MobileTranscriptBlock> {
    val agent = sourceAgent ?: snapshot.finalizedAgent ?: snapshot.selectedAgent
    return acceptedConversation(
        history = snapshot.conversationHistory,
        prompt = snapshot.prompt.takeIf { agent != null }.orEmpty(),
        transcript = agent?.let { snapshot.agentTranscripts[it] ?: MobileAgentTranscript() },
    )
}

internal fun acceptedConversation(
    history: List<MobileTranscriptBlock>,
    prompt: String,
    transcript: MobileAgentTranscript?,
): List<MobileTranscriptBlock> {
    val historicalIds = history.mapTo(mutableSetOf(), MobileTranscriptBlock::id)
    val blocks = if (transcript != null) {
        MobileTranscriptFormatter.visibleBlocks(
            history = history,
            prompt = prompt,
            transcript = transcript,
        )
    } else {
        history
    }
    return blocks
        .filter { block ->
            block.kind == MobileTranscriptKind.USER ||
                block.kind == MobileTranscriptKind.OUTPUT
        }
        .mapIndexed { index, block ->
            block.copy(
                id = if (block.id in historicalIds) {
                    block.id
                } else {
                    "carried-$index-${block.id}"
                },
                isHistorical = true,
                isFinalAnswer = block.kind == MobileTranscriptKind.OUTPUT,
            )
        }
}

internal class PendingStorageActionStore {
    private val actions = mutableMapOf<String, PendingStorageAction>()

    operator fun get(context: RunMutationContext): PendingStorageAction? = actions[context.key]

    fun put(action: PendingStorageAction) {
        actions[action.context.key] = action
    }

    fun remove(context: RunMutationContext): PendingStorageAction? = actions.remove(context.key)

    fun removeWhere(predicate: (WorkspaceRunKey) -> Boolean) {
        actions.keys.removeAll { encoded ->
            WorkspaceRunKey.decode(encoded)?.let(predicate) ?: true
        }
    }

    fun clear() = actions.clear()
}

internal fun MainUiState.projectRun(
    snapshot: WorkspaceRunSnapshot?,
    fallbackStatus: String = "",
    promptDrafts: MutableMap<String, String>? = null,
): MainUiState = copy(
    prompt = if (promptDrafts != null) restoreHydratedConversationDraft(snapshot, promptDrafts) else prompt,
    visibleConversationId = snapshot?.conversationId?.takeIf(String::isNotBlank),
    visibleConversationTitle = snapshot?.conversationTitle.orEmpty(),
    runStatus = snapshot?.status ?: fallbackStatus,
    runSynthesisAgents = snapshot?.synthesisAgents ?: 0,
    canAddAgents = snapshot?.canAddAgents ?: false,
    canUpdateSynthesis = snapshot?.canUpdateSynthesis ?: false,
    runPrompt = snapshot?.prompt.orEmpty(),
    agentSummaries = snapshot?.agents.orEmpty(),
    selectedAgent = snapshot?.selectedAgent,
    recommendedAgent = snapshot?.recommendedAgent,
    finalizedAgent = snapshot?.finalizedAgent,
    conversationHistory = snapshot?.conversationHistory.orEmpty(),
    agentTranscripts = snapshot?.agentTranscripts.orEmpty(),
    agentDiffSummaries = snapshot?.agentDiffSummaries.orEmpty(),
)

internal fun MainUiState.restoreHydratedConversationDraft(
    snapshot: WorkspaceRunSnapshot?,
    drafts: MutableMap<String, String>,
): String {
    val conversationId = snapshot?.conversationId?.takeIf(String::isNotBlank) ?: return prompt
    if (!visibleConversationId.isNullOrBlank()) return prompt
    val runId = visibleRunId
    val macId = selectedMacId ?: return prompt
    val workspaceId = selectedWorkspaceId ?: return prompt
    val unresolvedKey = promptDraftContextKey(
        macId, workspaceId, null, runSettings.resumeSessionId, runId,
    )
    val conversationKey = promptDraftContextKey(
        macId, workspaceId, conversationId, runSettings.resumeSessionId,
    )
    // Cold start learns the run/session before its Conversation ID. Move the
    // draft to that stable identity, including edits made while loading.
    drafts.remove(unresolvedKey)?.let { edited ->
        if (edited.isEmpty()) drafts.remove(conversationKey) else drafts[conversationKey] = edited
        return edited
    }
    return restorePromptDraft(drafts, macId, workspaceId, conversationId, runSettings.resumeSessionId)
}
