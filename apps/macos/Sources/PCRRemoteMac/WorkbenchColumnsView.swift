import AppKit
import SwiftUI

@MainActor
enum PaneMotion {
    static let duration: TimeInterval = 0.22

    static func setCollapsed(_ changes: [(NSSplitViewItem, Bool)], in view: NSView, animated: Bool) {
        let changes = changes.filter { $0.0.isCollapsed != $0.1 }
        guard !changes.isEmpty else { return }
        let animate = animated && view.window?.isVisible == true
            && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        NSAnimationContext.runAnimationGroup { context in
            context.duration = animate ? duration : 0
            context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            for (item, collapsed) in changes {
                if animate {
                    let animation = CABasicAnimation()
                    animation.duration = duration
                    animation.timingFunction = context.timingFunction
                    item.animations = ["collapsed": animation]
                    item.animator().isCollapsed = collapsed
                } else {
                    item.isCollapsed = collapsed
                }
            }
        }
    }

    static func item<V: View>(_ host: NSHostingView<V>) -> NSSplitViewItem {
        host.sizingOptions = []
        if #available(macOS 13.3, *) { host.safeAreaRegions = [] }
        let controller = NSViewController()
        controller.view = host
        let item = NSSplitViewItem(viewController: controller)
        item.collapseBehavior = .preferResizingSiblingsWithFixedSplitView
        item.canCollapseFromWindowResize = false
        return item
    }
}

struct NavigationSplitColumns<Sidebar: View, Content: View>: NSViewControllerRepresentable {
    let showsSidebar: Bool
    @ViewBuilder let sidebar: Sidebar
    @ViewBuilder let content: Content

    func makeNSViewController(context: Context) -> Controller {
        Controller(sidebar: sidebar, content: content, showsSidebar: showsSidebar)
    }

    func updateNSViewController(_ controller: Controller, context: Context) {
        controller.sidebar.rootView = sidebar
        controller.content.rootView = content
        PaneMotion.setCollapsed([(controller.sidebarItem, !showsSidebar)], in: controller.view, animated: true)
    }

    final class Controller: NSSplitViewController {
        let sidebar: NSHostingView<Sidebar>
        let content: NSHostingView<Content>
        let sidebarItem: NSSplitViewItem

        init(sidebar: Sidebar, content: Content, showsSidebar: Bool) {
            self.sidebar = NSHostingView(rootView: sidebar)
            self.content = NSHostingView(rootView: content)
            sidebarItem = PaneMotion.item(self.sidebar)
            let contentItem = PaneMotion.item(self.content)
            super.init(nibName: nil, bundle: nil)
            splitView.isVertical = true
            splitView.dividerStyle = .thin
            sidebarItem.minimumThickness = ParallelCodexVisualStyle.Layout.sidebarMinimum
            sidebarItem.maximumThickness = ParallelCodexVisualStyle.Layout.sidebarMaximum
            sidebarItem.holdingPriority = .defaultLow
            contentItem.minimumThickness = 540
            contentItem.holdingPriority = NSLayoutConstraint.Priority(249)
            self.sidebar.setFrameSize(NSSize(width: ParallelCodexVisualStyle.Layout.sidebarIdeal, height: 680))
            sidebarItem.isCollapsed = !showsSidebar
            addSplitViewItem(sidebarItem)
            addSplitViewItem(contentItem)
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    }
}

struct WorkbenchSplitColumns<Conversation: View, Results: View, Review: View>: NSViewControllerRepresentable {
    let layout: WorkbenchColumnsLayout
    let showsResults: Bool
    let showsReview: Bool
    @ViewBuilder let conversation: Conversation
    @ViewBuilder let results: Results
    @ViewBuilder let review: Review

    func makeNSViewController(context: Context) -> Controller {
        Controller(conversation: conversation, results: results, review: review)
    }

    func updateNSViewController(_ controller: Controller, context: Context) {
        controller.conversation.rootView = conversation
        controller.results.rootView = results
        controller.review.rootView = review
        controller.update(layout: layout, showsResults: showsResults, showsReview: showsReview)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsViewController: Controller, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? layout.contentWidth, height: proposal.height ?? 0)
    }

    // Own the split items rather than letting HSplitView infer minimum sizes
    // from a previously wider, hosted conversation. Keep the editor mounted.
    final class Controller: NSSplitViewController {
        let conversation: NSHostingView<Conversation>
        let results: NSHostingView<Results>
        let review: NSHostingView<Review>
        private let conversationItem: NSSplitViewItem
        private let resultsItem: NSSplitViewItem
        private let reviewItem: NSSplitViewItem
        private var installedItems = false

        init(conversation: Conversation, results: Results, review: Review) {
            self.conversation = NSHostingView(rootView: conversation)
            self.results = NSHostingView(rootView: results)
            self.review = NSHostingView(rootView: review)
            conversationItem = PaneMotion.item(self.conversation)
            resultsItem = PaneMotion.item(self.results)
            reviewItem = PaneMotion.item(self.review)
            super.init(nibName: nil, bundle: nil)
            splitView.isVertical = true
            splitView.dividerStyle = .thin
            // Pane preferences must yield to the hosting viewport's width.
            // High priorities keep the old wide layout clipped after a resize.
            conversationItem.holdingPriority = NSLayoutConstraint.Priority(249)
            resultsItem.holdingPriority = .defaultLow
            reviewItem.holdingPriority = .defaultLow
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

        func update(layout: WorkbenchColumnsLayout, showsResults: Bool, showsReview: Bool) {
            let scale = layout.conversationMinimum / 360
            conversationItem.minimumThickness = layout.conversationMinimum
            resultsItem.minimumThickness = layout.resultsMinimum
            resultsItem.maximumThickness = 440 * scale
            reviewItem.minimumThickness = layout.reviewMinimum
            reviewItem.maximumThickness = 800 * scale
            if !installedItems {
                results.setFrameSize(NSSize(width: 300 * scale, height: splitView.bounds.height))
                review.setFrameSize(NSSize(width: 440 * scale, height: splitView.bounds.height))
                resultsItem.isCollapsed = !showsResults
                reviewItem.isCollapsed = !showsReview
                for item in [conversationItem, resultsItem, reviewItem] { addSplitViewItem(item) }
                installedItems = true
            } else {
                // Retain pane identity, its chosen width and native scroll state.
                PaneMotion.setCollapsed(
                    [(resultsItem, !showsResults), (reviewItem, !showsReview)], in: view, animated: true
                )
            }
        }
    }
}
