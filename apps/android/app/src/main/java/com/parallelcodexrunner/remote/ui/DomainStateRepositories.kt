package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.CodexModelOptions
import com.parallelcodexrunner.remote.data.PersistedMobileState
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.generated.AgentCandidateRecord
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot
import com.parallelcodexrunner.remote.data.generated.MachineRecord
import com.parallelcodexrunner.remote.data.generated.TurnRecord
import com.parallelcodexrunner.remote.data.generated.WorkspaceRecord

/** Machine-scoped state. Network request bookkeeping intentionally stays in the ViewModel. */
internal class MachineStateRepository(restored: PersistedMobileState) {
    val records = mutableMapOf<String, MachineRecord>()
    var cachedDevices: List<RelayDevice> = restored.cachedDevices
    val defaultSettings = restored.defaultSettingsByMac.toMutableMap()
    val defaultPrimaryOptions = mutableMapOf<String, CodexModelOptions>()
    val defaultSynthesisOptions = mutableMapOf<String, CodexModelOptions>()
    val defaultSettingsErrors = mutableMapOf<String, String>()
    val defaultOptionsErrors = mutableMapOf<String, String>()
    val dirtyDefaultSettingFields = restored.dirtyDefaultSettingFieldsByMac
        .mapValuesTo(mutableMapOf()) { (_, fields) -> fields.toMutableSet() }
}

/** Workspace-scoped state and the client-local workspace selection. */
internal class WorkspaceStateRepository(restored: PersistedMobileState) {
    val records = mutableMapOf<String, WorkspaceRecord>()
    val cachedByMachine = restored.cachedWorkspacesByMac.toMutableMap()
    val selectedByMachine = restored.selectedWorkspaceByMac.toMutableMap()
    val settings = restored.workspaceSettings.toMutableMap()
    val dirtySettingFields = restored.dirtyWorkspaceSettingFields
        .mapValuesTo(mutableMapOf()) { (_, fields) -> fields.toMutableSet() }

    fun cacheSavedSettings(machineId: String, workspaceId: String, saved: RemoteRunSettings) {
        val workspaces = cachedByMachine[machineId] ?: return
        cachedByMachine[machineId] = workspaces.map { workspace ->
            if (workspace.workspaceId == workspaceId) workspace.copy(settings = saved)
            else workspace
        }
    }
}

/** Conversation-scoped drafts, queues, and durable submission state. */
internal class ConversationStateRepository(restored: PersistedMobileState) {
    val records = mutableMapOf<String, ConversationRecord>()
    val viewedRunIdsByWorkspace = restored.viewedRunIdsByWorkspace.toMutableMap()
    val detachedContexts = restored.detachedConversationContexts.toMutableSet()
    val detachedResumeSessions = restored.detachedResumeSessions.toMutableMap()
    val promptDrafts = restored.promptDrafts.toMutableMap()
    val followUpQueues = restored.followUpQueueItems.toMutableMap()
    val followUpDeadlines = restored.followUpDeadlines.toMutableMap()
    val promptOutbox = restored.promptOutbox.toMutableMap()
    val acceptedPromptResults = restored.acceptedPromptResults.toMutableMap()

    fun detach(workspaceKey: String, resumeSessionId: String) {
        viewedRunIdsByWorkspace.remove(workspaceKey)
        detachedContexts.add(workspaceKey)
        detachedResumeSessions[workspaceKey] = resumeSessionId.trim().ifBlank { "NO" }
    }

    fun attach(workspaceKey: String) {
        viewedRunIdsByWorkspace.remove(workspaceKey)
        detachedContexts.remove(workspaceKey)
        detachedResumeSessions.remove(workspaceKey)
    }

    fun selectRun(workspaceKey: String, runId: String) {
        if (runId.isBlank()) return
        attach(workspaceKey)
        viewedRunIdsByWorkspace[workspaceKey] = runId
    }

    fun displayedRunId(workspaceKey: String?, latestRunId: String?): String? {
        if (workspaceKey in detachedContexts) return null
        return viewedRunIdsByWorkspace[workspaceKey] ?: latestRunId?.takeIf(String::isNotBlank)
    }

    fun advanceViewedRun(workspaceKey: String, sourceRunId: String, nextRunId: String) {
        if (viewedRunIdsByWorkspace[workspaceKey] == sourceRunId) {
            selectRun(workspaceKey, nextRunId)
        }
    }

    fun settingsForDraft(workspaceKey: String?, settings: RemoteRunSettings): RemoteRunSettings {
        if (workspaceKey !in detachedContexts) return settings
        val session = detachedResumeSessions[workspaceKey] ?: return settings
        // Workspace defaults can change on another client. They must not change
        // the identity of a new or explicitly resumed conversation being composed.
        return settings.copy(resumeSessionId = session)
    }

    fun replaceWorkspaceRecords(
        workspaceId: String,
        values: List<ConversationRecord>,
    ) {
        records.keys.removeAll { id -> records[id]?.workspaceId == workspaceId }
        values.forEach { record ->
            if (record.conversationId.isNotBlank()) {
                records[record.conversationId] = record
            }
        }
    }

    fun mergeWorkspaceRecords(
        workspaceId: String,
        values: List<ConversationRecord>,
    ) {
        values.forEach { record ->
            if (record.conversationId.isNotBlank() && record.workspaceId == workspaceId) {
                records[record.conversationId] = record
            }
        }
    }

    fun recordsForWorkspace(workspaceId: String): List<ConversationRecord> = records.values
        .asSequence()
        .filter { it.workspaceId == workspaceId }
        .sortedWith(
            compareByDescending<ConversationRecord> { it.updatedAt }
                .thenByDescending { it.conversationId }
        )
        .toList()
}

/** Turn-scoped protocol records and complete cached conversation projections. */
internal class TurnStateRepository(diskCache: WorkspaceRunDiskCache) {
    val records = mutableMapOf<String, TurnRecord>()
    val runState = WorkspaceRunStateStore(diskCache)
    val unreadCompletions = mutableSetOf<String>()
}

/** Agent-candidate records and user view choices. */
internal class AgentCandidateStateRepository(restored: PersistedMobileState) {
    val records = mutableMapOf<String, AgentCandidateRecord>()
    val selectedByContext = restored.selectedAgents.toMutableMap()
    val workExpansionStates = restored.workExpansionStates.toMutableMap()
    val viewedCompletions = restored.viewedAgentCompletions.toMutableSet()
}

/**
 * The single Android ownership graph for PCR domain state.
 *
 * MainViewModel remains responsible for lifecycle and requests; user-domain data lives here and
 * is populated from the generated ConversationSnapshot contract.
 */
internal class ClientDomainRepositories(
    restored: PersistedMobileState,
    diskCache: WorkspaceRunDiskCache,
) {
    val machines = MachineStateRepository(restored)
    val workspaces = WorkspaceStateRepository(restored)
    val conversations = ConversationStateRepository(restored)
    val turns = TurnStateRepository(diskCache).also {
        it.unreadCompletions += restored.unreadRunCompletions
    }
    val candidates = AgentCandidateStateRepository(restored)

    fun ingest(snapshot: ConversationSnapshot) {
        if (snapshot.machine.machineId.isNotBlank()) {
            machines.records[snapshot.machine.machineId] = snapshot.machine
        }
        if (snapshot.workspace.workspaceId.isNotBlank()) {
            workspaces.records[snapshot.workspace.workspaceId] = snapshot.workspace
        }
        if (snapshot.conversation.conversationId.isNotBlank()) {
            conversations.records[snapshot.conversation.conversationId] = snapshot.conversation
        }
        if (snapshot.turn.turnId.isNotBlank()) {
            turns.records[snapshot.turn.turnId] = snapshot.turn
        }
        snapshot.candidates.forEach { candidate ->
            if (candidate.candidateId.isNotBlank()) {
                candidates.records[candidate.candidateId] = candidate
            }
        }
    }

    fun removeMachine(machineId: String) {
        val workspaceIds = workspaces.records.values
            .filter { it.machineId == machineId }
            .mapTo(mutableSetOf(), WorkspaceRecord::workspaceId)
        workspaceIds.forEach(::removeWorkspace)
        machines.records.remove(machineId)
    }

    fun removeWorkspace(workspaceId: String) {
        val conversationIds = conversations.records.values
            .filter { it.workspaceId == workspaceId }
            .mapTo(mutableSetOf(), ConversationRecord::conversationId)
        val turnIds = turns.records.values
            .filter { it.conversationId in conversationIds }
            .mapTo(mutableSetOf(), TurnRecord::turnId)
        candidates.records.keys.removeAll { id ->
            candidates.records[id]?.turnId in turnIds
        }
        turns.records.keys.removeAll(turnIds)
        conversations.records.keys.removeAll(conversationIds)
        workspaces.records.remove(workspaceId)
    }
}
