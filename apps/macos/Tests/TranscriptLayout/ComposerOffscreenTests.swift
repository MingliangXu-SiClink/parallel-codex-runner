import AppKit
import Combine
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ComposerOffscreenTests {
    @Test
    func nativeEditorKeepsMarkedTextSelectionAndIdentityDuringBackgroundUpdates() async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = DesktopStateStore(directory: directory)
        let model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
            tokenURL: directory.appendingPathComponent("absent.token")),
            windowID: UUID(), desktopStateStore: store)
        defer {
            model.promptDraftSaveTask?.cancel()
            model.stopWindowObservation()
            try? FileManager.default.removeItem(at: directory)
        }
        model.promptDraftContext = "offscreen-conversation"
        let host = NSHostingView(rootView: PromptComposer().environmentObject(model))
        host.frame = NSRect(x: 0, y: 0, width: 800, height: 400)
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(host.window == nil)
        editor.insertText("Draft: ", replacementRange: NSRange(location: NSNotFound, length: 0))
        #expect(model.prompt == "Draft: ")
        editor.setMarkedText("输入", selectedRange: NSRange(location: 2, length: 0),
            replacementRange: NSRange(location: NSNotFound, length: 0))
        #expect(editor.hasMarkedText())
        let composing = editor.string
        let selection = editor.selectedRange()
        for index in 1...40 {
            model.statusText = "Background snapshot \(index)"
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(5))
            #expect(editor.hasMarkedText())
            #expect(editor.string == composing)
            #expect(editor.selectedRange() == selection)
            #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
        }
        let markedRange = editor.markedRange()
        editor.insertText("输入完成", replacementRange: markedRange)
        #expect(!editor.hasMarkedText())
        #expect(model.prompt == "Draft: 输入完成")
        model.updatePrompt("External history selection")
        try await Task.sleep(for: .milliseconds(30))
        host.layoutSubtreeIfNeeded()
        #expect(editor.string == "External history selection")
        model.flushPromptDraftPersistence()
        #expect(store.loadSnapshot().promptDraftsByContext[model.promptDraftContext] == model.prompt)
        #expect(host.window == nil)
    }

    @Test
    func composerReceivesDraftOnlyPublicationsWithoutReaderRefresh() async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let model = AppModel(windowID: UUID(), desktopStateStore: DesktopStateStore(directory: directory))
        defer { model.stopWindowObservation(); try? FileManager.default.removeItem(at: directory) }
        let host = NSHostingView(rootView: PromptComposer().environmentObject(model))
        host.frame = NSRect(x: 0, y: 0, width: 540, height: 400)
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        var readerPublications = 0
        let reader = model.objectWillChange.sink { readerPublications += 1 }
        defer { reader.cancel() }
        for index in 1...40 {
            model.updatePrompt("A draft \(index) with several words")
            try await Task.sleep(for: .milliseconds(5))
            host.layoutSubtreeIfNeeded()
            #expect(editor.string == model.prompt)
            #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
        }
        #expect(readerPublications == 0)
        #expect(host.window == nil)
    }

    private func descendants(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(descendants)
    }
}
