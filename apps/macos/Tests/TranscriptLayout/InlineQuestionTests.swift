import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct InlineQuestionTests {
    private func request(_ id: String = "request", answered: Bool = false, at: Double = 1, turn: String = "turn") -> AgentInputRequest {
        .init(id: id, connectionId: "async:thread", threadId: "thread", turnId: turn, itemId: "call_\(id)",
            isBlocking: false, questions: [.init(id: "choice", header: "Question 1",
                question: "历史模型是否可以作为现成基线复用？训练和独立探索会继续进行，不等待这个回答。",
                options: [.init(label: "可以复用本赛道历史模型，不看其他并行 Agent 的新方案", description: ""),
                    .init(label: "历史模型也不复用，保持完全独立", description: "")], isOther: true, isSecret: false)],
            state: answered ? "resolved" : "open", delivery: answered ? "sent" : "none",
            answerId: answered ? "answer-\(id)" : nil,
            answers: answered ? ["choice": .init(answers: ["可以复用本赛道历史模型，不看其他并行 Agent 的新方案"])] : nil,
            createdAt: at, updatedAt: answered ? at + 1 : at, kind: "async")
    }

    private func presentation(_ request: AgentInputRequest, expanded: Bool = false) -> AgentQuestionPresentation {
        .init(runID: "run", agent: 2, request: request, command: nil, expanded: expanded)
    }

    private func source(_ request: AgentInputRequest) -> TranscriptBlock {
        .init(id: "text-original-owner:\(request.itemId)", kind: .output,
            text: request.questions.map { $0.question + "\n" + ($0.options ?? []).map { "- " + $0.label }.joined(separator: "\n") }.joined(separator: "\n\n"),
            activityType: "assistant_progress", linkOrigin: .init(runID: "run", agent: 2))
    }

    @Test func nativeIdentityKeepsTheQuestionBetweenItsSurroundingReplies() throws {
        let q = request(), p = presentation(q)
        let before = TranscriptBlock(id: "before", kind: .output, text: "Before", activityType: "assistant_progress")
        let after = TranscriptBlock(id: "after", kind: .output, text: "After", activityType: "assistant_progress")
        let original = [before, source(q), source(q), after]
        let projected = TranscriptFormatter.coalescedVisibleConversation(AgentQuestionTranscript.project(original, questions: [p]))
        #expect(projected.map(\.id) == ["before", p.id, "after"])
        #expect(projected[1].question == p)
        #expect(original[1].question == nil)
        let received = presentation(request(answered: true))
        let updated = AgentQuestionTranscript.project(projected, questions: [received])
        #expect(updated.map(\.id) == projected.map(\.id))
        #expect(updated[1].question?.values["choice"] == received.values["choice"])
        #expect(updated[1].question?.delivery == .received)
    }

    @Test func identicalQuestionWordsNeverMergeDifferentNativeRequestsOrReinterpretOrdinaryProse() {
        let first = request("first"), second = request("second", at: 2)
        let ordinary = TranscriptBlock(id: "ordinary", kind: .output, text: source(first).text)
        let values = AgentQuestionTranscript.project([ordinary, source(first), source(second)],
            questions: [presentation(first), presentation(second)])
        #expect(values.count == 3 && values[0] == ordinary)
        #expect(values.compactMap(\.question).map(\.request.id) == ["first", "second"])
    }

    @Test func aRequestWithoutATextProjectionPinsItsFirstObservedPositionAndSurvivesCacheReload() throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 2,
            blocks: [.init(id: "before", kind: .output, text: "Before")])
        transcript.mergeInputRequests([request()])
        transcript.blocks.append(.init(id: "after", kind: .output, text: "After"))
        transcript.mergeInputRequests([request()])
        #expect(transcript.questionAnchors?["request"] == "before")
        let restored = try JSONDecoder().decode(AgentTranscript.self, from: JSONEncoder().encode(transcript))
        var p = presentation(request())
        p.anchorAfterID = restored.questionAnchors?["request"]
        let projected = AgentQuestionTranscript.project(restored.blocks, questions: [p])
        #expect(projected.map(\.id) == ["before", p.id, "after"])
        #expect(agentTranscriptBoundToBranch(restored, runID: "other", agent: 2).inputRequests == nil)
        #expect(agentTranscriptBoundToBranch(restored, runID: "run", agent: 3).questionAnchors == nil)
    }

    @Test func carriedHistoryKeepsAnAnsweredQuestionAndItsOriginWithoutBecomingAnEditableNewPrompt() throws {
        let original = request(answered: true)
        let transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 2,
            blocks: [source(original)], inputRequests: [original])
        let history = branchConversationHistory(history: [], prompt: "Previous", transcript: transcript)
        let saved = try JSONDecoder().decode([ResumeHistoryEntry].self, from: JSONEncoder().encode(history))
        let restored = TranscriptFormatter.blocks(history: saved, prompt: "New", transcript: .init())
        let question = try #require(restored.first { $0.question != nil })
        #expect(question.isHistorical)
        #expect(question.linkOrigin == .init(runID: "run", agent: 2))
        #expect(question.question?.editable == false)
        #expect(question.question?.request.answerId == original.answerId)
        #expect(restored.last?.id == "current-prompt")
    }

    @Test func authoritativeHistoryKeepsKnownQuestionsInsideTheirOriginalNativeTurn() throws {
        let original = request(answered: true), p = presentation(original)
        let current = [ResumeHistoryEntry(category: "output", text: p.searchableText, id: p.id, question: p)]
        let text = "Before\n\n" + source(original).text + "\n\nAfter"
        let authoritative = [ResumeHistoryEntry(category: "user", text: "Previous", id: "history:thread:1", turnId: "turn"),
            ResumeHistoryEntry(category: "output", text: text, id: "history:thread:2", turnId: "turn")]
        let merged = mergeConversationHistory(current: current, authoritative: authoritative)
        #expect(merged.map(\.id) == authoritative.map(\.id))
        #expect(merged.map(\.text) == authoritative.map(\.text))
        let saved = try JSONDecoder().decode([ResumeHistoryEntry].self, from: JSONEncoder().encode(merged))
        let refreshed = mergeConversationHistory(current: saved, authoritative: authoritative)
        let blocks = TranscriptFormatter.blocks(history: refreshed, prompt: "Next", transcript: .init())
        let q = try #require(blocks.first { $0.question != nil })
        #expect(q.id == p.id && q.isHistorical)
        #expect(q.question?.editable == false)
        #expect(q.question?.request.answerId == original.answerId)
        #expect(blocks.map(\.text).contains("Before") && blocks.map(\.text).contains("After"))
        #expect(blocks.firstIndex { $0.text == "Before" }! < blocks.firstIndex { $0.id == p.id }!)
        #expect(blocks.firstIndex { $0.id == p.id }! < blocks.firstIndex { $0.text == "After" }!)
    }

    @Test func historyNeverUsesRepeatedWordsToGuessAQuestionIdentityAcrossOrWithinTurns() {
        let first = request("first", answered: true), second = request("second", answered: true)
        let current = [presentation(first), presentation(second)].map {
            ResumeHistoryEntry(category: "output", text: $0.searchableText, id: $0.id, question: $0)
        }
        let original = source(first).text
        for (turn, text) in [("other-turn", original), ("turn", original), ("turn", original + "\n\n" + original)] {
            let authoritative = [ResumeHistoryEntry(category: "user", text: "Previous", id: "user", turnId: turn),
                ResumeHistoryEntry(category: "output", text: text, id: "output", turnId: turn)]
            let merged = mergeConversationHistory(current: current, authoritative: authoritative)
            #expect(merged.map(\.text) == authoritative.map(\.text))
            #expect(merged.allSatisfy { $0.questions == nil })
            let blocks = TranscriptFormatter.blocks(history: merged, prompt: "Next", transcript: .init())
            #expect(blocks.allSatisfy { $0.question == nil })
        }
        let earlier = presentation(request("earlier", answered: true, turn: "earlier-turn"))
        let earlierEntry = ResumeHistoryEntry(category: "output", text: earlier.searchableText,
            id: earlier.id, question: earlier)
        let history = mergeConversationHistory(current: [current[0], earlierEntry], authoritative: [
            .init(category: "output", text: original, id: "old", turnId: "earlier-turn"),
            .init(category: "output", text: original, id: "new", turnId: "turn")])
        let blocks = TranscriptFormatter.blocks(history: history, prompt: "", transcript: .init())
        #expect(blocks.compactMap(\.question).map(\.request.id) == ["earlier", "first"])
    }

    @Test func onlyAConfirmedNativeReceiptIsLabeledReceivedAndSecretAnswersAreNotSearchable() {
        var q = request(answered: true)
        q = .init(id: q.id, connectionId: q.connectionId, threadId: q.threadId, turnId: q.turnId,
            itemId: q.itemId, isBlocking: false, questions: [.init(id: "choice", header: "Secret",
                question: "Secret choice", options: nil, isOther: true, isSecret: true)],
            state: "open", delivery: "queued", answerId: "answer", answers: ["choice": .init(answers: ["sensitive-fixture"])],
            createdAt: 1, updatedAt: 2, kind: "async")
        let queued = presentation(q)
        #expect(queued.delivery == .queued)
        #expect(queued.receiptLabel(language: .chinese).contains("尚未接收"))
        #expect(!queued.searchableText.contains("sensitive-fixture"))
        #expect(queued.searchableText.contains("********"))
    }

    @Test(arguments: [420.0, 980.0], [AppTextSize.standard, .extraLarge])
    func nativeQuestionUsesTheReplyColumnAndGrowsToFitRatherThanCreatingANestedViewport(width: Double, size: AppTextSize) async throws {
        let question = presentation(request())
        let height = AgentQuestionCollectionItem.preferredHeight(question: question, language: .chinese,
            textSize: size, accentColor: .orange, appearance: .dark, availableWidth: width)
        #expect(height > 80)
        let item = AgentQuestionCollectionItem()
        item.configure(question: question, language: .chinese, textSize: size, accentColor: .orange,
            appearance: .dark, availableWidth: width, actions: .init(), heightChanged: { _ in })
        item.view.frame = NSRect(x: 0, y: 0, width: width, height: height)
        let window = NSWindow(contentRect: item.view.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = item.view
        defer { window.contentView = nil; window.close() }
        for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
        let host = try #require(item.view.subviews.first { !($0 is NSImageView) })
        #expect(abs(host.frame.minX + 24 - 40) < 0.5)
        #expect(abs(host.frame.width - AgentQuestionCollectionItem.contentWidth(width) - 24) < 0.5)
        #expect(host.frame.height <= height + 1)
        #expect(host.frame.height > 80)
        let radios = descendants(host).compactMap { $0 as? NSButton }
        #expect(radios.count == 3)
        for radio in radios {
            let rect = radio.convert(radio.bounds, to: item.view)
            #expect(rect.minX >= 0 && rect.maxX <= width && rect.maxY <= height + 1)
            #expect(host.bounds.contains(radio.convert(radio.bounds, to: host)),
                "Native radio buttons must remain inside their hosting view's hit-test bounds.")
        }
        #expect(!window.isVisible && !window.isKeyWindow)
        if let folder = ProcessInfo.processInfo.environment["PCR_INLINE_QUESTION_EVIDENCE"] {
            item.view.displayIfNeeded()
            let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
            item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            #expect(png.count > 4000)
            try png.write(to: URL(fileURLWithPath: folder).appendingPathComponent("question-\(Int(width))-\(size.rawValue).png"))
        }
    }

    @Test func theProductionTimelineUpdatesTheSameQuestionCellAndScrollsItWithTheDialogue() async throws {
        let suite = "InlineQuestionTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let memory = TranscriptScrollMemory(defaults: defaults)
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700))
        let window = NSWindow(contentRect: scroll.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = scroll
        var question = presentation(request())
        func parent() -> ConversationTimelineView {
            let block = TranscriptBlock(id: question.id, kind: .output, text: question.searchableText,
                activityType: "agent_question", linkOrigin: .init(runID: "run", agent: 2), question: question)
            return .init(language: .chinese, textSize: .standard, accentColor: .orange, appearance: .dark,
                contextID: "inline-question", items: [
                    .message(.init(id: "before", kind: .output, text: "我会继续核对现有结果，同时确认这个问题。", activityType: "assistant_progress")),
                    .message(block),
                    .message(.init(id: "after", kind: .output, text: "训练和独立探索继续进行，不等待这个回答。", activityType: "assistant_progress"))],
                contentIsLoading: false, jumpToLatestRequest: 0, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
        }
        let coordinator = ConversationTimelineView.Coordinator(parent: parent())
        let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
        collection.frame = scroll.bounds
        scroll.documentView = collection
        coordinator.attach(scrollView: scroll, collectionView: collection)
        defer { coordinator.detach(); window.contentView = nil; window.close() }
        coordinator.update(parent: parent())
        for _ in 0..<6 { collection.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let pending = try #require(collection.item(at: IndexPath(item: 1, section: 0)) as? AgentQuestionCollectionItem)
        let before = try #require(collection.item(at: IndexPath(item: 0, section: 0)))
        let body = try #require(before.view.subviews.first { String(describing: type(of: $0)) == "ActionableTranscriptBodyView" })
        let host = try #require(pending.view.subviews.first { !($0 is NSImageView) })
        #expect(abs(host.frame.minX + 24 - body.frame.minX) < 0.5)
        #expect(!descendants(pending.view).contains { $0 is NSScrollView })
        question = presentation(request(answered: true))
        coordinator.update(parent: parent())
        for _ in 0..<6 { collection.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let answered = try #require(collection.item(at: IndexPath(item: 1, section: 0)) as? AgentQuestionCollectionItem)
        #expect(answered === pending, "A receipt change must not replace the native question/input tree.")
        let next = try #require(collection.item(at: IndexPath(item: 2, section: 0)))
        #expect(answered.view.frame.maxY <= next.view.frame.minY)
        #expect(!window.isVisible && !window.isKeyWindow)
        if let folder = ProcessInfo.processInfo.environment["PCR_INLINE_QUESTION_EVIDENCE"] {
            scroll.displayIfNeeded()
            let bitmap = try #require(scroll.bitmapImageRepForCachingDisplay(in: scroll.bounds))
            scroll.cacheDisplay(in: scroll.bounds, to: bitmap)
            try #require(bitmap.representation(using: .png, properties: [:])).write(to:
                URL(fileURLWithPath: folder).appendingPathComponent("answered-question-in-dialogue.png"))
        }
    }

    private func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }

    @Test func streamingInsertionBeforeAQuestionPreservesTheNativeEditorAndMarkedText() async throws {
        let suite = "QuestionInput.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let memory = TranscriptScrollMemory(defaults: defaults)
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 900, height: 700))
        let window = NSWindow(contentRect: scroll.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = scroll
        let base = request()
        let free = AgentInputRequest(id: base.id, connectionId: base.connectionId, threadId: base.threadId,
            turnId: base.turnId, itemId: base.itemId, isBlocking: false,
            questions: [.init(id: "choice", header: "Question 1", question: "请补充你的看法。",
                options: nil, isOther: true, isSecret: false)], state: "open", delivery: "none",
            answerId: nil, answers: nil, createdAt: 1, updatedAt: 1, kind: "async")
        let question = presentation(free)
        var prefix: [ConversationTimelineItem] = [
            .message(.init(id: "before", kind: .output, text: "Existing output", activityType: "assistant_progress"))]
        var questionFirst = false
        func parent() -> ConversationTimelineView {
            let row: ConversationTimelineItem = .message(.init(id: question.id, kind: .output,
                text: question.searchableText, activityType: "agent_question", question: question))
            return .init(language: .chinese, textSize: .standard, accentColor: .orange, appearance: .dark,
                contextID: "question-input", items: questionFirst ? [row] + prefix : prefix + [row],
                contentIsLoading: false, jumpToLatestRequest: 0, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
        }
        let coordinator = ConversationTimelineView.Coordinator(parent: parent())
        let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
        collection.frame = scroll.bounds
        scroll.documentView = collection
        coordinator.attach(scrollView: scroll, collectionView: collection)
        defer { coordinator.detach(); window.contentView = nil; window.close() }
        coordinator.update(parent: parent())
        for _ in 0..<6 { collection.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let cell = try #require(collection.item(at: IndexPath(item: 1, section: 0)) as? AgentQuestionCollectionItem)
        let field = try #require(descendants(cell.view).compactMap { $0 as? NSTextField }.first { $0.isEditable })
        #expect(window.makeFirstResponder(field))
        let editor = try #require(field.currentEditor() as? NSTextView)
        editor.setMarkedText("尚未完成", selectedRange: NSRange(location: 4, length: 0), replacementRange: editor.selectedRange())
        #expect(editor.hasMarkedText())
        prefix.append(.message(.init(id: "new-output", kind: .output, text: "A delayed native output page",
            activityType: "assistant_progress")))
        coordinator.update(parent: parent())
        for _ in 0..<6 { collection.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let refreshed = try #require(collection.item(at: IndexPath(item: 2, section: 0)) as? AgentQuestionCollectionItem)
        #expect(refreshed === cell)
        #expect(field.currentEditor() === editor)
        #expect(window.firstResponder === editor)
        #expect(editor.string == "尚未完成" && editor.hasMarkedText())
        // The native source can replace a provisional anchor after a page arrives.
        questionFirst = true
        coordinator.update(parent: parent())
        for _ in 0..<6 { collection.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        #expect(collection.item(at: IndexPath(item: 0, section: 0)) === cell)
        #expect(field.currentEditor() === editor && window.firstResponder === editor)
        #expect(editor.string == "尚未完成" && editor.hasMarkedText())
        #expect(!window.isVisible && !window.isKeyWindow)
    }
}
