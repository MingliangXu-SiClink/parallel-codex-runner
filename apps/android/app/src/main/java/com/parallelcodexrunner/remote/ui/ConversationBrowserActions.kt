package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

private const val CONVERSATION_OPEN_TIMEOUT_MILLIS = 20_000L

internal fun canOpenWorkspaceConversation(
    visibleConversationId: String?,
    openingConversationId: String?,
    requestedConversationId: String,
): Boolean {
    val requested = requestedConversationId.trim()
    return requested.isNotEmpty() &&
        requested != visibleConversationId &&
        requested != openingConversationId
}

fun MainViewModel.refreshWorkspaceConversations(force: Boolean = false) {
    val snapshot = state.value
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    if (!force && snapshot.conversationListLoading &&
        snapshot.conversationListWorkspaceId == workspaceId
    ) return

    val requestId = ++conversationListRequestId
    val cached = domainRepositories.conversations.recordsForWorkspace(workspaceId)
    mutableState.update { current ->
        if (current.selectedMacId == mac.deviceId &&
            current.selectedWorkspaceId == workspaceId
        ) {
            current.copy(
                workspaceConversations = cached,
                conversationListWorkspaceId = workspaceId,
                conversationListLoading = true,
                conversationListError = null,
            )
        } else {
            current
        }
    }
    viewModelScope.launch {
        try {
            if (cached.isEmpty()) {
                val diskCached = try {
                    runStateStore.hydrateConversationIndex(mac.deviceId, workspaceId)
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Throwable) {
                    emptyList()
                }
                if (diskCached.isNotEmpty() && isConversationListRequestCurrent(
                        requestId,
                        mac.deviceId,
                        workspaceId,
                    )
                ) {
                    domainRepositories.conversations.mergeWorkspaceRecords(
                        workspaceId,
                        diskCached,
                    )
                    mutableState.update { current ->
                        if (isConversationListRequestCurrent(
                                requestId,
                                mac.deviceId,
                                workspaceId,
                                current,
                            )
                        ) {
                            current.copy(
                                workspaceConversations = domainRepositories.conversations
                                    .recordsForWorkspace(workspaceId),
                            )
                        } else {
                            current
                        }
                    }
                }
            }
            val fetched = linkedMapOf<String, ConversationRecord>()
            val seenCursors = mutableSetOf<String>()
            var cursor = ""
            while (true) {
                val response = repository.workspaceConversations(
                    mac = mac,
                    workspaceId = workspaceId,
                    cursor = cursor,
                )
                if (!isConversationListRequestCurrent(
                        requestId,
                        mac.deviceId,
                        workspaceId,
                    )
                ) return@launch
                response.conversations.forEach { conversation ->
                    if (conversation.conversationId.isNotBlank()) {
                        fetched[conversation.conversationId] = conversation
                    }
                }
                domainRepositories.conversations.mergeWorkspaceRecords(
                    workspaceId,
                    response.conversations,
                )
                mutableState.update { current ->
                    if (isConversationListRequestCurrent(
                            requestId,
                            mac.deviceId,
                            workspaceId,
                            current,
                        )
                    ) {
                        current.copy(
                            workspaceConversations = domainRepositories.conversations
                                .recordsForWorkspace(workspaceId),
                            conversationListError = null,
                        )
                    } else {
                        current
                    }
                }
                if (!response.hasMore) break
                val nextCursor = response.nextCursor.trim()
                check(nextCursor.isNotEmpty() && nextCursor != cursor && seenCursors.add(nextCursor)) {
                    snapshot.appLanguage.text(
                        "设备返回了不完整的对话列表，请稍后重试。",
                        "The computer returned an incomplete conversation list. Try again shortly.",
                    )
                }
                cursor = nextCursor
            }

            domainRepositories.conversations.replaceWorkspaceRecords(
                workspaceId,
                fetched.values.toList(),
            )
            mutableState.update { current ->
                if (isConversationListRequestCurrent(
                        requestId,
                        mac.deviceId,
                        workspaceId,
                        current,
                    )
                ) {
                    current.copy(
                        workspaceConversations = domainRepositories.conversations
                            .recordsForWorkspace(workspaceId),
                        conversationListLoading = false,
                        conversationListError = null,
                    )
                } else {
                    current
                }
            }
            try {
                runStateStore.persistConversationIndex(
                    mac.deviceId,
                    workspaceId,
                    domainRepositories.conversations.recordsForWorkspace(workspaceId),
                )
            } catch (error: CancellationException) {
                throw error
            } catch (_: Throwable) {
                // The Relay remains authoritative; a cache write must not turn
                // a successfully loaded conversation list into an error.
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (!isConversationListRequestCurrent(requestId, mac.deviceId, workspaceId)) {
                return@launch
            }
            mutableState.update { current ->
                if (isConversationListRequestCurrent(
                        requestId,
                        mac.deviceId,
                        workspaceId,
                        current,
                    )
                ) {
                    current.copy(
                        conversationListLoading = false,
                        conversationListError = friendlyError(error, current.appLanguage),
                    )
                } else {
                    current
                }
            }
        }
    }
}

fun MainViewModel.openWorkspaceConversation(conversationId: String) {
    val normalizedId = conversationId.trim()
    val snapshot = state.value
    if (!canOpenWorkspaceConversation(
            visibleConversationId = snapshot.visibleConversationId,
            openingConversationId = snapshot.conversationOpeningId,
            requestedConversationId = normalizedId,
        )
    ) return
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    val expectedGeneration = contextGeneration
    val requestId = ++conversationOpenRequestId
    mutableState.update { current ->
        if (current.selectedMacId == mac.deviceId &&
            current.selectedWorkspaceId == workspaceId
        ) {
            current.copy(
                conversationOpeningId = normalizedId,
                conversationListError = null,
            )
        } else {
            current
        }
    }

    viewModelScope.launch {
        var activeRequestId = requestId
        var activeGeneration = expectedGeneration
        var openedFromCache = false
        var openedNativeHistory = false
        try {
            val indexedConversation = domainRepositories.conversations.records[normalizedId]
            val preferredTurnId = indexedConversation?.activeTurnId
                ?: indexedConversation?.turnIds?.lastOrNull()
            val preferredRunId = preferredTurnId
                ?.let(domainRepositories.turns.records::get)
                ?.runId
                ?.takeIf(String::isNotBlank)
            val cachedConversation = runStateStore.hydrateConversation(
                macId = mac.deviceId,
                workspaceId = workspaceId,
                conversationId = normalizedId,
                preferredRunId = preferredRunId,
            )
            if (cachedConversation != null && isConversationOpenRequestCurrent(
                    activeRequestId,
                    activeGeneration,
                    mac.deviceId,
                    workspaceId,
                )
            ) {
                val nativeSession = cachedConversation.key.runId
                    .takeIf { it.startsWith("resume-session:") }
                    ?.removePrefix("resume-session:")
                    ?: cachedConversation.snapshot.recoveredSessionId.takeIf(String::isNotBlank)
                if (nativeSession != null) {
                    val nativeKey = resumeConversationCacheKey(mac.deviceId, workspaceId, nativeSession)
                    val native = runStateStore.hydrate(nativeKey)
                    if (!isConversationOpenRequestCurrent(
                            activeRequestId, activeGeneration, mac.deviceId, workspaceId,
                        )) return@launch
                    if (native != null) {
                        installResumeHistory(cachedConversation.key, nativeSession,
                            native.conversationHistory, normalizedId, native.conversationTitle)
                        activeRequestId = conversationOpenRequestId
                        activeGeneration = contextGeneration
                        openedFromCache = true
                        openedNativeHistory = true
                        if (mac.online == false) return@launch
                        loadResumeHistory(mac, workspaceId, nativeSession, contextGeneration)
                    }
                }
                if (!openedNativeHistory) {
                    domainRepositories.conversations.attach(
                        workspaceSettingsKey(mac.deviceId, workspaceId)
                    )
                    prewarmVisibleMarkdown(cachedConversation.snapshot)
                    restoreCachedWorkspaces(
                        mac = mac,
                        preferredWorkspaceId = workspaceId,
                        preferredRunId = cachedConversation.key.runId,
                    )
                    activeRequestId = conversationOpenRequestId
                    activeGeneration = contextGeneration
                    openedFromCache = true
                    mutableState.update { current ->
                        if (current.conversationOpeningId == normalizedId) {
                            current.copy(conversationOpeningId = null)
                        } else {
                            current
                        }
                    }
                    if (mac.online == false) return@launch
                }
            }
            val detail = withTimeout(CONVERSATION_OPEN_TIMEOUT_MILLIS) {
                repository.conversation(mac, normalizedId)
            }
            if (!isConversationOpenRequestCurrent(
                    activeRequestId,
                    activeGeneration,
                    mac.deviceId,
                    workspaceId,
                )
            ) return@launch
            detail.turns.forEach(domainRepositories::ingest)
            domainRepositories.conversations.records[normalizedId] = detail.conversation
            val target = latestConversationSnapshot(detail.conversation, detail.turns)
                ?: error(state.value.appLanguage.text(
                    "这个对话还没有可以打开的内容。",
                    "This conversation does not have a turn that can be opened yet.",
                ))
            val runId = target.turn.runId
            val key = WorkspaceRunKey(mac.deviceId, workspaceId, runId)
            val cached = runStateStore.hydrate(key)
            val recoveredSession = cached?.recoveredSessionId?.takeIf(String::isNotBlank)
            if (recoveredSession != null && adoptedHistorySession(key, detail) == recoveredSession) {
                val native = runStateStore.hydrate(
                    resumeConversationCacheKey(mac.deviceId, workspaceId, recoveredSession))
                if (!isConversationOpenRequestCurrent(
                        activeRequestId, activeGeneration, mac.deviceId, workspaceId,
                    )) return@launch
                if (native != null) {
                    installResumeHistory(key, recoveredSession, native.conversationHistory,
                        normalizedId, detail.conversation.title)
                    if (!openedNativeHistory && mac.online != false) {
                        loadResumeHistory(mac, workspaceId, recoveredSession, contextGeneration)
                    }
                    return@launch
                }
            }
            if (cached == null && mac.online != false) {
                // Only fetch the compact run projection before navigation. Transcript and
                // history paging continue after the target is visible, so a long history can
                // never hold the picker open for minutes.
                try {
                    withTimeout(CONVERSATION_OPEN_TIMEOUT_MILLIS) {
                        loadRun(mac, key)
                    }
                } catch (error: Throwable) {
                    if (!isMissingRun(error, key.runId)) throw error
                    val session = adoptedHistorySession(key, detail) ?: throw error
                    val history = withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) {
                        readCompleteResumeHistory { cursor ->
                            if (!isConversationOpenRequestCurrent(
                                    activeRequestId, activeGeneration, mac.deviceId, workspaceId,
                                )) throw CancellationException("Conversation changed")
                            repository.resumeHistory(mac, workspaceId, session, cursor)
                        }
                    }
                    if (!isConversationOpenRequestCurrent(
                            activeRequestId, activeGeneration, mac.deviceId, workspaceId,
                        )) return@launch
                    installResumeHistory(key, session, history, normalizedId, detail.conversation.title)
                    return@launch
                }
                if (!runStateStore[key]?.recoveredSessionId.isNullOrBlank()) return@launch
            } else if (cached == null) {
                error(state.value.appLanguage.text(
                    "这台设备离线，并且本机还没有缓存这个对话。",
                    "This computer is offline and this conversation is not cached yet.",
                ))
            }
            if (!isConversationOpenRequestCurrent(
                    activeRequestId,
                    activeGeneration,
                    mac.deviceId,
                    workspaceId,
                )
            ) return@launch

            pendingRunNavigation = PendingRunNavigation(mac.deviceId, workspaceId, runId)
            mutableState.update { current ->
                if (current.conversationOpeningId == normalizedId) {
                    current.copy(conversationOpeningId = null)
                } else {
                    current
                }
            }
            loadWorkspaces(
                mac = mac,
                expectedGeneration = activeGeneration,
                preferredWorkspaceId = workspaceId,
                forceVisibleRunRefresh = true,
            )
        } catch (error: TimeoutCancellationException) {
            if (!isConversationOpenRequestCurrent(
                    activeRequestId,
                    activeGeneration,
                    mac.deviceId,
                    workspaceId,
                )
            ) return@launch
            if (openedFromCache) return@launch
            mutableState.update { current ->
                if (current.conversationOpeningId == normalizedId) {
                    current.copy(
                        conversationOpeningId = null,
                        conversationListError = current.appLanguage.text(
                            "设备响应较慢，请确认连接后重试。",
                            "The computer is taking too long to respond. Check the connection and try again.",
                        ),
                    )
                } else {
                    current
                }
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (!isConversationOpenRequestCurrent(
                    activeRequestId,
                    activeGeneration,
                    mac.deviceId,
                    workspaceId,
                )
            ) return@launch
            if (openedFromCache) return@launch
            mutableState.update { current ->
                if (current.conversationOpeningId == normalizedId) {
                    current.copy(
                        conversationOpeningId = null,
                        conversationListError = friendlyError(error, current.appLanguage),
                    )
                } else {
                    current
                }
            }
        }
    }
}

fun MainViewModel.cancelConversationOpen() {
    conversationOpenRequestId += 1
    mutableState.update { current ->
        if (current.conversationOpeningId == null) current
        else current.copy(conversationOpeningId = null)
    }
}

fun MainViewModel.newWorkspaceConversation() {
    // Invalidate any historical conversation still loading before detaching the draft.
    cancelConversationOpen()
    setResumeSessionId("NO")
}

internal fun latestConversationSnapshot(
    conversation: ConversationRecord,
    turns: List<ConversationSnapshot>,
): ConversationSnapshot? {
    val available = turns.filter { it.turn.runId.isNotBlank() }
    conversation.activeTurnId?.let { activeTurnId ->
        available.firstOrNull { it.turn.turnId == activeTurnId }?.let { return it }
    }
    return available.maxWithOrNull(
        compareBy<ConversationSnapshot> { it.turn.turnIndex }
            .thenBy { it.turn.updatedAt }
            .thenBy { it.turn.runId }
    )
}

private fun MainViewModel.isConversationListRequestCurrent(
    requestId: Int,
    macId: String,
    workspaceId: String,
    current: MainUiState = state.value,
): Boolean = conversationListRequestId == requestId &&
    current.selectedMacId == macId &&
    current.selectedWorkspaceId == workspaceId &&
    current.conversationListWorkspaceId == workspaceId

private fun MainViewModel.isConversationOpenRequestCurrent(
    requestId: Int,
    expectedGeneration: Int,
    macId: String,
    workspaceId: String,
): Boolean = conversationOpenRequestId == requestId &&
    contextGeneration == expectedGeneration &&
    state.value.selectedMacId == macId &&
    state.value.selectedWorkspaceId == workspaceId
