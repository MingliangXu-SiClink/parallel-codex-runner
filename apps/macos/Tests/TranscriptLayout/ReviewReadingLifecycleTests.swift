import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ReviewReadingLifecycleTests {
    @Test(arguments: [0, 1, 2])
    func reopeningHighlightedFileKeepsItsReadingAnchor(location: Int) async throws {
        _ = NSApplication.shared
        let text = "diff --git a/long.py b/long.py\n--- a/long.py\n+++ b/long.py\n@@ -1,5200 +1,5201 @@\n" +
            (0..<5200).map { " print(\"line \(String(format: "%05d", $0)) 中文 full context retained\")\n" }.joined() +
            "+print(\"END-OF-FILE\")\n"
        var saved: ReviewReadingPosition?
        func root(_ visible: Bool) -> AnyView {
            AnyView(VStack(spacing: 0) {
                if visible {
                    Text("File").frame(height: 52)
                    ReviewTextView(text: text, isDiff: true, fontSize: 13, filePath: "long.py",
                        initialPosition: saved, onSavePosition: { saved = $0 })
                    Text("Copy").frame(height: 44)
                } else { Color.clear }
            }.frame(maxWidth: .infinity, maxHeight: .infinity))
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 560),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let host = NSHostingView(rootView: root(true))
        window.contentView = host
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        func ready() async throws -> ReviewScrollView {
            for _ in 0..<300 {
                host.layoutSubtreeIfNeeded()
                if let scroll = descendants(host).compactMap({ $0 as? ReviewScrollView }).first,
                   let editor = scroll.documentView as? NSTextView,
                   (editor.textStorage?.attribute(.font, at: text.utf16.count - 4,
                        effectiveRange: nil) as? NSFont)?.fontName.hasPrefix("Menlo") == true,
                   let color = editor.textStorage?.attribute(.foregroundColor,
                        at: (text as NSString).range(of: "line 00000").location, effectiveRange: nil) as? NSColor,
                   !color.isEqual(NSColor.labelColor),
                   scroll.contentSize.height > 100 {
                    scroll.layoutSubtreeIfNeeded()
                    return scroll
                }
                try await Task.sleep(for: .milliseconds(10))
            }
            throw NSError(domain: "ReviewReadingLifecycleTests", code: 1)
        }
        let first = try await ready()
        let editor = try #require(first.documentView as? NSTextView)
        let bottom = editor.frame.height - first.contentSize.height
        first.contentView.scroll(to: NSPoint(x: 0,
            y: location == 0 ? 40_000 : bottom - (location == 2 ? 54 : 0)))
        first.reflectScrolledClipView(first.contentView)
        editor.setSelectedRange(NSRange(location: 100, length: 12))
        let expected = try #require(first.readingPosition())
        #expect(expected.atBottom == (location == 1))
        for _ in 0..<3 {
            saved = nil
            host.rootView = root(false)
            host.layoutSubtreeIfNeeded()
            for _ in 0..<100 where saved == nil { try await Task.sleep(for: .milliseconds(10)) }
            let stored = try #require(saved)
            #expect(stored.anchor == expected.anchor)
            #expect(abs(stored.lineOffset - expected.lineOffset) < 1)
            #expect(stored.atBottom == expected.atBottom)
            host.rootView = root(true)
            let reopened = try await ready()
            let actual = try #require(reopened.readingPosition())
            #expect(actual.atBottom == expected.atBottom)
            #expect(actual.anchor == expected.anchor)
            #expect(abs(actual.lineOffset - expected.lineOffset) < 1)
            #expect(actual.selection == expected.selection)
            #expect(abs(actual.origin.y - expected.origin.y) < 1)
        }
    }

    @Test(arguments: [false, true])
    func initialZeroViewportDoesNotConsumeReadingIntent(atBottom: Bool) throws {
        let scroll = ReviewScrollView(frame: .zero)
        let editor = ReviewNSTextView(frame: .zero)
        editor.textContainer?.widthTracksTextView = false
        editor.textContainer?.containerSize = NSSize(width: 1_000_000, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainerInset = NSSize(width: 16, height: 14)
        editor.font = ReviewSyntaxRequest.font(ofSize: 13)
        editor.string = (0..<5200).map { "Line \($0)" }.joined(separator: "\n")
        scroll.documentView = editor
        scroll.resizeDocumentToFitContent()
        let intent = ReviewReadingPosition(origin: NSPoint(x: 0, y: 40_000),
            selection: NSRange(location: 0, length: 0), atBottom: atBottom)
        scroll.restoreReadingPosition(intent)
        #expect(scroll.readingPosition()?.origin == intent.origin)
        scroll.setFrameSize(NSSize(width: 640, height: 460))
        scroll.layoutSubtreeIfNeeded()
        let actual = try #require(scroll.readingPosition())
        #expect(actual.atBottom == atBottom)
        #expect(abs(actual.origin.y - (atBottom ? editor.frame.height - scroll.contentSize.height : intent.origin.y)) < 1)

        scroll.contentView.scroll(to: NSPoint(x: 0, y: 12_000))
        let userPosition = try #require(scroll.readingPosition())
        scroll.preservingReadingPosition {
            scroll.setFrameSize(NSSize(width: 320, height: 500))
            editor.font = ReviewSyntaxRequest.font(ofSize: 16)
            scroll.layout()
        }
        #expect(scroll.readingPosition()?.anchor == userPosition.anchor)
        #expect(scroll.readingPosition()?.atBottom == false)
    }

    @Test(arguments: [0, 1, 2])
    func endingNativeLiveResizeDoesNotReplacePreciseReadingPosition(location: Int) throws {
        let scroll = ReviewScrollView(frame: NSRect(x: 0, y: 0, width: 320, height: 475.5))
        let editor = ReviewNSTextView(usingTextLayoutManager: false)
        editor.textContainer?.widthTracksTextView = false
        editor.textContainer?.containerSize = NSSize(width: 1_000_000, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainerInset = NSSize(width: 16, height: 14)
        editor.font = ReviewSyntaxRequest.font(ofSize: 13)
        editor.string = (0..<5200).map { "print(\"line \($0) 中文 full context retained\")" }.joined(separator: "\n")
        editor.isEditable = false
        editor.isSelectable = true
        scroll.documentView = editor
        scroll.layoutSubtreeIfNeeded()
        scroll.resizeDocumentToFitContent()
        let bottom = editor.frame.height - scroll.contentSize.height
        scroll.contentView.scroll(to: NSPoint(x: 10,
            y: location == 0 ? 40_007.5 : bottom - (location == 2 ? 54 : 0)))
        let expected = try #require(scroll.readingPosition())
        editor.viewWillStartLiveResize()
        scroll.setFrameSize(NSSize(width: 360, height: 440.5))
        scroll.setFrameSize(NSSize(width: 320, height: 475.5))
        scroll.restoreReadingPosition(expected)
        // A scroll during resize supersedes the initial position.
        scroll.contentView.scroll(to: NSPoint(x: 12, y: expected.origin.y - 36))
        let latest = try #require(scroll.readingPosition())
        editor.viewDidEndLiveResize()
        #expect(scroll.readingPosition()?.origin == latest.origin)
        scroll.restoreReadingPosition(expected)
        editor.viewDidEndLiveResize()
        let actual = try #require(scroll.readingPosition())
        #expect(actual.atBottom == expected.atBottom)
        #expect(actual.anchor == expected.anchor)
        #expect(abs(actual.origin.x - expected.origin.x) < 1)
        #expect(abs(actual.origin.y - expected.origin.y) < 1)
    }
}
