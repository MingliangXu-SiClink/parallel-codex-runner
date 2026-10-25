import AppKit
import Quartz
import SwiftUI
import UniformTypeIdentifiers

struct ReviewContext: Equatable, Sendable, Hashable {
    let target: PcrdRequestTarget
    let workspaceID: String
    let runID: String?
    let agent: Int?
    var conversationID: String? = nil
}

enum WorkspacePreviewKind: Equatable {
    case markdown, text, visual, quickLook, external, directory

    static func resolve(_ url: URL, contentType: String) -> Self {
        if ["md", "markdown", "mdown", "mkd"].contains(url.pathExtension.lowercased()) { return .markdown }
        if VisualPreviewKind.resolve(path: url.path, contentType: contentType) != nil { return .visual }
        if url.pathExtension.lowercased() == "rtf" { return .quickLook }
        let type = UTType(filenameExtension: url.pathExtension) ?? UTType(mimeType: contentType)
        if contentType.hasPrefix("text/") || type?.conforms(to: .text) == true ||
            ["json", "yaml", "yml", "toml", "py", "swift", "kt", "js", "ts", "tsx", "rs", "tex", "log", "sh"].contains(url.pathExtension.lowercased()) {
            return .text
        }
        if type?.conforms(to: .image) == true || type?.conforms(to: .pdf) == true ||
            ["doc", "docx", "ppt", "pptx", "xls", "xlsx"].contains(url.pathExtension.lowercased()) { return .quickLook }
        return .external
    }
}

struct WorkspaceQuickLookPreview: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> QLPreviewView { QLPreviewView(frame: .zero, style: .normal)! }
    func updateNSView(_ view: QLPreviewView, context: Context) {
        if (view.previewItem?.previewItemURL ?? nil) != url {
            view.previewItem = url as NSURL
            view.autostarts = false
        }
    }
}

struct ReviewSelection: Equatable {
    let line: Int
    let endLine: Int
    let text: String
}

struct ReviewReadingPosition {
    let origin: NSPoint
    let selection: NSRange
    var anchor: Int? = nil
    var lineOffset: CGFloat = 0
    var lineHeight: CGFloat = 0
    var atBottom = false
}

struct ReviewTextView: NSViewRepresentable {
    @Environment(\.appLanguage) private var language
    @Environment(\.colorScheme) private var colorScheme
    let text: String
    let isDiff: Bool
    let fontSize: CGFloat
    var filePath = ""
    var initialLine: Int? = nil
    var initialPosition: ReviewReadingPosition? = nil
    var onSavePosition: ((ReviewReadingPosition) -> Void)? = nil
    var onSelection: ((ReviewSelection) -> Void)? = nil

    final class Coordinator {
        var request: ReviewSyntaxRequest?
        var task: Task<Void, Never>?
        var onSavePosition: ((ReviewReadingPosition) -> Void)?
        var restoredPosition = false
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    static func dismantleNSView(_ view: NSScrollView, coordinator: Coordinator) {
        coordinator.task?.cancel()
        if let position = (view as? ReviewScrollView)?.readingPosition() {
            coordinator.onSavePosition?(position)
        }
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = ReviewScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        let editor = ReviewNSTextView(usingTextLayoutManager: false)
        editor.isEditable = false
        editor.isSelectable = true
        editor.isRichText = false
        editor.autoresizingMask = []
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = true
        editor.minSize = NSSize(width: 0, height: 0)
        editor.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainer?.containerSize = NSSize(width: 1_000_000, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainer?.widthTracksTextView = false
        editor.textContainerInset = NSSize(width: 16, height: 14)
        scroll.documentView = editor
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let editor = scroll.documentView as? ReviewNSTextView else { return }
        context.coordinator.onSavePosition = onSavePosition
        editor.onSelection = onSelection
        editor.quoteTitle = language.text("引用到对话", "Quote in conversation")
        let request = ReviewSyntaxRequest(text: text, path: filePath, isDiff: isDiff,
                                         fontSize: fontSize, dark: colorScheme == .dark)
        guard context.coordinator.request != request else { return }
        context.coordinator.request = request
        context.coordinator.task?.cancel()
        let styled = NSAttributedString(string: text, attributes: [
            .font: ReviewSyntaxRequest.font(ofSize: fontSize),
            .foregroundColor: NSColor.labelColor,
        ])
        let selection = editor.selectedRange()
        (scroll as? ReviewScrollView)?.preservingReadingPosition {
            editor.textStorage?.setAttributedString(styled)
            editor.setSelectedRange(NSRange(location: min(selection.location, text.utf16.count),
                length: min(selection.length, max(0, text.utf16.count - selection.location))))
            editor.backgroundColor = .textBackgroundColor
            editor.font = ReviewSyntaxRequest.font(ofSize: fontSize)
        }
        let coordinator = context.coordinator
        context.coordinator.task = Task { @MainActor [weak editor, weak scroll, weak coordinator] in
            let result = await ReviewSyntaxRenderer.shared.render(request)
            guard !Task.isCancelled, coordinator?.request == request,
                  let editor, editor.string == request.text else { return }
            let selection = editor.selectedRanges
            (scroll as? ReviewScrollView)?.preservingReadingPosition {
                editor.textStorage?.setAttributedString(result.value)
                editor.selectedRanges = selection
            }
        }
        if !context.coordinator.restoredPosition, let initialPosition {
            context.coordinator.restoredPosition = true
            let length = (text as NSString).length
            let start = min(initialPosition.selection.location, length)
            editor.setSelectedRange(NSRange(location: start, length: min(initialPosition.selection.length, length - start)))
            (scroll as? ReviewScrollView)?.restoreReadingPosition(initialPosition)
        }
        if let line = initialLine, line > 0, !editor.didScrollToInitialLine {
            let lines = text.components(separatedBy: "\n")
            let offset = lines.prefix(max(0, min(line - 1, lines.count))).reduce(0) { $0 + ($1 as NSString).length + 1 }
            editor.scrollRangeToVisible(NSRange(location: min(offset, (text as NSString).length), length: 0))
            editor.didScrollToInitialLine = true
        }
    }
}

final class ReviewScrollView: NSScrollView {
    private var pendingReadingPosition: ReviewReadingPosition?
    private var preservingPosition = false

    override func setFrameSize(_ newSize: NSSize) {
        preservingReadingPosition { super.setFrameSize(newSize) }
    }

    func preservingReadingPosition(_ change: () -> Void) {
        guard !preservingPosition else { change(); return }
        let position = readingPosition()
        preservingPosition = true
        defer { preservingPosition = false }
        change()
        resizeDocumentToFitContent()
        if let position { restoreReadingPosition(position) }
    }

    func readingPosition() -> ReviewReadingPosition? {
        if let pendingReadingPosition { return pendingReadingPosition }
        guard let editor = documentView as? NSTextView else { return nil }
        if let layout = editor.layoutManager, let container = editor.textContainer {
            layout.ensureLayout(for: container)
        }
        var position = ReviewReadingPosition(origin: contentView.bounds.origin, selection: editor.selectedRange())
        let maxY = max(0, editor.frame.height - contentSize.height)
        position.atBottom = maxY > 1 && position.origin.y >= maxY - 1
        if let layout = editor.layoutManager, let container = editor.textContainer, !editor.string.isEmpty {
            let point = NSPoint(x: 0, y: max(0, position.origin.y - editor.textContainerOrigin.y))
            let glyph = layout.glyphIndex(for: point, in: container)
            if glyph < layout.numberOfGlyphs {
                position.anchor = layout.characterIndexForGlyph(at: glyph)
                let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
                position.lineOffset = position.origin.y - editor.textContainerOrigin.y - line.minY
                position.lineHeight = line.height
            }
        }
        return position
    }

    func restoreReadingPosition(_ position: ReviewReadingPosition) {
        guard contentSize.width > 1, contentSize.height > 1 else {
            // SwiftUI can supply text before assigning the final viewport.
            // Keep the reading intent instead of accepting a clamped offset.
            pendingReadingPosition = position
            return
        }
        pendingReadingPosition = nil
        var origin = position.origin
        if let editor = documentView as? NSTextView, let layout = editor.layoutManager,
           let container = editor.textContainer, let anchor = position.anchor, !editor.string.isEmpty {
            layout.ensureLayout(for: container)
            let glyph = layout.glyphIndexForCharacter(at: min(anchor, (editor.string as NSString).length - 1))
            let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            let offset = position.lineHeight > 0 ? position.lineOffset * line.height / position.lineHeight : position.lineOffset
            origin.y = line.minY + editor.textContainerOrigin.y + offset
        }
        if position.atBottom, let documentView {
            origin.y = max(0, documentView.frame.height - contentSize.height)
        }
        contentView.scroll(to: origin)
        reflectScrolledClipView(contentView)
    }

    override func layout() {
        preservingReadingPosition { super.layout() }
    }

    func resizeDocumentToFitContent() {
        guard let editor = documentView as? NSTextView,
              let container = editor.textContainer, let layout = editor.layoutManager else { return }
        layout.ensureLayout(for: container)
        let used = layout.usedRect(for: container)
        let size = NSSize(width: max(contentSize.width, used.maxX + editor.textContainerInset.width * 2),
                          height: max(contentSize.height, used.maxY + editor.textContainerInset.height * 2))
        if editor.frame.size != size { editor.setFrameSize(size) }
    }
}

final class ReviewNSTextView: NSTextView {
    var onSelection: ((ReviewSelection) -> Void)?
    var didScrollToInitialLine = false
    var quoteTitle = "Quote in conversation"

    override func viewDidEndLiveResize() {
        guard let scroll = enclosingScrollView as? ReviewScrollView else {
            super.viewDidEndLiveResize()
            return
        }
        // NSTextView recenters a glyph when native split animations finish.
        // Retain the precise viewport, including horizontal and within-line offsets.
        scroll.preservingReadingPosition { super.viewDidEndLiveResize() }
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        let menu = super.menu(for: event) ?? NSMenu()
        if selectedRange().length > 0, onSelection != nil {
            menu.addItem(.separator())
            let item = NSMenuItem(title: quoteTitle, action: #selector(quoteSelection), keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }
        return menu
    }

    @objc private func quoteSelection() {
        let range = selectedRange()
        let source = string as NSString
        guard range.length > 0, NSMaxRange(range) <= source.length else { return }
        let line = source.substring(to: range.location).filter { $0 == "\n" }.count + 1
        let selected = source.substring(with: range)
        onSelection?(ReviewSelection(line: line, endLine: line + selected.filter { $0 == "\n" }.count, text: selected))
    }
}

struct ReviewFile: Identifiable, Equatable, Sendable {
    let path: String
    let text: String
    var id: String { path }

    static func needsMore(patch: String, requestedPath: String?, hasMore: Bool) -> Bool {
        guard hasMore, let requestedPath else { return false }
        let files = parse(patch)
        // A file at the page tail may still have unloaded hunks. Continue until
        // the following file starts (or EOF), not merely until its header exists.
        guard let index = files.firstIndex(where: { $0.path == requestedPath }) else { return true }
        return index == files.count - 1
    }

    static func parse(_ patch: String) -> [ReviewFile] {
        var files: [ReviewFile] = []
        var path: String?
        var lines: [String] = []
        func finish() {
            if let path { files.append(ReviewFile(path: path, text: lines.joined(separator: "\n"))) }
        }
        let sourceLines = patch.components(separatedBy: "\n")
        var remainingOld = 0
        var remainingNew = 0
        for (index, line) in sourceLines.enumerated() {
            if line.hasPrefix("diff --git ") {
                finish()
                let tokens = line.dropFirst(11)
                if tokens.hasPrefix("\""),
                   let regex = try? NSRegularExpression(pattern: #""(?:[^"\\]|\\.)*""#),
                   let last = regex.matches(in: String(tokens), range: NSRange(location: 0, length: (String(tokens) as NSString).length)).last,
                   let data = (String(tokens) as NSString).substring(with: last.range).data(using: .utf8),
                   let decoded = try? JSONDecoder().decode(String.self, from: data) {
                    path = decoded.hasPrefix("b/") ? String(decoded.dropFirst(2)) : decoded
                } else { path = String(line.components(separatedBy: " b/").last ?? line) }
                lines = [line]
                remainingOld = 0
                remainingNew = 0
            } else if line.hasPrefix("--- "), remainingOld == 0, remainingNew == 0,
                      index + 1 < sourceLines.count, sourceLines[index + 1].hasPrefix("+++ ") {
                if lines.contains(where: { $0.hasPrefix("+++ ") }) {
                    finish()
                    lines = []
                    path = nil
                }
                let old = String(line.dropFirst(4)).components(separatedBy: "\t")[0]
                let new = String(sourceLines[index + 1].dropFirst(4)).components(separatedBy: "\t")[0]
                let name = new == "/dev/null" ? old : new
                path = name.hasPrefix("a/") || name.hasPrefix("b/") ? String(name.dropFirst(2)) : name
                lines.append(line)
            } else {
                if line.hasPrefix("@@ ") {
                    let fields = line.split(separator: " ")
                    if fields.count >= 3 {
                        func count(_ field: Substring) -> Int {
                            let values = field.split(separator: ",")
                            return values.count > 1 ? Int(values[1]) ?? 0 : 1
                        }
                        remainingOld = count(fields[1])
                        remainingNew = count(fields[2])
                    }
                } else if remainingOld > 0 || remainingNew > 0 {
                    if line.hasPrefix("-") || line.hasPrefix(" ") { remainingOld = max(0, remainingOld - 1) }
                    if line.hasPrefix("+") || line.hasPrefix(" ") { remainingNew = max(0, remainingNew - 1) }
                }
                if path != nil { lines.append(line) }
            }
        }
        finish()
        return files.isEmpty && !patch.isEmpty ? [ReviewFile(path: "Patch", text: patch)] : files
    }
}

extension AppModel {
    func addReviewReference(context: ReviewContext?, file: String, selection: ReviewSelection) {
        guard let context,
              client.currentTarget() == context.target, selectedWorkspaceID == context.workspaceID,
              activeRun?.runId == context.runID, selectedConversationID == context.conversationID else {
            errorMessage = appLanguage.text("请回到该文件所属的对话后引用。", "Return to this file's conversation before quoting it.")
            return
        }
        NativeMultilineEditorCommitRegistry.shared.commitPendingText()
        let name = context.agent.map { "Agent \($0) · " } ?? ""
        let quoted = selection.text.components(separatedBy: "\n").map { "> " + $0 }.joined(separator: "\n")
        prompt += (prompt.isEmpty ? "" : "\n\n") + "\(name)\(file):\(selection.line)-\(selection.endLine)\n\(quoted)\n\n"
        promptFocusRequestSequence &+= 1
    }
}
