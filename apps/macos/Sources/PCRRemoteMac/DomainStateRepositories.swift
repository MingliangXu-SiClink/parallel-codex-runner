import Foundation

/// Machine-scoped state. Request tasks and retry timers remain AppModel concerns.
final class MachineStateRepository {
    var records: [String: MachineRecord] = [:]
    var selectedWorkspaceByMachine: [String: String] = [:]
    var defaultSettingsStates: [String: DefaultSettingsContextState] = [:]
}

/// Workspace-scoped settings and the selected conversation for each workspace.
final class WorkspaceStateRepository {
    var records: [String: WorkspaceRecord] = [:]
    var settingsByWorkspace: [String: RunSettings] = [:]
    var dirtySettingsFields: [String: Set<WorkspaceSettingField>] = [:]
    var selectedConversationByWorkspace: [String: String] = [:]
}

/// Conversation-scoped drafts, queues, and durable prompt-delivery state.
final class ConversationStateRepository {
    var records: [String: ConversationRecord] = [:]
    var titlesByContext: [String: ConversationRecord] = [:]
    var promptDraftsByContext: [String: String] = [:]
    var persistedFollowUpQueues: [String: PersistedFollowUpQueue] = [:]
    var promptOutboxByWorkspace: [String: PersistedPromptSubmission] = [:]
    var acceptedPromptResultsByWorkspace: [String: PersistedAcceptedPromptResult] = [:]
    var deferredPromptsByWorkspace: [String: [PersistedDeferredPrompt]] = [:]
}

/// Turn-scoped protocol records and complete cached conversation projections.
final class TurnStateRepository {
    var records: [String: TurnRecord] = [:]
    var runSummariesByContext: [WorkspaceRunViewKey: RunSummary] = [:]
    let runViewStateStore: WorkspaceRunViewStateStore

    init(
        runViewStateStore: WorkspaceRunViewStateStore = WorkspaceRunViewStateStore(
            diskCache: WorkspaceRunDiskCache.standard()
        )
    ) {
        self.runViewStateStore = runViewStateStore
    }
}

/// Agent-candidate protocol records and user branch selections.
final class AgentCandidateStateRepository {
    var records: [String: AgentCandidateRecord] = [:]
    var selectedAgentsByContext: [String: Int] = [:]
    var selectedAgentContextsWhileWorking: Set<String> = []
}

/// The single macOS ownership graph for generated PCR conversation-domain state.
final class ClientDomainRepositories {
    let machines = MachineStateRepository()
    let workspaces = WorkspaceStateRepository()
    let conversations = ConversationStateRepository()
    let turns: TurnStateRepository
    let candidates = AgentCandidateStateRepository()

    init(windowID: UUID? = nil, runViewStateStore: WorkspaceRunViewStateStore? = nil) {
        turns = TurnStateRepository(runViewStateStore: runViewStateStore ?? WorkspaceRunViewStateStore(
            diskCache: WorkspaceRunDiskCache.standard(windowID: windowID)
        ))
    }

    func ingest(_ snapshot: ConversationSnapshot) {
        if !snapshot.machine.machineId.isEmpty {
            machines.records[snapshot.machine.machineId] = snapshot.machine
        }
        if !snapshot.workspace.workspaceId.isEmpty {
            workspaces.records[snapshot.workspace.workspaceId] = snapshot.workspace
        }
        if !snapshot.conversation.conversationId.isEmpty {
            conversations.records[snapshot.conversation.conversationId] = snapshot.conversation
        }
        if !snapshot.turn.turnId.isEmpty {
            turns.records[snapshot.turn.turnId] = snapshot.turn
        }
        for candidate in snapshot.candidates where !candidate.candidateId.isEmpty {
            candidates.records[candidate.candidateId] = candidate
        }
    }

    func removeMachine(_ machineID: String) {
        let workspaceIDs = Set(
            workspaces.records.values
                .filter { $0.machineId == machineID }
                .map(\.workspaceId)
        )
        for workspaceID in workspaceIDs {
            removeWorkspace(workspaceID)
        }
        machines.records.removeValue(forKey: machineID)
    }

    func removeWorkspace(_ workspaceID: String) {
        let conversationIDs = Set(
            conversations.records.values
                .filter { $0.workspaceId == workspaceID }
                .map(\.conversationId)
        )
        let turnIDs = Set(
            turns.records.values
                .filter { conversationIDs.contains($0.conversationId) }
                .map(\.turnId)
        )
        candidates.records = candidates.records.filter { !turnIDs.contains($0.value.turnId) }
        turns.records = turns.records.filter { !turnIDs.contains($0.key) }
        conversations.records = conversations.records.filter {
            !conversationIDs.contains($0.key)
        }
        workspaces.records.removeValue(forKey: workspaceID)
    }
}
