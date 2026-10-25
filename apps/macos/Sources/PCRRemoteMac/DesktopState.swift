import Foundation

struct PromptEditorSelection: Codable, Equatable, Sendable {
    let location: Int
    let length: Int
    func range(in text: String) -> NSRange {
        let start = min(max(0, location), text.utf16.count)
        return NSRange(location: start, length: min(max(0, length), text.utf16.count - start))
    }
}

struct PersistedDesktopState: Codable {
    var workspaceDirectoriesByMachine: [String: [CachedWorkspaceDirectoryEntry]] = [:]
    var settingsByWorkspace: [String: RunSettings] = [:]
    var dirtySettingsFieldsByWorkspace: [String: Set<WorkspaceSettingField>] = [:]
    var defaultSettingsByMachine: [String: DefaultSettingsContextState] = [:]
    var promptDraftsByContext: [String: String] = [:]
    var promptSelectionsByContext: [String: PromptEditorSelection] = [:]
    var followUpQueuesByContext: [String: PersistedFollowUpQueue] = [:]
    var promptOutboxByWorkspace: [String: PersistedPromptSubmission] = [:]
    var acceptedPromptResultsByWorkspace: [String: PersistedAcceptedPromptResult] = [:]
    var deferredPromptsByWorkspace: [String: [PersistedDeferredPrompt]] = [:]
    // Only explicit user choices belong here. Earlier builds also persisted
    // whichever Agent happened to be visible while a Run was starting. That
    // made a completed Run reopen on Agent 1 instead of its recommendation.
    var explicitSelectedAgentsByContext: [String: Int] = [:]
    var selectedAgentContextsWhileWorking: Set<String> = []
    var selectedWorkspaceByMachine: [String: String] = [:]
    var selectedConversationByWorkspace: [String: String] = [:]
    var conversationTitlesByContext: [String: ConversationRecord] = [:]
    var feedbackOutboxByID: [String: PersistedFeedbackSubmission] = [:]

    init() {}

    private enum CodingKeys: String, CodingKey {
        case workspaceDirectoriesByMachine
        case settingsByWorkspace
        case dirtySettingsFieldsByWorkspace
        case defaultSettingsByMachine
        case dirtySettingsWorkspaces
        case promptDraftsByContext
        case promptSelectionsByContext
        case followUpQueuesByContext
        case promptOutboxByWorkspace
        case acceptedPromptResultsByWorkspace
        case deferredPromptsByWorkspace
        case explicitSelectedAgentsByContext
        case selectedAgentContextsWhileWorking
        // Kept only so older state files decode. Ambiguous automatic
        // selections from this key are intentionally not migrated.
        case selectedAgentsByContext
        case selectedWorkspaceByMachine
        case selectedConversationByWorkspace
        case conversationTitlesByContext
        case feedbackOutboxByID
        case followUpQueue
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workspaceDirectoriesByMachine = try container.decodeIfPresent(
            [String: [CachedWorkspaceDirectoryEntry]].self, forKey: .workspaceDirectoriesByMachine
        ) ?? [:]
        settingsByWorkspace = try container.decodeIfPresent(
            [String: RunSettings].self,
            forKey: .settingsByWorkspace
        ) ?? [:]
        dirtySettingsFieldsByWorkspace = try container.decodeIfPresent(
            [String: Set<WorkspaceSettingField>].self,
            forKey: .dirtySettingsFieldsByWorkspace
        ) ?? [:]
        dirtySettingsFieldsByWorkspace = dirtySettingsFieldsByWorkspace
            .filter { !$0.key.isEmpty }
            .mapValues(WorkspaceSettingField.normalized)
        defaultSettingsByMachine = try container.decodeIfPresent(
            [String: DefaultSettingsContextState].self,
            forKey: .defaultSettingsByMachine
        )?.filter { !$0.key.isEmpty } ?? [:]
        let legacyDirtyWorkspaces = try container.decodeIfPresent(
            Set<String>.self,
            forKey: .dirtySettingsWorkspaces
        ) ?? []
        for workspaceID in legacyDirtyWorkspaces where !workspaceID.isEmpty {
            if dirtySettingsFieldsByWorkspace[workspaceID] == nil {
                dirtySettingsFieldsByWorkspace[workspaceID] = WorkspaceSettingField.all
            }
        }
        promptDraftsByContext = try container.decodeIfPresent(
            [String: String].self,
            forKey: .promptDraftsByContext
        ) ?? [:]
        followUpQueuesByContext = try container.decodeIfPresent(
            [String: PersistedFollowUpQueue].self,
            forKey: .followUpQueuesByContext
        ) ?? [:]
        promptSelectionsByContext = try container.decodeIfPresent(
            [String: PromptEditorSelection].self, forKey: .promptSelectionsByContext
        ) ?? [:]
        if let legacyQueue = try container.decodeIfPresent(
            PersistedFollowUpQueue.self,
            forKey: .followUpQueue
        ), !legacyQueue.items.isEmpty {
            followUpQueuesByContext[legacyQueue.contextKey] = legacyQueue
        }
        followUpQueuesByContext = followUpQueuesByContext.filter {
            !$0.key.isEmpty && !$0.value.items.isEmpty
        }
        promptOutboxByWorkspace = try container.decodeIfPresent(
            [String: PersistedPromptSubmission].self,
            forKey: .promptOutboxByWorkspace
        )?.filter {
            !$0.key.isEmpty && !$0.value.workspaceID.isEmpty && !$0.value.prompt.isEmpty
        } ?? [:]
        acceptedPromptResultsByWorkspace = try container.decodeIfPresent(
            [String: PersistedAcceptedPromptResult].self,
            forKey: .acceptedPromptResultsByWorkspace
        )?.filter {
            !$0.key.isEmpty
                && !$0.value.submission.workspaceID.isEmpty
                && !$0.value.runID.isEmpty
        } ?? [:]
        deferredPromptsByWorkspace = try container.decodeIfPresent(
            [String: [PersistedDeferredPrompt]].self, forKey: .deferredPromptsByWorkspace
        ) ?? [:]
        explicitSelectedAgentsByContext = try container.decodeIfPresent(
            [String: Int].self,
            forKey: .explicitSelectedAgentsByContext
        )?.filter { !$0.key.isEmpty && $0.value > 0 } ?? [:]
        selectedAgentContextsWhileWorking = try container.decodeIfPresent(
            Set<String>.self,
            forKey: .selectedAgentContextsWhileWorking
        )?.filter { explicitSelectedAgentsByContext[$0] != nil } ?? []
        selectedWorkspaceByMachine = try container.decodeIfPresent(
            [String: String].self,
            forKey: .selectedWorkspaceByMachine
        )?.filter { !$0.key.isEmpty && !$0.value.isEmpty } ?? [:]
        selectedConversationByWorkspace = try container.decodeIfPresent(
            [String: String].self,
            forKey: .selectedConversationByWorkspace
        )?.filter { !$0.key.isEmpty && !$0.value.isEmpty } ?? [:]
        conversationTitlesByContext = try container.decodeIfPresent(
            [String: ConversationRecord].self, forKey: .conversationTitlesByContext
        ) ?? [:]
        feedbackOutboxByID = try container.decodeIfPresent(
            [String: PersistedFeedbackSubmission].self,
            forKey: .feedbackOutboxByID
        )?.filter {
            !$0.key.isEmpty && !$0.value.id.isEmpty && !$0.value.description.isEmpty
        } ?? [:]
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(workspaceDirectoriesByMachine, forKey: .workspaceDirectoriesByMachine)
        try container.encode(settingsByWorkspace, forKey: .settingsByWorkspace)
        try container.encode(
            dirtySettingsFieldsByWorkspace,
            forKey: .dirtySettingsFieldsByWorkspace
        )
        try container.encode(defaultSettingsByMachine, forKey: .defaultSettingsByMachine)
        try container.encode(promptDraftsByContext, forKey: .promptDraftsByContext)
        try container.encode(promptSelectionsByContext, forKey: .promptSelectionsByContext)
        try container.encode(followUpQueuesByContext, forKey: .followUpQueuesByContext)
        try container.encode(promptOutboxByWorkspace, forKey: .promptOutboxByWorkspace)
        try container.encode(
            acceptedPromptResultsByWorkspace,
            forKey: .acceptedPromptResultsByWorkspace
        )
        try container.encode(deferredPromptsByWorkspace, forKey: .deferredPromptsByWorkspace)
        try container.encode(
            explicitSelectedAgentsByContext,
            forKey: .explicitSelectedAgentsByContext
        )
        try container.encode(
            selectedAgentContextsWhileWorking,
            forKey: .selectedAgentContextsWhileWorking
        )
        try container.encode(selectedWorkspaceByMachine, forKey: .selectedWorkspaceByMachine)
        try container.encode(
            selectedConversationByWorkspace,
            forKey: .selectedConversationByWorkspace
        )
        try container.encode(feedbackOutboxByID, forKey: .feedbackOutboxByID)
        try container.encode(conversationTitlesByContext, forKey: .conversationTitlesByContext)
    }
}

struct DesktopStateSnapshot: Sendable {
    let notice: String?
    let settingsByWorkspace: [String: RunSettings]
    let dirtySettingsFieldsByWorkspace: [String: Set<WorkspaceSettingField>]
    let defaultSettingsByMachine: [String: DefaultSettingsContextState]
    let promptDraftsByContext: [String: String]
    let followUpQueuesByContext: [String: PersistedFollowUpQueue]
    let promptOutboxByWorkspace: [String: PersistedPromptSubmission]
    let acceptedPromptResultsByWorkspace: [String: PersistedAcceptedPromptResult]
    let selectedAgentsByContext: [String: Int]
    let selectedAgentContextsWhileWorking: Set<String>
    let selectedWorkspaceByMachine: [String: String]
    let selectedConversationByWorkspace: [String: String]
    let feedbackOutboxByID: [String: PersistedFeedbackSubmission]
    var conversationTitlesByContext: [String: ConversationRecord] = [:]
    var promptSelectionsByContext: [String: PromptEditorSelection] = [:]
    var deferredPromptsByWorkspace: [String: [PersistedDeferredPrompt]] = [:]
    var workspaceDirectoriesByMachine: [String: [CachedWorkspaceDirectoryEntry]] = [:]

    static let empty = DesktopStateSnapshot(
        notice: nil,
        settingsByWorkspace: [:],
        dirtySettingsFieldsByWorkspace: [:],
        defaultSettingsByMachine: [:],
        promptDraftsByContext: [:],
        followUpQueuesByContext: [:],
        promptOutboxByWorkspace: [:],
        acceptedPromptResultsByWorkspace: [:],
        selectedAgentsByContext: [:],
        selectedAgentContextsWhileWorking: [],
        selectedWorkspaceByMachine: [:],
        selectedConversationByWorkspace: [:],
        feedbackOutboxByID: [:]
    )
}

// Navigation metadata only: cached paths never grant current server permissions.
struct CachedWorkspaceDirectoryEntry: Codable, Equatable, Sendable {
    let workspaceID: String
    let path: String
    let name: String
    let lastRunID: String
    let lastResumeSessionID: String
}

enum BoundedQueueDrain {
    static func wait(
        for queue: DispatchQueue,
        timeout: TimeInterval
    ) -> Bool {
        guard timeout > 0 else { return false }
        let drained = DispatchSemaphore(value: 0)
        queue.async {
            drained.signal()
        }
        return drained.wait(timeout: .now() + timeout) == .success
    }
}

struct PersistedFollowUpQueue: Codable, Equatable, Sendable {
    var machineID: String? = nil
    let workspaceID: String
    let runID: String
    let items: [QueuedFollowUp]
    var deadline: Date? = nil

    var contextKey: String {
        guard let machineID else { return "\(workspaceID)\u{1f}\(runID)" }
        return MachineContextKey.run(
            machineID: machineID,
            workspaceID: workspaceID,
            runID: runID
        )
    }
}

enum DeferredPromptDestination: Codable, Equatable, Sendable {
    case newConversation
    case session(String)
    case run(String, agent: Int?)
    case conversation(String)
    case workspace
    case afterCommand(String)
}

struct PersistedDeferredPrompt: Codable, Equatable, Sendable {
    var item: QueuedFollowUp
    let machineID: String
    let workspaceID: String
    let workspacePath: String
    var conversationID: String?
    var destination: DeferredPromptDestination
    var originRunID: String? = nil
    var originAgent: Int? = nil
    var readyAt: Date? = nil
    var navigationConversationID: String? = nil
    var chainID: String? = nil

    var contextKey: String {
        MachineContextKey.workspace(machineID: machineID, workspaceID: workspaceID)
    }
}

enum PromptSubmissionStatus: String, Codable, Sendable {
    case pending
    case retrying
    case checkingReceipt = "checking_receipt"
    case receiptUnresolved = "receipt_unresolved"
    case waitingForPreviousTurn = "waiting_previous_turn"
    case opening
    case openingFailed = "opening_failed"
    case awaitingConfirmation = "awaiting_confirmation"
    case failed

    var conversationLabel: String {
        switch self {
        case .pending: return "Queued"
        // A retry is still the same user action. Exposing transport retries in
        // the conversation makes a normally recoverable send look like a new
        // or failed operation.
        case .retrying, .checkingReceipt, .receiptUnresolved, .waitingForPreviousTurn: return "Queued"
        case .opening: return "Sent"
        case .openingFailed: return "Sent"
        case .awaitingConfirmation: return "Storage confirmation needed"
        case .failed: return "Not sent"
        }
    }
}

struct PersistedPromptSubmission: Codable, Equatable, Sendable {
    static let connectionRecoveryCodes: Set<String> = ["unauthorized", "invalid_token", "relay_credentials_rejected",
        "relay_pairing_required", "controller_unpaired", "device_revoked"]
    let id: String
    var machineID: String? = nil
    let workspaceID: String
    let prompt: String
    let settings: RunSettings
    let sourceRunID: String?
    let sourceAgent: Int?
    let resumeSessionID: String
    var confirmLargeRun: Bool
    var attempt: Int
    var status: PromptSubmissionStatus
    var error: String
    var retryRequiresNewCommandID: Bool? = nil
    var errorCode: String? = nil
    var receiptUncertain: Bool? = nil
    var firstAttemptAt: Date? = nil
    var conversationID: String? = nil
    var receiptStoreID: String? = nil
    var storageEstimate: RunStorageEstimate? = nil
    var workspacePath: String? = nil
    var deliveryChainID: String? = nil

    var allowsManualRecovery: Bool {
        status == .failed && retryRequiresNewCommandID == true && !needsHistoryReceiptRecovery
    }

    var canReturnToDraft: Bool {
        allowsManualRecovery || (status == .awaitingConfirmation && receiptUncertain == false)
    }

    var needsConnectionRecovery: Bool {
        Self.connectionRecoveryCodes.contains(errorCode ?? "")
    }

    var conversationLabel: String {
        status == .failed && !allowsManualRecovery ? "Awaiting receipt" : status.conversationLabel
    }

    var presentationStatus: PromptSubmissionStatus {
        needsHistoryReceiptRecovery ? .checkingReceipt : status
    }

    var resumesAutomatically: Bool {
        if needsHistoryReceiptRecovery { return true }
        if [.pending, .retrying, .checkingReceipt, .receiptUnresolved, .waitingForPreviousTurn].contains(status) { return true }
        return status == .failed && receiptUncertain == true
            && !["command_in_doubt", "command_conflict", "journal_corrupt", "internal_error",
                 "pcr_error", "filesystem_error", "command_failed",
                 "receipt_expired", "receipt_date_unknown"].contains(errorCode ?? "")
    }

    var needsReceiptReconciliation: Bool {
        status == .failed && (receiptUncertain != false || needsHistoryReceiptRecovery)
    }

    private var needsHistoryReceiptRecovery: Bool {
        status == .failed && PromptSubmissionRetryPolicy.isLegacyHistoryCompatibilityFailure(
            code: errorCode, message: error
        )
    }

    var commandID: String {
        "\(id)-attempt-\(max(0, attempt))\(confirmLargeRun ? "-confirmed" : "")"
    }

    var contextKey: String {
        guard let machineID else { return workspaceID }
        return MachineContextKey.workspace(machineID: machineID, workspaceID: workspaceID)
    }

    mutating func prepareForManualRetry() {
        if retryRequiresNewCommandID == true {
            attempt += 1
        }
        status = .pending
        error = ""
        retryRequiresNewCommandID = nil
        errorCode = nil
        receiptUncertain = false
        firstAttemptAt = nil
    }
}

struct PersistedAcceptedPromptResult: Codable, Equatable, Sendable {
    var submission: PersistedPromptSubmission
    let runID: String
    let continuedSessionID: String
    let workspaceRevision: Int?

    var contextKey: String { submission.contextKey }
    var commandID: String { submission.commandID }
}

extension PersistedDesktopState {
    mutating func redirectDeferredPrompts(contextKey: String, from oldCommandID: String, to newCommandID: String) {
        guard oldCommandID != newCommandID else { return }
        deferredPromptsByWorkspace[contextKey] = deferredPromptsByWorkspace[contextKey]?.map {
            var queued = $0
            if queued.destination == .afterCommand(oldCommandID) { queued.destination = .afterCommand(newCommandID) }
            return queued
        }
    }

    mutating func recordAcceptedPromptResult(
        contextKey: String,
        expected: PersistedPromptSubmission,
        result: PersistedAcceptedPromptResult
    ) -> Bool {
        guard var current = promptOutboxByWorkspace[contextKey],
              current.id == expected.id,
              current.commandID == expected.commandID else {
            return false
        }
        promptOutboxByWorkspace.removeValue(forKey: contextKey)
        current.receiptUncertain = false
        current.status = .opening
        current.error = ""
        current.errorCode = nil
        current.retryRequiresNewCommandID = nil
        acceptedPromptResultsByWorkspace[contextKey] = PersistedAcceptedPromptResult(
            submission: current,
            runID: result.runID,
            continuedSessionID: result.continuedSessionID,
            workspaceRevision: result.workspaceRevision
        )
        return true
    }
}

enum DesktopStatePersistenceError: LocalizedError {
    case promptHandoff

    var errorDescription: String? {
        switch self {
        case .promptHandoff:
            return "The task started, but its local handoff could not be saved. Retrying safely."
        }
    }
}

enum FeedbackSubmissionStatus: String, Codable, Sendable {
    case pending
    case retrying
    case failed
}

struct PersistedFeedbackAttachment: Codable, Equatable, Sendable {
    let fileName: String
    let contentType: String
    let sha256: String
    let sizeBytes: Int
}

struct PersistedFeedbackSubmission: Codable, Equatable, Sendable, Identifiable {
    let id: String
    let kind: String
    let description: String
    let diagnostics: [String: String]
    let attachments: [PersistedFeedbackAttachment]
    var remoteFeedbackID: String
    var status: FeedbackSubmissionStatus
    var error: String
}

final class DesktopStateStore: @unchecked Sendable {
    static let shared = DesktopStateStore()
    private let legacyDefaultsKey = "parallel-codex.desktop-state.v1"
    private let stateFileName: String
    private let importsLegacyState: Bool
    private let directory: URL?

    init(windowID: UUID? = nil, directory: URL? = nil, writeQueue: DispatchQueue? = nil) {
        stateFileName = windowID.map { "window-\($0.uuidString).v3.json" } ?? "desktop-state.v3.json"
        importsLegacyState = windowID == nil
        self.directory = directory
        self.writeQueue = writeQueue ?? DispatchQueue(label: "com.parallelcodexrunner.mac.desktop-state", qos: .utility)
    }

    private final class UserDefaultsBox: @unchecked Sendable {
        let value: UserDefaults

        init(_ value: UserDefaults) {
            self.value = value
        }
    }

    private let writeQueue: DispatchQueue

    func loadSnapshot() -> DesktopStateSnapshot {
        writeQueue.sync { snapshot() }
    }

    @discardableResult
    func flushPendingWrites(timeout: TimeInterval = 0.5) -> Bool {
        BoundedQueueDrain.wait(for: writeQueue, timeout: timeout)
    }

    func loadSettings(defaults: UserDefaults = .standard) -> [String: RunSettings] {
        load(defaults: defaults).settingsByWorkspace
    }

    func loadPromptDrafts(defaults: UserDefaults = .standard) -> [String: String] {
        load(defaults: defaults).promptDraftsByContext
    }

    func loadDirtySettingFields(
        defaults: UserDefaults = .standard
    ) -> [String: Set<WorkspaceSettingField>] {
        load(defaults: defaults).dirtySettingsFieldsByWorkspace
    }

    func loadFollowUpQueues(
        defaults: UserDefaults = .standard
    ) -> [String: PersistedFollowUpQueue] {
        load(defaults: defaults).followUpQueuesByContext
    }

    func loadPromptOutbox(
        defaults: UserDefaults = .standard
    ) -> [String: PersistedPromptSubmission] {
        load(defaults: defaults).promptOutboxByWorkspace
    }

    func loadAcceptedPromptResults(
        defaults: UserDefaults = .standard
    ) -> [String: PersistedAcceptedPromptResult] {
        load(defaults: defaults).acceptedPromptResultsByWorkspace
    }

    func loadSelectedAgents(
        defaults: UserDefaults = .standard
    ) -> [String: Int] {
        load(defaults: defaults).explicitSelectedAgentsByContext
    }

    func loadFeedbackOutbox(
        defaults: UserDefaults = .standard
    ) -> [String: PersistedFeedbackSubmission] {
        load(defaults: defaults).feedbackOutboxByID
    }

    func saveSettings(
        _ settings: [String: RunSettings],
        defaults: UserDefaults = .standard
    ) {
        enqueueMutation(defaults: defaults) {
            $0.settingsByWorkspace = settings
        }
    }

    func savePromptDrafts(
        _ drafts: [String: String],
        defaults: UserDefaults = .standard
    ) {
        let filtered = drafts.filter { !$0.key.isEmpty && !$0.value.isEmpty }
        enqueueMutation(defaults: defaults) {
            $0.promptDraftsByContext = filtered
        }
    }

    func savePromptSelections(_ selections: [String: PromptEditorSelection]) {
        enqueueMutation(defaults: .standard) { state in
            state.promptSelectionsByContext = selections.filter { state.promptDraftsByContext[$0.key] != nil }
        }
    }

    func saveDirtySettingFields(
        _ fieldsByWorkspace: [String: Set<WorkspaceSettingField>],
        defaults: UserDefaults = .standard
    ) {
        let filtered = fieldsByWorkspace
            .filter { !$0.key.isEmpty && !$0.value.isEmpty }
            .mapValues(WorkspaceSettingField.normalized)
        enqueueMutation(defaults: defaults) {
            $0.dirtySettingsFieldsByWorkspace = filtered
        }
    }

    func saveDefaultSettings(
        _ states: [String: DefaultSettingsContextState],
        defaults: UserDefaults = .standard
    ) {
        let filtered = states.filter { !$0.key.isEmpty }
        enqueueMutation(defaults: defaults) {
            $0.defaultSettingsByMachine = filtered
        }
    }

    func saveFollowUpQueues(
        _ queues: [String: PersistedFollowUpQueue],
        defaults: UserDefaults = .standard
    ) {
        let filtered = queues.filter {
            !$0.key.isEmpty && !$0.value.items.isEmpty
        }
        enqueueMutation(defaults: defaults) {
            $0.followUpQueuesByContext = filtered
        }
    }

    func savePromptOutbox(
        _ submissions: [String: PersistedPromptSubmission],
        defaults: UserDefaults = .standard
    ) {
        let filtered = submissions.filter {
            !$0.key.isEmpty && !$0.value.prompt.isEmpty
        }
        enqueueMutation(defaults: defaults) {
            for (key, submission) in filtered {
                if let previous = $0.promptOutboxByWorkspace[key], previous.id == submission.id {
                    $0.redirectDeferredPrompts(contextKey: key, from: previous.commandID, to: submission.commandID)
                }
            }
            $0.promptOutboxByWorkspace = filtered
        }
    }

    func recordPromptSubmission(_ submission: PersistedPromptSubmission) async -> Bool {
        await withCheckedContinuation { continuation in
            writeQueue.async {
                var state = self.load(defaults: .standard)
                if let previous = state.promptOutboxByWorkspace[submission.contextKey], previous.id == submission.id {
                    state.redirectDeferredPrompts(contextKey: submission.contextKey, from: previous.commandID, to: submission.commandID)
                }
                state.promptOutboxByWorkspace[submission.contextKey] = submission
                continuation.resume(returning: self.save(state, defaults: .standard))
            }
        }
    }

    func updatePromptSubmission(_ replacement: PersistedPromptSubmission, expected: PersistedPromptSubmission) {
        enqueueMutation(defaults: .standard) { state in
            guard let current = state.promptOutboxByWorkspace[expected.contextKey],
                  current.id == expected.id, current.commandID == expected.commandID,
                  replacement.id == expected.id, replacement.contextKey == expected.contextKey else { return }
            state.redirectDeferredPrompts(contextKey: expected.contextKey, from: expected.commandID, to: replacement.commandID)
            state.promptOutboxByWorkspace[expected.contextKey] = replacement
        }
    }

    func updateAcceptedPromptResult(_ replacement: PersistedAcceptedPromptResult) {
        enqueueMutation(defaults: .standard) { state in
            guard let current = state.acceptedPromptResultsByWorkspace[replacement.contextKey],
                  current.commandID == replacement.commandID, current.runID == replacement.runID else { return }
            state.acceptedPromptResultsByWorkspace[replacement.contextKey] = replacement
        }
    }

    func forgetPromptDelivery(workspaceKey: String) {
        enqueueMutation(defaults: .standard) {
            $0.promptOutboxByWorkspace.removeValue(forKey: workspaceKey)
            $0.acceptedPromptResultsByWorkspace.removeValue(forKey: workspaceKey)
            $0.deferredPromptsByWorkspace.removeValue(forKey: workspaceKey)
        }
    }

    func updateStorageConfirmation(
        _ expected: PersistedPromptSubmission,
        with replacement: PersistedPromptSubmission
    ) -> Bool {
        writeQueue.sync {
            var state = load(defaults: .standard)
            guard let current = state.promptOutboxByWorkspace[expected.contextKey],
                  current.commandID == expected.commandID,
                  current.status == .awaitingConfirmation,
                  replacement.id == current.id,
                  replacement.contextKey == current.contextKey else { return false }
            state.promptOutboxByWorkspace[expected.contextKey] = replacement
            state.redirectDeferredPrompts(contextKey: expected.contextKey, from: expected.commandID, to: replacement.commandID)
            return save(state, defaults: .standard)
        }
    }

    func returnPromptSubmissionToDraft(
        _ submission: PersistedPromptSubmission,
        draftContext: String
    ) -> Bool {
        writeQueue.sync {
            var state = load(defaults: .standard)
            guard !draftContext.isEmpty,
                  let current = state.promptOutboxByWorkspace[submission.contextKey],
                  current.commandID == submission.commandID,
                  current.canReturnToDraft,
                  state.promptDraftsByContext[draftContext, default: ""].isEmpty
                    || state.promptDraftsByContext[draftContext] == submission.prompt else { return false }
            state.promptDraftsByContext[draftContext] = submission.prompt
            state.promptOutboxByWorkspace.removeValue(forKey: submission.contextKey)
            state.deferredPromptsByWorkspace[submission.contextKey] = state.deferredPromptsByWorkspace[submission.contextKey]?.map {
                var queued = $0
                if queued.destination == .afterCommand(submission.commandID) { queued.item.paused = true }
                return queued
            }
            return save(state, defaults: .standard)
        }
    }

    func saveAcceptedPromptResults(
        _ results: [String: PersistedAcceptedPromptResult],
        defaults: UserDefaults = .standard
    ) {
        let filtered = results.filter {
            !$0.key.isEmpty && !$0.value.runID.isEmpty
        }
        enqueueMutation(defaults: defaults) {
            $0.acceptedPromptResultsByWorkspace = filtered
        }
    }

    @discardableResult
    func recordAcceptedPromptResult(
        contextKey: String,
        expected: PersistedPromptSubmission,
        result: PersistedAcceptedPromptResult,
        defaults: UserDefaults = .standard
    ) -> Bool {
        writeQueue.sync {
            var state = load(defaults: defaults)
            guard state.recordAcceptedPromptResult(
                contextKey: contextKey,
                expected: expected,
                result: result
            ) else {
                return false
            }
            return save(state, defaults: defaults)
        }
    }

    @discardableResult
    func consumeAcceptedPromptResult(_ expected: PersistedAcceptedPromptResult, conversationID: String? = nil) -> Bool {
        writeQueue.sync {
            var state = load(defaults: .standard)
            guard let current = state.acceptedPromptResultsByWorkspace[expected.contextKey],
                  current.commandID == expected.commandID,
                  current.runID == expected.runID else { return false }
            state.acceptedPromptResultsByWorkspace.removeValue(forKey: expected.contextKey)
            state.deferredPromptsByWorkspace[expected.contextKey] = state.deferredPromptsByWorkspace[expected.contextKey]?.map {
                var queued = $0
                if queued.destination == .afterCommand(expected.commandID) {
                    queued.destination = .run(expected.runID, agent: nil)
                    queued.conversationID = conversationID ?? queued.conversationID
                }
                return queued
            }
            return save(state, defaults: .standard)
        }
    }

    func appendDeferredPrompt(_ value: PersistedDeferredPrompt) async -> [PersistedDeferredPrompt]? {
        await withCheckedContinuation { continuation in
            writeQueue.async {
                var state = self.load(defaults: .standard)
                var value = value
                if let chain = value.chainID {
                    let owner = state.promptOutboxByWorkspace[value.contextKey]
                        ?? state.acceptedPromptResultsByWorkspace[value.contextKey]?.submission
                    if let owner, owner.deliveryChainID == chain || owner.commandID == chain {
                        value.destination = .afterCommand(owner.commandID)
                    } else if let tail = state.deferredPromptsByWorkspace[value.contextKey]?.last(where: { $0.chainID == chain }) {
                        value.destination = tail.destination
                        value.conversationID = tail.conversationID
                    }
                }
                state.deferredPromptsByWorkspace[value.contextKey, default: []].append(value)
                continuation.resume(returning: self.save(state, defaults: .standard)
                    ? state.deferredPromptsByWorkspace[value.contextKey] : nil)
            }
        }
    }

    func replaceDeferredPrompts(key: String, expected: [PersistedDeferredPrompt],
                               replacement: [PersistedDeferredPrompt]) async -> Bool {
        await withCheckedContinuation { continuation in
            writeQueue.async {
                var state = self.load(defaults: .standard)
                guard state.deferredPromptsByWorkspace[key, default: []] == expected else {
                    continuation.resume(returning: false)
                    return
                }
                state.deferredPromptsByWorkspace[key] = replacement.isEmpty ? nil : replacement
                continuation.resume(returning: self.save(state, defaults: .standard))
            }
        }
    }

    func promoteDeferredPrompt(_ expected: PersistedDeferredPrompt,
                               submission: PersistedPromptSubmission) async -> [PersistedDeferredPrompt]? {
        await withCheckedContinuation { continuation in
            writeQueue.async {
                var state = self.load(defaults: .standard)
                let key = expected.contextKey
                guard let items = state.deferredPromptsByWorkspace[key], items.first == expected,
                      state.promptOutboxByWorkspace[key] == nil, state.acceptedPromptResultsByWorkspace[key] == nil,
                      submission.contextKey == key, submission.id == expected.item.id.uuidString.lowercased(),
                      submission.workspacePath == expected.workspacePath,
                      submission.prompt == expected.item.prompt else {
                    continuation.resume(returning: nil)
                    return
                }
                let remaining = items.dropFirst().map { value in
                    var value = value
                    if (expected.chainID != nil && value.chainID == expected.chainID)
                        || (expected.chainID == nil && value.destination == expected.destination && value.conversationID == expected.conversationID) {
                        value.destination = .afterCommand(submission.commandID)
                    }
                    return value
                }
                state.promptOutboxByWorkspace[key] = submission
                state.deferredPromptsByWorkspace[key] = remaining.isEmpty ? nil : remaining
                continuation.resume(returning: self.save(state, defaults: .standard) ? remaining : nil)
            }
        }
    }

    func saveAgentSelections(
        _ agents: [String: Int],
        selectedWhileWorking: Set<String>,
        defaults: UserDefaults = .standard
    ) {
        let filtered = agents.filter {
            !$0.key.isEmpty && $0.value > 0
        }
        enqueueMutation(defaults: defaults) {
            $0.explicitSelectedAgentsByContext = filtered
            $0.selectedAgentContextsWhileWorking = selectedWhileWorking.filter {
                filtered[$0] != nil
            }
        }
    }

    func saveConversationNavigation(
        selectedWorkspaceByMachine: [String: String],
        selectedConversationByWorkspace: [String: String],
        defaults: UserDefaults = .standard
    ) {
        let workspaces = selectedWorkspaceByMachine.filter {
            !$0.key.isEmpty && !$0.value.isEmpty
        }
        let conversations = selectedConversationByWorkspace.filter {
            !$0.key.isEmpty && !$0.value.isEmpty
        }
        enqueueMutation(defaults: defaults) {
            $0.selectedWorkspaceByMachine = workspaces
            $0.selectedConversationByWorkspace = conversations
        }
    }

    func saveFeedbackOutbox(
        _ submissions: [String: PersistedFeedbackSubmission],
        defaults: UserDefaults = .standard
    ) {
        let filtered = submissions.filter {
            !$0.key.isEmpty && !$0.value.id.isEmpty && !$0.value.description.isEmpty
        }
        enqueueMutation(defaults: defaults) {
            $0.feedbackOutboxByID = filtered
        }
    }

    func saveConversationTitles(_ titles: [String: ConversationRecord]) {
        enqueueMutation(defaults: .standard) {
            $0.conversationTitlesByContext = titles
        }
    }

    func saveWorkspaceDirectory(_ entries: [CachedWorkspaceDirectoryEntry], machineID: String) {
        enqueueMutation(defaults: .standard) {
            $0.workspaceDirectoriesByMachine[machineID] = entries
        }
    }

    private func snapshot() -> DesktopStateSnapshot {
        let state = load(defaults: .standard)
        return DesktopStateSnapshot(
            notice: nil,
            settingsByWorkspace: state.settingsByWorkspace,
            dirtySettingsFieldsByWorkspace: state.dirtySettingsFieldsByWorkspace,
            defaultSettingsByMachine: state.defaultSettingsByMachine,
            promptDraftsByContext: state.promptDraftsByContext,
            followUpQueuesByContext: state.followUpQueuesByContext,
            promptOutboxByWorkspace: state.promptOutboxByWorkspace,
            acceptedPromptResultsByWorkspace: state.acceptedPromptResultsByWorkspace,
            selectedAgentsByContext: state.explicitSelectedAgentsByContext,
            selectedAgentContextsWhileWorking: state.selectedAgentContextsWhileWorking,
            selectedWorkspaceByMachine: state.selectedWorkspaceByMachine,
            selectedConversationByWorkspace: state.selectedConversationByWorkspace,
            feedbackOutboxByID: state.feedbackOutboxByID,
            conversationTitlesByContext: state.conversationTitlesByContext,
            promptSelectionsByContext: state.promptSelectionsByContext,
            deferredPromptsByWorkspace: state.deferredPromptsByWorkspace,
            workspaceDirectoriesByMachine: state.workspaceDirectoriesByMachine
        )
    }

    private func load(defaults: UserDefaults) -> PersistedDesktopState {
        if let data = try? Data(contentsOf: stateFileURL),
           let state = try? JSONDecoder().decode(PersistedDesktopState.self, from: data) {
            return state
        }
        if FileManager.default.fileExists(atPath: stateFileURL.path) {
            quarantineUnreadableState()
        }
        guard importsLegacyState,
              let legacyData = defaults.data(forKey: legacyDefaultsKey),
              let legacyState = try? JSONDecoder().decode(
                PersistedDesktopState.self,
                from: legacyData
              ) else {
            return PersistedDesktopState()
        }
        if save(legacyState, defaults: defaults) {
            if importsLegacyState { defaults.removeObject(forKey: legacyDefaultsKey) }
        }
        return legacyState
    }

    @discardableResult
    private func save(
        _ state: PersistedDesktopState,
        defaults: UserDefaults
    ) -> Bool {
        guard let data = try? JSONEncoder().encode(state) else { return false }
        do {
            let directory = stateFileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try data.write(to: stateFileURL, options: .atomic)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: stateFileURL.path
            )
            defaults.removeObject(forKey: legacyDefaultsKey)
            return true
        } catch {
            return false
        }
    }

    private func enqueueMutation(
        defaults: UserDefaults,
        _ mutation: @escaping @Sendable (inout PersistedDesktopState) -> Void
    ) {
        let defaultsBox = UserDefaultsBox(defaults)
        writeQueue.async {
            var state = self.load(defaults: defaultsBox.value)
            mutation(&state)
            self.save(state, defaults: defaultsBox.value)
        }
    }

    private func quarantineUnreadableState() {
        let suffix = "unreadable-\(Int(Date().timeIntervalSince1970))-\(UUID().uuidString.prefix(8))"
        let recoveryURL = stateFileURL.appendingPathExtension(suffix)
        try? FileManager.default.moveItem(at: stateFileURL, to: recoveryURL)
        try? FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: recoveryURL.path
        )
    }

    private var stateFileURL: URL {
        if let directory { return directory.appendingPathComponent(stateFileName) }
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        return base
            .appendingPathComponent("Parallel Codex", isDirectory: true)
            .appendingPathComponent(stateFileName)
    }
}
