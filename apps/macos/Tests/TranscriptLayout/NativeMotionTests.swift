import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct NativeMotionTests {
    @Test
    func navigationCollapsesContinuouslyWithoutReplacingItsPanes() async throws {
        let controller = NavigationSplitColumns<Text, Text>.Controller(
            sidebar: Text("Sidebar"), content: Text("Conversation"), showsSidebar: true)
        let window = makeWindow()
        defer { window.close() }
        window.contentViewController = controller
        window.makeKeyAndOrderFront(nil)
        await settle()
        controller.splitView.setPosition(280, ofDividerAt: 0)
        controller.view.layoutSubtreeIfNeeded()
        let before = displayedX(controller.content, in: controller.view)
        let sidebar = controller.sidebar
        let content = controller.content
        PaneMotion.setCollapsed([(controller.sidebarItem, true)], in: controller.view, animated: true)
        try await Task.sleep(for: .milliseconds(65))
        let middle = displayedX(controller.content, in: controller.view)
        #expect(middle > 1 && middle < before - 1)
        await settle()
        #expect(displayedX(controller.content, in: controller.view) < 1)
        #expect(controller.splitViewItems.count == 2)
        PaneMotion.setCollapsed([(controller.sidebarItem, false)], in: controller.view, animated: true)
        await settle()
        #expect(controller.sidebar === sidebar && controller.content === content)
        #expect(abs(displayedX(controller.content, in: controller.view) - before) < 2)
    }

    @Test
    func inspectorKeepsAllColumnsAndDividerWidthAcrossReopen() async {
        let controller = WorkbenchSplitColumns<Text, Text, Text>.Controller(
            conversation: Text("Conversation"), results: Text("Results"), review: Text("File"))
        let layout = WorkbenchColumnsLayout(availableWidth: 1400, scale: 1, showsResults: true, showsReview: true)
        controller.update(layout: layout, showsResults: true, showsReview: true)
        let window = makeWindow(width: 1400)
        defer { window.close() }
        window.contentViewController = controller
        window.makeKeyAndOrderFront(nil)
        await settle()
        controller.splitView.setPosition(600, ofDividerAt: 0)
        controller.splitView.setPosition(950, ofDividerAt: 1)
        controller.view.layoutSubtreeIfNeeded()
        let width = controller.results.frame.width
        let identities = controller.splitViewItems
        controller.update(layout: layout, showsResults: false, showsReview: true)
        await settle()
        #expect(controller.splitViewItems == identities)
        #expect(controller.splitViewItems[1].isCollapsed)
        controller.update(layout: layout, showsResults: true, showsReview: true)
        await settle()
        #expect(abs(controller.results.frame.width - width) < 2)
        #expect(controller.splitViewItems == identities)
    }

    @Test
    func reversingPaneMotionDoesNotLeaveACollapsedOrDetachedPane() async throws {
        let controller = NavigationSplitColumns<Text, Text>.Controller(
            sidebar: Text("Sidebar"), content: Text("Conversation"), showsSidebar: true)
        let window = makeWindow()
        defer { window.close() }
        window.contentViewController = controller
        window.makeKeyAndOrderFront(nil)
        await settle()
        for collapsed in [true, false, true, false] {
            PaneMotion.setCollapsed([(controller.sidebarItem, collapsed)], in: controller.view, animated: true)
            try await Task.sleep(for: .milliseconds(45))
        }
        await settle()
        #expect(!controller.sidebarItem.isCollapsed)
        #expect(controller.sidebar.frame.width >= ParallelCodexVisualStyle.Layout.sidebarMinimum)
        #expect(displayedX(controller.content, in: controller.view) > 100)
        #expect(controller.splitViewItems.count == 2)
    }

    @Test
    func followMotionIsContinuousRetargetableAndCancellable() async throws {
        let window = makeWindow()
        defer { window.close() }
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 800, height: 500))
        let document = FlippedView(frame: NSRect(x: 0, y: 0, width: 800, height: 2500))
        scroll.documentView = document
        window.contentView = scroll
        window.makeKeyAndOrderFront(nil)
        let motion = NativeScrollMotion()
        motion.move(to: NSPoint(x: 0, y: 600), in: scroll, animated: true)
        try await Task.sleep(for: .milliseconds(55))
        let middle = scroll.contentView.bounds.minY
        #expect(middle > 0 && middle < 600)
        motion.move(to: NSPoint(x: 0, y: 900), in: scroll, animated: true)
        #expect(abs(scroll.contentView.bounds.minY - middle) < 1)
        try await Task.sleep(for: .milliseconds(40))
        motion.cancel()
        let cancelled = scroll.contentView.bounds.minY
        await settle()
        #expect(abs(scroll.contentView.bounds.minY - cancelled) < 1)
        motion.move(to: NSPoint(x: 0, y: 100), in: scroll, animated: false)
        #expect(!motion.isAnimating)
        #expect(abs(scroll.contentView.bounds.minY - 100) < 1)
    }

    @Test
    func streamedTextKeepsItsPrefixButAppliesMarkdownRestyling() {
        let font = NSFont.systemFont(ofSize: 15)
        let storage = NSTextStorage(string: "First", attributes: [.font: font])
        TranscriptCollectionItem.updateText(storage, to: NSAttributedString(string: "First next", attributes: [.font: font]))
        #expect(storage.string == "First next")
        let restyled = NSAttributedString(string: "First next", attributes: [.font: NSFont.boldSystemFont(ofSize: 15)])
        TranscriptCollectionItem.updateText(storage, to: restyled)
        #expect(storage.isEqual(to: restyled))
        TranscriptCollectionItem.updateText(storage, to: NSAttributedString(string: "short"))
        #expect(storage.string == "short")
    }

    private func settle() async { try? await Task.sleep(for: .milliseconds(350)) }

    private func displayedX(_ view: NSView, in root: NSView) -> CGFloat {
        view.convert(view.bounds, to: root).minX
    }

    private func makeWindow(width: CGFloat = 1100) -> NSWindow {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 680),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }

    private final class FlippedView: NSView {
        override var isFlipped: Bool { true }
    }
}
