import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ComposerModelPickerTests {
    @Test func catalogPublicationDoesNotRecreateTheComposerOrReplaceMarkedText() async throws {
        _ = NSApplication.shared
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.prompt = "A draft\nSecond line"
        model.settings.model = "gpt-6.1-sol"
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 500),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer {
            window.makeFirstResponder(nil)
            NativeMultilineEditorCommitRegistry.shared.commitPendingText()
            window.close()
        }
        let host = NSHostingView(rootView: PromptComposer().environmentObject(model))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        window.makeFirstResponder(editor)
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.main.async { continuation.resume() }
        }
        #expect(window.firstResponder === editor)
        editor.setSelectedRange(NSRange(location: 2, length: 2))
        editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0), replacementRange: editor.selectedRange())
        let draft = editor.string
        let selection = editor.selectedRange()
        let value = CodexModelOptions(configuredModel: "gpt-6.1-sol", configuredEffort: "max",
            configuredServiceTier: nil, effectiveModel: "gpt-6.1-sol", effectiveEffort: "max", effectiveFast: nil,
            models: [CodexOption(label: "gpt-6.1-sol", value: "gpt-6.1-sol", selected: true)],
            efforts: [CodexOption(label: "max", value: "max", selected: true)])
        _ = try await model.modelOptionsCache.refresh(context: model.primaryModelOptionsContext) { value }
        model.modelOptionsRevision &+= 1
        await Task.yield()
        host.layoutSubtreeIfNeeded()
        #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
        #expect(editor.string == draft)
        #expect(editor.selectedRange() == selection)
        #expect(editor.hasMarkedText())
        editor.unmarkText()
        editor.didChangeText()
        await Task.yield()
        host.layoutSubtreeIfNeeded()
        #expect(model.prompt == draft)
    }

    private func descendants(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(descendants)
    }
}
