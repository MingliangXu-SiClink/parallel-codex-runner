import AppKit

@MainActor
final class ConversationSearchFocus {
    private weak var window: NSWindow?
    private weak var view: NSView?

    init(window: NSWindow?, restoringInitialResponder: Bool = false) {
        self.window = window
        if restoringInitialResponder {
            view = window?.initialFirstResponder
        } else if let fieldEditor = window?.firstResponder as? NSTextView,
           fieldEditor.isFieldEditor {
            // AppKit shares a field editor between text fields. Remember its
            // owning control, not the editor that search is about to reuse.
            view = fieldEditor.delegate as? NSView
        } else {
            view = window?.firstResponder as? NSView
        }
    }

    @discardableResult
    func restore() -> Bool {
        guard let window, let view,
              view.window === window,
              !view.isHiddenOrHasHiddenAncestor,
              view.acceptsFirstResponder else { return false }
        return window.makeFirstResponder(view)
    }
}
