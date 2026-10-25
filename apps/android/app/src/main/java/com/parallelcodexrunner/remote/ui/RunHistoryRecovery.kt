package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.ConversationDetailResponse
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.ResumeHistoryPage
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.TurnPhase
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout

internal fun isMissingRun(error: Throwable, runId: String): Boolean {
    val remote = generateSequence(error as Throwable?) { it.cause }
        .take(8).filterIsInstance<RemoteCommandException>().firstOrNull() ?: return false
    return remote.code in setOf("run_not_found", "unknown_run") ||
        remote.message?.trim() == "Unknown PCR run: $runId"
}

internal fun adoptedHistorySession(
    key: WorkspaceRunKey,
    detail: ConversationDetailResponse,
): String? {
    if (detail.conversation.workspaceId != key.workspaceId) return null
    val turn = detail.turns.firstOrNull { it.turn.runId == key.runId }?.turn ?: return null
    if (turn.conversationId != detail.conversation.conversationId ||
        turn.turnId != detail.conversation.activeTurnId || turn.phase != TurnPhase.FINALIZED
    ) return null
    return detail.conversation.resumeSessionId?.trim()?.takeUnless(::isNoResumeSession)
}

internal suspend fun readCompleteResumeHistory(
    readPage: suspend (Int) -> ResumeHistoryPage,
): List<MobileTranscriptBlock> {
    val blocks = mutableListOf<MobileTranscriptBlock>()
    var cursor = 0
    var entryCount = 0
    do {
        currentCoroutineContext().ensureActive()
        val page = readPage(cursor)
        check(page.cursor == cursor && (!page.hasMore || page.nextCursor > cursor)) {
            "The computer returned an incomplete conversation history"
        }
        blocks += MobileTranscriptFormatter.historyBlocks(page.entries, startingIndex = entryCount)
        entryCount += page.entries.size
        cursor = page.nextCursor
    } while (page.hasMore)
    return blocks
}

internal fun MainUiState.resumeHistoryProjection(
    key: WorkspaceRunKey,
    sessionId: String,
    history: List<MobileTranscriptBlock>,
    conversationId: String? = visibleConversationId,
    title: String = visibleConversationTitle,
    draft: String = prompt,
): MainUiState {
    if (selectedMacId != key.macId || selectedWorkspaceId != key.workspaceId) return this
    return withoutVisibleConversation().copy(
        visibleConversationId = conversationId?.takeIf(String::isNotBlank),
        visibleConversationTitle = title,
        conversationHistory = history,
        prompt = draft,
        runSettings = runSettings.copy(resumeSessionId = sessionId),
        conversationOpeningId = null,
        conversationListError = null,
    )
}

internal suspend fun MainViewModel.recoverMissingRunHistory(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    failure: Throwable,
): Boolean = runHistoryRecoveryMutex.withLock {
    if (!isMissingRun(failure, key.runId) || !isVisibleRun(key)) return@withLock false
    val generation = contextGeneration
    val runRequestId = runRequestIds[key]
    val cached = runStateStore[key]
    var recoveredConversation: ConversationRecord? = null
    fun isCurrent() = generation == contextGeneration && isVisibleRun(key) &&
        runRequestId == runRequestIds[key]
    val (sessionId, history) = conversationReadStore.read(key, ConversationReadTarget.Metadata) {
        withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) {
            var session = cached?.recoveredSessionId?.takeIf(String::isNotBlank)
            val inspected = mutableSetOf<String>()
            suspend fun inspect(conversationId: String) {
                if (session != null || conversationId.isBlank() || !inspected.add(conversationId)) return
                if (!isCurrent()) throw CancellationException("Conversation changed")
                val detail = try {
                    repository.conversation(mac, conversationId)
                } catch (error: RemoteCommandException) {
                    if (error.message?.trim() == "Unknown conversation_id: $conversationId") return
                    throw error
                }
                session = adoptedHistorySession(key, detail)
                if (session != null) recoveredConversation = detail.conversation
            }
            inspect(cached?.conversationId.orEmpty())
            // Legacy caches may predate Conversation IDs. Resolve by the actual
            // run in server-owned Turn records, never by title or workspace defaults.
            var cursor = ""
            val cursors = mutableSetOf<String>()
            while (session == null) {
                val page = repository.workspaceConversations(mac, key.workspaceId, cursor)
                for (record in page.conversations) {
                    if (record.workspaceId == key.workspaceId) inspect(record.conversationId)
                    if (session != null) break
                }
                if (session != null || !page.hasMore) break
                check(page.nextCursor.isNotBlank() && cursors.add(page.nextCursor)) {
                    "The computer returned an incomplete conversation list"
                }
                cursor = page.nextCursor
            }
            val source = session ?: cached?.adoptedSessionId?.takeIf(String::isNotBlank) ?: throw failure
            val content = readCompleteResumeHistory { offset ->
                if (!isCurrent()) throw CancellationException("Conversation changed")
                repository.resumeHistory(mac, key.workspaceId, source, offset)
            }
            source to content
        }
    }
    if (!isCurrent()) return@withLock false
    installResumeHistory(
        key, sessionId, history,
        recoveredConversation?.conversationId ?: state.value.visibleConversationId.orEmpty(),
        recoveredConversation?.title ?: state.value.visibleConversationTitle,
    )
    true
}

internal fun MainViewModel.installResumeHistory(
    key: WorkspaceRunKey,
    sessionId: String,
    history: List<MobileTranscriptBlock>,
    conversationId: String,
    title: String,
) {
    // Switch only after every page is readable. A network/archival failure leaves
    // the old content, draft and navigation untouched and reports the real error.
    captureVisibleRunState()
    rememberPromptDraft()
    val before = state.value
    val sameConversation = before.visibleRunId == key.runId ||
        (conversationId.isNotBlank() && before.visibleConversationId == conversationId) ||
        before.isViewingResumeHistory(key.macId, key.workspaceId, sessionId)
    if (!sameConversation) advanceContext(retainStoredFollowUps = true)
    val nativeKey = resumeConversationCacheKey(key.macId, key.workspaceId, sessionId)
    runStateStore.put(nativeKey, WorkspaceRunSnapshot(
        conversationId = conversationId,
        conversationTitle = title,
        conversationHistory = history,
    ))
    if (key != nativeKey) {
        runStateStore.update(key) { it.copy(
            conversationId = conversationId,
            conversationTitle = title,
            recoveredSessionId = sessionId,
        ) }
    }
    domainRepositories.conversations.detach(workspaceSettingsKey(key.macId, key.workspaceId), sessionId)
    // Reads already waiting on disk/network must not restore the expired run
    // after this atomic navigation has selected native history.
    workspacesRequestId += 1
    runRequestIds.remove(key)
    resumeHistoryRequestId += 1
    runHistoryRequestIds.remove(key)
    agentRequestIds.keys.removeAll { it.first == key }
    val draft = if (sameConversation) before.restoreHydratedConversationDraft(
        WorkspaceRunSnapshot(conversationId = conversationId), promptDraftsByContext,
    ) else restorePromptDraft(
        key.macId, key.workspaceId, conversationId, sessionId, key.runId,
    )
    mutableState.update { current ->
        current.resumeHistoryProjection(key, sessionId, history, conversationId, title, draft)
    }
    rememberPromptDraft()
    scheduleMobileStateSave()
}
