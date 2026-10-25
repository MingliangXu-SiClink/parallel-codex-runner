package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull

internal fun MainViewModel.beginPolling(runId: String) {
    val context = currentRunKey(runId) ?: return
    beginPolling(context.first, context.second)
}

internal fun MainViewModel.beginPolling(mac: RelayDevice, key: WorkspaceRunKey) {
    if (runPollingByContext[key]?.isActive == true) return
    if (!isVisibleRun(key) && !runStateStore[key]?.recoveredSessionId.isNullOrBlank()) return
    val pollingJob = viewModelScope.launch {
        var consecutiveFailures = 0
        var consecutiveConnectionFailures = 0
        while (true) {
            var loaded = false
            try {
                // Keep every active Workspace registered for polling while bounding
                // only concurrent network requests. Dropping the seventh context
                // left that Workspace permanently stale until the user opened it.
                runPollingPermits.withPermit {
                    loadRun(latestPollingDevice(state.value.macs, mac), key)
                }
                loaded = true
                if (!runStateStore[key]?.recoveredSessionId.isNullOrBlank()) break
                clearLiveUpdateInterrupted(key)
                consecutiveFailures = 0
                consecutiveConnectionFailures = 0
            } catch (error: CancellationException) {
                if (error !is TimeoutCancellationException) throw error
                currentCoroutineContext().ensureActive()
                consecutiveFailures += 1
            } catch (error: Throwable) {
                consecutiveFailures += 1
                if (isLiveUpdateConnectionFailure(error)) {
                    consecutiveConnectionFailures += 1
                } else {
                    consecutiveConnectionFailures = 0
                    clearLiveUpdateInterrupted(key)
                }
                if (isVisibleRun(key) && consecutiveConnectionFailures >= 3) {
                    mutableState.update { current ->
                        if (current.selectedMacId == key.macId &&
                            current.selectedWorkspaceId == key.workspaceId &&
                            current.visibleRunId == key.runId &&
                            current.liveUpdateInterruptedContext != key.encoded
                        ) {
                            current.copy(liveUpdateInterruptedContext = key.encoded)
                        } else {
                            current
                        }
                    }
                }
                val reads = conversationReadStore[key]
                val issue = reads.metadata.issue ?: reads.agents[runStateStore[key]?.selectedAgent]?.issue
                if (issue?.retryAutomatically == false) break
            }
            if (loaded && runStateStore[key]?.let { snapshot ->
                    isConversationPollingComplete(snapshot, conversationReadStore[key])
                } == true
            ) {
                break
            }
            val retryDelay = if (consecutiveFailures == 0) {
                if (isVisibleRun(key)) 2_000L else 5_000L
            } else {
                minOf(
                    10_000L,
                    1_000L shl (consecutiveFailures - 1).coerceAtMost(3),
                )
            }
            delay(retryDelay)
        }
    }
    runPollingByContext[key] = pollingJob
    pollingJob.invokeOnCompletion {
        runPollingByContext.remove(key, pollingJob)
    }
}

internal suspend fun MainViewModel.loadRun(runId: String) {
    val context = currentRunKey(runId) ?: return
    loadRun(context.first, context.second)
}

internal suspend fun MainViewModel.loadRun(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    preloadSelectedAgent: Boolean = false,
) {
    val requestId = ++nextConversationRequestId
    runRequestIds[key] = requestId
    runStateStore.hydrate(key)?.let { cached ->
        cached.recoveredSessionId.takeIf(String::isNotBlank)?.let { session ->
            val history = runStateStore.hydrate(
                resumeConversationCacheKey(key.macId, key.workspaceId, session))
            if (history != null && runRequestIds[key] == requestId && isVisibleRun(key)) {
                installResumeHistory(key, session, history.conversationHistory,
                    history.conversationId, history.conversationTitle)
                loadResumeHistory(mac, key.workspaceId, session, contextGeneration)
                return
            }
        }
        if (runRequestIds[key] == requestId && isVisibleRun(key)) {
            prewarmVisibleMarkdown(cached)
            awaitFirstFrameDrawn()
            if (runRequestIds[key] != requestId || !isVisibleRun(key)) return@let
            val latest = runStateStore[key] ?: cached
            mutableState.update { current ->
                if (current.hasSameRunProjection(latest)) current
                else current.projectRun(latest, promptDrafts = promptDraftsByContext)
            }
            scheduleMobileStateSave()
        }
    }
    val (run, conversationSnapshot) = try {
        conversationReadStore.read(key, ConversationReadTarget.Metadata) {
            val response = withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) { repository.run(mac, key.runId) }
            response to repository.conversationSnapshot(response)
        }
    } catch (error: Throwable) {
        if (runRequestIds[key] == requestId && recoverMissingRunHistory(mac, key, error)) return
        throw error
    }
    if (runRequestIds[key] != requestId) return
    conversationSnapshot?.let(domainRepositories::ingest)
    val protocolCandidates = conversationSnapshot?.candidates.orEmpty()
    val rawAgents = if (protocolCandidates.isNotEmpty()) {
        protocolCandidates.map { candidate ->
            AgentSummary(
                number = candidate.agentIndex,
                status = candidate.status.name.lowercase(),
                reasoningTokens = candidate.reasoningTokens,
                seconds = candidate.seconds,
                recommended = candidate.recommended,
                rejected = candidate.rejected,
                workspaceAvailable = candidate.workspaceAvailable,
                canAccept = candidate.canAccept,
                canContinue = candidate.canContinue,
                canRetry = candidate.canRetry,
                reasoningTokenCounts = candidate.reasoningTokenCounts.mapNotNull {
                        (rawStep, count) ->
                    val step = rawStep.toLongOrNull() ?: return@mapNotNull null
                    if (step > 0 && count > 0) step to count else null
                }.toMap(),
                role = candidate.role.name.lowercase(),
            )
        }
    } else {
        run["agents"]?.jsonArray.orEmpty().mapNotNull { raw ->
            val item = raw.jsonObject
            val number = item["agent"]?.jsonPrimitive?.intOrNull
                ?: item["idx"]?.jsonPrimitive?.intOrNull
                ?: return@mapNotNull null
            AgentSummary(
                number,
                item["status"]?.jsonPrimitive?.content ?: "unknown",
                item["reasoning_tokens"]?.jsonPrimitive?.content?.toLongOrNull(),
                item["seconds"]?.jsonPrimitive?.doubleOrNull,
                item["recommended"]?.jsonPrimitive?.booleanOrNull == true,
                item["rejected"]?.jsonPrimitive?.booleanOrNull == true,
                item["workspace_available"]?.jsonPrimitive?.booleanOrNull == true,
                item["can_accept"]?.jsonPrimitive?.booleanOrNull == true,
                item["can_continue"]?.jsonPrimitive?.booleanOrNull == true,
                item["can_retry"]?.jsonPrimitive?.booleanOrNull == true,
                reasoningTokenCounts = item["reasoning_token_counts"]
                    ?.jsonObject
                    ?.mapNotNull { (rawStep, rawCount) ->
                        val step = rawStep.toLongOrNull() ?: return@mapNotNull null
                        val count = rawCount.jsonPrimitive.intOrNull ?: return@mapNotNull null
                        if (step > 0 && count > 0) step to count else null
                    }
                    ?.toMap()
                    .orEmpty(),
                role = item["role"]?.jsonPrimitive?.contentOrNull ?: "candidate",
            )
        }
    }
    val recommended = conversationSnapshot?.turn?.recommendedCandidateId
        ?.let { id -> protocolCandidates.firstOrNull { it.candidateId == id }?.agentIndex }
        ?: run["recommended_agent"]?.jsonPrimitive?.intOrNull
    val finalized = conversationSnapshot?.turn?.finalizedCandidateId
        ?.let { id -> protocolCandidates.firstOrNull { it.candidateId == id }?.agentIndex }
        ?: run["finalized_agent"]?.jsonPrimitive?.intOrNull
    val status = run["status"]?.jsonPrimitive?.content ?: "unknown"
    val runSynthesisAgents = conversationSnapshot?.turn?.settings?.synthesisAgents
        ?: run["config"]?.jsonObject
            ?.get("synthesis_agents")?.jsonPrimitive?.intOrNull
        ?: 0
    val canUpdateSynthesis =
        run["can_update_synthesis"]?.jsonPrimitive?.booleanOrNull == true
    val canAddAgents = run["can_add_agents"]?.jsonPrimitive?.booleanOrNull == true
    val terminal = isTerminalRunStatus(status)
    val workerActive = run["worker_active"]?.jsonPrimitive?.booleanOrNull ?: !terminal
    val agents = reconcileTerminalRunAgents(status, rawAgents)
    resetViewedCompletionsForActiveAgents(key, agents)
    val workspaceName = cachedWorkspacesByMac[key.macId]
        .orEmpty()
        .firstOrNull { it.workspaceId == key.workspaceId }
        ?.name
        .orEmpty()
    if (!terminal) {
        scheduleCompletionMonitor(
            macId = key.macId,
            workspaceId = key.workspaceId,
            workspaceName = workspaceName,
            runId = key.runId,
        )
    } else {
        scheduledCompletionRuns.remove(
            completionMonitorKey(key.macId, key.workspaceId, key.runId)
        )
    }
    val previous = runStateStore[key] ?: WorkspaceRunSnapshot()
    val eventCount = run["event_count"]?.jsonPrimitive?.intOrNull ?: previous.eventCount
    // requestId already rejects out-of-order responses. The server may rebuild a
    // completed run's derived event stream with fewer normalized items, so a
    // larger cached count must not keep an otherwise valid run permanently stale.
    val wasTerminal = isTerminalRunStatus(previous.status)
    val rememberedSelected = selectedAgentsByContext[key.encoded]
        ?.takeIf { selected -> agents.any { it.number == selected } }
    val previousSelected = previous.selectedAgent?.takeIf { selected ->
        agents.any { it.number == selected }
    }
    val observedTransition = previous.status.isNotBlank() && !wasTerminal
    if (terminal && observedTransition) {
        markRunCompletionUnread(key)
    }
    val stableSelected = rememberedSelected ?: previousSelected
    val continuation = run["continuation_agent"]?.jsonPrimitive?.intOrNull ?: recommended
    val automaticTarget = automaticReviewAgent(previous, terminal, workerActive, continuation, finalized)
    val deferAutomaticSelection = automaticTarget != null &&
        stableSelected != null &&
        stableSelected != automaticTarget &&
        !previous.agentTranscripts.containsKey(automaticTarget)
    val selected = if (automaticTarget != null && !deferAutomaticSelection) {
        automaticTarget
    } else {
        stableSelected
            ?: recommended?.takeIf { terminal }
            ?: agents.firstOrNull { isActiveAgentStatus(it.status) }?.number
            ?: agents.firstOrNull()?.number
    }
    selected?.let { selectedAgentsByContext[key.encoded] = it }
    if (selected != null) scheduleMobileStateSave()
    val updated = previous.copy(
        adoptedSessionId = (run["session_promotion"] as? JsonObject)
            ?.get("session_id")?.jsonPrimitive?.contentOrNull
            ?.takeUnless(::isNoResumeSession) ?: previous.adoptedSessionId,
        recoveredSessionId = "",
        eventCount = eventCount,
        conversationId = conversationSnapshot?.conversation?.conversationId
            ?.takeIf(String::isNotBlank)
            ?: previous.conversationId,
        conversationTitle = conversationSnapshot?.conversation?.title
            ?.takeIf(String::isNotBlank)
            ?: previous.conversationTitle,
        status = status,
        synthesisAgents = runSynthesisAgents,
        canAddAgents = canAddAgents,
        canUpdateSynthesis = canUpdateSynthesis,
        prompt = conversationSnapshot?.turn?.prompt
            ?: run["prompt"]?.jsonPrimitive?.content
            ?: previous.prompt,
        agents = agents,
        selectedAgent = selected,
        recommendedAgent = recommended,
        finalizedAgent = finalized,
        workerActive = workerActive,
        reviewSelectionEstablished = terminal && !workerActive,
        agentDiffSummaries = previous.agentDiffSummaries.filterKeys { number ->
            agents.firstOrNull { it.number == number }
                ?.status
                ?.let(::isTerminalAgentStatus) == true
        },
    )
    runStateStore.put(key, updated)
    updateWorkspaceRunSummary(key, status, workerActive, agents)
    if (!isVisibleRun(key)) {
        val workspaceKey = workspaceSettingsKey(key.macId, key.workspaceId)
        val viewedRunId = domainRepositories.conversations.viewedRunIdsByWorkspace[workspaceKey]
        val latestRunId = cachedWorkspacesByMac[key.macId].orEmpty()
            .firstOrNull { it.workspaceId == key.workspaceId }?.lastRunId
        val viewed = viewedRunId?.let {
            runStateStore.hydrate(WorkspaceRunKey(key.macId, key.workspaceId, it))
        }
        val advancesViewedConversation = key.runId == latestRunId &&
            isNextViewedConversationTurn(
                viewedRunId, latestRunId, viewed?.conversationId, updated.conversationId,
            )
        if (advancesViewedConversation) {
            // Load the next turn before changing the visible conversation, including
            // continuations started by the daemon while this client was closed.
            loadRunHistory(mac, key)
            selected?.let { loadRunAgentDetail(mac, key, it, terminal, maxPages = 4) }
            if (runRequestIds[key] == requestId &&
                domainRepositories.conversations.viewedRunIdsByWorkspace[workspaceKey] == viewedRunId &&
                cachedWorkspacesByMac[key.macId].orEmpty()
                    .firstOrNull { it.workspaceId == key.workspaceId }?.lastRunId == key.runId
            ) {
                domainRepositories.conversations.advanceViewedRun(
                    workspaceKey, checkNotNull(viewedRunId), key.runId,
                )
                scheduleMobileStateSave()
                if (state.value.selectedMacId == key.macId &&
                    state.value.selectedWorkspaceId == key.workspaceId &&
                    state.value.visibleRunId == viewedRunId
                ) {
                    loadWorkspaces(mac, contextGeneration, key.workspaceId)
                }
            }
        }
        if (preloadSelectedAgent) {
            selected?.let {
                loadRunAgentDetail(mac, key, it, terminal, maxPages = 20)
            }
        }
        return
    }
    prewarmVisibleMarkdown(updated)
    awaitFirstFrameDrawn()
    if (runRequestIds[key] != requestId || !isVisibleRun(key)) return
    mutableState.update { it.projectRun(runStateStore[key], promptDrafts = promptDraftsByContext) }
    scheduleMobileStateSave()
    if (key !in loadedRunHistories &&
        conversationReadStore[key].history.let {
            it.phase != ConversationReadPhase.LOADING && it.issue?.retryAutomatically != false
        }
    ) {
        loadRunHistory(mac, key)
    }
    if (deferAutomaticSelection) {
        selectAgent(checkNotNull(automaticTarget), userInitiated = false)
    } else {
        selected?.let {
            loadRunAgentDetail(mac, key, it, terminal, maxPages = 4)
        }
    }
    if (terminal) prefetchAgentDiffSummaries(mac, key, agents)
    if (isVisibleRun(key) && terminal && state.value.followUpQueue.isNotEmpty()) {
        reconcileFollowUpQueue(
            resetCountdown = !wasTerminal && !hasFollowUpDeadline(key.runId),
        )
    } else if (isVisibleRun(key) &&
        !terminal && state.value.followUpSecondsRemaining != null
    ) {
        followUpTask?.cancel()
        followUpTask = null
        mutableState.update { it.copy(followUpSecondsRemaining = null) }
    }
}

internal fun latestPollingDevice(
    devices: List<RelayDevice>,
    fallback: RelayDevice,
): RelayDevice = devices.firstOrNull { it.deviceId == fallback.deviceId } ?: fallback

private fun MainViewModel.clearLiveUpdateInterrupted(key: WorkspaceRunKey) {
    mutableState.update { current ->
        if (current.liveUpdateInterruptedContext == key.encoded) {
            current.copy(liveUpdateInterruptedContext = null)
        } else {
            current
        }
    }
}

internal fun isLiveUpdateConnectionFailure(error: Throwable): Boolean {
    if (transientRelayConnectionMessage(error) != null) return true
    return generateSequence(error as Throwable?) { it.cause }
        .take(8)
        .filterIsInstance<RemoteCommandException>()
        .any { it.code == "machine_offline" }
}

private suspend fun MainViewModel.loadRunAgentDetail(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    agent: Int,
    terminal: Boolean,
    maxPages: Int,
) {
    try {
        loadAgent(mac, key, agent, maxPages)
    } catch (error: RemoteCommandException) {
        if (!terminal || isLiveUpdateConnectionFailure(error)) throw error
        // An unavailable detail must not prevent opening the known turn or advancing
        // a background continuation. Its failed read remains separate from content.
    }
}

internal fun isConversationPollingComplete(
    snapshot: WorkspaceRunSnapshot,
    reads: ConversationReadState,
): Boolean {
    if (!isTerminalRunStatus(snapshot.status) || reads.needsHistoryRetry()) return false
    val selectedRead = reads.agents[snapshot.selectedAgent]
    if (selectedRead?.phase == ConversationReadPhase.FAILED) {
        return selectedRead.issue?.retryAutomatically == false
    }
    return !selectedAgentDetailHasMore(snapshot)
}

private fun MainViewModel.updateWorkspaceRunSummary(
    key: WorkspaceRunKey,
    status: String,
    workerActive: Boolean,
    agents: List<AgentSummary>,
) {
    val finished = agents.count { isTerminalAgentStatus(it.status) }
    val failed = agents.count {
        isTerminalAgentStatus(it.status) && !it.status.equals("success", ignoreCase = true)
    }
    fun update(workspaces: List<RemoteWorkspace>): List<RemoteWorkspace> =
        workspaces.map { workspace ->
            if (workspace.workspaceId == key.workspaceId && workspace.lastRunId == key.runId) {
                workspace.copy(
                    runStatus = status,
                    runWorkerActive = workerActive,
                    runAgentsTotal = agents.size,
                    runAgentsFinished = finished,
                    runAgentsFailed = failed,
                )
            } else {
                workspace
            }
        }
    cachedWorkspacesByMac[key.macId]?.let { cached ->
        cachedWorkspacesByMac[key.macId] = update(cached)
    }
    mutableState.update { current ->
        if (current.selectedMacId == key.macId) {
            current.copy(workspaces = update(current.workspaces))
        } else {
            current
        }
    }
}

internal suspend fun MainViewModel.loadAgent(runId: String, agent: Int) {
    val context = currentRunKey(runId) ?: return
    loadAgent(context.first, context.second, agent)
}

internal suspend fun MainViewModel.loadAgent(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    agent: Int,
    maxPages: Int = 20,
) {
    val generation = contextGeneration
    try {
        conversationReadStore.read(key, ConversationReadTarget.Agent(agent)) {
            loadAgentPages(mac, key, agent, maxPages)
        }
    } catch (error: Throwable) {
        if (generation == contextGeneration && recoverMissingRunHistory(mac, key, error)) return
        throw error
    }
}

private suspend fun MainViewModel.loadAgentPages(
    mac: RelayDevice,
    key: WorkspaceRunKey,
    agent: Int,
    maxPages: Int,
) {
    val requestKey = key to agent
    val requestId = ++nextConversationRequestId
    agentRequestIds[requestKey] = requestId
    var transcript = runStateStore[key]
        ?.agentTranscripts
        ?.get(agent)
        ?.recoverMissingCachedContent()
        ?: MobileAgentTranscript()
    var madeProgress: Boolean
    var agentFinished: Boolean
    var pagesLoaded = 0
    do {
        val previousCursor = transcript.cursor
        val previousFinalMessageCursor = transcript.finalMessageCursor
        val detail = withTimeout(CONVERSATION_READ_TIMEOUT_MILLIS) {
            repository.agent(mac, key.runId, agent, previousCursor, previousFinalMessageCursor)
        }
        if (agentRequestIds[requestKey] != requestId) return
        val transcriptPage = detail["transcript_page"] as? JsonObject
        val eventPage = detail["event_page"] as? JsonObject
        val finalMessagePage = detail["final_message_page"] as? JsonObject
        val page = transcriptPage ?: eventPage
        val items = transcriptPage?.get("items")?.jsonArray
        val events = eventPage?.get("events")?.jsonArray
        val blocks = when {
            items != null -> MobileTranscriptFormatter.appendItems(transcript.blocks, items)
            events != null -> MobileTranscriptFormatter.appendEvents(transcript.blocks, events)
            else -> transcript.blocks
        }
        val next = page?.get("next_cursor")?.jsonPrimitive?.intOrNull ?: previousCursor
        val hasMore = page?.get("has_more")?.jsonPrimitive?.booleanOrNull ?: false
        val error = listOfNotNull(
            detail["error"]?.jsonPrimitive?.contentOrNull?.takeIf(String::isNotBlank),
            detail["artifact_error"]?.jsonPrimitive?.contentOrNull?.takeIf(String::isNotBlank),
        ).distinct().joinToString("\n\n")
        var finalMessage = transcript.finalMessage
        var finalMessageCursor = transcript.finalMessageCursor
        var finalMessageHasMore = false
        var finalMessageId = transcript.finalMessageId
        if (finalMessagePage != null) {
            val responseCursor = finalMessagePage["cursor"]?.jsonPrimitive?.intOrNull ?: 0
            val responseTotalBytes = finalMessagePage["total_bytes"]
                ?.jsonPrimitive
                ?.intOrNull
                ?: 0
            val responseId = finalMessagePage["message_id"]
                ?.jsonPrimitive
                ?.contentOrNull
                .orEmpty()
            if (responseId.isEmpty() && responseTotalBytes == 0) {
                // Accepted-run cleanup may remove the persisted artifact after
                // its text has reached the phone. Preserve that cached answer
                // and stop paging until a later poll exposes a new artifact.
                finalMessageHasMore = false
            } else {
                if (
                    responseCursor == 0 &&
                    previousFinalMessageCursor != 0 &&
                    responseId != finalMessageId
                ) {
                    finalMessage = ""
                    finalMessageCursor = 0
                    finalMessageId = ""
                }
                if (
                    finalMessageId.isNotEmpty() &&
                    responseId.isNotEmpty() &&
                    responseId != finalMessageId
                ) {
                    transcript = transcript.copy(
                        finalMessage = "",
                        finalMessageCursor = 0,
                        finalMessageHasMore = true,
                        finalMessageId = "",
                    )
                    madeProgress = true
                    agentFinished = true
                    continue
                }
                if (responseCursor == finalMessageCursor) {
                    finalMessage += finalMessagePage["text"]
                        ?.jsonPrimitive
                        ?.contentOrNull
                        .orEmpty()
                    finalMessageCursor = finalMessagePage["next_cursor"]
                        ?.jsonPrimitive
                        ?.intOrNull
                        ?: responseCursor
                    finalMessageHasMore = finalMessagePage["has_more"]
                        ?.jsonPrimitive
                        ?.booleanOrNull
                        ?: false
                    finalMessageId = responseId
                }
            }
        } else {
            detail["final_message"]?.jsonPrimitive?.contentOrNull?.let {
                finalMessage = it
            }
        }
        transcript = transcript.copy(
            blocks = blocks,
            cursor = next,
            hasMore = hasMore,
            finalMessage = finalMessage,
            finalMessageCursor = finalMessageCursor,
            finalMessageHasMore = finalMessageHasMore,
            finalMessageId = finalMessageId,
            error = if (detail.containsKey("error") || detail.containsKey("artifact_error")) {
                error
            } else {
                transcript.error
            },
            errorFromAgentResponse = if (detail.containsKey("error") || detail.containsKey("artifact_error")) {
                true
            } else {
                transcript.errorFromAgentResponse
            },
        )
        val updated = runStateStore.update(key) { current ->
            current.copy(agentTranscripts = current.agentTranscripts + (agent to transcript))
        }
        val visibleAgent = state.value.let { current ->
            isVisibleRun(key) &&
                (current.selectedAgent == agent || current.pendingAgentSelection == agent)
        }
        if (visibleAgent) {
            prewarmVisibleMarkdown(updated.copy(selectedAgent = agent))
            awaitFirstFrameDrawn()
            if (agentRequestIds[requestKey] != requestId || !isVisibleRun(key)) return
            if (state.value.selectedAgent == agent) {
                mutableState.update { current -> current.projectRun(runStateStore[key]) }
            } else if (state.value.pendingAgentSelection != agent) {
                return
            }
        }
        pagesLoaded += 1
        madeProgress = transcript.cursor > previousCursor ||
            transcript.finalMessageCursor > previousFinalMessageCursor
        agentFinished = runStateStore[key]
            ?.agents
            .orEmpty()
            .firstOrNull { it.number == agent }
            ?.status
            ?.let(::isTerminalAgentStatus) == true
    } while (
        (transcript.hasMore || transcript.finalMessageHasMore) &&
        madeProgress &&
        agentFinished &&
        pagesLoaded < maxPages.coerceAtLeast(1)
    )
}

internal fun selectedAgentDetailHasMore(snapshot: WorkspaceRunSnapshot): Boolean {
    if (snapshot.selectedAgentDetailMissing()) return true
    val selected = snapshot.selectedAgent ?: return false
    val transcript = snapshot.agentTranscripts[selected] ?: return false
    return transcript.hasMore || transcript.finalMessageHasMore
}

internal fun MainViewModel.ensureVisibleRunPolling() {
    val context = currentRunKey() ?: return
    beginPolling(context.first, context.second)
}

fun MainViewModel.clearMessage(expected: String) = mutableState.update {
    if (it.message == expected) it.copy(message = null, messageIsError = false) else it
}

internal fun MainViewModel.selectedMac(): RelayDevice? = state.value.macs.firstOrNull {
    it.deviceId == state.value.selectedMacId
}

internal fun MainViewModel.currentRunKey(
    runId: String = state.value.visibleRunId.orEmpty(),
): Pair<RelayDevice, WorkspaceRunKey>? {
    if (runId.isBlank()) return null
    val snapshot = state.value
    val mac = selectedMac() ?: return null
    val workspaceId = snapshot.selectedWorkspaceId ?: return null
    return mac to WorkspaceRunKey(mac.deviceId, workspaceId, runId)
}

internal fun MainViewModel.isVisibleRun(key: WorkspaceRunKey): Boolean {
    val snapshot = state.value
    return snapshot.selectedMacId == key.macId &&
        snapshot.selectedWorkspaceId == key.workspaceId &&
        snapshot.visibleRunId == key.runId
}

internal fun MainViewModel.captureVisibleRunState() {
    val key = currentRunKey()?.second ?: return
    runStateStore.capture(key, state.value)
}

internal fun MainViewModel.hydrateVisibleRunCache(key: WorkspaceRunKey) {
    viewModelScope.launch {
        val cached = runStateStore.hydrate(key) ?: return@launch
        if (!isVisibleRun(key)) return@launch
        prewarmVisibleMarkdown(cached)
        awaitFirstFrameDrawn()
        if (!isVisibleRun(key)) return@launch
        mutableState.update { current ->
            if (isVisibleRun(key)) current.projectRun(runStateStore[key], promptDrafts = promptDraftsByContext)
            else current
        }
        scheduleMobileStateSave()
    }
}

internal fun MainViewModel.removeRunContexts(
    removePersisted: Boolean = false,
    predicate: (WorkspaceRunKey) -> Boolean,
) {
    runPollingByContext.keys.filter(predicate).forEach { key ->
        runPollingByContext.remove(key)?.cancel()
    }
    runRequestIds.keys.removeAll(predicate)
    agentRequestIds.keys.removeAll { (key, _) -> predicate(key) }
    runHistoryRequestIds.keys.removeAll(predicate)
    loadedRunHistories.removeAll(predicate)
    runStateStore.removeWhere(predicate, removePersisted = removePersisted)
    conversationReadStore.removeWhere(predicate)
    pendingStorageActions.removeWhere(predicate)
}

internal fun MainViewModel.pruneObsoleteRunContexts(
    macId: String,
    workspaces: List<RemoteWorkspace>,
) {
    val retained = workspaces.mapNotNullTo(mutableSetOf()) { workspace ->
        workspace.lastRunId.takeIf(String::isNotBlank)?.let { runId ->
            WorkspaceRunKey(macId, workspace.workspaceId, runId)
        }
    }
    currentRunKey()?.second
        ?.takeIf { it.macId == macId }
        ?.let(retained::add)
    domainRepositories.conversations.viewedRunIdsByWorkspace.forEach { (context, runId) ->
        val parts = context.split('\u001f')
        if (parts.size == 2 && parts[0] == macId &&
            workspaces.any { it.workspaceId == parts[1] }
        ) retained.add(WorkspaceRunKey(macId, parts[1], runId))
    }
    pendingRunNavigation
        ?.takeIf { it.macId == macId }
        ?.let { navigation ->
            runCatching {
                WorkspaceRunKey(
                    navigation.macId,
                    navigation.workspaceId,
                    navigation.runId,
                )
            }.getOrNull()?.let(retained::add)
        }
    removeRunContexts { key -> key.macId == macId && key !in retained }
    val selectionChanged = selectedAgentsByContext.keys.removeAll { encoded ->
        WorkspaceRunKey.decode(encoded)?.let { key ->
            key.macId == macId && key !in retained
        } == true
    }
    scheduledCompletionRuns.removeAll { encoded ->
        WorkspaceRunKey.decode(encoded)?.let { key ->
            key.macId == macId && key !in retained
        } == true
    }
    if (selectionChanged) scheduleMobileStateSave()
}

internal fun MainViewModel.scheduleCompletionMonitor(
    macId: String,
    workspaceId: String,
    workspaceName: String,
    runId: String,
) {
    if (runId.isBlank()) return
    val key = completionMonitorKey(macId, workspaceId, runId)
    if (!scheduledCompletionRuns.add(key)) return
    app.runCompletionScheduler.enqueue(
        macID = macId,
        workspaceID = workspaceId,
        workspaceName = workspaceName,
        runID = runId,
    )
}

internal const val MAX_CONCURRENT_RUN_POLL_REQUESTS = 6
