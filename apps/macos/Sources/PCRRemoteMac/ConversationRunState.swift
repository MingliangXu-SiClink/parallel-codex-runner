import Combine
import Foundation
import SwiftUI

/// High-frequency run-time state extracted from AppModel so that
/// settings, connection, and other low-change views are not re-rendered
/// on every streaming tick, 800 ms workspace poll, or 1-second countdown tick.
///
/// AppModel holds `let convState = ConversationRunState()` and exposes
/// forwarding computed properties for backward compatibility with its
/// own extension methods.  Views that need reactive updates on this
/// state declare `@EnvironmentObject var convState: ConversationRunState`.
@MainActor
final class ConversationRunState: ObservableObject {

    // MARK: - Conversation list (updated every ~800 ms poll)

    @Published var workspaceConversations: [WorkspaceConversationSummary] = []
    @Published var selectedConversationID: String?
    @Published var conversationIndexLoading = false

    // MARK: - Agent selection

    @Published var selectedAgent: Int?
    @Published var pendingAgentSelection: Int?

    // MARK: - Transcript content (updated during agent streaming)

    @Published var transcripts: [Int: AgentTranscript] = [:]
    @Published var loadingAgentKeys: Set<String> = []
    @Published var resumeSessions: [ResumeSession] = []
    @Published var resumeHistory: [ResumeHistoryEntry] = []

    // MARK: - Work process (updated on expand / collapse)

    @Published var workProcess = WorkProcessState()
    @Published var expandedAgentWorkContexts: Set<String> = []
    @Published var expandedQuestionRequests: Set<String> = []
    @Published var collapsedAgentWorkContexts: Set<String> = []
    @Published var continuationSelections: [String: AgentContinuationSelection] = [:]

    // MARK: - Follow-up countdown (updated every second while active)

    @Published var followUpSecondsRemaining: Int?
    @Published var followUpQueue: [QueuedFollowUp] = []
    @Published var daemonFollowUpSnapshot: RemoteFollowUpQueue?

    // MARK: - Operation state (updated per prompt submission)

    @Published var isRunOperationInProgress = false
    @Published var runOperationsByContext: [String: RunOperationKind] = [:]
    @Published var pendingPromptSubmission: PersistedPromptSubmission?
    @Published var runOperationIssues: [String: RunOperationIssue] = [:]
    @Published var storageConfirmationMessage: String?
}
