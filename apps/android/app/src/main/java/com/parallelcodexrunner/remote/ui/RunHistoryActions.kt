package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

internal fun RemoteRunSettings.forConversationHistory(
    runId: String?,
    adoptedSessionId: String,
): RemoteRunSettings = if (runId == null && !isNoResumeSession(adoptedSessionId)) {
    // Run defaults describe its input. The registry's adopted session contains
    // the completed conversation, including the final reply.
    copy(resumeSessionId = adoptedSessionId.trim())
} else this

internal fun MainUiState.conversationSourceChanged(runId: String?, sessionId: String): Boolean =
    visibleRunId != runId || (runId == null && runSettings.resumeSessionId != sessionId)

internal fun MainUiState.isViewingResumeHistory(
    macId: String,
    workspaceId: String,
    sessionId: String,
): Boolean = selectedMacId == macId && selectedWorkspaceId == workspaceId &&
    visibleRunId == null && runSettings.resumeSessionId == sessionId

internal fun MainViewModel.loadResumeHistory(
    mac: RelayDevice,
    workspaceId: String,
    sessionId: String,
    expectedGeneration: Int,
) {
    val requestId = ++resumeHistoryRequestId
    viewModelScope.launch {
        val cacheKey = resumeConversationCacheKey(mac.deviceId, workspaceId, sessionId)
        fun isCurrent() = requestId == resumeHistoryRequestId &&
            expectedGeneration == contextGeneration &&
            state.value.isViewingResumeHistory(mac.deviceId, workspaceId, sessionId)
        try {
            runStateStore.hydrate(cacheKey)?.let { cached ->
                if (isCurrent()) {
                    mutableState.update { current ->
                        current.copy(
                            prompt = current.restoreHydratedConversationDraft(cached, promptDraftsByContext),
                            visibleConversationId = cached.conversationId.takeIf(String::isNotBlank)
                                ?: current.visibleConversationId,
                            visibleConversationTitle = cached.conversationTitle.ifBlank {
                                current.visibleConversationTitle
                            },
                            conversationHistory = mergeConversationHistory(
                                current.conversationHistory,
                                cached.conversationHistory,
                            ),
                        )
                    }
                }
            }
            val blocks = withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) {
                readCompleteResumeHistory { cursor ->
                    if (!isCurrent()) throw CancellationException("Conversation changed")
                    repository.resumeHistory(mac, workspaceId, sessionId, cursor)
                }
            }
            if (!isCurrent()) return@launch
            val merged = mergeConversationHistory(state.value.conversationHistory, blocks)
            mutableState.update { it.copy(conversationHistory = merged) }
            runStateStore.update(cacheKey) { it.copy(conversationHistory = merged) }
        } catch (error: TimeoutCancellationException) {
            if (isCurrent()) showError(state.value.appLanguage.text(
                "设备响应较慢，已保留当前内容，请稍后重试。",
                "The computer is taking too long to respond. Your current content is kept. Try again shortly.",
            ))
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (isCurrent()) {
                val language = state.value.appLanguage
                showError(
                    if (isUnavailableConversationHistory(error)) {
                        friendlyError(error, language)
                    } else {
                        language.text(
                            "无法加载之前的对话：",
                            "The earlier conversation could not be loaded: ",
                        ) + friendlyError(error, language)
                    },
                )
            }
        }
    }
}

internal fun MainViewModel.hydrateResumeConversationCache(
    macId: String,
    workspaceId: String,
    sessionId: String,
) {
    val key = resumeConversationCacheKey(macId, workspaceId, sessionId)
    val generation = contextGeneration
    viewModelScope.launch {
        val cached = runStateStore.hydrate(key) ?: return@launch
        val current = state.value
        if (generation != contextGeneration ||
            !current.isViewingResumeHistory(macId, workspaceId, sessionId)) return@launch
        mutableState.update { visible ->
            if (visible.isViewingResumeHistory(macId, workspaceId, sessionId)) {
                visible.copy(
                    prompt = visible.restoreHydratedConversationDraft(cached, promptDraftsByContext),
                    visibleConversationId = cached.conversationId.takeIf(String::isNotBlank)
                        ?: visible.visibleConversationId,
                    visibleConversationTitle = cached.conversationTitle.ifBlank {
                        visible.visibleConversationTitle
                    },
                    conversationHistory = mergeConversationHistory(
                        visible.conversationHistory,
                        cached.conversationHistory,
                    ),
                )
            } else {
                visible
            }
        }
    }
}

internal fun MainViewModel.loadRunHistory(
    mac: RelayDevice,
    key: WorkspaceRunKey,
) {
    val requestId = ++nextConversationRequestId
    runHistoryRequestIds[key] = requestId
    viewModelScope.launch {
        try {
            conversationReadStore.read(key, ConversationReadTarget.History) {
                loadRunHistoryPages(mac, key, requestId)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            // The scoped read state keeps the error and retry action beside this
            // conversation, including when no history has been cached yet.
        }
    }
}

internal suspend fun MainViewModel.preloadRunHistory(
    mac: RelayDevice,
    key: WorkspaceRunKey,
) {
    if (key in loadedRunHistories) return
    val requestId = ++nextConversationRequestId
    runHistoryRequestIds[key] = requestId
    conversationReadStore.read(key, ConversationReadTarget.History) {
        loadRunHistoryPages(mac, key, requestId)
    }
}

private suspend fun MainViewModel.loadRunHistoryPages(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    requestId: Int,
) {
    val entries = mutableListOf<ResumeHistoryEntry>()
    val blocks = mutableListOf<MobileTranscriptBlock>()
    var cursor = 0
    do {
        val page = withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) {
            repository.runHistory(mac = mac, runId = key.runId, cursor = cursor)
        }
        if (runHistoryRequestIds[key] != requestId) return
        val startingIndex = entries.size
        entries += page.entries
        blocks += MobileTranscriptFormatter.historyBlocks(
            page.entries,
            startingIndex = startingIndex,
        )
        if (runHistoryRequestIds[key] != requestId) return
        val updated = runStateStore.update(key) { current ->
            current.copy(
                conversationHistory = mergeConversationHistory(
                    current.conversationHistory,
                    blocks,
                ),
            )
        }
        if (isVisibleRun(key)) {
            mutableState.update { current -> current.projectRun(updated) }
        }
        val previousCursor = cursor
        cursor = page.nextCursor
        if (!page.hasMore || cursor <= previousCursor) break
    } while (true)
    if (runHistoryRequestIds[key] == requestId) loadedRunHistories += key
}
