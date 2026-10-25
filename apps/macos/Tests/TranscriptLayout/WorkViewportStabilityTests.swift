import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkViewportStabilityTests {
    @Test
    func queuedHeightRestoreCannotUndoAWorkScrollHandoff() async throws {
        let fixture = Fixture(height: 500,
            leadingText: (0..<100).map { "An earlier conversation paragraph \($0)." }.joined(separator: "\n"),
            trailingTexts: Array(repeating: "A later conversation paragraph.", count: 12))
        defer { fixture.close() }
        await fixture.settle()
        let collection = try #require(fixture.scroll.documentView as? NSCollectionView)
        let frame = try #require(collection.collectionViewLayout?.layoutAttributesForItem(at:
            IndexPath(item: 2, section: 0))).frame
        let clip = fixture.scroll.contentView
        clip.scroll(to: NSPoint(x: 0, y: frame.minY - clip.bounds.height + 24))
        fixture.scroll.reflectScrolledClipView(clip)
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: 600))
        inner.reflectScrolledClipView(inner.contentView)
        fixture.coordinator.beginLiveResize()
        fixture.window.setContentSize(NSSize(width: 1173, height: 300))
        fixture.scroll.setFrameSize(NSSize(width: 1173, height: 300))
        fixture.scroll.layoutSubtreeIfNeeded()
        clip.scroll(to: NSPoint(x: 0, y: frame.minY - clip.bounds.height + 24))
        fixture.scroll.reflectScrolledClipView(clip)
        fixture.coordinator.endLiveResize()
        let reading = clip.bounds.minY
        inner.scrollWheel(with: try wheel(-35, phase: .began))
        #expect(abs(clip.bounds.minY - reading - 35) < 1)
        await fixture.settle()
        #expect(abs(clip.bounds.minY - reading - 35) < 1)
        #expect(inner.contentView.bounds.minY == 600)
        #expect(inner.frame.height <= clip.bounds.height - 58 + 1)
    }

    @Test
    func contextChangeAndDetachRetireTheConversationScrollOwner() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.scroll.claimScrollGesture()
        fixture.contextID = "another-conversation"
        fixture.update()
        #expect(!fixture.scroll.ownsScrollGesture)
        await fixture.settle()
        fixture.scroll.claimScrollGesture()
        fixture.coordinator.detach()
        #expect(!fixture.scroll.ownsScrollGesture)
    }

    @Test(arguments: [AppTextSize.standard, .extraLarge])
    func clippedProductionWorkLogScrollsPastWithoutReadingEveryCommand(size: AppTextSize) async throws {
        let fixture = Fixture(textSize: size, height: 330,
            leadingText: (0..<35).map { "Earlier conversation paragraph \($0)." }.joined(separator: "\n"),
            trailingTexts: (0..<3).map { paragraph in
                (0..<35).map { "Conversation after the work process \(paragraph)-\($0)." }.joined(separator: "\n")
            })
        defer { fixture.close() }
        await fixture.settle()
        let collection = try #require(fixture.scroll.documentView as? NSCollectionView)
        let frame = try #require(collection.collectionViewLayout?.layoutAttributesForItem(at:
            IndexPath(item: 2, section: 0))).frame
        let clip = fixture.scroll.contentView
        clip.scroll(to: NSPoint(x: 0, y: frame.minY - clip.bounds.height + 24))
        fixture.scroll.reflectScrolledClipView(clip)
        await fixture.settle()
        let inner = try fixture.workScroll(stage: "one-row")
        inner.contentView.scroll(to: NSPoint(x: 0, y: 600))
        inner.reflectScrolledClipView(inner.contentView)
        let visibleHeight = inner.bounds.intersection(inner.convert(clip.documentVisibleRect, from: collection)).height
        #expect(abs(visibleHeight - 24) < 1)
        #expect(fixture.maximum(in: inner) > 4000)
        try fixture.capture("one-row-\(size.rawValue)")

        let reading = clip.bounds.minY
        inner.scrollWheel(with: try wheel(-40, phase: .began))
        #expect(abs(clip.bounds.minY - reading - 40) < 1)
        #expect(inner.contentView.bounds.minY == 600)
        let revealDelta = Int32((frame.minY - 20 - clip.bounds.minY).rounded())
        inner.scrollWheel(with: try wheel(-revealDelta, phase: .changed))
        #expect(inner.bounds.intersection(inner.convert(clip.documentVisibleRect, from: collection)).height
            >= inner.bounds.height - 1)
        try fixture.capture("revealed-\(size.rawValue)")
        let revealedReading = clip.bounds.minY
        inner.scrollWheel(with: try wheel(-35, phase: .changed))
        inner.scrollWheel(with: try wheel(0, phase: .ended))
        inner.scrollWheel(with: try wheel(-45, momentum: 1))
        #expect(abs(clip.bounds.minY - revealedReading - 80) < 1)
        #expect(inner.contentView.bounds.minY == 600)
        let pastDelta = Int32((frame.maxY + 30 - clip.bounds.minY).rounded())
        inner.scrollWheel(with: try wheel(-pastDelta, momentum: 2))
        inner.scrollWheel(with: try wheel(0, momentum: 3))
        #expect(clip.bounds.minY > frame.maxY)
        #expect(inner.contentView.bounds.minY == 600)
        try fixture.capture("past-work-\(size.rawValue)")

        clip.scroll(to: NSPoint(x: 0, y: frame.minY - 20))
        fixture.scroll.reflectScrolledClipView(clip)
        collection.layoutSubtreeIfNeeded()
        await fixture.settle()
        let readable = try fixture.workScroll(stage: "fresh-reading")
        readable.contentView.scroll(to: NSPoint(x: 0, y: 600))
        readable.reflectScrolledClipView(readable.contentView)
        let outerReading = clip.bounds.minY
        readable.scrollWheel(with: try wheel(-30, phase: .began))
        #expect(readable.contentView.bounds.minY == 630)
        #expect(clip.bounds.minY == outerReading)
    }

    @Test(arguments: [AppTextSize.standard, .extraLarge], [true, false])
    func workViewportFitsTheConversationWhenOnlyItsHeightChanges(size: AppTextSize, following: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.textSize = size
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        let originalHeight = inner.frame.height
        inner.contentView.scroll(to: NSPoint(x: 0, y: following ? fixture.maximum(in: inner) : 600))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        fixture.window.setContentSize(NSSize(width: 1173, height: 240))
        fixture.scroll.setFrameSize(NSSize(width: 1173, height: 240))
        await fixture.settle()
        #expect(try fixture.workScroll() === inner)
        #expect(inner.frame.height <= fixture.scroll.contentSize.height - 58 + 1)
        #expect(inner.frame.height > 60)
        #expect(inner.frame.height < originalHeight)
        #expect(abs(inner.contentView.bounds.minY - (following ? fixture.maximum(in: inner) : 600)) < 1)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == following)
        try fixture.capture("small-window-\(size.rawValue)")
        fixture.window.setContentSize(NSSize(width: 1173, height: 800))
        fixture.scroll.setFrameSize(NSSize(width: 1173, height: 800))
        await fixture.settle()
        #expect(abs(inner.frame.height - originalHeight) < 1)
        #expect(abs(inner.contentView.bounds.minY - (following ? fixture.maximum(in: inner) : 600)) < 1)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == following)
        #expect(!fixture.window.isVisible && !fixture.window.isKeyWindow)
    }

    private func wheel(_ delta: Int32, phase: NSEvent.Phase = [], momentum: Int64 = 0) throws -> NSEvent {
        let event = try #require(CGEvent(scrollWheelEvent2Source: nil,
            units: .pixel, wheelCount: 1, wheel1: delta, wheel2: 0, wheel3: 0))
        let nativePhase: CGScrollPhase = phase == .began ? .began
            : phase == .changed ? .changed : phase == .ended ? .ended : CGScrollPhase(rawValue: 0)!
        event.setIntegerValueField(.scrollWheelEventScrollPhase, value: Int64(nativePhase.rawValue))
        event.setIntegerValueField(.scrollWheelEventMomentumPhase, value: momentum)
        return try #require(NSEvent(cgEvent: event))
    }

    @Test
    func hostedRefreshKeepsTheScrollerExtentStable() async throws {
        _ = NSApplication.shared
        let store = HostedStore()
        let host = NSHostingView(rootView: HostedTimeline(store: store))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1215, height: 600),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { store.close(); window.close() }
        for _ in 0..<8 {
            try await Task.sleep(for: .milliseconds(20))
            host.layoutSubtreeIfNeeded()
        }
        @MainActor func workScroll(in view: NSView) -> FadingWorkScrollView? {
            if let scroll = view as? FadingWorkScrollView { return scroll }
            return view.subviews.lazy.compactMap { workScroll(in: $0) }.first
        }
        let scroll = try #require(workScroll(in: host))
        let document = try #require(scroll.documentView as? NSCollectionView)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: document.frame.height - scroll.contentSize.height))
        scroll.reflectScrolledClipView(scroll.contentView)
        let knob = try #require(scroll.verticalScroller)
        let baseline = document.frame.height
        var extents: [CGFloat] = []
        var proportions: [Double] = []
        document.postsFrameChangedNotifications = true
        let observer = NotificationCenter.default.addObserver(forName: NSView.frameDidChangeNotification,
            object: document, queue: .main) { _ in
                MainActor.assumeIsolated { extents.append(document.frame.height) }
            }
        let observation = knob.observe(\.knobProportion, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { proportions.append(value) } }
        }
        defer { NotificationCenter.default.removeObserver(observer); observation.invalidate() }
        for index in 1...12 {
            store.blocks[0].operation = .init(id: "op", eventId: index, kind: .command, status: .completed)
            for _ in 0..<8 {
                try await Task.sleep(for: .milliseconds(20))
                host.layoutSubtreeIfNeeded()
            }
            #expect(workScroll(in: host) === scroll)
            #expect(extents.allSatisfy { abs($0 - baseline) < 1 })
            #expect(proportions.allSatisfy { abs($0 - scroll.contentSize.height / baseline) < 0.0001 })
            #expect(proportions.isEmpty, "Metadata refreshed a native scroller without changing its geometry")
        }
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test(arguments: [true, false])
    func reflowPublishesOnlyTheCurrentScrollerExtent(following: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.blocks.insert(.init(id: "earlier", kind: .activity, text: "Earlier output",
            activityType: "operation_details"), at: 1)
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        let document = try #require(inner.documentView as? NSCollectionView)
        let flow = try #require(document.collectionViewLayout as? NSCollectionViewFlowLayout)
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner) - (following ? 0 : 600)))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        document.postsFrameChangedNotifications = true
        var frames: [CGFloat] = []
        var proportions: [Double] = []
        var positions: [Double] = []
        let observer = NotificationCenter.default.addObserver(forName: NSView.frameDidChangeNotification,
            object: document, queue: .main) { _ in
                MainActor.assumeIsolated { frames.append(document.frame.height) }
            }
        let knob = try #require(inner.verticalScroller)
        let observation = knob.observe(\.knobProportion, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { proportions.append(value) } }
        }
        let positionObservation = knob.observe(\.doubleValue, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { positions.append(value) } }
        }
        defer {
            NotificationCenter.default.removeObserver(observer)
            observation.invalidate()
            positionObservation.invalidate()
        }
        for count in [100, 2, 80, 1, 100, 1] {
            frames.removeAll()
            proportions.removeAll()
            positions.removeAll()
            fixture.blocks[1].text = Array(repeating: "Earlier output that changes height", count: count)
                .joined(separator: "\n")
            fixture.update()
            let committed = document.frame.height
            let committedPosition = inner.contentView.bounds.minY / fixture.maximum(in: inner)
            await fixture.settle()
            #expect(frames.allSatisfy { abs($0 - committed) < 1 })
            #expect(abs(document.frame.height - flow.collectionViewContentSize.height) < 1)
            #expect(proportions.allSatisfy { abs($0 - inner.contentSize.height / committed) < 0.0001 })
            #expect(positions.allSatisfy { abs($0 - committedPosition) < 0.0001 },
                "A native scroller consumed an intermediate reading position: \(positions)")
            #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == following)
        }
    }

    @Test(arguments: AppTextSize.allCases)
    func unchangedMetadataKeepsDocumentExtentAndScrollerGeometryConsistent(size: AppTextSize) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.textSize = size
        fixture.blocks[1].text = (0..<18).map { "test_result_\($0) (tests.ContractTests.test_result_\($0)) ... ok" }.joined(separator: "\n")
        fixture.blocks.insert(contentsOf: (0..<50).map {
            TranscriptBlock(id: "earlier-\($0)", kind: .command, text: "Earlier completed operation \($0)",
                activityType: "work_operation")
        }, at: 0)
        fixture.blocks[0].operation = .init(id: "op", eventId: 0, kind: .command, status: .completed)
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        let document = try #require(inner.documentView as? NSCollectionView)
        let flow = try #require(document.collectionViewLayout as? NSCollectionViewFlowLayout)
        let source = try #require(document.dataSource as? ConversationTimelineView.Coordinator)
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        let baselineCommits = source.geometryCommitCount
        document.postsFrameChangedNotifications = true
        var extents: [CGFloat] = []
        let observer = NotificationCenter.default.addObserver(forName: NSView.frameDidChangeNotification,
            object: document, queue: .main) { _ in
                MainActor.assumeIsolated { extents.append(document.frame.height) }
            }
        defer { NotificationCenter.default.removeObserver(observer) }
        let baseline = document.frame.height
        let knob = try #require(inner.verticalScroller)
        var proportions: [Double] = []
        let observation = knob.observe(\.knobProportion, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { proportions.append(value) } }
        }
        defer { observation.invalidate() }
        for index in 1...12 {
            extents.removeAll()
            proportions.removeAll()
            fixture.blocks[0].operation = .init(id: "op", eventId: index, kind: .command, status: .completed)
            fixture.update()
            await fixture.settle()
            let last = try #require(flow.layoutAttributesForItem(at:
                IndexPath(item: fixture.blocks.count - 1, section: 0)))
            let measuredEnd = last.frame.maxY + flow.sectionInset.bottom
            #expect(extents.allSatisfy { abs($0 - baseline) < 1 }, "Unchanged rows resized the native document: \(extents)")
            #expect(abs(document.frame.height - measuredEnd) < 1)
            #expect(source.geometryCommitCount == baselineCommits)
            guard case .message(let refreshed) = source.parent.items[0] else {
                Issue.record("Metadata refresh must still reach the displayed item")
                continue
            }
            #expect(refreshed.operation?.eventId == index)
            #expect(proportions.isEmpty, "Unchanged row metrics republished native scroller state")
        }
    }

    @Test
    func metadataCannotSupersedeAnUnfinishedGeometryUpdate() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        let document = try #require(inner.documentView as? NSCollectionView)
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        for index in 1...20 {
            fixture.blocks[1].text += "\nOutput \(index) before the metadata-only update"
            fixture.update()
            fixture.blocks[0].operation = .init(id: "op", eventId: index, kind: .command, status: .completed)
            fixture.update()
        }
        await fixture.settle()
        let source = try #require(document.dataSource as? ConversationTimelineView.Coordinator)
        #expect(source.parent.items.count == fixture.blocks.count)
        guard case .message(let detail) = source.parent.items[1] else {
            Issue.record("The final geometry update must not be discarded")
            return
        }
        #expect(detail.text == fixture.blocks[1].text)
        #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == true)
    }

    @Test
    func equalTotalHeightStillCommitsChangedRowGeometry() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let short = Array(repeating: "Measured output", count: 2).joined(separator: "\n")
        let long = Array(repeating: "Measured output", count: 60).joined(separator: "\n")
        fixture.blocks[0] = .init(id: "command", kind: .activity, text: short, activityType: "operation_details")
        fixture.blocks[1].text = long
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        let document = try #require(inner.documentView as? NSCollectionView)
        let source = try #require(document.dataSource as? ConversationTimelineView.Coordinator)
        let flow = try #require(document.collectionViewLayout as? NSCollectionViewFlowLayout)
        let height = document.frame.height
        let before = try #require(flow.layoutAttributesForItem(at: IndexPath(item: 1, section: 0)))
            .frame.minY
        let commits = source.geometryCommitCount
        fixture.blocks[0].text = long
        fixture.blocks[1].text = short
        fixture.update()
        await fixture.settle()
        let after = try #require(flow.layoutAttributesForItem(at: IndexPath(item: 1, section: 0)))
            .frame.minY
        #expect(abs(document.frame.height - height) < 1)
        #expect(after > before + 500)
        #expect(source.geometryCommitCount == commits + 1)
    }

    @Test(arguments: AppTextSize.allCases)
    func operationMetadataRefreshesAtTheEndDoNotChangeContentOrPosition(size: AppTextSize) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.textSize = size
        fixture.blocks[0].operation = .init(id: "op", eventId: 0, kind: .command, status: .completed)
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        let before = inner.contentView.bounds.minY
        for index in 1...20 {
            fixture.blocks[0].operation = .init(id: "op", eventId: index, kind: .command, status: .completed)
            fixture.update()
            await fixture.settle()
            #expect(abs(inner.contentView.bounds.minY - before) < 1)
        }
    }

    @Test(arguments: [false, true])
    func passiveUpdatesNeverMoveAwayFromTheFollowedEnd(appending: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        let document = try #require(inner.documentView as? NSCollectionView)
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        var previousY = inner.contentView.bounds.minY
        let observer = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
            object: inner.contentView, queue: .main) { [weak fixture] _ in
                MainActor.assumeIsolated {
                    if let fixture, let inner = try? fixture.workScroll() {
                        fixture.offsets.append(inner.contentView.bounds.minY)
                    }
                }
            }
        defer { NotificationCenter.default.removeObserver(observer) }
        for index in 0..<12 {
            fixture.offsets.removeAll()
            if appending {
                fixture.blocks.append(.init(id: "extra-\(index)", kind: .activity,
                    text: "New operation \(index)", activityType: "file_change"))
            } else {
                fixture.blocks[1].text += "\nMore output \(index)"
            }
            fixture.update()
            document.layoutSubtreeIfNeeded()
            await fixture.settle()
            let minimum = fixture.offsets.min() ?? inner.contentView.bounds.minY
            #expect(minimum >= previousY - 1,
                "Passive follow rewound from \(previousY) to \(minimum): \(fixture.offsets)")
            #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1)
            previousY = inner.contentView.bounds.minY
            #expect(try fixture.workScroll() === inner)
        }
        #expect(!fixture.window.isVisible)
    }

    @Test(arguments: [true, false])
    func earlierOutputReflowCommitsTheViewportInOnePaint(following: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.blocks.insert(.init(id: "earlier", kind: .activity, text: "Earlier output",
            activityType: "operation_details"), at: 1)
        fixture.update()
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner) - (following ? 0 : 600)))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        let before = try fixture.viewportBitmap(inner)
        let outerBounds = fixture.scroll.contentView.bounds
        for count in [100, 2, 80, 1, 100, 1] {
            fixture.blocks[1].text = Array(repeating: "Earlier output that changes height", count: count)
                .joined(separator: "\n")
            fixture.update()
            if following {
                #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1,
                    "Geometry painted before follow: \(inner.contentView.bounds.minY) vs \(fixture.maximum(in: inner))")
            }
            #expect(try fixture.viewportBitmap(inner) == before)
            #expect(fixture.scroll.contentView.bounds == outerBounds)
            await fixture.settle()
            #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == following)
            if following { #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1) }
        }
        fixture.blocks[2].text += "\nA new visible tail marker"
        fixture.update()
        await fixture.settle()
        if following { #expect(try fixture.viewportBitmap(inner) != before) }
    }

    @Test
    func aShortenedDocumentCannotChangeReadingIntentIntoFollowing() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: 600))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        let original = fixture.blocks[1].text
        fixture.blocks[1].text = "Short output"
        fixture.update()
        await fixture.settle()
        #expect(fixture.maximum(in: inner) == 0)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == false)
        fixture.blocks[1].text = original
        fixture.update()
        await fixture.settle()
        #expect(inner.contentView.bounds.minY < fixture.maximum(in: inner) - 200)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == false)
    }

    @Test
    func rapidAppendsCommitTheCurrentEndWithoutWaitingForFutureCallbacks() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        for index in 0..<30 {
            fixture.blocks.append(.init(id: "append-\(index)", kind: .activity,
                text: "Output \(index)", activityType: "file_change"))
            fixture.update()
            #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1)
        }
        await fixture.settle()
        #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1)
        let source = try #require((inner.documentView as? NSCollectionView)?.dataSource
            as? ConversationTimelineView.Coordinator)
        #expect(source.parent.items.count == fixture.blocks.count)
    }

    @Test
    func aWheelGestureAtTheEndStillFollowsNewOutputAfterItFinishes() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        let cg = try #require(CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
            wheelCount: 1, wheel1: -25, wheel2: 0, wheel3: 0))
        inner.scrollWheel(with: try #require(NSEvent(cgEvent: cg)))
        fixture.blocks[1].text += "\nOutput arriving during the gesture"
        fixture.update()
        try await Task.sleep(for: .milliseconds(240))
        await fixture.settle()
        #expect(abs(inner.contentView.bounds.minY - fixture.maximum(in: inner)) < 1)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == true)
    }

    @Test
    func navigationSupersedesPendingFollowAndPassiveRefreshes() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        inner.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximum(in: inner)))
        inner.reflectScrolledClipView(inner.contentView)
        fixture.blocks[1].text += "\nA new output page"
        fixture.update()
        inner.onUserScroll?()
        inner.contentView.scroll(to: NSPoint(x: 0, y: 400))
        inner.reflectScrolledClipView(inner.contentView)
        for index in 0..<20 {
            fixture.blocks[1].text += "\nUpdate \(index) while reading"
            fixture.update()
        }
        try await Task.sleep(for: .milliseconds(240))
        await fixture.settle()
        #expect(abs(inner.contentView.bounds.minY - 400) < 1)
        #expect(fixture.memory.snapshot(for: "fixture/work")?.followsLatest == false)
        #expect(try fixture.workScroll() === inner)
    }

    @Test
    func unchangedPollsAndRelayoutKeepTheSameTallOutputReadingPosition() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let inner = try fixture.workScroll()
        let reading = max(0, fixture.maximum(in: inner) - 280)
        inner.onUserScroll?()
        inner.contentView.scroll(to: NSPoint(x: 0, y: reading))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        for _ in 0..<40 {
            fixture.update()
            inner.documentView?.needsLayout = true
            inner.documentView?.layoutSubtreeIfNeeded()
            await fixture.settle()
            #expect(abs(inner.contentView.bounds.minY - reading) < 1)
        }
        #expect(!fixture.window.isVisible)
    }

    @MainActor
    private final class HostedStore: ObservableObject {
        let suite = "hosted-scroll-\(UUID().uuidString)"
        let defaults: UserDefaults
        let memory: TranscriptScrollMemory
        @Published var blocks: [TranscriptBlock] = (0..<120).map {
            .init(id: "command-\($0)", kind: .command, text: "Earlier operation \($0)", activityType: "work_operation")
        } + [
            .init(id: "test", kind: .command, text: "Completed tests", activityType: "work_operation_expanded"),
            .init(id: "details", kind: .activity,
                text: (0..<18).map { "test_result_\($0) (tests.ContractTests.test_result_\($0)) ... ok" }.joined(separator: "\n"),
                activityType: "operation_details"),
            .init(id: "files", kind: .activity, text: "Listed files", activityType: "work_operation"),
        ]

        init() {
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
        }

        func close() {
            memory.freezeForTermination()
            defaults.removePersistentDomain(forName: suite)
        }
    }

    @MainActor
    private struct HostedTimeline: View {
        @ObservedObject var store: HostedStore
        @State private var nearBottom = true
        @State private var inlinePosition = TranscriptInlineControlPosition.missing

        var body: some View {
            VStack(spacing: 0) {
                ConversationTimelineView(language: .english, textSize: .standard,
                    accentColor: .orange, appearance: .dark, contextID: "hosted", items: [
                        .message(.init(id: "reply", kind: .output,
                            text: Array(repeating: "An earlier reply in this conversation.", count: 30).joined(separator: "\n"))),
                        .message(.init(id: "summary", kind: .activity, text: "Work process",
                            activityType: "work_summary_expanded")),
                        .workProcess(.init(id: "work", blocks: store.blocks)),
                    ], contentIsLoading: false, jumpToLatestRequest: 0, findQuery: "", findMatch: nil,
                    findNavigationRequest: 0, memory: store.memory, resultsInspectorVisible: false,
                    openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                    isNearBottom: $nearBottom, inlineAgentSwitchPosition: $inlinePosition)
            }
        }
    }

    @MainActor
    private final class Fixture {
        let suite = "WorkViewportStabilityTests.\(UUID().uuidString)"
        let defaults: UserDefaults
        let memory: TranscriptScrollMemory
        let window: NSWindow
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 1173, height: 800))
        var coordinator: ConversationTimelineView.Coordinator!
        var offsets: [CGFloat] = []
        var textSize = AppTextSize.standard
        var contextID = "fixture"
        var leadingText = "A reply above the work process."
        var trailingTexts: [String] = []
        var blocks = [
            TranscriptBlock(id: "command", kind: .command, text: "Read operation output"),
            TranscriptBlock(id: "detail", kind: .activity,
                text: (0..<300).map { "line \($0): value = compute(input)" }.joined(separator: "\n"),
                activityType: "operation_details"),
            TranscriptBlock(id: "files", kind: .activity, text: "Changed 4 files", activityType: "file_change"),
        ]

        init(textSize: AppTextSize = .standard, height: CGFloat = 800,
             leadingText: String = "A reply above the work process.", trailingTexts: [String] = []) {
            _ = NSApplication.shared
            self.textSize = textSize
            self.leadingText = leadingText
            self.trailingTexts = trailingTexts
            scroll.setFrameSize(NSSize(width: 1173, height: height))
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
            window = NSWindow(contentRect: scroll.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = scroll
            memory.save(.init(offsetY: 0, followsLatest: false), for: "fixture")
            coordinator = ConversationTimelineView.Coordinator(parent: parent())
            let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            collection.frame = scroll.bounds
            scroll.documentView = collection
            coordinator.attach(scrollView: scroll, collectionView: collection)
            update()
        }

        func parent() -> ConversationTimelineView {
            var items: [ConversationTimelineItem] = [
                    .message(.init(id: "reply", kind: .output, text: leadingText)),
                    .message(.init(id: "summary", kind: .activity, text: "Work process",
                        activityType: "work_summary_expanded")),
                    .workProcess(.init(id: "work", blocks: blocks)),
                ]
            items += trailingTexts.enumerated().map { index, text in
                .message(.init(id: "later-\(index)", kind: .output, text: text))
            }
            return ConversationTimelineView(language: .english, textSize: textSize,
                accentColor: .orange, appearance: .dark, contextID: contextID, items: items,
                contentIsLoading: false, jumpToLatestRequest: 0, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
        }

        func update() { coordinator.update(parent: parent()) }

        func workScroll(stage: String = "existing-work") throws -> FadingWorkScrollView {
            let collection = try #require(scroll.documentView as? NSCollectionView)
            let item = try #require(collection.item(at: IndexPath(item: 2, section: 0)) as? WorkProcessCollectionItem,
                "Missing work at \(stage), viewport \(scroll.contentView.documentVisibleRect), visible \(collection.indexPathsForVisibleItems())")
            return try #require(item.view as? FadingWorkScrollView)
        }

        func maximum(in scroll: NSScrollView) -> CGFloat {
            max(0, (scroll.documentView?.frame.height ?? 0) - scroll.contentSize.height)
        }

        func viewportBitmap(_ scroll: NSScrollView) throws -> Data {
            let clip = scroll.contentView
            let bitmap = try #require(clip.bitmapImageRepForCachingDisplay(in: clip.bounds))
            clip.cacheDisplay(in: clip.bounds, to: bitmap)
            let data = try #require(bitmap.bitmapData)
            let pixels = UnsafeBufferPointer(start: data, count: bitmap.bytesPerRow * bitmap.pixelsHigh)
            #expect(Set(pixels).count > 10, "The native viewport must contain painted text, not a blank surface")
            #expect(!window.isVisible && !window.isKeyWindow)
            return try #require(bitmap.representation(using: .png, properties: [:]))
        }

        func capture(_ name: String) throws {
            let bitmap = try viewportBitmap(scroll)
            if let directory = ProcessInfo.processInfo.environment["PCR_WORK_SCROLL_QA_OUTPUT"] {
                try bitmap.write(to: URL(fileURLWithPath: directory).appendingPathComponent(name + ".png"), options: .atomic)
            }
        }

        func settle() async {
            for _ in 0..<4 {
                try? await Task.sleep(for: .milliseconds(15))
                window.contentView?.layoutSubtreeIfNeeded()
            }
        }

        func close() {
            coordinator.detach()
            memory.freezeForTermination()
            window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }
}
