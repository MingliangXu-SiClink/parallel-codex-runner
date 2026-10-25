import Foundation

struct MarkdownListItemPresentation: Equatable, Sendable {
    let ordinal: Int
    let depth: Int
    let isOrdered: Bool
}

func markdownListItemPresentation(
    from components: [PresentationIntent.IntentType]
) -> MarkdownListItemPresentation? {
    var ordinal: Int?
    var depth = 0
    var innermostListIsOrdered: Bool?
    for component in components {
        switch component.kind {
        case .listItem(let value):
            if ordinal == nil { ordinal = value }
        case .orderedList:
            if innermostListIsOrdered == nil { innermostListIsOrdered = true }
            depth += 1
        case .unorderedList:
            if innermostListIsOrdered == nil { innermostListIsOrdered = false }
            depth += 1
        default:
            continue
        }
    }
    guard let ordinal, let innermostListIsOrdered else { return nil }
    return MarkdownListItemPresentation(
        ordinal: ordinal,
        depth: max(1, depth),
        isOrdered: innermostListIsOrdered
    )
}

enum TranscriptKind: String, Codable, Sendable {
    case user
    case thought
    case output
    case command
    case activity
    case warning
    case error
}

struct TranscriptBlock: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let kind: TranscriptKind
    var text: String
    var status: String?
    var operationId: String? = nil
    var activityType: String? = nil
    var operation: WorkOperationRecord? = nil
    var isHistorical = false
    var isFinalAnswer = false
    var linkOrigin: TranscriptLinkOrigin? = nil
    var image: TranscriptImageContent? = nil
    var question: AgentQuestionPresentation? = nil

    var isAssistantProgress: Bool {
        kind == .output && !isFinalAnswer && activityType == "assistant_progress"
    }
}

struct TranscriptImageContent: Codable, Hashable, Sendable {
    let sourceID: String
    let data: Data
}

enum NativeImageTranscript {
    static let maximumEncodedBytes = 24 * 1024 * 1024
    private static let legacyFragmentBytes = 128 * 1024

    private final class CachedImage: NSObject {
        let block: TranscriptBlock
        init(_ block: TranscriptBlock) { self.block = block }
    }

    private final class ResultCache: @unchecked Sendable {
        // NSCache is thread-safe. Only immutable, complete native results enter.
        let values: NSCache<NSString, CachedImage> = {
            let cache = NSCache<NSString, CachedImage>()
            cache.totalCostLimit = 32 * 1024 * 1024
            cache.countLimit = 16
            return cache
        }()
    }

    private static let cache = ResultCache()

    private static func eventNumber(_ block: TranscriptBlock) -> Int? {
        guard block.id.hasPrefix("event-") else { return nil }
        return Int(block.id.dropFirst(6))
    }

    static func isSource(_ block: TranscriptBlock) -> Bool {
        guard block.kind == .activity, block.operation == nil, eventNumber(block) != nil else { return false }
        // Legacy pcrd split an unrecognized native imageGeneration envelope
        // into adjacent event records. Never infer images from ordinary Base64.
        let prefix = String(block.text.prefix(512))
        if prefix.range(of: #"^\s*\{\s*"type"\s*:\s*"item\.(?:completed|updated|started)"\s*,\s*"item"\s*:\s*\{\s*"type"\s*:\s*"imageGeneration"\s*[,}]"#,
                        options: .regularExpression) != nil { return true }
        guard prefix.contains("imageGeneration"), block.text.utf8.count <= maximumEncodedBytes,
              let data = block.text.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (payload["type"] as? String)?.hasPrefix("item.") == true,
              let item = payload["item"] as? [String: Any] else { return false }
        return item["type"] as? String == "imageGeneration"
    }

    static func project(_ blocks: [TranscriptBlock]) -> [TranscriptBlock] {
        var result: [TranscriptBlock] = []
        var imagePositions: [String: Int] = [:]
        var index = 0
        while index < blocks.count {
            let source = blocks[index]
            guard isSource(source), let firstEvent = eventNumber(source) else {
                result.append(source)
                index += 1
                continue
            }
            var raw = source.text
            var end = index
            var byteCount = raw.utf8.count
            var payload: [String: Any]?
            var cached: CachedImage?
            while true {
                if byteCount > maximumEncodedBytes, blocks[end].text.hasSuffix("}") { break }
                if byteCount <= maximumEncodedBytes, raw.hasSuffix("}") {
                    cached = cache.values.object(forKey: raw as NSString)
                    if cached != nil { break }
                    if let data = raw.data(using: .utf8),
                       let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        payload = decoded
                        break
                    }
                }
                guard blocks[end].text.utf8.count == legacyFragmentBytes,
                      end + 1 < blocks.count else { break }
                let next = blocks[end + 1]
                guard next.kind == .activity, next.operation == nil,
                      next.isHistorical == source.isHistorical, next.linkOrigin == source.linkOrigin,
                      next.activityType == source.activityType, next.status == source.status,
                      next.operationId == source.operationId,
                      eventNumber(next) == firstEvent + end + 1 - index,
                      !next.text.hasPrefix("{"),
                      next.text.utf8.count == legacyFragmentBytes || next.text.hasSuffix("}") else { break }
                byteCount += next.text.utf8.count
                if byteCount <= maximumEncodedBytes { raw += next.text }
                end += 1
            }
            var block = TranscriptBlock(id: source.id, kind: .output, text: "Loading image",
                activityType: "native_image_loading", isHistorical: source.isHistorical, linkOrigin: source.linkOrigin)
            if let cached {
                block.text = cached.block.text
                block.activityType = cached.block.activityType
                block.image = cached.block.image
                block.operationId = cached.block.operationId
            } else if let item = payload?["item"] as? [String: Any], item["type"] as? String == "imageGeneration" {
                block.activityType = "native_image"
                block.text = "Image preview unavailable"
                block.operationId = item["id"] as? String
                if let encoded = item["result"] as? String, !encoded.isEmpty,
                   encoded.utf8.count <= maximumEncodedBytes,
                   let data = Data(base64Encoded: encoded), data.count <= 16 * 1024 * 1024,
                   let identifier = item["id"] as? String {
                    block.image = TranscriptImageContent(sourceID: identifier, data: data)
                } else if let failure = item["failure"] as? String, !failure.isEmpty {
                    block.text = failure
                } else if ["in_progress", "inProgress", "running"].contains(item["status"] as? String ?? "") {
                    block.activityType = "native_image_loading"
                    block.text = "Loading image"
                }
                cache.values.setObject(CachedImage(block), forKey: raw as NSString,
                    cost: byteCount + (block.image?.data.count ?? 0))
            } else if byteCount > maximumEncodedBytes {
                block.activityType = "native_image"
                block.text = "Image too large to preview"
            } else if payload != nil || raw.hasSuffix("}") || end + 1 < blocks.count {
                block.activityType = "native_image"
                block.text = "Image preview unavailable"
            }
            if let identity = block.operationId, !identity.isEmpty, let previous = imagePositions[identity] {
                if block.activityType != "native_image_loading" || result[previous].image == nil {
                    block = TranscriptBlock(id: result[previous].id, kind: block.kind, text: block.text,
                        operationId: identity, activityType: block.activityType,
                        isHistorical: block.isHistorical, linkOrigin: block.linkOrigin, image: block.image)
                    result[previous] = block
                }
            } else {
                if let identity = block.operationId, !identity.isEmpty { imagePositions[identity] = result.count }
                result.append(block)
            }
            index = end + 1
        }
        return result
    }
}

struct AgentConversationPage: Codable, Equatable, Sendable {
    struct Item: Codable, Equatable, Sendable {
        let eventID: Int
        let block: TranscriptBlock
    }
    var cursor = 0
    var hasMore = true
    var items: [Item] = []
}

struct AgentRecentWork: Codable, Equatable, Sendable {
    static let initialEventWindow = 2_048
    let startCursor: Int
    var cursor: Int
    var hasMore = true
    var items: [AgentConversationPage.Item] = []
}

struct AgentTranscript: Codable, Equatable, Sendable {
    /// Identifies the exact branch that produced this cached transcript. The
    /// fields are optional so caches written by older clients still decode;
    /// an unbound legacy value is discarded before it can be displayed.
    var sourceRunID: String? = nil
    var sourceAgent: Int? = nil
    var eventIDs: Set<Int> = []
    var blocks: [TranscriptBlock] = []
    var cursor = 0
    var hasMore = true
    var finalMessage = ""
    var finalMessageCursor = 0
    var finalMessageHasMore = true
    var finalMessageID = ""
    var finalMessagePresentationID: String? = nil
    /// A pagination EOF observed before execution ends is not a terminal
    /// answer receipt. Missing in older caches means it still needs confirmation.
    var finalMessageReadStatus: String? = nil
    var error = ""
    var artifactError = ""
    var inputRequests: [AgentInputRequest]? = nil
    var questionAnchors: [String: String]? = nil
    /// Conversation output advances independently of the complete tool/work log.
    var conversation: AgentConversationPage? = nil
    /// A separate live range never certifies or advances the full-history cursor.
    var recentWork: AgentRecentWork? = nil

    mutating func preserveRecentWorkProgress(from additionalBlocks: [TranscriptBlock] = []) {
        guard var recent = recentWork else { return }
        var latest: [String: TranscriptBlock] = [:]
        for block in blocks + additionalBlocks {
            guard let operation = block.operation,
                  operation.eventId > (latest[operation.id]?.operation?.eventId ?? -1) else { continue }
            latest[operation.id] = block
        }
        recent.items = recent.items.map { item in
            guard let identity = item.block.operation?.id, let block = latest[identity],
                  let operation = block.operation else { return item }
            var retained = [item.block]
            TranscriptFormatter.append(items: [.init(id: block.id, eventId: operation.eventId,
                kind: block.kind.rawValue, text: block.text, status: operation.status.rawValue,
                operationId: block.operationId, activityType: block.activityType, operation: operation)], to: &retained)
            return .init(eventID: item.eventID, block: retained[0])
        }
        recentWork = recent
    }

    mutating func copyFinalMessage(from transcript: Self) {
        finalMessage = transcript.finalMessage
        finalMessageCursor = transcript.finalMessageCursor
        finalMessageHasMore = transcript.finalMessageHasMore
        finalMessageID = transcript.finalMessageID
        finalMessagePresentationID = transcript.finalMessagePresentationID
        finalMessageReadStatus = transcript.finalMessageReadStatus
        artifactError = transcript.artifactError
    }

    mutating func clearUnavailableFinalMessage() {
        finalMessage = ""
        finalMessageCursor = 0
        finalMessageHasMore = false
        finalMessageID = ""
        finalMessageReadStatus = nil
    }

    /// Returns true when a replaced artifact must be read again from byte zero.
    mutating func mergeFinalMessage(
        _ detail: RunAgentDetail,
        requested: Self,
        requestedStatus: String?
    ) -> Bool {
        if detail.runId == sourceRunID, detail.agent == sourceAgent, let incoming = detail.inputRequests {
            mergeInputRequests(incoming)
        }
        // An answer-only read and a work preload may overlap. A late receipt
        // cannot overwrite or append to an answer advanced by the other reader.
        guard finalMessageID == requested.finalMessageID,
              finalMessageCursor == requested.finalMessageCursor,
              finalMessage == requested.finalMessage else { return false }
        // Capture status before issuing the RPC, never from the newer summary
        // at callback time. A late running snapshot cannot certify or regress
        // a terminal answer, even when its work events remain useful.
        let readStatus = requestedStatus?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !isTerminalAgentStatus(finalMessageReadStatus)
                || isTerminalAgentStatus(readStatus) else { return false }
        guard detail.artifactError?.isEmpty != false else {
            artifactError = detail.artifactError ?? ""
            return false
        }
        guard let page = detail.finalMessagePage else {
            guard !detail.finalMessage.isEmpty || finalMessage.isEmpty else { return false }
            finalMessage = detail.finalMessage
            finalMessageCursor = detail.finalMessage.utf8.count
            finalMessageHasMore = false
            finalMessageID = ""
            finalMessageReadStatus = readStatus
            artifactError = ""
            return false
        }
        guard page.nextCursor >= page.cursor,
              !page.hasMore || page.nextCursor > page.cursor else { return false }
        if page.messageId.isEmpty, page.totalBytes == 0 {
            guard finalMessage.isEmpty else { return false }
            clearUnavailableFinalMessage()
            finalMessageReadStatus = readStatus
            artifactError = ""
            return false
        }
        if !finalMessageID.isEmpty, !page.messageId.isEmpty, finalMessageID != page.messageId {
            clearUnavailableFinalMessage()
            finalMessageHasMore = true
            if page.cursor != 0 { return true }
        }
        if page.cursor == finalMessageCursor {
            finalMessage += page.text
            finalMessageCursor = page.nextCursor
            finalMessageHasMore = page.hasMore
            finalMessageID = page.messageId
            finalMessageReadStatus = readStatus
            artifactError = ""
        }
        return false
    }

    mutating func mergeInputRequests(_ incoming: [AgentInputRequest]) {
        inputRequests = AgentInputRequest.merge(incoming, into: inputRequests ?? [])
        var anchors = questionAnchors ?? [:]
        let last = conversation?.items.last?.block.id ?? blocks.last?.id ?? "current-prompt"
        for request in inputRequests ?? [] where anchors[request.id] == nil {
            anchors[request.id] = last
        }
        questionAnchors = anchors
    }
}

func agentTranscriptBoundToBranch(
    _ cached: AgentTranscript?,
    runID: String,
    agent: Int
) -> AgentTranscript {
    guard var cached,
          cached.sourceRunID == runID,
          cached.sourceAgent == agent else {
        return AgentTranscript(sourceRunID: runID, sourceAgent: agent)
    }
    cached.sourceRunID = runID
    cached.sourceAgent = agent
    return cached
}

struct TranscriptProjectionInput: Equatable, Sendable {
    let runID: String?
    let runStatus: String?
    let prompt: String
    let history: [ResumeHistoryEntry]
    let transcript: AgentTranscript
    let runError: String?
    let workerError: String?
    let pendingSubmission: PersistedPromptSubmission?
    var questions: [AgentQuestionPresentation] = []

    static func == (lhs: Self, rhs: Self) -> Bool {
        let pendingEqual: Bool
        if let before = lhs.pendingSubmission, let after = rhs.pendingSubmission {
            pendingEqual = before.id == after.id && before.prompt == after.prompt
                && before.conversationLabel == after.conversationLabel
        } else {
            pendingEqual = lhs.pendingSubmission == nil && rhs.pendingSubmission == nil
        }
        return pendingEqual && lhs.questions == rhs.questions && lhs.runID == rhs.runID && lhs.runStatus == rhs.runStatus
            && lhs.prompt == rhs.prompt && lhs.history == rhs.history
            // Transport cursors are not body content; question presentation is
            // compared separately so draft/receipt updates preserve native cells.
            // Keep full AgentTranscript equality for merge/late-response guards.
            && lhs.transcript.sourceRunID == rhs.transcript.sourceRunID
            && lhs.transcript.sourceAgent == rhs.transcript.sourceAgent
            && lhs.transcript.blocks == rhs.transcript.blocks
            && lhs.transcript.conversation?.items == rhs.transcript.conversation?.items
            && lhs.transcript.recentWork?.items == rhs.transcript.recentWork?.items
            && lhs.transcript.finalMessage == rhs.transcript.finalMessage
            && lhs.transcript.finalMessageHasMore == rhs.transcript.finalMessageHasMore
            && lhs.transcript.finalMessagePresentationID == rhs.transcript.finalMessagePresentationID
            && lhs.transcript.error == rhs.transcript.error
            && lhs.transcript.artifactError == rhs.transcript.artifactError
            && lhs.runError == rhs.runError && lhs.workerError == rhs.workerError
    }
}

struct TranscriptTimelineProjectionInput: Equatable {
    let transcriptGeneration: UInt64
    let runID: String
    let agent: Int
    let expanded: Bool
    let language: String
    var workRevision: Int = 0
}

struct ConversationTimelineItemsProjectionInput: Equatable {
    let timelineGeneration: UInt64
    let switchState: ConversationAgentSwitchState?
}

struct TranscriptCollectionUpdatePlan: Equatable, Sendable {
    let reloadAll: Bool
    let changedIndices: [Int]
    let insertedIndices: [Int]

    static func make(
        old: [TranscriptBlock],
        new: [TranscriptBlock]
    ) -> Self {
        let sharedCount = min(old.count, new.count)
        let preservesPrefix = (0..<sharedCount).allSatisfy {
            old[$0].id == new[$0].id
        }
        guard preservesPrefix, new.count >= old.count else {
            return Self(reloadAll: true, changedIndices: [], insertedIndices: [])
        }
        return Self(
            reloadAll: false,
            changedIndices: (0..<sharedCount).filter { old[$0] != new[$0] },
            insertedIndices: Array(old.count..<new.count)
        )
    }
}

/// The one branch selector embedded in the current turn. Historical messages
/// are deliberately absent from this state: changing an Agent may only replace
/// the current turn's candidate branch.
struct ConversationAgentSwitchState: Equatable, Sendable {
    let runID: String
    let agents: [RunAgentSummary]
    let selectedAgent: Int?
    var pendingAgent: Int? = nil
    let recommendedAgent: Int?
    let finalizedAgent: Int?
    let selectionEnabled: Bool
    var executionConfirmed = false

    /// Selection feedback belongs to the click, while the candidate body is
    /// replaced only after its first page is ready. Keeping those two moments
    /// distinct makes a slow branch load feel responsive without flashing an
    /// empty transcript.
    var highlightedAgent: Int? { pendingAgent ?? selectedAgent }

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.runID == rhs.runID
            && lhs.selectedAgent == rhs.selectedAgent
            && lhs.pendingAgent == rhs.pendingAgent
            && lhs.recommendedAgent == rhs.recommendedAgent
            && lhs.finalizedAgent == rhs.finalizedAgent
            && lhs.selectionEnabled == rhs.selectionEnabled
            && lhs.executionConfirmed == rhs.executionConfirmed
            && lhs.agents.count == rhs.agents.count
            && zip(lhs.agents, rhs.agents).allSatisfy(agentPresentationMatches)
    }

    private static func agentPresentationMatches(
        _ lhs: RunAgentSummary,
        _ rhs: RunAgentSummary
    ) -> Bool {
        guard lhs.agent == rhs.agent,
              lhs.name == rhs.name,
              lhs.role == rhs.role,
              lhs.status == rhs.status,
              lhs.activity?.readerState == rhs.activity?.readerState,
              lhs.rejected == rhs.rejected,
              lhs.recommended == rhs.recommended,
              lhs.finalized == rhs.finalized,
              lhs.workspaceAvailable == rhs.workspaceAvailable else {
            return false
        }

        // Running summaries change elapsed time and reasoning counters on every
        // poll, but neither change the tab's geometry or action. Treating those
        // counters as a new switcher tears down an in-progress click. Once an
        // Agent reaches a terminal state, include its final metrics so the help
        // text is refreshed exactly once with the completed values.
        guard lhs.isTerminal || rhs.isTerminal else { return true }
        return lhs.seconds == rhs.seconds
            && lhs.reasoningTokens == rhs.reasoningTokens
            && lhs.reasoningTokenCounts == rhs.reasoningTokenCounts
    }
}

extension ConversationAgentSwitchState {
    func adjacentAgent(delta: Int) -> Int? {
        let ordered = agents.filter { !$0.isSynthesis }.sorted { $0.agent < $1.agent }
            + agents.filter(\.isSynthesis).sorted { $0.agent < $1.agent }
        guard let index = ordered.firstIndex(where: { $0.agent == highlightedAgent }),
              ordered.indices.contains(index + delta) else { return nil }
        return ordered[index + delta].agent
    }
}

struct ConversationWorkProcess: Equatable, Sendable {
    let id: String
    var blocks: [TranscriptBlock]
}

enum ConversationTimelineItem: Identifiable, Equatable, Sendable {
    case message(TranscriptBlock)
    case agentSwitch(ConversationAgentSwitchState)
    case workProcess(ConversationWorkProcess)

    var id: String {
        switch self {
        case .message(let block): return "message:\(block.id)"
        case .agentSwitch(let state): return "agent-switch:\(state.runID)"
        case .workProcess(let process): return process.id
        }
    }

    func containsMessage(id: String) -> Bool {
        if case .workProcess(let process) = self {
            return process.blocks.contains { "message:\($0.id)" == id }
        }
        return self.id == id
    }
}

enum ConversationTimelineProjection {
    static func make(
        blocks: [TranscriptBlock],
        switchState: ConversationAgentSwitchState?
    ) -> [ConversationTimelineItem] {
        // A selector with one branch cannot switch anything. Keeping it in the
        // transcript turns internal Agent naming into prominent UI noise and
        // adds an unexplained control between the prompt and answer.
        var items: [ConversationTimelineItem] = []
        var process: ConversationWorkProcess?
        for block in blocks {
            if process != nil, TranscriptFormatter.isAgentWorkBlock(block),
               block.activityType?.hasPrefix("work_summary") != true {
                process?.blocks.append(block)
                continue
            }
            if let current = process, !current.blocks.isEmpty { items.append(.workProcess(current)) }
            process = nil
            items.append(.message(block))
            if block.activityType == "work_summary_expanded" {
                process = ConversationWorkProcess(id: "work-process:\(block.id)", blocks: [])
            }
        }
        if let process, !process.blocks.isEmpty { items.append(.workProcess(process)) }
        guard let switchState, switchState.agents.count > 1 else { return items }

        let switchIndex: Int
        if let promptIndex = items.firstIndex(where: {
            guard case .message(let block) = $0 else { return false }
            return block.id == "current-prompt" || block.id.hasPrefix("current-prompt-visible-")
        }) {
            switchIndex = items.index(after: promptIndex)
        } else if let currentIndex = items.firstIndex(where: {
            guard case .message(let block) = $0 else { return false }
            return !block.isHistorical
        }) {
            switchIndex = currentIndex
        } else {
            switchIndex = items.endIndex
        }
        items.insert(.agentSwitch(switchState), at: switchIndex)
        return items
    }

    static func changedInlineControlID(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem]
    ) -> String? {
        let oldByID = Dictionary(uniqueKeysWithValues: old.map { ($0.id, $0) })
        let newByID = Dictionary(uniqueKeysWithValues: new.map { ($0.id, $0) })
        return old.first { item in
            guard let replacement = newByID[item.id] else { return false }
            switch item {
            case .workProcess:
                return false
            case .agentSwitch(let oldState):
                // Candidate status and pending-selection updates do not change
                // this row's fixed geometry. Treating every poll as a scroll
                // anchor forced the collection view to reload the control while
                // the user was trying to click it. A committed branch change
                // still anchors here because the transcript suffix is replaced.
                guard case .agentSwitch(let newState) = replacement else { return true }
                return oldState.selectedAgent != newState.selectedAgent
            case .message(let block):
                if let question = block.question, case .message(let next) = replacement,
                   question.expanded != next.question?.expanded { return true }
                if block.activityType?.hasPrefix("work_summary") == true {
                    return replacement != item
                }
                guard block.operation != nil, case .message(let next) = replacement else { return false }
                if block.activityType != next.activityType { return true }
                // Loading and the eventual output are the same disclosure.
                // Keep its visible owner anchored and update the detail view
                // in place when the asynchronous page arrives.
                let detailID = "message:\(block.id):details"
                return next.activityType == "work_operation_expanded"
                    && oldByID[detailID] != newByID[detailID]
            }
        }?.id
    }

    static func changesOnlyFixedAgentSwitch(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem]
    ) -> Bool {
        guard old.count == new.count else { return false }
        var foundChange = false
        for (oldItem, newItem) in zip(old, new) {
            guard oldItem.id == newItem.id else { return false }
            guard oldItem != newItem else { continue }
            guard case .agentSwitch = oldItem,
                  case .agentSwitch = newItem else { return false }
            foundChange = true
        }
        return foundChange
    }

    static func toggledWorkDisclosureID(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem]
    ) -> String? {
        let newByID = Dictionary(uniqueKeysWithValues: new.map { ($0.id, $0) })
        return old.compactMap { item -> String? in
            guard case .message(let oldBlock) = item,
                  oldBlock.activityType?.hasPrefix("work_summary") == true,
                  case .message(let newBlock)? = newByID[item.id],
                  newBlock.activityType?.hasPrefix("work_summary") == true,
                  oldBlock.activityType != newBlock.activityType else {
                return nil
            }
            return item.id
        }.first
    }

    static func committedAgentSwitchID(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem]
    ) -> String? {
        let newByID = Dictionary(uniqueKeysWithValues: new.map { ($0.id, $0) })
        return old.compactMap { item -> String? in
            guard case .agentSwitch(let oldState) = item,
                  oldState.pendingAgent != nil,
                  case .agentSwitch(let newState)? = newByID[item.id],
                  newState.pendingAgent == nil,
                  oldState.selectedAgent != newState.selectedAgent else {
                return nil
            }
            return item.id
        }.first
    }
}

struct ConversationTimelineUpdatePlan: Equatable, Sendable {
    struct Move: Equatable, Sendable {
        let from: Int
        let to: Int
    }
    let reloadAll: Bool
    let changedIndices: [Int]
    let removedIndices: [Int]
    let insertedIndices: [Int]
    var movedItems: [Move] = []

    static func makeForInlineDisclosure(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem],
        preservingEditors: Bool = false
    ) -> Self {
        let oldIDs = Set(old.map(\.id))
        let newIDs = Set(new.map(\.id))
        guard preservingEditors || old.filter({ newIDs.contains($0.id) }).map(\.id)
                == new.filter({ oldIDs.contains($0.id) }).map(\.id) else {
            return make(old: old, new: new)
        }
        let oldByID = Dictionary(uniqueKeysWithValues: old.map { ($0.id, $0) })
        // Shared rows keep their views, including the answer after the work
        // section. Changed indices address the new array for reconfiguration.
        let moves: [Move] = preservingEditors ? new.map(\.id).difference(from: old.map(\.id)).inferringMoves()
            .compactMap { change in
                guard case .insert(let to, _, let from?) = change else { return nil }
                return Move(from: from, to: to)
            } : []
        return Self(
            reloadAll: false,
            changedIndices: new.indices.filter { index in
                oldByID[new[index].id].map { $0 != new[index] } ?? false
            },
            removedIndices: old.indices.filter { !newIDs.contains(old[$0].id) },
            insertedIndices: new.indices.filter { !oldIDs.contains(new[$0].id) },
            movedItems: moves
        )
    }

    static func make(
        old: [ConversationTimelineItem],
        new: [ConversationTimelineItem]
    ) -> Self {
        let sharedCount = min(old.count, new.count)
        let commonPrefixCount = (0..<sharedCount).first(where: {
            old[$0].id != new[$0].id
        }) ?? sharedCount
        let preservesCompleteSharedPrefix = commonPrefixCount == sharedCount
        let preservesStableInlineControl = old[..<commonPrefixCount].contains {
            switch $0 {
            case .workProcess:
                return true
            case .agentSwitch:
                return true
            case .message(let block):
                return block.activityType?.hasPrefix("work_summary") == true
            }
        }
        guard preservesCompleteSharedPrefix || preservesStableInlineControl else {
            return Self(
                reloadAll: true,
                changedIndices: [],
                removedIndices: [],
                insertedIndices: []
            )
        }

        // Agent switching owns only the suffix after the stable current-turn
        // boundary. Replacing that suffix keeps accepted history and the user
        // prompt mounted instead of repainting the entire conversation.
        return Self(
            reloadAll: false,
            changedIndices: (0..<commonPrefixCount).filter { old[$0] != new[$0] },
            removedIndices: Array(commonPrefixCount..<old.count),
            insertedIndices: Array(commonPrefixCount..<new.count)
        )
    }
}

enum TranscriptFormatter {
    private static let runtimeLogExpression = try? NSRegularExpression(
        pattern: #"^(?:\S+\s+)?(ERROR|WARN(?:ING)?)\s+([A-Za-z0-9_.:-]+):\s*(.+)$"#,
        options: [.caseInsensitive, .dotMatchesLineSeparators]
    )

    static func isAgentWorkBlock(_ block: TranscriptBlock) -> Bool {
        switch block.kind {
        case .thought, .command: return true
        case .activity: return block.activityType != "turn_interrupted"
        // Commentary is addressed to the reader, not an internal work event.
        // It also marks the boundary between consecutive work disclosures.
        default: return false
        }
    }

    static func blocksWithWorkDisclosure(
        _ blocks: [TranscriptBlock],
        runID: String,
        agent: Int,
        expanded: Bool,
        language: AppLanguage = .english,
        sectionOverrides: [String: Bool] = [:]
    ) -> [TranscriptBlock] {
        var result: [TranscriptBlock] = []
        var work: [TranscriptBlock] = []
        func flushWork() {
            guard let first = work.first else { return }
            let id = "work-summary-\(runID)-\(agent)-\(first.id)"
            let isExpanded = sectionOverrides[id] ?? expanded
            result.append(TranscriptBlock(id: id, kind: .activity,
                text: workSummaryText(work, language: language),
                activityType: isExpanded ? "work_summary_expanded" : "work_summary"))
            if isExpanded { result.append(contentsOf: work) }
            work.removeAll(keepingCapacity: true)
        }
        for block in blocks {
            // Unknown legacy phases remain where the reader saw them. A final
            // answer must not retroactively move earlier messages into a fold.
            if isAgentWorkBlock(block) {
                if !block.isHistorical {
                    work.append(block)
                }
            } else {
                flushWork()
                result.append(block)
            }
        }
        flushWork()
        return result
    }

    private static func workSummaryText(
        _ blocks: [TranscriptBlock],
        language: AppLanguage
    ) -> String {
        // Summarize what happened, not the number of internal events received.
        let kinds = Set(blocks.compactMap { $0.operation?.kind })
        var parts: [String] = []
        if kinds.contains(.fileRead) { parts.append(language.text("读取文件", "Read files")) }
        if kinds.contains(.fileSearch) || kinds.contains(.directoryList) {
            parts.append(language.text("检索项目", "Explored project"))
        }
        if kinds.contains(.fileChange) || blocks.contains(where: { $0.activityType == "file_change" }) {
            parts.append(language.text("编辑文件", "Edited files"))
        }
        if kinds.contains(.webSearch) || blocks.contains(where: { $0.activityType == "web_search" }) {
            parts.append(language.text("搜索网页", "Searched the web"))
        }
        if kinds.contains(.command) || blocks.contains(where: { $0.kind == .command && $0.operation == nil }) {
            parts.append(language.text("运行命令", "Ran commands"))
        }
        return parts.isEmpty ? language.text("思考与工作过程", "Reasoning and work")
            : parts.joined(separator: language.text("、", " · "))
    }

    static func coalescedForPresentation(_ blocks: [TranscriptBlock]) -> [TranscriptBlock] {
        var values: [TranscriptBlock] = []
        for rawBlock in blocks {
            var block = rawBlock
            if block.kind == .thought {
                block.text = reasoningPresentationText(block.text)
            }
            if block.kind == .thought,
               block.status == nil,
               let lastIndex = values.indices.last,
               values[lastIndex].kind == .thought,
               values[lastIndex].status == nil,
               values[lastIndex].isHistorical == block.isHistorical,
               values[lastIndex].linkOrigin == block.linkOrigin {
                values[lastIndex].text += "\n\n" + block.text
                continue
            }
            values.append(block)
        }
        return values
    }

    static func coalescedVisibleConversation(_ blocks: [TranscriptBlock]) -> [TranscriptBlock] {
        var values: [TranscriptBlock] = []
        for block in blocks {
            if block.kind == .output,
               block.question == nil, values.last?.question == nil,
               block.activityType?.hasPrefix("native_image") != true,
               block.status == nil,
               let lastIndex = values.indices.last,
               values[lastIndex].kind == .output,
               values[lastIndex].isHistorical == block.isHistorical,
               values[lastIndex].linkOrigin == block.linkOrigin,
               values[lastIndex].status == nil,
               (values[lastIndex].id == block.id || block.isFinalAnswer || values[lastIndex].isFinalAnswer),
               equivalentAssistantPresentation(values[lastIndex].text, block.text) {
                if block.isFinalAnswer {
                    // The final artifact has the canonical, sync-back-aware
                    // file links. Keep the streamed block's stable identity,
                    // but present the authoritative final text.
                    values[lastIndex].text = block.text
                }
                values[lastIndex].isFinalAnswer = values[lastIndex].isFinalAnswer
                    || block.isFinalAnswer
                continue
            }
            if block.kind == .output,
               block.question == nil, values.last?.question == nil,
               block.activityType?.hasPrefix("native_image") != true,
               block.status == nil,
               let lastIndex = values.indices.last,
               values[lastIndex].kind == .output,
               values[lastIndex].isHistorical == block.isHistorical,
               values[lastIndex].linkOrigin == block.linkOrigin,
               values[lastIndex].status == nil,
               values[lastIndex].activityType == block.activityType,
               (
                   (!block.isFinalAnswer && !values[lastIndex].isFinalAnswer)
                       || (block.isHistorical && values[lastIndex].isHistorical)
               ) {
                values[lastIndex].text += "\n\n" + block.text
                values[lastIndex].isFinalAnswer = values[lastIndex].isFinalAnswer
                    || block.isFinalAnswer
                continue
            }
            values.append(block)
        }
        return values
    }

    static func reasoningPresentationText(_ text: String) -> String {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return value }
        let boundaryPattern = #"\*{2,3}[\t ]+\*{2,3}"#
        let separated = value.replacingOccurrences(
            of: boundaryPattern,
            with: "\n\n",
            options: .regularExpression
        )
        if separated != value {
            return strippingOuterEmphasis(separated)
                .components(separatedBy: "\n\n")
                .map(strippingWrappingEmphasis)
                .joined(separator: "\n\n")
        }
        return strippingWrappingEmphasis(value)
    }

    static func markdownPresentationText(_ text: String) -> String {
        var inCodeFence = false
        return text
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map { rawLine -> String in
                let line = String(rawLine)
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                    inCodeFence.toggle()
                    return line
                }
                guard !inCodeFence else { return line }
                if trimmed == "**" || trimmed == "***" { return "" }
                let markerCount = trimmed.components(separatedBy: "**").count - 1
                guard markerCount % 2 == 1 else { return line }
                let indentation = String(line.prefix { $0 == " " || $0 == "\t" })
                var content = trimmed
                if content.hasPrefix("**") {
                    content.removeFirst(2)
                } else if content.hasSuffix("**") {
                    content.removeLast(2)
                }
                return indentation + content
            }
            .joined(separator: "\n")
    }

    static func assistantPresentationText(_ text: String) -> String {
        var value = text.replacingOccurrences(
            of: #"(?is)<oai-mem-citation\b[^>]*>.*?</oai-mem-citation\s*>"#,
            with: "",
            options: .regularExpression
        )
        // During streaming the closing tag may not have arrived yet. Hide the
        // incomplete metadata tail immediately instead of briefly rendering
        // protocol bookkeeping as part of the Agent answer.
        if let unfinished = value.range(
            of: #"(?is)<oai-mem-citation\b[^>]*>"#,
            options: .regularExpression
        ) {
            value.removeSubrange(unfinished.lowerBound..<value.endIndex)
        }
        value = value.replacingOccurrences(
            of: #"(?is)</?oai-mem-citation\s*>"#,
            with: "",
            options: .regularExpression
        )
        value = value.replacingOccurrences(
            of: #"\n[\t ]*\n(?:[\t ]*\n)+"#,
            with: "\n\n",
            options: .regularExpression
        ).trimmingCharacters(in: .whitespacesAndNewlines)
        // Older pcrd processes merged Codex's event_msg and response_item copy
        // of the same final answer into one history entry. The duplicate may
        // predate memory citations, so presentation must not use their presence
        // as the signal that the answer is mirrored.
        let answer = deduplicatingMirroredFinalAnswer(value)
        return codexFileCitationsToMarkdown(answer)
    }

    static func codexFileCitationsToMarkdown(_ text: String) -> String {
        guard text.contains(":codex-file-citation{") else { return text }
        let directivePattern = #":codex-file-citation\{(?:(?:[^"{}\\]|\\.)|"(?:[^"\\]|\\.)*")*\}"#
        let pathPattern = "(?:^|[\\{\\s])path\\s*=\\s*\\\"(?<path>(?:[^\\\"\\\\]|\\\\.)*)\\\""
        guard let directiveExpression = try? NSRegularExpression(pattern: directivePattern),
              let pathExpression = try? NSRegularExpression(pattern: pathPattern) else {
            return text
        }
        let result = NSMutableString(string: text)
        let matches = directiveExpression.matches(
            in: text,
            range: NSRange(location: 0, length: (text as NSString).length)
        )
        for match in matches.reversed() {
            let directive = (text as NSString).substring(with: match.range)
            guard let pathMatch = pathExpression.firstMatch(
                in: directive,
                range: NSRange(location: 0, length: (directive as NSString).length)
            ) else { continue }
            let rawPath = (directive as NSString).substring(
                with: pathMatch.range(withName: "path")
            )
            guard let encoded = "\"\(rawPath)\"".data(using: .utf8),
                  let path = (try? JSONSerialization.jsonObject(
                      with: encoded,
                      options: .fragmentsAllowed
                  )) as? String,
                  !path.isEmpty else { continue }

            let destination: String
            let labelPath: String
            if path.lowercased().hasPrefix("file:"),
               let url = URL(string: path),
               url.isFileURL,
               !url.path.isEmpty {
                destination = url.absoluteString
                labelPath = url.path
            } else if path.hasPrefix("/") {
                destination = URL(fileURLWithPath: path).absoluteString
                labelPath = path
            } else {
                continue
            }
            let label = URL(fileURLWithPath: labelPath).lastPathComponent
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "[", with: "\\[")
                .replacingOccurrences(of: "]", with: "\\]")
            guard !label.isEmpty else { continue }
            result.replaceCharacters(
                in: match.range,
                with: "[\(label)](<\(destination)>)"
            )
        }
        return result as String
    }

    private static func deduplicatingMirroredFinalAnswer(_ text: String) -> String {
        let bytes = Array(text.utf8)
        for separator in [[UInt8(10)], [UInt8(10), UInt8(10)], [UInt8(13), UInt8(10)]] {
            guard bytes.count > separator.count,
                  (bytes.count - separator.count).isMultiple(of: 2) else { continue }
            let half = (bytes.count - separator.count) / 2
            guard Array(bytes[half..<(half + separator.count)]) == separator,
                  bytes[..<half] == bytes[(half + separator.count)...] else { continue }
            return String(decoding: bytes[..<half], as: UTF8.self)
        }
        return text
    }

    static func removingInternalAssistantMetadata(
        from blocks: [TranscriptBlock]
    ) -> [TranscriptBlock] {
        blocks.compactMap { original in
            guard original.kind == .output else { return original }
            var block = original
            block.text = assistantPresentationText(block.text)
            if block.text.isEmpty && block.status == nil { return nil }
            return block
        }
    }

    private static func strippingOuterEmphasis(_ text: String) -> String {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.first == "*" { value.removeFirst() }
        while value.last == "*" { value.removeLast() }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func strippingWrappingEmphasis(_ text: String) -> String {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        for marker in ["***", "**", "*"]
            where value.hasPrefix(marker) && value.hasSuffix(marker) {
            let inner = String(value.dropFirst(marker.count).dropLast(marker.count))
            if !inner.contains(marker) {
                return inner.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        return value
    }

    static func blocks(
        history: [ResumeHistoryEntry],
        prompt: String,
        transcript: AgentTranscript
    ) -> [TranscriptBlock] {
        var values: [TranscriptBlock] = []
        var operationIdentities: [String: String] = [:]
        for item in transcript.recentWork?.items ?? [] {
            if let operation = item.block.operation, operationIdentities[operation.id] == nil {
                operationIdentities[operation.id] = item.block.id
            }
        }
        var streamedBlocks = transcript.blocks.map { block in
            guard let operation = block.operation, let identity = operationIdentities[operation.id],
                  identity != block.id else { return block }
            return TranscriptBlock(id: identity, kind: block.kind, text: block.text, status: block.status,
                operationId: block.operationId, activityType: block.activityType, operation: operation,
                isHistorical: block.isHistorical, isFinalAnswer: block.isFinalAnswer, linkOrigin: block.linkOrigin,
                image: block.image, question: block.question)
        }
        var streamIndex: BlockIndex? = BlockIndex(streamedBlocks)
        let supplements = (transcript.conversation?.items ?? []) + (transcript.recentWork?.items ?? [])
        var knownEvents = transcript.eventIDs
        for item in supplements.sorted(by: { $0.eventID < $1.eventID })
            where knownEvents.insert(item.block.operation?.eventId ?? item.eventID).inserted {
            append(item.block, to: &streamedBlocks, index: &streamIndex)
        }
        streamedBlocks = NativeImageTranscript.project(streamedBlocks)

        for (index, entry) in history.enumerated() {
            let text = cleaned(entry.text)
            guard !text.isEmpty else { continue }
            let kind = historyKind(entry.category)
            let id = entry.id.isEmpty ? "history-\(index)" : entry.id
            if kind == .user, let prompt = userTextBeforeCodexInterruption(text) {
                if !prompt.isEmpty {
                    values.append(TranscriptBlock(id: id, kind: .user, text: prompt, isHistorical: true))
                }
                values.append(TranscriptBlock(
                    id: "\(id):interrupted", kind: .activity, text: "Response interrupted",
                    activityType: "turn_interrupted", isHistorical: true
                ))
                continue
            }
            values.append(contentsOf: AgentQuestionTranscript.projectHistory(
                TranscriptBlock(
                    id: id,
                    kind: kind,
                    text: text,
                    status: nil,
                    isHistorical: true,
                    isFinalAnswer: kind == .output,
                    linkOrigin: entry.linkOrigin,
                    question: entry.question
                ), entry: entry
            ))
        }

        let currentTurnStart = values.count
        let input = cleaned(prompt)
        if !input.isEmpty {
            // Run-scoped history ends before the current rollout. Identical
            // wording in an older turn is not a duplicate of this prompt.
            values.append(
                TranscriptBlock(id: "current-prompt", kind: .user, text: input, status: nil)
            )
        }

        let finalMessage = cleaned(transcript.finalMessage)
        let presentedFinalMessage = assistantPresentationText(finalMessage)
        let answerFirstID = transcript.finalMessagePresentationID
        var mirroredFinalIndex: Int?
        if answerFirstID != nil, !finalMessage.isEmpty {
            mirroredFinalIndex = streamedBlocks.indices.last(where: {
                streamedBlocks[$0].kind == .output
                    && equivalentAssistantPresentation(streamedBlocks[$0].text, finalMessage)
            })
            if mirroredFinalIndex == nil, !presentedFinalMessage.isEmpty, transcript.finalMessageHasMore {
                let matches = streamedBlocks.indices.filter {
                    let block = streamedBlocks[$0]
                    return block.kind == .output && block.isFinalAnswer
                        && markdownVisibleFingerprint(assistantPresentationText(block.text))
                            .hasPrefix(markdownVisibleFingerprint(presentedFinalMessage))
                }
                if let last = matches.last, matches.allSatisfy({
                    equivalentAssistantPresentation(streamedBlocks[$0].text, streamedBlocks[last].text)
                }) {
                    mirroredFinalIndex = last
                }
            }
        }
        var visibleIndex: BlockIndex? = BlockIndex(values)
        for (index, block) in streamedBlocks.enumerated() where index != mirroredFinalIndex {
            append(block, to: &values, index: &visibleIndex)
        }

        if !finalMessage.isEmpty {
            if let answerFirstID {
                // A completed event can contain the rest of a paged artifact.
                // Keep its full text readable without changing the byte cursor
                // or hiding unrelated final messages.
                let answer = transcript.finalMessageHasMore
                    ? mirroredFinalIndex.map { assistantPresentationText(streamedBlocks[$0].text) }
                        ?? presentedFinalMessage
                    : presentedFinalMessage
                append(TranscriptBlock(id: answerFirstID, kind: .output,
                    text: answer, status: nil, isFinalAnswer: true), to: &values)
            } else if let index = values.indices.dropFirst(currentTurnStart).last(where: {
                values[$0].kind == .output
                    && equivalentAssistantPresentation(
                        values[$0].text,
                        presentedFinalMessage
                    )
            }) {
                // Event text may still contain the temporary Agent workspace
                // path from an older disk cache. The final artifact is already
                // rewritten to the real workspace, so use it without changing
                // the event's stable presentation identity.
                values[index].text = presentedFinalMessage
                values[index].isFinalAnswer = true
            } else {
                append(
                    TranscriptBlock(
                        id: "final-message",
                        kind: .output,
                        text: finalMessage,
                        status: nil,
                        isFinalAnswer: true
                    ),
                    to: &values
                )
            }
        }

        let error = cleaned(transcript.error)
        if !error.isEmpty {
            values.append(
                TranscriptBlock(id: "agent-error", kind: .error, text: error, status: nil)
            )
        }
        let artifactError = cleaned(transcript.artifactError)
        if !artifactError.isEmpty, artifactError != error {
            values.append(
                TranscriptBlock(
                    id: "artifact-error",
                    kind: .error,
                    text: artifactError,
                    status: nil
                )
            )
        }
        if let runID = transcript.sourceRunID, let agent = transcript.sourceAgent {
            for index in values.indices.dropFirst(currentTurnStart) {
                values[index].linkOrigin = TranscriptLinkOrigin(runID: runID, agent: agent)
            }
        }
        var seen: Set<String> = []
        return values.enumerated().map { index, block in
            guard !seen.insert(block.id).inserted else { return block }
            return TranscriptBlock(
                id: "\(block.id)-\(index)",
                kind: block.kind,
                text: block.text,
                status: block.status,
                operationId: block.operationId,
                activityType: block.activityType,
                isHistorical: block.isHistorical,
                isFinalAnswer: block.isFinalAnswer,
                linkOrigin: block.linkOrigin,
                image: block.image,
                question: block.question
            )
        }
    }

    static func pendingTurnBlocks(
        history: [ResumeHistoryEntry],
        prompt: String,
        status: String?
    ) -> [TranscriptBlock] {
        var values = blocks(
            history: history,
            prompt: "",
            transcript: AgentTranscript()
        )
        let input = cleaned(prompt)
        if !input.isEmpty {
            values.append(
                TranscriptBlock(
                    id: "current-prompt",
                    kind: .user,
                    text: input,
                    status: status
                )
            )
        }
        return values
    }

    static func append(events: [RunEvent], to blocks: inout [TranscriptBlock]) {
        for event in events {
            guard let block = block(for: event) else { continue }
            append(block, to: &blocks)
        }
    }

    static func append(items: [RemoteTranscriptItem], to blocks: inout [TranscriptBlock]) {
        for item in items {
            guard let kind = TranscriptKind(rawValue: item.kind) else { continue }
            append(
                TranscriptBlock(
                    id: item.id,
                    kind: kind,
                    text: cleaned(item.text),
                    status: commandStatusTitle(item.status),
                    operationId: item.operationId,
                    activityType: item.activityType,
                    operation: item.operation,
                    isFinalAnswer: item.activityType == "assistant_final"
                ),
                to: &blocks
            )
        }
    }

    private struct BlockIndex {
        var ids: [String: Int] = [:]
        var operations: [String: Int] = [:]

        init(_ blocks: [TranscriptBlock]) {
            for (offset, block) in blocks.enumerated() { record(block, at: offset) }
        }

        mutating func record(_ block: TranscriptBlock, at offset: Int) {
            if ids[block.id] == nil { ids[block.id] = offset }
            if let operation = block.operation, operations[operation.id] == nil {
                operations[operation.id] = offset
            }
        }
    }

    private static func append(_ block: TranscriptBlock, to values: inout [TranscriptBlock]) {
        var index: BlockIndex?
        append(block, to: &values, index: &index)
    }

    private static func append(_ block: TranscriptBlock, to values: inout [TranscriptBlock],
                               index: inout BlockIndex?) {
        guard !block.text.isEmpty else { return }
        if let operation = block.operation {
            let existing = index != nil ? index!.operations[operation.id]
                : values.firstIndex(where: { $0.operation?.id == operation.id })
            if let offset = existing {
                guard let previous = values[offset].operation,
                      operation.eventId > previous.eventId,
                      !(previous.status != .running && previous.status != .unknown && operation.status == .running)
                else { return }
                let replacement = TranscriptBlock(
                    id: values[offset].id, kind: block.kind, text: block.text,
                    status: block.status, operationId: block.operationId,
                    activityType: block.activityType, operation: operation,
                    isHistorical: block.isHistorical, isFinalAnswer: block.isFinalAnswer
                )
                values[offset] = replacement
            } else {
                index?.record(block, at: values.count)
                values.append(block)
            }
            return
        }
        let existing = index != nil ? index!.ids[block.id] : values.firstIndex(where: { $0.id == block.id })
        if let offset = existing {
            if let oldOperation = values[offset].operation, index?.operations[oldOperation.id] == offset {
                index?.operations.removeValue(forKey: oldOperation.id)
            }
            values[offset] = block
            index?.record(block, at: offset)
            return
        }
        if block.kind == .command || block.kind == .activity,
           let operationID = block.operationId,
           let offset = values.lastIndex(where: {
               $0.kind == block.kind
                   && $0.status == "Running"
                   && $0.operationId == operationID
           }) {
            values[offset] = TranscriptBlock(
                id: values[offset].id,
                kind: block.kind,
                text: block.text,
                status: block.status,
                operationId: block.operationId,
                activityType: block.activityType,
                isHistorical: block.isHistorical,
                isFinalAnswer: block.isFinalAnswer
            )
            return
        }
        if block.kind != .command,
           block.kind != .user,
           block.kind != .output,
           block.kind != .warning,
           block.kind != .error,
           block.text.utf8.count < 128 * 1024,
           let last = values.last,
           last.kind == block.kind,
           last.status == block.status,
           equivalent(last.text, block.text) {
            return
        }
        index?.record(block, at: values.count)
        values.append(block)
    }

    private static func commandStatusTitle(_ value: String?) -> String? {
        switch value?.lowercased() {
        case "running": return "Running"
        case "completed": return "Completed"
        case "failed": return "Failed"
        case "cancelled", "canceled": return "Cancelled"
        case .some(let value): return value.replacingOccurrences(of: "_", with: " ").capitalized
        case nil: return nil
        }
    }

    private static func block(for event: RunEvent) -> TranscriptBlock? {
        guard event.type == "agent_line" else { return nil }
        let raw = cleaned(event.text ?? event.message ?? "")
        guard !raw.isEmpty, !hiddenRuntimeDiagnostic(raw) else { return nil }

        if let data = raw.data(using: .utf8),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if payload["type"] as? String == "pcr.input_request",
               let requestID = payload["request_id"] as? String {
                return TranscriptBlock(id: "event-\(event.eventId)", kind: .activity,
                    text: requestID, activityType: "agent_question_source")
            }
            if eventItem(in: payload)?["type"] as? String == "imageGeneration" {
                return TranscriptBlock(id: "event-\(event.eventId)", kind: .activity, text: raw)
            }
            if let item = eventItem(in: payload), item["type"] as? String == "sleep" {
                let ended = (payload["type"] as? String) == "item.completed"
                let identifier = item["id"] as? String
                let duration = (item["durationMs"] as? Int).flatMap { $0 >= 0 ? $0 : nil }
                let until = !ended ? event.timestamp.flatMap(parseInternetDate).flatMap { start in
                    duration.map { start.addingTimeInterval(Double($0) / 1_000).ISO8601Format() }
                } : nil
                return TranscriptBlock(id: "event-\(event.eventId)", kind: .activity,
                    text: ended ? "Wait ended" : "Waiting", status: ended ? "Completed" : "Running",
                    operationId: identifier, activityType: "sleep",
                    operation: identifier.map { WorkOperationRecord(id: "sleep:\(event.idx ?? 0):\($0)",
                        eventId: event.eventId, kind: .unknown, status: ended ? .completed : .running,
                        command: "sleep", plannedDurationMs: duration, waitUntil: until, detailsAvailable: false) })
            }
            if let item = commandItem(in: payload) {
                let command = commandText(item["command"])
                guard !command.isEmpty else { return nil }
                return TranscriptBlock(
                    id: "event-\(event.eventId)",
                    kind: .command,
                    text: stripShellWrapper(command),
                    status: commandStatus(item)
                )
            }

            if let item = eventItem(in: payload),
               item["type"] as? String == "error",
               let message = item["message"] as? String {
                let text = cleaned(message)
                guard !text.isEmpty else { return nil }
                return TranscriptBlock(
                    id: "event-\(event.eventId)",
                    kind: isRecoverableWarning(text) ? .warning : .error,
                    text: text,
                    status: nil
                )
            }

            let kind = payloadKind(payload)
            let phase = eventItem(in: payload)?["phase"] as? String
            let text = cleaned(firstText(in: payload))
            guard !text.isEmpty else { return nil }
            return TranscriptBlock(
                id: "event-\(event.eventId)",
                kind: kind,
                text: stripAgentPrefix(text),
                status: nil,
                activityType: kind == .output && phase == "commentary" ? "assistant_progress" : nil,
                isFinalAnswer: kind == .output && phase == "final_answer"
            )
        }

        if let diagnostic = runtimeDiagnosticBlock(raw, eventID: event.eventId) {
            return diagnostic
        }

        let lowered = raw.lowercased()
        let kind: TranscriptKind
        if lowered.hasPrefix("agent_reasoning:") {
            kind = .thought
        } else if lowered.hasPrefix("agent_message:") {
            kind = .output
        } else {
            kind = .activity
        }
        return TranscriptBlock(
            id: "event-\(event.eventId)",
            kind: kind,
            text: stripAgentPrefix(raw),
            status: nil
        )
    }

    private static func runtimeDiagnosticBlock(
        _ value: String,
        eventID: Int
    ) -> TranscriptBlock? {
        guard let runtimeLogExpression else { return nil }
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        guard let match = runtimeLogExpression.firstMatch(in: value, range: range),
              let levelRange = Range(match.range(at: 1), in: value),
              let messageRange = Range(match.range(at: 3), in: value) else {
            return nil
        }

        let level = String(value[levelRange]).uppercased()
        var message = cleaned(String(value[messageRange]))
        if message.lowercased().hasPrefix("error=") {
            message = cleaned(String(message.dropFirst("error=".count)))
        }
        guard !message.isEmpty else { return nil }

        let normalized = message.lowercased()
        if normalized.contains("createprocess")
            && normalized.contains("no such file or directory") {
            return TranscriptBlock(
                id: "event-\(eventID)",
                kind: .warning,
                text: "A command could not start because a required path no longer exists.",
                activityType: "missing_execution_path"
            )
        }

        return TranscriptBlock(
            id: "event-\(eventID)",
            kind: level.hasPrefix("WARN") ? .warning : .error,
            text: message,
            activityType: "runtime_diagnostic"
        )
    }

    private static func hiddenRuntimeDiagnostic(_ value: String) -> Bool {
        let normalized = value.lowercased()
        return normalized.contains("codex_models_manager") &&
            normalized.contains("failed to refresh available models")
    }

    private static func isRecoverableWarning(_ value: String) -> Bool {
        let normalized = value
            .lowercased()
            .split(whereSeparator: \Character.isWhitespace)
            .joined(separator: " ")
        let modelResume = normalized.hasPrefix("this session was recorded with model")
            && normalized.contains("but is resuming with")
            && normalized.contains("consider switching back to")
        let transportRecovery = normalized.hasPrefix(
            "falling back from websockets to https transport"
        ) || normalized.hasPrefix("reconnecting...")
        return modelResume || transportRecovery
    }

    private static func historyKind(_ category: String) -> TranscriptKind {
        let value = category.lowercased()
        if value.contains("user") || value.contains("input") { return .user }
        if value.contains("reason") || value.contains("thought") { return .thought }
        if value.contains("command") || value.contains("tool") { return .activity }
        return .output
    }

    private static func payloadKind(_ payload: [String: Any]) -> TranscriptKind {
        var signals: [String] = []
        collectSignals(payload, into: &signals)
        let value = signals.joined(separator: " ").lowercased()
        if value.contains("reasoning") || value.contains("thinking") { return .thought }
        if value.contains("agent_message") || value.contains("assistant") { return .output }
        if value.contains("message") && !value.contains("user") { return .output }
        return .activity
    }

    private static func collectSignals(_ value: Any, into signals: inout [String]) {
        guard let object = value as? [String: Any] else { return }
        for key in ["type", "event", "role"] {
            if let text = object[key] as? String { signals.append(text) }
        }
        for key in ["payload", "item"] {
            if let nested = object[key] { collectSignals(nested, into: &signals) }
        }
    }

    private static func commandItem(in payload: [String: Any]) -> [String: Any]? {
        if let item = payload["item"] as? [String: Any],
           item["type"] as? String == "command_execution" {
            return item
        }
        if let nested = payload["payload"] as? [String: Any] {
            return commandItem(in: nested)
        }
        return nil
    }

    private static func eventItem(in payload: [String: Any]) -> [String: Any]? {
        if let item = payload["item"] as? [String: Any] { return item }
        if let nested = payload["payload"] as? [String: Any] {
            return eventItem(in: nested)
        }
        return nil
    }

    private static func firstText(in value: Any) -> String {
        if let text = value as? String { return text }
        if let values = value as? [Any] {
            return values.map(firstText).filter { !$0.isEmpty }.joined(separator: "\n")
        }
        guard let object = value as? [String: Any] else { return "" }
        for key in ["message", "text", "content", "delta", "summary", "reasoning"] {
            let text = firstText(in: object[key] as Any)
            if !text.isEmpty { return text }
        }
        for key in ["item", "payload"] {
            let text = firstText(in: object[key] as Any)
            if !text.isEmpty { return text }
        }
        return ""
    }

    private static func commandText(_ value: Any?) -> String {
        if let text = value as? String { return cleaned(text) }
        if let values = value as? [Any] {
            return values.compactMap { $0 as? String }.joined(separator: " ")
        }
        return ""
    }

    private static func commandStatus(_ item: [String: Any]) -> String? {
        if let exitCode = item["exit_code"] {
            let value = String(describing: exitCode)
            return value == "0" ? "Completed" : "Failed (exit \(value))"
        }
        let raw = (item["status"] as? String ?? "").lowercased()
        switch raw {
        case "completed", "complete", "success", "succeeded": return "Completed"
        case "failed", "failure", "error": return "Failed"
        case "cancelled", "canceled": return "Cancelled"
        case "in_progress", "running", "started", "": return "Running"
        default: return raw.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private static func stripAgentPrefix(_ value: String) -> String {
        let lowered = value.lowercased()
        for prefix in ["agent_reasoning:", "agent_message:"] where lowered.hasPrefix(prefix) {
            return cleaned(String(value.dropFirst(prefix.count)))
        }
        return value
    }

    static func stripShellWrapper(_ command: String) -> String {
        let patterns = ["/bin/zsh -lc ", "zsh -lc ", "/bin/bash -lc ", "bash -lc "]
        for prefix in patterns where command.hasPrefix(prefix) {
            var value = String(command.dropFirst(prefix.count))
            if value.count >= 2,
               let first = value.first,
               first == value.last,
               first == "\"" || first == "'" {
                value.removeFirst()
                value.removeLast()
            }
            return value
        }
        return command
    }

    private static func cleaned(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func equivalent(_ lhs: String, _ rhs: String) -> Bool {
        if lhs == rhs { return true }
        var left = lhs.startIndex
        var right = rhs.startIndex
        // Compare lazily: different work items usually differ immediately.
        // Do not allocate two normalized copies of multi-megabyte tool output.
        while true {
            while left < lhs.endIndex, lhs[left].isWhitespace { lhs.formIndex(after: &left) }
            while right < rhs.endIndex, rhs[right].isWhitespace { rhs.formIndex(after: &right) }
            if left == lhs.endIndex || right == rhs.endIndex {
                return left == lhs.endIndex && right == rhs.endIndex
            }
            while left < lhs.endIndex, right < rhs.endIndex,
                  !lhs[left].isWhitespace, !rhs[right].isWhitespace {
                guard lhs[left] == rhs[right] else { return false }
                lhs.formIndex(after: &left)
                rhs.formIndex(after: &right)
            }
            let leftEnded = left == lhs.endIndex || lhs[left].isWhitespace
            let rightEnded = right == rhs.endIndex || rhs[right].isWhitespace
            guard leftEnded && rightEnded else { return false }
        }
    }

    private static func equivalentAssistantPresentation(_ lhs: String, _ rhs: String) -> Bool {
        equivalent(
            markdownVisibleFingerprint(assistantPresentationText(lhs)),
            markdownVisibleFingerprint(assistantPresentationText(rhs))
        )
    }

    private static func markdownVisibleFingerprint(_ text: String) -> String {
        // Markdown renders the label, not the destination. A streamed answer
        // can point at an Agent workspace while its final artifact points at
        // the sync-backed workspace; those are one visible answer.
        text.replacingOccurrences(
            of: #"(!?\[[^\]\n]*\])\((?:<[^>\n]*>|(?:\\.|[^)\n])*)\)"#,
            with: "$1",
            options: .regularExpression
        )
    }
}
