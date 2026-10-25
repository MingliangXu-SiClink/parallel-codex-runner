import Foundation

enum ConversationSurfacePhase: Equatable, Sendable {
    case newConversation
    case loading
    case submitting
    case historical
    case running
    case review
    case applying
    case applied
    case recovery
    case failed
    case stopped
    case finished
}

struct ConversationSurfaceState: Equatable, Sendable {
    let phase: ConversationSurfacePhase
    let title: String
    let workspaceName: String
    let turnNumber: Int?
    let runPresentation: ConversationRunPresentation?
    let selectedAgent: Int?
    let selectedAgentName: String?
    let recommendedAgent: Int?
    let acceptedAgent: Int?
    let acceptedAgentName: String?
    let candidateCount: Int
    let isLatestTurn: Bool

    var turnLabel: String? {
        turnNumber.map { "Turn \($0)" }
    }

    var selectedAgentIsRecommended: Bool {
        selectedAgent != nil && selectedAgent == recommendedAgent
    }

    var selectedAgentIsAccepted: Bool {
        selectedAgent != nil && selectedAgent == acceptedAgent
    }

    var headerSubtitle: String {
        guard let turnLabel else { return workspaceName }
        return "\(workspaceName) · \(turnLabel)"
    }

    func toolbarStatusLabel(language: AppLanguage) -> String? {
        switch phase {
        case .newConversation, .historical, .loading: return nil
        case .submitting: return nil
        case .running: return language.text("正在回复", "Working")
        case .applying: return language.text("正在保存", "Saving")
        case .review, .finished: return language.text("已完成", "Completed")
        case .applied: return language.text("已保存", "Saved")
        case .recovery:
            if runPresentation?.label == "Storage confirmation needed" {
                return language.text("等待容量确认", "Storage confirmation needed")
            }
            return runPresentation?.label == "Message retained"
                ? language.text("消息已保留", "Message retained") : language.text("需要处理", "Needs attention")
        case .failed: return language.text("未能完成", "Could not complete")
        case .stopped: return language.text("已停止", "Stopped")
        }
    }

    static func make(
        workspace: RemoteWorkspace,
        conversation: WorkspaceConversationSummary?,
        composingNewConversation: Bool,
        run: RunSummary?,
        selectedAgent: RunAgentSummary?,
        isLatestTurn: Bool,
        loading: Bool,
        pendingSubmission: PersistedPromptSubmission? = nil,
        presentationOverride: ConversationRunPresentation? = nil,
        language: AppLanguage = .english
    ) -> Self {
        let title: String
        if composingNewConversation {
            title = language.text("新对话", "New Conversation")
        } else if let conversation {
            title = conversation.title == "Previous conversation"
                ? language.text("之前的对话", "Previous conversation")
                : conversation.title
        } else {
            title = workspace.name
        }

        guard let run else {
            if let pendingSubmission {
                return Self(
                    phase: pendingSubmission.surfacePhase,
                    title: title,
                    workspaceName: workspace.name,
                    turnNumber: 1,
                    runPresentation: pendingSubmission.runPresentation,
                    selectedAgent: nil,
                    selectedAgentName: nil,
                    recommendedAgent: nil,
                    acceptedAgent: nil,
                    acceptedAgentName: nil,
                    candidateCount: 0,
                    isLatestTurn: true
                )
            }
            return Self(
                phase: loading
                    ? .loading
                    : (conversation?.resumeSession == nil ? .newConversation : .historical),
                title: title,
                workspaceName: workspace.name,
                turnNumber: nil,
                runPresentation: nil,
                selectedAgent: nil,
                selectedAgentName: nil,
                recommendedAgent: nil,
                acceptedAgent: nil,
                acceptedAgentName: nil,
                candidateCount: 0,
                isLatestTurn: isLatestTurn
            )
        }

        let presentation = pendingSubmission?.runPresentation
            ?? presentationOverride
            ?? ConversationRunPresentation.make(run: run, language: language)
        let phase: ConversationSurfacePhase
        if let pendingSubmission {
            phase = pendingSubmission.surfacePhase
        } else if !isLatestTurn {
            phase = .historical
        } else {
            phase = switch presentation.phase {
            case .preparing, .primary, .synthesis, .stopping, .finishing:
                .running
            case .finalizing:
                .applying
            case .review:
                .review
            case .finalized:
                .applied
            case .syncAttention, .cleanupAttention:
                .recovery
            case .failed:
                .failed
            case .stopped, .discarded, .expired:
                .stopped
            case .finished:
                .finished
            }
        }

        let orderedRuns = conversation?.runs ?? [run]
        let currentTurnNumber = conversation?.turnNumber(for: run.runId)
            ?? run.turnIndex.map { $0 + 1 }
            ?? orderedRuns.firstIndex(where: { $0.runId == run.runId }).map { $0 + 1 }
            ?? 1
        let turnNumber = pendingSubmission == nil
            ? currentTurnNumber
            : max(currentTurnNumber + 1, (conversation?.turnCount ?? currentTurnNumber) + 1)
        return Self(
            phase: phase,
            title: title,
            workspaceName: workspace.name,
            turnNumber: turnNumber,
            runPresentation: presentation,
            selectedAgent: selectedAgent?.agent,
            selectedAgentName: selectedAgent.map {
                run.displayName(forAgent: $0.agent, language: language)
            },
            recommendedAgent: run.recommendedAgent,
            acceptedAgent: run.finalizedAgent,
            acceptedAgentName: run.finalizedAgent.map {
                run.displayName(forAgent: $0, language: language)
            },
            candidateCount: run.agents.count,
            isLatestTurn: isLatestTurn
        )
    }
}

private extension PersistedPromptSubmission {
    var surfacePhase: ConversationSurfacePhase {
        if needsConnectionRecovery { return .recovery }
        switch status {
        case .pending, .retrying, .checkingReceipt, .waitingForPreviousTurn, .opening, .openingFailed, .receiptUnresolved:
            return .submitting
        case .awaitingConfirmation:
            return .recovery
        case .failed:
            return allowsManualRecovery ? .failed : .submitting
        }
    }

    var runPresentation: ConversationRunPresentation {
        switch status {
        case .pending, .retrying, .checkingReceipt, .waitingForPreviousTurn, .receiptUnresolved:
            return ConversationRunPresentation(
                phase: .preparing,
                label: "Queued",
                systemImage: "clock",
                tone: .neutral,
                animated: false
            )
        case .opening, .openingFailed:
            return ConversationRunPresentation(
                phase: .preparing,
                label: "Sent",
                systemImage: "bubble.left.and.bubble.right",
                tone: .neutral,
                animated: false
            )
        case .awaitingConfirmation:
            return ConversationRunPresentation(
                phase: .syncAttention,
                label: "Storage confirmation needed",
                systemImage: "externaldrive.badge.exclamationmark",
                tone: .warning,
                animated: false
            )
        case .failed:
            return ConversationRunPresentation(
                phase: allowsManualRecovery ? .failed : .syncAttention,
                label: allowsManualRecovery ? "Message not sent" : "Queued",
                systemImage: "exclamationmark.circle",
                tone: allowsManualRecovery ? .failure : .neutral,
                animated: false
            )
        }
    }
}
