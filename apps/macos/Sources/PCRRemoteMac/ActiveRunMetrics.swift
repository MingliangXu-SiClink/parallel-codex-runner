import Combine
import Foundation

@MainActor
final class ActiveRunMetrics: ObservableObject {
    private(set) var runID: String?
    private(set) var runPhaseTimings: [String: Double] = [:]
    @Published private(set) var agents: [RunAgentSummary] = []

    func update(_ run: RunSummary?) {
        guard runID != run?.runId || agents != (run?.agents ?? [])
                || runPhaseTimings != (run?.phaseTimings ?? [:]) else { return }
        runID = run?.runId
        runPhaseTimings = run?.phaseTimings ?? [:]
        agents = run?.agents ?? []
    }

    func agent(runID: String, index: Int) -> RunAgentSummary? {
        guard self.runID == runID else { return nil }
        return agents.first { $0.agent == index }
    }
}

struct RunReaderState: Equatable, Sendable {
    let summary: RunSummary
    let activities: [AgentActivityReaderState?]
    let candidateActivities: [AgentActivityReaderState?]
}

extension RunSummary {
    // Counts still drive detail fetching through the full activeRun value.
    // Only reader invalidation omits live metrics and polling timestamps.
    var readerState: RunReaderState {
        var value = self
        value.updatedAt = ""
        value.eventCount = 0
        value.phaseTimings = nil
        value.agents = agents.map { agent in
            var value = agent
            value.phaseTimings = nil
            value.activity = nil
            guard !agent.isTerminal else { return value }
            value.seconds = nil
            value.reasoningTokens = nil
            value.reasoningTokenCounts = [:]
            return value
        }
        value.conversationSnapshot = conversationSnapshot.map { snapshot in
            ConversationSnapshot(
                schemaVersion: snapshot.schemaVersion,
                machine: MachineRecord(machineId: snapshot.machine.machineId,
                    name: snapshot.machine.name, platform: snapshot.machine.platform,
                    online: snapshot.machine.online, updatedAt: ""),
                workspace: WorkspaceRecord(workspaceId: snapshot.workspace.workspaceId,
                    machineId: snapshot.workspace.machineId, name: snapshot.workspace.name,
                    path: snapshot.workspace.path, available: snapshot.workspace.available,
                    updatedAt: ""),
                conversation: ConversationRecord(
                    conversationId: snapshot.conversation.conversationId,
                    workspaceId: snapshot.conversation.workspaceId,
                    title: snapshot.conversation.title, customTitle: snapshot.conversation.customTitle,
                    titleRevision: snapshot.conversation.titleRevision,
                    createdAt: snapshot.conversation.createdAt, updatedAt: "",
                    activeTurnId: snapshot.conversation.activeTurnId,
                    resumeSessionId: snapshot.conversation.resumeSessionId,
                    turnIds: snapshot.conversation.turnIds),
                turn: TurnRecord(turnId: snapshot.turn.turnId,
                    conversationId: snapshot.turn.conversationId, runId: snapshot.turn.runId,
                    turnIndex: snapshot.turn.turnIndex, prompt: snapshot.turn.prompt,
                    phase: snapshot.turn.phase, settings: snapshot.turn.settings,
                    createdAt: snapshot.turn.createdAt, updatedAt: "",
                    continuedFromTurnId: snapshot.turn.continuedFromTurnId,
                    continuedFromCandidateId: snapshot.turn.continuedFromCandidateId,
                    recommendedCandidateId: snapshot.turn.recommendedCandidateId,
                    finalizedCandidateId: snapshot.turn.finalizedCandidateId,
                    candidateIds: snapshot.turn.candidateIds),
                candidates: snapshot.candidates.map { candidate in
                    let terminal = agents.first { $0.agent == candidate.agentIndex }?.isTerminal != false
                    return AgentCandidateRecord(candidateId: candidate.candidateId,
                        turnId: candidate.turnId, agentIndex: candidate.agentIndex,
                        displayName: candidate.displayName, role: candidate.role, status: candidate.status,
                        seconds: terminal ? candidate.seconds : nil,
                        startedAt: candidate.startedAt, finishedAt: candidate.finishedAt,
                        activity: nil,
                        reasoningTokens: terminal ? candidate.reasoningTokens : nil,
                        reasoningTokenCounts: terminal ? candidate.reasoningTokenCounts : [:],
                        codexThreadId: candidate.codexThreadId, error: candidate.error,
                        rejected: candidate.rejected, stale: candidate.stale,
                        recommended: candidate.recommended, finalized: candidate.finalized,
                        workspaceDir: candidate.workspaceDir, workspaceAvailable: candidate.workspaceAvailable,
                        canAccept: candidate.canAccept, canContinue: candidate.canContinue,
                        canRetry: candidate.canRetry)
                })
        }
        return RunReaderState(summary: value, activities: agents.map { $0.activity?.readerState },
            candidateActivities: conversationSnapshot?.candidates.map { $0.activity?.readerState } ?? [])
    }
}
