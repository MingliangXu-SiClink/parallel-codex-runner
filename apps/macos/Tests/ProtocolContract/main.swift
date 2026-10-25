import AppKit
import Foundation

private func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError(message) }
}

private func jsonObject<T: Encodable>(_ value: T) throws -> [String: Any] {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    let data = try encoder.encode(value)
    guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        fatalError("Encoded body is not a JSON object")
    }
    return object
}

let remoteTransition = RemoteTurnTransition(
    requestID: 7,
    machineID: "mac-a",
    workspaceID: "workspace-a",
    conversationID: "conversation-a"
)
var staleWorkspaceSettings = RunSettings()
var displayedParallelSettings = RunSettings()
displayedParallelSettings.agents = 2
displayedParallelSettings.maxParallel = 15
require(displayedParallelSettings.displayedMaxParallel == 2,
        "Automatic parallel display retained an unrelated previous manual value")
displayedParallelSettings.execution = .serial
require(displayedParallelSettings.displayedMaxParallel == 1,
        "Serial execution does not display one simultaneous candidate")
displayedParallelSettings.execution = .parallel
displayedParallelSettings.automaticMaxParallel = false
require(displayedParallelSettings.displayedMaxParallel == 15,
        "Changing display modes destroyed the saved manual parallel limit")
staleWorkspaceSettings.resumeSessionID = "previous-conversation-session"
staleWorkspaceSettings.agents = 2
let newConversationSettings = PromptSubmissionPlan.start(resuming: false)
    .settingsForSubmission(staleWorkspaceSettings)
require(
    newConversationSettings.resumeSessionID == "NO"
        && newConversationSettings.agents == 2,
    "A new conversation submission must never resume stale workspace history"
)
require(
    PromptSubmissionPlan.start(resuming: true)
        .settingsForSubmission(staleWorkspaceSettings).resumeSessionID
        == "previous-conversation-session",
    "An explicit conversation resume must retain the selected session"
)
require(
    remoteTransition.matches(
        machineID: "mac-a", workspaceID: "workspace-a", conversationID: "conversation-a"
    ),
    "An actual remote Turn transition must gate its own conversation"
)
for conversationID in ["conversation-b", "local:new-conversation", nil] as [String?] {
    require(
        !remoteTransition.matches(
            machineID: "mac-a", workspaceID: "workspace-a", conversationID: conversationID
        ),
        "A previous conversation's remote transition must not disable a new draft"
    )
}
require(
    !remoteTransition.matches(
        machineID: "mac-b", workspaceID: "workspace-a", conversationID: "conversation-a"
    ) && !remoteTransition.matches(
        machineID: "mac-a", workspaceID: "workspace-b", conversationID: "conversation-a"
    ),
    "Remote Turn transitions must remain scoped to their machine and workspace"
)

require(
    multilineEditorKeyAction(
        keyCode: 36,
        modifierFlags: [],
        submitsWithReturn: true
    ) == .submit,
    "Plain Return does not submit from the prompt editor"
)
require(
    multilineEditorKeyAction(
        keyCode: 36,
        modifierFlags: .shift,
        submitsWithReturn: true
    ) == .insertText,
    "Shift-Return no longer inserts a newline in the prompt editor"
)
require(
    multilineEditorKeyAction(
        keyCode: 36,
        modifierFlags: .command,
        submitsWithReturn: true
    ) == .submit,
    "Command-Return no longer submits from the prompt editor"
)
require(
    multilineEditorKeyAction(
        keyCode: 36,
        modifierFlags: .command,
        submitsWithReturn: false
    ) == .insertText,
    "Feedback Return handling unexpectedly submits the form"
)
require(
    !multilineEditorShouldApplyExternalText(
        currentText: "输入中的中文",
        externalText: "输入中的",
        hasMarkedText: true
    ),
    "A SwiftUI refresh can overwrite uncommitted input-method text"
)
require(
    multilineEditorShouldApplyExternalText(
        currentText: "old draft",
        externalText: "new draft",
        hasMarkedText: false
    ),
    "External prompt changes no longer update an idle native editor"
)
require(
    !multilineEditorShouldApplyExternalText(
        currentText: "same draft",
        externalText: "same draft",
        hasMarkedText: false
    ),
    "An unchanged editor is being rewritten unnecessarily"
)
require(
    NativeMultilineEditor.horizontalTextInset > 0
        && NativeMultilineEditor.verticalTextInset > 0,
    "The prompt editor lost its explicit insertion insets"
)
require(
    multilineEditorShouldShowPlaceholder(
        text: "",
        placeholder: "Ask a follow-up",
        hasMarkedText: false
    ),
    "An empty prompt editor no longer displays its placeholder"
)
require(
    !multilineEditorShouldShowPlaceholder(
        text: "draft",
        placeholder: "Ask a follow-up",
        hasMarkedText: false
    ) && !multilineEditorShouldShowPlaceholder(
        text: "",
        placeholder: "Ask a follow-up",
        hasMarkedText: true
    ),
    "The prompt placeholder can overlap entered or marked text"
)

@MainActor
final class NativeEditorCommitProbe: NativeMultilineEditorCommitTarget {
    var commitCount = 0

    func commitPendingText() {
        commitCount += 1
    }
}

MainActor.assumeIsolated {
    let nativeEditorCommitProbe = NativeEditorCommitProbe()
    NativeMultilineEditorCommitRegistry.shared.register(nativeEditorCommitProbe)
    NativeMultilineEditorCommitRegistry.shared.commitPendingText()
    require(
        nativeEditorCommitProbe.commitCount == 1,
        "The application lifecycle cannot commit pending native editor text"
    )
    NativeMultilineEditorCommitRegistry.shared.unregister(nativeEditorCommitProbe)
}
let localizedModelChoices = codexOptionChoices(
    dynamic: [
        CodexOption(label: "default (gpt-5.6-sol)", value: "", selected: true),
        CodexOption(label: "gpt-5.6-luna", value: "gpt-5.6-luna", selected: false),
    ],
    current: "",
    fallbackLabel: localizedDefaultModelLabel("gpt-5.6-sol", language: .chinese)
)
require(
    localizedModelChoices.first?.label == "默认（gpt-5.6-sol）"
        && localizedModelChoices.filter { $0.value.isEmpty }.count == 1,
    "A server-provided English default option overrides the localized model label"
)
require(
    localizedAutomaticCodexLabel("auto (fast)", language: .chinese) == "自动（开启）",
    "The effective automatic fast-mode value is not localized"
)
require(
    localizedAutomaticCodexLabel("auto (xhigh)", language: .english)
        == "Automatic (Extra high)",
    "Automatic effort labels lose their effective value"
)
let localizedEffortChoices = codexEffortChoices(
    dynamic: [
        CodexOption(label: "auto (high)", value: "", selected: false),
        CodexOption(label: "low", value: "low", selected: true),
        CodexOption(label: "medium", value: "medium", selected: false),
        CodexOption(label: "Custom balanced profile", value: "custom", selected: false),
    ],
    current: "low", effective: "high", language: .chinese
)
require(
    localizedEffortChoices.map(\.label) == ["自动（高）", "低", "中", "Custom balanced profile"]
        && localizedEffortChoices.map(\.value) == ["", "low", "medium", "custom"]
        && localizedEffortChoices[1].selected,
    "Localized effort choices must preserve protocol values, custom labels, and selection"
)
require(
    codexEffortChoices(dynamic: nil, current: "future-level", effective: nil, language: .chinese)
        .map(\.label) == ["自动", "future-level"],
    "An unrecognized current reasoning setting must remain selectable"
)
require(
    ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"].map {
        localizedCodexEffortLabel($0, language: .chinese)
    } == ["无", "最低", "低", "中", "高", "极高", "最高", "超强"],
    "Reasoning levels must have distinct localized names"
)

let capabilitiesData = Data(
    """
    {
      "daemon_name": "pcrd",
      "daemon_version": "0.1.7",
      "protocol_version": 1,
      "minimum_client_protocol_version": 1,
      "supported_protocol_versions": [1],
      "transcript_schema_version": 1,
      "maximum_agent_count": 128,
      "transport": "relay",
      "platform": "linux",
      "supports_daemon_completion_notifications": true,
      "supports_daemon_remote_completion_notifications": true,
      "actions": ["system.health", "runs.list"]
    }
    """.utf8
)
let decoder = JSONDecoder()
decoder.keyDecodingStrategy = .convertFromSnakeCase
let wireSnapshotSource = ConversationSnapshot(
    schemaVersion: 1,
    machine: MachineRecord(machineId: "machine-wire"),
    workspace: WorkspaceRecord(
        workspaceId: "workspace-wire",
        machineId: "machine-wire"
    ),
    conversation: ConversationRecord(
        conversationId: "conversation-wire",
        workspaceId: "workspace-wire",
        turnIds: ["turn-wire"]
    ),
    turn: TurnRecord(
        turnId: "turn-wire",
        conversationId: "conversation-wire",
        runId: "run-wire",
        candidateIds: ["candidate-wire"]
    ),
    candidates: [
        AgentCandidateRecord(
            candidateId: "candidate-wire",
            turnId: "turn-wire",
            agentIndex: 1
        )
    ]
)
let wireEncoder = JSONEncoder()
wireEncoder.keyEncodingStrategy = .convertToSnakeCase
let wireConversationSnapshot = try decoder.decode(
    ConversationSnapshot.self,
    from: wireEncoder.encode(wireSnapshotSource)
)
require(
    wireConversationSnapshot.turn.runId == "run-wire"
        && wireConversationSnapshot.candidates.first?.agentIndex == 1,
    "Snake-case conversation snapshots no longer decode through PcrdClient"
)
let capabilities = try decoder.decode(SystemCapabilities.self, from: capabilitiesData)
require(capabilities.compatible, "Compatible protocol was rejected")
require(capabilities.supports("runs.list"), "Advertised action is unavailable")
require(capabilities.clientMaximumAgentCount == 128, "Agent capability was not decoded")
require(capabilities.platform == "linux", "Machine platform was not decoded")
require(
    capabilities.supportsDaemonCompletionNotifications == true,
    "Daemon notification ownership was not decoded"
)
require(
    capabilities.supportsDaemonRemoteCompletionNotifications == true,
    "Remote daemon notification ownership was not decoded"
)

let domainRepositories = ClientDomainRepositories()
let conversationSnapshot = ConversationSnapshot(
    schemaVersion: generatedConversationSchemaVersion,
    machine: MachineRecord(machineId: "machine-1"),
    workspace: WorkspaceRecord(
        workspaceId: "workspace-1",
        machineId: "machine-1"
    ),
    conversation: ConversationRecord(
        conversationId: "conversation-1",
        workspaceId: "workspace-1",
        turnIds: ["turn-1"]
    ),
    turn: TurnRecord(
        turnId: "turn-1",
        conversationId: "conversation-1",
        runId: "run-1",
        candidateIds: ["candidate-1"]
    ),
    candidates: [
        AgentCandidateRecord(
            candidateId: "candidate-1",
            turnId: "turn-1",
            agentIndex: 1
        )
    ]
)
domainRepositories.ingest(conversationSnapshot)
require(
    domainRepositories.turns.records["turn-1"]?.runId == "run-1",
    "Generated Turn state was not stored"
)
require(
    domainRepositories.candidates.records["candidate-1"]?.agentIndex == 1,
    "Generated Agent candidate state was not stored"
)
domainRepositories.removeWorkspace("workspace-1")
require(
    domainRepositories.workspaces.records.isEmpty
        && domainRepositories.conversations.records.isEmpty
        && domainRepositories.turns.records.isEmpty
        && domainRepositories.candidates.records.isEmpty,
    "Removing a workspace left child domain state behind"
)

let latexExtraction = LatexMarkdownExtractor.extract(
    "Value $x^2$ and \\[\\sum_i y_i\\]. `price $5$`\n```sh\necho '$not_math$'\n```"
)
require(latexExtraction.fragments.count == 2, "Markdown LaTeX was not detected")
require(
    latexExtraction.fragments.map(\.source) == ["x^2", "\\sum_i y_i"],
    "LaTeX delimiters changed formula contents"
)
require(
    latexExtraction.markdown.contains("`price $5$`")
        && latexExtraction.markdown.contains("echo '$not_math$'"),
    "LaTeX parsing changed inline or fenced code"
)
let codexBracketLatex = LatexMarkdownExtractor.extract(
    "[ G_t=2+0.5\\times4+0.5^2\\times10=6.5 ]"
)
require(
    codexBracketLatex.fragments.map(\.source)
        == ["G_t=2+0.5\\times4+0.5^2\\times10=6.5"],
    "Codex bracketed display formula was not detected"
)
let multilineLatex = LatexMarkdownExtractor.extract(
    "before\n\\[\nQ^\\pi(s,a)\n=\n\\mathbb E[r+\\gamma Q^\\pi(s',a')]\n\\]\nafter"
)
require(
    multilineLatex.fragments.map(\.source)
        == ["Q^\\pi(s,a)\n=\n\\mathbb E[r+\\gamma Q^\\pi(s',a')]"],
    "Multiline Codex display formula was not detected"
)
require(
    multilineLatex.markdown.contains("before")
        && multilineLatex.markdown.contains("after")
        && !multilineLatex.markdown.contains("\\["),
    "Multiline formula replacement changed surrounding Markdown"
)
require(
    LatexNativeTextFormulaParser.parse(
        "\\boxed{\\text{当前总价值}=\\text{当前奖励}+\\gamma\\times\\text{下一步开始的总价值}}"
    ) == LatexNativeTextFormula(
        text: "当前总价值 = 当前奖励 + γ × 下一步开始的总价值",
        boxed: true
    ),
    "Mixed Chinese text and LaTeX operators were not converted for native rendering"
)

let relayStatus = try decoder.decode(
    RelayStatus.self,
    from: Data(
        """
        {
          "configured": true,
          "paired": true,
          "trusted_android_devices": 1,
          "trusted_controllers": [
            {"device_id": "macos-controller", "public_key": "controller-key"}
          ]
        }
        """.utf8
    )
)
let relayIdentity = try decoder.decode(
    RelayControllerIdentity.self,
    from: Data(
        """
        {
          "device_id": "macos-controller",
          "public_key": "controller-key"
        }
        """.utf8
    )
)
require(
    relayIdentity.deviceId == "macos-controller",
    "The desktop controller identity did not decode its device id"
)
require(
    relayStatus.trusts(relayIdentity),
    "The local server did not recognize its paired desktop controller"
)
require(
    !relayStatus.trusts(
        RelayControllerIdentity(deviceId: "macos-controller", publicKey: "other-key")
    ),
    "A mismatched desktop controller key was trusted"
)

let listBody = try jsonObject(
    ListRunsBody(limit: 100, cursor: "opaque-cursor", maxBytes: 524_288)
)
require(listBody["limit"] as? Int == 100, "Run-list limit was not encoded")
require(
    listBody["cursor"] as? String == "opaque-cursor",
    "Opaque run cursor was not encoded"
)
require(listBody["max_bytes"] as? Int == 524_288, "Run-list byte limit is missing")

let historyBody = try jsonObject(
    RunHistoryBody(
        runId: "run-1",
        cursor: 3,
        limit: 100,
        maxBytes: 524_288,
        conversationItemsOnly: true
    )
)
require(
    historyBody["max_bytes"] as? Int == 524_288,
    "Run-history byte limit is missing"
)
require(
    historyBody["conversation_items_only"] as? Bool == true,
    "Run history still requests operational transcript noise"
)

let agentBody = try jsonObject(
    GetAgentBody(
        runId: "run-1",
        agent: 2,
        includeDiff: false,
        includeEvents: true,
        cursor: 9,
        eventLimit: 200,
        transcriptItemsOnly: true,
        conversationItemsOnly: false,
        finalMessageCursor: 17
    )
)
require(
    agentBody["event_max_bytes"] as? Int == 512 * 1024,
    "Agent event page is not byte bounded"
)
require(
    agentBody["transcript_items_only"] as? Bool == true,
    "Stable transcript item mode is disabled"
)
require(
    agentBody["final_message_cursor"] as? Int == 17 &&
        agentBody["final_message_max_bytes"] as? Int == 192 * 1024,
    "Agent final response is not byte paged"
)

let followUpID = UUID(uuidString: "1e8d6af3-e87f-4a37-a682-114de08bc570")!
let followUpBody = try jsonObject(
    FollowUpQueueEnqueueBody(
        workspaceId: "workspace-1",
        runId: "run-1",
        item: FollowUpQueueItemBody(
            QueuedFollowUp(id: followUpID, prompt: "continue", settings: RunSettings()),
            fallbackSettings: RunSettings()
        ),
        selectedAgent: 2
    )
)
require(
    followUpBody["workspace_id"] as? String == "workspace-1"
        && followUpBody["run_id"] as? String == "run-1"
        && followUpBody["selected_agent"] as? Int == 2,
    "Atomic queued-message context was not encoded"
)
let encodedFollowUpItem = followUpBody["item"] as? [String: Any]
require(
    encodedFollowUpItem?["id"] as? String == followUpID.uuidString.lowercased()
        && encodedFollowUpItem?["prompt"] as? String == "continue",
    "Atomic queued message was not encoded"
)

require(
    AppTextSize.small.larger == .standard
        && AppTextSize.standard.larger == .large
        && AppTextSize.large.larger == .extraLarge
        && AppTextSize.extraLarge.larger == nil,
    "Display zoom-in steps are incomplete"
)
require(
    AppTextSize.extraLarge.smaller == .large
        && AppTextSize.large.smaller == .standard
        && AppTextSize.standard.smaller == .small
        && AppTextSize.small.smaller == nil,
    "Display zoom-out steps are incomplete"
)
require(
    AppLanguage.chinese.localizedClientMessage(
        "The conversation changed before this queued message could be sent. Open the queue to review it."
    ) == "对话已发生变化，这条排队消息没有被发送。请打开队列确认后重试。",
    "Queued-message context changes are not localized"
)
require(
    TranscriptAppearance(.light) == .light
        && TranscriptAppearance(.dark) == .dark
        && TranscriptAppearance.light.nsAppearance.name == .aqua
        && TranscriptAppearance.dark.nsAppearance.name == .darkAqua,
    "Transcript rendering does not track the effective app appearance"
)
let missingSessionDiagnostic = "Codex session old-session is not resumable from /tmp/project"
let missingArtifactsDiagnostic = "Expired artifact cleanup failed: The recorded run root is no longer a real directory: /tmp/run"
let historyNotice = AppErrorNotice.previousConversationHistory(missingSessionDiagnostic)
require(
    historyNotice.message(language: .chinese) == "无法加载上一段对话。原始对话记录无法在此工作区恢复。"
        && historyNotice.message(language: .english)
            == "The previous conversation could not be loaded. The original conversation history cannot be restored in this workspace."
        && historyNotice == .previousConversationHistory(missingSessionDiagnostic),
    "An existing history error keeps its previous display language or loses the original diagnostic"
)
require(
    AppErrorNotice.selectedConversationHistory("Connection timed out")
        .message(language: .english)
        == "The selected conversation could not be loaded. Connection timed out",
    "A transient history failure is mislabeled as permanently missing data"
)
require(
    AppLanguage.chinese.userMessage(for: PcrdClientError.remote("pcr_error", missingSessionDiagnostic))
        == "原始对话记录无法在此工作区恢复。"
        && AppLanguage.english.localizedClientMessage(missingSessionDiagnostic)
            == "The original conversation history cannot be restored in this workspace.",
    "Legacy missing history errors expose internal session IDs instead of useful context"
)
require(
    AppLanguage.chinese.localizedClientMessage(missingArtifactsDiagnostic)
        == "本轮运行文件已不可用，无法完成清理。"
        && AppLanguage.english.localizedClientMessage(missingArtifactsDiagnostic)
            == "The working files for this turn are unavailable, so cleanup could not finish.",
    "Legacy cleanup diagnostics are not presented consistently in both languages"
)
for language in AppLanguage.allCases {
    for diagnostic in ["Connection timed out", "Unexpected server error: detail", "Codex session write failed"] {
        require(
            language.localizedClientMessage(diagnostic) == diagnostic,
            "An unrelated error was incorrectly classified as missing conversation data"
        )
    }
}
let lightUserSurface = ParallelCodexVisualStyle.resolvedNSColor(
    ParallelCodexVisualStyle.userSurfaceNSColor,
    for: .light
).usingColorSpace(.genericGray)!
let darkUserSurface = ParallelCodexVisualStyle.resolvedNSColor(
    ParallelCodexVisualStyle.userSurfaceNSColor,
    for: .dark
).usingColorSpace(.genericGray)!
require(
    lightUserSurface.whiteComponent > 0.8
        && darkUserSurface.whiteComponent < 0.3,
    "User message surface did not resolve against the selected appearance"
)

let item = RemoteTranscriptItem(
    id: "event-1",
    eventId: 1,
    kind: "output",
    text: "**完成**",
    status: nil,
    operationId: nil,
    activityType: nil
)
var transcript = AgentTranscript()
TranscriptFormatter.append(items: [item], to: &transcript.blocks)
require(transcript.blocks.count == 1, "Transcript item was dropped")
require(transcript.blocks[0].text == "**完成**", "Markdown text was changed")

let warningItem = RemoteTranscriptItem(
    id: "event-2",
    eventId: 2,
    kind: "warning",
    text: "Model mismatch",
    status: nil,
    operationId: nil,
    activityType: nil
)
TranscriptFormatter.append(items: [warningItem], to: &transcript.blocks)
require(transcript.blocks.last?.kind == .warning, "Transcript warning was dropped")

let transportFallbackEvent = RunEvent(
    eventId: 3,
    timestamp: nil,
    type: "agent_line",
    idx: 1,
    stream: "stdout",
    text: #"{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Falling back from WebSockets to HTTPS transport. stream disconnected before completion"}}"#,
    message: nil,
    status: nil,
    reasoningTokens: nil
)
var transportFallbackBlocks: [TranscriptBlock] = []
TranscriptFormatter.append(events: [transportFallbackEvent], to: &transportFallbackBlocks)
require(
    transportFallbackBlocks.last?.kind == .warning,
    "Recoverable transport fallback is still rendered as an error"
)

let plainRuntimeLogEvent = RunEvent(
    eventId: 4,
    timestamp: nil,
    type: "agent_line",
    idx: 1,
    stream: "stderr",
    text: "2026-09-02T08:13:22.000000Z ERROR codex_core::tools::router: error=exec_command failed: CreateProcess cwd=/tmp/old: No such file or directory",
    message: nil,
    status: nil,
    reasoningTokens: nil
)
var runtimeLogBlocks: [TranscriptBlock] = []
TranscriptFormatter.append(events: [plainRuntimeLogEvent], to: &runtimeLogBlocks)
require(
    runtimeLogBlocks.count == 1
        && runtimeLogBlocks[0].kind == .warning
        && runtimeLogBlocks[0].activityType == "missing_execution_path",
    "Plain recoverable runtime log was not classified as a warning"
)
require(
    !runtimeLogBlocks[0].text.contains("codex_core")
        && !runtimeLogBlocks[0].text.contains("2026-09-02"),
    "Raw logger metadata still leaks into the transcript"
)

let groupedReasoning = TranscriptFormatter.coalescedForPresentation([
    TranscriptBlock(id: "thought-1", kind: .thought, text: "Inspect the project"),
    TranscriptBlock(id: "thought-2", kind: .thought, text: "Choose the implementation"),
    TranscriptBlock(id: "command-1", kind: .command, text: "rg --files", status: "Completed"),
    TranscriptBlock(id: "thought-3", kind: .thought, text: "Verify the result"),
])
require(groupedReasoning.count == 3, "Adjacent reasoning was not grouped for presentation")
require(
    groupedReasoning[0].text == "Inspect the project\n\nChoose the implementation",
    "Grouped reasoning changed its chronological content"
)
require(
    groupedReasoning[2].text == "Verify the result",
    "Reasoning was merged across an intervening command"
)
let finalAnswerProjection = TranscriptFormatter.blocks(
    history: [],
    prompt: "question",
    transcript: AgentTranscript(
        blocks: [
            TranscriptBlock(id: "update-1", kind: .output, text: "I will inspect this."),
            TranscriptBlock(id: "answer-event", kind: .output, text: "The final answer."),
        ],
        finalMessage: "The final answer."
    )
)
require(
    finalAnswerProjection.filter { $0.isFinalAnswer }.count == 1
        && finalAnswerProjection.last?.id == "answer-event",
    "The streamed final response was duplicated instead of promoted"
)
let metadataMirroredFinalAnswer = TranscriptFormatter.blocks(
    history: [],
    prompt: "question",
    transcript: AgentTranscript(
        blocks: [
            TranscriptBlock(
                id: "answer-with-metadata",
                kind: .output,
                text: "Answer text.\n\n<oai-mem-citation><citation_entries>x</citation_entries></oai-mem-citation>"
            ),
        ],
        finalMessage: "Answer text."
    )
)
let answerFirstPrefix = AgentTranscript(finalMessage: "Answer prefix",
    finalMessageHasMore: true, finalMessagePresentationID: "final-message")
var answerFirstWithWork = answerFirstPrefix
answerFirstWithWork.blocks = [TranscriptBlock(id: "completed-event", kind: .output,
    text: "Answer prefix and its remaining pages", isFinalAnswer: true)]
let answerFirstBeforeWork = TranscriptFormatter.blocks(history: [], prompt: "", transcript: answerFirstPrefix)
let answerFirstAfterWork = TranscriptFormatter.blocks(history: [], prompt: "", transcript: answerFirstWithWork)
require(answerFirstBeforeWork.count == 1 && answerFirstAfterWork.count == 1
    && answerFirstBeforeWork[0].id == answerFirstAfterWork[0].id
    && answerFirstAfterWork[0].text == "Answer prefix and its remaining pages",
    "Late work pages lost the complete answer or replaced its reading identity")
for prefix in ["Answer prefix and", "Answer prefix and its remaining pag", "Answer prefix and its remaining pages"] {
    answerFirstWithWork.finalMessage = prefix
    answerFirstWithWork.finalMessageHasMore = prefix != "Answer prefix and its remaining pages"
    let answerFirstComplete = TranscriptFormatter.blocks(history: [], prompt: "", transcript: answerFirstWithWork)
    require(answerFirstComplete.count == 1 && answerFirstComplete[0].id == answerFirstBeforeWork[0].id
        && answerFirstComplete[0].text == "Answer prefix and its remaining pages",
        "Completing answer pagination truncated the visible answer or replaced its identity")
}
var unrelatedFinalAnswer = answerFirstPrefix
unrelatedFinalAnswer.blocks = [TranscriptBlock(id: "different-final", kind: .output,
    text: "A different complete answer", isFinalAnswer: true)]
let unrelatedFinalProjection = TranscriptFormatter.blocks(history: [], prompt: "", transcript: unrelatedFinalAnswer)
require(unrelatedFinalProjection.map(\.text) == ["A different complete answer", "Answer prefix"],
    "Answer-first presentation hid an unrelated final event")
var ambiguousFinalAnswers = answerFirstPrefix
ambiguousFinalAnswers.blocks = [
    TranscriptBlock(id: "first-final", kind: .output, text: "Answer prefix with one conclusion", isFinalAnswer: true),
    TranscriptBlock(id: "second-final", kind: .output, text: "Answer prefix with a different conclusion", isFinalAnswer: true),
]
let ambiguousFinalProjection = TranscriptFormatter.blocks(history: [], prompt: "", transcript: ambiguousFinalAnswers)
require(ambiguousFinalProjection.map(\.text) == ["Answer prefix with one conclusion",
    "Answer prefix with a different conclusion", "Answer prefix"],
    "An ambiguous answer prefix silently chose and hid one of several final events")
ambiguousFinalAnswers.finalMessage = "Answer prefix with a different"
let resolvedPrefixProjection = TranscriptFormatter.blocks(history: [], prompt: "", transcript: ambiguousFinalAnswers)
require(resolvedPrefixProjection.map(\.text) == ["Answer prefix with one conclusion",
    "Answer prefix with a different conclusion"], "A newly resolved prefix lost its full matching event")
ambiguousFinalAnswers.finalMessage = "Answer prefix with an incompatible result"
let divergentPrefixProjection = TranscriptFormatter.blocks(history: [], prompt: "", transcript: ambiguousFinalAnswers)
require(divergentPrefixProjection.count == 3, "A later mismatch failed to restore the original final events")
var similarProgress = answerFirstPrefix
similarProgress.blocks = [TranscriptBlock(id: "progress", kind: .output,
    text: "Answer prefix describing a progress update")]
let similarProgressProjection = TranscriptFormatter.blocks(history: [], prompt: "", transcript: similarProgress)
require(similarProgressProjection.count == 2 && similarProgressProjection.last?.text == "Answer prefix",
    "A progress message was incorrectly substituted for a partial final artifact")
require(
    metadataMirroredFinalAnswer.filter { $0.kind == .output }.count == 1
        && metadataMirroredFinalAnswer.last?.id == "answer-with-metadata"
        && metadataMirroredFinalAnswer.last?.isFinalAnswer == true,
    "A metadata-bearing streamed answer was duplicated by the final-message artifact"
)
let rewrittenLinkFinalAnswer = TranscriptFormatter.blocks(
    history: [],
    prompt: "Show the updated image",
    transcript: AgentTranscript(
        blocks: [
            TranscriptBlock(
                id: "cached-agent-answer",
                kind: .output,
                text: "Done.\n\n[View image](/tmp/agent-workspace/assets/result.png)"
            ),
        ],
        finalMessage: "Done.\n\n[View image](/Users/example/project/assets/result.png)\n\n<oai-mem-citation><citation_entries>x</citation_entries></oai-mem-citation>"
    )
)
require(
    rewrittenLinkFinalAnswer.filter { $0.kind == .output }.count == 1
        && rewrittenLinkFinalAnswer.filter { $0.kind == .user }.map(\.text)
            == ["Show the updated image"]
        && rewrittenLinkFinalAnswer.last?.id == "cached-agent-answer"
        && rewrittenLinkFinalAnswer.last?.isFinalAnswer == true
        && rewrittenLinkFinalAnswer.last?.text.contains("/Users/example/project/") == true
        && rewrittenLinkFinalAnswer.last?.text.contains("oai-mem-citation") == false,
    "A sync-back path rewrite duplicated the streamed and final answer"
)
let separatelyCachedFinalMirror = TranscriptFormatter.coalescedVisibleConversation([
    TranscriptBlock(id: "streamed-answer", kind: .output, text: "Same answer"),
    TranscriptBlock(
        id: "final-artifact",
        kind: .output,
        text: "Same answer",
        isFinalAnswer: true
    ),
])
require(
    separatelyCachedFinalMirror.count == 1
        && separatelyCachedFinalMirror[0].id == "streamed-answer"
        && separatelyCachedFinalMirror[0].isFinalAnswer,
    "Separately cached copies of one final answer remained visible twice"
)
let separatelyCachedLinkMirror = TranscriptFormatter.coalescedVisibleConversation([
    TranscriptBlock(
        id: "cached-agent-answer",
        kind: .output,
        text: "Done.\n\n[View image](/tmp/agent-workspace/assets/result.png)"
    ),
    TranscriptBlock(
        id: "final-artifact",
        kind: .output,
        text: "Done.\n\n[View image](/Users/example/project/assets/result.png)",
        isFinalAnswer: true
    ),
])
require(
    separatelyCachedLinkMirror.count == 1
        && separatelyCachedLinkMirror[0].id == "cached-agent-answer"
        && separatelyCachedLinkMirror[0].isFinalAnswer
        && separatelyCachedLinkMirror[0].text.contains("/Users/example/project/"),
    "Cached Agent and sync-back links remained as two visible answers"
)
let groupedUpdates = TranscriptFormatter.coalescedVisibleConversation([
    TranscriptBlock(id: "update-a", kind: .output, text: "First update"),
    TranscriptBlock(
        id: "update-b",
        kind: .output,
        text: "Second update",
        isHistorical: true
    ),
    TranscriptBlock(
        id: "answer",
        kind: .output,
        text: "Answer",
        isFinalAnswer: true
    ),
])
require(
    groupedUpdates.count == 3
        && groupedUpdates[0].text == "First update"
        && groupedUpdates[1].text == "Second update"
        && groupedUpdates[2].isFinalAnswer,
    "Current work was merged into the persisted conversation history"
)
let groupedHistoricalAnswers = TranscriptFormatter.coalescedVisibleConversation([
    TranscriptBlock(
        id: "history-a",
        kind: .output,
        text: "First response fragment",
        isHistorical: true,
        isFinalAnswer: true
    ),
    TranscriptBlock(
        id: "history-b",
        kind: .output,
        text: "Second response fragment",
        isHistorical: true,
        isFinalAnswer: true
    ),
    TranscriptBlock(id: "next-user", kind: .user, text: "Next question", isHistorical: true),
])
require(
    groupedHistoricalAnswers.count == 2
        && groupedHistoricalAnswers[0].text
            == "First response fragment\n\nSecond response fragment"
        && groupedHistoricalAnswers[1].kind == .user,
    "Adjacent assistant fragments from one historical turn repeat as separate messages"
)
let collapsedWork = TranscriptFormatter.blocksWithWorkDisclosure(
    [
        TranscriptBlock(id: "prompt", kind: .user, text: "Question"),
        TranscriptBlock(id: "reasoning", kind: .thought, text: "Think"),
        TranscriptBlock(id: "command", kind: .command, text: "rg --files"),
        TranscriptBlock(id: "answer", kind: .output, text: "Answer", isFinalAnswer: true),
    ],
    runID: "run-1",
    agent: 2,
    expanded: false
)
require(
    collapsedWork.map(\.id) == ["prompt", "work-summary-run-1-2-reasoning", "answer"]
        && collapsedWork[1].text == "Ran commands"
        && collapsedWork[1].status == nil,
    "Completed Agent work is not collapsed immediately before the final answer"
)
let collapsedWorkWithNaturalUpdate = TranscriptFormatter.blocksWithWorkDisclosure(
    [
        TranscriptBlock(id: "prompt", kind: .user, text: "Question"),
        TranscriptBlock(id: "reasoning", kind: .thought, text: "Think"),
        TranscriptBlock(id: "update", kind: .output, text: "I found the cause."),
        TranscriptBlock(id: "answer", kind: .output, text: "Answer", isFinalAnswer: true),
    ],
    runID: "run-1",
    agent: 2,
    expanded: false
)
require(
    collapsedWorkWithNaturalUpdate.map(\.id)
        == ["prompt", "work-summary-run-1-2-reasoning", "update", "answer"],
    "An unknown legacy message must not move into Work when the final answer arrives"
)
let expandedWork = TranscriptFormatter.blocksWithWorkDisclosure(
    [
        TranscriptBlock(id: "prompt", kind: .user, text: "Question"),
        TranscriptBlock(id: "reasoning", kind: .thought, text: "Think"),
        TranscriptBlock(id: "command", kind: .command, text: "rg --files"),
        TranscriptBlock(id: "answer", kind: .output, text: "Answer", isFinalAnswer: true),
    ],
    runID: "run-1",
    agent: 2,
    expanded: true
)
require(
    expandedWork.map(\.id)
        == ["prompt", "work-summary-run-1-2-reasoning", "reasoning", "command", "answer"]
        && expandedWork[1].text == "Ran commands"
        && expandedWork[1].status == nil,
    "Expanded Agent work loses its inline collapse control"
)
let expandedWorkPlan = ConversationTimelineUpdatePlan.make(
    old: ConversationTimelineProjection.make(blocks: collapsedWork, switchState: nil),
    new: ConversationTimelineProjection.make(blocks: expandedWork, switchState: nil)
)
require(
    !expandedWorkPlan.reloadAll
        && expandedWorkPlan.changedIndices == [1]
        && expandedWorkPlan.removedIndices == [2]
        && expandedWorkPlan.insertedIndices == [2, 3],
    "Expanding Agent work reloads the full conversation and can move the viewport"
)
require(
    ConversationTimelineProjection.changedInlineControlID(
        old: ConversationTimelineProjection.make(blocks: collapsedWork, switchState: nil),
        new: ConversationTimelineProjection.make(blocks: expandedWork, switchState: nil)
    ) == "message:work-summary-run-1-2-reasoning",
    "Work expansion cannot keep the disclosure control at a stable screen position"
)
require(
    ConversationTimelineProjection.toggledWorkDisclosureID(
        old: ConversationTimelineProjection.make(blocks: collapsedWork, switchState: nil),
        new: ConversationTimelineProjection.make(blocks: expandedWork, switchState: nil)
    ) == "message:work-summary-run-1-2-reasoning",
    "Work disclosure expansion is not distinguishable from live count updates"
)
let incrementalUpdate = TranscriptCollectionUpdatePlan.make(
    old: [TranscriptBlock(id: "a", kind: .output, text: "A")],
    new: [
        TranscriptBlock(id: "a", kind: .output, text: "A extended"),
        TranscriptBlock(id: "b", kind: .command, text: "rg --files"),
    ]
)
require(
    !incrementalUpdate.reloadAll
        && incrementalUpdate.changedIndices == [0]
        && incrementalUpdate.insertedIndices == [1],
    "Live transcript updates still require a full collection reload"
)
require(
    TranscriptCollectionUpdatePlan.make(
        old: [TranscriptBlock(id: "a", kind: .output, text: "A")],
        new: [TranscriptBlock(id: "different", kind: .output, text: "B")]
    ).reloadAll,
    "A changed transcript identity was treated as an incremental append"
)
let historicalProjection = TranscriptFormatter.blocks(
    history: [ResumeHistoryEntry(category: "thought", text: "previous work")],
    prompt: "next prompt",
    transcript: AgentTranscript(
        blocks: [TranscriptBlock(id: "current-work", kind: .thought, text: "current work")]
    )
)
require(
    historicalProjection.first?.isHistorical == true
        && historicalProjection.last?.isHistorical == false,
    "Previous and current Agent activity cannot be distinguished in the timeline"
)
let optimisticNextTurn = TranscriptFormatter.pendingTurnBlocks(
    history: branchConversationHistory(
        history: [
            ResumeHistoryEntry(category: "user", text: "Previous question"),
            ResumeHistoryEntry(category: "output", text: "Previous answer"),
        ],
        prompt: "Accepted question",
        transcript: AgentTranscript(
            blocks: [
                TranscriptBlock(id: "private-work", kind: .thought, text: "Investigating")
            ],
            finalMessage: "Accepted answer"
        )
    ),
    prompt: "Next question",
    status: "Sending"
)
require(
    optimisticNextTurn.dropLast().allSatisfy(\.isHistorical)
        && optimisticNextTurn.last?.id == "current-prompt"
        && optimisticNextTurn.last?.text == "Next question"
        && optimisticNextTurn.last?.isHistorical == false
        && !optimisticNextTurn.contains { $0.text == "Investigating" },
    "A pending follow-up did not freeze the accepted branch as stable history"
)
require(
    !ConversationTimelineProjection.make(
        blocks: optimisticNextTurn,
        switchState: nil
    ).contains {
        if case .agentSwitch = $0 { return true }
        return false
    },
    "The previous turn's Agent selector remained visible while opening the next turn"
)
require(
    TranscriptFormatter.reasoningPresentationText(
        "***Inspect context** **Choose implementation** **Verify result***"
    ) == "Inspect context\n\nChoose implementation\n\nVerify result",
    "Concatenated reasoning emphasis was not separated into readable steps"
)
require(
    TranscriptFormatter.reasoningPresentationText(
        "***Inspect context** **Choose implementation** **Verify result***"
    ).contains("\n\nChoose implementation\n\n"),
    "Middle reasoning steps retained runtime emphasis markers"
)
require(
    TranscriptFormatter.reasoningPresentationText("Use **bold** when needed")
        == "Use **bold** when needed",
    "Ordinary inline emphasis was changed in reasoning text"
)
require(
    TranscriptFormatter.markdownPresentationText(
        "**Heading without a close\n\nBody with a stray close**\n\n**\n\nUse **bold** normally"
    ) == "Heading without a close\n\nBody with a stray close\n\n\n\nUse **bold** normally",
    "Malformed runtime emphasis markers still leak into the conversation"
)
require(
    TranscriptFormatter.markdownPresentationText(
        "```text\n**literal code marker\n```"
    ).contains("**literal code marker"),
    "Markdown cleanup changed code-block contents"
)
let nestedListMarkdown = try AttributedString(
    markdown: "6. Outer\n    - Inner A\n    - Inner B\n7. Next",
    options: .init(interpretedSyntax: .full)
)
let innerListMarker = nestedListMarkdown.runs.compactMap { run in
    String(nestedListMarkdown[run.range].characters) == "Inner A"
        ? markdownListItemPresentation(from: run.presentationIntent?.components ?? [])
        : nil
}.first
require(
    innerListMarker == MarkdownListItemPresentation(
        ordinal: 1,
        depth: 2,
        isOrdered: false
    ),
    "Nested Markdown lists inherited the outer list ordinal or style"
)
let answerWithInternalMetadata = """
Useful answer.

<oai-mem-citation>
<citation_entries>
MEMORY.md:1-2|note=[internal]
</citation_entries>
</oai-mem-citation>
"""
require(
    TranscriptFormatter.assistantPresentationText(answerWithInternalMetadata)
        == "Useful answer.",
    "Internal memory metadata remained visible in an Agent answer"
)
let legacyMirroredAnswer = """
Useful answer with a link: [result](https://example.com).

Useful answer with a link: [result](https://example.com).

<oai-mem-citation>
<citation_entries>
MEMORY.md:1-2|note=[internal]
</citation_entries>
</oai-mem-citation>
"""
require(
    TranscriptFormatter.assistantPresentationText(legacyMirroredAnswer)
        == "Useful answer with a link: [result](https://example.com).",
    "A legacy event/response mirror rendered the final answer twice"
)
let legacyMirroredAnswerWithoutMetadata =
    "Legacy answer without metadata.\nLegacy answer without metadata."
require(
    TranscriptFormatter.assistantPresentationText(legacyMirroredAnswerWithoutMetadata)
        == "Legacy answer without metadata.",
    "A legacy event/response mirror without metadata rendered twice"
)
require(
    TranscriptFormatter.assistantPresentationText(
        "Useful streaming answer.\n\n<oai-mem-citation>\n<citation_entries>"
    ) == "Useful streaming answer.",
    "An incomplete streaming metadata block flashed in the conversation"
)
let answerWithFileCitation = """
Created :codex-file-citation{path="/Users/test/Project Name/result [final].docx" purpose="output"} successfully.
"""
require(
    TranscriptFormatter.assistantPresentationText(answerWithFileCitation)
        == "Created [result \\[final\\].docx](<file:///Users/test/Project%20Name/result%20%5Bfinal%5D.docx>) successfully.",
    "An internal Codex file citation remained visible in an Agent answer"
)
require(
    TranscriptFormatter.removingInternalAssistantMetadata(from: [
        TranscriptBlock(
            id: "answer",
            kind: .output,
            text: answerWithInternalMetadata,
            status: nil,
            isFinalAnswer: true
        ),
        TranscriptBlock(
            id: "user-literal",
            kind: .user,
            text: "<oai-mem-citation>literal user text",
            status: nil
        ),
    ]).map(\.text) == [
        "Useful answer.",
        "<oai-mem-citation>literal user text",
    ],
    "Assistant metadata filtering changed user-authored text"
)

require(
    PromptSubmissionPlan.start(resuming: false).contextText
        == "Start a new parallel turn",
    "A new conversation does not disclose its submission behavior"
)
require(
    PromptSubmissionPlan.start(resuming: true).contextText
        == "Continue this conversation with a parallel turn",
    "A resumed conversation does not disclose its submission behavior"
)
require(
    PromptSubmissionPlan.start(resuming: true).promptPlaceholder
        == "Ask a follow-up",
    "A resumed Codex conversation looks like an unrelated new task"
)
require(
    PromptSubmissionPlan.enqueue.promptPlaceholder
        == "Write the next message",
    "The composer does not explain queued submission behavior"
)
require(
    PromptSubmissionPlan.blocked(
        "Open the latest turn in this conversation to continue"
    ).visibleContextText(language: .chinese) == "请先打开此对话的最新一轮",
    "Historical Turn recovery guidance was not localized"
)
require(
    PromptSubmissionPlan.continueFrom(
        runID: "run-1",
        agent: 3,
        agentName: "AGENT-003",
        stopsRemainingAgents: false
    ).contextText == "Continue from AGENT-003",
    "The selected continuation branch and finalize behavior are not visible"
)
require(
    PromptSubmissionPlan.start(resuming: false).visibleContextText == nil,
    "A routine new task adds an unnecessary composer status row"
)
require(
    PromptSubmissionPlan.make(
        composingNewConversation: true,
        run: nil,
        selectedAgent: nil,
        hasQueuedFollowUps: true,
        syncBack: true,
        hasSelectedResumeSession: true
    ) == .start(resuming: false),
    "A new conversation inherits queued or resumed context"
)
require(
    PromptSubmissionPlan.continueFrom(
        runID: "run-1",
        agent: 3,
        agentName: "AGENT-003",
        stopsRemainingAgents: false
    ).visibleContextText == "The next message continues from AGENT-003",
    "The review composer does not identify the continuation branch"
)
require(
    PromptSubmissionPlan.continueFrom(
        runID: "run-1",
        agent: 3,
        agentName: "AGENT-003",
        stopsRemainingAgents: false
    ).promptPlaceholder == "Ask a follow-up",
    "The continuation composer uses Agent state as its editor placeholder"
)
require(
    PromptSubmissionPlan.continueFrom(
        runID: "run-1",
        agent: 3,
        agentName: "AGENT-003",
        stopsRemainingAgents: true
    ).stopsRemainingAgents,
    "An immediate continuation does not expose that unfinished Agents will stop"
)
require(
    !PromptSubmissionPlan.enqueue.stopsRemainingAgents,
    "A queued follow-up is incorrectly presented as stopping active Agents"
)
require(
    !PromptSubmissionPlan.blocked("Resolve the current run").isAvailable,
    "A blocked submission plan was treated as executable"
)
require(
    !PromptSubmissionPlan.loading.isAvailable
        && PromptSubmissionPlan.loading.recoveryAction == nil
        && PromptSubmissionPlan.loading.visibleContextText == nil
        && PromptSubmissionPlan.loading.blockedReason(language: .chinese) == nil
        && PromptSubmissionPlan.loading.promptPlaceholder(language: .chinese) == "消息",
    "Unresolved destination navigation must retain the draft without offering an executable action"
)
require(
    PromptSubmissionRetryPolicy.shouldRetry(PcrdClientError.connection("offline")),
    "A transient connection failure would strand the prompt outbox"
)
require(
    PromptSubmissionRetryPolicy.shouldRetry(
        PcrdClientError.remote("relay_http_500", "server unavailable")
    ),
    "A transient Relay server failure would strand the prompt outbox"
)
require(
    PromptSubmissionRetryPolicy.shouldRetry(
        PcrdClientError.remote(
            "pcr_operation_pending",
            "Agent finalization is still running"
        )
    ),
    "An atomic continuation stops after finalization instead of retrying the same message"
)
require(
    !PromptSubmissionRetryPolicy.shouldRetry(
        PcrdClientError.remote("invalid_argument", "bad prompt")
    ),
    "A permanent run rejection would retry forever"
)
require(
    PromptSubmissionRetryPolicy.delay(after: 1) == 2
        && PromptSubmissionRetryPolicy.delay(after: 20) == 30,
    "Prompt retry backoff is not bounded"
)
require(
    PromptSubmissionStatus.retrying.conversationLabel == "Queued"
        && PromptSubmissionStatus.opening.conversationLabel == "Sent"
        && PromptSubmissionStatus.failed.conversationLabel == "Not sent",
    "Prompt outbox status is not expressed as user-facing conversation text"
)
var ambiguousPromptRetry = PersistedPromptSubmission(
    id: "prompt-retry-ambiguous",
    machineID: "machine-1",
    workspaceID: "workspace-1",
    prompt: "continue",
    settings: RunSettings(),
    sourceRunID: "run-1",
    sourceAgent: 1,
    resumeSessionID: "session-1",
    confirmLargeRun: false,
    attempt: 2,
    status: .retrying,
    error: "offline",
    retryRequiresNewCommandID: false
)
let ambiguousCommandID = ambiguousPromptRetry.commandID
ambiguousPromptRetry.prepareForManualRetry()
require(
    ambiguousPromptRetry.status == .pending
        && ambiguousPromptRetry.error.isEmpty
        && ambiguousPromptRetry.commandID == ambiguousCommandID,
    "Retrying an ambiguous send changed its idempotency key or stayed in retry state"
)
var rejectedPromptRetry = ambiguousPromptRetry
rejectedPromptRetry.status = .failed
rejectedPromptRetry.error = "rejected"
rejectedPromptRetry.retryRequiresNewCommandID = true
let rejectedCommandID = rejectedPromptRetry.commandID
rejectedPromptRetry.prepareForManualRetry()
require(
    rejectedPromptRetry.status == .pending
        && rejectedPromptRetry.error.isEmpty
        && rejectedPromptRetry.commandID != rejectedCommandID,
    "Retrying a permanent rejection reused its rejected idempotency key"
)
var editedFollowUp = QueuedFollowUp(prompt: "original", attempt: 2)
require(
    editedFollowUp.needsDaemonAcknowledgement,
    "A locally restored queue item is incorrectly treated as daemon-acknowledged"
)
let originalFollowUpCommandID = editedFollowUp.commandID
editedFollowUp.resetAfterEdit(settings: RunSettings())
require(
    editedFollowUp.commandID != originalFollowUpCommandID,
    "Editing a queued prompt reused the server's previous idempotency key"
)
let editedFollowUpCommandID = editedFollowUp.commandID
editedFollowUp.retryManually()
require(
    editedFollowUp.commandID != editedFollowUpCommandID,
    "Manually retrying a queued prompt reused a rejected idempotency key"
)
editedFollowUp.daemonAcknowledged = true
require(
    !editedFollowUp.needsDaemonAcknowledgement,
    "A daemon response would be resubmitted after it was acknowledged"
)
require(
    FollowUpDispatchRoute.resolve(
        ownershipKnown: true,
        daemonOwnsQueue: true
    ) == .daemon,
    "A daemon-owned queue would also be dispatched by the macOS client"
)
require(
    FollowUpDispatchRoute.resolve(
        ownershipKnown: false,
        daemonOwnsQueue: false
    ) == .waitForCapabilities,
    "The legacy queue executor can race capability discovery"
)
require(
    FollowUpDispatchRoute.resolve(
        ownershipKnown: true,
        daemonOwnsQueue: false
    ) == .legacyClient,
    "A legacy pcrd queue no longer has a client-side dispatcher"
)

let emptyRunConfiguration = RunConfiguration(
    numAgents: 1,
    maxParallel: 1,
    serial: false,
    recommendBy: "duration",
    model: nil,
    effort: nil,
    fast: nil,
    synthesisAgents: 0,
    followUpDelay: 60,
    synthesisModel: nil,
    synthesisEffort: nil,
    synthesisFast: nil,
    subagents: false,
    subagentsLimit: 1,
    resumeSessionId: nil,
    syncBack: true,
    keepWorkspaces: false,
    runsDir: nil,
    codexBin: nil
)
func runAgent(
    agent: Int = 1,
    status: String,
    canContinue: Bool? = nil,
    canAccept: Bool? = nil,
    recommended: Bool = true,
    workspaceAvailable: Bool = true,
    finalized: Bool = false,
    canRetry: Bool? = nil,
    seconds: Double? = nil,
    reasoningTokens: Int? = nil,
    reasoningTokenCounts: [String: Int] = [:]
) -> RunAgentSummary {
    RunAgentSummary(
        agent: agent,
        name: String(format: "AGENT-%03d", agent),
        role: nil,
        status: status,
        seconds: seconds,
        reasoningTokens: reasoningTokens,
        reasoningTokenCounts: reasoningTokenCounts,
        codexThreadId: nil,
        error: nil,
        rejected: false,
        stale: nil,
        recommended: recommended,
        finalized: finalized,
        workspaceAvailable: workspaceAvailable,
        canAccept: canAccept ?? (status == "success"),
        canContinue: canContinue ?? (status == "success"),
        canRetry: canRetry ?? false
    )
}

require(
    agentSelectionCommitsBeforeHydration(
        transcriptReady: false,
        agent: runAgent(status: "running")
    ),
    "A running Agent waits for transcript hydration before its tab changes"
)
require(
    agentSelectionCommitsBeforeHydration(
        transcriptReady: false,
        agent: runAgent(status: "queued")
    ),
    "A queued Agent waits for its first event before its tab changes"
)
require(
    agentSelectionCommitsBeforeHydration(
        transcriptReady: false,
        agent: runAgent(status: "pending")
    ),
    "A pending Agent cannot be selected before it starts"
)
require(
    agentSelectionCommitsBeforeHydration(
        transcriptReady: false,
        agent: runAgent(status: "success")
    ),
    "A completed Agent waits for work or file hydration before its tab changes"
)
require(
    !agentSelectionCommitsBeforeHydration(
        transcriptReady: true,
        agent: runAgent(status: "running")
    ),
    "An already hydrated Agent took the empty-branch transition"
)

let reasoningAgent = runAgent(
    status: "success",
    reasoningTokens: 9_999,
    reasoningTokenCounts: ["512": 2, "1024": 1, "128": 4]
)
require(
    reasoningAgent.reasoningSteps.map(\.interval) == [1024, 512, 128],
    "Reasoning steps were not ordered by contribution and interval"
)
require(
    reasoningAgent.reasoningSteps.map(\.contribution) == [1024, 1024, 512],
    "Reasoning step contributions were not calculated"
)
let chineseReasoningDisplay = reasoningAgent.reasoningDisplay(language: .chinese)
require(
    chineseReasoningDisplay?.hasPrefix("推理用量 ") == true
        && chineseReasoningDisplay?.contains("共 7 次") == true
        && chineseReasoningDisplay?.contains("total:") == false,
    "Chinese reasoning usage exposed raw internal labels"
)
require(
    reasoningAgent.compactReasoningDisplay(language: .chinese)?.contains("(") == false,
    "Compact reasoning usage retained the technical distribution"
)

let diffSummary = AgentDiffSummary.parse(
    """
    A  src/New.swift
    M  src/App.swift
    D  docs/old.md
    3 files changed

    diff --git a/src/New.swift b/src/New.swift
    """
)
require(
    diffSummary.changes == [
        AgentFileChange(status: "A", path: "src/New.swift"),
        AgentFileChange(status: "M", path: "src/App.swift"),
        AgentFileChange(status: "D", path: "docs/old.md"),
    ],
    "Agent file-change summary did not preserve status and path"
)
require(
    diffSummary.totalFiles == 3 && !diffSummary.truncated,
    "Agent file-change summary did not preserve its total"
)
func runSummary(
    runID: String = "run-terminal",
    status: String,
    eventCount: Int,
    agentStatus: String,
    prompt: String = "test",
    conversationID: String = "run-terminal",
    turnIndex: Int? = 0,
    createdAt: String = "",
    updatedAt: String = "",
    configuration: RunConfiguration = emptyRunConfiguration,
    sessionPromotion: RunSessionPromotion? = nil,
    recommendedAgent: Int? = 1,
    finalizedAgent: Int? = nil,
    workerActive: Bool = false,
    artifactsAvailable: Bool? = true,
    agents: [RunAgentSummary]? = nil
) -> RunSummary {
    RunSummary(
        runId: runID,
        status: status,
        workspace: "/tmp/project",
        prompt: prompt,
        conversationId: conversationID,
        continuedFromRunId: nil,
        continuedFromAgent: nil,
        turnIndex: turnIndex,
        createdAt: createdAt,
        updatedAt: updatedAt,
        runRoot: nil,
        config: configuration,
        recommendedAgent: recommendedAgent,
        finalizedAgent: finalizedAgent,
        syncedBack: false,
        workspacesDeleted: false,
        codexHomesDeleted: false,
        artifactsAvailable: artifactsAvailable,
        candidateWorkspacesAvailable: true,
        canAddAgents: true,
        canUpdateSynthesis: true,
        workerActive: workerActive,
        workerAvailable: true,
        workerPid: nil,
        workerState: nil,
        workerOperation: nil,
        workerDeadline: nil,
        workerError: nil,
        sessionPromotion: sessionPromotion,
        workspaceRevision: nil,
        error: nil,
        eventCount: eventCount,
        agents: agents ?? [runAgent(status: agentStatus)]
    )
}
let searchableRun = runSummary(
    runID: "searchable-run",
    status: "completed",
    eventCount: 1,
    agentStatus: "success",
    prompt: "Compare the gaze models",
    conversationID: "searchable-conversation",
    finalizedAgent: 1
)
let searchableConversation = WorkspaceConversationSummary(
    id: "searchable-conversation",
    title: "Model comparison",
    runs: [searchableRun],
    resumeSession: nil
)
let searchableSnapshot = WorkspaceRunViewSnapshot(
    selectedAgent: 1,
    transcripts: [
        1: AgentTranscript(
            sourceRunID: "searchable-run",
            sourceAgent: 1,
            blocks: [
                TranscriptBlock(
                    id: "final-search-answer",
                    kind: .output,
                    text: "The benchmark favors the baseline.",
                    isFinalAnswer: true
                ),
                TranscriptBlock(
                    id: "legacy-visible-answer",
                    kind: .output,
                    text: "Legacy visible answers remain searchable."
                ),
                TranscriptBlock(
                    id: "hidden-search-command",
                    kind: .command,
                    text: "internal-only-token"
                ),
            ],
            finalMessage: "The benchmark favors the baseline."
        ),
    ],
    conversationHistory: []
)
require(
    conversationSearchMatches(
        searchableConversation,
        query: "benchmark",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ),
    "Conversation search ignored the cached answer body"
)
require(
    conversationSearchMatches(
        searchableConversation,
        query: "legacy visible answers",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ),
    "Conversation search ignored a visible legacy output block"
)
require(
    conversationSearchMatches(
        searchableConversation,
        query: "gaze models",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ),
    "Conversation search ignored an earlier Turn prompt"
)
require(
    !conversationSearchMatches(
        searchableConversation,
        query: "internal-only-token",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ),
    "Conversation search exposed hidden command output as chat content"
)
let searchableResumeSnapshot = WorkspaceRunViewSnapshot(
    selectedAgent: 1,
    transcripts: [
        1: AgentTranscript(
            sourceRunID: "searchable-run",
            sourceAgent: 1,
            finalMessage: "The cached branch compares GazeFollower."
        ),
    ],
    conversationHistory: [
        ResumeHistoryEntry(
            category: "assistant",
            text: "Cached history mentions MPIIFaceGaze."
        ),
    ]
)
require(
    conversationSearchMatchText(
        searchableConversation, query: "benchmark",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ) == "The benchmark favors the baseline.",
    "Cached search results must expose the same answer text that matched"
)
require(
    conversationSearchMatchText(
        searchableConversation, query: "internal-only-token",
        snapshotsByRunID: ["searchable-run": searchableSnapshot]
    ) == nil,
    "Search snippets must not expose hidden command output"
)
require(
    conversationSearchSnippet("First line\nCAFÉ and 中文内容", query: "cafe")
        == "First line CAFÉ and 中文内容",
    "Search snippets must preserve display text while matching accents and folding line breaks"
)
require(
    conversationSearchSnippet(String(repeating: "前", count: 200) + "目标" + String(repeating: "后", count: 200), query: "目标")
        == "…" + String(repeating: "前", count: 48) + "目标" + String(repeating: "后", count: 120) + "…",
    "Search snippets must stay bounded around the match without splitting Unicode characters"
)
require(conversationSearchSnippet("A reply", query: " ") == nil, "Empty queries must not create snippets")
require(
    conversationSearchMatches(
        searchableConversation,
        query: "MPIIFaceGaze",
        resumeSnapshot: searchableResumeSnapshot
    ),
    "Conversation search ignored the visible resume history"
)
require(
    conversationSearchMatches(
        searchableConversation,
        query: "GazeFollower",
        resumeSnapshot: searchableResumeSnapshot
    ),
    "Conversation search ignored the visible cached branch"
)
let timelineAgents = [
    runAgent(agent: 1, status: "success"),
    runAgent(agent: 2, status: "success", recommended: false),
]
let liveSwitchState = ConversationAgentSwitchState(
    runID: "live-switch-run",
    agents: [
        runAgent(
            agent: 1,
            status: "running",
            seconds: 1,
            reasoningTokens: 128,
            reasoningTokenCounts: ["128": 1]
        ),
        runAgent(agent: 2, status: "queued", recommended: false),
    ],
    selectedAgent: 1,
    recommendedAgent: nil,
    finalizedAgent: nil,
    selectionEnabled: true
)
let advancedLiveSwitchState = ConversationAgentSwitchState(
    runID: "live-switch-run",
    agents: [
        runAgent(
            agent: 1,
            status: "running",
            seconds: 9,
            reasoningTokens: 1_024,
            reasoningTokenCounts: ["128": 8]
        ),
        runAgent(agent: 2, status: "queued", recommended: false),
    ],
    selectedAgent: 1,
    recommendedAgent: nil,
    finalizedAgent: nil,
    selectionEnabled: true
)
require(
    liveSwitchState == advancedLiveSwitchState,
    "Live elapsed-time and reasoning polls rebuild the Agent switcher during clicks"
)
let finishedSwitchState = ConversationAgentSwitchState(
    runID: "live-switch-run",
    agents: [
        runAgent(
            agent: 1,
            status: "success",
            seconds: 9,
            reasoningTokens: 1_024,
            reasoningTokenCounts: ["128": 8]
        ),
        runAgent(agent: 2, status: "queued", recommended: false),
    ],
    selectedAgent: 1,
    recommendedAgent: nil,
    finalizedAgent: nil,
    selectionEnabled: true
)
require(
    liveSwitchState != finishedSwitchState,
    "A terminal Agent status no longer refreshes the switcher"
)
let singleAgentTimeline = ConversationTimelineProjection.make(
    blocks: [TranscriptBlock(id: "single-answer", kind: .output, text: "Only answer")],
    switchState: ConversationAgentSwitchState(
        runID: "single-agent-run",
        agents: [runAgent(agent: 1, status: "success")],
        selectedAgent: 1,
        recommendedAgent: 1,
        finalizedAgent: 1,
        selectionEnabled: true
    )
)
require(
    singleAgentTimeline.map(\.id) == ["message:single-answer"],
    "A non-interactive one-Agent selector adds technical noise to the transcript"
)
let stableTimelinePrefix = [
    TranscriptBlock(
        id: "history-user",
        kind: .user,
        text: "Previous question",
        isHistorical: true
    ),
    TranscriptBlock(
        id: "history-answer",
        kind: .output,
        text: "Previously accepted answer",
        isHistorical: true,
        isFinalAnswer: true
    ),
    TranscriptBlock(id: "current-prompt", kind: .user, text: "Current question"),
]
let agentOneTimeline = ConversationTimelineProjection.make(
    blocks: stableTimelinePrefix + [
        TranscriptBlock(id: "agent-one-answer", kind: .output, text: "First candidate")
    ],
    switchState: ConversationAgentSwitchState(
        runID: "run-current",
        agents: timelineAgents,
        selectedAgent: 1,
        recommendedAgent: 1,
        finalizedAgent: nil,
        selectionEnabled: true
    )
)
let agentTwoTimeline = ConversationTimelineProjection.make(
    blocks: stableTimelinePrefix + [
        TranscriptBlock(id: "agent-two-answer", kind: .output, text: "Second candidate")
    ],
    switchState: ConversationAgentSwitchState(
        runID: "run-current",
        agents: timelineAgents,
        selectedAgent: 2,
        recommendedAgent: 1,
        finalizedAgent: nil,
        selectionEnabled: true
    )
)
let pendingAgentTimeline = ConversationTimelineProjection.make(
    blocks: stableTimelinePrefix + [
        TranscriptBlock(id: "agent-one-answer", kind: .output, text: "First candidate")
    ],
    switchState: ConversationAgentSwitchState(
        runID: "run-current",
        agents: timelineAgents,
        selectedAgent: 1,
        pendingAgent: 2,
        recommendedAgent: 1,
        finalizedAgent: nil,
        selectionEnabled: true
    )
)
require(
    {
        guard case .agentSwitch(let state) = pendingAgentTimeline[3] else { return false }
        return state.highlightedAgent == 2 && state.selectedAgent == 1
    }(),
    "A candidate click does not move the selection indicator until its transcript finishes loading"
)
let pendingAgentPlan = ConversationTimelineUpdatePlan.make(
    old: agentOneTimeline,
    new: pendingAgentTimeline
)
let conversationFindMatches = ConversationFindIndex.matches(
    in: agentOneTimeline,
    query: "previous"
)
require(
    conversationFindMatches == [
        ConversationFindMatch(itemID: "message:history-user", occurrence: 0),
        ConversationFindMatch(itemID: "message:history-answer", occurrence: 0),
    ],
    "Find in Conversation did not preserve transcript order"
)
require(
    ConversationFindIndex.matches(
        in: agentOneTimeline,
        query: "CANDIDATE"
    ) == [ConversationFindMatch(itemID: "message:agent-one-answer", occurrence: 0)],
    "Find in Conversation is unexpectedly case-sensitive"
)
require(
    ConversationFindIndex.matches(
        in: agentOneTimeline,
        query: "   "
    ).isEmpty,
    "An empty conversation search reported matches"
)
let repeatedFindTimeline = ConversationTimelineProjection.make(
    blocks: [TranscriptBlock(
        id: "repeated",
        kind: .output,
        text: "Universal first; universal second"
    )],
    switchState: nil
)
require(
    ConversationFindIndex.matches(
        in: repeatedFindTimeline,
        query: "UNIVERSAL"
    ) == [
        ConversationFindMatch(itemID: "message:repeated", occurrence: 0),
        ConversationFindMatch(itemID: "message:repeated", occurrence: 1),
    ],
    "Find in Conversation collapsed repeated matches in one message"
)
require(
    ConversationFindIndex.advancedSelection(0, count: 2, delta: -1) == 1
        && ConversationFindIndex.advancedSelection(1, count: 2, delta: 1) == 0,
    "Conversation find navigation does not wrap in both directions"
)
let firstFindCommand = ConversationFindCommandRequest.idle.advanced(to: .open)
let repeatedFindCommand = firstFindCommand.advanced(to: .open)
let nextFindCommand = repeatedFindCommand.advanced(to: .next)
require(
    firstFindCommand.sequence == 1
        && repeatedFindCommand.sequence == 2
        && repeatedFindCommand.command == .open
        && nextFindCommand.sequence == 3
        && nextFindCommand.command == .next,
    "Repeated conversation find commands were collapsed instead of dispatched"
)
require(
    !pendingAgentPlan.reloadAll
        && pendingAgentPlan.removedIndices.isEmpty
        && pendingAgentPlan.insertedIndices.isEmpty
        && pendingAgentPlan.changedIndices == [3],
    "Loading another Agent repainted or removed the visible conversation"
)
require(
    ConversationTimelineProjection.changedInlineControlID(
        old: agentOneTimeline,
        new: pendingAgentTimeline
    ) == nil,
    "A pending Agent selection incorrectly treated the fixed switcher as a scroll anchor"
)
require(
    ConversationTimelineProjection.changesOnlyFixedAgentSwitch(
        old: agentOneTimeline,
        new: pendingAgentTimeline
    ),
    "A status-only Agent switcher update was mistaken for transcript geometry work"
)
require(
    Array(agentOneTimeline.prefix(4).map(\.id))
        == Array(agentTwoTimeline.prefix(4).map(\.id)),
    "Switching Agents changed the accepted history or current prompt identity"
)
require(
    agentOneTimeline[2].id == "message:current-prompt"
        && agentOneTimeline[3].id == "agent-switch:run-current",
    "The Agent switcher is not immediately below the current prompt"
)
require(
    agentOneTimeline.count {
        if case .message(let block) = $0 { return block.id == "current-prompt" }
        return false
    } == 1,
    "The current turn prompt is repeated for each Agent branch"
)
require(
    agentOneTimeline.last?.id == "message:agent-one-answer"
        && agentTwoTimeline.last?.id == "message:agent-two-answer",
    "Switching Agents did not replace only the current candidate branch"
)
let branchSwitchUpdate = ConversationTimelineUpdatePlan.make(
    old: agentOneTimeline,
    new: agentTwoTimeline
)
require(
    !branchSwitchUpdate.reloadAll
        && branchSwitchUpdate.changedIndices == [3]
        && branchSwitchUpdate.removedIndices == [4]
        && branchSwitchUpdate.insertedIndices == [4],
    "Switching Agents still reloads the accepted conversation trunk"
)
require(
    ConversationTimelineProjection.committedAgentSwitchID(
        old: pendingAgentTimeline,
        new: agentTwoTimeline
    ) == "agent-switch:run-current",
    "A completed Agent load cannot commit its label and candidate body atomically"
)
require(
    ConversationTimelineProjection.changedInlineControlID(
        old: pendingAgentTimeline,
        new: agentTwoTimeline
    ) == "agent-switch:run-current",
    "A committed Agent branch change no longer preserves the switcher's viewport position"
)
require(
    !ConversationTimelineProjection.changesOnlyFixedAgentSwitch(
        old: pendingAgentTimeline,
        new: agentTwoTimeline
    ),
    "A committed Agent branch replacement was mistaken for a fixed switcher-only update"
)
require(
    ConversationTimelineProjection.committedAgentSwitchID(
        old: agentOneTimeline,
        new: pendingAgentTimeline
    ) == nil,
    "Starting an Agent load was mistaken for a completed branch replacement"
)
let terminalSnapshot = runSummary(status: "completed", eventCount: 10, agentStatus: "success")
require(
    conversationBranchPrimaryAction(
        run: terminalSnapshot,
        agent: terminalSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil,
        hasPendingSubmission: true
    ) == .none,
    "A pending continuation still exposes a second acceptance of its source branch"
)
require(
    conversationBranchPrimaryAction(
        run: terminalSnapshot,
        agent: terminalSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .useResult(stopsOthers: false),
    "A completed branch does not expose the explicit Use Result action"
)
require(
    conversationBranchPrimaryAction(
        run: terminalSnapshot,
        agent: terminalSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: false,
        operation: nil
    ) == .enableSyncBack,
    "The branch action claims a result can be used while Sync Back is disabled"
)
let partiallyCompletedWorkingSnapshot = runSummary(
    status: "running",
    eventCount: 6,
    agentStatus: "running",
    workerActive: true,
    agents: [
        runAgent(agent: 1, status: "success"),
        runAgent(agent: 2, status: "running", recommended: false),
    ]
)
require(
    ConversationContextTransitionPlan.make(
        destination: .workspace,
        run: partiallyCompletedWorkingSnapshot,
        selectedAgent: partiallyCompletedWorkingSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        hasPendingSubmission: false,
        hasRunOperation: false
    ) == .proceed,
    "Changing Workspace still resolves or finalizes the visible Agent"
)
require(
    ConversationContextTransitionPlan.make(
        destination: .workspace,
        run: partiallyCompletedWorkingSnapshot,
        selectedAgent: partiallyCompletedWorkingSnapshot.agents[1],
        isLatestWorkspaceRun: true,
        syncBack: true,
        hasPendingSubmission: true,
        hasRunOperation: true
    ) == .proceed,
    "Changing Workspace is still blocked by work in the current Workspace"
)
require(
    {
        let visibleBranchSnapshot = runSummary(
            status: "running",
            eventCount: 6,
            agentStatus: "running",
            recommendedAgent: 1,
            workerActive: true,
            agents: [
                runAgent(agent: 1, status: "running"),
                runAgent(agent: 2, status: "success", recommended: false),
            ]
        )
        return ConversationContextTransitionPlan.make(
            destination: .applicationExit,
            run: visibleBranchSnapshot,
            selectedAgent: visibleBranchSnapshot.agents[1],
            isLatestWorkspaceRun: true,
            syncBack: true,
            hasPendingSubmission: false,
            hasRunOperation: false
        ) == .proceed
    }(),
    "Quitting implicitly adopted a browsed Agent and stopped the remaining work"
)
require(
    ConversationContextTransitionPlan.make(
        destination: .applicationExit,
        run: partiallyCompletedWorkingSnapshot,
        selectedAgent: partiallyCompletedWorkingSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        hasPendingSubmission: false,
        hasRunOperation: false
    ) == .proceed,
    "Quitting adopted a viewed branch instead of leaving continuation to the daemon queue"
)
require(
    {
        let plan = ConversationContextTransitionPlan.make(
            destination: .resumeSession,
            run: partiallyCompletedWorkingSnapshot,
            selectedAgent: partiallyCompletedWorkingSnapshot.agents[1],
            isLatestWorkspaceRun: true,
            syncBack: true,
            hasPendingSubmission: false,
            hasRunOperation: false
        )
        if case .blocked = plan { return true }
        return false
    }(),
    "Resume selection changed underneath an unfinished visible Agent"
)
require(
    ConversationContextTransitionPlan.make(
        destination: .workspace,
        run: partiallyCompletedWorkingSnapshot,
        selectedAgent: partiallyCompletedWorkingSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: false,
        hasPendingSubmission: false,
        hasRunOperation: false
    ) == .proceed,
    "Changing Workspace overrode the user's disabled Sync Back setting"
)
require(
    conversationBranchPrimaryAction(
        run: partiallyCompletedWorkingSnapshot,
        agent: partiallyCompletedWorkingSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .useResult(stopsOthers: true),
    "Using an early successful result does not disclose that unfinished Agents stop"
)
require(
    conversationBranchPrimaryAction(
        run: partiallyCompletedWorkingSnapshot,
        agent: partiallyCompletedWorkingSnapshot.agents[1],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .stopAgent,
    "A running branch does not expose its stop action"
)
let retryableAgent = runAgent(
    status: "failed",
    canAccept: false,
    canRetry: true
)
let retryableSnapshot = runSummary(
    status: "completed",
    eventCount: 4,
    agentStatus: "failed",
    agents: [retryableAgent]
)
require(
    conversationBranchPrimaryAction(
        run: retryableSnapshot,
        agent: retryableAgent,
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .retryAgent,
    "A failed retryable branch does not expose Retry"
)
let finalizingAgent = runAgent(status: "success", finalized: true)
let finalizingSnapshot = runSummary(
    status: "finalizing",
    eventCount: 12,
    agentStatus: "success",
    finalizedAgent: 1,
    workerActive: true,
    agents: [finalizingAgent]
)
require(
    conversationBranchPrimaryAction(
        run: finalizingSnapshot,
        agent: finalizingAgent,
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .progress("Applying Result"),
    "A reopened finalization exposes a second Use Result action"
)
let finalizedBranchSnapshot = runSummary(
    status: "finalized",
    eventCount: 13,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [
        finalizingAgent,
        runAgent(agent: 2, status: "success", recommended: false),
    ]
)
let finalizedCleanedAgent = runAgent(
    status: "success",
    workspaceAvailable: false,
    finalized: true
)
let finalizedCleanedSnapshot = runSummary(
    status: "finalized",
    eventCount: 14,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [finalizedCleanedAgent]
)
require(
    conversationAgentDisplayStatus(
        run: finalizedCleanedSnapshot,
        agent: finalizedCleanedAgent
    ) == "Used"
        && conversationAgentDisplayStatus(
            run: finalizedCleanedSnapshot,
            agent: finalizedCleanedAgent,
            language: .chinese
        ) == "已采用",
    "A used result is presented as unavailable after its temporary workspace is cleaned"
)
require(
    conversationAgentGlyphStatus(
        run: finalizedCleanedSnapshot,
        agent: finalizedCleanedAgent
    ) == "success",
    "A used result receives an unavailable warning glyph after cleanup"
)
let finalizedReadOnlyAgent = runAgent(
    agent: 2,
    status: "success",
    recommended: false,
    workspaceAvailable: false
)
let finalizedReadOnlySnapshot = runSummary(
    status: "finalized",
    eventCount: 15,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [finalizedCleanedAgent, finalizedReadOnlyAgent]
)
require(
    conversationAgentDisplayStatus(
        run: finalizedReadOnlySnapshot,
        agent: finalizedReadOnlyAgent
    ) == "Completed · Read only"
        && conversationAgentDisplayStatus(
            run: finalizedReadOnlySnapshot,
            agent: finalizedReadOnlyAgent,
            language: .chinese
        ) == "已完成 · 只读",
    "A completed alternative is presented as unavailable after the adopted result cleans temporary workspaces"
)
require(
    conversationAgentGlyphStatus(
        run: finalizedReadOnlySnapshot,
        agent: finalizedReadOnlyAgent
    ) == "success",
    "A completed read-only alternative receives an unavailable warning glyph"
)
require(
    conversationBranchPrimaryAction(
        run: finalizedBranchSnapshot,
        agent: finalizedBranchSnapshot.agents[0],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .used,
    "The adopted branch is not identified after finalization"
)
require(
    conversationBranchPrimaryAction(
        run: finalizedBranchSnapshot,
        agent: finalizedBranchSnapshot.agents[1],
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .previewUsed(1),
    "Previewing an unused branch hides which branch was actually adopted"
)
let ambiguousSnapshot = runSummary(
    status: "sync_ambiguous",
    eventCount: 13,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [finalizingAgent]
)
require(
    conversationBranchPrimaryAction(
        run: ambiguousSnapshot,
        agent: finalizingAgent,
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .resolveSync,
    "An interrupted workspace sync does not expose its recovery action"
)
let cleanupFailedSnapshot = runSummary(
    status: "cleanup_failed",
    eventCount: 13,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [finalizingAgent]
)
require(
    conversationBranchPrimaryAction(
        run: cleanupFailedSnapshot,
        agent: finalizingAgent,
        isLatestWorkspaceRun: true,
        syncBack: true,
        operation: nil
    ) == .retryCleanup,
    "A cleanup failure does not expose Retry Cleanup"
)
let unavailableAgent = runAgent(status: "success", workspaceAvailable: false)
require(
    unavailableAgent.compactStatusLabel == "Unavailable",
    "A successful Agent whose retained workspace is gone is presented as usable"
)
require(
    unavailableAgent.accessibilityLabel.contains("Unavailable"),
    "Accessibility still announces an unavailable result as successful"
)
let unavailableResultSnapshot = runSummary(
    status: "completed",
    eventCount: 10,
    agentStatus: "success",
    agents: [unavailableAgent]
)
for (status, expectedPhase, chineseLabel, englishLabel) in [
    ("discarded", ConversationRunPhase.discarded, "已放弃", "Discarded"),
    ("expired", ConversationRunPhase.expired, "已过期", "Expired"),
] {
    let releasedResult = runSummary(
        status: status,
        eventCount: 10,
        agentStatus: "success",
        agents: [unavailableAgent]
    )
    let presentation = ConversationRunPresentation.make(run: releasedResult)
    require(
        presentation.phase == expectedPhase
            && presentation.tone == .neutral
            && presentation.navigationLabel(language: .chinese) == chineseLabel
            && presentation.navigationLabel == englishLabel,
        "Intentional result disposal is incorrectly reported as a task failure"
    )
}
require(
    ConversationRunPresentation.make(run: unavailableResultSnapshot).label
        == "Results unavailable",
    "A turn whose candidate workspaces are gone is presented as completed"
)
require(
    ConversationRunPresentation.make(run: terminalSnapshot).phase == .review,
    "A completed successful Run is not presented as reviewable"
)
require(
    ConversationRunPresentation.make(run: terminalSnapshot).label
        == "Ready to review · 1 result",
    "A reviewable Run does not explain that the result still needs a user decision"
)
let startingAgentsSnapshot = runSummary(
    status: "running",
    eventCount: 0,
    agentStatus: "queued",
    workerActive: true,
    agents: []
)
require(
    ConversationRunPresentation.make(run: startingAgentsSnapshot).label == "Starting Agents",
    "A newly accepted Run is presented as zero Agents already working"
)
let synthesisConfiguration = RunConfiguration(
    numAgents: 1,
    maxParallel: 1,
    serial: false,
    recommendBy: "duration",
    model: nil,
    effort: nil,
    fast: nil,
    synthesisAgents: 2,
    followUpDelay: 60,
    synthesisModel: nil,
    synthesisEffort: nil,
    synthesisFast: nil,
    subagents: false,
    subagentsLimit: 1,
    resumeSessionId: nil,
    syncBack: true,
    keepWorkspaces: false,
    runsDir: nil,
    codexBin: nil
)
let preparingSynthesisSnapshot = runSummary(
    status: "running",
    eventCount: 10,
    agentStatus: "success",
    configuration: synthesisConfiguration,
    workerActive: true
)
require(
    ConversationRunPresentation.make(run: preparingSynthesisSnapshot).label
        == "Preparing synthesis",
    "The gap before Synthesis is presented as completed primary Agents still working"
)
let finishingSnapshot = runSummary(
    status: "running",
    eventCount: 12,
    agentStatus: "success",
    configuration: synthesisConfiguration,
    workerActive: true,
    agents: [
        runAgent(agent: 1, status: "success"),
        RunAgentSummary(
            agent: 2,
            name: "AGENT-002",
            role: "synthesis",
            status: "success",
            seconds: nil,
            reasoningTokens: nil,
            reasoningTokenCounts: [:],
            codexThreadId: nil,
            error: nil,
            rejected: false,
            stale: nil,
            recommended: true,
            finalized: false,
            workspaceAvailable: true,
            canAccept: true,
            canContinue: true,
            canRetry: false
        ),
    ]
)
require(
    ConversationRunPresentation.make(run: finishingSnapshot).label == "Finishing task",
    "A Run with terminal Agents and pending worker cleanup regresses to Agents working"
)
let runningSelectionSnapshot = runSummary(
    status: "running",
    eventCount: 9,
    agentStatus: "running",
    recommendedAgent: 2,
    agents: [
        runAgent(agent: 1, status: "running", recommended: false),
        runAgent(agent: 2, status: "running", recommended: true),
    ]
)
let reviewSelectionSnapshot = runSummary(
    status: "completed",
    eventCount: 10,
    agentStatus: "success",
    recommendedAgent: 2,
    agents: [
        runAgent(agent: 1, status: "success", recommended: false),
        runAgent(agent: 2, status: "success", recommended: true),
    ]
)
require(
    selectedAgentAfterRunUpdate(
        previousRun: runningSelectionSnapshot,
        currentSelection: 1,
        updatedRun: reviewSelectionSnapshot
    ) == 2,
    "Entering review did not select the recommended result"
)
require(
    selectedAgentAfterRunUpdate(
        previousRun: runningSelectionSnapshot,
        currentSelection: 1,
        updatedRun: reviewSelectionSnapshot,
        explicitSelection: 1
    ) == 1,
    "Completion replaced the branch the user explicitly selected while it was running"
)
require(
    selectedAgentAfterRunUpdate(
        previousRun: nil,
        currentSelection: 1,
        updatedRun: reviewSelectionSnapshot
    ) == 1,
    "Reopening a completed turn erased the user's remembered result selection"
)
require(
    selectedAgentForRestoredRun(
        explicitSelection: nil,
        cachedSelection: 1,
        run: reviewSelectionSnapshot
    ) == 2,
    "An automatic running-state selection overrode the completed Run recommendation"
)
require(
    selectedAgentForRestoredRun(
        explicitSelection: 1,
        cachedSelection: 2,
        run: reviewSelectionSnapshot
    ) == 1,
    "A result explicitly selected during review was not restored"
)
let invalidRecommendationSnapshot = runSummary(
    status: "completed",
    eventCount: 10,
    agentStatus: "success",
    recommendedAgent: 2,
    agents: [
        runAgent(agent: 1, status: "success", recommended: false),
        runAgent(agent: 2, status: "failed", recommended: true),
    ]
)
require(
    selectedAgentForRestoredRun(
        explicitSelection: nil,
        cachedSelection: nil,
        run: invalidRecommendationSnapshot
    ) == 1,
    "An unavailable recommendation hides the remaining usable result"
)
let legacyDesktopSelection = try JSONDecoder().decode(
    PersistedDesktopState.self,
    from: Data(#"{"selectedAgentsByContext":{"legacy-run":1}}"#.utf8)
)
require(
    legacyDesktopSelection.explicitSelectedAgentsByContext.isEmpty,
    "Ambiguous automatic Agent selections from older builds were migrated as user choices"
)
let explicitDesktopSelection = try JSONDecoder().decode(
    PersistedDesktopState.self,
    from: Data(#"{"explicitSelectedAgentsByContext":{"current-run":2}}"#.utf8)
)
require(
    explicitDesktopSelection.explicitSelectedAgentsByContext["current-run"] == 2,
    "An explicit Agent selection did not survive desktop-state decoding"
)
let stoppedSnapshot = runSummary(status: "completed", eventCount: 10, agentStatus: "killed")
require(
    ConversationRunPresentation.make(run: stoppedSnapshot).label == "No usable results",
    "A completed Run with no successful Agent is presented as successful"
)
let partiallySuccessfulStoppedSnapshot = runSummary(
    status: "stopped",
    eventCount: 10,
    agentStatus: "success",
    agents: [
        runAgent(agent: 1, status: "success"),
        runAgent(agent: 2, status: "killed"),
    ]
)
require(
    partiallySuccessfulStoppedSnapshot.isReviewable
        && ConversationRunPresentation.make(run: partiallySuccessfulStoppedSnapshot).phase == .review,
    "Stopping unfinished Agents hides a successful result that can still be reviewed"
)
let discardedSnapshot = runSummary(
    status: "discarded",
    eventCount: 10,
    agentStatus: "success"
)
require(
    !discardedSnapshot.isReviewable,
    "A discarded Run is still treated as an unresolved result selection"
)
require(
    PromptSubmissionPlan.make(
        run: discardedSnapshot,
        selectedAgent: discardedSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .start(resuming: false),
    "A discarded Run blocks a new task because its historical Agent was treated as usable"
)
require(
    PromptSubmissionPlan.make(
        run: terminalSnapshot,
        selectedAgent: terminalSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .continueFrom(
        runID: "run-terminal",
        agent: 1,
        agentName: "AGENT-001",
        stopsRemainingAgents: false
    ),
    "A reviewable branch does not produce the continuation action shown by the composer"
)
require(
    PromptSubmissionPlan.make(
        run: terminalSnapshot,
        selectedAgent: terminalSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: false,
        hasSelectedResumeSession: false
    ) == .blocked("Continuing from a result requires workspace sync-back"),
    "A completed branch can continue without synchronizing its workspace result"
)
let finalizedSnapshot = runSummary(
    status: "finalized",
    eventCount: 12,
    agentStatus: "success",
    finalizedAgent: 1
)
require(
    PromptSubmissionPlan.make(
        run: finalizedSnapshot,
        selectedAgent: finalizedSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: true
    ) == .continueFrom(
        runID: "run-terminal",
        agent: 1,
        agentName: "AGENT-001",
        stopsRemainingAgents: false
    ),
    "An applied result silently starts a new PCR conversation instead of preserving lineage"
)
for (reason, action) in [
    ("Open the latest turn in this conversation to continue", PromptComposerRecoveryAction.openLatestConversationTurn),
    ("A newer turn is still running in this workspace", .openLatestWorkspaceTurn),
    ("A newer turn is waiting for review", .openLatestWorkspaceTurn),
    ("Discard this run before starting another task", .discardCurrentTurn),
] {
    require(
        PromptSubmissionPlan.blocked(reason).recoveryAction == action,
        "Composer recovery does not target the actual blocked condition: \(reason)"
    )
}
require(
    PromptSubmissionPlan.blocked("Select a successful Agent to continue").recoveryAction == nil
        && PromptSubmissionPlan.blocked("Resolve the previous result before continuing").recoveryAction == nil
        && PromptSubmissionPlan.enqueue.recoveryAction == nil,
    "An unrelated composer condition offers navigation to a different conversation"
)
for availability in [false, true, nil] as [Bool?] {
    let unavailableRun = runSummary(
        status: "completed", eventCount: 10, agentStatus: "failed",
        artifactsAvailable: availability
    )
    let plan = PromptSubmissionPlan.make(
        run: unavailableRun, selectedAgent: unavailableRun.agents[0],
        hasQueuedFollowUps: false, syncBack: true, hasSelectedResumeSession: true
    )
    require(
        !plan.isAvailable
            && plan.recoveryAction == (availability == false ? .startNewConversation : .discardCurrentTurn),
        "Missing working files offer an impossible discard, or unknown availability bypasses discard"
    )
}
let cleanedFinalizedRun = runSummary(
    status: "finalized", eventCount: 12, agentStatus: "success",
    finalizedAgent: 1, artifactsAvailable: false
)
require(
    PromptSubmissionPlan.make(
        run: cleanedFinalizedRun, selectedAgent: cleanedFinalizedRun.agents[0],
        hasQueuedFollowUps: false, syncBack: true, hasSelectedResumeSession: true
    ) == .continueFrom(runID: cleanedFinalizedRun.runId, agent: 1, agentName: "AGENT-001", stopsRemainingAgents: false),
    "Cleaning adopted working files prevents continuing a valid promoted session"
)
let firstTurn = runSummary(
    runID: "run-first",
    status: "finalized",
    eventCount: 12,
    agentStatus: "success",
    prompt: "  Fix   the parser\nwith tests ",
    conversationID: "conversation-a",
    turnIndex: 0,
    createdAt: "2026-08-25T01:00:00Z",
    updatedAt: "2026-08-25T01:01:00Z",
    finalizedAgent: 1
)
let secondTurn = runSummary(
    runID: "run-second",
    status: "completed",
    eventCount: 8,
    agentStatus: "success",
    prompt: "Add the missing regression test",
    conversationID: "conversation-a",
    turnIndex: 1,
    createdAt: "2026-08-25T02:00:00Z",
    updatedAt: "2026-08-25T02:01:00Z"
)
let otherConversation = runSummary(
    runID: "run-other",
    status: "completed",
    eventCount: 4,
    agentStatus: "success",
    prompt: "Review the release",
    conversationID: "conversation-b",
    turnIndex: 0,
    createdAt: "2026-08-25T03:00:00Z",
    updatedAt: "2026-08-25T03:01:00Z"
)
let conversations = workspaceConversationSummaries(
    from: [secondTurn, otherConversation, firstTurn]
)
require(
    conversations.map(\.id) == ["conversation-b", "conversation-a"],
    "Workspace conversations are not ordered by their latest turn"
)
require(
    conversations[1].runs.map(\.runId) == ["run-first", "run-second"],
    "Conversation turns are not ordered by lineage"
)
require(
    conversations[1].title == "Fix the parser with tests",
    "Conversation titles are not derived from the root user prompt"
)
require(
    cachedConversationTitle(from: [
        ResumeHistoryEntry(category: "user", text: "Root question for this conversation"),
        ResumeHistoryEntry(category: "output", text: "Answer"),
        ResumeHistoryEntry(category: "user", text: "Newest follow-up"),
    ]) == "Root question for this conversation",
    "A cache-first conversation title changes when older turns finish loading"
)
let greetingTurn = runSummary(
    runID: "run-greeting",
    status: "finalized",
    eventCount: 4,
    agentStatus: "success",
    prompt: "你好。",
    conversationID: "conversation-greeting",
    turnIndex: 0,
    createdAt: "2026-08-25T00:00:00Z",
    updatedAt: "2026-08-25T00:01:00Z",
    finalizedAgent: 1
)
let meaningfulGreetingFollowUp = runSummary(
    runID: "run-greeting-follow-up",
    status: "completed",
    eventCount: 8,
    agentStatus: "success",
    prompt: "修复重复回答，并保留我的输入。",
    conversationID: "conversation-greeting",
    turnIndex: 1,
    createdAt: "2026-08-25T01:00:00Z",
    updatedAt: "2026-08-25T01:01:00Z"
)
require(
    workspaceConversationSummaries(
        from: [meaningfulGreetingFollowUp, greetingTurn]
    ).first?.title == "修复重复回答，并保留我的输入。"
        && cachedConversationTitle(from: [
            ResumeHistoryEntry(category: "user", text: "你好"),
            ResumeHistoryEntry(category: "output", text: "你好，请问需要什么帮助？"),
            ResumeHistoryEntry(category: "user", text: "修复重复回答，并保留我的输入。"),
        ]) == "修复重复回答，并保留我的输入。",
    "A generic greeting remained the permanent conversation title"
)
let cliSession = ResumeSession(
    sessionId: "cli-session",
    title: "Continued from PCR CLI",
    cwd: "/tmp/project",
    updatedAt: 1_900_000_000,
    createdAt: 1_899_999_000,
    source: "jsonl",
    model: "gpt-5.6-sol",
    rolloutPath: "/tmp/rollout.jsonl",
    preview: "Continued from PCR CLI",
    tokensUsed: nil
)
let conversationsWithCLI = workspaceConversationSummaries(
    from: [secondTurn, otherConversation, firstTurn],
    resumeSessions: [cliSession]
)
require(
    conversationsWithCLI.first?.resumeSession?.sessionId == "cli-session"
        && conversationsWithCLI.first?.runs.isEmpty == true,
    "A standalone CLI or TUI Codex conversation was omitted from the app index"
)
require(
    conversationMatchingNavigationID(
        "resume:cli-session",
        in: conversationsWithCLI
    )?.resumeSession?.sessionId == "cli-session"
        && conversationMatchingNavigationID(
            "cli-session",
            in: conversationsWithCLI
        )?.resumeSession?.sessionId == "cli-session",
    "A saved standalone conversation cannot be restored across resume ID formats"
)
require(
    conversationMatchingNavigationID(
        firstTurn.runId,
        in: conversationsWithCLI
    )?.id == "conversation-a",
    "A saved Run navigation ID cannot be repaired to its conversation"
)
require(
    conversationRefreshMayOpenLatestTurn(
        visibleRunID: nil,
        previousLatestRunID: firstTurn.runId,
        latestRunID: secondTurn.runId
    )
        && conversationRefreshMayOpenLatestTurn(
            visibleRunID: secondTurn.runId,
            previousLatestRunID: firstTurn.runId,
            latestRunID: secondTurn.runId
        )
        && conversationRefreshMayOpenLatestTurn(
            visibleRunID: firstTurn.runId,
            previousLatestRunID: firstTurn.runId,
            latestRunID: secondTurn.runId
        )
        && !conversationRefreshMayOpenLatestTurn(
            visibleRunID: "older-than-previous-latest",
            previousLatestRunID: firstTurn.runId,
            latestRunID: secondTurn.runId
        )
        && !conversationRefreshMayOpenLatestTurn(
            visibleRunID: nil,
            previousLatestRunID: firstTurn.runId,
            latestRunID: secondTurn.runId,
            opensResumeHistory: true
        ),
    "A background refresh did not follow a remote Turn or replaced newer direct history"
)
require(
    workspaceSurfaceNeedsBlockingLoading(
        hydrating: true,
        composingNewConversation: false,
        hasRenderableContent: false,
        hasPendingSubmission: false
    )
        && !workspaceSurfaceNeedsBlockingLoading(
            hydrating: true,
            composingNewConversation: false,
            hasRenderableContent: true,
            hasPendingSubmission: false
        )
        && !workspaceSurfaceNeedsBlockingLoading(
            hydrating: true,
            composingNewConversation: true,
            hasRenderableContent: false,
            hasPendingSubmission: false
        )
        && !workspaceSurfaceNeedsBlockingLoading(
            hydrating: true,
            composingNewConversation: false,
            hasRenderableContent: false,
            hasPendingSubmission: true
        ),
    "Background workspace hydration blocked an already usable conversation surface"
)
require(
    !runViewCacheNeedsHydration(
        activeRunID: "run-visible",
        requestedRunID: "run-visible"
    ),
    "Polling a visible Run would replay its disk cache"
)
require(
    runViewCacheNeedsHydration(
        activeRunID: "run-previous",
        requestedRunID: "run-destination"
    ) && runViewCacheNeedsHydration(
        activeRunID: nil,
        requestedRunID: "run-destination"
    ),
    "Opening a destination Run no longer hydrates its first-paint cache"
)
require(
    !transcriptRestoreWidthIsUsable(0)
        && !transcriptRestoreWidthIsUsable(1)
        && transcriptRestoreWidthIsUsable(2)
        && !transcriptRestoreWidthIsUsable(.infinity),
    "A timeline restore can complete against provisional startup geometry"
)
let stableCacheDirectory = FileManager.default.temporaryDirectory
    .appendingPathComponent("pcr-stable-run-cache-\(UUID().uuidString)", isDirectory: true)
let stableDiskCache = WorkspaceRunDiskCache(directory: stableCacheDirectory)
let stableViewStore = WorkspaceRunViewStateStore(diskCache: stableDiskCache)
let stableRunKey = WorkspaceRunViewKey(
    machineID: "machine-cache",
    workspaceID: "workspace-cache",
    runID: "run-cache"
)
let stableSnapshot = WorkspaceRunViewSnapshot(
    selectedAgent: nil,
    transcripts: [:],
    conversationHistory: []
)
stableViewStore.put(stableSnapshot, for: stableRunKey)
require(stableViewStore.flushPendingWrites(), "Initial Run cache write timed out")
let firstStableRecord = stableDiskCache.record(for: stableRunKey)
Thread.sleep(forTimeInterval: 0.01)
stableViewStore.put(stableSnapshot, for: stableRunKey)
require(stableViewStore.flushPendingWrites(), "Unchanged Run cache flush timed out")
let secondStableRecord = stableDiskCache.record(for: stableRunKey)
require(
    firstStableRecord?.updatedAt == secondStableRecord?.updatedAt,
    "An unchanged visible Run was written to disk again"
)
try? FileManager.default.removeItem(at: stableCacheDirectory)
let surfaceWorkspace = RemoteWorkspace(
    workspaceId: "workspace-1",
    path: "/tmp/project",
    name: "Project",
    lastRunId: secondTurn.runId,
    lastResumeSessionId: "",
    runStatus: secondTurn.status,
    runWorkerActive: false,
    runAgentsTotal: secondTurn.agents.count,
    runAgentsFinished: secondTurn.agents.count,
    runAgentsFailed: 0,
    selected: true,
    available: true,
    readable: true,
    writable: true,
    settings: nil
)
let settledNewerWorkspace = RemoteWorkspace(
    workspaceId: "workspace-1",
    path: "/tmp/project",
    name: "Project",
    lastRunId: "newer-finalized-run",
    lastResumeSessionId: "",
    runStatus: "finalized",
    runWorkerActive: false,
    runAgentsTotal: 1,
    runAgentsFinished: 1,
    runAgentsFailed: 0,
    selected: true,
    available: true,
    readable: true,
    writable: true,
    settings: nil
)
let olderReviewableRun = runSummary(
    runID: "older-reviewable-run",
    status: "completed",
    eventCount: 8,
    agentStatus: "success",
    conversationID: "older-conversation"
)
let newlyStartedWorkspace = surfaceWorkspace.replacingRunSummary(
    with: olderReviewableRun,
    startingNewTurn: true
)
require(
    newlyStartedWorkspace.lastRunId == olderReviewableRun.runId
        && newlyStartedWorkspace.runStatus == olderReviewableRun.status
        && earlierConversationContinuationBlockReason(
            run: olderReviewableRun,
            workspace: newlyStartedWorkspace,
            isLatestConversationTurn: true
        ) == nil,
    "An accepted turn remained blocked by the previous workspace summary"
)
require(
    surfaceWorkspace.replacingRunSummary(with: olderReviewableRun) == surfaceWorkspace,
    "Polling a historical turn changed the workspace's latest turn"
)
require(
    earlierConversationContinuationBlockReason(
        run: olderReviewableRun,
        workspace: settledNewerWorkspace,
        isLatestConversationTurn: true
    ) == nil,
    "An unfinalized latest Turn in an older conversation cannot be continued after the workspace is settled"
)
require(
    PromptSubmissionPlan.make(
        run: olderReviewableRun,
        selectedAgent: olderReviewableRun.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .continueFrom(
        runID: olderReviewableRun.runId,
        agent: 1,
        agentName: "AGENT-001",
        stopsRemainingAgents: false
    ),
    "An older conversation does not finalize its selected result as part of continuation"
)
let reviewSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: secondTurn,
    selectedAgent: secondTurn.agents[0],
    isLatestTurn: true,
    loading: false
)
let newConversationSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: nil,
    composingNewConversation: true,
    run: nil,
    selectedAgent: nil,
    isLatestTurn: true,
    loading: false,
    language: .chinese
)
require(
    newConversationSurface.title == "新对话",
    "A new conversation uses a different title in the window and toolbar"
)
require(
    ConversationRunPresentation.make(run: secondTurn).navigationLabel == "Review",
    "Conversation navigation exposes only an unexplained status color"
)
require(
    reviewSurface.phase == .review
        && reviewSurface.turnNumber == 2
        && reviewSurface.selectedAgentIsRecommended
        && reviewSurface.headerSubtitle == "Project · Turn 2",
    "The conversation surface does not keep Turn, viewed, and recommended state distinct"
)
let pendingFollowUp = PersistedPromptSubmission(
    id: "pending-follow-up",
    machineID: "local",
    workspaceID: surfaceWorkspace.workspaceId,
    prompt: "Continue with the selected result",
    settings: RunSettings(),
    sourceRunID: secondTurn.runId,
    sourceAgent: secondTurn.agents[0].agent,
    resumeSessionID: "",
    confirmLargeRun: false,
    attempt: 0,
    status: .pending,
    error: ""
)
let pendingSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: secondTurn,
    selectedAgent: secondTurn.agents[0],
    isLatestTurn: true,
    loading: false,
    pendingSubmission: pendingFollowUp
)
require(
    pendingSurface.phase == .submitting
        && pendingSurface.turnNumber == 3
        && pendingSurface.runPresentation?.label == "Queued"
        && pendingSurface.toolbarStatusLabel(language: .chinese) == nil,
    "An optimistic follow-up leaves the header on the previous Turn's review state"
)
var failedFollowUp = pendingFollowUp
failedFollowUp.status = .failed
failedFollowUp.retryRequiresNewCommandID = true
let failedSubmissionSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: secondTurn,
    selectedAgent: secondTurn.agents[0],
    isLatestTurn: true,
    loading: false,
    pendingSubmission: failedFollowUp
)
require(
    failedSubmissionSurface.phase == .failed
        && failedSubmissionSurface.runPresentation?.label == "Message not sent",
    "A failed follow-up is still presented as a reviewable previous Turn"
)
var uncertainFollowUp = failedFollowUp
uncertainFollowUp.retryRequiresNewCommandID = false
let uncertainSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace, conversation: conversations[1], composingNewConversation: false,
    run: secondTurn, selectedAgent: secondTurn.agents[0], isLatestTurn: true,
    loading: false, pendingSubmission: uncertainFollowUp
)
require(uncertainSurface.phase == .submitting
    && uncertainSurface.runPresentation?.label == "Queued",
    "An unknown receipt must not be presented as a definitely unsent message")
let applyingSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: secondTurn,
    selectedAgent: secondTurn.agents[0],
    isLatestTurn: true,
    loading: false,
    presentationOverride: ConversationRunPresentation(
        phase: .finalizing,
        label: "Using selected result",
        systemImage: "arrow.triangle.merge",
        tone: .accent,
        animated: true
    )
)
require(
    applyingSurface.phase == .applying
        && applyingSurface.runPresentation?.label == "Using selected result",
    "An in-flight result operation is hidden behind the previous server phase"
)
let historicalSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: firstTurn,
    selectedAgent: firstTurn.agents[0],
    isLatestTurn: false,
    loading: false
)
require(
    historicalSurface.phase == .historical
        && historicalSurface.turnNumber == 1
        && historicalSurface.toolbarStatusLabel(language: .chinese) == nil,
    "A previous Turn adds a redundant technical status to the conversation title"
)
let finalizedRun = runSummary(
    runID: "finalized-run",
    status: "finalized",
    eventCount: 10,
    agentStatus: "success",
    finalizedAgent: 1,
    agents: [runAgent(status: "success", finalized: true)]
)
let finalizedSurface = ConversationSurfaceState.make(
    workspace: surfaceWorkspace,
    conversation: conversations[1],
    composingNewConversation: false,
    run: finalizedRun,
    selectedAgent: finalizedRun.agents[0],
    isLatestTurn: true,
    loading: false
)
require(
    finalizedSurface.toolbarStatusLabel(language: .chinese) == "已保存"
        && finalizedSurface.toolbarStatusLabel(language: .english) == "Saved",
    "The conversation toolbar must show the outcome; candidate details belong beside the Agent"
)
require(
    conversationTitleAccessibilityLabel(
        title: "这个项目主要在讲什么来着。",
        status: finalizedSurface.toolbarStatusLabel(language: .chinese)
    ) == "这个项目主要在讲什么来着。, 已保存",
    "The conversation toolbar exposes separate title nodes that AppKit can reuse with stale labels"
)
require(
    conversationTitleAccessibilityLabel(title: "  New conversation  ", status: nil)
        == "New conversation",
    "A conversation without status does not expose a clean toolbar accessibility label"
)
require(
    localizedConversationTitle("Previous conversation", language: .chinese)
        == "之前的对话"
        && localizedConversationTitle("Named conversation", language: .english)
            == "Named conversation",
    "Sidebar conversation titles are not localized consistently"
)
let synthesisAgent = RunAgentSummary(
    agent: 2,
    name: "AGENT-002",
    role: "synthesis",
    status: "success",
    seconds: nil,
    reasoningTokens: nil,
    reasoningTokenCounts: [:],
    codexThreadId: nil,
    error: nil,
    rejected: false,
    stale: nil,
    recommended: true,
    finalized: true,
    workspaceAvailable: true,
    canAccept: true,
    canContinue: true,
    canRetry: false
)
require(
    conversationAgentDisplayName(
        agentID: synthesisAgent.agent,
        agents: [runAgent(status: "success"), synthesisAgent],
        language: .chinese
    ) == "综合 1",
    "Synthesis results expose their internal Agent number instead of their visible position"
)
require(
    surfaceWorkspace.hasUnresolvedResultSelection,
    "A reviewable current run is treated as settled when switching conversations"
)
let failedWorkspace = RemoteWorkspace(
    workspaceId: "workspace-1",
    path: "/tmp/project",
    name: "Project",
    lastRunId: "failed-run",
    lastResumeSessionId: "",
    runStatus: "completed",
    runWorkerActive: false,
    runAgentsTotal: 2,
    runAgentsFinished: 2,
    runAgentsFailed: 2,
    selected: true,
    available: true,
    readable: true,
    writable: true,
    settings: nil
)
require(
    !failedWorkspace.hasUnresolvedResultSelection
        && !failedWorkspace.removalMayDiscardResults,
    "An all-failed current run unnecessarily blocks a settled conversation from resuming"
)
let stoppedWorkspaceWithResult = RemoteWorkspace(
    workspaceId: "workspace-1",
    path: "/tmp/project",
    name: "Project",
    lastRunId: "stopped-run",
    lastResumeSessionId: "",
    runStatus: "stopped",
    runWorkerActive: false,
    runAgentsTotal: 2,
    runAgentsFinished: 2,
    runAgentsFailed: 1,
    selected: true,
    available: true,
    readable: true,
    writable: true,
    settings: nil
)
require(
    stoppedWorkspaceWithResult.hasUnresolvedResultSelection
        && stoppedWorkspaceWithResult.removalMayDiscardResults
        && stoppedWorkspaceWithResult.runStatusLabel == "Ready to review · 1 result",
    "A stopped current run hides its remaining usable result in workspace navigation"
)
let resumedConfiguration = RunConfiguration(
    numAgents: 1,
    maxParallel: 1,
    serial: false,
    recommendBy: "duration",
    model: nil,
    effort: nil,
    fast: nil,
    synthesisAgents: 0,
    followUpDelay: 60,
    synthesisModel: nil,
    synthesisEffort: nil,
    synthesisFast: nil,
    subagents: false,
    subagentsLimit: 1,
    resumeSessionId: "legacy-session",
    syncBack: true,
    keepWorkspaces: false,
    runsDir: nil,
    codexBin: nil
)
let legacyRoot = runSummary(
    runID: "legacy-root",
    status: "finalized",
    eventCount: 3,
    agentStatus: "success",
    prompt: "Legacy root",
    conversationID: "legacy-root",
    turnIndex: nil,
    createdAt: "2026-08-24T01:00:00Z",
    updatedAt: "2026-08-24T01:01:00Z",
    sessionPromotion: RunSessionPromotion(
        sessionId: "legacy-session",
        workspace: "/tmp/project",
        error: nil
    ),
    finalizedAgent: 1
)
let legacyContinuation = runSummary(
    runID: "legacy-next",
    status: "completed",
    eventCount: 4,
    agentStatus: "success",
    prompt: "Legacy follow-up",
    conversationID: "legacy-next",
    turnIndex: nil,
    createdAt: "2026-08-24T02:00:00Z",
    updatedAt: "2026-08-24T02:01:00Z",
    configuration: resumedConfiguration
)
let legacyConversations = workspaceConversationSummaries(
    from: [legacyContinuation, legacyRoot]
)
require(
    legacyConversations.count == 1
        && legacyConversations[0].runs.map(\.runId) == ["legacy-root", "legacy-next"],
    "Legacy resume runs are split into unrelated conversations"
)
let resumedModernRoot = runSummary(
    runID: "resumed-modern-root",
    status: "finalized",
    eventCount: 3,
    agentStatus: "success",
    prompt: "First resumed prompt",
    conversationID: "resumed-modern-root",
    turnIndex: 0,
    createdAt: "2026-08-24T03:00:00Z",
    updatedAt: "2026-08-24T03:01:00Z",
    sessionPromotion: RunSessionPromotion(
        sessionId: "shared-modern-session",
        workspace: "/tmp/project",
        error: nil
    ),
    finalizedAgent: 1
)
let sharedSessionConfiguration = RunConfiguration(
    numAgents: 1,
    maxParallel: 1,
    serial: false,
    recommendBy: "duration",
    model: nil,
    effort: nil,
    fast: nil,
    synthesisAgents: 0,
    followUpDelay: 60,
    synthesisModel: nil,
    synthesisEffort: nil,
    synthesisFast: nil,
    subagents: false,
    subagentsLimit: 1,
    resumeSessionId: "shared-modern-session",
    syncBack: true,
    keepWorkspaces: false,
    runsDir: nil,
    codexBin: nil
)
let resumedModernFollowUp = runSummary(
    runID: "resumed-modern-follow-up",
    status: "completed",
    eventCount: 4,
    agentStatus: "success",
    prompt: "Second resumed prompt",
    conversationID: "resumed-modern-follow-up",
    turnIndex: 0,
    createdAt: "2026-08-24T04:00:00Z",
    updatedAt: "2026-08-24T04:01:00Z",
    configuration: sharedSessionConfiguration
)
let resumedModernConversations = workspaceConversationSummaries(
    from: [resumedModernFollowUp, resumedModernRoot]
)
require(
    resumedModernConversations.count == 1
        && resumedModernConversations[0].id == "resumed-modern-root"
        && resumedModernConversations[0].runs.map(\.runId)
            == ["resumed-modern-root", "resumed-modern-follow-up"]
        && resumedModernConversations[0].turnNumber(
            for: "resumed-modern-follow-up"
        ) == 2,
    "Runs that resume the same Codex session are shown as separate one-turn conversations"
)
require(
    conversationSelectionContainsRun(
        resumedModernConversations,
        selectedConversationID: "resumed-modern-follow-up",
        runID: "resumed-modern-follow-up"
    ),
    "A repaired conversation identity prevents its latest Run from refreshing"
)
let modernRoot = runSummary(
    runID: "modern-root",
    status: "finalized",
    eventCount: 3,
    agentStatus: "success",
    prompt: "Modern root",
    conversationID: "modern-root",
    turnIndex: 0,
    createdAt: "2026-08-25T01:00:00Z",
    updatedAt: "2026-08-25T01:01:00Z",
    sessionPromotion: RunSessionPromotion(
        sessionId: "modern-session",
        workspace: "/tmp/project",
        error: nil
    ),
    finalizedAgent: 1
)
let modernContinuation = runSummary(
    runID: "modern-next",
    status: "completed",
    eventCount: 4,
    agentStatus: "success",
    prompt: "Modern follow-up",
    conversationID: "modern-root",
    turnIndex: 1,
    createdAt: "2026-08-25T02:00:00Z",
    updatedAt: "2026-08-25T02:01:00Z"
)
let modernConversations = workspaceConversationSummaries(
    from: [modernContinuation, modernRoot]
)
require(
    modernConversations.count == 1
        && modernConversations[0].id == "modern-root"
        && modernConversations[0].runs.map(\.runId) == ["modern-root", "modern-next"],
    "A conversation root whose id equals its Run id was split from its continuations"
)
let singleModernConversation = workspaceConversationSummaries(from: [modernRoot])
require(
    singleModernConversation.count == 1
        && singleModernConversation[0].id == "modern-root",
    "A current one-turn conversation changed identity after session promotion"
)
func resumeCacheFixture(
    runID: String,
    run: RunSummary? = nil,
    activity: Int? = nil
) -> WorkspaceRunViewCacheRecord {
    WorkspaceRunViewCacheRecord(
        key: WorkspaceRunViewKey(machineID: "mac-a", workspaceID: "workspace-a", runID: runID),
        runSummary: run,
        snapshot: WorkspaceRunViewSnapshot(
            selectedAgent: nil, transcripts: [:],
            conversationHistory: [ResumeHistoryEntry(category: "user", text: runID)],
            conversationUpdatedAt: activity
        )
    )
}
let interruptionNotice = """
<turn_aborted>
The user interrupted the previous turn on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.
</turn_aborted>
"""
let interruptedHistory = TranscriptFormatter.blocks(
    history: [
        ResumeHistoryEntry(category: "user", text: interruptionNotice, id: "cancel-one"),
        ResumeHistoryEntry(category: "user", text: "Keep my prompt\n\(interruptionNotice)", id: "prompt-two"),
    ], prompt: "", transcript: AgentTranscript()
)
require(
    interruptedHistory.map(\.text) == ["Response interrupted", "Keep my prompt", "Response interrupted"]
        && interruptedHistory.map(\.id) == ["cancel-one:interrupted", "prompt-two", "prompt-two:interrupted"]
        && interruptedHistory[0].kind == .activity && interruptedHistory[1].kind == .user,
    "Internal interruption metadata appeared as a user prompt or removed the actual user's text"
)
require(
    TranscriptFormatter.blocksWithWorkDisclosure(
        interruptedHistory, runID: "run", agent: 1, expanded: false
    ) == interruptedHistory,
    "A conversation interruption disappeared inside collapsed technical work"
)
for literal in [
    "```xml\n\(interruptionNotice)\n```",
    "```xml\n\(interruptionNotice)",
    "<turn_aborted>my own markup</turn_aborted>",
    "Explain this inline: \(interruptionNotice)",
] {
    require(userTextBeforeCodexInterruption(literal) == nil, "Literal user code was mistaken for runtime metadata")
}
let cachedPromotedHistory = resumeCacheFixture(
    runID: "resume-session:modern-session", activity: 1_900_000_000
)
require(
    !runViewCacheNeedsHydration(activeRunID: nil, requestedRunID: "")
        && runViewCacheNeedsHydration(activeRunID: "older-turn", requestedRunID: ""),
    "Refreshing a history-only conversation erased its cached projection because nil differed from an empty Run ID"
)
let cachedConversationRecords = [
    resumeCacheFixture(runID: modernRoot.runId, run: modernRoot),
    cachedPromotedHistory,
    resumeCacheFixture(runID: "resume-session:unrelated-session", activity: 2_000_000_000),
]
for navigationID in [modernRoot.runId, "resume:modern-session", "modern-session"] {
    require(
        cachedResumeConversationRecord(
            from: cachedConversationRecords, navigationID: navigationID
        )?.key == cachedPromotedHistory.key,
        "Cached CLI history was not resolved from its saved Conversation/Turn/session identity"
    )
}
require(
    cachedResumeConversationRecord(from: cachedConversationRecords, navigationID: nil) == nil
        && cachedResumeConversationRecord(from: cachedConversationRecords, navigationID: "new") == nil
        && cachedResumeConversationRecord(from: cachedConversationRecords, navigationID: "unknown") == nil,
    "A missing or new conversation opened unrelated cached history"
)
let linkedCachedTurn = runSummary(
    runID: "linked-cached-turn", status: "finalized", eventCount: 8,
    agentStatus: "success", conversationID: modernRoot.runId, turnIndex: 1,
    configuration: resumedConfiguration, finalizedAgent: 1
)
let newerLinkedHistory = resumeCacheFixture(
    runID: "resume-session:legacy-session", activity: 1_900_000_001_000
)
let linkedCacheRecords = cachedConversationRecords + [
    resumeCacheFixture(runID: linkedCachedTurn.runId, run: linkedCachedTurn), newerLinkedHistory,
]
for records in [linkedCacheRecords, Array(linkedCacheRecords.reversed())] {
    require(
        cachedResumeConversationRecord(from: records, navigationID: modernRoot.runId)?.key
            == newerLinkedHistory.key,
        "Cached session selection ignored actual activity time or mixed second and millisecond timestamps"
    )
}
for status in ["finalized", "running", "completed"] {
    let cachedTurn = runSummary(
        runID: "cached-next-turn", status: status, eventCount: 8,
        agentStatus: status == "running" ? "running" : "success",
        conversationID: modernRoot.runId, turnIndex: 1,
        createdAt: "2026-08-25T02:00:00Z", updatedAt: "2026-08-25T02:01:00Z",
        finalizedAgent: status == "finalized" ? 1 : nil
    )
    let records = cachedConversationRecords + [resumeCacheFixture(runID: cachedTurn.runId, run: cachedTurn)]
    require(
        cachedResumeConversationRecord(from: records, navigationID: cachedTurn.runId)?.key
            == cachedPromotedHistory.key,
        "A saved continuation Turn did not resolve its canonical conversation's cached session"
    )
    let historySession = ResumeSession(
        sessionId: "modern-session", title: "Latest CLI continuation", cwd: "/tmp/project",
        updatedAt: cachedPromotedHistory.snapshot.conversationUpdatedAt, createdAt: nil,
        source: "cache", model: "", rolloutPath: "", preview: "", tokensUsed: nil
    )
    let restored = conversationMatchingNavigationID(
        cachedTurn.runId,
        in: workspaceConversationSummaries(
            from: records.compactMap(\.runSummary), resumeSessions: [historySession]
        )
    )
    require(
        restored?.opensResumeHistory == (status == "finalized"),
        "Cached resume history either displaced an unfinished Turn or hid a newer CLI continuation"
    )
}
let runningSnapshot = runSummary(status: "running", eventCount: 5, agentStatus: "running")
require(
    PromptSubmissionPlan.make(
        run: runningSnapshot,
        selectedAgent: runningSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .enqueue,
    "Sending during an active Run does not consistently enter the queue"
)
require(
    PromptSubmissionPlan.enqueue.submitSystemImage == "clock.fill",
    "The composer does not visually distinguish a queued follow-up from an immediate send"
)
let partiallyCompletedSnapshot = runSummary(
    runID: "run-partial",
    status: "running",
    eventCount: 6,
    agentStatus: "running",
    recommendedAgent: 1,
    agents: [
        runAgent(agent: 1, status: "success", recommended: true),
        runAgent(agent: 2, status: "running", recommended: false),
    ]
)
require(
    PromptSubmissionPlan.make(
        run: partiallyCompletedSnapshot,
        selectedAgent: partiallyCompletedSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .enqueue,
    "A candidate finishing changed the queue action into an interrupt action"
)
require(
    PromptSubmissionPlan.continueFrom(
        runID: "run-partial",
        agent: 1,
        agentName: "AGENT-001",
        stopsRemainingAgents: true
    ).submitSystemImage == "arrow.up.forward.circle.fill",
    "An early continuation is visually indistinguishable from an ordinary follow-up"
)
require(
    PromptSubmissionPlan.make(
        run: partiallyCompletedSnapshot,
        selectedAgent: partiallyCompletedSnapshot.agents[0],
        hasQueuedFollowUps: true,
        syncBack: true,
        hasSelectedResumeSession: false
    ) == .enqueue,
    "A second message bypassed an existing follow-up queue"
)
require(
    PromptSubmissionPlan.make(
        run: runningSnapshot,
        selectedAgent: runningSnapshot.agents[0],
        hasQueuedFollowUps: false,
        syncBack: false,
        hasSelectedResumeSession: false
    ) == .blocked("Follow-up queue requires workspace sync-back"),
    "The composer enables a queued continuation that cannot be finalized"
)
var delayedSnapshot = runSummary(status: "completed", eventCount: 11, agentStatus: "running")
delayedSnapshot.preserveTerminalAgentStates(from: terminalSnapshot)
require(
    delayedSnapshot.agents[0].status == "success",
    "A later event snapshot regressed a terminal Agent to running"
)
require(
    selectedAgentDetailNeedsRefresh(
        run: terminalSnapshot,
        selectedAgent: 1,
        transcripts: [:]
    ),
    "Polling stopped before the selected terminal Agent was loaded"
)
var staleTerminalTranscript = AgentTranscript(sourceRunID: terminalSnapshot.runId, sourceAgent: 1)
staleTerminalTranscript.cursor = 9
staleTerminalTranscript.hasMore = false
staleTerminalTranscript.finalMessageHasMore = false
require(
    selectedAgentDetailNeedsRefresh(
        run: terminalSnapshot,
        selectedAgent: 1,
        transcripts: [1: staleTerminalTranscript]
    ),
    "Polling stopped while the selected Agent transcript lagged the terminal Run"
)
staleTerminalTranscript.cursor = terminalSnapshot.eventCount
require(
    selectedAgentAnswerNeedsRefresh(run: terminalSnapshot, selectedAgent: 1,
        transcripts: [1: staleTerminalTranscript]),
    "A running-era EOF or legacy cache suppressed terminal answer confirmation"
)
staleTerminalTranscript.finalMessageReadStatus = "success"
require(
    !selectedAgentDetailNeedsRefresh(
        run: terminalSnapshot,
        selectedAgent: 1,
        transcripts: [1: staleTerminalTranscript]
    ),
    "A complete selected Agent transcript kept terminal polling alive"
)
var caughtUpRunningTranscript = AgentTranscript(sourceRunID: runningSnapshot.runId, sourceAgent: 1)
caughtUpRunningTranscript.cursor = runningSnapshot.eventCount
caughtUpRunningTranscript.hasMore = false
caughtUpRunningTranscript.finalMessageHasMore = false
require(
    !selectedAgentDetailNeedsRefresh(
        run: runningSnapshot,
        selectedAgent: 1,
        transcripts: [1: caughtUpRunningTranscript]
    ),
    "A caught-up running Agent kept issuing detail requests without new events"
)
require(
    !visibleRunRefreshNeedsReload(
        current: terminalSnapshot,
        remote: terminalSnapshot,
        selectedAgent: 1,
        transcripts: [1: staleTerminalTranscript]
    ),
    "An unchanged completed conversation still reloaded its transcript every refresh"
)
require(
    visibleRunRefreshNeedsReload(
        current: terminalSnapshot,
        remote: delayedSnapshot,
        selectedAgent: 1,
        transcripts: [1: staleTerminalTranscript]
    ),
    "A changed Run summary was incorrectly treated as stable"
)
require(
    visibleRunRefreshNeedsReload(
        current: terminalSnapshot,
        remote: terminalSnapshot,
        selectedAgent: 1,
        transcripts: [:]
    ),
    "A missing selected Agent transcript was incorrectly treated as stable"
)
let legacyTranscript = AgentTranscript(finalMessage: "wrong workspace")
require(
    selectedAgentDetailNeedsRefresh(run: terminalSnapshot, selectedAgent: 1,
        transcripts: [1: AgentTranscript(sourceRunID: "another-run", sourceAgent: 1,
            cursor: terminalSnapshot.eventCount, hasMore: false, finalMessageHasMore: false)]),
    "A cached transcript from another run suppressed background loading"
)
require(
    agentTranscriptBoundToBranch(legacyTranscript, runID: "run-1", agent: 1)
        == AgentTranscript(sourceRunID: "run-1", sourceAgent: 1),
    "A legacy unbound transcript was displayed in a current Run"
)
let otherRunTranscript = AgentTranscript(
    sourceRunID: "run-other",
    sourceAgent: 1,
    finalMessage: "wrong run"
)
require(
    agentTranscriptBoundToBranch(otherRunTranscript, runID: "run-1", agent: 1)
        == AgentTranscript(sourceRunID: "run-1", sourceAgent: 1),
    "A transcript from another Run leaked into the selected conversation"
)
let matchingTranscript = AgentTranscript(
    sourceRunID: "run-1",
    sourceAgent: 1,
    finalMessage: "current answer"
)
require(
    agentTranscriptBoundToBranch(matchingTranscript, runID: "run-1", agent: 1)
        == matchingTranscript,
    "A correctly bound transcript was discarded"
)
var unavailableFinalMessage = matchingTranscript
unavailableFinalMessage.finalMessageCursor = 14
unavailableFinalMessage.finalMessageHasMore = true
unavailableFinalMessage.finalMessageID = "artifact-1"
unavailableFinalMessage.clearUnavailableFinalMessage()
require(
    unavailableFinalMessage.finalMessage.isEmpty
        && unavailableFinalMessage.finalMessageCursor == 0
        && !unavailableFinalMessage.finalMessageHasMore
        && unavailableFinalMessage.finalMessageID.isEmpty,
    "An explicitly absent final-message artifact left stale text visible"
)
require(usableResumeSessionID("NO") == nil, "New-conversation sentinel was sent as a session")
require(usableResumeSessionID("  ") == nil, "Blank resume session was sent to the server")
require(
    usableResumeSessionID(" session-1 ") == "session-1",
    "A valid resume session was not normalized"
)
require(
    parseInternetDate("2026-08-25T17:51:46.073316+00:00") != nil,
    "A fractional server timestamp was not parsed"
)
require(
    parseInternetDate("2026-08-25T17:51:46Z") != nil,
    "A whole-second server timestamp was not parsed"
)

require(
    workspaceLinkKind("/Users/example/project/report.md:42") == .workspaceFile,
    "An absolute workspace file link was treated as external"
)
require(
    workspaceLinkKind("docs/report.md:42") == .workspaceFile,
    "A relative workspace file with a line suffix was rejected as a URL scheme"
)
require(
    workspaceLinkKind("https://openai.com") == .external,
    "A web link was not kept external"
)
require(
    workspaceLinkKind("realman-robotics.cn/cn/products/rm75.html") == .external,
    "A bare web address from a Codex answer was treated as a workspace file"
)
require(
    externalWorkspaceLinkURL("github.com/openai/codex")?.absoluteString
        == "https://github.com/openai/codex",
    "A bare web address was not upgraded to HTTPS"
)
require(
    workspaceLinkKind("README.md") == .workspaceFile,
    "A normal workspace filename was treated as a web address"
)
for name in ["preview.png", "figure.JPG", "image.webp", "report.docx", "slides.pptx", "data.xlsx", "audio.wav", "video.mov", "archive.zip"] {
    require(workspaceLinkKind(name) == .workspaceFile, "A file preview was sent to a website: \(name)")
    require(externalWorkspaceLinkURL(name) == nil, "A file preview leaked into an external URL: \(name)")
}
require(
    workspaceLinkKind("javascript:alert(1)") == .unsupported,
    "An unsafe URL scheme was accepted"
)
require(
    resolveWorkspaceLink("figures/result.pdf", relativeTo: "docs/report.md")
        == "docs/figures/result.pdf",
    "A document-relative workspace link was not resolved from its parent"
)

let carriedHistory = [
    ResumeHistoryEntry(category: "user", text: "first"),
    ResumeHistoryEntry(category: "output", text: "intermediate update"),
    ResumeHistoryEntry(category: "output", text: "answer"),
]
let conversationOnly = conversationHistoryEntries(from: [
    TranscriptBlock(id: "user", kind: .user, text: "first"),
    TranscriptBlock(id: "thought", kind: .thought, text: "private work"),
    TranscriptBlock(id: "command", kind: .command, text: "rg --files"),
    TranscriptBlock(id: "update", kind: .output, text: "intermediate update"),
    TranscriptBlock(id: "output", kind: .output, text: "answer", isFinalAnswer: true),
    TranscriptBlock(id: "warning", kind: .warning, text: "temporary warning"),
])
require(
    conversationOnly == carriedHistory,
    "The chosen branch did not preserve natural Agent messages while dropping technical work"
)
let agentOneHistory = branchConversationHistory(
    history: [],
    prompt: "compare the candidates",
    transcript: AgentTranscript(finalMessage: "Agent one answer")
)
let agentTwoHistory = branchConversationHistory(
    history: [],
    prompt: "compare the candidates",
    transcript: AgentTranscript(finalMessage: "Agent two answer")
)
require(
    agentTwoHistory.contains { $0.text == "Agent two answer" }
        && !agentTwoHistory.contains { $0.text == "Agent one answer" },
    "Continuation history was built from a different Agent branch"
)
require(
    agentOneHistory != agentTwoHistory,
    "Distinct Agent branches produced the same continuation history"
)
require(
    transcriptIsNearBottom(distance: 95, viewportHeight: 900),
    "The transcript stopped following while the reader was still at the bottom"
)
require(
    !transcriptIsNearBottom(distance: 180, viewportHeight: 900),
    "The transcript followed new output while the reader was away from the bottom"
)
require(
    transcriptFollowsLatest(liveValue: nil, persistedValue: nil),
    "A conversation without scroll history did not begin at the latest content"
)
require(
    !transcriptFollowsLatest(liveValue: nil, persistedValue: false),
    "A newly selected conversation ignored its own saved reading position"
)
require(
    transcriptFollowsLatest(liveValue: true, persistedValue: false),
    "Live scroll state did not override an older persisted snapshot"
)
require(
    transcriptConversationContextID(
        selectedConversationID: "conversation-root",
        activeRunID: "cached-turn",
        workspaceLastRunID: "latest-turn"
    ) == "conversation-root",
    "A cached or authoritative Turn replaced the stable Conversation scroll context"
)
require(
    transcriptConversationContextID(
        selectedConversationID: nil,
        activeRunID: "visible-turn",
        workspaceLastRunID: "latest-turn"
    ) == "visible-turn",
    "The visible Turn was not used when no Conversation identity was available"
)
require(
    transcriptConversationContextID(
        selectedConversationID: "  ",
        activeRunID: nil,
        workspaceLastRunID: "latest-turn"
    ) == "latest-turn",
    "The workspace fallback was not used for a blank Conversation identity"
)
require(
    transcriptConversationContextID(
        selectedConversationID: nil,
        activeRunID: nil,
        workspaceLastRunID: nil
    ) == "empty",
    "An empty transcript context is not deterministic"
)
require(
    transcriptAgentContextID(selectedAgent: nil, restoredAgent: 2) == "2",
    "A cached candidate branch briefly fell back to history during hydration"
)
require(
    transcriptAgentContextID(selectedAgent: 3, restoredAgent: 2) == "3",
    "The selected candidate did not override the restored branch"
)
require(
    transcriptAgentContextID(selectedAgent: nil, restoredAgent: nil) == "history",
    "A resume-only conversation no longer uses its history context"
)
require(
    !transcriptAnchorGeometryIsResolved(
        anchorIndex: 42,
        firstItemMinY: 14,
        anchorMinY: 0,
        anchorHeight: 80
    ),
    "A provisional origin frame was accepted as restored transcript geometry"
)
require(
    transcriptAnchorGeometryIsResolved(
        anchorIndex: 42,
        firstItemMinY: 14,
        anchorMinY: 72_000,
        anchorHeight: 80
    ),
    "Resolved offscreen transcript geometry was rejected"
)
let preferredVisibleAnchor = transcriptPreferredViewportAnchor(
    visibleMinY: 500,
    visibleMaxY: 1_100,
    candidates: [
        .init(itemIndex: 4, minY: 120, maxY: 620),
        .init(itemIndex: 5, minY: 650, maxY: 780),
        .init(itemIndex: 6, minY: 810, maxY: 1_240),
    ]
)
require(
    preferredVisibleAnchor?.itemIndex == 5,
    "Transcript presentation changes anchor a reflowing row above the viewport"
)
let spanningVisibleAnchor = transcriptPreferredViewportAnchor(
    visibleMinY: 500,
    visibleMaxY: 1_100,
    candidates: [
        .init(itemIndex: 7, minY: 100, maxY: 1_300),
    ]
)
require(
    spanningVisibleAnchor?.itemIndex == 7,
    "A viewport contained by one long transcript row lost its fallback anchor"
)
require(
    transcriptMeasurementWidth(947.8, duringLiveResize: false) == 947,
    "A settled transcript width was unexpectedly quantized"
)
require(
    transcriptMeasurementWidth(947.8, duringLiveResize: true) == 936
        && transcriptMeasurementWidth(946.1, duringLiveResize: true) == 936,
    "Live resize no longer coalesces adjacent widths into one measurement"
)
require(
    transcriptMeasurementWidth(948.2, duringLiveResize: true) == 948,
    "Live resize did not advance at the next measurement step"
)
require(
    transcriptWidthBecameUsable(previousWidth: 1, currentWidth: 640),
    "The transcript did not recognize its first usable layout width"
)
require(
    !transcriptWidthBecameUsable(previousWidth: 640, currentWidth: 720),
    "Ordinary window resizing was mistaken for initial transcript layout"
)
let narrowTranscriptInsets = transcriptHorizontalInsets(
    width: 720,
    maximumContentWidth: 900,
    minimumEdge: 28
)
require(
    narrowTranscriptInsets.leading == 28 && narrowTranscriptInsets.trailing == 28,
    "Narrow transcripts no longer retain their minimum edge insets"
)
let wideTranscriptInsets = transcriptHorizontalInsets(
    width: 1_440,
    maximumContentWidth: 900,
    minimumEdge: 28
)
require(
    wideTranscriptInsets.leading == 270 && wideTranscriptInsets.trailing == 270,
    "Wide transcripts no longer stay centered at the capped reading width"
)
require(
    transcriptEstimatedLiveResizeHeight(
        measuredHeight: 100,
        measuredWidth: 900,
        targetWidth: 600
    ) == 158,
    "Live resize no longer estimates narrower transcript rows conservatively"
)
require(
    transcriptEstimatedLiveResizeHeight(
        measuredHeight: 100,
        measuredWidth: 600,
        targetWidth: 900
    ) == 100,
    "Widening a transcript unexpectedly increased its live row estimate"
)
MainActor.assumeIsolated {
    let transcriptScrollSuiteName =
        "com.parallelcodexrunner.tests.transcript-scroll.\(UUID().uuidString)"
    let transcriptScrollDefaults = UserDefaults(suiteName: transcriptScrollSuiteName)!
    let transcriptScrollMemory = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "scroll-memory"
    )
    let transcriptScrollContext = "machine\u{1f}workspace\u{1f}run\u{1f}agent"
    transcriptScrollMemory.save(
        .init(
            offsetY: 1_234.5,
            followsLatest: false,
            anchorID: "message:turn-7",
            anchorOffset: 42
        ),
        for: transcriptScrollContext
    )
    transcriptScrollMemory.flush()
    let reloadedTranscriptScrollMemory = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "scroll-memory"
    )
    let reloadedTranscriptScrollSnapshot = reloadedTranscriptScrollMemory.snapshot(
        for: transcriptScrollContext
    )
    require(
        reloadedTranscriptScrollSnapshot?.offsetY == 1_234.5
            && reloadedTranscriptScrollSnapshot?.followsLatest == false
            && reloadedTranscriptScrollSnapshot?.anchorID == "message:turn-7"
            && reloadedTranscriptScrollSnapshot?.anchorOffset == 42,
        "Flushing transcript position no longer survives an app relaunch"
    )
    reloadedTranscriptScrollMemory.suspendForViewTeardown()
    reloadedTranscriptScrollMemory.save(
        .init(offsetY: 0, followsLatest: false, anchorID: "teardown", anchorOffset: 0),
        for: transcriptScrollContext
    )
    let afterTeardownSnapshot = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "scroll-memory"
    ).snapshot(for: transcriptScrollContext)
    require(
        afterTeardownSnapshot?.offsetY == 1_234.5
            && afterTeardownSnapshot?.anchorID == "message:turn-7",
        "Closed-window teardown replaced the last live transcript position"
    )
    reloadedTranscriptScrollMemory.resumeAfterViewTeardown()
    reloadedTranscriptScrollMemory.save(
        .init(
            offsetY: 2_345.5,
            followsLatest: false,
            anchorID: "message:turn-8",
            anchorOffset: 24
        ),
        for: transcriptScrollContext
    )
    reloadedTranscriptScrollMemory.flush()
    let afterWindowReopenSnapshot = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "scroll-memory"
    ).snapshot(for: transcriptScrollContext)
    require(
        afterWindowReopenSnapshot?.offsetY == 2_345.5
            && afterWindowReopenSnapshot?.anchorID == "message:turn-8",
        "Reopened windows cannot resume transcript position persistence"
    )
    reloadedTranscriptScrollMemory.freezeForTermination()
    reloadedTranscriptScrollMemory.save(
        .init(offsetY: 0, followsLatest: false, anchorID: "termination", anchorOffset: 0),
        for: transcriptScrollContext
    )
    let afterTerminationSnapshot = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "scroll-memory"
    ).snapshot(for: transcriptScrollContext)
    require(
        afterTerminationSnapshot?.offsetY == 2_345.5
            && afterTerminationSnapshot?.anchorID == "message:turn-8",
        "Application teardown replaced the last live transcript position"
    )
    let branchMemory = TranscriptScrollMemory(
        defaults: transcriptScrollDefaults,
        storageKey: "branch-memory"
    )
    let conversationContext = "machine\u{1f}workspace\u{1f}conversation"
    branchMemory.save(
        .init(offsetY: 10, followsLatest: false),
        for: conversationContext + "\u{1f}1"
    )
    branchMemory.save(
        .init(offsetY: 20, followsLatest: false),
        for: conversationContext + "\u{1f}history"
    )
    require(
        branchMemory.mostRecentlyUsedAgent(
            for: conversationContext
        ) == 1,
        "A transient history projection replaced the last candidate branch"
    )
    branchMemory.save(
        .init(offsetY: 30, followsLatest: false),
        for: conversationContext + "\u{1f}2"
    )
    require(
        branchMemory.mostRecentlyUsedAgent(
            for: conversationContext
        ) == 2,
        "The latest candidate branch was not restored"
    )
    let firstCandidateContext = transcriptScrollContextID(
        machineID: "machine", workspaceID: "workspace", conversationID: "conversation",
        runID: "first-turn", agentID: "2"
    )
    let nextCandidateContext = transcriptScrollContextID(
        machineID: "machine", workspaceID: "workspace", conversationID: "conversation",
        runID: "next-turn", agentID: "2"
    )
    require(
        branchMemory.snapshot(for: firstCandidateContext)?.offsetY == 30,
        "Existing reading positions were lost during turn-scoped scroll migration"
    )
    require(
        branchMemory.snapshot(for: nextCandidateContext) == nil,
        "The next turn reused an earlier candidate's reading position"
    )
    branchMemory.save(.init(offsetY: 900, followsLatest: false), for: nextCandidateContext)
    branchMemory.flush()
    let reloadedBranches = TranscriptScrollMemory(defaults: transcriptScrollDefaults, storageKey: "branch-memory")
    require(
        reloadedBranches.snapshot(for: firstCandidateContext)?.offsetY == 30
            && reloadedBranches.snapshot(for: nextCandidateContext)?.offsetY == 900,
        "Independent turn and candidate positions did not survive relaunch"
    )
    require(
        reloadedBranches.mostRecentlyUsedAgent(for: conversationContext, runID: "next-turn") == 2,
        "Turn-scoped candidate selection was not restored"
    )
    transcriptScrollDefaults.removePersistentDomain(forName: transcriptScrollSuiteName)
}
require(
    transcriptInlineControlPosition(
        itemMinY: 120,
        itemMaxY: 154,
        visibleMinY: 200,
        visibleMaxY: 900
    ) == .aboveViewport,
    "A current-turn selector above the viewport did not become sticky"
)
require(
    transcriptInlineControlPosition(
        itemMinY: 920,
        itemMaxY: 954,
        visibleMinY: 200,
        visibleMaxY: 900
    ) == .belowViewport,
    "A current-turn selector below historical reading position became sticky"
)
require(
    transcriptInlineControlPosition(
        itemMinY: 880,
        itemMaxY: 914,
        visibleMinY: 200,
        visibleMaxY: 900
    ) == .visible,
    "A partially visible current-turn selector was treated as offscreen"
)
require(
    transcriptInlineControlAccessibilityHidden(.aboveViewport),
    "The offscreen inline Agent selector remained exposed beside its sticky copy"
)
require(
    !transcriptInlineControlAccessibilityHidden(.visible)
        && !transcriptInlineControlAccessibilityHidden(.belowViewport)
        && !transcriptInlineControlAccessibilityHidden(.missing),
    "The inline Agent selector was hidden when no sticky duplicate was present"
)
let authoritativeHistory = carriedHistory + [
    ResumeHistoryEntry(category: "user", text: "follow-up")
]
require(
    mergeConversationHistory(
        current: carriedHistory,
        authoritative: authoritativeHistory
    ) == authoritativeHistory,
    "Run history did not extend the optimistic conversation"
)
let stableAuthoritativeHistory = [
    ResumeHistoryEntry(
        category: "user",
        text: "first",
        id: "history:session-1:3",
        turnId: "turn-1"
    ),
    ResumeHistoryEntry(
        category: "output",
        text: "intermediate update",
        id: "history:session-1:5",
        turnId: "turn-1"
    ),
    ResumeHistoryEntry(
        category: "output",
        text: "answer",
        id: "history:session-1:7",
        turnId: "turn-1"
    ),
]
let identityMergedHistory = mergeConversationHistory(
    current: carriedHistory,
    authoritative: stableAuthoritativeHistory
)
require(
    identityMergedHistory.map(\.id)
        == ["history:session-1:3", "history:session-1:5", "history:session-1:7"],
    "Authoritative history identities were not preserved"
)
require(
    identityMergedHistory.map(\.turnId) == ["turn-1", "turn-1", "turn-1"],
    "Authoritative turn identities were not preserved"
)
let decodedHistoryIdentity = try decoder.decode(
    ResumeHistoryEntry.self,
    from: Data(
        #"{"category":"user","text":"first","id":"history:session-1:3","turn_id":"turn-1"}"#.utf8
    )
)
require(
    decodedHistoryIdentity.id == "history:session-1:3"
        && decodedHistoryIdentity.turnId == "turn-1",
    "Run history identity fields were not decoded"
)
require(
    mergeConversationHistory(
        current: carriedHistory,
        authoritative: [carriedHistory[0]]
    ) == carriedHistory,
    "A shorter history response erased optimistic conversation blocks"
)
let detailedHistory = [
    carriedHistory[0],
    ResumeHistoryEntry(category: "thought", text: "inspect the project"),
    ResumeHistoryEntry(category: "command", text: "rg --files"),
    carriedHistory[1],
]
require(
    mergeConversationHistory(
        current: detailedHistory,
        authoritative: carriedHistory
    ) == carriedHistory,
    "Attempt-only Agent work leaked into permanent conversation history"
)
let repeatedTextHistory = [
    ResumeHistoryEntry(
        category: "user",
        text: "continue",
        id: "history:session-1:3",
        turnId: "turn-1"
    ),
    ResumeHistoryEntry(
        category: "output",
        text: "done",
        id: "history:session-1:7",
        turnId: "turn-1"
    ),
    ResumeHistoryEntry(
        category: "user",
        text: "continue",
        id: "history:session-1:11",
        turnId: "turn-2"
    ),
    ResumeHistoryEntry(
        category: "output",
        text: "done",
        id: "history:session-1:15",
        turnId: "turn-2"
    ),
]
require(
    mergeConversationHistory(
        current: carriedHistory,
        authoritative: repeatedTextHistory
    ) == repeatedTextHistory,
    "Repeated conversation text was collapsed instead of preserving rollout identity"
)
let otherBranch = [
    ResumeHistoryEntry(category: "user", text: "first"),
    ResumeHistoryEntry(category: "output", text: "different answer"),
]
require(
    mergeConversationHistory(
        current: detailedHistory,
        authoritative: otherBranch
    ) == otherBranch,
    "A divergent authoritative branch was mixed with the visible conversation"
)

let machineARun = MachineContextKey.run(
    machineID: "mac-a",
    workspaceID: "workspace-1",
    runID: "run-1"
)
let machineBRun = MachineContextKey.run(
    machineID: "mac-b",
    workspaceID: "workspace-1",
    runID: "run-1"
)
require(machineARun != machineBRun, "Machine contexts share persisted state")
require(
    MachineContextKey.belongsToWorkspace(
        machineARun,
        machineID: "mac-a",
        workspaceID: "workspace-1"
    ),
    "Run context is not associated with its workspace"
)
require(
    !MachineContextKey.belongsToWorkspace(
        machineARun,
        machineID: "mac-b",
        workspaceID: "workspace-1"
    ),
    "Run context leaked across machines"
)
require(
    MachineContextKey.machineID(from: machineARun) == "mac-a",
    "Machine identity could not be recovered from a scoped key"
)
let machineConversationA = MachineContextKey.conversation(
    machineID: "mac-a",
    workspaceID: "workspace-1",
    conversationID: "conversation-a"
)
let machineConversationB = MachineContextKey.conversation(
    machineID: "mac-a",
    workspaceID: "workspace-1",
    conversationID: "conversation-b"
)
let machineSession = MachineContextKey.session(
    machineID: "mac-a",
    workspaceID: "workspace-1",
    sessionID: "conversation-a"
)
require(
    machineConversationA != machineConversationB,
    "Conversation drafts share one persisted context"
)
require(
    machineConversationA != machineSession,
    "Conversation draft context collides with resume-session history"
)
require(
    MachineContextKey.belongsToWorkspace(
        machineConversationA,
        machineID: "mac-a",
        workspaceID: "workspace-1"
    ),
    "Conversation draft context is not associated with its workspace"
)
let machineTask = MachineContextKey.task(machineID: "mac-a", runID: "run-42")
require(
    MachineContextKey.taskRunID(from: machineTask) == "run-42",
    "Run identity could not be recovered from a task key"
)
require(
    MachineContextKey.taskRunID(from: machineARun) == nil,
    "A workspace context was mistaken for a notification task"
)

let runViewStore = WorkspaceRunViewStateStore(capacity: 2)
let runViewA = WorkspaceRunViewKey(
    machineID: "mac-a",
    workspaceID: "workspace-1",
    runID: "run-1"
)
let runViewB = WorkspaceRunViewKey(
    machineID: "mac-a",
    workspaceID: "workspace-2",
    runID: "run-2"
)
let runViewC = WorkspaceRunViewKey(
    machineID: "mac-b",
    workspaceID: "workspace-1",
    runID: "run-3"
)
runViewStore.put(
    WorkspaceRunViewSnapshot(
        selectedAgent: 1,
        transcripts: [
            1: agentTranscriptBoundToBranch(transcript, runID: "run-1", agent: 1),
        ],
        conversationHistory: []
    ),
    for: runViewA
)
runViewStore.put(
    WorkspaceRunViewSnapshot(
        selectedAgent: 2,
        transcripts: [:],
        conversationHistory: []
    ),
    for: runViewB
)
require(
    runViewStore.snapshot(for: runViewA)?.selectedAgent == 1,
    "Workspace run state was not restored"
)
runViewStore.put(
    WorkspaceRunViewSnapshot(
        selectedAgent: 3,
        transcripts: [:],
        conversationHistory: []
    ),
    for: runViewC
)
require(runViewStore.count == 2, "Run state cache exceeded its capacity")
require(
    runViewStore.snapshot(for: runViewA) != nil,
    "Recently accessed run state was evicted"
)
require(
    runViewStore.snapshot(for: runViewB) == nil,
    "Least recently used run state was not evicted"
)
runViewStore.removeWorkspace(machineID: "mac-a", workspaceID: "workspace-1")
require(
    runViewStore.snapshot(for: runViewA) == nil,
    "Removed workspace retained cached run state"
)

let runCacheDirectory = FileManager.default.temporaryDirectory
    .appendingPathComponent("pcr-run-cache-\(UUID().uuidString)", isDirectory: true)
let diskRunStore = WorkspaceRunDiskCache(
    directory: runCacheDirectory,
    capacity: 4,
    maximumBytes: 16 * 1024 * 1024
)
let fullCachedAnswer = String(repeating: "完整回答", count: 10_000)
var diskTranscript = transcript
diskTranscript.sourceRunID = "run-1"
diskTranscript.sourceAgent = 1
diskTranscript.blocks = [
    TranscriptBlock(
        id: "cached-answer",
        kind: .output,
        text: fullCachedAnswer,
        isFinalAnswer: true
    )
]
let diskSnapshot = WorkspaceRunViewSnapshot(
    selectedAgent: 1,
    transcripts: [1: diskTranscript],
    conversationHistory: [],
    conversationTitle: "Stable conversation title",
    conversationUpdatedAt: 1_777_777_777
)
diskRunStore.put(
    WorkspaceRunViewCacheRecord(
        key: runViewA,
        runSummary: runningSnapshot,
        snapshot: diskSnapshot
    )
)
let otherMachineRecord = WorkspaceRunViewCacheRecord(
    key: runViewC,
    runSummary: runningSnapshot,
    snapshot: WorkspaceRunViewSnapshot(
        selectedAgent: 3,
        transcripts: [:],
        conversationHistory: []
    )
)
diskRunStore.put(otherMachineRecord)
require(
    diskRunStore.records(machineID: "mac-a", workspaceID: "workspace-1").map(\.key)
        == [runViewA],
    "Persistent conversation discovery leaked another machine or workspace"
)
let reopenedDiskRunStore = WorkspaceRunDiskCache(
    directory: runCacheDirectory,
    capacity: 4,
    maximumBytes: 16 * 1024 * 1024
)
let reopenedRecord = reopenedDiskRunStore.record(for: runViewA)
require(
    reopenedRecord?.snapshot.transcripts[1]?.blocks.first?.text == fullCachedAnswer,
    "Complete conversation text did not survive a disk-cache restart"
)
require(
    reopenedRecord?.snapshot.conversationTitle == "Stable conversation title",
    "A stable conversation title did not survive a disk-cache restart"
)
require(
    reopenedRecord?.snapshot.conversationUpdatedAt == 1_777_777_777,
    "A cached conversation timestamp did not survive a disk-cache restart"
)
require(
    preferredCachedConversationTitle(
        runIDs: ["old-turn", "latest-turn"],
        persisted: ["latest-turn": "Stable root title"],
        fallback: ["old-turn": "History root title"]
    ) == "History root title",
    "A stale persisted PCR Turn title overrode the root Codex history"
)
let contaminatedSnapshot = WorkspaceRunViewSnapshot(
    selectedAgent: 1,
    transcripts: [1: otherRunTranscript],
    conversationHistory: carriedHistory
).validated(for: runViewA)
require(
    contaminatedSnapshot.transcripts.isEmpty
        && contaminatedSnapshot.conversationHistory == carriedHistory,
    "Cache repair did not isolate a foreign transcript while preserving conversation history"
)
reopenedDiskRunStore.removeWorkspace(machineID: "mac-a", workspaceID: "workspace-1")
require(
    reopenedDiskRunStore.record(for: runViewA) == nil,
    "Removed workspace retained its persistent conversation cache"
)
require(
    reopenedDiskRunStore.record(for: runViewC) != nil,
    "Removing one machine workspace erased another machine's conversation"
)
try? FileManager.default.removeItem(at: runCacheDirectory)

var cachedDefaults = RunSettings()
cachedDefaults.agents = 24
cachedDefaults.subagentsLimit = 96
let cachedDefaultState = DefaultSettingsContextState(
    settings: cachedDefaults,
    baseline: RunSettings(),
    dirtyFields: [.agents, .subagentsLimit],
    loaded: true,
    needsSave: true
)
let encodedDefaultState = try JSONEncoder().encode(cachedDefaultState)
let decodedDefaultState = try JSONDecoder().decode(
    DefaultSettingsContextState.self,
    from: encodedDefaultState
)
require(
    decodedDefaultState == cachedDefaultState,
    "Per-machine default settings did not survive local persistence"
)

var cachedWorkspaceSettings = RunSettings()
cachedWorkspaceSettings.agents = 9
cachedWorkspaceSettings.resumeSessionID = "resume-local"
let remoteWorkspaceSettings = RemoteRunSettings(
    configured: true,
    agents: 4,
    synthesisAgents: 2,
    followUpDelay: 60,
    execution: .parallel,
    automaticMaxParallel: true,
    maxParallel: 4,
    subagents: false,
    subagentsLimit: 8,
    recommendBy: "reasoning_tokens",
    model: "remote-model",
    effort: "high",
    fastMode: "automatic",
    synthesisModel: "remote-model",
    synthesisEffort: "high",
    synthesisFastMode: "automatic",
    syncBack: true,
    keepWorkspaces: false,
    resumeSessionId: "resume-remote",
    resumeIncludeNonInteractive: false,
    runsDir: nil,
    codexBin: "codex"
)
let restoredWorkspaceSettings = resolvedWorkspaceSettings(
    cached: cachedWorkspaceSettings,
    remote: remoteWorkspaceSettings,
    preserving: [.agents, .resumeSessionID]
)
require(
    restoredWorkspaceSettings.agents == 9
        && restoredWorkspaceSettings.resumeSessionID == "resume-local"
        && restoredWorkspaceSettings.model == "remote-model",
    "Immediate workspace restoration lost local draft context settings"
)
require(
    resolvedWorkspaceSettings(
        cached: cachedWorkspaceSettings,
        remote: nil,
        preserving: []
    ) == cachedWorkspaceSettings,
    "Offline workspace restoration discarded cached settings"
)

let layoutKeyInput = TranscriptLayoutMetricKeyInput(
    kind: "output",
    text: "A stable transcript block",
    statusPresent: false,
    activityType: nil,
    isHistorical: false,
    isFinalAnswer: true,
    textSize: "standard",
    maximumBodyWidth: 720
)
let layoutOSVersion = OperatingSystemVersion(
    majorVersion: 26,
    minorVersion: 0,
    patchVersion: 0
)
let layoutKey = transcriptLayoutMetricKey(
    layoutKeyInput,
    operatingSystemVersion: layoutOSVersion
)
require(
    layoutKey != transcriptLayoutMetricKey(
        layoutKeyInput, schemaVersion: 4, operatingSystemVersion: layoutOSVersion
    ),
    "Resolved fallback fonts reused heights measured before font substitution"
)
let chineseLayoutKey = transcriptLayoutMetricKey(
    layoutKeyInput,
    operatingSystemVersion: layoutOSVersion,
    preferredLanguages: ["zh-Hans"],
    preferredLocalizations: ["zh-Hans"]
)
let englishLayoutKey = transcriptLayoutMetricKey(
    layoutKeyInput,
    operatingSystemVersion: layoutOSVersion,
    preferredLanguages: ["en"],
    preferredLocalizations: ["en"]
)
require(
    chineseLayoutKey != englishLayoutKey,
    "Transcript layout reused Chinese font measurements in an English process"
)
require(
    chineseLayoutKey != transcriptLayoutMetricKey(
        layoutKeyInput,
        operatingSystemVersion: layoutOSVersion,
        preferredLanguages: ["zh-Hans"],
        preferredLocalizations: ["en"]
    ),
    "Changing the UI language hid the process font localization from the cache key"
)
let fractionalWidthKey = { (width: CGFloat) in
    transcriptLayoutMetricKey(.init(
        kind: "user", text: "A message near its wrapping threshold", statusPresent: false,
        activityType: nil, isHistorical: false, isFinalAnswer: false,
        textSize: "standard", maximumBodyWidth: width
    ))
}
require(
    fractionalWidthKey(320.1) != fractionalWidthKey(320.4),
    "Transcript layout rounded distinct wrapping widths into the same cache entry"
)
require(
    layoutKey == transcriptLayoutMetricKey(
        layoutKeyInput,
        operatingSystemVersion: layoutOSVersion
    ),
    "Transcript layout cache key was not deterministic"
)
require(
    layoutKey != transcriptLayoutMetricKey(
        .init(
            kind: "output",
            text: "A changed transcript block",
            statusPresent: false,
            activityType: nil,
            isHistorical: false,
            isFinalAnswer: true,
            textSize: "standard",
            maximumBodyWidth: 720
        ),
        operatingSystemVersion: layoutOSVersion
    ),
    "Transcript layout cache key ignored changed content"
)
let layoutCacheDirectory = FileManager.default.temporaryDirectory
    .appendingPathComponent("pcr-layout-cache-tests-\(UUID().uuidString)", isDirectory: true)
let layoutCacheURL = layoutCacheDirectory.appendingPathComponent("metrics.json")
let layoutMetrics = TranscriptLayoutMetrics(
    bodyWidth: 640,
    textWidth: 616,
    textHeight: 144,
    padding: 12
)
let layoutStore = TranscriptLayoutMetricsStore(
    fileURL: layoutCacheURL,
    maximumEntries: 2
)
layoutStore.store(layoutMetrics, forKey: layoutKey)
layoutStore.store(layoutMetrics, forKey: "second")
layoutStore.store(layoutMetrics, forKey: "third")
layoutStore.flush()
let reopenedLayoutStore = TranscriptLayoutMetricsStore(
    fileURL: layoutCacheURL,
    maximumEntries: 2
)
require(
    reopenedLayoutStore.count == 2
        && [layoutKey, "second", "third"].compactMap {
            reopenedLayoutStore.metrics(forKey: $0)
        }.count == 2,
    "Transcript layout metrics did not persist or prune correctly"
)
try? FileManager.default.removeItem(at: layoutCacheDirectory)

let unavailableServerError = NSError(
    domain: "ParallelCodexTests",
    code: 1,
    userInfo: [NSLocalizedDescriptionKey: "Server unavailable"]
)
require(
    AppLanguage.chinese.userMessage(for: unavailableServerError) == "服务端不可用",
    "Chinese mode exposed a raw server error"
)
require(
    AppLanguage.english.userMessage(for: unavailableServerError) == "Server unavailable",
    "English mode unexpectedly rewrote a server error"
)
let reconnectBackoffError = PcrdClientError.connection(
    "The desktop app is waiting before reconnecting to the server."
)
require(
    AppLanguage.chinese.userMessage(for: reconnectBackoffError)
        == "桌面 App 正在等待重新连接服务端。",
    "Chinese mode exposed the raw relay reconnect backoff status"
)
let incompleteServerSetup = PcrdClientError.remote(
    "server_not_configured",
    "missing setup"
)
require(
    AppLanguage.english.userMessage(for: incompleteServerSetup)
        == "This computer's server setup is incomplete. Repair Parallel Codex Server on this computer, then try again."
        && AppLanguage.chinese.userMessage(for: incompleteServerSetup)
        == "这台设备的服务配置不完整。请修复 Parallel Codex Server 后重试。",
    "Incomplete server setup exposed an internal command or ignored the selected language"
)
let queueContextConflict = PcrdClientError.remote(
    "follow_up_context_conflict",
    "This workspace already has queued messages for another conversation. Open the existing queue before changing it."
)
require(
    AppLanguage.chinese.userMessage(for: queueContextConflict)
        == "这个工作区的另一段对话已有排队消息，请先打开现有队列。",
    "Chinese mode exposed a raw queued-message context conflict"
)
let pendingTransition = ConversationContextTransitionPlan.make(
    destination: .resumeSession,
    run: nil,
    selectedAgent: nil,
    isLatestWorkspaceRun: true,
    syncBack: true,
    hasPendingSubmission: true,
    hasRunOperation: false
)
require(
    pendingTransition.blockedReason(language: .chinese)
        == "请等待消息发送完成后再切换对话。",
    "Conversation transition warning ignored the selected language"
)
require(
    PromptSubmissionPlan.blocked("The applied result cannot be continued")
        .blockedReason(language: .chinese) == "已采用的结果无法继续",
    "Prompt submission warning ignored the selected language"
)

let adaptiveInlineColor = ParallelCodexVisualStyle.appearancePreservingAlpha(
    .labelColor,
    alpha: 0.86
)
func resolvedRed(_ color: NSColor, appearanceName: NSAppearance.Name) -> CGFloat {
    let appearance = NSAppearance(named: appearanceName)!
    var component: CGFloat = -1
    appearance.performAsCurrentDrawingAppearance {
        component = color.usingColorSpace(.deviceRGB)?.redComponent ?? -1
    }
    return component
}
require(
    resolvedRed(adaptiveInlineColor, appearanceName: .aqua) < 0.2
        && resolvedRed(adaptiveInlineColor, appearanceName: .darkAqua) > 0.8,
    "Semantic transcript colors became fixed to the appearance used at creation time"
)

print("macOS protocol contract tests passed")
