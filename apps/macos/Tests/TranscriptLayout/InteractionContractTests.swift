import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct InteractionContractTests {
    @Test
    func finalAnswerDoesNotMovePreviouslyVisibleUnknownPhaseMessages() {
        let unknown = TranscriptBlock(id: "legacy", kind: .output, text: "An earlier visible message")
        let progress = TranscriptBlock(id: "progress", kind: .output, text: "Working", activityType: "assistant_progress")
        let final = TranscriptBlock(id: "final", kind: .output, text: "Answer", isFinalAnswer: true)
        let before = TranscriptFormatter.blocksWithWorkDisclosure([unknown, progress], runID: "r", agent: 1, expanded: false)
        let after = TranscriptFormatter.blocksWithWorkDisclosure([unknown, progress, final], runID: "r", agent: 1, expanded: false)
        #expect(Array(after.prefix(before.count)) == before)
        #expect(after.last == final)
    }

    @Test
    func selectionsDecodeFromOldStateAndRoundTripPerConversation() throws {
        #expect(try JSONDecoder().decode(PersistedDesktopState.self, from: Data("{}".utf8)).promptSelectionsByContext.isEmpty)
        var state = PersistedDesktopState()
        state.promptSelectionsByContext["a"] = .init(location: 4, length: 3)
        state.promptSelectionsByContext["b"] = .init(location: 10, length: 0)
        let decoded = try JSONDecoder().decode(PersistedDesktopState.self, from: JSONEncoder().encode(state))
        #expect(decoded.promptSelectionsByContext == state.promptSelectionsByContext)
        #expect(decoded.promptSelectionsByContext["b"]?.range(in: "短文") == NSRange(location: 2, length: 0))
    }

    @Test
    func restoredEditorKeepsSelectionOnInitialFocus() async throws {
        let window = makeWindow()
        defer { window.close() }
        let host = NSHostingView(rootView: NativeMultilineEditor(text: .constant("First line\nSecond line"),
            measuredHeight: .constant(100), minimumHeight: 60, maximumHeight: 180,
            isInitialFirstResponder: true, onSubmit: nil, initialSelection: .init(location: 3, length: 4)))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(40))
        let text = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(text.selectedRange() == NSRange(location: 3, length: 4))
    }

    @Test
    func composerContextSwitchPreservesDraftSelection() async throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "draft-qa"
        model.selectedConversationID = "one"
        model.preparePromptHistoryContext()
        model.updatePrompt("draft ABC cursor XYZ")
        let context = model.promptDraftContext
        let window = makeWindow()
        defer { window.close() }
        let host = NSHostingView(rootView: PromptComposer().environmentObject(model))
        window.contentView = host
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(80))
        let text = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        text.setSelectedRange(NSRange(location: 6, length: 3))
        model.suspendPromptHistoryContext()
        model.selectedConversationID = "two"
        model.preparePromptHistoryContext()
        try await Task.sleep(for: .milliseconds(80))
        #expect(model.promptSelectionsByContext[context] == .init(location: 6, length: 3))
        model.suspendPromptHistoryContext()
        model.selectedConversationID = "one"
        model.preparePromptHistoryContext()
        try await Task.sleep(for: .milliseconds(80))
        let restored = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(restored.string == "draft ABC cursor XYZ")
        #expect(restored.selectedRange() == NSRange(location: 6, length: 3))
        #expect(model.promptSelectionsByContext[context] == .init(location: 6, length: 3))
    }

    @Test
    func tallParagraphKeepsItsCharacterAnchorAfterReflow() throws {
        let item = TranscriptCollectionItem()
        let window = makeWindow()
        defer { window.close() }
        window.contentView?.addSubview(item.view)
        let block = TranscriptBlock(id: "long", kind: .output,
            text: (1...200).map { "Sentence \($0) includes some words for wrapping." }.joined(separator: " "))
        func layout(_ width: CGFloat) {
            item.configure(block: block, language: .english, textSize: .standard, accentColor: .orange,
                appearance: .dark, findQuery: "", currentFindOccurrence: nil, availableWidth: width,
                openURL: { _ in }, activateBlock: { _ in })
            item.view.frame = NSRect(x: 0, y: 0, width: width, height:
                TranscriptCollectionItem.preferredHeight(for: block, language: .english, textSize: .standard,
                    accentColor: .orange, appearance: .dark, availableWidth: width))
            item.view.layoutSubtreeIfNeeded()
        }
        layout(750)
        let anchor = try #require(item.readingAnchor(at: 450))
        let originalOffset = try #require(item.readingOffset(for: anchor))
        layout(390)
        let nextOffset = try #require(item.readingOffset(for: anchor))
        #expect(nextOffset > originalOffset + 100)
        let next = try #require(item.readingAnchor(at: nextOffset))
        #expect(abs(next.character - anchor.character) < 70)
    }

    @Test
    func textAnchorsPersistWithoutSavingMessageContent() throws {
        let name = "InteractionQA.\(UUID())"
        let defaults = try #require(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let memory = TranscriptScrollMemory(defaults: defaults)
        memory.save(.init(offsetY: 100, followsLatest: false, anchorID: "message:a", anchorOffset: 50,
                          textAnchor: .init(character: 90, lineOffset: 4)), for: "context")
        memory.flush()
        let restored = TranscriptScrollMemory(defaults: defaults).snapshot(for: "context")
        #expect(restored?.textAnchor == .init(character: 90, lineOffset: 4))
    }

    @Test
    func fileFailureRetainsOpenDocumentAndDoesNotBecomeConversationError() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        let document = WorkspaceFileDocument(name: "existing.md", content: "Keep this", fileURL: URL(fileURLWithPath: "/qa/existing.md"), relativePath: "existing.md", line: nil)
        model.workspaceDocument = document
        model.openTranscriptLink(try #require(URL(string: "javascript:alert(1)")))
        #expect(model.workspaceFileIssue != nil)
        #expect(model.workspaceDocument?.id == document.id)
        #expect(model.errorMessage == nil)
        model.dismissWorkspaceDocument()
        #expect(model.workspaceFileIssue == nil)
    }

    @Test
    func queueEditingRetainsDraftWhenItemDisappears() {
        let item = QueuedFollowUp(prompt: "before")
        #expect(QueuedFollowUpChange.edit(original: "before", replacement: "draft").applying(to: [], id: item.id) == nil)
        #expect(QueuedFollowUpChange.edit(original: "stale", replacement: "draft").applying(to: [item], id: item.id) == nil)
    }

    @Test
    func deliberatelyPausedQueueDoesNotLookLikeAnAgentFailure() {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        var item = QueuedFollowUp(prompt: "Later")
        item.paused = true
        item.lastError = "Queue paused after stopping this turn."
        model.followUpQueue = [item]
        #expect(model.followUpStatusText == model.appLanguage.text("已停止本轮，队列已暂停", "Turn stopped; queue paused"))
    }

    private func makeWindow() -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 500),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }
    private func descendants(_ view: NSView) -> [NSView] {
        view.subviews.flatMap { [$0] + descendants($0) }
    }
}
