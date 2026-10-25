import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct ReviewPanelsTests {
    @MainActor @Test func retryPhasesKeepNativeColumnsWidthsFocusAndReadingPosition() async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-panel-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("long.txt")
        let content = (0..<4000).map { "line \($0) for native reading position" }.joined(separator: "\n")
        try Data(content.utf8).write(to: file)
        let client = PcrdClient(socketURL: directory.appendingPathComponent("missing-socket"),
                               tokenURL: directory.appendingPathComponent("missing-token"))
        let model = AppModel(client: client, windowID: UUID())
        defer { model.stopWindowObservation() }
        model.workspaces = [RemoteWorkspace(workspaceId: "retry-layout", path: directory.path, name: "QA",
            lastRunId: "", lastResumeSessionId: "", runStatus: nil, runWorkerActive: nil,
            runAgentsTotal: nil, runAgentsFinished: nil, runAgentsFailed: nil,
            selected: true, available: true, readable: true, writable: true, settings: nil)]
        model.selectedWorkspaceID = "retry-layout"
        model.resultsInspectorVisible = true
        model.prompt = "Keep this draft and focus through retry."
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        let identity = try #require(model.workspaceDocument?.id)
        let host = NSHostingView(rootView: WorkspaceWorkbench().environmentObject(model))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1600, height: 900),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        func settle() async throws {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(100))
            host.layoutSubtreeIfNeeded()
        }
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        try await settle()
        let split = try #require(descendants(host).compactMap { $0 as? NSSplitView }
            .first { $0.isVertical && $0.arrangedSubviews.count == 3 })
        split.setPosition(720, ofDividerAt: 0)
        split.setPosition(1040, ofDividerAt: 1)
        try await settle()
        let frames = split.arrangedSubviews.map(\.frame)
        let scroll = try #require(descendants(host).compactMap { $0 as? ReviewScrollView }.first)
        scroll.contentView.scroll(to: NSPoint(x: 20, y: 1500.5))
        let position = try #require(scroll.readingPosition())
        let composer = try #require(descendants(host).compactMap { $0 as? NSTextView }.first { $0.isEditable })
        #expect(window.makeFirstResponder(composer))
        composer.setSelectedRange(NSRange(location: 5, length: 4))
        for _ in 0..<3 {
            model.workspaceFileIssue = WorkspaceFileIssue(reference: file.path, message: "Previous failure", retry: {})
            try await settle()
            model.workspaceFileLoading = true
            model.workspaceFileIssue = nil
            try await settle()
            #expect(split.arrangedSubviews.map(\.frame) == frames)
            #expect(window.firstResponder === composer)
            #expect(composer.selectedRange() == NSRange(location: 5, length: 4))
            model.workspaceFileLoading = false
            try await settle()
        }
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        try await settle()
        #expect(model.workspaceDocument?.id == identity)
        #expect(descendants(host).contains { $0 === scroll })
        #expect(abs(scroll.contentView.bounds.origin.y - position.origin.y) < 1)
        #expect(model.prompt == "Keep this draft and focus through retry.")
        model.diffPresented = true
        model.dismissWorkspaceDocument()
        try await settle()
        #expect(split.arrangedSubviews.map(\.frame) == frames)
        #expect(model.reviewColumnPresented)
    }

    @MainActor @Test func columnHeadersStayBelowTheConversationHeader() async throws {
        _ = NSApplication.shared
        let header = NSView(), column = NSView()
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
            styleMask: [.titled, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.titlebarAppearsTransparent = true
        defer { window.close() }
        let host = NSHostingView(rootView: HSplitView {
            Color.clear.frame(width: 240)
            VStack(spacing: 0) {
                LayoutProbe(view: header).frame(height: 52)
                GeometryReader { viewport in
                    WorkbenchHorizontalScroll(size: viewport.size) {
                        HSplitView {
                            Color.clear.frame(minWidth: 360).frame(height: viewport.size.height)
                            VStack(spacing: 0) {
                                LayoutProbe(view: column).frame(height: 52)
                                Color.clear
                            }.frame(minWidth: 260, idealWidth: 300, maxWidth: 440)
                                .frame(height: viewport.size.height).clipped()
                        }.frame(width: viewport.size.width, height: viewport.size.height)
                    }.clipped()
                }
            }.ignoresSafeArea(.container, edges: .top)
        }.toolbar {
            ToolbarItem(placement: .navigation) { Button("Sidebar") {} }
        }.toolbarBackground(.hidden, for: .windowToolbar))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(100))
        host.layoutSubtreeIfNeeded()
        let first = header.convert(header.bounds, to: host)
        let second = column.convert(column.bounds, to: host)
        #expect(!first.intersects(second))
        #expect(abs(second.minY - first.maxY) < 1)
    }
    @Test func fractionalQuotaDoesNotInventPrecision() {
        func label(_ used: Double?) -> String? {
            CodexUsageWindow(id: "weekly", usedPercent: used, windowMinutes: 10080, resetsAt: nil).remainingLabel
        }
        #expect(label(1.24) == "98.76%")
        #expect(label(12) == "88%")
        #expect(label(100) == "0%")
        #expect(label(nil) == nil)
        #expect(label(.nan) == nil)
        #expect(label(.infinity) == nil)
    }

    @Test func processGroupingKeepsAnswersOutsideAndSearchesAllOperations() {
        let work = (0..<100).map { TranscriptBlock(id: "work-\($0)", kind: .thought, text: "step \($0)") }
        let blocks = TranscriptFormatter.blocksWithWorkDisclosure(work + [
            TranscriptBlock(id: "answer", kind: .output, text: "Final", isFinalAnswer: true)
        ], runID: "test", agent: 1, expanded: true)
        let items = ConversationTimelineProjection.make(blocks: blocks, switchState: nil)
        #expect(items.count == 3)
        guard case .workProcess(let process) = items[1] else { Issue.record("Expected bounded work group"); return }
        #expect(process.blocks.count == 100)
        #expect(items.last?.id == "message:answer")
        let found = ConversationFindIndex.matches(in: items, query: "step 99")
        #expect(found == [ConversationFindMatch(itemID: "message:work-99", occurrence: 0)])
        #expect(items[1].containsMessage(id: "message:work-99"))
    }

    @Test func reviewColumnsNeverReplaceResultDetails() {
        for scale in [1.0, 1.4] {
            for width in [640.0, 1240.0, 2000.0] {
                let value = WorkbenchColumnsLayout(availableWidth: width, scale: scale,
                                                   showsResults: true, showsReview: true)
                #expect(value.contentWidth >= width)
                #expect(value.contentWidth >= value.conversationMinimum + value.resultsMinimum + value.reviewMinimum + 2)
            }
        }
    }

    @MainActor @Test func narrowColumnsRevealANewFileWithoutOverridingManualScrolling() {
        let scroll = WorkbenchColumnScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 480))
        scroll.documentView = NSView(frame: NSRect(x: 0, y: 0, width: 940, height: 480))
        scroll.updateReviewVisibility(true)
        scroll.layoutSubtreeIfNeeded()
        #expect(abs(scroll.documentVisibleRect.maxX - 940) < 1)
        scroll.contentView.scroll(to: NSPoint(x: 50, y: 0))
        scroll.updateReviewVisibility(true)
        scroll.layoutSubtreeIfNeeded()
        #expect(abs(scroll.documentVisibleRect.minX - 50) < 1)
        scroll.documentView?.setFrameSize(NSSize(width: 640, height: 480))
        scroll.updateReviewVisibility(false)
        scroll.layoutSubtreeIfNeeded()
        #expect(scroll.documentVisibleRect.minX == 0)
    }

    @MainActor @Test func closingHostedReviewRestoresTheConversationOrigin() async throws {
        _ = NSApplication.shared
        let conversation = NSView()
        func columns(_ review: Bool, results: Bool = true) -> some View {
            let width: CGFloat = review ? 942 : 640
            return WorkbenchHorizontalScroll(size: CGSize(width: width, height: 480), showsReview: review) {
                HSplitView {
                    LayoutProbe(view: conversation).frame(minWidth: 360).layoutPriority(1)
                    if results {
                        Color.clear.frame(minWidth: 260, idealWidth: 300, maxWidth: 440).layoutPriority(-1)
                    }
                    if review {
                        Color.clear.frame(minWidth: 320, idealWidth: 440, maxWidth: 800).layoutPriority(-1)
                    }
                }.frame(width: width, height: 480)
            }
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 480),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let host = NSHostingView(rootView: columns(true))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let scroll = try #require(descendants(host).compactMap { $0 as? WorkbenchColumnScrollView }.first)
        #expect(scroll.contentView.bounds.minX > 200)
        host.rootView = columns(false)
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        host.layoutSubtreeIfNeeded()
        #expect(scroll.contentView.bounds.minX == 0)
        #expect(scroll.documentView?.frame.width == 640)
        #expect(conversation.convert(conversation.bounds, to: host).minX >= 0)
        host.rootView = columns(false, results: false)
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        host.layoutSubtreeIfNeeded()
        #expect(abs(conversation.convert(conversation.bounds, to: host).minX) < 1)
        #expect(abs(conversation.frame.width - 640) < 1)
    }

    @Test func fileSyntaxAndDiffSyntaxSurviveInBothAppearances() async throws {
        for dark in [false, true] {
            let source = "def answer(value):\n    return \"你好\" if value else 42\n"
            let request = ReviewSyntaxRequest(text: source, path: "example.py", isDiff: false, fontSize: 13, dark: dark)
            let rendered = await ReviewSyntaxRenderer.shared.render(request).value
            #expect(rendered.string == source)
            let keyword = rendered.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor
            let name = rendered.attribute(.foregroundColor, at: 4, effectiveRange: nil) as? NSColor
            #expect(keyword != name)
            let patch = "diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n@@ -1,2 +1,2 @@\n def answer(value):\n-    return 1\n+    return 42\n"
            let diff = await ReviewSyntaxRenderer.shared.render(.init(
                text: patch, path: "example.py", isDiff: true, fontSize: 13, dark: dark)).value
            #expect(diff.string == patch)
            let deletion = (patch as NSString).range(of: "-    return 1").location
            let addition = (patch as NSString).range(of: "+    return 42").location
            #expect(diff.attribute(.backgroundColor, at: addition, effectiveRange: nil) != nil)
            #expect(diff.attribute(.backgroundColor, at: deletion, effectiveRange: nil) != nil)
            #expect(diff.attribute(.foregroundColor, at: addition + 5, effectiveRange: nil) as? NSColor == keyword)
        }
    }

    @MainActor @Test func closingReviewAndResultsKeepsTheLiveComposerInsideTheWindow() async throws {
        _ = NSApplication.shared
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.workspaces = [RemoteWorkspace(workspaceId: "review-layout", path: "/qa", name: "QA",
            lastRunId: "", lastResumeSessionId: "", runStatus: nil, runWorkerActive: nil,
            runAgentsTotal: nil, runAgentsFinished: nil, runAgentsFailed: nil,
            selected: true, available: true, readable: true, writable: true, settings: nil)]
        model.selectedWorkspaceID = "review-layout"
        model.resultsInspectorVisible = true
        model.prompt = "Keep this draft and its editor while closing side panels."
        let host = NSHostingView(rootView: WorkspaceWorkbench().environmentObject(model))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 741, height: 900),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        func settle() async throws {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(100))
            host.layoutSubtreeIfNeeded()
        }
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        try await settle()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        #expect(window.makeFirstResponder(editor))
        editor.setSelectedRange(NSRange(location: 5, length: 3))
        for _ in 0..<2 {
            model.resultsInspectorVisible = true
            model.diffPresented = true
            try await settle()
            model.diffPresented = false
            try await settle()
            model.resultsInspectorVisible = false
            try await settle()
            let frame = editor.convert(editor.bounds, to: host)
            #expect(frame.minX >= 0 && frame.maxX <= 741)
            #expect(abs(frame.midX - 741 / 2) < 16)
            #expect(descendants(host).contains { $0 === editor })
            #expect(window.firstResponder === editor)
            #expect(editor.selectedRange() == NSRange(location: 5, length: 3))
        }
        window.setContentSize(NSSize(width: 1600, height: 900))
        model.resultsInspectorVisible = true
        model.diffPresented = true
        try await settle()
        let split = try #require(descendants(host).compactMap { $0 as? NSSplitView }
            .first { $0.isVertical && $0.arrangedSubviews.count == 3 })
        let file = split.arrangedSubviews[2]
        #expect(file.frame.width >= 400)
        split.setPosition(split.arrangedSubviews[0].frame.width - 80, ofDividerAt: 0)
        let moved = split.arrangedSubviews[0].frame.width
        model.prompt += " Still editing."
        try await settle()
        #expect(abs(split.arrangedSubviews[0].frame.width - moved) < 1)
        window.setContentSize(NSSize(width: 741, height: 900))
        try await settle()
        let horizontal = try #require(descendants(host).compactMap { $0 as? WorkbenchColumnScrollView }.first)
        #expect(split.frame.width <= horizontal.documentView!.frame.width)
        #expect(file.frame.maxX <= horizontal.documentView!.frame.width)
    }

    @Test func patchLikeStringsInSourceAreNotMistakenForHeaders() {
        let patch = "@@ -1,2 +1,2 @@\n---literal\n+++literal\n common\n"
        let lines = ReviewDiffSyntax.lines(patch)
        #expect(lines.map(\.kind) == [nil, "-", "+", " "])
    }
}

private struct LayoutProbe: NSViewRepresentable {
    let view: NSView
    func makeNSView(context: Context) -> NSView { view }
    func updateNSView(_ view: NSView, context: Context) {}
}
