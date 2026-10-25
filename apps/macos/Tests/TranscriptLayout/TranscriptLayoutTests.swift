import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

struct TranscriptLayoutTests {
    @Test
    @MainActor
    func wideConversationsUseTheExpandedColumnAndStillFitNarrowWindows() throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 2600, height: 1000),
            styleMask: [.borderless], backing: .buffered, defer: false)
        defer { window.contentView = nil }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        #expect(ParallelCodexVisualStyle.Layout.transcriptMaximumWidth == 2000)
        #expect(ParallelCodexVisualStyle.Layout.composerMaximumWidth == 1040)
        for kind in [TranscriptKind.output, .thought, .user] {
            let block = TranscriptBlock(id: "wide-column", kind: kind,
                text: String(repeating: "Long conversation text should use the available space. ", count: 40))
            for width: CGFloat in [320, 640, 900, 1200, 1800, 2000, 2600, 320] {
                let height = TranscriptCollectionItem.preferredHeight(for: block,
                    textSize: .standard, accentColor: .orange, appearance: .dark, availableWidth: width)
                item.configure(block: block, language: .english, textSize: .standard,
                    accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                    availableWidth: width, openURL: { _ in }, activateBlock: { _ in })
                item.view.frame = NSRect(x: 0, y: 0, width: width, height: height)
                item.view.layoutSubtreeIfNeeded()
                let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                let frame = text.convert(text.bounds, to: item.view)
                #expect(frame.minX >= 0 && frame.maxX <= width + 0.5)
                if kind == .user {
                    if width >= 1200 {
                        // Bubbles size to the longest wrapped line rather than
                        // filling the entire cap for a short final word.
                        #expect(frame.width > 760 && frame.width <= 796)
                    }
                } else {
                    let expected = min(width, 2000) - (kind == .output ? 40 : 80)
                    #expect(abs(frame.width - expected) < 1,
                        "\(kind) at \(width) points has width \(frame.width), expected \(expected)")
                }
            }
        }
    }

    @Test
    @MainActor
    func composerStaysCenteredAndNarrowerThanWideReplies() async throws {
        _ = NSApplication.shared
        let model = AppModel(windowID: UUID())
        model.prompt = "A multiline prompt\nA second line"
        defer { model.stopWindowObservation() }
        let host = NSHostingView(rootView: PromptComposer().environmentObject(model))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 2400, height: 400),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        for width: CGFloat in [2400, 1440, 540, 2400] {
            window.setContentSize(NSSize(width: width, height: 400))
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(30))
            host.layoutSubtreeIfNeeded()
            let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
            let frame = editor.convert(editor.bounds, to: host)
            #expect(frame.width <= 1040)
            #expect(frame.minX >= 0 && frame.maxX <= width)
            #expect(abs(frame.midX - width / 2) < 16)
            if width >= 1440 { #expect(frame.width > 900) }
        }
    }

    @Test
    @MainActor
    func aCollapsedReplyCannotDrawOutsideItsCollectionFrame() throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 500),
            styleMask: [.borderless], backing: .buffered, defer: false)
        let host = NSView(frame: NSRect(x: 0, y: 0, width: 1000, height: 500))
        host.wantsLayer = true
        window.contentView = host
        defer { window.contentView = nil }
        let item = TranscriptCollectionItem()
        host.addSubview(item.view)
        item.configure(block: TranscriptBlock(id: "old", kind: .output,
            text: String(repeating: "Old message outside its row.\n\n", count: 20)),
            language: .english, textSize: .standard, accentColor: .orange, appearance: .dark,
            findQuery: "", currentFindOccurrence: nil, availableWidth: 920,
            openURL: { _ in }, activateBlock: { _ in })
        item.view.frame = NSRect(x: 0, y: 50, width: 920, height: 400)
        host.layoutSubtreeIfNeeded()
        // AppKit can collapse or reposition a reusable row before its children
        // receive their next layout pass. That row must not paint other rows.
        item.view.frame.size.height = 0
        let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        var painted = 0
        for y in 0..<bitmap.pixelsHigh {
            for x in 0..<bitmap.pixelsWide {
                if let color = bitmap.colorAt(x: x, y: y), color.alphaComponent > 0.1 {
                    painted += 1
                }
            }
        }
        #expect(painted == 0, "A zero-height reusable row painted \(painted) pixels outside its frame.")
    }

    @Test
    @MainActor
    func reusedReplyDrawsOnlyItsCurrentText() throws {
        _ = NSApplication.shared
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1000, height: 800),
            styleMask: [.borderless], backing: .buffered, defer: false
        )
        window.contentView?.wantsLayer = true
        defer { window.contentView = nil }
        let reused = TranscriptCollectionItem()
        let fresh = TranscriptCollectionItem()
        let previous = TranscriptBlock(id: "previous", kind: .output,
            text: String(repeating: "Previous conversation must not remain visible.\n\n", count: 30))
        let current = TranscriptBlock(id: "current", kind: .output,
            text: "Current response.\n\n| Method | Error |\n| --- | ---: |\n| First | 209.3 |\n| Second | 292.1 |\n\nFinal paragraph.")
        for item in [reused, fresh] {
            window.contentView?.addSubview(item.view)
            let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
            #expect(text.textLayoutManager == nil,
                    "Transcript measurement uses NSLayoutManager; the view must not first create TextKit 2 drawing fragments.")
        }
        func configure(_ item: TranscriptCollectionItem, _ block: TranscriptBlock) throws -> NSBitmapImageRep {
            let width: CGFloat = 920
            let height = TranscriptCollectionItem.preferredHeight(
                for: block, textSize: .standard, accentColor: .orange,
                appearance: .dark, availableWidth: width
            )
            item.configure(block: block, language: .english, textSize: .standard,
                accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                availableWidth: width, openURL: { _ in }, activateBlock: { _ in })
            item.view.frame = NSRect(x: 0, y: 0, width: width, height: height)
            item.view.layoutSubtreeIfNeeded()
            let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
            item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
            return bitmap
        }
        _ = try configure(reused, previous)
        let actual = try configure(reused, current)
        let expected = try configure(fresh, current)
        #expect(actual.pixelsWide == expected.pixelsWide)
        #expect(actual.pixelsHigh == expected.pixelsHigh)
        #expect(actual.representation(using: .png, properties: [:])
                == expected.representation(using: .png, properties: [:]))
    }

    @Test
    @MainActor
    func userMessageKeepsLineBreaksWithoutLosingMarkdown() {
        let source = "&#x20; vx: -0.12\n&#x20; vy: 0\nomega: 0\nduration: 5\ncommand\\_duration: 0.15"
        let rendered = render(source, kind: .user)
        #expect(rendered.string == "  vx: -0.12\n  vy: 0\nomega: 0\nduration: 5\ncommand_duration: 0.15")
        #expect(render("First line\nSecond line\n最后一行", kind: .user).string == "First line\nSecond line\n最后一行")
        #expect(render("**First**\n[Second](https://example.com)", kind: .user).string == "First\nSecond")
        #expect(render("```ini\nx=1\ny=2\n```", kind: .user).string.contains("x=1\ny=2"))
        #expect(render("First line\nSecond line", kind: .output).string == "First line Second line")
        let linked = render("[First](https://example.com)\nSecond", kind: .user)
        #expect(linked.attribute(.link, at: 0, effectiveRange: nil) as? URL == URL(string: "https://example.com"))
    }

    @MainActor
    private func render(_ text: String, kind: TranscriptKind) -> NSAttributedString {
        TranscriptTextRenderer.attributedText(
            for: TranscriptBlock(id: "line-break-test", kind: kind, text: text),
            textSize: .standard, accentColor: .orange, appearance: .dark
        )
    }

    @Test
    @MainActor
    func userMessageGlyphsFitInsideTheirNativeView() throws {
        _ = NSApplication.shared
        let item = TranscriptCollectionItem()
        let root = item.view
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1000, height: 800),
            styleMask: [.borderless], backing: .buffered, defer: false
        )
        window.contentView?.addSubview(root)
        let messages = [
            "push到云端了吗？",
            "请提交必要文件。并commit push。然后将git仓库打包（只要git追踪的部分）。",
            "First line\nSecond line\n最后一行",
            "中文 English العربية",
        ]
        for width: CGFloat in [320, 640, 920] {
            for size in AppTextSize.allCases {
                for message in messages {
                    let block = TranscriptBlock(id: "layout", kind: .user, text: message)
                    let height = TranscriptCollectionItem.preferredHeight(
                        for: block, language: .chinese, textSize: size,
                        accentColor: .orange, appearance: .dark, availableWidth: width
                    )
                    root.frame = .zero
                    item.configure(
                        block: block, language: .chinese, textSize: size,
                        accentColor: .orange, appearance: .dark,
                        findQuery: "", currentFindOccurrence: nil, availableWidth: width,
                        openURL: { _ in }, activateBlock: { _ in }
                    )
                    root.frame = NSRect(x: 0, y: 0, width: width, height: height)
                    root.layoutSubtreeIfNeeded()
                    let bitmap = try #require(root.bitmapImageRepForCachingDisplay(in: root.bounds))
                    root.cacheDisplay(in: root.bounds, to: bitmap)
                    let textView = try #require(descendants(root).compactMap { $0 as? NSTextView }.first)
                    let container = try #require(textView.textContainer)
                    let layout = try #require(textView.layoutManager)
                    layout.ensureLayout(for: container)
                    let used = layout.usedRect(for: container)
                    let freshHeight = TranscriptTextRenderer.textHeight(textView.attributedString(), width: container.containerSize.width)
                    let original = TranscriptTextRenderer.attributedText(for: block, textSize: size, accentColor: .orange, appearance: .dark)
                    let originalHeight = TranscriptTextRenderer.textHeight(original, width: container.containerSize.width)
                    let diagnostic = "message=\(message) width=\(width) size=\(size) row=\(root.frame) text=\(textView.frame) container=\(container.containerSize) used=\(used) freshHeight=\(freshHeight) originalHeight=\(originalHeight) equal=\(original.isEqual(to: textView.attributedString())) origin=\(textView.textContainerOrigin)"
                    #expect(originalHeight == freshHeight, Comment(rawValue: diagnostic))
                    #expect(
                        used.maxY + textView.textContainerOrigin.y <= textView.bounds.height + 0.5,
                        Comment(rawValue: diagnostic)
                    )
                    #expect(used.maxX <= textView.bounds.width + 0.5, Comment(rawValue: diagnostic))
                }
            }
        }
        window.contentView = nil
    }

    @MainActor
    private func descendants(_ view: NSView) -> [NSView] {
        view.subviews.flatMap { [$0] + descendants($0) }
    }

    @Test
    @MainActor
    func answerLinksKeepRelativeReferencesInNativeDelegate() throws {
        _ = NSApplication.shared
        let item = TranscriptCollectionItem()
        let block = TranscriptBlock(id: "answer-links", kind: .output,
            text: "[review.txt](review.txt) and [preview.png](preview.png)")
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 800),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView?.addSubview(item.view)
        defer { window.contentView = nil }
        var opened: [URL] = []
        item.configure(block: block, language: .english, textSize: .standard,
            accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 920, openURL: { opened.append($0) }, activateBlock: { _ in })
        item.view.frame = NSRect(x: 0, y: 600, width: 920, height: 100)
        item.view.layoutSubtreeIfNeeded()
        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
        for name in ["review.txt", "preview.png"] {
            let range = (text.string as NSString).range(of: name)
            let link = try #require(text.textStorage?.attribute(.link, at: range.location, effectiveRange: nil))
            text.clicked(onLink: link, at: range.location)
        }
        #expect(opened.map(\.relativeString) == ["review.txt", "preview.png"])
    }

    @Test
    @MainActor
    func operationMouseTargetsRespectTheSharedReadingColumn() throws {
        _ = NSApplication.shared
        let operation = WorkOperationRecord(id: "read", eventId: 1, kind: .fileRead,
            status: .completed, targets: [WorkOperationTarget(path: "review.txt")], detailsAvailable: true)
        let block = TranscriptBlock(id: "read", kind: .command, text: "Read review.txt", operation: operation)
        let item = TranscriptCollectionItem()
        _ = item.view
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 2000, height: 1000),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView?.addSubview(item.view)
        defer { window.contentView = nil }
        var activations = 0
        for width in [CGFloat(320), 920, 1800] {
            item.configure(block: block, language: .english, textSize: .standard,
                accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                availableWidth: width, openURL: { _ in }, activateBlock: { _ in activations += 1 })
            item.view.frame = NSRect(x: 0, y: 700, width: width, height:
                TranscriptCollectionItem.preferredHeight(for: block, textSize: .standard,
                    accentColor: .orange, appearance: .dark, availableWidth: width))
            item.view.layoutSubtreeIfNeeded()
            let body = try #require(descendants(item.view).first { $0.accessibilityRole() == .button })
            let text = try #require(descendants(body).compactMap { $0 as? NSTextView }.first)
            let layout = try #require(text.layoutManager)
            let container = try #require(text.textContainer)
            layout.ensureLayout(for: container)
            let prefix = layout.boundingRect(forGlyphRange: NSRange(location: 0, length: 1), in: container)
            let prefixPoint = text.convert(NSPoint(x: prefix.midX, y: prefix.midY), to: body.superview)
            #expect(body.hitTest(prefixPoint) === body)
            #expect(body.hitTest(prefixPoint)?.accessibilityPerformPress() == true)
            let link = (text.string as NSString).range(of: "review.txt")
            let linkGlyphs = layout.glyphRange(forCharacterRange: link, actualCharacterRange: nil)
            let linkRect = layout.boundingRect(forGlyphRange: linkGlyphs, in: container)
            let linkPoint = text.convert(NSPoint(x: linkRect.midX, y: linkRect.midY), to: body.superview)
            #expect(body.hitTest(linkPoint) === text)
        }
        #expect(activations == 3)
    }

    @Test
    @MainActor
    func repliesUseFullColumnWhileWorkTextKeepsItsIconIndentAcrossResize() throws {
        _ = NSApplication.shared
        let blocks = [
            TranscriptBlock(id: "answer", kind: .output, text: "The current response."),
            TranscriptBlock(id: "historical-answer", kind: .output, text: "An earlier response.\n\nIts second paragraph.", isHistorical: true),
            TranscriptBlock(id: "reasoning", kind: .thought, text: "Planning single sleep 90 exec avoiding file reads\nSetting exec command with no login shell"),
            TranscriptBlock(id: "progress", kind: .output, text: "Checking the layout and message roles.", activityType: "assistant_progress"),
            TranscriptBlock(id: "command", kind: .command, text: "sleep 90", status: "completed"),
            TranscriptBlock(id: "summary", kind: .activity, text: "Agent work", activityType: "work_summary"),
            TranscriptBlock(id: "activity", kind: .activity, text: "Reading a file", activityType: "file_read"),
            TranscriptBlock(id: "file-operation", kind: .command, text: "Read review.txt", operation:
                WorkOperationRecord(id: "read-1", eventId: 1, kind: .fileRead, status: .completed,
                    targets: [WorkOperationTarget(path: "review.txt")])),
            TranscriptBlock(id: "warning", kind: .warning, text: "Request timed out."),
            TranscriptBlock(id: "error", kind: .error, text: "Connection unavailable."),
        ]
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1600, height: 800),
            styleMask: [.borderless], backing: .buffered, defer: false
        )
        defer { window.contentView = nil }
        let items = blocks.map { _ in TranscriptCollectionItem() }
        for item in items { window.contentView?.addSubview(item.view) }
        for appearance in [TranscriptAppearance.light, .dark] {
            for size in AppTextSize.allCases {
                for (index, width) in [CGFloat(920), 320, 1400, 640, 920].enumerated() {
                    var textOrigins: [CGFloat] = []
                    for (block, item) in zip(blocks, items) {
                        let height = TranscriptCollectionItem.preferredHeight(
                            for: block, textSize: size, accentColor: .orange,
                            appearance: appearance, availableWidth: width
                        )
                        item.configure(
                            block: block, language: .english, textSize: size,
                            accentColor: .orange, appearance: appearance,
                            findQuery: "", currentFindOccurrence: nil, availableWidth: width,
                            openURL: { _ in }, activateBlock: { _ in }
                        )
                        if index > 0 {
                            item.updateAvailableWidth(width)
                        }
                        item.view.frame = NSRect(x: 0, y: 0, width: width, height: height)
                        item.view.layoutSubtreeIfNeeded()
                        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                        let frame = text.convert(text.bounds, to: item.view)
                        textOrigins.append(frame.minX)
                        #expect(frame.maxX <= width + 0.5)
                        #expect(frame.minX >= 0)
                        let icon = try #require(item.view.subviews.compactMap { $0 as? NSImageView }.first)
                        if block.kind == .thought || block.isAssistantProgress || block.operation != nil {
                            #expect(!icon.isHidden)
                            let layout = try #require(text.layoutManager)
                            let line = layout.lineFragmentRect(forGlyphAt: 0, effectiveRange: nil)
                            let firstLine = text.convert(line.offsetBy(
                                dx: text.textContainerOrigin.x, dy: text.textContainerOrigin.y
                            ), to: item.view)
                            // SF Symbols include optical padding outside AppKit's alignment rect.
                            let iconAlignment = icon.alignmentRect(forFrame: icon.frame)
                            // AppKit snaps image frames to device pixels on 1x and 2x displays.
                            let halfPixel = 0.5 / window.backingScaleFactor
                            #expect(abs(iconAlignment.midY - firstLine.midY) <= halfPixel + 0.001,
                                "\(block.id), \(size), \(window.backingScaleFactor)x, icon \(iconAlignment), line \(firstLine)")
                        }
                        if !icon.isHidden {
                            #expect(icon.frame.minX >= 0)
                            #expect(icon.frame.maxX < frame.minX)
                            #expect(abs(icon.alignmentRect(forFrame: icon.frame).minX - textOrigins[0]) < 0.5)
                        }
                    }
                    let expected = try #require(textOrigins.first)
                    for (block, origin) in zip(blocks, textOrigins) {
                        let expectedIndent: CGFloat = block.kind == .output && !block.isAssistantProgress ? 0 : 40
                        #expect(abs(origin - expected - expectedIndent) < 0.5,
                                "\(block.kind) at width \(width): text starts at \(origin), answer starts at \(expected)")
                    }
                }
            }
        }
    }
}
