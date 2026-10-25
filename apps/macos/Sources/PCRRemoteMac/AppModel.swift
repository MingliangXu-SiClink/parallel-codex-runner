import AppKit
import Combine
import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var appLanguage = AppLanguage.load()
    @Published var appColorMode = AppAppearanceStore.loadColorMode()
    @Published var appAccentColor = AppAppearanceStore.loadAccentColor()
    @Published var appTextSize = AppAppearanceStore.loadTextSize()
    @Published var workExpandedByDefault = AppAppearanceStore.loadWorkExpandedByDefault()
    private static let workExpansionPreferenceDidChange = Notification.Name("ParallelCodex.WorkExpansionPreferenceDidChange")
    private var workExpansionPreferenceObservation: AnyCancellable?
    private var conversationTrashObservation: AnyCancellable?
    let conversationTrash: ConversationTrashStore
    @Published var completionNotificationsEnabled = AppAppearanceStore
        .loadCompletionNotificationsEnabled()
    @Published var macOSUpdateAvailable = MacOSPreparedUpdate.restore()?.update
    @Published var macOSUpdateChecked = false
    @Published var macOSUpdateChecking = false
    @Published var macOSUpdateDownloading = false
    @Published var macOSUpdateError: String?
    @Published var macOSPreparedUpdate = MacOSPreparedUpdate.restore()
    @Published var macOSUpdateReminderDismissed = false
    @Published var macOSUpdateRestarting = false
    var automaticMacOSUpdateCheckStarted = false
    weak var applicationDelegate: ParallelCodexAppDelegate?
    @Published var daemonOnline = false
    @Published var statusText = "Connecting"
    @Published var connectionLoading = true
    @Published var connectionErrorMessage: String?
    @Published var liveConversationWarning: String?
    var liveConversationNeedsAction = false
    @Published var workspaces: [RemoteWorkspace] = []
    @Published var devices: [RemoteDevice] = []
    @Published var machines: [RelayMachine] = []
    @Published var selectedMachineID: String?
    @Published var machineSelectionInProgress = false
    @Published var machineSelectionTargetID: String?
    @Published var machinePairingPresented = false
    @Published var machinePairingCode = ""
    @Published var machinePairingInProgress = false
    @Published var machinePairingError: String?
    @Published var selectedWorkspaceID: String?
    @Published var workspaceContextLoadingID: String?
    @Published var remoteTurnTransition: RemoteTurnTransition?
    var remoteTurnTransitionInProgress: Bool {
        remoteTurnTransition?.matches(
            machineID: currentMachineContextID,
            workspaceID: selectedWorkspaceID,
            conversationID: selectedConversationID
        ) == true
    }
    @Published var workspaceRevision = 0
    @Published var workspaceDirectoryIsCached = false
    @Published var workspaceDirectoryLoaded = false
    var workspaceDirectoriesByMachine: [String: [CachedWorkspaceDirectoryEntry]] = [:]
    @Published var browserPage: DirectoryPage?
    @Published var browserRoots: FileSystemRoots?
    @Published var browserPresented = false
    @Published var browserLoading = false
    @Published var browserLoadingNextPage = false
    @Published var browserPaginationError: String?
    @Published var browserAdding = false
    @Published var browserError: String?
    @Published var showHiddenFiles = false
    @Published var errorNotice: AppErrorNotice?
    var errorMessage: String? {
        get { errorNotice?.message(language: appLanguage) }
        set { errorNotice = newValue.map { .message($0) } }
    }
    @Published var relay = RelayStatus(
        configured: false,
        paired: false,
        baseUrl: nil,
        macDeviceId: nil,
        deviceName: nil,
        pairingId: nil,
        pairingUri: nil,
        pairingExpiresAt: nil,
        userId: nil,
        trustedAndroidDevices: 0,
        trustedControllers: nil
    )
    @Published var pairingPresented = false
    @Published var pairingIsLoading = false
    @Published var pairingError: String?
    @Published var relayLink: RelayLinkInfo?

    let promptDraft = PromptDraftState()
    var prompt: String {
        get { promptDraft.text }
        set { promptDraft.setText(newValue) }
    }
    @Published var settings = RunSettings()
    @Published var settingsPresented = false
    @Published var defaultSettings = RunSettings()
    @Published var defaultSettingsLoading = false
    @Published var defaultSettingsSaving = false
    @Published var defaultSettingsError: String?
    @Published var defaultSettingsOptionsError: String?
    @Published var defaultPrimaryModelOptions: CodexModelOptions?
    @Published var defaultSynthesisModelOptions: CodexModelOptions?
    @Published var storageSummary: StorageSummary?
    @Published var storageSummaryLoading = false
    @Published var storageSummaryError: String?
    @Published var storageCleaningRunIDsByMachine: [String: String] = [:]
    private var activeRunValue: RunSummary?
    let activeRunMetrics = ActiveRunMetrics()
    var activeRun: RunSummary? {
        get { activeRunValue }
        set {
            guard activeRunValue != newValue else { return }
            if activeRunValue?.readerState != newValue?.readerState {
                objectWillChange.send()
            }
            activeRunValue = newValue
            activeRunMetrics.update(newValue)
        }
    }
    @Published var workspaceConversations: [WorkspaceConversationSummary] = []
    @Published var selectedConversationID: String?
    @Published var conversationSearchMatchesByRun: [String: ConversationTextMatch] = [:]
    @Published var conversationSearchLoading = false
    @Published var conversationSearchError: String?
    @Published var conversationSearchNavigation: ConversationSearchNavigation?
    var conversationSearchGeneration = UUID()
    @Published var conversationNavigationTarget: ConversationNavigationTarget?
    @Published var historicalConversationTurn: WorkspaceRunViewKey?
    var openingConversationID: String? { conversationNavigationTarget?.conversationID }
    @Published var conversationRenameRequest: ConversationRenameRequest?
    @Published var conversationDeleteRequest: ConversationDeleteRequest?
    @Published var lastDeletedConversation: DeletedConversation?
    var conversationTitlesTask: Task<Void, Never>?
    @Published var promptFocusRequestSequence = 0
    @Published var conversationIndexLoading = false
    @Published var selectedAgent: Int?
    @Published var pendingAgentSelection: Int?
    @Published var transcripts: [Int: AgentTranscript] = [:]
    @Published var loadingAgentKeys: Set<String> = []
    @Published var resumeSessions: [ResumeSession] = []
    @Published var resumeHistory: [ResumeHistoryEntry] = []
    let modelOptionsCache = CodexModelOptionsCache()
    @Published var modelOptionsRevision = 0
    @Published var loadingPrimaryModelOptionsContexts: Set<CodexModelOptionsContext> = []
    var primaryModelOptionsLoading: Bool {
        loadingPrimaryModelOptionsContexts.contains(primaryModelOptionsContext)
    }
    var primaryModelOptionsContext: CodexModelOptionsContext {
        CodexModelOptionsContext(machineID: currentMachineContextID, model: settings.model)
    }
    var synthesisModelOptionsContext: CodexModelOptionsContext {
        CodexModelOptionsContext(machineID: currentMachineContextID, model: settings.synthesisModel)
    }
    var primaryModelOptions: CodexModelOptions? {
        modelOptionsCache.options(for: primaryModelOptionsContext)
    }
    var synthesisModelOptions: CodexModelOptions? {
        modelOptionsCache.options(for: synthesisModelOptionsContext)
    }
    var codexModelCatalog: [CodexOption]? {
        modelOptionsCache.models(for: currentMachineContextID)
    }
    @Published var modelOptionsError: String?
    @Published var isRunOperationInProgress = false
    @Published var runOperationsByContext: [String: RunOperationKind] = [:]
    @Published var pendingPromptSubmission: PersistedPromptSubmission?
    @Published var storageConfirmationMessage: String?
    @Published var diffPresented = false
    @Published var resultsInspectorVisible = false {
        didSet {
            guard oldValue != resultsInspectorVisible, !AppLaunchMode.isUISmokeTest else { return }
            UserDefaults.standard.set(resultsInspectorVisible, forKey: resultsInspectorVisibilityKey)
        }
    }
    @Published var diffContext: ReviewContext?
    @Published var diffSelectedFile: String?
    @Published var comparisonRequest: AgentComparisonRequest?
    @Published var diffLoading = false
    @Published var diffText = ""
    @Published var diffTitle = "Changes"
    @Published var agentDiffSummaries: [String: AgentDiffSummary] = [:]
    let agentReviewCache = AgentReviewCache()
    @Published var agentDiffSummaryLoadingKeys: Set<String> = []
    var agentDiffSummaryTerminalKeys: Set<String> = []
    @Published var workspaceFileLoading = false
    @Published var workspaceFileProgress: Double?
    // Panel visibility follows opening/closing, not the request's transient state.
    @Published var workspaceFilePresented = false
    var reviewColumnPresented: Bool { diffPresented || workspaceFilePresented }
    let fileComparisonCache = FileComparisonCache()
    @Published var workspaceFileIssue: WorkspaceFileIssue? {
        didSet {
            if workspaceFileIssue != nil && !workspaceFilePresented { workspaceFilePresented = true }
        }
    }
    var workspaceFileRequestID = UUID()
    @Published var runOperationIssues: [String: RunOperationIssue] = [:]
    @Published var workspaceDocument: WorkspaceFileDocument? {
        didSet {
            if workspaceDocument != nil && !workspaceFilePresented { workspaceFilePresented = true }
        }
    }
    @Published var workspaceDocumentHistory: [WorkspaceFileDocument] = []
    @Published var addAgentsPresented = false
    @Published var additionalAgentCount = 1
    @Published var queuePresented = false
    @Published var followUpQueue: [QueuedFollowUp] = []
    @Published var queueIssue: String? {
        didSet { if queueIssue == nil { queueIssueRecovery = nil } }
    }
    var queueIssueRecovery: (workspaceKey: String, proof: FollowUpQueueReplacementProof, message: String)?
    @Published var queueEditRequest: QueuedFollowUp?
    @Published var daemonFollowUpSnapshot: RemoteFollowUpQueue?
    @Published var followUpSecondsRemaining: Int?
    var promptHistoryEntries: [String] {
        get { promptDraft.historyEntries }
        set { promptDraft.setHistoryEntries(newValue) }
    }
    var promptHistoryIndex: Int {
        get { promptDraft.historyIndex }
        set { promptDraft.setHistoryIndex(newValue) }
    }
    @Published var expandedAgentWorkContexts: Set<String> = []
    @Published var expandedQuestionRequests: Set<String> = []
    @Published var workProcess = WorkProcessState()
    var workOperationTasks: [String: Task<Void, Never>] = [:]
    var workOperationRequestIDs: [String: UUID] = [:]
    var workOperationRequests: [String: WorkOperationRecord] = [:]
    @Published var collapsedAgentWorkContexts: Set<String> = []

    let client: PcrdClient
    let questionAnswers: AgentAnswerOutbox
    let resultsInspectorVisibilityKey: String
    let desktopStateStore: DesktopStateStore
    let feedback: FeedbackModel
    let domainRepositories: ClientDomainRepositories
    let transcriptScrollMemory: TranscriptScrollMemory
    var loadedWorkspaceID: String?
    var conversationIndexTask: Task<Void, Never>?
    var conversationIndexRequestID = 0
    var conversationNavigationRequestID = 0
    var workspaceEntryDefaultNavigationRequestID: Int?
    var runPollingTasks: [WorkspaceRunViewKey: Task<Void, Never>] = [:]
    var runPollingTokens: [WorkspaceRunViewKey: UUID] = [:]
    var runSummariesByContext: [WorkspaceRunViewKey: RunSummary] {
        get { domainRepositories.turns.runSummariesByContext }
        set { domainRepositories.turns.runSummariesByContext = newValue }
    }
    var conversationPreloadTask: Task<Void, Never>?
    var conversationPreloadToken: UUID?
    var conversationReadTasks: [WorkspaceRunViewKey: Task<Void, Never>] = [:]
    var conversationReadTokens: [WorkspaceRunViewKey: UUID] = [:]
    var conversationAgentReadTokens: [String: UUID] = [:]
    var conversationHistoryTasks: [WorkspaceRunViewKey: Task<String?, Never>] = [:]
    var conversationHistoryTokens: [WorkspaceRunViewKey: UUID] = [:]
    var workspaceContextTask: Task<Void, Never>?
    var preloadedConversationKeys: Set<WorkspaceRunViewKey> = []
    var warmedWorkingConversationKeys: Set<WorkspaceRunViewKey> = []
    var conversationPreloadRetryAfter: [WorkspaceRunViewKey: Date] = [:]
    var followUpTask: Task<Void, Never>?
    var daemonFollowUpMonitorToken: UUID?
    var followUpCoordinatorTask: Task<Void, Never>?
    var backgroundFollowUpKeys: Set<String> = []
    var daemonFollowUpQueueConfirmedMachines: Set<String> = []
    var daemonFollowUpSnapshotsByWorkspace: [String: RemoteFollowUpQueue] = [:]
    var daemonFollowUpRevisionsByWorkspace: [String: Int] = [:]
    var followUpAdmissionChecksByWorkspace: [String: Int] = [:]
    var followUpAdmissionCapabilityChecksByMachine: [String: Date] = [:]
    var followUpEnqueueTask: Task<Bool, Never>?
    var followUpEnqueueCount = 0
    var synthesisSettingsTasks: [String: Task<Void, Never>] = [:]
    var synthesisSettingsTaskTokens: [String: UUID] = [:]
    var defaultSettingsNeedsSave = false
    var defaultSettingsBaseline: RunSettings?
    var dirtyDefaultSettingFields: Set<WorkspaceSettingField> = []
    var promptDraftSaveTask: Task<Void, Never>?
    var workspaceFileTask: Task<Void, Never>?
    var defaultSettingsLoaded = false
    var storageSummaryRequestID = 0
    var defaultPrimaryOptionsRequestID = 0
    var defaultSynthesisOptionsRequestID = 0
    var resumeHistoryRequestID = 0
    var defaultModelOptionErrors: [String: String] = [:]
    var modelOptionErrors: [String: String] = [:]
    var defaultSettingsStates: [String: DefaultSettingsContextState] {
        get { domainRepositories.machines.defaultSettingsStates }
        set { domainRepositories.machines.defaultSettingsStates = newValue }
    }
    var defaultSettingsSaveTasks: [String: Task<Void, Never>] = [:]
    var defaultSettingsSaveRequestIDs: [String: Int] = [:]
    var defaultSettingsAutoRetryAfter: [String: Date] = [:]
    var settingsPersistenceTasks: [String: Task<Void, Never>] = [:]
    var settingsSaveRequestIDs: [String: Int] = [:]
    var dirtySettingsFieldsByWorkspace: [String: Set<WorkspaceSettingField>] {
        get { domainRepositories.workspaces.dirtySettingsFields }
        set { domainRepositories.workspaces.dirtySettingsFields = newValue }
    }
    var reportedSettingsFailures: Set<String> = []
    var loadingAgents: Set<String> = []
    var agentDetailLoadTokens: [String: UUID] = [:]
    var agentAnswerLoadTokens: [String: UUID] = [:]
    var agentAnswerReadRetries: [String: AgentAnswerReadRetry] = [:]
    var agentSelectionRequestID = 0
    @Published var continuationSelections: [String: AgentContinuationSelection] = [:]
    var continuationSelectionTasks: [String: Task<Void, Never>] = [:]
    var continuationSelectionTaskIDs: [String: UUID] = [:]
    var promptPreparationContexts: Set<String> = []
    var settingsByWorkspace: [String: RunSettings] {
        get { domainRepositories.workspaces.settingsByWorkspace }
        set { domainRepositories.workspaces.settingsByWorkspace = newValue }
    }
    var pendingStorageOperation: PendingStorageOperation?
    var followUpRunID: String?
    var followUpDeadline: Date?
    var promptHistoryDraft = ""
    var promptHistoryContext = ""
    var promptDraftContext = ""
    var promptSelectionsByContext: [String: PromptEditorSelection] = [:]
    var promptDraftsByContext: [String: String] {
        get { domainRepositories.conversations.promptDraftsByContext }
        set { domainRepositories.conversations.promptDraftsByContext = newValue }
    }
    var persistedFollowUpQueues: [String: PersistedFollowUpQueue] {
        get { domainRepositories.conversations.persistedFollowUpQueues }
        set { domainRepositories.conversations.persistedFollowUpQueues = newValue }
    }
    var promptOutboxByWorkspace: [String: PersistedPromptSubmission] {
        get { domainRepositories.conversations.promptOutboxByWorkspace }
        set { domainRepositories.conversations.promptOutboxByWorkspace = newValue }
    }
    var acceptedPromptResultsByWorkspace: [String: PersistedAcceptedPromptResult] {
        get { domainRepositories.conversations.acceptedPromptResultsByWorkspace }
        set { domainRepositories.conversations.acceptedPromptResultsByWorkspace = newValue }
    }
    var deferredPromptsByWorkspace: [String: [PersistedDeferredPrompt]] {
        get { domainRepositories.conversations.deferredPromptsByWorkspace }
        set {
            objectWillChange.send()
            domainRepositories.conversations.deferredPromptsByWorkspace = newValue
        }
    }
    var deferredPromptTasks: [String: Task<Void, Never>] = [:]
    var acceptedPromptSettlementTasks: [String: Task<Void, Never>] = [:]
    var deferredPromptSaves: Set<String> = []
    private var promptSubmissionTasks: [String: Task<Void, Never>] = [:]
    private var storageReceiptLookups: Set<String> = []
    private var promptSubmissionTaskCommandIDs: [String: String] = [:]
    var acceptedPromptAdoptionsInFlight: Set<String> = []
    var selectedAgentsByContext: [String: Int] {
        get { domainRepositories.candidates.selectedAgentsByContext }
        set { domainRepositories.candidates.selectedAgentsByContext = newValue }
    }
    var selectedAgentContextsWhileWorking: Set<String> {
        get { domainRepositories.candidates.selectedAgentContextsWhileWorking }
        set { domainRepositories.candidates.selectedAgentContextsWhileWorking = newValue }
    }
    var selectedWorkspaceByMachine: [String: String] {
        get { domainRepositories.machines.selectedWorkspaceByMachine }
        set { domainRepositories.machines.selectedWorkspaceByMachine = newValue }
    }
    var selectedConversationByWorkspace: [String: String] {
        get { domainRepositories.workspaces.selectedConversationByWorkspace }
        set { domainRepositories.workspaces.selectedConversationByWorkspace = newValue }
    }
    var runViewStateStore: WorkspaceRunViewStateStore {
        domainRepositories.turns.runViewStateStore
    }
    var transcriptProjectionInput: TranscriptProjectionInput?
    var transcriptProjectionBlocks: [TranscriptBlock] = []
    var transcriptProjectionGeneration: UInt64 = 0
    var transcriptTimelineProjectionInput: TranscriptTimelineProjectionInput?
    var transcriptTimelineProjectionBlocks: [TranscriptBlock] = []
    var transcriptTimelineProjectionGeneration: UInt64 = 0
    var conversationTimelineItemsProjectionInput: ConversationTimelineItemsProjectionInput?
    var conversationTimelineItemsProjectionItems: [ConversationTimelineItem] = []
    var conversationTimelineItemsProjectionGeneration: UInt64 = 0
    var presentationProjectionSequence: UInt64 = 0
    var transcriptPresentationKey: TranscriptPresentationKey?
    var transcriptPresentationCache = TranscriptPresentationCache()
    var notifiedRunIDs: Set<String> = []
    var observedWorkingRuns: [String: String] = [:]
    var runMonitoringInitializedMachines: Set<String> = []
    var daemonMonitoredRemoteRuns: Set<String> = []
    var nextBackgroundRunNotificationRefresh = Date.distantPast
    var reportedRunLoadFailures: Set<String> = []
    var unavailableRunContexts: Set<WorkspaceRunViewKey> = []
    var workspaceContextGeneration = 0
    var workspaceSelectionRequestID = 0
    var refreshRequestID = 0
    var appliedRefreshRequestID = 0
    var systemCapabilities: SystemCapabilities?
    var systemCapabilitiesByMachine: [String: SystemCapabilities] = [:]
    var relayBootstrapTask: Task<Void, Error>?
    var relayBootstrapToken: UUID?
    var machineDirectoryRefreshTask: Task<Void, Never>?
    var machineDirectoryRefreshToken: UUID?
    var localDaemonMachineID: String?
    var nextMachineDirectoryRefresh = Date.distantPast
    var relayBootstrapRetryAfter = Date.distantPast
    var directoryRequestID = 0
    var directoryPaginationRequestID: UUID?
    var activeRunOperationKeys: Set<String> = []
    var activeWorkspaceOperationIDs: Set<String> = []
    var pendingStorageOperationsByWorkspace: [String: PendingStorageOperation] = [:]
    var storageConfirmationMessagesByWorkspace: [String: String] = [:]
    let maxDiffDisplayBytes = 2_000_000

    func setAppLanguage(_ language: AppLanguage) {
        guard appLanguage != language else { return }
        AppLanguage.save(language)
        appLanguage = language
    }

    func setAppColorMode(_ mode: AppColorMode) {
        guard appColorMode != mode else { return }
        AppAppearanceStore.save(mode)
        appColorMode = mode
    }

    func setAppAccentColor(_ color: AppAccentColor) {
        guard appAccentColor != color else { return }
        AppAppearanceStore.save(color)
        appAccentColor = color
    }

    func setAppTextSize(_ size: AppTextSize) {
        guard appTextSize != size else { return }
        AppAppearanceStore.save(size)
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            appTextSize = size
        }
    }

    func setWorkExpandedByDefault(_ expanded: Bool) {
        guard workExpandedByDefault != expanded else { return }
        AppAppearanceStore.saveWorkExpandedByDefault(expanded)
        applyWorkExpandedByDefault(expanded)
        workProcess.save()
        NotificationCenter.default.post(name: Self.workExpansionPreferenceDidChange, object: nil)
    }

    private func applyWorkExpandedByDefault(_ expanded: Bool) {
        guard workExpandedByDefault != expanded else { return }
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            // Changing the global preference reapplies it to remembered sections,
            // without discarding opened operation details or their cached output.
            expandedAgentWorkContexts.removeAll()
            collapsedAgentWorkContexts.removeAll()
            workProcess.sections.removeAll()
            workProcess.revision &+= 1
            workExpandedByDefault = expanded
        }
    }

    func setCompletionNotificationsEnabled(_ enabled: Bool) {
        guard completionNotificationsEnabled != enabled else { return }
        AppAppearanceStore.saveCompletionNotificationsEnabled(enabled)
        completionNotificationsEnabled = enabled
        if enabled { DesktopNotifier.prepare() }
    }

    var storageCleaningRunID: String? {
        storageCleaningRunIDsByMachine[currentMachineContextID]
    }

    struct RunOperationContext {
        let machineID: String
        let target: PcrdRequestTarget
        let workspaceID: String
        let runID: String
        let settings: RunSettings

        var key: String {
            MachineContextKey.run(
                machineID: machineID,
                workspaceID: workspaceID,
                runID: runID
            )
        }
    }

    struct WorkspaceOperationContext {
        let machineID: String
        let target: PcrdRequestTarget
        let workspaceID: String

        var key: String {
            MachineContextKey.workspace(
                machineID: machineID,
                workspaceID: workspaceID
            )
        }
    }

    func isCurrentWorkspaceOperation(_ context: WorkspaceOperationContext) -> Bool {
        isCurrentMachine(context.machineID)
            && selectedWorkspaceID == context.workspaceID
    }

    struct BackgroundFollowUpContext {
        let key: String
        let machineID: String
        let target: PcrdRequestTarget
        let workspaceID: String
        let runID: String
    }

    enum PendingStorageOperation {
        case start(workspaceID: String, prompt: String, settings: RunSettings)
        case continuation(
            workspaceID: String,
            runID: String,
            agent: Int,
            prompt: String,
            settings: RunSettings
        )
        case more(workspaceID: String, runID: String, count: Int)

        var workspaceID: String {
            switch self {
            case .start(let workspaceID, _, _),
                 .continuation(let workspaceID, _, _, _, _),
                 .more(let workspaceID, _, _):
                return workspaceID
            }
        }
    }

    init(client: PcrdClient = .standard, windowID: UUID? = nil,
         conversationTrash: ConversationTrashStore = .shared,
         desktopStateStore: DesktopStateStore? = nil,
         runViewStateStore: WorkspaceRunViewStateStore? = nil,
         questionAnswers: AgentAnswerOutbox? = nil) {
        self.client = client
        self.questionAnswers = questionAnswers ?? .shared
        self.conversationTrash = conversationTrash
        resultsInspectorVisibilityKey = "parallel-codex.results-inspector-visible."
            + (windowID?.uuidString ?? "main")
        self.desktopStateStore = desktopStateStore
            ?? windowID.map { DesktopStateStore(windowID: $0) } ?? .shared
        domainRepositories = ClientDomainRepositories(windowID: windowID, runViewStateStore: runViewStateStore)
        transcriptScrollMemory = TranscriptScrollMemory(storageKey: windowID.map {
            "pcr.transcript-scroll-memory.v1.\($0.uuidString)"
        } ?? "pcr.transcript-scroll-memory.v1")
        feedback = FeedbackModel(client: client, desktopStateStore: self.desktopStateStore)
        resultsInspectorVisible = !AppLaunchMode.isUISmokeTest
            && UserDefaults.standard.bool(forKey: resultsInspectorVisibilityKey)
        // This file is deliberately small (navigation, drafts, and pending
        // actions). Load it before the first frame so a restored conversation
        // never renders an empty composer and then replaces it with a draft.
        // Network and transcript hydration remain asynchronous.
        applyDesktopStateSnapshot(
            AppLaunchMode.isUISmokeTest ? .empty : self.desktopStateStore.loadSnapshot()
        )
        workExpansionPreferenceObservation = NotificationCenter.default
            .publisher(for: Self.workExpansionPreferenceDidChange)
            .sink { [weak self] _ in
                self?.applyWorkExpandedByDefault(AppAppearanceStore.loadWorkExpandedByDefault())
            }
        conversationTrashObservation = conversationTrash.changes.sink { [weak self] change in
            self?.applyConversationTrashChange(change)
        }
    }

    func stopWindowObservation() {
        continuationSelectionTasks.values.forEach { $0.cancel() }
        continuationSelectionTasks.removeAll()
        continuationSelectionTaskIDs.removeAll()
        for key in Array(workOperationTasks.keys) { cancelWorkOperation(key) }
        for task in runPollingTasks.values { task.cancel() }
        runPollingTasks.removeAll()
        cancelConversationPreload()
        workspaceContextTask?.cancel()
        conversationIndexTask?.cancel()
        conversationTitlesTask?.cancel()
        workspaceFileTask?.cancel()
        followUpTask?.cancel()
        followUpCoordinatorTask?.cancel()
    }

    func refreshVisibleContext() async {
        guard !isRunOperationInProgress else { return }
        await refresh(reportErrors: false)
        guard daemonOnline, !isRunOperationInProgress else { return }
        let visibleSummaryChanged = activeRun.map { run in
            selectedWorkspace.map {
                $0.lastRunId == run.runId
                    && (($0.runStatus != nil && $0.runStatus != run.status)
                        || ($0.runWorkerActive != nil
                            && $0.runWorkerActive != run.workerActive))
            } ?? false
        } ?? false
        if let runID = activeRun?.runId,
           activeRun?.hasUnresolvedFinalization == true || visibleSummaryChanged {
            do {
                try await loadRun(runID, loadDetailPages: 4)
                resumeEventPollingIfNeeded()
            } catch {
                // Periodic refreshes are best-effort; explicit actions still report errors.
            }
        }
        let now = Date()
        let refreshBackgroundRuns = now >= nextBackgroundRunNotificationRefresh
        if refreshBackgroundRuns {
            nextBackgroundRunNotificationRefresh = now.addingTimeInterval(15)
        }
        await refreshRunNotifications(includeBackgroundRuns: refreshBackgroundRuns)
    }

    func selectWorkspace(_ workspaceID: String?) async {
        guard let workspaceID,
              workspaceID != selectedWorkspaceID,
              let targetWorkspace = workspaces.first(where: {
                  $0.workspaceId == workspaceID
              }) else { return }
        workspaceSelectionRequestID += 1
        let selectionRequestID = workspaceSelectionRequestID
        let machineID = currentMachineContextID
        cacheVisibleRunViewState()
        rememberCurrentSettings()
        let cachedRecords = runViewStateStore.cachedRecords(
            machineID: machineID,
            workspaceID: workspaceID
        )
        guard selectionRequestID == workspaceSelectionRequestID,
              currentMachineContextID == machineID,
              workspaces.contains(where: { $0.workspaceId == workspaceID }) else {
            if selectionRequestID == workspaceSelectionRequestID,
               workspaceContextLoadingID == workspaceID {
                workspaceContextLoadingID = nil
            }
            return
        }
        suspendPromptHistoryContext()
        errorMessage = nil
        selectedWorkspaceID = workspaceID
        loadedWorkspaceID = nil
        resetRunView()
        workspaceEntryDefaultNavigationRequestID = conversationNavigationRequestID
        let destinationWorkspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        settings = resolvedWorkspaceSettings(
            cached: settingsByWorkspace[destinationWorkspaceKey],
            remote: targetWorkspace.settings,
            preserving: dirtySettingsFieldsByWorkspace[destinationWorkspaceKey] ?? []
        )
        preparePromptHistoryContext()
        workspaceContextLoadingID = workspaceID
        rememberWorkspaceSelection()
        installCachedWorkspaceConversation(
            cachedRecords,
            workspace: targetWorkspace,
            machineID: machineID
        )
        restoreVisiblePromptSubmission()
        refreshVisibleRunOperationState()
        if activeRun != nil || !resumeHistory.isEmpty { workspaceContextLoadingID = nil }
        let generation = workspaceContextGeneration
        workspaceContextTask?.cancel()
        workspaceContextTask = Task { [weak self] in
            guard let self, selectionRequestID == self.workspaceSelectionRequestID,
                  self.isCurrentMachine(machineID), self.selectedWorkspaceID == workspaceID else { return }
            // Recover this destination's body before slow global revalidation.
            await self.restoreCachedWorkspaceConversation(workspace: targetWorkspace,
                machineID: machineID, generation: generation)
            guard self.isCurrentWorkspaceContext(workspaceID, generation: generation) else { return }
            // Destination reads must not queue behind the global health,
            // workspace directory, or native-session index refresh.
            let destination = self.reconciledWorkspaceHead(targetWorkspace, machineID: machineID)
            if !destination.lastRunId.isEmpty {
                self.scheduleConversationRead(workspace: destination,
                    key: WorkspaceRunViewKey(machineID: machineID, workspaceID: workspaceID,
                        runID: destination.lastRunId), target: self.client.currentTarget())
            }
            await self.refresh()
        }
    }

    func removeWorkspace(
        _ workspaceID: String,
        discardReviewableResults: Bool = false
    ) async {
        let machineID = currentMachineContextID
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        guard !activeWorkspaceOperationIDs.contains(workspaceKey) else {
            errorMessage = appLanguage.text(
                "请等待当前操作完成后再移除工作区。",
                "Wait for the current operation to finish before removing a workspace."
            )
            return
        }
        guard let workspace = workspaces.first(where: { $0.workspaceId == workspaceID }) else {
            return
        }
        guard !workspace.removalLooksActive else {
            errorMessage = appLanguage.text(
                "请先停止此工作区正在运行的任务。",
                "Stop this workspace's running task before removing it."
            )
            return
        }
        guard !persistedFollowUpQueues.values.contains(where: {
            MachineContextKey.normalizeMachineID($0.machineID) == machineID
                && $0.workspaceID == workspaceID
                && !$0.items.isEmpty
        }) else {
            errorMessage = appLanguage.text(
                "请先处理或删除此工作区的排队提问。",
                "Apply or remove the queued follow-ups before removing this workspace."
            )
            return
        }
        guard promptOutboxByWorkspace[workspaceKey] == nil else {
            errorMessage = appLanguage.text(
                "请先完成、编辑或取消待发送的提问。",
                "Finish, edit, or cancel the pending prompt before removing this workspace."
            )
            return
        }
        guard acceptedPromptResultsByWorkspace[workspaceKey] == nil else {
            errorMessage = appLanguage.text(
                "请先打开刚刚启动的任务。",
                "Open the task that just started before removing this workspace."
            )
            return
        }
        await performWorkspaceOperation(workspaceID) { context in
            var run: RunSummary?
            if !workspace.lastRunId.isEmpty {
                do {
                    run = try await self.client.call(
                        target: context.target,
                        "turns.get",
                        body: GetRunBody(
                            runId: workspace.lastRunId,
                            includeEvents: false,
                            cursor: 0,
                            eventLimit: 1
                        )
                    )
                } catch let error as PcrdClientError where error.isMissingRun(workspace.lastRunId) {
                    // Removing a bookmark does not require historical artifacts to
                    // survive retention. workspaces.remove still checks live safety.
                    run = nil
                }
            }
            if let run {
                guard !run.isWorking else {
                    self.reportWorkspaceOperationFailure(
                        self.appLanguage.text(
                            "请先停止此工作区正在运行的任务。",
                            "Stop this workspace's running task before removing it."
                        ), context: context
                    )
                    return
                }
                guard !run.hasUnresolvedFinalization else {
                    self.reportWorkspaceOperationFailure(
                        self.appLanguage.text(
                            "请先确认工作区同步状态并完成清理。",
                            "Confirm the workspace sync status and finish cleanup before removing this workspace."
                        ), context: context
                    )
                    return
                }
                if run.isReviewable {
                    guard discardReviewableResults else {
                        self.reportWorkspaceOperationFailure(
                            self.appLanguage.text(
                                "请确认可以丢弃尚未采用的 Agent 结果。",
                                "Confirm that unaccepted Agent results may be discarded before removing this workspace."
                            ), context: context
                        )
                        return
                    }
                    let _: RunSummary = try await self.client.call(
                        target: context.target,
                        "runs.discard",
                        body: RunDiscardBody(
                            runId: run.runId,
                            keepWorkspaces: false,
                            waitSeconds: 20
                        ),
                        timeoutSeconds: 60
                    )
                }
            }
            let mutation: WorkspaceRemoval = try await self.client.call(
                target: context.target,
                "workspaces.remove",
                body: SelectWorkspaceBody(workspaceId: context.workspaceID)
            )
            guard mutation.removedWorkspaceId == workspaceID else {
                throw PcrdClientError.invalidResponse
            }
            if self.isCurrentMachine(context.machineID) {
                // Commit the acknowledged mutation before any best-effort refresh;
                // an older in-flight health response must not resurrect the row.
                self.refreshRequestID += 1
                self.workspaceRevision = mutation.revision
            }
            self.settingsByWorkspace.removeValue(forKey: workspaceKey)
            self.dirtySettingsFieldsByWorkspace.removeValue(forKey: workspaceKey)
            self.desktopStateStore.saveSettings(self.settingsByWorkspace)
            self.desktopStateStore.saveDirtySettingFields(self.dirtySettingsFieldsByWorkspace)
            if self.isCurrentMachine(context.machineID),
               self.selectedWorkspaceID == workspaceID {
                self.suspendPromptHistoryContext()
            }
            self.forgetDesktopState(for: workspaceID, machineID: context.machineID)
            let retainedDirectory = (self.workspaceDirectoriesByMachine[context.machineID] ?? [])
                .filter { $0.workspaceID != workspaceID }
            self.workspaceDirectoriesByMachine[context.machineID] = retainedDirectory
            self.desktopStateStore.saveWorkspaceDirectory(retainedDirectory, machineID: context.machineID)
            self.forgetCachedRunViews(
                machineID: context.machineID,
                workspaceID: workspaceID
            )
            self.domainRepositories.removeWorkspace(workspaceID)
            self.pendingStorageOperationsByWorkspace.removeValue(forKey: workspaceKey)
            self.storageConfirmationMessagesByWorkspace.removeValue(forKey: workspaceKey)
            if self.isCurrentMachine(context.machineID),
               self.selectedWorkspaceID == workspaceID {
                self.loadedWorkspaceID = nil
                self.resetRunView()
            }
            if self.isCurrentMachine(context.machineID) {
                self.workspaces.removeAll { $0.workspaceId == workspaceID }
                self.selectedConversationByWorkspace.removeValue(forKey: workspaceKey)
                if self.selectedWorkspaceID == workspaceID {
                    self.selectedWorkspaceID = mutation.selectedWorkspaceId.flatMap { candidate in
                        self.workspaces.contains { $0.workspaceId == candidate } ? candidate : nil
                    } ?? self.workspaces.first?.workspaceId
                    self.selectedWorkspaceByMachine[context.machineID] = self.selectedWorkspaceID
                }
                self.desktopStateStore.saveConversationNavigation(
                    selectedWorkspaceByMachine: self.selectedWorkspaceByMachine,
                    selectedConversationByWorkspace: self.selectedConversationByWorkspace
                )
                await self.refresh()
            }
        }
    }

    func submitPrompt() async {
        let originalDraft = prompt
        let value = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, let workspace = selectedWorkspace else { return }
        let plan = promptSubmissionPlan
        guard canSubmitPrompt else {
            if !plan.isAvailable { errorMessage = plan.contextText(language: appLanguage) }
            return
        }
        let preparationKey = "\(currentMachineContextID):\(workspace.workspaceId):\(selectedConversationID ?? ""):\(originalDraft)"
        guard promptPreparationContexts.insert(preparationKey).inserted else { return }
        defer { promptPreparationContexts.remove(preparationKey) }

        if shouldDeferPrompt {
            await saveDeferredPrompt(value, originalDraft: originalDraft)
            return
        }

        if plan == .enqueue {
            let target = client.currentTarget()
            let sessionID = settings.resumeSessionID
            if await enqueueFollowUp(value) {
                await recordPromptHistory(value, workspaceID: workspace.workspaceId,
                                          sessionID: sessionID, target: target)
            }
            return
        }
        let sourceRunID: String?
        let sourceAgent: Int?
        switch plan {
        case .continueFrom(let runID, let agent, _, _):
            sourceRunID = runID
            sourceAgent = agent
        case .start:
            sourceRunID = nil
            sourceAgent = nil
        case .loading, .enqueue, .blocked:
            return
        }
        let snapshot = plan.settingsForSubmission(settings, run: activeRun)
        let target = client.currentTarget()
        let resumesConversation = sourceAgent != nil
            || usableResumeSessionID(snapshot.resumeSessionID) != nil
        let optimisticHistory = resumesConversation
            ? (activeRun == nil ? resumeHistory : conversationHistory(for: sourceAgent))
            : []
        let submission = PersistedPromptSubmission(
            id: UUID().uuidString.lowercased(),
            machineID: target.machineID,
            workspaceID: workspace.workspaceId,
            prompt: value,
            settings: snapshot,
            sourceRunID: sourceRunID,
            sourceAgent: sourceAgent,
            resumeSessionID: snapshot.resumeSessionID,
            confirmLargeRun: false,
            attempt: 0,
            status: .pending,
            error: "",
            receiptUncertain: false,
            conversationID: selectedConversationID,
            workspacePath: workspace.path
        )
        guard promptOutboxByWorkspace[submission.contextKey] == nil else { return }
        promptOutboxByWorkspace[submission.contextKey] = submission
        guard await desktopStateStore.recordPromptSubmission(submission) else {
            promptOutboxByWorkspace.removeValue(forKey: submission.contextKey)
            if isPromptSubmissionVisible(submission) {
                errorMessage = appLanguage.text(
                    "暂时无法保存消息，内容仍在输入框中。请检查本机可用空间。",
                    "The message could not be saved. Your draft is unchanged. Check this Mac's available storage."
                )
            }
            return
        }
        if isPromptSubmissionVisible(submission) {
            pendingPromptSubmission = submission
            if prompt == originalDraft { updatePrompt("") }
        }
        submitPendingPrompt(submission, optimisticHistory: optimisticHistory)
    }

    @discardableResult
    func submitPendingPrompt(
        _ submission: PersistedPromptSubmission,
        optimisticHistory: [ResumeHistoryEntry]? = nil
    ) -> Task<Void, Never>? {
        let submissionKey = submission.contextKey
        let commandID = submission.commandID
        if let existingTask = promptSubmissionTasks[submissionKey] {
            if promptSubmissionTaskCommandIDs[submissionKey] == commandID { return existingTask }
            existingTask.cancel()
        }
        guard let target = requestTarget(for: submission.machineID) else { return nil }
        let workspaceID = submission.workspaceID
        promptSubmissionTaskCommandIDs[submissionKey] = commandID
        promptSubmissionTasks[submissionKey] = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.promptSubmissionTaskCommandIDs[submissionKey] == commandID {
                    self.promptSubmissionTasks[submissionKey] = nil
                    self.promptSubmissionTaskCommandIDs[submissionKey] = nil
                }
            }
            var transientFailureCount = 0
            while !Task.isCancelled {
                guard self.promptOutboxByWorkspace[submissionKey]?.commandID == commandID else {
                    return
                }
                let current = self.promptOutboxByWorkspace[submissionKey] ?? submission
                let previouslyUncertain = current.receiptUncertain != false || current.needsReceiptReconciliation
                let firstAttemptAt = self.promptOutboxByWorkspace[submissionKey]?.firstAttemptAt
                do {
                    var recoveredResponse: RunStartResponse?
                    let withinReplayWindow = firstAttemptAt.map {
                        Date().timeIntervalSince($0) < 29 * 24 * 60 * 60
                    } ?? false
                    var safeToReplay = !previouslyUncertain || (
                        withinReplayWindow && ([.pending, .retrying, .waitingForPreviousTurn].contains(current.status)
                            || current.needsConnectionRecovery)
                    )
                    if previouslyUncertain {
                        do {
                            let receipt = try await self.promptCommandReceipt(current, target: target)
                            guard self.promptOutboxByWorkspace[submissionKey]?.commandID == commandID else { return }
                            switch receipt.status {
                            case "completed":
                                guard let result = receipt.result else { throw PcrdClientError.invalidResponse }
                                recoveredResponse = result
                            case let status where ["retryable", "processing"].contains(status)
                                && receipt.recoveryOwnedByDaemon == true:
                                var waiting = current
                                waiting.status = .waitingForPreviousTurn
                                waiting.receiptUncertain = true
                                waiting.retryRequiresNewCommandID = false
                                waiting.errorCode = "pcr_operation_pending"
                                waiting.error = ""
                                self.savePromptSubmission(waiting)
                                try await Task.sleep(for: .seconds(2))
                                continue
                            case "retryable":
                                // An authenticated receipt for this exact original
                                // command proves retryability; age only limits an
                                // absent receipt, not a still-present preflight record.
                                safeToReplay = true
                            case "rejected":
                                var rejected = current
                                rejected.status = .failed
                                rejected.receiptUncertain = false
                                rejected.retryRequiresNewCommandID = true
                                rejected.errorCode = receipt.error?.code
                                rejected.error = receipt.error?.message ?? ""
                                self.savePromptSubmission(rejected)
                                return
                            case "not_found":
                                safeToReplay = safeToReplay && current.receiptStoreID != nil
                                    && current.receiptStoreID == receipt.receiptStoreId
                            default:
                                safeToReplay = false
                            }
                        } catch PcrdClientError.remote(let code, _) where ["unknown_action", "unsupported_action"].contains(code) {
                            if !safeToReplay {
                                var held = current
                                held.status = .receiptUnresolved
                                held.errorCode = withinReplayWindow ? "receipt_lookup_unsupported"
                                    : (firstAttemptAt == nil ? "receipt_date_unknown" : "receipt_expired")
                                self.savePromptSubmission(held)
                                try await Task.sleep(for: .seconds(60))
                                continue
                            }
                        }
                    }
                    if recoveredResponse == nil && !safeToReplay {
                        var held = current
                        held.status = .receiptUnresolved
                        held.receiptUncertain = true
                        held.retryRequiresNewCommandID = false
                        if !withinReplayWindow {
                            held.errorCode = firstAttemptAt == nil ? "receipt_date_unknown" : "receipt_expired"
                        }
                        self.savePromptSubmission(held)
                        try await Task.sleep(for: .seconds(60))
                        continue
                    }
                    if !previouslyUncertain {
                        // A daemon upgrade can keep the same address. Persist
                        // the current receipt store before this command can run.
                        let capabilities = try await self.verifiedSystemCapabilities(
                            forceRefresh: true, target: target
                        )
                        guard !Task.isCancelled,
                              self.promptOutboxByWorkspace[submissionKey]?.commandID == commandID else { return }
                        var attempting = current
                        attempting.receiptUncertain = true
                        attempting.firstAttemptAt = firstAttemptAt ?? Date()
                        attempting.receiptStoreID = capabilities.commandReceiptStoreId
                        guard await self.desktopStateStore.recordPromptSubmission(attempting) else { return }
                        self.promptOutboxByWorkspace[submissionKey] = attempting
                    }
                    let response: RunStartResponse
                    if let recoveredResponse {
                        response = recoveredResponse
                    } else if let runID = submission.sourceRunID,
                       let agent = submission.sourceAgent {
                        response = try await self.requestContinuation(
                            runID: runID,
                            agent: agent,
                            prompt: submission.prompt,
                            settings: submission.settings,
                            confirmLargeRun: submission.confirmLargeRun,
                            commandID: commandID,
                            target: target
                        )
                    } else {
                        response = try await self.requestStartRun(
                            workspaceID: workspaceID,
                            prompt: submission.prompt,
                            settings: submission.settings,
                            confirmLargeRun: submission.confirmLargeRun,
                            commandID: commandID,
                            target: target
                        )
                    }
                    guard !Task.isCancelled,
                          self.promptOutboxByWorkspace[submissionKey]?.commandID == commandID else {
                        return
                    }
                    if response.status == "confirmation_required" {
                        var waiting = self.promptOutboxByWorkspace[submissionKey] ?? submission
                        waiting.status = .awaitingConfirmation
                        waiting.receiptUncertain = false
                        waiting.storageEstimate = response.storage
                        waiting.error = response.message
                            ?? "This run needs more disk space than usual."
                        self.savePromptSubmission(waiting)
                        return
                    }
                    guard let acceptedRunID = response.runId ?? response.run?.runId else {
                        throw PcrdClientError.invalidResponse
                    }
                    var acceptedSubmission = self.promptOutboxByWorkspace[submissionKey] ?? submission
                    acceptedSubmission.status = .opening
                    acceptedSubmission.error = ""
                    acceptedSubmission.errorCode = nil
                    acceptedSubmission.retryRequiresNewCommandID = nil
                    acceptedSubmission.receiptUncertain = false
                    let acceptedResult = PersistedAcceptedPromptResult(
                        submission: acceptedSubmission,
                        runID: acceptedRunID,
                        continuedSessionID: response.continuedFrom?.sessionId ?? "",
                        workspaceRevision: response.workspaceRevision
                    )
                    guard self.desktopStateStore.recordAcceptedPromptResult(
                        contextKey: submissionKey,
                        expected: submission,
                        result: acceptedResult
                    ) else {
                        throw DesktopStatePersistenceError.promptHandoff
                    }
                    self.promptOutboxByWorkspace.removeValue(forKey: submissionKey)
                    self.acceptedPromptResultsByWorkspace[submissionKey] = acceptedResult
                    if self.isPromptSubmissionVisible(submission) {
                        var opening = submission
                        opening.status = .opening
                        opening.error = ""
                        self.pendingPromptSubmission = opening
                        self.storageConfirmationMessage = nil
                    }
                    _ = await self.adoptAcceptedPromptResult(
                        acceptedResult,
                        response: response,
                        carriedHistory: optimisticHistory
                    )
                    self.ensureAcceptedPromptSettlement()
                    await self.recordPromptHistory(
                        submission.prompt,
                        workspaceID: workspaceID,
                        sessionID: response.continuedFrom?.sessionId
                            ?? submission.resumeSessionID,
                        target: target
                    )
                    if self.isCurrentMachine(submission.machineID) {
                        await self.refresh(reportErrors: false)
                    }
                    return
                } catch {
                    guard self.promptOutboxByWorkspace[submissionKey]?.commandID == commandID else {
                        return
                    }
                    var failed = self.promptOutboxByWorkspace[submissionKey] ?? submission
                    if PromptSubmissionRetryPolicy.shouldRetry(error) {
                        transientFailureCount += 1
                        failed.status = ["pcr_operation_pending", "workspace_busy"].contains(PromptSubmissionRetryPolicy.errorCode(error) ?? "")
                            ? .waitingForPreviousTurn
                            : ([.checkingReceipt, .receiptUnresolved].contains(current.status) ? current.status : .retrying)
                        failed.error = error.localizedDescription
                        failed.errorCode = PromptSubmissionRetryPolicy.errorCode(error)
                        failed.retryRequiresNewCommandID = false
                        self.savePromptSubmission(failed)
                        let delay = PromptSubmissionRetryPolicy.delay(
                            after: transientFailureCount
                        )
                        do {
                            try await Task.sleep(for: .seconds(delay))
                        } catch {
                            return
                        }
                        continue
                    }
                    failed.retryRequiresNewCommandID = !previouslyUncertain
                        && !PromptSubmissionRetryPolicy.outcomeIsUncertain(error)
                    failed.receiptUncertain = failed.retryRequiresNewCommandID == false
                    failed.status = failed.receiptUncertain == true
                        ? (current.status == .checkingReceipt || current.status == .receiptUnresolved
                            ? .receiptUnresolved : .checkingReceipt) : .failed
                    failed.errorCode = PromptSubmissionRetryPolicy.errorCode(error)
                    failed.error = error.localizedDescription
                    self.savePromptSubmission(failed)
                    if failed.receiptUncertain == true && !failed.needsConnectionRecovery {
                        try? await Task.sleep(for: .seconds(failed.status == .receiptUnresolved ? 60 : 2))
                        continue
                    }
                    return
                }
            }
        }
        return promptSubmissionTasks[submissionKey]
    }

    func resumePendingPromptSubmissions() {
        ensureDeferredPromptProcessing()
        ensureAcceptedPromptSettlement()
        restoreVisiblePromptSubmission()
        for submission in promptOutboxByWorkspace.values where
            submission.status == .awaitingConfirmation && submission.storageEstimate == nil {
            restoreStorageConfirmationReceipt(submission)
        }
        for submission in promptOutboxByWorkspace.values where
            submission.resumesAutomatically || submission.needsReceiptReconciliation {
            submitPendingPrompt(submission)
        }
        for result in acceptedPromptResultsByWorkspace.values {
            Task { [weak self] in
                _ = await self?.adoptAcceptedPromptResult(result)
            }
        }
    }

    private func restoreStorageConfirmationReceipt(_ submission: PersistedPromptSubmission) {
        let lookup = submission.contextKey + ":" + submission.commandID
        guard let target = requestTarget(for: submission.machineID),
              storageReceiptLookups.insert(lookup).inserted else { return }
        Task { [weak self] in
            guard let self else { return }
            do {
                let receipt = try await self.promptCommandReceipt(submission, target: target)
                guard receipt.status == "completed",
                      let response = receipt.result, response.status == "confirmation_required",
                      let storage = response.storage,
                      var current = self.promptOutboxByWorkspace[submission.contextKey],
                      current.commandID == submission.commandID,
                      current.status == .awaitingConfirmation else { return }
                current.storageEstimate = storage
                current.receiptUncertain = false
                guard self.desktopStateStore.updateStorageConfirmation(submission, with: current) else { return }
                self.savePromptSubmission(current, alreadyPersisted: true)
            } catch {
                self.storageReceiptLookups.remove(lookup)
            }
        }
    }

    private func savePromptSubmission(_ submission: PersistedPromptSubmission, alreadyPersisted: Bool = false) {
        guard let previous = promptOutboxByWorkspace[submission.contextKey], previous != submission else { return }
        promptOutboxByWorkspace[submission.contextKey] = submission
        if !alreadyPersisted { self.desktopStateStore.updatePromptSubmission(submission, expected: previous) }
        guard isPromptSubmissionVisible(submission) else { return }
        // Diagnostics belong to the durable delivery record, not to the
        // visible conversation. Repeated transport failures cannot reflow it.
        if pendingPromptSubmission?.status != submission.status
            || pendingPromptSubmission?.commandID != submission.commandID
            || pendingPromptSubmission?.needsConnectionRecovery != submission.needsConnectionRecovery
            || ![.retrying, .checkingReceipt, .receiptUnresolved, .waitingForPreviousTurn].contains(submission.status) {
            pendingPromptSubmission = submission
        }
        storageConfirmationMessage = submission.status == .awaitingConfirmation
            ? submission.error
            : nil
    }

    func retryPendingPrompt() async {
        guard let visibleSubmission = pendingPromptSubmission,
              visibleSubmission.allowsManualRecovery
        else { return }
        let submissionKey = visibleSubmission.contextKey
        if let activeTask = promptSubmissionTasks[submissionKey] {
            activeTask.cancel()
            await activeTask.value
        }
        guard var submission = promptOutboxByWorkspace[submissionKey],
              submission.id == visibleSubmission.id,
              submission.allowsManualRecovery else { return }
        submission.prepareForManualRetry()
        savePromptSubmission(submission)
        submitPendingPrompt(submission)
    }

    func editPendingPrompt() {
        guard let submission = pendingPromptSubmission,
              submission.allowsManualRecovery,
              prompt.isEmpty || prompt == submission.prompt else { return }
        let draftContext = currentPromptDraftContext()
        promptDraftSaveTask?.cancel()
        promptDraftSaveTask = nil
        guard desktopStateStore.returnPromptSubmissionToDraft(
            submission, draftContext: draftContext
        ) else {
            errorMessage = appLanguage.text(
                "暂时无法保存草稿，原消息已保留。请检查本机可用空间。",
                "The draft could not be saved. The original message is retained. Check this Mac's available storage."
            )
            return
        }
        promptSubmissionTasks[submission.contextKey]?.cancel()
        promptSubmissionTasks[submission.contextKey] = nil
        promptSubmissionTaskCommandIDs[submission.contextKey] = nil
        promptOutboxByWorkspace.removeValue(forKey: submission.contextKey)
        pendingPromptSubmission = nil
        updatePrompt(submission.prompt)
    }

    func confirmStorageUsage(expectedCommandID: String? = nil) async {
        if let expectedCommandID {
            guard pendingPromptSubmission?.commandID == expectedCommandID,
                  pendingPromptSubmission?.status == .awaitingConfirmation else { return }
        }
        if var submission = pendingPromptSubmission,
           submission.status == .awaitingConfirmation,
           promptOutboxByWorkspace[submission.contextKey]?.commandID == submission.commandID {
            let original = submission
            submission.confirmLargeRun = true
            submission.status = .pending
            submission.receiptUncertain = false
            submission.firstAttemptAt = nil
            submission.error = ""
            submission.storageEstimate = nil
            guard desktopStateStore.updateStorageConfirmation(original, with: submission) else {
                promptOutboxByWorkspace = desktopStateStore.loadSnapshot().promptOutboxByWorkspace
                restoreVisiblePromptSubmission()
                return
            }
            savePromptSubmission(submission, alreadyPersisted: true)
            deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
            submitPendingPrompt(submission)
            return
        }
        guard let operation = pendingStorageOperation,
              !activeWorkspaceOperationIDs.contains(
                  machineWorkspaceContextKey(workspaceID: operation.workspaceID)
              ) else { return }
        await performWorkspaceOperation(operation.workspaceID) { context in
            let workspaceID = context.workspaceID
            switch operation {
            case .start(_, let prompt, let settings):
                let started = try await self.startRun(
                    workspaceID: workspaceID,
                    prompt: prompt,
                    confirmLargeRun: true,
                    settingsSnapshot: settings,
                    target: context.target
                )
                if started {
                    await self.recordPromptHistory(
                        prompt,
                        workspaceID: workspaceID,
                        sessionID: settings.resumeSessionID
                    )
                }
            case .continuation(_, let runID, let agent, let prompt, let settings):
                let started = try await self.continueRun(
                    workspaceID: workspaceID,
                    runID: runID,
                    agent: agent,
                    prompt: prompt,
                    confirmLargeRun: true,
                    settingsSnapshot: settings,
                    target: context.target
                )
                if started {
                    await self.recordPromptHistory(
                        prompt,
                        workspaceID: workspaceID,
                        sessionID: settings.resumeSessionID
                    )
                }
            case .more(_, let runID, let count):
                _ = try await self.addAgents(
                    workspaceID: workspaceID,
                    runID: runID,
                    count: count,
                    confirmLargeRun: true,
                    target: context.target
                )
            }
        }
    }

    func cancelStorageUsage(expectedCommandID: String? = nil) {
        if let expectedCommandID {
            guard pendingPromptSubmission?.commandID == expectedCommandID,
                  pendingPromptSubmission?.status == .awaitingConfirmation else { return }
        }
        if let submission = pendingPromptSubmission,
           submission.status == .awaitingConfirmation {
            guard submission.canReturnToDraft,
                  prompt.isEmpty || prompt == submission.prompt,
                  desktopStateStore.returnPromptSubmissionToDraft(
                    submission, draftContext: currentPromptDraftContext()
                  ) else {
                errorMessage = appLanguage.text(
                    "输入框已有另一条草稿，或暂时无法保存。待确认的原消息仍保留。",
                    "Another draft is present, or the draft could not be saved. The original message remains pending."
                )
                return
            }
            promptOutboxByWorkspace.removeValue(forKey: submission.contextKey)
            deferredPromptsByWorkspace = desktopStateStore.loadSnapshot().deferredPromptsByWorkspace
            pendingPromptSubmission = nil
            storageConfirmationMessage = nil
            updatePrompt(submission.prompt)
            return
        }
        if let workspaceID = selectedWorkspaceID {
            clearPendingStorageOperation(workspaceID: workspaceID)
        }
    }

}
