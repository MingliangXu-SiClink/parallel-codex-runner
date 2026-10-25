import Foundation

struct ConversationNavigationTarget: Equatable, Sendable {
    let requestID: Int
    let machineID: String
    let workspaceID: String
    let conversationID: String
    let runID: String?
    var followsLatest: Bool = false

    func belongsTo(machineID: String, workspaceID: String?, requestID: Int) -> Bool {
        self.machineID == machineID && self.workspaceID == workspaceID
            && self.requestID == requestID
    }
}

struct RemoteTurnTransition: Equatable, Sendable {
    let requestID: Int
    let machineID: String
    let workspaceID: String
    let conversationID: String?

    func matches(machineID: String, workspaceID: String?, conversationID: String?) -> Bool {
        self.machineID == machineID
            && self.workspaceID == workspaceID
            && self.conversationID == conversationID
    }
}

enum ConversationContextDestination: Equatable, Sendable {
    case workspace
    case resumeSession
    case applicationExit
}

enum ConversationContextTransitionPlan: Equatable, Sendable {
    case proceed
    case applyVisibleAgent(Int)
    case blocked(String)

    static func make(
        destination: ConversationContextDestination,
        run: RunSummary?,
        selectedAgent: RunAgentSummary?,
        isLatestWorkspaceRun: Bool,
        syncBack: Bool,
        hasPendingSubmission: Bool,
        hasRunOperation: Bool
    ) -> Self {
        // Workspaces are independent conversation surfaces. Navigating between
        // them must not finalize, stop, or wait on work in the surface being left.
        if destination == .workspace || destination == .applicationExit { return .proceed }
        if hasPendingSubmission {
            return .blocked("Wait for the message to finish sending before changing conversations.")
        }
        if hasRunOperation {
            return .blocked("Wait for the current conversation action to finish.")
        }
        guard isLatestWorkspaceRun,
              let run,
              run.finalizedAgent == nil else {
            return .proceed
        }
        if run.hasUnresolvedFinalization {
            return .blocked(
                "Confirm the workspace sync status and finish cleanup before changing conversations."
            )
        }
        if let selectedAgent, selectedAgent.mayAccept {
            if syncBack { return .applyVisibleAgent(selectedAgent.agent) }
            if destination == .resumeSession {
                return .blocked(
                    "Turn on Sync Back or discard the current turn before changing conversations."
                )
            }
            return .proceed
        }
        if destination == .resumeSession, run.isWorking || run.isReviewable {
            return .blocked(
                run.isWorking
                    ? "Choose a completed Agent before changing conversations."
                    : "Use or discard the current turn before changing conversations."
            )
        }
        return .proceed
    }

    func blockedReason(language: AppLanguage) -> String? {
        guard case .blocked(let reason) = self else { return nil }
        let chinese: String = switch reason {
        case "Wait for the message to finish sending before changing conversations.":
            "请等待消息发送完成后再切换对话。"
        case "Wait for the current conversation action to finish.":
            "请等待当前对话操作完成。"
        case "Confirm the workspace sync status and finish cleanup before changing conversations.":
            "请先确认工作区同步状态并完成清理。"
        case "Turn on Sync Back or discard the current turn before changing conversations.":
            "请开启同步回原工作区，或先放弃当前轮次。"
        case "Choose a completed Agent before changing conversations.":
            "请先选择一个已完成的 Agent。"
        case "Use or discard the current turn before changing conversations.":
            "请先采用或放弃当前轮次。"
        default:
            reason
        }
        return language.text(chinese, reason)
    }
}
