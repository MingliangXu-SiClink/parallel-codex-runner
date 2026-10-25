import AppKit
import SwiftUI

struct ConversationTimelineView: NSViewRepresentable {
    let language: AppLanguage
    let textSize: AppTextSize
    let accentColor: AppAccentColor
    let appearance: TranscriptAppearance
    let contextID: String
    let items: [ConversationTimelineItem]
    let contentIsLoading: Bool
    let jumpToLatestRequest: Int
    let findQuery: String
    let findMatch: ConversationFindMatch?
    let findNavigationRequest: Int
    let memory: TranscriptScrollMemory
    let resultsInspectorVisible: Bool
    let openURL: (URL, TranscriptBlock) -> Void
    let activateBlock: (TranscriptBlock) -> Void
    let selectAgent: (Int) -> Void
    let toggleResults: () -> Void
    var itemsRevision: UInt64? = nil
    var questionActions = AgentQuestionActions()
    var isWorkProcess = false
    var onUserNavigation: () -> Void = {}
    @Binding var isNearBottom: Bool
    @Binding var inlineAgentSwitchPosition: TranscriptInlineControlPosition

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let collection = Self.makeCollectionView(coordinator: context.coordinator)
        let scroll: NSScrollView = isWorkProcess ? FadingWorkScrollView() : ConversationTimelineScrollView()
        scroll.borderType = .noBorder
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.documentView = collection
        context.coordinator.attach(scrollView: scroll, collectionView: collection)
        context.coordinator.update(parent: self)
        return scroll
    }

    @MainActor
    static func makeCollectionView(coordinator: Coordinator) -> WidthAwareCollectionView {
        let flow = TranscriptFlowLayout()
        flow.minimumLineSpacing = coordinator.parent.isWorkProcess ? WorkOperationPresentation.rowSpacing : 22
        flow.sectionInset = coordinator.parent.isWorkProcess
            ? NSEdgeInsets(top: 8, left: ParallelCodexVisualStyle.Layout.workProcessIndent, bottom: 8, right: 0)
            : NSEdgeInsets(
            top: 24,
            left: ParallelCodexVisualStyle.Layout.transcriptInset,
            bottom: 34,
            right: ParallelCodexVisualStyle.Layout.transcriptInset
        )

        let collection = WidthAwareCollectionView()
        collection.collectionViewLayout = flow
        collection.dataSource = coordinator
        collection.delegate = coordinator
        collection.isSelectable = false
        collection.wantsLayer = true
        collection.register(
            TranscriptCollectionItem.self,
            forItemWithIdentifier: TranscriptCollectionItem.identifier
        )
        collection.register(
            AgentSwitchCollectionItem.self,
            forItemWithIdentifier: AgentSwitchCollectionItem.identifier
        )
        collection.register(WorkProcessCollectionItem.self,
                            forItemWithIdentifier: WorkProcessCollectionItem.identifier)
        collection.register(AgentQuestionCollectionItem.self,
                            forItemWithIdentifier: AgentQuestionCollectionItem.identifier)
        collection.onLiveResizeBegan = { [weak coordinator] in
            coordinator?.beginLiveResize()
        }
        collection.onWidthChange = { [weak coordinator] liveResize in
            coordinator?.collectionWidthDidChange(duringLiveResize: liveResize)
        }
        collection.onLiveResizeEnded = { [weak coordinator] in
            coordinator?.endLiveResize()
        }

        return collection
    }

    func updateNSView(_ view: NSScrollView, context: Context) {
        context.coordinator.scheduleRepresentableUpdate(parent: self)
    }

    static func dismantleNSView(_ view: NSScrollView, coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator: NSObject, NSCollectionViewDataSource, NSCollectionViewDelegateFlowLayout {
        private struct ItemMeasurement {
            let block: TranscriptBlock
            let referenceWidth: CGFloat
            let height: CGFloat
        }

        private struct InlineControlAnchor {
            let id: String
            let viewportOffset: CGFloat
            let usesFlippedCoordinates: Bool
        }

        var parent: ConversationTimelineView
        private weak var scrollView: NSScrollView?
        private weak var collectionView: NSCollectionView?
        private var boundsObserver: NSObjectProtocol?
        private var userScrollObserver: NSObjectProtocol?
        private let scrollMotion: NativeScrollMotion
        private var scrollRequestGeneration = 0
        private var activeContext = ""
        private var renderedItems: [ConversationTimelineItem] = []
        private var previousJumpRequest = 0
        private var previousFindNavigationRequest = 0
        private var isRestoring = false
        // Geometry can clamp a reader to the end without a navigation request.
        // Keep the viewport's intent separate from that temporary position.
        private var followsLatest = true
        private var lastPublishedNearBottom: Bool?
        private var lastPublishedAgentSwitchPosition: TranscriptInlineControlPosition?
        private var lastWidth: CGFloat = 0
        private var resizeWorkItem: DispatchWorkItem?
        private var resizeSnapshot: TranscriptScrollMemory.Snapshot?
        private var liveResizeInProgress = false
        private var liveReflowWorkItem: DispatchWorkItem?
        private var presentationRestoreWorkItem: DispatchWorkItem?
        private var pendingRestoreSnapshot: TranscriptScrollMemory.Snapshot?
        private var pendingInlineControlAnchor: InlineControlAnchor?
        private var collectionUpdateInFlight = false
        private var deferredCollectionUpdate: ConversationTimelineView?
        private var collectionUpdateGeneration = 0
        private var committedInlineLayout = false
        private var agentSwitchInteractionInProgress = false
        private var deferredInteractionUpdate: ConversationTimelineView?
        private var deferredScrollUpdate: ConversationTimelineView?
        private var scrollIdleWorkItem: DispatchWorkItem?
        private var userScrollInProgress = false
        private var trackingScroll = false
        private var momentumScroll = false
        private var lastGeometryCommit: String?
        private var itemMeasurementRevision = 0
        private var cachedContentHeight: CGFloat?
        private var cachedContentWidth: CGFloat = 0
        private var itemMeasurements: [String: ItemMeasurement] = [:]
        private var historyEntries: [ConversationHistoryEntry] = []
        private var historyQuestionOffsets: [CGFloat]?
        private var isAttached = false
        private var pendingRepresentableUpdate: ConversationTimelineView?
        private var representableUpdateWorkItem: DispatchWorkItem?
        private var representableUpdateGeneration = 0
        private(set) var unchangedUpdateCount = 0
        private(set) var deferredScrollUpdateCount = 0
        private(set) var geometryCommitCount = 0

        init(parent: ConversationTimelineView, scrollMotion: NativeScrollMotion = NativeScrollMotion()) {
            self.parent = parent
            self.scrollMotion = scrollMotion
            previousJumpRequest = parent.jumpToLatestRequest
            previousFindNavigationRequest = parent.findNavigationRequest
        }

        func attach(scrollView: NSScrollView, collectionView: NSCollectionView) {
            isAttached = true
            self.scrollView = scrollView
            self.collectionView = collectionView
            // The native viewport owns its backing surface. Transparent scroll
            // buffers can retain previously scrolled text under SwiftUI's canvas.
            scrollView.wantsLayer = true
            collectionView.wantsLayer = true
            scrollView.drawsBackground = true
            (scrollView as? ConversationTimelineScrollView)?.historyRail.navigate = { [weak self] id in
                self?.scrollToHistoryQuestion(id: id)
            }
            let stopFollowing: () -> Void = { [weak self] in self?.beginUserScroll() }
            (scrollView as? ConversationTimelineScrollView)?.onUserScroll = stopFollowing
            (scrollView as? FadingWorkScrollView)?.onUserScroll = stopFollowing
            let observeScroll: (NSEvent) -> Void = { [weak self] in self?.observeScrollEvent($0) }
            (scrollView as? ConversationTimelineScrollView)?.onScrollEvent = observeScroll
            (scrollView as? FadingWorkScrollView)?.onScrollEvent = observeScroll
            (scrollView as? ConversationTimelineScrollView)?.onViewportHeightChange = { [weak self] in
                self?.viewportHeightDidChange()
            }
            scrollMotion.onCompletion = { [weak self] in self?.recordScrollPosition() }
            userScrollObserver = NotificationCenter.default.addObserver(
                forName: NSScrollView.willStartLiveScrollNotification, object: scrollView, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.beginUserScroll() }
            }
            scrollView.contentView.postsBoundsChangedNotifications = true
            boundsObserver = NotificationCenter.default.addObserver(
                forName: NSView.boundsDidChangeNotification,
                object: scrollView.contentView,
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.recordScrollPosition(viewportChanged: true) }
            }
        }

        func detach() {
            isAttached = false
            cancelRepresentableUpdate()
            collectionUpdateGeneration &+= 1
            collectionUpdateInFlight = false
            deferredCollectionUpdate = nil
            cancelScrollMotion()
            endScrollCoalescing(discard: true)
            (scrollView as? ConversationTimelineScrollView)?.onUserScroll = nil
            (scrollView as? ConversationTimelineScrollView)?.onScrollEvent = nil
            (scrollView as? ConversationTimelineScrollView)?.onViewportHeightChange = nil
            (scrollView as? ConversationTimelineScrollView)?.resetScrollGestureOwnership()
            (scrollView as? FadingWorkScrollView)?.onUserScroll = nil
            (scrollView as? FadingWorkScrollView)?.onScrollEvent = nil
            if let userScrollObserver { NotificationCenter.default.removeObserver(userScrollObserver) }
            userScrollObserver = nil
            // boundsDidChange already records the last user-visible position.
            // During AppKit teardown the document can briefly collapse to its
            // origin; sampling here would replace that valid snapshot with 0.
            parent.memory.flush()
            resizeWorkItem?.cancel()
            resizeWorkItem = nil
            resizeSnapshot = nil
            liveResizeInProgress = false
            liveReflowWorkItem?.cancel()
            liveReflowWorkItem = nil
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            agentSwitchInteractionInProgress = false
            deferredInteractionUpdate = nil
            (scrollView as? ConversationTimelineScrollView)?.historyRail.navigate = nil
            if let boundsObserver {
                NotificationCenter.default.removeObserver(boundsObserver)
            }
            boundsObserver = nil
        }

        func scheduleRepresentableUpdate(parent: ConversationTimelineView) {
            guard isAttached else { return }
            pendingRepresentableUpdate = parent
            // These snapshots predate the newest SwiftUI submission. Their
            // completion callbacks must not overtake the pending graph commit.
            deferredCollectionUpdate = nil
            deferredInteractionUpdate = nil
            deferredScrollUpdate = nil
            guard representableUpdateWorkItem == nil else { return }
            let generation = representableUpdateGeneration
            // Hiding/removing a focused AppKit row recomputes SwiftUI key views.
            // Doing that inside updateNSView reenters the graph being evaluated.
            // Commit the whole snapshot after that callback, never a half-swap.
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.isAttached,
                      self.representableUpdateGeneration == generation,
                      let pending = self.pendingRepresentableUpdate else { return }
                self.update(parent: pending)
            }
            representableUpdateWorkItem = work
            DispatchQueue.main.async(execute: work)
        }

        private func cancelRepresentableUpdate() {
            representableUpdateGeneration &+= 1
            representableUpdateWorkItem?.cancel()
            representableUpdateWorkItem = nil
            pendingRepresentableUpdate = nil
        }

        func update(parent: ConversationTimelineView) {
            cancelRepresentableUpdate()
            if collectionUpdateInFlight {
                // Keep the native transaction's source, text and geometry on
                // one snapshot until its completion. Intermediate polls are
                // superseded, not replayed against later index paths.
                deferredCollectionUpdate = parent
                return
            }
            deferredCollectionUpdate = nil
            committedInlineLayout = false
            if agentSwitchInteractionInProgress {
                deferredInteractionUpdate = parent
                return
            }
            let oldContext = activeContext
            let contextChanged = oldContext != parent.contextID
            let inlineAccessibilityChanged = self.parent.inlineAgentSwitchPosition
                != parent.inlineAgentSwitchPosition
            let agentSwitchPresentationChanged = self.parent.resultsInspectorVisible
                != parent.resultsInspectorVisible
            let presentationChanged = self.parent.textSize != parent.textSize
                || self.parent.language != parent.language
                || self.parent.accentColor != parent.accentColor
                || self.parent.appearance != parent.appearance
            let candidateChanged = !presentationChanged
                && transcriptContextChangesOnlyAgent(from: oldContext, to: parent.contextID)
            let findPresentationChanged = self.parent.findQuery != parent.findQuery
                || self.parent.findMatch != parent.findMatch
            let contentFinishedLoading = self.parent.contentIsLoading
                && !parent.contentIsLoading
            let oldItems = renderedItems
            let explicitNavigation = contextChanged || presentationChanged || findPresentationChanged
                || parent.jumpToLatestRequest != previousJumpRequest
                || parent.findNavigationRequest != previousFindNavigationRequest
                || pendingInlineControlAnchor != nil
                || ConversationTimelineProjection.toggledWorkDisclosureID(old: oldItems, new: parent.items) != nil
            if explicitNavigation {
                endScrollCoalescing(discard: true)
            } else if userScrollInProgress, !contentFinishedLoading {
                // Do not mutate dataSource, native cells or their heights until
                // tracking and momentum finish. The model still receives data.
                deferredScrollUpdate = parent
                deferredScrollUpdateCount += 1
                return
            }
            let itemsUnchanged = parent.itemsRevision.map {
                self.parent.itemsRevision == $0
            } ?? (oldItems == parent.items)
            if !contextChanged, itemsUnchanged, !presentationChanged,
               !findPresentationChanged, !contentFinishedLoading,
               !inlineAccessibilityChanged, !agentSwitchPresentationChanged,
               parent.jumpToLatestRequest == previousJumpRequest,
               parent.findNavigationRequest == previousFindNavigationRequest,
               !isRestoring, pendingRestoreSnapshot == nil, pendingInlineControlAnchor == nil {
                self.parent = parent
                unchangedUpdateCount += 1
                return
            }
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            if contextChanged || presentationChanged { lastGeometryCommit = nil }
            let wasNearBottom = followsLatest
            let changesOnlyFixedAgentSwitch = ConversationTimelineProjection
                .changesOnlyFixedAgentSwitch(old: oldItems, new: parent.items)
            let changesOnlyFixedWorkProcess = !presentationChanged && !contextChanged
                && oldItems.count == parent.items.count
                && zip(oldItems, parent.items).allSatisfy { old, new in
                    if old == new { return true }
                    guard case .workProcess(let before) = old,
                          case .workProcess(let after) = new,
                          before.id == after.id, let collectionView else { return false }
                    let width = itemWidth(in: collectionView)
                    return workProcessHeight(before, width: width) == workProcessHeight(after, width: width)
                }
            let changedInlineControlID = ConversationTimelineProjection
                .changedInlineControlID(old: oldItems, new: parent.items)
            let toggledWorkDisclosureID = ConversationTimelineProjection
                .toggledWorkDisclosureID(old: oldItems, new: parent.items)
            let committedAgentSwitchID = ConversationTimelineProjection
                .committedAgentSwitchID(old: oldItems, new: parent.items)
            // Mouse and accessibility actions capture their row before they
            // mutate the model. Diff inference is only a fallback for changes
            // initiated elsewhere, because an expanded disclosure can keep
            // the same summary value while inserting many work rows below it.
            let inferredInlineControlAnchor = pendingInlineControlAnchor == nil
                ? captureInlineControlAnchor(
                    id: toggledWorkDisclosureID
                        ?? committedAgentSwitchID
                        ?? changedInlineControlID,
                    oldItems: oldItems,
                    force: toggledWorkDisclosureID != nil
                        || committedAgentSwitchID != nil
                )
                : nil
            let preservedSnapshot = snapshotPreservingConversationTrunk(
                isRestoring
                    ? pendingRestoreSnapshot ?? self.parent.memory.snapshot(for: activeContext)
                    : currentSnapshot(),
                oldItems: oldItems,
                newItems: parent.items
            )
            if contextChanged,
               transcriptContextContinuesSameConversation(from: oldContext, to: parent.contextID),
               !wasNearBottom,
               parent.memory.snapshot(for: parent.contextID) == nil,
               let preservedSnapshot {
                parent.memory.save(preservedSnapshot, for: parent.contextID)
            }
            if contextChanged {
                (scrollView as? ConversationTimelineScrollView)?.resetScrollGestureOwnership()
                (scrollView as? ConversationTimelineScrollView)?.obscuredTopHeight = 0
                cancelScrollMotion()
                resizeWorkItem?.cancel()
                resizeWorkItem = nil
                resizeSnapshot = nil
                liveResizeInProgress = false
                liveReflowWorkItem?.cancel()
                liveReflowWorkItem = nil
                presentationRestoreWorkItem?.cancel()
                presentationRestoreWorkItem = nil
                // Bounds notifications already persist the last live position.
                // Sampling after SwiftUI has changed the context can observe a
                // temporarily collapsed document and corrupt the old context.
                // Suppress bounds notifications from reloadData and the first
                // layout pass. They describe AppKit's temporary origin, not the
                // user's saved position, and must not overwrite that snapshot.
                isRestoring = true
                followsLatest = parent.memory.snapshot(for: parent.contextID)?.followsLatest ?? true
                pendingRestoreSnapshot = nil
                if !candidateChanged { pendingInlineControlAnchor = nil }
                collectionUpdateInFlight = false
                lastPublishedNearBottom = nil
                lastPublishedAgentSwitchPosition = nil
            }

            self.parent = parent
            activeContext = parent.contextID
            let itemsChanged = oldItems != parent.items
            if contextChanged || itemsChanged {
                historyEntries = ConversationHistoryEntry.make(from: parent.items)
            }
            if contextChanged || itemsChanged || presentationChanged {
                historyQuestionOffsets = nil
                if let collectionView, updateSectionInsets(for: collectionView.bounds.width) {
                    collectionView.collectionViewLayout?.invalidateLayout()
                }
                (scrollView as? ConversationTimelineScrollView)?.historyRail.configure(
                    entries: historyEntries, language: parent.language,
                    textSize: parent.textSize, appearance: parent.appearance,
                    resetsInteraction: contextChanged && !candidateChanged
                )
            }
            if (contextChanged && !candidateChanged) || presentationChanged {
                if (contextChanged && !oldContext.isEmpty) || presentationChanged {
                    isRestoring = true
                    renderedItems = parent.items
                    replaceCollectionView()
                }
                let canvas = ParallelCodexVisualStyle.resolvedNSColor(
                    ParallelCodexVisualStyle.canvasNSColor, for: parent.appearance
                )
                scrollView?.backgroundColor = canvas
                collectionView?.backgroundColors = [canvas]
                scrollView?.contentView.needsDisplay = true
                itemMeasurements.removeAll(keepingCapacity: true)
            } else if itemsChanged, !changesOnlyFixedAgentSwitch {
                trimInactiveMeasurements(retaining: parent.items)
            }
            if contextChanged
                || (itemsChanged && !changesOnlyFixedAgentSwitch)
                || presentationChanged {
                cachedContentHeight = nil
            }
            if let inferredInlineControlAnchor {
                // A disclosure or branch tab is an in-place control. Preserve
                // its exact viewport coordinate rather than relying on the
                // collection view's automatic insertion compensation.
                pendingInlineControlAnchor = inferredInlineControlAnchor
                pendingRestoreSnapshot = nil
                isRestoring = true
            } else if itemsChanged,
               !changesOnlyFixedAgentSwitch,
               !contextChanged,
               (!changesOnlyFixedWorkProcess || !wasNearBottom),
               pendingInlineControlAnchor == nil,
               pendingRestoreSnapshot == nil,
               let preservedSnapshot {
                // Reading and following both own the geometry transaction.
                // Painting new row heights before a queued animated follow
                // exposes old content, then visibly chases the end on each page.
                pendingRestoreSnapshot = preservedSnapshot
                isRestoring = true
                cancelScrollMotion()
            }
            if candidateChanged {
                applyItemChanges(
                    from: oldItems, to: parent.items,
                    isWorkDisclosureChange: false, isCandidateSwitch: true
                )
            } else if contextChanged {
                renderedItems = parent.items
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0
                    context.allowsImplicitAnimation = false
                    collectionView?.reloadData()
                    collectionView?.collectionViewLayout?.invalidateLayout()
                    collectionView?.needsDisplay = true
                }
            } else if presentationChanged {
                if pendingInlineControlAnchor == nil {
                    pendingRestoreSnapshot = preservedSnapshot
                        ?? parent.memory.snapshot(for: activeContext)
                        ?? TranscriptScrollMemory.Snapshot(
                            offsetY: .greatestFiniteMagnitude,
                            followsLatest: true
                        )
                } else {
                    // A disclosure or Agent switch already owns the viewport
                    // during its short transition. Let that semantic anchor
                    // absorb the text reflow instead of leaving a second,
                    // stale restore queued behind it.
                    pendingRestoreSnapshot = nil
                }
                isRestoring = true
                renderedItems = parent.items
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0
                    context.allowsImplicitAnimation = false
                    collectionView?.reloadData()
                    collectionView?.collectionViewLayout?.invalidateLayout()
                    collectionView?.needsDisplay = true
                }
            } else if itemsChanged {
                applyItemChanges(
                    from: oldItems,
                    to: parent.items,
                    isWorkDisclosureChange: toggledWorkDisclosureID != nil
                )
            } else if findPresentationChanged {
                reloadMessagesForFindPresentation()
            }

            if candidateChanged {
                // The branch and its viewport were committed in one paint.
            } else if contextChanged {
                restorePosition()
            } else if committedInlineLayout {
                // Geometry and the click anchor were committed before painting.
            } else if presentationChanged, pendingInlineControlAnchor != nil {
                schedulePendingInlineControlRestore()
            } else if presentationChanged {
                schedulePresentationRestore()
            } else if itemsChanged, pendingInlineControlAnchor != nil {
                schedulePendingInlineControlRestore()
            } else if contentFinishedLoading, pendingRestoreSnapshot != nil {
                restorePendingPosition()
            } else if itemsChanged,
                      !changesOnlyFixedAgentSwitch,
                      pendingRestoreSnapshot != nil {
                restorePendingPosition()
            }
            if inlineAccessibilityChanged || agentSwitchPresentationChanged {
                refreshVisibleAgentSwitch()
            }
            if parent.jumpToLatestRequest != previousJumpRequest {
                previousJumpRequest = parent.jumpToLatestRequest
                scrollToLatest(animated: true)
            }
            if parent.findNavigationRequest != previousFindNavigationRequest {
                previousFindNavigationRequest = parent.findNavigationRequest
                scrollToFindMatch()
            }
            if contextChanged || itemsChanged || presentationChanged {
                let context = activeContext
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.activeContext == context else { return }
                    self.updateHistoryRailPosition()
                }
            }
        }

        private func replaceCollectionView() {
            guard let scrollView, let previous = collectionView else { return }
            // A new transcript or typography must not inherit AppKit's previous
            // reuse/drawing surface. Keep semantic scroll memory, but retire the
            // native document and its pending layout callbacks.
            previous.isHidden = true
            previous.dataSource = nil
            previous.delegate = nil
            if let previous = previous as? WidthAwareCollectionView {
                previous.onLiveResizeBegan = nil
                previous.onWidthChange = nil
                previous.onLiveResizeEnded = nil
            }
            let replacement = ConversationTimelineView.makeCollectionView(coordinator: self)
            replacement.frame = NSRect(origin: .zero, size: scrollView.contentView.bounds.size)
            scrollView.documentView = replacement
            collectionView = replacement
            lastGeometryCommit = nil
            _ = updateSectionInsets(for: replacement.bounds.width)
        }

        func scrollToHistoryQuestion(id: String) {
            endScrollCoalescing(discard: false)
            cancelScrollMotion()
            guard historyEntries.contains(where: { $0.id == id }) else { return }
            // History navigation is a viewport operation, not a Turn/Agent
            // selection. It must never load another branch or finalize a run.
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            resizeWorkItem?.cancel()
            resizeWorkItem = nil
            resizeSnapshot = nil
            pendingInlineControlAnchor = nil
            pendingRestoreSnapshot = nil
            restore(snapshot: .init(
                offsetY: 0, followsLatest: false, anchorID: id, anchorOffset: -16
            ))
        }

        private func updateHistoryRailPosition() {
            guard !isRestoring, !collectionUpdateInFlight,
                  let scrollView = scrollView as? ConversationTimelineScrollView,
                  let layout = collectionView?.collectionViewLayout,
                  !historyEntries.isEmpty else { return }
            if historyQuestionOffsets == nil {
                let offsets = historyEntries.compactMap {
                    layout.layoutAttributesForItem(at: IndexPath(item: $0.itemIndex, section: 0))?.frame.minY
                }
                guard offsets.count == historyEntries.count,
                      zip(offsets, offsets.dropFirst()).allSatisfy({ $0 < $1 }) else { return }
                historyQuestionOffsets = offsets
            }
            let index = nearBottom() ? historyEntries.count - 1 : ConversationHistoryRailGeometry.activeIndex(
                offset: scrollView.contentView.documentVisibleRect.minY + 24,
                questionOffsets: historyQuestionOffsets ?? []
            )
            scrollView.historyRail.setActiveIndex(index)
        }

        private func reloadMessagesForFindPresentation() {
            guard let collectionView else { return }
            let paths = Set(renderedItems.indices.compactMap { index -> IndexPath? in
                if case .agentSwitch = renderedItems[index] { return nil }
                return IndexPath(item: index, section: 0)
            })
            guard !paths.isEmpty else { return }
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0
                context.allowsImplicitAnimation = false
                collectionView.reloadItems(at: paths)
            }
        }

        private func scrollToFindMatch() {
            cancelScrollMotion()
            followsLatest = false
            let context = activeContext
            let generation = scrollRequestGeneration
            let requestedMatch = parent.findMatch
            DispatchQueue.main.async { [weak self] in
                guard let self,
                      self.activeContext == context,
                      self.scrollRequestGeneration == generation,
                      self.parent.findMatch == requestedMatch,
                      let collectionView = self.collectionView,
                      let match = requestedMatch,
                      let index = self.renderedItems.firstIndex(where: {
                          $0.containsMessage(id: match.itemID)
                      }) else {
                    return
                }
                collectionView.layoutSubtreeIfNeeded()
                collectionView.scrollToItems(
                    at: [IndexPath(item: index, section: 0)],
                    scrollPosition: .centeredVertically
                )
                collectionView.layoutSubtreeIfNeeded()
                (collectionView.item(at: IndexPath(item: index, section: 0))
                    as? TranscriptCollectionItem)?.revealCurrentFindMatch()
                (collectionView.item(at: IndexPath(item: index, section: 0))
                    as? WorkProcessCollectionItem)?.revealFindMatch()
                self.followsLatest = false
                self.recordScrollPosition()
            }
        }

        private func applyItemChanges(
            from oldItems: [ConversationTimelineItem],
            to newItems: [ConversationTimelineItem],
            isWorkDisclosureChange: Bool,
            isCandidateSwitch: Bool = false
        ) {
            if oldItems.map(\.id) != newItems.map(\.id) { lastGeometryCommit = nil }
            collectionUpdateGeneration &+= 1
            let updateGeneration = collectionUpdateGeneration
            guard let collectionView else {
                renderedItems = newItems
                return
            }
            let newIDs = Set(newItems.map(\.id))
            let preservesQuestionEditor = oldItems.contains { item in
                guard case .message(let block) = item, block.question?.editable == true else { return false }
                return newIDs.contains(item.id)
            }
            // Streaming/history insertions can shift an active question's index.
            // Keep its native field editor, selection and IME composition mounted.
            if preservesQuestionEditor || isCandidateSwitch || (pendingInlineControlAnchor != nil
                && (isWorkDisclosureChange || pendingInlineControlAnchor?.id.hasPrefix("message:") == true)) {
                let plan = ConversationTimelineUpdatePlan.makeForInlineDisclosure(old: oldItems, new: newItems,
                    preservingEditors: preservesQuestionEditor)
                let visibleItems = Dictionary(uniqueKeysWithValues: collectionView.indexPathsForVisibleItems().compactMap {
                    path -> (String, NSCollectionViewItem)? in
                    guard oldItems.indices.contains(path.item),
                          let item = collectionView.item(at: path) else { return nil }
                    return (oldItems[path.item].id, item)
                })
                committedInlineLayout = true
                let updateContext = activeContext
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0
                    context.allowsImplicitAnimation = false
                    if plan.reloadAll {
                        retireItemViews(at: Array(oldItems.indices), in: collectionView)
                        renderedItems = newItems
                        collectionView.reloadData()
                    } else {
                        collectionUpdateInFlight = true
                        retireItemViews(at: plan.removedIndices, in: collectionView)
                        collectionView.performBatchUpdates {
                            renderedItems = newItems
                            for index in plan.changedIndices {
                                let item = visibleItems[newItems[index].id]
                                switch newItems[index] {
                                case .workProcess(let process):
                                    if let item = item as? WorkProcessCollectionItem {
                                        configureWorkProcess(item, process: process)
                                    }
                                case .message(let block):
                                    configureMessageItem(item, block: block)
                                case .agentSwitch(let state):
                                    if let item = item as? AgentSwitchCollectionItem {
                                        configureAgentSwitch(item, state: state)
                                    }
                                }
                            }
                            collectionView.deleteItems(at: Set(plan.removedIndices.map { IndexPath(item: $0, section: 0) }))
                            collectionView.insertItems(at: Set(plan.insertedIndices.map { IndexPath(item: $0, section: 0) }))
                            for move in plan.movedItems {
                                collectionView.moveItem(at: IndexPath(item: move.from, section: 0),
                                    to: IndexPath(item: move.to, section: 0))
                            }
                        } completionHandler: { [weak self, weak collectionView] _ in
                            self?.completeCollectionUpdate(in: collectionView,
                                context: updateContext, generation: updateGeneration)
                        }
                    }
                    collectionView.collectionViewLayout?.invalidateLayout()
                    synchronizeDocumentGeometry()
                    collectionView.layoutSubtreeIfNeeded()
                    if pendingInlineControlAnchor != nil {
                        restorePendingInlineControlAnchor(finalPass: true, duringBatch: true)
                    } else if isCandidateSwitch {
                        restore(snapshot: parent.memory.snapshot(for: activeContext))
                    }
                    collectionView.needsDisplay = true
                }
                CATransaction.commit()
                return
            }
            let plan = ConversationTimelineUpdatePlan.make(
                old: oldItems,
                new: newItems
            )
            guard !plan.reloadAll else {
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0
                    context.allowsImplicitAnimation = false
                    retireItemViews(at: Array(oldItems.indices), in: collectionView)
                    renderedItems = newItems
                    collectionView.reloadData()
                    commitItemGeometry(in: collectionView)
                }
                CATransaction.commit()
                return
            }

            let inPlaceWorkIndices = plan.changedIndices.filter { index in
                guard oldItems.indices.contains(index), newItems.indices.contains(index),
                      oldItems[index].id == newItems[index].id,
                      case .workProcess = newItems[index] else { return false }
                return true
            }
            let inPlaceAgentSwitchIndices = plan.changedIndices.filter { index in
                guard oldItems.indices.contains(index), newItems.indices.contains(index),
                      oldItems[index].id == newItems[index].id,
                      case .agentSwitch = oldItems[index],
                      case .agentSwitch = newItems[index] else { return false }
                return true
            }
            let inPlaceMessageIndices = plan.changedIndices.filter { index in
                guard oldItems.indices.contains(index), newItems.indices.contains(index),
                      oldItems[index].id == newItems[index].id,
                      case .message = oldItems[index], case .message = newItems[index] else { return false }
                return true
            }
            let reloadedIndices = plan.changedIndices.filter {
                !inPlaceAgentSwitchIndices.contains($0) && !inPlaceWorkIndices.contains($0)
                    && !inPlaceMessageIndices.contains($0)
            }

            guard !reloadedIndices.isEmpty
                    || !plan.removedIndices.isEmpty
                    || !plan.insertedIndices.isEmpty else {
                // The fixed-height Agent bar was reconfigured in place. Avoid a
                // collection transaction so a live poll cannot invalidate the
                // button underneath an in-progress mouse click.
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0
                    context.allowsImplicitAnimation = false
                    renderedItems = newItems
                    for index in inPlaceAgentSwitchIndices { refreshVisibleAgentSwitch(at: index) }
                    for index in inPlaceWorkIndices { refreshVisibleWorkProcess(at: index) }
                    for index in inPlaceMessageIndices { refreshVisibleMessage(at: index) }
                    if !inPlaceWorkIndices.isEmpty || !inPlaceMessageIndices.isEmpty {
                        commitItemGeometry(in: collectionView)
                    }
                }
                CATransaction.commit()
                return
            }
            let updateContext = activeContext
            collectionUpdateInFlight = true
            retireItemViews(at: plan.removedIndices + reloadedIndices, in: collectionView)
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0
                context.allowsImplicitAnimation = false
                collectionView.performBatchUpdates {
                    // AppKit must observe the old item count before opening
                    // the transaction, including a not-yet-laid-out collection.
                    renderedItems = newItems
                    for index in inPlaceAgentSwitchIndices { refreshVisibleAgentSwitch(at: index) }
                    for index in inPlaceWorkIndices { refreshVisibleWorkProcess(at: index) }
                    for index in inPlaceMessageIndices { refreshVisibleMessage(at: index) }
                    if !plan.removedIndices.isEmpty {
                        collectionView.deleteItems(
                            at: Set(plan.removedIndices.map { IndexPath(item: $0, section: 0) })
                        )
                    }
                    if !reloadedIndices.isEmpty {
                        collectionView.reloadItems(
                            at: Set(reloadedIndices.map { IndexPath(item: $0, section: 0) })
                        )
                    }
                    if !plan.insertedIndices.isEmpty {
                        collectionView.insertItems(
                            at: Set(plan.insertedIndices.map { IndexPath(item: $0, section: 0) })
                        )
                    }
                } completionHandler: { [weak self, weak collectionView] _ in
                    self?.completeCollectionUpdate(in: collectionView,
                        context: updateContext, generation: updateGeneration)
                }
                commitItemGeometry(in: collectionView)
            }
            CATransaction.commit()
        }

        private func completeCollectionUpdate(in collection: NSCollectionView?, context: String, generation: Int) {
            guard let collection, collectionView === collection,
                  activeContext == context, collectionUpdateGeneration == generation else { return }
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            collectionUpdateInFlight = false
            commitItemGeometry(in: collection)
            if pendingInlineControlAnchor != nil {
                restorePendingInlineControlAnchor(finalPass: true)
            } else if let snapshot = pendingRestoreSnapshot {
                restore(snapshot: snapshot)
            }
            updateHistoryRailPosition()
            // Completion may run inside performBatchUpdates. Do not reenter
            // the caller's anchor/geometry commit with a different snapshot.
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.collectionUpdateInFlight,
                      let pending = self.deferredCollectionUpdate else { return }
                self.deferredCollectionUpdate = nil
                self.update(parent: pending)
            }
        }

        private func retireItemViews(at indices: [Int], in collection: NSCollectionView) {
            // Exit animations may retain removed views. They no longer own a
            // row and must stop contributing pixels before a branch is inserted.
            for index in indices {
                collection.item(at: IndexPath(item: index, section: 0))?.view.isHidden = true
            }
        }

        private func commitItemGeometry(in collectionView: NSCollectionView) {
            let width = itemWidth(in: collectionView)
            let height = contentHeight
            // Equal total heights can conceal a different distribution of row
            // heights. Include the measurement revision, not only the sum.
            // Transaction identity changes on every poll, even when row
            // geometry does not. Only geometry can invalidate native layout.
            let geometry = "\(width):\(height):\(itemMeasurementRevision)"
            guard lastGeometryCommit != geometry else { return }
            geometryCommitCount += 1
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            // Row heights, the document extent and the reading anchor must
            // reach AppKit in one paint, not in a later restore callback.
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            defer { CATransaction.commit() }
            collectionView.collectionViewLayout?.invalidateLayout()
            collectionView.layoutSubtreeIfNeeded()
            synchronizeDocumentGeometry()
            collectionView.layoutSubtreeIfNeeded()
            if pendingInlineControlAnchor != nil {
                restorePendingInlineControlAnchor(finalPass: true, duringBatch: true)
            } else if let snapshot = pendingRestoreSnapshot {
                restore(snapshot: snapshot)
            }
            collectionView.needsDisplay = true
            scrollView?.contentView.needsDisplay = true
            lastGeometryCommit = "\(width):\(contentHeight):\(itemMeasurementRevision)"
        }

        private func refreshVisibleMessage(at index: Int) {
            guard renderedItems.indices.contains(index), case .message(let block) = renderedItems[index],
                  let item = collectionView?.item(at: IndexPath(item: index, section: 0))
            else { return }
            configureMessageItem(item, block: block)
        }

        func numberOfSections(in collectionView: NSCollectionView) -> Int { 1 }

        func collectionView(
            _ collectionView: NSCollectionView,
            numberOfItemsInSection section: Int
        ) -> Int {
            guard usableWidth(in: collectionView) > 1 else { return 0 }
            return renderedItems.count
        }

        func collectionView(
            _ collectionView: NSCollectionView,
            itemForRepresentedObjectAt indexPath: IndexPath
        ) -> NSCollectionViewItem {
            guard renderedItems.indices.contains(indexPath.item) else {
                return NSCollectionViewItem()
            }
            switch renderedItems[indexPath.item] {
            case .workProcess(let process):
                let item = collectionView.makeItem(withIdentifier: WorkProcessCollectionItem.identifier,
                    for: indexPath) as! WorkProcessCollectionItem
                configureWorkProcess(item, process: process)
                return item
            case .message(let block):
                let item = collectionView.makeItem(
                    withIdentifier: block.question == nil ? TranscriptCollectionItem.identifier : AgentQuestionCollectionItem.identifier,
                    for: indexPath
                )
                configureMessageItem(item, block: block)
                return item
            case .agentSwitch(let state):
                let item = collectionView.makeItem(
                    withIdentifier: AgentSwitchCollectionItem.identifier,
                    for: indexPath
                ) as! AgentSwitchCollectionItem
                configureAgentSwitch(item, state: state)
                return item
            }
        }

        private func configureWorkProcess(_ item: WorkProcessCollectionItem, process: ConversationWorkProcess) {
            guard let collectionView else { return }
            item.view.isHidden = false
            let width = itemWidth(in: collectionView)
            item.configure(process: process, parent: parent,
                size: NSSize(width: width, height: workProcessHeight(process, width: width)))
        }

        private func refreshVisibleWorkProcess(at index: Int) {
            guard case .workProcess(let process) = renderedItems[index],
                  let item = collectionView?.item(at: IndexPath(item: index, section: 0))
                    as? WorkProcessCollectionItem else { return }
            configureWorkProcess(item, process: process)
        }

        private func workProcessHeight(_ process: ConversationWorkProcess, width: CGFloat) -> CGFloat {
            let preferred = WorkProcessCollectionItem.maximumHeight(textSize: parent.textSize)
            let insets = (collectionView?.collectionViewLayout as? NSCollectionViewFlowLayout)?.sectionInset
                ?? NSEdgeInsetsZero
            let viewportHeight = scrollView?.contentSize.height ?? 0
            let maximum = viewportHeight > 0
                ? min(preferred, max(1, viewportHeight - max(insets.top, stickyAgentSwitchHeight) - insets.bottom))
                : preferred
            var height: CGFloat = 16
            for (index, block) in process.blocks.enumerated() {
                height += measuredHeight(for: block,
                    availableWidth: max(1, width - ParallelCodexVisualStyle.Layout.workProcessIndent - 4))
                    + (index == 0 ? 0 : WorkOperationPresentation.rowSpacing)
                if height >= maximum { return maximum }
            }
            return ceil(height)
        }

        private var stickyAgentSwitchHeight: CGFloat {
            renderedItems.contains {
                if case .agentSwitch(let state) = $0 { return state.agents.count > 1 }
                return false
            } ? ConversationAgentSwitchBar.stickyHeight(for: parent.textSize) : 0
        }

        private func configureMessageItem(_ item: NSCollectionViewItem?, block: TranscriptBlock) {
            if let question = block.question, let item = item as? AgentQuestionCollectionItem,
               let collectionView {
                let width = itemWidth(in: collectionView)
                var actions = parent.questionActions
                actions.toggleDetails = { [weak self] question in
                    guard let self else { return }
                    self.prepareInlineControlMutation(id: "message:\(block.id)")
                    self.parent.questionActions.toggleDetails(question)
                }
                item.configure(question: question, language: parent.language, textSize: parent.textSize,
                    accentColor: parent.accentColor, appearance: parent.appearance, availableWidth: width,
                    actions: actions, interactionChanged: { [weak self] in self?.setAgentSwitchInteraction($0) },
                    heightChanged: { [weak self] height in
                        DispatchQueue.main.async { [weak self] in
                            self?.questionHeightChanged(height, block: block, width: width)
                        }
                    })
            } else if let item = item as? TranscriptCollectionItem {
                configureMessage(item, block: block)
            }
        }

        private func questionHeightChanged(_ height: CGFloat, block: TranscriptBlock, width: CGFloat) {
            guard height.isFinite, height > 0, let collectionView,
                  !collectionUpdateInFlight, !liveResizeInProgress, !userScrollInProgress,
                  abs(itemWidth(in: collectionView) - width) < 0.5,
                  renderedItems.contains(.message(block)) else { return }
            let id = "message:\(block.id)"
            let value = max(24, ceil(height))
            guard abs((itemMeasurements[id]?.height ?? 0) - value) > 0.5 else { return }
            let snapshot = currentSnapshot()
            itemMeasurements[id] = ItemMeasurement(block: block,
                referenceWidth: AgentQuestionCollectionItem.contentWidth(width), height: value)
            itemMeasurementRevision &+= 1
            cachedContentHeight = nil
            historyQuestionOffsets = nil
            if pendingInlineControlAnchor == nil {
                pendingRestoreSnapshot = snapshot
                isRestoring = snapshot != nil
            }
            commitItemGeometry(in: collectionView)
        }

        private func configureMessage(_ item: TranscriptCollectionItem, block: TranscriptBlock) {
            guard let collectionView else { return }
            item.view.isHidden = false
            item.configure(
                block: block, language: parent.language, textSize: parent.textSize,
                accentColor: parent.accentColor, appearance: parent.appearance,
                findQuery: parent.findQuery,
                currentFindOccurrence: parent.findMatch?.itemID == "message:\(block.id)"
                    ? parent.findMatch?.occurrence : nil,
                availableWidth: itemWidth(in: collectionView), openURL: { [weak self] url in
                    self?.parent.openURL(url, block)
                },
                activateBlock: { [weak self] activatedBlock in
                    guard let self else { return }
                    self.prepareInlineControlMutation(id: "message:\(activatedBlock.id)")
                    self.parent.activateBlock(activatedBlock)
                }
            )
        }

        private func refreshVisibleAgentSwitch(at explicitIndex: Int? = nil) {
            guard let collectionView,
                  let index = explicitIndex ?? renderedItems.firstIndex(where: {
                    if case .agentSwitch = $0 { return true }
                    return false
                  }),
                  renderedItems.indices.contains(index),
                  case .agentSwitch(let state) = renderedItems[index],
                  let item = collectionView.item(
                      at: IndexPath(item: index, section: 0)
                  ) as? AgentSwitchCollectionItem else { return }
            configureAgentSwitch(item, state: state)
        }

        private func configureAgentSwitch(_ item: AgentSwitchCollectionItem, state: ConversationAgentSwitchState) {
            item.view.isHidden = false
            item.configure(
                language: parent.language,
                textSize: parent.textSize,
                accentColor: parent.accentColor,
                state: state,
                resultsInspectorVisible: parent.resultsInspectorVisible,
                accessibilityHidden: transcriptInlineControlAccessibilityHidden(
                    parent.inlineAgentSwitchPosition
                ),
                selectAgent: { [weak self] agent in
                    guard let self else { return }
                    self.prepareInlineControlMutation(id: "agent-switch:\(state.runID)")
                    self.parent.selectAgent(agent)
                },
                toggleResults: parent.toggleResults,
                interactionChanged: { [weak self] active in
                    self?.setAgentSwitchInteraction(active)
                }
            )
        }

        private func setAgentSwitchInteraction(_ active: Bool) {
            guard agentSwitchInteractionInProgress != active else { return }
            agentSwitchInteractionInProgress = active
            guard !active, let deferred = deferredInteractionUpdate else { return }
            deferredInteractionUpdate = nil
            update(parent: deferred)
        }

        func collectionView(
            _ collectionView: NSCollectionView,
            layout collectionViewLayout: NSCollectionViewLayout,
            sizeForItemAt indexPath: IndexPath
        ) -> NSSize {
            let width = itemWidth(in: collectionView)
            guard renderedItems.indices.contains(indexPath.item) else {
                return NSSize(width: width, height: 1)
            }
            switch renderedItems[indexPath.item] {
            case .workProcess(let process):
                return NSSize(width: width, height: workProcessHeight(process, width: width))
            case .message(let block):
                let height: CGFloat
                let liveResize = liveResizeInProgress || collectionView.inLiveResize
                if liveResize, let measured = itemMeasurements[renderedItems[indexPath.item].id] {
                    height = transcriptEstimatedLiveResizeHeight(
                        measuredHeight: measured.height,
                        measuredWidth: measured.referenceWidth,
                        targetWidth: TranscriptCollectionItem.layoutReferenceWidth(
                            for: block,
                            availableWidth: width
                        )
                    )
                } else {
                    height = measuredHeight(for: block, availableWidth: width)
                }
                return NSSize(
                    width: width,
                    height: height
                )
            case .agentSwitch:
                return NSSize(
                    width: width,
                    height: AgentSwitchCollectionItem.preferredHeight(for: parent.textSize)
                )
            }
        }

        func beginLiveResize() {
            endScrollCoalescing(discard: false)
            guard !liveResizeInProgress else { return }
            cancelScrollMotion()
            liveResizeInProgress = true
            resizeWorkItem?.cancel()
            resizeWorkItem = nil
            captureResizeSnapshotIfNeeded()
        }

        func collectionWidthDidChange(duringLiveResize: Bool) {
            guard let collectionView else { return }
            if duringLiveResize, !liveResizeInProgress {
                beginLiveResize()
            }
            let width = itemWidth(in: collectionView)
            guard abs(width - lastWidth) > 0.5 else { return }
            historyQuestionOffsets = nil
            let becameUsable = transcriptWidthBecameUsable(
                previousWidth: lastWidth,
                currentWidth: width
            )
            lastWidth = width
            captureResizeSnapshotIfNeeded()
            let insetsChanged = updateSectionInsets(for: collectionView.bounds.width)
            // The representable can receive its first data reload before
            // SwiftUI has assigned any width. The data source deliberately
            // returns zero items in that provisional state; reload once when
            // the collection obtains real geometry so the transcript cannot
            // remain blank until some unrelated model update arrives.
            if becameUsable {
                collectionView.reloadData()
            }
            if duringLiveResize || liveResizeInProgress {
                // Coalesce visible text reflow, not the window's geometry.
                // Offscreen rows keep cheap estimates during the gesture.
                if insetsChanged {
                    collectionView.collectionViewLayout?.invalidateLayout()
                }
                scheduleLiveReflow()
                return
            }
            cachedContentHeight = nil
            scheduleResizeFinalization()
        }

        private func viewportHeightDidChange() {
            guard renderedItems.contains(where: {
                if case .workProcess = $0 { return true }
                return false
            }) else { return }
            captureResizeSnapshotIfNeeded()
            cachedContentHeight = nil
            historyQuestionOffsets = nil
            if liveResizeInProgress {
                scheduleLiveReflow()
            } else {
                scheduleResizeFinalization()
            }
        }

        func endLiveResize() {
            guard liveResizeInProgress else { return }
            liveResizeInProgress = false
            liveReflowWorkItem?.cancel()
            liveReflowWorkItem = nil
            scheduleResizeFinalization()
        }

        private func scheduleLiveReflow() {
            guard liveReflowWorkItem == nil else { return }
            let work = DispatchWorkItem { [weak self] in
                guard let self else { return }
                self.liveReflowWorkItem = nil
                guard self.liveResizeInProgress, let collection = self.collectionView else { return }
                let viewport = self.scrollView as? TranscriptViewportScrollView
                viewport?.beginViewportUpdate()
                defer { viewport?.endViewportUpdate() }
                self.isRestoring = true
                self.updateVisibleItemWidths()
                collection.collectionViewLayout?.invalidateLayout()
                collection.layoutSubtreeIfNeeded()
                self.cachedContentHeight = nil
                self.synchronizeDocumentGeometry()
                self.restore(snapshot: self.resizeSnapshot)
            }
            liveReflowWorkItem = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0 / 30, execute: work)
        }

        private func captureResizeSnapshotIfNeeded() {
            guard resizeSnapshot == nil else { return }
            if isRestoring {
                // Initial layout commonly reports its first width while a
                // persisted position is still being restored. Never let that
                // temporary origin replace the persisted position.
                resizeSnapshot = pendingRestoreSnapshot
                    ?? parent.memory.snapshot(for: activeContext)
                    ?? TranscriptScrollMemory.Snapshot(
                        offsetY: .greatestFiniteMagnitude,
                        followsLatest: true
                    )
            } else {
                resizeSnapshot = currentSnapshot()
                    ?? parent.memory.snapshot(for: activeContext)
                    ?? TranscriptScrollMemory.Snapshot(
                        offsetY: .greatestFiniteMagnitude,
                        followsLatest: true
                    )
            }
        }

        private func scheduleResizeFinalization() {
            resizeWorkItem?.cancel()
            let generation = scrollRequestGeneration
            let work = DispatchWorkItem { [weak self] in
                guard let self, !self.liveResizeInProgress,
                      self.scrollRequestGeneration == generation else { return }
                self.finishResize()
            }
            resizeWorkItem = work
            DispatchQueue.main.async(execute: work)
        }

        private func finishResize() {
            guard let collectionView else { return }
            let workViewports = collectionView.visibleItems().compactMap { $0 as? WorkProcessCollectionItem }
            workViewports.forEach { $0.beginViewportResize() }
            defer { workViewports.forEach { $0.endViewportResize() } }
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            resizeWorkItem = nil
            isRestoring = true
            lastWidth = itemWidth(in: collectionView)
            cachedContentHeight = nil
            historyQuestionOffsets = nil
            updateSectionInsets(for: collectionView.bounds.width)
            collectionView.collectionViewLayout?.invalidateLayout()
            collectionView.layoutSubtreeIfNeeded()
            updateVisibleItemWidths()
            synchronizeDocumentGeometry()
            let snapshot = resizeSnapshot
            resizeSnapshot = nil
            // Reflow and anchor restoration must reach the screen together.
            if pendingRestoreSnapshot != nil {
                restorePendingPosition()
            } else {
                restore(snapshot: snapshot)
            }
        }

        private func updateVisibleItemWidths() {
            guard let collectionView else { return }
            let width = itemWidth(in: collectionView)
            for indexPath in collectionView.indexPathsForVisibleItems() {
                guard renderedItems.indices.contains(indexPath.item),
                      case .message(let block) = renderedItems[indexPath.item] else {
                    continue
                }
                _ = measuredHeight(for: block, availableWidth: width)
                if block.question != nil {
                    configureMessageItem(collectionView.item(at: indexPath), block: block)
                } else {
                    (collectionView.item(at: indexPath) as? TranscriptCollectionItem)?.updateAvailableWidth(width)
                }
            }
        }

        private func measuredHeight(for block: TranscriptBlock, availableWidth: CGFloat) -> CGFloat {
            let id = "message:\(block.id)"
            let width = block.question == nil
                ? TranscriptCollectionItem.layoutReferenceWidth(for: block, availableWidth: availableWidth)
                : AgentQuestionCollectionItem.contentWidth(availableWidth)
            if let measurement = itemMeasurements[id],
               measurement.referenceWidth == width, measurement.block == block {
                return measurement.height
            }
            let height: CGFloat
            if let question = block.question {
                height = AgentQuestionCollectionItem.preferredHeight(question: question, language: parent.language,
                    textSize: parent.textSize, accentColor: parent.accentColor, appearance: parent.appearance,
                    availableWidth: availableWidth)
            } else {
                height = TranscriptCollectionItem.preferredHeight(
                for: block, language: parent.language, textSize: parent.textSize,
                accentColor: parent.accentColor, appearance: parent.appearance,
                availableWidth: availableWidth
                )
            }
            if itemMeasurements[id]?.height != height || itemMeasurements[id]?.referenceWidth != width {
                itemMeasurementRevision &+= 1
            }
            itemMeasurements[id] = ItemMeasurement(block: block, referenceWidth: width, height: height)
            return height
        }

        private func trimInactiveMeasurements(retaining items: [ConversationTimelineItem]) {
            // Collapsing work should not discard the heights needed on reopen.
            // Content and width are checked on reuse; presentation/context
            // changes clear the cache. Bound retention to the active timeline.
            guard itemMeasurements.count > 8192 else { return }
            let activeIDs = Set(items.map(\.id))
            itemMeasurements = itemMeasurements.filter { activeIDs.contains($0.key) }
        }

        private func itemWidth(in collectionView: NSCollectionView) -> CGFloat {
            // Flow layout treats an item that rounds up to the available width
            // as invalid. Account for both inset layers and leave a small
            // tolerance so resizing never enters its undefined fallback.
            return transcriptMeasurementWidth(
                max(1, floor(usableWidth(in: collectionView)) - 4),
                duringLiveResize: liveResizeInProgress || collectionView.inLiveResize
            )
        }

        private func usableWidth(in collectionView: NSCollectionView) -> CGFloat {
            let insets = (collectionView.collectionViewLayout as? NSCollectionViewFlowLayout)?
                .sectionInset ?? NSEdgeInsetsZero
            let contentInsets = collectionView.enclosingScrollView?.contentInsets
                ?? NSEdgeInsetsZero
            // During a split-pane resize AppKit updates the clip before its
            // document. Never size a row using the previous, wider document.
            let viewportWidth = collectionView.enclosingScrollView?.contentSize.width
                ?? collectionView.bounds.width
            return min(collectionView.bounds.width, viewportWidth)
                - insets.left
                - insets.right
                - contentInsets.left
                - contentInsets.right
        }

        @discardableResult
        private func updateSectionInsets(for width: CGFloat) -> Bool {
            guard let flow = collectionView?.collectionViewLayout as? NSCollectionViewFlowLayout else {
                return false
            }
            if parent.isWorkProcess { return false }
            let minimumEdge: CGFloat = width < 620
                ? ParallelCodexVisualStyle.Layout.transcriptNarrowInset
                : ParallelCodexVisualStyle.Layout.transcriptInset
            var horizontalInsets = transcriptHorizontalInsets(
                width: width,
                maximumContentWidth: ParallelCodexVisualStyle.Layout.transcriptMaximumWidth,
                minimumEdge: minimumEdge
            )
            if historyEntries.count > 1 {
                horizontalInsets.leading = max(44, horizontalInsets.leading)
            }
            guard abs(flow.sectionInset.left - horizontalInsets.leading) > 0.5
                    || abs(flow.sectionInset.right - horizontalInsets.trailing) > 0.5 else {
                return false
            }
            flow.sectionInset = NSEdgeInsets(
                top: 24,
                left: horizontalInsets.leading,
                bottom: 34,
                right: horizontalInsets.trailing
            )
            return true
        }

        private func restorePosition() {
            let context = activeContext
            let snapshot = parent.memory.snapshot(for: activeContext)
            let generation = scrollRequestGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self, self.activeContext == context,
                      self.scrollRequestGeneration == generation else { return }
                self.collectionView?.layoutSubtreeIfNeeded()
                self.restore(snapshot: snapshot)
            }
        }

        private func restorePendingPosition() {
            guard pendingInlineControlAnchor == nil,
                  !collectionUpdateInFlight,
                  let snapshot = pendingRestoreSnapshot else { return }
            let context = activeContext
            let generation = scrollRequestGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self,
                      self.activeContext == context,
                      self.scrollRequestGeneration == generation,
                      self.pendingRestoreSnapshot != nil else { return }
                self.collectionView?.layoutSubtreeIfNeeded()
                self.restore(snapshot: snapshot)
            }
        }

        private func captureInlineControlAnchor(
            id: String?,
            oldItems: [ConversationTimelineItem],
            force: Bool
        ) -> InlineControlAnchor? {
            guard let id,
                  let collectionView,
                  let scrollView,
                  let index = oldItems.firstIndex(where: { $0.id == id }),
                  let frame = collectionView.collectionViewLayout?
                    .layoutAttributesForItem(at: IndexPath(item: index, section: 0))?.frame,
                  force || collectionView.indexPathsForVisibleItems().contains(
                    IndexPath(item: index, section: 0)
                  ) else {
                return nil
            }
            return InlineControlAnchor(
                id: id,
                viewportOffset: collectionView.isFlipped
                    ? scrollView.contentView.documentVisibleRect.minY - frame.minY
                    : scrollView.contentView.documentVisibleRect.maxY - frame.maxY,
                usesFlippedCoordinates: collectionView.isFlipped
            )
        }

        func prepareInlineControlMutation(id: String) {
            endScrollCoalescing(discard: false)
            guard pendingInlineControlAnchor == nil,
                  let anchor = captureInlineControlAnchor(
                    id: id,
                    oldItems: renderedItems,
                    force: true
                  ) else { return }
            cancelScrollMotion()
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            pendingInlineControlAnchor = anchor
            pendingRestoreSnapshot = nil
            isRestoring = true
            scrollView?.window?.makeFirstResponder(collectionView)
        }

        private func restorePendingInlineControlAnchor(finalPass: Bool = false, duringBatch: Bool = false) {
            guard !collectionUpdateInFlight || duringBatch,
                  let anchor = pendingInlineControlAnchor,
                  let collectionView,
                  let scrollView,
                  let document = scrollView.documentView else {
                return
            }
            isRestoring = true
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            collectionView.layoutSubtreeIfNeeded()
            synchronizeDocumentGeometry()
            collectionView.layoutSubtreeIfNeeded()
            guard let index = renderedItems.firstIndex(where: { $0.id == anchor.id }),
                  let frame = collectionView.collectionViewLayout?
                    .layoutAttributesForItem(at: IndexPath(item: index, section: 0))?.frame else {
                if !finalPass {
                    let generation = scrollRequestGeneration
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { [weak self] in
                        guard self?.scrollRequestGeneration == generation else { return }
                        self?.restorePendingInlineControlAnchor(finalPass: true)
                    }
                } else {
                    pendingInlineControlAnchor = nil
                    isRestoring = false
                    recordScrollPosition()
                }
                return
            }
            let clip = scrollView.contentView
            let maximumY = max(document.bounds.minY, contentHeight - clip.bounds.height)
            let anchoredOffset = if anchor.usesFlippedCoordinates {
                frame.minY + anchor.viewportOffset
            } else {
                frame.maxY + anchor.viewportOffset - clip.bounds.height
            }
            // Keep the disclosure at the exact screen coordinate where the
            // user clicked it; never center it after inserting details.
            let target = min(
                max(anchoredOffset, document.bounds.minY),
                maximumY
            )
            let point = NSPoint(x: document.bounds.minX, y: target)
            if clip.bounds.origin != point {
                clip.scroll(to: point)
                scrollView.reflectScrolledClipView(clip)
            }

            // Commit the viewport in the same paint, but keep the semantic
            // anchor until AppKit finishes any insertion compensation.
            if collectionUpdateInFlight { return }

            if !finalPass {
                // AppKit may apply one last insertion compensation after the
                // current update. Correct the same anchor on the next turn
                // before persisting the viewport.
                let generation = scrollRequestGeneration
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { [weak self] in
                    guard self?.scrollRequestGeneration == generation else { return }
                    self?.restorePendingInlineControlAnchor(finalPass: true)
                }
                return
            }

            pendingInlineControlAnchor = nil
            isRestoring = false
            collectionView.needsDisplay = true
            scrollView.contentView.needsDisplay = true
            collectionView.displayIfNeeded()
            scrollView.contentView.displayIfNeeded()
            recordScrollPosition()
        }

        private func schedulePendingInlineControlRestore() {
            let context = activeContext
            let generation = scrollRequestGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self, self.activeContext == context,
                      self.scrollRequestGeneration == generation else { return }
                self.restorePendingInlineControlAnchor()
            }
        }

        private func schedulePresentationRestore() {
            guard pendingInlineControlAnchor == nil,
                  let snapshot = pendingRestoreSnapshot else { return }
            let context = activeContext
            let generation = scrollRequestGeneration
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            DispatchQueue.main.async { [weak self] in
                guard let self, self.activeContext == context,
                      self.scrollRequestGeneration == generation else { return }
                self.restore(snapshot: snapshot)

                // NSCollectionView applies one final scroll compensation after
                // reloadData returns. Keep the original semantic snapshot alive
                // through that pass so zooming at the bottom cannot settle part
                // way up the conversation and persist the wrong position.
                self.isRestoring = true
                let work = DispatchWorkItem { [weak self] in
                    guard let self, self.activeContext == context,
                          self.scrollRequestGeneration == generation else { return }
                    self.presentationRestoreWorkItem = nil
                    self.restore(snapshot: snapshot)
                }
                self.presentationRestoreWorkItem = work
                DispatchQueue.main.asyncAfter(
                    deadline: .now() + 0.12,
                    execute: work
                )
            }
        }

        private func restore(snapshot: TranscriptScrollMemory.Snapshot?) {
            guard let scrollView,
                  let collectionView,
                  let document = scrollView.documentView else {
                pendingRestoreSnapshot = nil
                isRestoring = false
                return
            }
            isRestoring = true
            let viewport = scrollView as? TranscriptViewportScrollView
            viewport?.beginViewportUpdate()
            defer { viewport?.endViewportUpdate() }
            // SwiftUI can ask the representable to restore before assigning
            // its first real width. Completing against that provisional
            // geometry records the origin as a user scroll and overwrites the
            // saved position. Hold the snapshot until the width-change path
            // has reloaded and measured the actual one-column timeline.
            guard transcriptRestoreWidthIsUsable(
                usableWidth(in: collectionView)
            ) else {
                pendingRestoreSnapshot = snapshot
                    ?? TranscriptScrollMemory.Snapshot(
                        offsetY: .greatestFiniteMagnitude,
                        followsLatest: true
                    )
                return
            }
            collectionView.layoutSubtreeIfNeeded()
            synchronizeDocumentGeometry()
            let clip = scrollView.contentView
            let maximumY = max(document.bounds.minY, contentHeight - clip.bounds.height)
            if let snapshot,
               !snapshot.followsLatest,
               shouldDeferRestore(snapshot, maximumY: maximumY) {
                pendingRestoreSnapshot = snapshot
                isRestoring = false
                return
            }
            let target: CGFloat
            if let snapshot, !snapshot.followsLatest {
                let anchored = anchoredOffset(for: snapshot)
                target = min(max(anchored ?? snapshot.offsetY, document.bounds.minY), maximumY)
            } else {
                target = maximumY
            }
            let point = NSPoint(x: document.bounds.minX, y: target)
            if clip.bounds.origin != point {
                clip.scroll(to: point)
                scrollView.reflectScrolledClipView(clip)
            }
            collectionView.needsDisplay = true
            collectionView.displayIfNeeded()
            clip.needsDisplay = true
            clip.displayIfNeeded()
            // AppKit may still compensate inserted/deleted rows at completion.
            // Keep the same intent, and suppress temporary bounds publication.
            if collectionUpdateInFlight { return }
            // Initial/context and resize restores also accept real geometry.
            // A later metadata poll must not invalidate that same layout again.
            lastGeometryCommit = "\(itemWidth(in: collectionView)):\(contentHeight):\(itemMeasurementRevision)"
            pendingRestoreSnapshot = nil
            isRestoring = false
            followsLatest = snapshot?.followsLatest ?? true
            recordScrollPosition()
        }

        private func shouldDeferRestore(
            _ snapshot: TranscriptScrollMemory.Snapshot,
            maximumY: CGFloat
        ) -> Bool {
            guard parent.contentIsLoading else { return false }
            guard snapshot.anchorID != nil,
                  let desiredOffset = anchoredOffset(for: snapshot) else { return true }
            return desiredOffset > maximumY + 1
        }

        private func beginUserScroll() {
            parent.onUserNavigation()
            userScrollInProgress = true
            scheduleScrollIdle(after: trackingScroll || momentumScroll ? 1 : ConversationTimelineScrollView.scrollIdleInterval)
            cancelScrollMotion()
            // A deliberate scroll supersedes restoration queued by a refresh,
            // disclosure or typography change, including its delayed pass.
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            resizeWorkItem?.cancel()
            resizeWorkItem = nil
            resizeSnapshot = nil
            pendingRestoreSnapshot = nil
            pendingInlineControlAnchor = nil
            isRestoring = false
        }

        func observeScrollEvent(_ event: NSEvent) {
            beginUserScroll()
            if event.phase.contains(.began) || event.phase.contains(.changed) {
                trackingScroll = true
            }
            if event.phase.contains(.ended) || event.phase.contains(.cancelled) {
                trackingScroll = false
            }
            if event.momentumPhase.contains(.began) || event.momentumPhase.contains(.changed) {
                momentumScroll = true
                trackingScroll = false
            }
            if event.momentumPhase.contains(.ended) || event.momentumPhase.contains(.cancelled) {
                momentumScroll = false
            }
            userScrollInProgress = true
            // The quiet gap after finger-up allows AppKit's momentum begin to
            // arrive. The longer watchdog also releases cancelled/lost phases.
            scheduleScrollIdle(after: trackingScroll || momentumScroll ? 1 : ConversationTimelineScrollView.scrollIdleInterval)
        }

        private func scheduleScrollIdle(after delay: TimeInterval) {
            scrollIdleWorkItem?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.endScrollCoalescing(discard: false) }
            scrollIdleWorkItem = work
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
        }

        private func endScrollCoalescing(discard: Bool) {
            scrollIdleWorkItem?.cancel()
            scrollIdleWorkItem = nil
            userScrollInProgress = false
            trackingScroll = false
            momentumScroll = false
            let deferred = deferredScrollUpdate
            deferredScrollUpdate = nil
            if !discard, let deferred, deferred.contextID == activeContext {
                update(parent: deferred)
            }
        }

        private func cancelScrollMotion() {
            scrollRequestGeneration &+= 1
            scrollMotion.cancel()
        }

        private func scrollToLatest(animated: Bool = false) {
            presentationRestoreWorkItem?.cancel()
            presentationRestoreWorkItem = nil
            pendingRestoreSnapshot = nil
            pendingInlineControlAnchor = nil
            followsLatest = true
            let context = activeContext
            scrollRequestGeneration &+= 1
            let generation = scrollRequestGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self, self.activeContext == context,
                      self.scrollRequestGeneration == generation,
                      let scroll = self.scrollView else { return }
                self.collectionView?.layoutSubtreeIfNeeded()
                self.synchronizeDocumentGeometry()
                self.isRestoring = false
                let y = max(0, self.contentHeight - scroll.contentSize.height)
                self.scrollMotion.move(to: NSPoint(x: 0, y: y), in: scroll, animated: animated)
            }
        }

        private var contentHeight: CGFloat {
            guard let collectionView,
                  !renderedItems.isEmpty,
                  let flow = collectionView.collectionViewLayout as? NSCollectionViewFlowLayout else {
                return 0
            }

            if liveResizeInProgress {
                if let cachedContentHeight { return cachedContentHeight }
                return max(0, flow.collectionViewContentSize.height)
            }

            // Flow layout attributes can retain an old document-sized frame
            // after a branch or width change. Derive the document end from the
            // exact same cached item measurements used by sizeForItem instead.
            let width = itemWidth(in: collectionView)
            if let cachedContentHeight,
               abs(width - cachedContentWidth) <= 0.5 {
                return cachedContentHeight
            }
            let itemHeights = renderedItems.reduce(CGFloat.zero) { partial, item in
                switch item {
                case .workProcess(let process):
                    return partial + workProcessHeight(process, width: width)
                case .message(let block):
                    return partial + measuredHeight(for: block, availableWidth: width)
                case .agentSwitch:
                    return partial + AgentSwitchCollectionItem.preferredHeight(
                        for: parent.textSize
                    )
                }
            }
            let gaps = flow.minimumLineSpacing * CGFloat(max(0, renderedItems.count - 1))
            let value = max(
                0,
                flow.sectionInset.top + itemHeights + gaps + flow.sectionInset.bottom
            )
            cachedContentWidth = width
            cachedContentHeight = value
            return value
        }

        private func synchronizeDocumentGeometry() {
            guard let scrollView, let collectionView else { return }
            let clipSize = scrollView.contentView.bounds.size
            let targetHeight = max(clipSize.height, contentHeight)
            var frame = collectionView.frame
            let targetWidth = clipSize.width
            if abs(frame.height - targetHeight) > 0.5
                    || abs(frame.width - targetWidth) > 0.5 {
                frame.size = NSSize(width: targetWidth, height: targetHeight)
                collectionView.frame = frame
                collectionView.needsLayout = true
                collectionView.needsDisplay = true
            }

            // This timeline never scrolls horizontally. AppKit can preserve a
            // stale negative X origin when the document width changes during a
            // workspace switch or live resize, which shifts trailing user
            // bubbles beyond the window edge. Re-anchor the clip view whenever
            // document geometry is synchronized.
            let clip = scrollView.contentView
            let horizontalOrigin = collectionView.bounds.minX
            guard abs(clip.bounds.minX - horizontalOrigin) > 0.5 else { return }
            clip.scroll(to: NSPoint(x: horizontalOrigin, y: clip.bounds.minY))
            scrollView.reflectScrolledClipView(clip)
        }

        private func savePosition() {
            guard !isRestoring,
                  pendingRestoreSnapshot == nil,
                  pendingInlineControlAnchor == nil,
                  !activeContext.isEmpty,
                  let snapshot = currentSnapshot() else { return }
            parent.memory.save(snapshot, for: activeContext)
        }

        private func recordScrollPosition(viewportChanged: Bool = false) {
            guard !isRestoring, !collectionUpdateInFlight, !liveResizeInProgress,
                  resizeSnapshot == nil, !scrollMotion.isAnimating else { return }
            if viewportChanged { followsLatest = nearBottom() }
            updateHistoryRailPosition()
            savePosition()
            let value = nearBottom()
            if value != lastPublishedNearBottom {
                lastPublishedNearBottom = value
                let context = activeContext
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.activeContext == context,
                          self.lastPublishedNearBottom == value else { return }
                    // The binding can already read this value from scroll
                    // memory without having published it to SwiftUI state.
                    self.parent.isNearBottom = value
                }
            }

            let switchPosition = inlineAgentSwitcherPosition()
            (scrollView as? ConversationTimelineScrollView)?.obscuredTopHeight =
                switchPosition == .aboveViewport ? stickyAgentSwitchHeight : 0
            if switchPosition != lastPublishedAgentSwitchPosition {
                lastPublishedAgentSwitchPosition = switchPosition
                let context = activeContext
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.activeContext == context,
                          self.lastPublishedAgentSwitchPosition == switchPosition,
                          self.parent.inlineAgentSwitchPosition != switchPosition else { return }
                    self.parent.inlineAgentSwitchPosition = switchPosition
                }
            }
        }

        private func inlineAgentSwitcherPosition() -> TranscriptInlineControlPosition {
            guard let collectionView,
                  let scrollView,
                  let index = renderedItems.firstIndex(where: {
                      if case .agentSwitch = $0 { return true }
                      return false
                  }),
                  let frame = collectionView.collectionViewLayout?
                    .layoutAttributesForItem(at: IndexPath(item: index, section: 0))?.frame else {
                return .missing
            }
            let visible = scrollView.contentView.documentVisibleRect
            return transcriptInlineControlPosition(
                itemMinY: frame.minY,
                itemMaxY: frame.maxY,
                visibleMinY: visible.minY,
                visibleMaxY: visible.maxY
            )
        }

        private func currentSnapshot() -> TranscriptScrollMemory.Snapshot? {
            guard let scrollView, let collectionView else { return nil }
            let visibleRect = scrollView.contentView.documentVisibleRect
            let candidates = collectionView.indexPathsForVisibleItems()
                .compactMap { indexPath -> TranscriptViewportAnchorCandidate? in
                    guard renderedItems.indices.contains(indexPath.item),
                          let frame = collectionView.collectionViewLayout?
                            .layoutAttributesForItem(at: indexPath)?.frame else {
                        return nil
                    }
                    return TranscriptViewportAnchorCandidate(
                        itemIndex: indexPath.item,
                        minY: frame.minY,
                        maxY: frame.maxY
                    )
                }
            let anchor = transcriptPreferredViewportAnchor(
                visibleMinY: visibleRect.minY,
                visibleMaxY: visibleRect.maxY,
                candidates: candidates
            )
            let textAnchor = anchor.flatMap { candidate in
                (collectionView.item(at: IndexPath(item: candidate.itemIndex, section: 0)) as? TranscriptCollectionItem)?
                    .readingAnchor(at: visibleRect.minY - candidate.minY)
            }
            return .init(
                offsetY: visibleRect.minY,
                followsLatest: followsLatest,
                anchorID: anchor.map { renderedItems[$0.itemIndex].id },
                anchorOffset: anchor.map { visibleRect.minY - $0.minY } ?? 0,
                textAnchor: textAnchor
            )
        }

        private func snapshotPreservingConversationTrunk(
            _ snapshot: TranscriptScrollMemory.Snapshot?,
            oldItems: [ConversationTimelineItem],
            newItems: [ConversationTimelineItem]
        ) -> TranscriptScrollMemory.Snapshot? {
            guard let snapshot, let collectionView, let scrollView else {
                return snapshot
            }
            let visible = scrollView.contentView.documentVisibleRect
            if let controlID = ConversationTimelineProjection.changedInlineControlID(
                    old: oldItems,
                    new: newItems
               ),
               let controlIndex = oldItems.firstIndex(where: { $0.id == controlID }),
               let frame = collectionView.collectionViewLayout?
                    .layoutAttributesForItem(
                        at: IndexPath(item: controlIndex, section: 0)
                    )?.frame,
               (controlID.hasPrefix("message:work-summary-")
                    || collectionView.indexPathsForVisibleItems().contains(
                        IndexPath(item: controlIndex, section: 0)
                    )) {
                // The disclosure or branch selector is the thing the user
                // acted on. Keep that control at the same screen coordinate
                // while its content changes around it.
                return TranscriptScrollMemory.Snapshot(
                    offsetY: snapshot.offsetY,
                    followsLatest: false,
                    anchorID: controlID,
                    anchorOffset: visible.minY - frame.minY
                )
            }
            guard
                  !snapshot.followsLatest,
                  let anchorID = snapshot.anchorID,
                  !newItems.contains(where: { $0.id == anchorID }),
                  let switchIndex = oldItems.firstIndex(where: {
                      if case .agentSwitch = $0 { return true }
                      return false
                  }),
                  let switchID = oldItems.indices.contains(switchIndex)
                    ? Optional(oldItems[switchIndex].id)
                    : nil,
                  newItems.contains(where: { $0.id == switchID }),
                  let frame = collectionView.collectionViewLayout?
                    .layoutAttributesForItem(
                        at: IndexPath(item: switchIndex, section: 0)
                    )?.frame else {
                return snapshot
            }
            return TranscriptScrollMemory.Snapshot(
                offsetY: snapshot.offsetY,
                followsLatest: false,
                anchorID: switchID,
                anchorOffset: scrollView.contentView.documentVisibleRect.minY - frame.minY
            )
        }

        private func anchoredOffset(
            for snapshot: TranscriptScrollMemory.Snapshot
        ) -> CGFloat? {
            guard let collectionView,
                  let anchorID = snapshot.anchorID,
                  let index = renderedItems.firstIndex(where: { $0.id == anchorID }),
                  let layout = collectionView.collectionViewLayout,
                  let attributes = layout.layoutAttributesForItem(
                    at: IndexPath(item: index, section: 0)
                  ) else {
                return nil
            }
            let frame = attributes.frame
            // During the first layout pass AppKit may return an origin frame
            // for an offscreen item. In a single-column timeline any later
            // item must be below the first one, so reject that provisional
            // geometry instead of snapping a saved position to the top.
            guard transcriptAnchorGeometryIsResolved(
                anchorIndex: index,
                firstItemMinY: layout.layoutAttributesForItem(
                    at: IndexPath(item: 0, section: 0)
                )?.frame.minY,
                anchorMinY: frame.minY,
                anchorHeight: frame.height
            ) else {
                return nil
            }
            if let anchor = snapshot.textAnchor,
               let item = collectionView.item(at: IndexPath(item: index, section: 0)) as? TranscriptCollectionItem,
               let offset = item.readingOffset(for: anchor) {
                return frame.minY + offset
            }
            return frame.minY + snapshot.anchorOffset
        }

        private func nearBottom() -> Bool {
            guard let scrollView else { return true }
            let clip = scrollView.contentView
            let distance = contentHeight - clip.documentVisibleRect.maxY
            return transcriptIsNearBottom(
                distance: distance,
                viewportHeight: clip.documentVisibleRect.height
            )
        }

    }
}
