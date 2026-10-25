import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ConversationSearchFocusTests {
    @Test
    func closingSearchRestoresTheEditorAndItsSelection() {
        let window = makeWindow()
        defer { window.close() }
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 300, height: 100))
        editor.string = "Draft with a cursor in the middle"
        window.contentView?.addSubview(editor)
        window.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: 6, length: 4))
        let restoration = ConversationSearchFocus(window: window)
        let search = NSTextField(frame: NSRect(x: 0, y: 110, width: 200, height: 24))
        window.contentView?.addSubview(search)
        window.makeFirstResponder(search)

        #expect(restoration.restore())
        #expect(window.firstResponder === editor)
        #expect(editor.selectedRange() == NSRange(location: 6, length: 4))
        #expect(editor.string == "Draft with a cursor in the middle")
    }

    @Test
    func sharedFieldEditorReturnsToItsOriginalControl() throws {
        let window = makeWindow()
        defer { window.close() }
        let original = NSTextField(frame: NSRect(x: 0, y: 0, width: 200, height: 24))
        let search = NSTextField(frame: NSRect(x: 0, y: 40, width: 200, height: 24))
        window.contentView?.addSubview(original)
        window.contentView?.addSubview(search)
        window.makeFirstResponder(original)
        let fieldEditor = try #require(window.firstResponder as? NSTextView)
        #expect(fieldEditor.isFieldEditor)
        let restoration = ConversationSearchFocus(window: window)
        window.makeFirstResponder(search)
        #expect(window.firstResponder === fieldEditor)

        #expect(restoration.restore())
        #expect(original.currentEditor() === fieldEditor)
        #expect(search.currentEditor() == nil)
    }

    @Test
    func removedOrHiddenEditorsDoNotReceiveFocus() {
        let window = makeWindow()
        defer { window.close() }
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 200, height: 80))
        window.contentView?.addSubview(editor)
        window.makeFirstResponder(editor)
        let restoration = ConversationSearchFocus(window: window)
        window.makeFirstResponder(window)
        editor.isHidden = true
        #expect(!restoration.restore())
        editor.removeFromSuperview()
        #expect(!restoration.restore())
        #expect(window.firstResponder === window)
        #expect(!ConversationSearchFocus(window: nil).restore())
    }

    @Test
    func resultNavigationWithoutAnEditableResponderReturnsToTheComposer() {
        let window = makeWindow()
        defer { window.close() }
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 300, height: 80))
        editor.string = "Unsent draft"
        editor.setSelectedRange(NSRange(location: 3, length: 0))
        window.contentView?.addSubview(editor)
        window.initialFirstResponder = editor
        window.makeFirstResponder(window)
        let restoration = ConversationSearchFocus(window: window, restoringInitialResponder: true)

        #expect(restoration.restore())
        #expect(window.firstResponder === editor)
        #expect(editor.string == "Unsent draft")
        #expect(editor.selectedRange() == NSRange(location: 3, length: 0))
        window.makeFirstResponder(window)
        editor.isHidden = true
        #expect(!restoration.restore())
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
}
