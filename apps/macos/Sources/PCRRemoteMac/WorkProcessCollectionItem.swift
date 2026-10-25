import AppKit
import SwiftUI

@MainActor
final class WorkProcessCollectionItem: NSCollectionViewItem {
    static let identifier = NSUserInterfaceItemIdentifier("WorkProcessCollectionItem")
    private var coordinator: ConversationTimelineView.Coordinator?
    private var timeline: ConversationTimelineView?

    static func maximumHeight(textSize: AppTextSize) -> CGFloat { 360 * textSize.scale }

    override func loadView() {
        let scroll = FadingWorkScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        view = scroll
    }

    override func prepareForReuse() {
        super.prepareForReuse()
        coordinator?.detach()
        coordinator = nil
        timeline = nil
        (view as? NSScrollView)?.documentView = nil
    }

    deinit {
        let coordinator = coordinator
        Task { @MainActor in coordinator?.detach() }
    }

    func configure(process: ConversationWorkProcess, parent: ConversationTimelineView, size: NSSize) {
        _ = view
        let context = parent.contextID + "/" + process.id
        if parent.memory.snapshot(for: context) == nil {
            parent.memory.save(.init(offsetY: 0, followsLatest: false), for: context)
        }
        let content = ConversationTimelineView(
            language: parent.language, textSize: parent.textSize,
            accentColor: parent.accentColor, appearance: parent.appearance,
            contextID: context,
            items: process.blocks.map(ConversationTimelineItem.message),
            contentIsLoading: false, jumpToLatestRequest: 0,
            findQuery: parent.findQuery, findMatch: parent.findMatch,
            findNavigationRequest: parent.findNavigationRequest, memory: parent.memory,
            resultsInspectorVisible: false, openURL: parent.openURL,
            activateBlock: parent.activateBlock, selectAgent: { _ in }, toggleResults: {},
            isWorkProcess: true, isNearBottom: .constant(false),
            inlineAgentSwitchPosition: .constant(.visible)
        )
        timeline = content
        if coordinator == nil, let scroll = view as? NSScrollView {
            // The collection already knows the viewport size. Avoid an extra
            // SwiftUI sizing pass that temporarily lays out rows at zero width.
            scroll.setFrameSize(size)
            let coordinator = content.makeCoordinator()
            let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            collection.frame = NSRect(origin: .zero, size: scroll.contentSize)
            scroll.documentView = collection
            coordinator.attach(scrollView: scroll, collectionView: collection)
            self.coordinator = coordinator
        }
        coordinator?.update(parent: content)
    }

    func revealFindMatch() {
        guard var timeline else { return }
        timeline = ConversationTimelineView(
            language: timeline.language, textSize: timeline.textSize,
            accentColor: timeline.accentColor, appearance: timeline.appearance,
            contextID: timeline.contextID, items: timeline.items,
            contentIsLoading: false, jumpToLatestRequest: 0,
            findQuery: timeline.findQuery, findMatch: timeline.findMatch,
            findNavigationRequest: timeline.findNavigationRequest + 1, memory: timeline.memory,
            resultsInspectorVisible: false, openURL: timeline.openURL,
            activateBlock: timeline.activateBlock, selectAgent: { _ in }, toggleResults: {},
            isWorkProcess: true, isNearBottom: .constant(false),
            inlineAgentSwitchPosition: .constant(.visible)
        )
        self.timeline = timeline
        coordinator?.update(parent: timeline)
    }

    func beginViewportResize() { coordinator?.beginLiveResize() }

    func endViewportResize() { coordinator?.endLiveResize() }
}

@MainActor
final class FadingWorkScrollView: TranscriptViewportScrollView {
    var onUserScroll: (() -> Void)?
    var onScrollEvent: ((NSEvent) -> Void)?
    private let fade = CAGradientLayer()
    private(set) var fadesTop = false
    private(set) var fadesBottom = false

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        borderType = .noBorder
        scrollerStyle = .overlay
        verticalScrollElasticity = .none
        layer?.mask = fade
        contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(self, selector: #selector(updateFade),
            name: NSView.boundsDidChangeNotification, object: contentView)
        setAccessibilityLabel("工作过程 / Work process")
        setAccessibilityIdentifier("conversation.work-scroll")
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func scrollWheel(with event: NSEvent) {
        onUserScroll?()
        onScrollEvent?(event)
        // Keep the outer rendered snapshot stable even while this inner log
        // consumes the gesture. Forward phases, not a second displacement.
        let outer = conversationScrollView
        outer?.beginScrollEvent(event)
        outer?.onScrollEvent?(event)
        defer { outer?.endScrollEvent(event) }
        if let outer, outer.ownsScrollGesture || !hasReadableViewport(in: outer) {
            outer.claimScrollGesture()
            forwardScrollWheel(event, to: outer)
            return
        }
        guard let documentView,
              abs(event.scrollingDeltaY) >= abs(event.scrollingDeltaX) else {
            super.scrollWheel(with: event)
            return
        }
        guard event.scrollingDeltaY != 0 else { return }
        let step = event.scrollingDeltaY * (event.hasPreciseScrollingDeltas ? 1 : 12)
        var point = contentView.bounds.origin
        let minimum = documentView.bounds.minY
        let maximum = max(minimum, documentView.bounds.maxY - contentSize.height)
        let current = min(maximum, max(minimum, point.y))
        let movement = documentView.isFlipped ? -step : step
        point.y = min(maximum, max(minimum, current + movement))
        let remaining = movement - (point.y - current)
        contentView.scroll(to: point)
        reflectScrolledClipView(contentView)
        guard abs(remaining) > 0.0001 else { return }
        // Ownership belongs to the conversation, not a recycled work cell.
        // Streaming, reversal and momentum must not recapture this gesture.
        outer?.claimScrollGesture()
        forwardScrollWheel(event, to: outer, fraction: abs(remaining / movement))
    }

    private var conversationScrollView: ConversationTimelineScrollView? {
        var ancestor = superview
        while let view = ancestor {
            if let outer = view as? ConversationTimelineScrollView { return outer }
            ancestor = view.superview
        }
        return nil
    }

    private func hasReadableViewport(in outer: ConversationTimelineScrollView) -> Bool {
        let viewport = convert(contentView.bounds, from: contentView)
        var readable = visibleRect.intersection(viewport)
        if let document = outer.documentView {
            var available = outer.contentView.documentVisibleRect
            let inset = min(available.height, outer.obscuredTopHeight)
            if document.isFlipped { available.origin.y += inset }
            available.size.height -= inset
            readable = readable.intersection(convert(available, from: document))
        }
        return viewport.height > 0 && readable.height >= viewport.height - 0.5
    }

    private func forwardScrollWheel(_ event: NSEvent, to outer: ConversationTimelineScrollView?, fraction: CGFloat = 1) {
        outer?.scrollFromNestedWork(verticalDelta:
            event.scrollingDeltaY * (event.hasPreciseScrollingDeltas ? 1 : 12) * fraction)
    }

    override func layout() {
        super.layout()
        updateFade()
    }

    @objc private func updateFade() {
        guard let documentView else { return }
        let visible = documentVisibleRect
        let height = contentSize.height
        let top = documentView.isFlipped ? visible.minY > 0.5 : visible.maxY < documentView.bounds.maxY - 0.5
        let bottom = documentView.isFlipped ? visible.maxY < documentView.bounds.maxY - 0.5 : visible.minY > 0.5
        fadesTop = top && documentView.bounds.height > height
        fadesBottom = bottom && documentView.bounds.height > height
        let edge = min(18 / max(1, bounds.height), 0.25)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fade.frame = bounds
        fade.startPoint = CGPoint(x: 0.5, y: 1)
        fade.endPoint = CGPoint(x: 0.5, y: 0)
        fade.locations = [0, NSNumber(value: edge), NSNumber(value: 1 - edge), 1]
        fade.colors = [fadesTop ? NSColor.clear.cgColor : NSColor.black.cgColor,
                       NSColor.black.cgColor, NSColor.black.cgColor,
                       fadesBottom ? NSColor.clear.cgColor : NSColor.black.cgColor]
        CATransaction.commit()
    }
}
