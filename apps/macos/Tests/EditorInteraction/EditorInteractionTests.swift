import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct EditorInteractionTests {
    @Test
    func displayZoomKeepsTheSameEditorAndUncommittedInput() throws {
        let window = makeWindow()
        defer { window.close() }
        func editorView(_ size: AppTextSize) -> some View {
            NativeMultilineEditor(
                text: .constant(""), measuredHeight: .constant(80),
                minimumHeight: 52, maximumHeight: 200, fontSize: 16,
                accessibilityPlaceholder: "Message", onSubmit: nil
            )
            .environment(\.appTextSize, size)
        }
        let host = NSHostingView(rootView: editorView(.small))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(editor.font?.pointSize == 16 * AppTextSize.small.scale)
        editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0),
                             replacementRange: NSRange(location: 0, length: 0))
        let selection = editor.selectedRange()
        host.rootView = editorView(.extraLarge)
        host.layoutSubtreeIfNeeded()
        #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
        #expect(editor.font?.pointSize == 16 * AppTextSize.extraLarge.scale)
        #expect(editor.string == "中文")
        #expect(editor.hasMarkedText())
        #expect(editor.selectedRange() == selection)
        editor.unmarkText()
    }

    @Test
    func largerComposerPreservesMarkedTextSelectionAndNativeInsets() async throws {
        let window = makeWindow()
        defer { window.close() }
        func editorView(_ value: String) -> NativeMultilineEditor {
            NativeMultilineEditor(
                text: .constant(value), measuredHeight: .constant(52),
                minimumHeight: ParallelCodexVisualStyle.Composer.minimumEditorHeight,
                maximumHeight: ParallelCodexVisualStyle.Composer.maximumEditorHeight,
                fontSize: ParallelCodexVisualStyle.Composer.fontSize,
                accessibilityPlaceholder: "Message", onSubmit: nil
            )
        }
        let host = NSHostingView(rootView: editorView("A draft\nSecond line"))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(editor.font?.pointSize == 16 * AppTextSize.standard.scale)
        #expect(editor.minSize.height >= ParallelCodexVisualStyle.Composer.minimumEditorHeight)
        #expect(editor.textContainerOrigin.x == NativeMultilineEditor.horizontalTextInset)
        #expect(editor.textContainerOrigin.y == NativeMultilineEditor.verticalTextInset)
        #expect(editor.textContainer?.lineFragmentPadding == 0)
        editor.setSelectedRange(NSRange(location: 3, length: 2))
        host.rootView = editorView("A draft\nSecond line")
        host.layoutSubtreeIfNeeded()
        #expect(editor.selectedRange() == NSRange(location: 3, length: 2))
        editor.setMarkedText("中文", selectedRange: NSRange(location: 2, length: 0), replacementRange: editor.selectedRange())
        let markedDraft = editor.string
        host.rootView = editorView("A draft\nSecond line")
        host.layoutSubtreeIfNeeded()
        #expect(editor.hasMarkedText())
        #expect(editor.string == markedDraft)
        editor.unmarkText()
        await Task.yield()
    }

    @Test
    func composerIsTheInitialKeyViewWithoutAnExplicitFocusRequest() async throws {
        let window = makeWindow()
        defer { window.close() }
        let host = makeEditor(initialFocus: true)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(window.initialFirstResponder === editor)

        window.makeKeyAndOrderFront(nil)
        #expect(window.firstResponder === editor)
        #expect(editor.string == "Restored draft\nSecond line")
        #expect(editor.selectedRange() == NSRange(location: editor.string.utf16.count, length: 0))

        let search = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 24))
        host.addSubview(search)
        window.makeFirstResponder(search)
        let focusedSearchEditor = window.firstResponder
        host.rootView = editorView(initialFocus: true)
        host.layoutSubtreeIfNeeded()
        await Task.yield()
        #expect(window.firstResponder === focusedSearchEditor)
    }

    @Test
    func editorDoesNotReplaceAnExistingInitialKeyView() throws {
        let window = makeWindow()
        defer { window.close() }
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
        window.contentView = root
        let search = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 24))
        root.addSubview(search)
        window.initialFirstResponder = search
        let host = makeEditor(initialFocus: true)
        root.addSubview(host)
        host.layoutSubtreeIfNeeded()
        #expect(window.initialFirstResponder === search)
    }

    @Test
    func composerCanReplaceTheHostingViewsDefaultInitialFocus() async throws {
        let window = makeWindow()
        defer { window.close() }
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
        window.contentView = root
        window.initialFirstResponder = root
        let host = makeEditor(initialFocus: true)
        root.addSubview(host)
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(window.initialFirstResponder === editor)
        window.makeKeyAndOrderFront(nil)
        await Task.yield()
        #expect(window.firstResponder === editor)
    }

    @Test
    func composerMountedAfterWindowPresentationReceivesUnclaimedFocus() async throws {
        let window = makeWindow()
        defer { window.close() }
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
        window.contentView = root
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(window)
        let host = makeEditor(initialFocus: true)
        root.addSubview(host)
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.main.async { continuation.resume() }
        }
        #expect(window.firstResponder === editor)
    }

    @Test
    func asynchronouslyRestoredDraftPlacesTheCaretAfterTheText() throws {
        let window = makeWindow()
        defer { window.close() }
        let host = NSHostingView(rootView: NativeMultilineEditor(
            text: .constant(""), measuredHeight: .constant(80),
            minimumHeight: 34, maximumHeight: 164,
            isInitialFirstResponder: true, onSubmit: nil
        ))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(editor.selectedRange().location == 0)
        host.rootView = editorView(initialFocus: true)
        host.layoutSubtreeIfNeeded()
        #expect(editor.string == "Restored draft\nSecond line")
        #expect(editor.selectedRange() == NSRange(location: editor.string.utf16.count, length: 0))
    }

    @Test
    func otherMultilineEditorsDoNotRequestInitialFocus() {
        let window = makeWindow()
        defer { window.close() }
        let host = makeEditor(initialFocus: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        #expect(window.initialFirstResponder == nil)
    }

    private func makeWindow() -> NSWindow {
        _ = NSApplication.shared
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 500, height: 200),
            styleMask: [.titled], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        return window
    }

    private func editorView(initialFocus: Bool) -> NativeMultilineEditor {
        NativeMultilineEditor(
            text: .constant("Restored draft\nSecond line"),
            measuredHeight: .constant(80), minimumHeight: 34, maximumHeight: 164,
            isInitialFirstResponder: initialFocus, onSubmit: nil
        )
    }

    private func makeEditor(initialFocus: Bool) -> NSHostingView<NativeMultilineEditor> {
        let host = NSHostingView(rootView: editorView(initialFocus: initialFocus))
        host.frame = NSRect(x: 0, y: 40, width: 480, height: 100)
        return host
    }

    private func descendants(_ view: NSView) -> [NSView] {
        view.subviews.flatMap { [$0] + descendants($0) }
    }
}
