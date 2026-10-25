package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteWorkspace
import kotlinx.coroutines.flow.update

internal fun agentConversationPreferenceKey(
    macId: String?,
    workspaceId: String?,
    runId: String?,
    agent: Int?,
): String? {
    if (macId.isNullOrBlank() || workspaceId.isNullOrBlank() ||
        runId.isNullOrBlank() || agent == null || agent <= 0
    ) {
        return null
    }
    return "$macId\u001f$workspaceId\u001f$runId\u001f$agent"
}

internal fun MainUiState.agentConversationPreferenceKey(agent: Int?): String? =
    agentConversationPreferenceKey(
        macId = selectedMacId,
        workspaceId = selectedWorkspaceId,
        runId = visibleRunId,
        agent = agent,
    )

internal fun MainUiState.isAgentCompletionUnread(agent: AgentSummary): Boolean {
    if (!isTerminalAgentStatus(agent.status) || agent.number == selectedAgent) return false
    val key = agentConversationPreferenceKey(agent.number) ?: return false
    return key !in viewedAgentCompletions
}

internal fun runCompletionPreferenceKey(
    macId: String,
    workspaceId: String,
    runId: String,
): String = "$macId\u001f$workspaceId\u001f$runId"

internal fun workspaceWorkExpansionPreferenceKey(
    macId: String?,
    workspaceId: String?,
): String? {
    if (macId.isNullOrBlank() || workspaceId.isNullOrBlank()) return null
    return "$macId\u001f$workspaceId"
}

internal fun MainUiState.workspaceWorkExpansionPreferenceKey(): String? =
    workspaceWorkExpansionPreferenceKey(selectedMacId, selectedWorkspaceId)

internal fun MainUiState.isWorkspaceRunCompletionUnread(
    workspace: RemoteWorkspace,
): Boolean {
    val macId = selectedMacId ?: return false
    if (workspace.lastRunId.isBlank()) return false
    if (!isTerminalRunStatus(workspace.runStatus) || workspace.runWorkerActive) return false
    return runCompletionPreferenceKey(
        macId,
        workspace.workspaceId,
        workspace.lastRunId,
    ) in unreadRunCompletions
}

internal fun MainViewModel.markRunCompletionUnread(key: WorkspaceRunKey) {
    val latestRunId = cachedWorkspacesByMac[key.macId]
        .orEmpty()
        .firstOrNull { it.workspaceId == key.workspaceId }
        ?.lastRunId
        ?.takeIf(String::isNotBlank)
        ?: state.value.takeIf {
            it.selectedMacId == key.macId && it.selectedWorkspaceId == key.workspaceId
        }?.visibleRunId
    if (!shouldMarkRunCompletionUnread(key.runId, latestRunId)) return
    val completionKey = runCompletionPreferenceKey(key.macId, key.workspaceId, key.runId)
    if (!unreadRunCompletions.add(completionKey)) return
    runCatching {
        mobileStateStore.update { persisted ->
            persisted.copy(
                unreadRunCompletions = persisted.unreadRunCompletions + completionKey,
            )
        }
    }
    publishConversationPreferences()
}

internal fun shouldMarkRunCompletionUnread(
    completedRunId: String,
    latestRunId: String?,
): Boolean = latestRunId.isNullOrBlank() || completedRunId == latestRunId

internal fun MainViewModel.markWorkspaceRunCompletionViewed(
    workspaceId: String?,
    runId: String?,
) {
    val macId = state.value.selectedMacId ?: return
    if (workspaceId.isNullOrBlank() || runId.isNullOrBlank()) return
    val completionKey = runCompletionPreferenceKey(macId, workspaceId, runId)
    if (!unreadRunCompletions.remove(completionKey)) return
    runCatching {
        mobileStateStore.update { persisted ->
            persisted.copy(
                unreadRunCompletions = persisted.unreadRunCompletions - completionKey,
            )
        }
    }
    publishConversationPreferences()
}

internal fun MainViewModel.onWorkspaceRunStarted(
    macId: String,
    workspaceId: String,
    runId: String,
) {
    val workspacePrefix = "$macId\u001f$workspaceId\u001f"
    val previousRunIds = buildSet {
        cachedWorkspacesByMac[macId]
            .orEmpty()
            .firstOrNull { it.workspaceId == workspaceId }
            ?.lastRunId
            ?.takeIf(String::isNotBlank)
            ?.let(::add)
        state.value.workspaces
            .firstOrNull {
                state.value.selectedMacId == macId && it.workspaceId == workspaceId
            }
            ?.lastRunId
            ?.takeIf(String::isNotBlank)
            ?.let(::add)
        scheduledCompletionRuns.mapNotNullTo(this) { encoded ->
            WorkspaceRunKey.decode(encoded)
                ?.takeIf { it.macId == macId && it.workspaceId == workspaceId }
                ?.runId
        }
    } - runId
    previousRunIds.forEach { previousRunId ->
        app.runCompletionScheduler.cancel(macId, workspaceId, previousRunId)
    }
    scheduledCompletionRuns.removeAll { it.startsWith(workspacePrefix) }
    val unreadChanged = unreadRunCompletions.removeAll {
        it.startsWith(workspacePrefix)
    }
    if (unreadChanged) {
        runCatching {
            mobileStateStore.update { persisted ->
                persisted.copy(
                    unreadRunCompletions = persisted.unreadRunCompletions.filterNotTo(
                        mutableSetOf()
                    ) { it.startsWith(workspacePrefix) },
                )
            }
        }
        publishConversationPreferences()
    }

    fun updated(workspaces: List<RemoteWorkspace>): List<RemoteWorkspace> =
        workspaces.map { workspace ->
            if (workspace.workspaceId == workspaceId) {
                workspace.copy(
                    lastRunId = runId,
                    runStatus = "starting",
                    runWorkerActive = true,
                    runAgentsTotal = 0,
                    runAgentsFinished = 0,
                    runAgentsFailed = 0,
                )
            } else {
                workspace
            }
        }

    cachedWorkspacesByMac[macId]?.let { cached ->
        cachedWorkspacesByMac[macId] = updated(cached)
    }
    mutableState.update { current ->
        if (current.selectedMacId == macId) {
            current.copy(workspaces = updated(current.workspaces))
        } else {
            current
        }
    }
}

internal fun MainUiState.isWorkspaceWorkExpanded(): Boolean {
    val workspaceKey = workspaceWorkExpansionPreferenceKey()
        ?: return workExpandedByDefault
    workExpansionStates[workspaceKey]?.let { return it }
    val legacyPrefix = "$workspaceKey\u001f"
    return workExpansionStates.entries.firstOrNull {
        it.key.startsWith(legacyPrefix)
    }?.value ?: workExpandedByDefault
}

internal fun MainViewModel.setWorkspaceWorkExpanded(expanded: Boolean) {
    val snapshot = state.value
    val key = snapshot.workspaceWorkExpansionPreferenceKey() ?: return
    val legacyPrefix = "$key\u001f"
    val legacyKeys = workExpansionStatesByWorkspace.keys
        .filter { it.startsWith(legacyPrefix) }
    if (workExpansionStatesByWorkspace[key] == expanded && legacyKeys.isEmpty()) return
    legacyKeys.forEach(workExpansionStatesByWorkspace::remove)
    workExpansionStatesByWorkspace[key] = expanded
    publishConversationPreferences()
    scheduleMobileStateSave()
}

internal fun MainUiState.workSectionPrefix(): String =
    "work-section|${agentConversationPreferenceKey(selectedAgent).orEmpty()}|"

internal fun MainViewModel.setWorkSectionExpanded(id: String, expanded: Boolean) {
    workExpansionStatesByWorkspace[state.value.workSectionPrefix() + id] = expanded
    publishConversationPreferences()
    scheduleMobileStateSave()
}

internal fun MainViewModel.markAgentCompletionViewed(agent: Int) {
    val snapshot = state.value
    if (snapshot.agentSummaries.none {
            it.number == agent && isTerminalAgentStatus(it.status)
        }
    ) {
        return
    }
    val key = snapshot.agentConversationPreferenceKey(agent) ?: return
    if (!viewedAgentCompletions.add(key)) return
    publishConversationPreferences()
    scheduleMobileStateSave()
}

internal fun MainViewModel.resetAgentCompletionViewed(agent: Int) {
    val key = state.value.agentConversationPreferenceKey(agent) ?: return
    if (!viewedAgentCompletions.remove(key)) return
    publishConversationPreferences()
    scheduleMobileStateSave()
}

internal fun MainViewModel.resetViewedCompletionsForActiveAgents(
    runKey: WorkspaceRunKey,
    agents: List<AgentSummary>,
) {
    val activeKeys = agents.asSequence()
        .filter { !isTerminalAgentStatus(it.status) }
        .mapNotNull { agent ->
            agentConversationPreferenceKey(
                macId = runKey.macId,
                workspaceId = runKey.workspaceId,
                runId = runKey.runId,
                agent = agent.number,
            )
        }
        .toSet()
    if (activeKeys.isEmpty() || !viewedAgentCompletions.removeAll(activeKeys)) return
    publishConversationPreferences()
    scheduleMobileStateSave()
}

internal fun MainViewModel.publishConversationPreferences() {
    val workExpansion = workExpansionStatesByWorkspace.toMap()
    val viewedCompletions = viewedAgentCompletions.toSet()
    val unreadCompletions = unreadRunCompletions.toSet()
    mutableState.update {
        it.copy(
            workExpansionStates = workExpansion,
            viewedAgentCompletions = viewedCompletions,
            unreadRunCompletions = unreadCompletions,
        )
    }
}
