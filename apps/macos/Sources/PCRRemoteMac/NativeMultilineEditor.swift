import AppKit
import SwiftUI

enum MultilineEditorKeyAction: Equatable {
    case insertText
    case submit
}

func multilineEditorKeyAction(
    keyCode: UInt16,
    modifierFlags: NSEvent.ModifierFlags,
    submitsWithReturn: Bool
) -> MultilineEditorKeyAction {
    let isReturn = keyCode == 36 || keyCode == 76
    let modifiers = modifierFlags.intersection(.deviceIndependentFlagsMask)
    let insertsNewline = !modifiers.intersection([.shift, .control, .option]).isEmpty
    return isReturn && submitsWithReturn && !insertsNewline ? .submit : .insertText
}

func multilineEditorShouldApplyExternalText(
    currentText: String,
    externalText: String,
    hasMarkedText: Bool
) -> Bool {
    currentText != externalText && !hasMarkedText
}

func multilineEditorShouldShowPlaceholder(
    text: String,
    placeholder: String?,
    hasMarkedText: Bool
) -> Bool {
    text.isEmpty && !hasMarkedText && !(placeholder?.isEmpty ?? true)
}

@MainActor
protocol NativeMultilineEditorCommitTarget: AnyObject {
    func commitPendingText()
}

/// Keeps AppKit's live editor contents available to lifecycle code. AppKit can
/// accept an accessibility edit or input-method update just before SwiftUI
/// propagates its binding, so termination must explicitly commit the native
/// value before draft persistence runs.
@MainActor
final class NativeMultilineEditorCommitRegistry {
    static let shared = NativeMultilineEditorCommitRegistry()

    private final class WeakTarget {
        weak var value: (any NativeMultilineEditorCommitTarget)?

        init(_ value: any NativeMultilineEditorCommitTarget) {
            self.value = value
        }
    }

    private var targets: [ObjectIdentifier: WeakTarget] = [:]

    func register(_ target: any NativeMultilineEditorCommitTarget) {
        targets[ObjectIdentifier(target)] = WeakTarget(target)
    }

    func unregister(_ target: any NativeMultilineEditorCommitTarget) {
        targets.removeValue(forKey: ObjectIdentifier(target))
    }

    func commitPendingText() {
        var stale: [ObjectIdentifier] = []
        for (identifier, target) in targets {
            guard let value = target.value else {
                stale.append(identifier)
                continue
            }
            value.commitPendingText()
        }
        for identifier in stale {
            targets.removeValue(forKey: identifier)
        }
    }
}

@MainActor
struct NativeMultilineEditor: NSViewRepresentable {
    nonisolated static let horizontalTextInset: CGFloat = 5
    nonisolated static let verticalTextInset: CGFloat = 5

    @Environment(\.appAccentColor) private var appAccentColor
    @Environment(\.appTextSize) private var appTextSize
    @Binding var text: String
    @Binding var measuredHeight: CGFloat
    var minimumHeight: CGFloat
    var maximumHeight: CGFloat
    var fontSize: CGFloat = NSFont.systemFontSize
    var accessibilityLabel: String? = nil
    var accessibilityPlaceholder: String? = nil
    var isInitialFirstResponder = false
    var focusRequestSequence: Int = 0
    var onSubmit: (() -> Void)?
    var onFocusChange: ((Bool) -> Void)? = nil
    var initialSelection: PromptEditorSelection? = nil
    var onSelectionChange: ((PromptEditorSelection) -> Void)? = nil

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.borderType = .noBorder
        scrollView.drawsBackground = false
        scrollView.hasHorizontalScroller = false
        scrollView.hasVerticalScroller = false
        scrollView.autohidesScrollers = true

        let textView = SubmittingTextView()
        textView.delegate = context.coordinator
        textView.isRichText = false
        textView.importsGraphics = false
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false
        textView.allowsUndo = true
        textView.drawsBackground = false
        textView.font = editorFont
        textView.textColor = ParallelCodexVisualStyle.primaryTextNSColor
        textView.defaultParagraphStyle = editorParagraphStyle
        textView.typingAttributes[.paragraphStyle] = editorParagraphStyle
        textView.insertionPointColor = appAccentColor.nsColor
        textView.textContainerInset = NSSize(
            width: Self.horizontalTextInset,
            height: Self.verticalTextInset
        )
        // NSTextView otherwise adds an implicit 5-point line-fragment inset.
        // Making the inset explicit lets SwiftUI place the placeholder at the
        // exact insertion point instead of drawing the caret through its first
        // character.
        textView.textContainer?.lineFragmentPadding = 0
        textView.isHorizontallyResizable = false
        textView.isVerticallyResizable = true
        textView.autoresizingMask = [.width]
        textView.minSize = NSSize(width: 0, height: minimumHeight)
        textView.maxSize = NSSize(
            width: CGFloat.greatestFiniteMagnitude,
            height: CGFloat.greatestFiniteMagnitude
        )
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.heightTracksTextView = false
        textView.string = text
        if let initialSelection {
            textView.setSelectedRange(initialSelection.range(in: text))
            textView.restoredSelection = true
        }
        textView.placeholder = accessibilityPlaceholder
        textView.setAccessibilityLabel(accessibilityLabel)
        textView.setAccessibilityPlaceholderValue(accessibilityPlaceholder)
        textView.submitAction = onSubmit
        textView.focusChangeAction = onFocusChange
        textView.isInitialFirstResponder = isInitialFirstResponder
        scrollView.documentView = textView
        context.coordinator.textView = textView
        NativeMultilineEditorCommitRegistry.shared.register(context.coordinator)
        context.coordinator.updateHeight(textView, scrollView: scrollView)
        context.coordinator.requestFocusIfNeeded(
            sequence: focusRequestSequence,
            textView: textView
        )
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let textView = scrollView.documentView as? SubmittingTextView else {
            return
        }
        textView.submitAction = onSubmit
        textView.focusChangeAction = onFocusChange
        textView.minSize.height = minimumHeight
        if textView.font != editorFont {
            textView.font = editorFont
            textView.defaultParagraphStyle = editorParagraphStyle
            textView.typingAttributes[.paragraphStyle] = editorParagraphStyle
            textView.textStorage?.addAttribute(.paragraphStyle, value: editorParagraphStyle,
                range: NSRange(location: 0, length: textView.string.utf16.count))
            textView.needsDisplay = true
        }
        textView.insertionPointColor = appAccentColor.nsColor
        textView.placeholder = accessibilityPlaceholder
        textView.setAccessibilityLabel(accessibilityLabel)
        textView.setAccessibilityPlaceholderValue(accessibilityPlaceholder)
        // An input method keeps unfinished Chinese, Japanese, and Korean text
        // in the text view without updating the SwiftUI binding. Never replace
        // that marked text with the older binding value during a view refresh.
        if multilineEditorShouldApplyExternalText(
            currentText: textView.string,
            externalText: text,
            hasMarkedText: textView.hasMarkedText()
        ) {
            let selectedRanges = isInitialFirstResponder && textView.string.isEmpty
                ? [NSValue(range: NSRange(location: text.utf16.count, length: 0))]
                : textView.selectedRanges
            textView.string = text
            textView.needsDisplay = true
            textView.selectedRanges = selectedRanges.compactMap { value in
                let range = value.rangeValue
                let location = min(range.location, text.utf16.count)
                let length = min(range.length, max(0, text.utf16.count - location))
                return NSValue(range: NSRange(location: location, length: length))
            }
        }
        context.coordinator.updateHeight(textView, scrollView: scrollView)
        context.coordinator.requestFocusIfNeeded(
            sequence: focusRequestSequence,
            textView: textView
        )
    }

    private var editorFont: NSFont {
        .systemFont(ofSize: fontSize * appTextSize.scale)
    }

    private var editorParagraphStyle: NSParagraphStyle {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = ParallelCodexVisualStyle.Typography.bodyLineSpacing * appTextSize.scale
        return style
    }

    static func dismantleNSView(_ scrollView: NSScrollView, coordinator: Coordinator) {
        coordinator.commitPendingText()
        NativeMultilineEditorCommitRegistry.shared.unregister(coordinator)
        coordinator.textView = nil
    }

    @MainActor
    final class Coordinator: NSObject, NSTextViewDelegate, NativeMultilineEditorCommitTarget {
        var parent: NativeMultilineEditor
        fileprivate weak var textView: SubmittingTextView?
        private var handledFocusRequestSequence = 0
        private var scheduledFocusRequestSequence: Int?

        init(parent: NativeMultilineEditor) {
            self.parent = parent
        }

        func textDidChange(_ notification: Notification) {
            guard let textView = notification.object as? SubmittingTextView,
                  let scrollView = textView.enclosingScrollView else { return }
            if parent.text != textView.string {
                parent.text = textView.string
            }
            textView.needsDisplay = true
            updateHeight(textView, scrollView: scrollView)
        }

        func textDidBeginEditing(_ notification: Notification) {
            parent.onFocusChange?(true)
        }

        func textDidEndEditing(_ notification: Notification) {
            commitPendingText()
            parent.onFocusChange?(false)
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard let textView, !textView.hasMarkedText() else { return }
            let range = textView.selectedRange()
            parent.onSelectionChange?(.init(location: range.location, length: range.length))
        }

        func commitPendingText() {
            guard let textView else { return }
            if parent.text != textView.string { parent.text = textView.string }
            let range = textView.selectedRange()
            parent.onSelectionChange?(.init(location: range.location, length: range.length))
        }

        func requestFocusIfNeeded(sequence: Int, textView: NSTextView) {
            guard sequence > 0,
                  sequence != handledFocusRequestSequence,
                  sequence != scheduledFocusRequestSequence else { return }
            scheduledFocusRequestSequence = sequence
            DispatchQueue.main.async { [weak self, weak textView] in
                guard let self else { return }
                self.scheduledFocusRequestSequence = nil
                guard let textView,
                      self.parent.focusRequestSequence == sequence,
                      let window = textView.window,
                      window.makeFirstResponder(textView) else { return }
                self.handledFocusRequestSequence = sequence
            }
        }

        func updateHeight(_ textView: NSTextView, scrollView: NSScrollView) {
            guard let layoutManager = textView.layoutManager,
                  let textContainer = textView.textContainer else { return }
            layoutManager.ensureLayout(for: textContainer)
            let contentHeight = ceil(
                layoutManager.usedRect(for: textContainer).height
                    + textView.textContainerInset.height * 2
            )
            let height = min(
                parent.maximumHeight,
                max(parent.minimumHeight, contentHeight)
            )
            scrollView.hasVerticalScroller = contentHeight > parent.maximumHeight
            guard abs(parent.measuredHeight - height) > 0.5 else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self,
                      abs(self.parent.measuredHeight - height) > 0.5 else { return }
                self.parent.measuredHeight = height
            }
        }
    }
}

private final class SubmittingTextView: NSTextView {
    var submitAction: (() -> Void)?
    var focusChangeAction: ((Bool) -> Void)?
    var isInitialFirstResponder = false
    var restoredSelection = false
    private weak var initialFocusWindow: NSWindow?
    var placeholder: String? {
        didSet {
            if oldValue != placeholder { needsDisplay = true }
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard isInitialFirstResponder,
              let window,
              window !== initialFocusWindow,
              window.initialFirstResponder.map({ isDescendant(of: $0) }) ?? true else { return }
        initialFocusWindow = window
        window.initialFirstResponder = self
        DispatchQueue.main.async { [weak self] in self?.focusInitiallyIfUnclaimed() }
    }

    private func focusInitiallyIfUnclaimed() {
        guard let window, window === initialFocusWindow else { return }
        let responder = window.firstResponder
        // SwiftUI can install its hosting view before the asynchronous composer
        // appears. Replace only that ancestor, never a search field or sidebar.
        guard responder == nil || responder === window
            || (responder as? NSView).map({ isDescendant(of: $0) }) == true else { return }
        if responder !== self && !restoredSelection {
            setSelectedRange(NSRange(location: string.utf16.count, length: 0))
        }
        window.makeFirstResponder(self)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard multilineEditorShouldShowPlaceholder(
            text: string,
            placeholder: placeholder,
            hasMarkedText: hasMarkedText()
        ), let placeholder, let font else { return }
        NSAttributedString(
            string: placeholder,
            attributes: [
                .font: font,
                .foregroundColor: ParallelCodexVisualStyle.placeholderTextNSColor,
            ]
        ).draw(at: textContainerOrigin)
    }

    override func keyDown(with event: NSEvent) {
        // Return belongs to the input method while marked text is active. Let
        // AppKit commit the composition first; a later Return may submit.
        if hasMarkedText(), (event.keyCode == 36 || event.keyCode == 76) {
            super.keyDown(with: event)
            return
        }
        let action = multilineEditorKeyAction(
            keyCode: event.keyCode,
            modifierFlags: event.modifierFlags,
            submitsWithReturn: submitAction != nil
        )
        if action == .submit {
            submitAction?()
            return
        }
        super.keyDown(with: event)
    }
}
