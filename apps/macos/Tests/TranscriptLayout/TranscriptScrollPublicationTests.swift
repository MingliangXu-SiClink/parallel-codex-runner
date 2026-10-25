import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct TranscriptScrollPublicationTests {
    @Test
    func trackingAndMomentumKeepOneCoherentSnapshotThenCommitOnlyTheLatest() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.scroll(to: 100)
        await fixture.settle()
        let original = fixture.items
        fixture.itemsRevision = 1
        fixture.coordinator.update(parent: fixture.parent())
        let offset = fixture.scrollView.contentView.bounds.minY
        let commits = fixture.coordinator.geometryCommitCount
        fixture.coordinator.observeScrollEvent(try scrollEvent(phase: .began))
        for index in 1...200 {
            fixture.itemsRevision = UInt64(index + 1)
            fixture.items = original + [.message(TranscriptBlock(id: "latest", kind: .output,
                text: "Newest snapshot \(index)"))]
            fixture.coordinator.update(parent: fixture.parent())
        }
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count)
        #expect(fixture.coordinator.geometryCommitCount == commits)
        #expect(fixture.coordinator.deferredScrollUpdateCount == 200)
        fixture.coordinator.observeScrollEvent(try scrollEvent(phase: .ended))
        fixture.coordinator.observeScrollEvent(try scrollEvent(momentum: 1))
        try await Task.sleep(for: .milliseconds(230))
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count)
        fixture.coordinator.observeScrollEvent(try scrollEvent(momentum: 3))
        try await Task.sleep(for: .milliseconds(230))
        await fixture.settle()
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count + 1)
        #expect(fixture.coordinator.geometryCommitCount == commits + 1)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - offset) < 2)
        try fixture.expectVisibleFramesMatchLayout()
    }

    @Test(arguments: [false, true])
    func explicitNavigationAndRevertedRefreshCannotReplayStaleDeferredContent(changeContext: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let original = fixture.items
        fixture.coordinator.observeScrollEvent(try scrollEvent(phase: .began))
        fixture.items.append(.message(TranscriptBlock(id: "discarded", kind: .output, text: "Old refresh")))
        fixture.coordinator.update(parent: fixture.parent())
        fixture.items = original
        if changeContext { fixture.context = "next" }
        else { fixture.jumpRequest += 1 }
        fixture.coordinator.update(parent: fixture.parent())
        try await Task.sleep(for: .milliseconds(250))
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - fixture.maximumY) < 2)
    }

    @Test
    func phaseLessWheelsAndTeardownReleaseDeferredUpdates() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        let original = fixture.items
        fixture.coordinator.observeScrollEvent(try scrollEvent())
        fixture.items.append(.message(TranscriptBlock(id: "new", kind: .output, text: "Latest")))
        fixture.coordinator.update(parent: fixture.parent())
        try await Task.sleep(for: .milliseconds(230))
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count + 1)
        fixture.coordinator.observeScrollEvent(try scrollEvent(phase: .began))
        fixture.items.append(.message(TranscriptBlock(id: "detached", kind: .output, text: "Never mount")))
        fixture.coordinator.update(parent: fixture.parent())
        fixture.coordinator.detach()
        try await Task.sleep(for: .milliseconds(230))
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == original.count + 1)
    }

    private func scrollEvent(phase: CGScrollPhase = CGScrollPhase(rawValue: 0)!,
                             momentum: Int64 = 0) throws -> NSEvent {
        let event = try #require(CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
            wheelCount: 1, wheel1: 0, wheel2: 0, wheel3: 0))
        event.setIntegerValueField(.scrollWheelEventScrollPhase, value: Int64(phase.rawValue))
        event.setIntegerValueField(.scrollWheelEventMomentumPhase, value: momentum)
        return try #require(NSEvent(cgEvent: event))
    }

    @Test func unchangedPollingSkipsSnapshotPreparationButExplicitNavigationStillWorks() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.itemsRevision = 1
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        fixture.scroll(to: 100)
        await fixture.settle()
        let before = fixture.coordinator.unchangedUpdateCount
        let offset = fixture.scrollView.contentView.bounds.minY
        for _ in 0..<200 { fixture.coordinator.update(parent: fixture.parent()) }
        #expect(fixture.coordinator.unchangedUpdateCount == before + 200)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - offset) < 1)
        fixture.jumpRequest = 1
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - fixture.maximumY) < 2)
        fixture.itemsRevision = 2
        fixture.items.append(.message(TranscriptBlock(id: "new", kind: .output, text: "A real update")))
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(fixture.collectionView.numberOfItems(inSection: 0) == fixture.items.count)
    }

    @Test
    func streamingFollowsCommittedGeometryUntilTheReaderScrollsAway() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let initial = String(repeating: "A streamed paragraph.\n\n", count: 35)
        fixture.items = [.message(TranscriptBlock(id: "live", kind: .output, text: initial))]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let before = fixture.scrollView.contentView.bounds.minY
        fixture.items = [.message(TranscriptBlock(id: "live", kind: .output,
            text: initial + String(repeating: "The answer continues.\n\n", count: 8)))]
        fixture.coordinator.update(parent: fixture.parent())
        // Streaming geometry is committed atomically; animated explicit jumps
        // are exercised separately by ScrollMotionOwnershipTests.
        await fixture.settle()
        let latest = fixture.scrollView.contentView.bounds.minY
        #expect(latest > before + 1)
        #expect(abs(latest - fixture.maximumY) < 2)
        fixture.scrollView.onUserScroll?()
        fixture.scroll(to: latest - 150)
        let reading = fixture.scrollView.contentView.bounds.minY
        try await Task.sleep(for: .milliseconds(250))
        #expect(abs(fixture.scrollView.contentView.bounds.minY - reading) < 1)
        fixture.items = [.message(TranscriptBlock(id: "live", kind: .output,
            text: initial + String(repeating: "The answer continues.\n\n", count: 20)))]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - reading) < 2)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
    }

    @Test
    func streamingUpdatesTheExistingTextViewAndKeepsSelection() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = [.message(TranscriptBlock(id: "stream", kind: .output, text: "Already readable text."))]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let index = IndexPath(item: 0, section: 0)
        let item = try #require(fixture.collectionView.item(at: index))
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
        text.setSelectedRange(NSRange(location: 2, length: 5))
        for count in 1...12 {
            fixture.items = [.message(TranscriptBlock(id: "stream", kind: .output,
                text: "Already readable text." + String(repeating: " More content.", count: count)))]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            #expect(fixture.collectionView.item(at: index) === item)
            #expect(descendants(item.view).contains { $0 === text })
            #expect(text.string.hasSuffix(" More content."))
            #expect(text.selectedRange() == NSRange(location: 2, length: 5))
            #expect(!item.view.isHidden)
            try fixture.expectVisibleFramesMatchLayout()
        }
    }

    @Test
    func liveResizeReflowsVisibleTextBeforeTheGestureEnds() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = [.message(TranscriptBlock(id: "long", kind: .output,
            text: String(repeating: "A long answer keeps its text visible while resizing. ", count: 80)))]
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)))
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
        let before = try #require(text.textContainer?.containerSize.width)
        fixture.coordinator.beginLiveResize()
        fixture.scrollView.setFrameSize(NSSize(width: 420, height: 480))
        fixture.collectionView.setFrameSize(NSSize(width: 420, height: fixture.collectionView.frame.height))
        fixture.coordinator.collectionWidthDidChange(duringLiveResize: true)
        await fixture.settle()
        #expect((text.textContainer?.containerSize.width ?? before) < before - 100)
        #expect(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)) === item)
        #expect(!text.isHidden)
        fixture.coordinator.endLiveResize()
        await fixture.settle()
        #expect(fixture.scrollView.contentView.bounds.minY < 2)
    }

    @Test
    func shrinkingTheViewportNeverUsesThePreviousDocumentWidth() async {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.scrollView.setFrameSize(NSSize(width: 360, height: 480))
        fixture.collectionView.setFrameSize(NSSize(width: 640, height: 2000))
        let layout = fixture.collectionView.collectionViewLayout as! NSCollectionViewFlowLayout
        let size = fixture.coordinator.collectionView(fixture.collectionView, layout: layout,
            sizeForItemAt: IndexPath(item: 0, section: 0))
        #expect(size.width < fixture.scrollView.contentSize.width - layout.sectionInset.left - layout.sectionInset.right)
        let context = layout.invalidationContext(forBoundsChange: NSRect(x: 0, y: 0, width: 360, height: 480))
            as? NSCollectionViewFlowLayoutInvalidationContext
        #expect(context?.invalidateFlowLayoutDelegateMetrics == true)
        await fixture.settle()
        for index in fixture.collectionView.indexPathsForVisibleItems() {
            let frame = layout.layoutAttributesForItem(at: index)?.frame ?? .zero
            #expect(frame.maxX <= fixture.scrollView.contentSize.width + 1)
        }
    }

    @Test
    func boundedWorkScrollKeepsTheAnswerAndReadingPositionStable() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let work = (0..<120).map { TranscriptBlock(id: "step-\($0)", kind: .thought, text: "Inspection step \($0)") }
        func rows(_ values: [TranscriptBlock]) -> [ConversationTimelineItem] {
            ConversationTimelineProjection.make(blocks: TranscriptFormatter.blocksWithWorkDisclosure(
                values + [TranscriptBlock(id: "final", kind: .output, text: "The answer.", isFinalAnswer: true)],
                runID: "work-scroll-test", agent: 1, expanded: true), switchState: nil)
        }
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        fixture.items = rows(work)
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        await fixture.settle()
        let index = IndexPath(item: 1, section: 0)
        let item = try #require(fixture.collectionView.item(at: index) as? WorkProcessCollectionItem)
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let inner = try #require(descendants(item.view).compactMap { $0 as? FadingWorkScrollView }.first)
        #expect(item.view.frame.height <= WorkProcessCollectionItem.maximumHeight(textSize: .standard))
        #expect((inner.documentView?.frame.height ?? 0) > inner.contentSize.height * 4)
        #expect(inner.contentView.bounds.minY < 1)
        #expect(!inner.fadesTop)
        #expect(inner.fadesBottom)
        let outerOffset = fixture.scrollView.contentView.bounds.minY
        inner.contentView.scroll(to: NSPoint(x: 0, y: 140))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - outerOffset) < 1)
        #expect(abs(inner.contentView.bounds.minY - 140) < 2)
        #expect(inner.fadesTop && inner.fadesBottom)
        fixture.items = rows(work + [TranscriptBlock(id: "later", kind: .thought, text: "A streamed update")])
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(fixture.collectionView.item(at: index) === item)
        #expect(abs(inner.contentView.bounds.minY - 140) < 2)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - outerOffset) < 1)
        let layout = try #require(fixture.collectionView.collectionViewLayout)
        let answer = try #require(layout.layoutAttributesForItem(at: IndexPath(item: 2, section: 0)))
        #expect(answer.frame.minY < 480)
        #expect(inner.layer?.mask is CAGradientLayer)
    }

    @Test(arguments: [CGFloat(320), 760, 1400, 2400], [TranscriptAppearance.light, .dark])
    func expandedWorkIndentsItsChildrenWithoutMovingReplies(width: CGFloat, appearance: TranscriptAppearance) async throws {
        for textSize in AppTextSize.allCases {
            let fixture = Fixture()
            defer { fixture.close() }
            fixture.textSize = textSize
            fixture.appearance = appearance
            fixture.scrollView.setFrameSize(NSSize(width: width, height: 480))
            fixture.collectionView.setFrameSize(NSSize(width: width, height: 480))
            let operation = WorkOperationRecord(id: "read", eventId: 1, kind: .fileRead, status: .completed,
                targets: [WorkOperationTarget(path: "src/example.swift")], detailsAvailable: true)
            fixture.items = ConversationTimelineProjection.make(blocks: [
                TranscriptBlock(id: "summary", kind: .activity, text: "Read files", activityType: "work_summary_expanded"),
                TranscriptBlock(id: "read", kind: .command, text: "Read example.swift", operation: operation),
                TranscriptBlock(id: "progress", kind: .output, text: "The intermediate reply stays outside the work group.",
                    activityType: "assistant_progress"),
            ], switchState: nil)
            fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            await fixture.settle()
            let header = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)))
            let work = try #require(fixture.collectionView.item(at: IndexPath(item: 1, section: 0)) as? WorkProcessCollectionItem)
            let reply = try #require(fixture.collectionView.item(at: IndexPath(item: 2, section: 0)))
            let inner = try #require(work.view as? NSScrollView)
            let collection = try #require(inner.documentView as? NSCollectionView)
            let row = try #require(collection.item(at: IndexPath(item: 0, section: 0)))
            let headerIcon = try #require(header.view.subviews.compactMap { $0 as? NSImageView }.first)
            let childIcon = try #require(row.view.subviews.compactMap { $0 as? NSImageView }.first)
            let headerX = headerIcon.convert(headerIcon.bounds, to: fixture.collectionView).minX
            let childX = childIcon.convert(childIcon.bounds, to: fixture.collectionView).minX
            let layout = try #require(collection.collectionViewLayout as? NSCollectionViewFlowLayout)
            #expect(layout.sectionInset.left == ParallelCodexVisualStyle.Layout.workProcessIndent)
            // Flow layout centers the spare four-point sizing tolerance.
            #expect(childX - headerX >= ParallelCodexVisualStyle.Layout.workProcessIndent)
            #expect(childX - headerX <= ParallelCodexVisualStyle.Layout.workProcessIndent + 4)
            #expect(abs(reply.view.frame.minX - header.view.frame.minX) < 1)
            #expect(row.view.frame.maxX <= inner.contentSize.width + 1)
            #expect(row.view.subviews.compactMap { $0 as? NSImageView }.filter { !$0.isHidden }
                .allSatisfy { $0.frame.maxX <= row.view.bounds.width })
        }
    }

    @Test(arguments: [CGFloat(320), 760, 1400])
    func wrappedWorkHeightUsesTheIndentedReadingWidth(width: CGFloat) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.scrollView.setFrameSize(NSSize(width: width, height: 480))
        fixture.collectionView.setFrameSize(NSSize(width: width, height: 480))
        let thought = TranscriptBlock(id: "wrapped", kind: .thought,
            text: "Inspect the files, check the command output and compare the edited document before continuing.")
        fixture.items = ConversationTimelineProjection.make(blocks: [
            TranscriptBlock(id: "summary", kind: .activity, text: "Inspecting files", activityType: "work_summary_expanded"),
            thought,
            TranscriptBlock(id: "reply", kind: .output, text: "An independent reply.", activityType: "assistant_progress"),
        ], switchState: nil)
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        await fixture.settle()
        let work = try #require(fixture.collectionView.item(at: IndexPath(item: 1, section: 0)) as? WorkProcessCollectionItem)
        let scroll = try #require(work.view as? NSScrollView)
        let collection = try #require(scroll.documentView as? NSCollectionView)
        let row = try #require(collection.item(at: IndexPath(item: 0, section: 0)))
        let expected = TranscriptCollectionItem.preferredHeight(for: thought, language: .english,
            textSize: .standard, accentColor: .orange, appearance: .dark, availableWidth: row.view.frame.width) + 16
        #expect(abs(work.view.frame.height - expected) < 1)
        #expect((scroll.documentView?.frame.height ?? 0) <= scroll.contentSize.height + 1)
    }

    @Test(arguments: [TranscriptAppearance.light, .dark])
    func workScrollEdgesContinueThroughTheMountedConversation(appearance: TranscriptAppearance) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.appearance = appearance
        let work = (0..<120).map { TranscriptBlock(id: "nested-step-\($0)", kind: .thought,
            text: "A work-process step \($0).") }
        let answer = TranscriptBlock(id: "nested-answer", kind: .output,
            text: String(repeating: "The conversation continues below the work process.\n\n", count: 35))
        fixture.items = [.workProcess(ConversationWorkProcess(id: "nested-work", blocks: work)), .message(answer)]
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        fixture.coordinator.update(parent: fixture.parent())
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        await fixture.settle()
        let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0))
            as? WorkProcessCollectionItem)
        let inner = try #require(item.view as? FadingWorkScrollView)
        let maximum = try #require(inner.documentView?.bounds.height) - inner.contentSize.height
        inner.contentView.scroll(to: NSPoint(x: 0, y: maximum))
        inner.reflectScrolledClipView(inner.contentView)
        func wheel(_ delta: Int32) throws -> NSEvent {
            let event = try #require(CGEvent(scrollWheelEvent2Source: nil,
                units: .pixel, wheelCount: 1, wheel1: delta, wheel2: 0, wheel3: 0))
            return try #require(NSEvent(cgEvent: event))
        }
        let outerBefore = fixture.scrollView.contentView.bounds.minY
        inner.scrollWheel(with: try wheel(-40))
        await fixture.settle()
        let outerAfter = fixture.scrollView.contentView.bounds.minY
        #expect(outerAfter > outerBefore + 30)
        #expect(abs(inner.contentView.bounds.minY - maximum) < 1)
        #expect(!inner.fadesBottom)
        inner.scrollWheel(with: try wheel(20))
        await fixture.settle()
        #expect(abs(inner.contentView.bounds.minY - (maximum - 20)) < 1)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - outerAfter) < 1)
        inner.contentView.scroll(to: .zero)
        inner.reflectScrolledClipView(inner.contentView)
        inner.scrollWheel(with: try wheel(20))
        await fixture.settle()
        let reading = fixture.scrollView.contentView.bounds.minY
        #expect(reading < outerAfter - 10)
        #expect(!inner.fadesTop)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
        fixture.items = [.workProcess(ConversationWorkProcess(id: "nested-work", blocks: work + [
            TranscriptBlock(id: "nested-later", kind: .thought, text: "Another streamed step")
        ])), .message(answer)]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)) === item)
        #expect(abs(fixture.scrollView.contentView.bounds.minY - reading) < 1)
        try fixture.expectVisibleFramesMatchLayout()
    }

    @Test
    func mixedHeightWorkLogKeepsItsDocumentAndScrollerStableWhileReading() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let blocks = (0..<160).map { index in
            TranscriptBlock(id: "mixed-\(index)", kind: index % 3 == 0 ? .output : .thought,
                text: "Step \(index). " + String(repeating: "A longer operation with wrapped text. ",
                    count: index % 11 + 1), activityType: index % 3 == 0 ? "assistant_progress" : nil)
        }
        fixture.items = [.workProcess(ConversationWorkProcess(id: "mixed-work", blocks: blocks))]
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        fixture.coordinator.update(parent: fixture.parent())
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        await fixture.settle()
        let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0))
            as? WorkProcessCollectionItem)
        let inner = try #require(item.view as? FadingWorkScrollView)
        let document = try #require(inner.documentView as? NSCollectionView)
        let height = document.frame.height
        let maximum = max(0, height - inner.contentSize.height)
        var heightRange = height...height
        var maximumDrift: CGFloat = 0
        for step in 0..<24 {
            let target = maximum * CGFloat(step % 8) / 8
            inner.contentView.scroll(to: NSPoint(x: 0, y: target))
            inner.reflectScrolledClipView(inner.contentView)
            for _ in 0..<5 {
                try await Task.sleep(for: .milliseconds(16))
                document.layoutSubtreeIfNeeded()
                heightRange = min(heightRange.lowerBound, document.frame.height)...max(heightRange.upperBound, document.frame.height)
                maximumDrift = max(maximumDrift, abs(inner.contentView.bounds.minY - target))
            }
        }
        print("Mixed work scroll: document \(heightRange), reading drift \(maximumDrift)")
        #expect(heightRange.upperBound - heightRange.lowerBound < 1)
        #expect(maximumDrift < 1)
    }

    @Test
    func streamingWorkRowSizesAgreeWithTheDocumentThroughoutUpdates() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        var blocks = (0..<80).map { index in
            TranscriptBlock(id: "live-work-\(index)", kind: .thought,
                text: "Step \(index). " + String(repeating: "Wrapped operation text. ", count: index % 9 + 1))
        }
        func update() {
            fixture.items = [.workProcess(ConversationWorkProcess(id: "streaming-work", blocks: blocks))]
            fixture.coordinator.update(parent: fixture.parent())
        }
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        update()
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        await fixture.settle()
        let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0))
            as? WorkProcessCollectionItem)
        let inner = try #require(item.view as? FadingWorkScrollView)
        let document = try #require(inner.documentView as? NSCollectionView)
        let flow = try #require(document.collectionViewLayout as? NSCollectionViewFlowLayout)
        inner.contentView.scroll(to: NSPoint(x: 0, y: 200))
        inner.reflectScrolledClipView(inner.contentView)
        var worstGeometryMismatch: CGFloat = 0
        var worstDrift: CGFloat = 0
        for _ in 1...15 {
            blocks[79].text += String(repeating: " More streamed text.", count: 20)
            update()
            for _ in 0..<8 {
                try await Task.sleep(for: .milliseconds(16))
                document.layoutSubtreeIfNeeded()
                worstGeometryMismatch = max(worstGeometryMismatch,
                    abs(document.frame.height - max(inner.contentSize.height, flow.collectionViewContentSize.height)))
                worstDrift = max(worstDrift, abs(inner.contentView.bounds.minY - 200))
            }
            #expect(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)) === item)
        }
        print("Streaming work scroll: geometry mismatch \(worstGeometryMismatch), reading drift \(worstDrift)")
        #expect(worstGeometryMismatch < 1)
        #expect(worstDrift < 1)
    }

    @Test(arguments: [false, true])
    func updatingEarlierRowsKeepsTheReadingAnchorInTheSamePaint(isWorkProcess: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        var blocks = (0..<80).map { index in
            TranscriptBlock(id: "reflow-work-\(index)", kind: .output,
                text: "Operation \(index). A readable progress paragraph.", activityType: "assistant_progress")
        }
        func update() {
            fixture.items = isWorkProcess
                ? [.workProcess(ConversationWorkProcess(id: "reflow-work", blocks: blocks))]
                : blocks.map(ConversationTimelineItem.message)
            fixture.coordinator.update(parent: fixture.parent())
        }
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        update()
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        await fixture.settle()
        let scroll: NSScrollView
        if isWorkProcess {
            let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0))
                as? WorkProcessCollectionItem)
            scroll = try #require(item.view as? FadingWorkScrollView)
        } else { scroll = fixture.scrollView }
        let document = try #require(scroll.documentView as? NSCollectionView)
        let layout = try #require(document.collectionViewLayout)
        let anchorIndex = IndexPath(item: 40, section: 0)
        let anchorBefore = try #require(layout.layoutAttributesForItem(at: anchorIndex)).frame.minY
        scroll.contentView.scroll(to: NSPoint(x: 0, y: anchorBefore - 16))
        scroll.reflectScrolledClipView(scroll.contentView)
        await fixture.settle()
        var worstPaintedDrift: CGFloat = 0
        for count in [100, 2, 80, 1] {
            blocks[0].text = String(repeating: "More detailed progress reported by the same operation. ", count: count)
            update()
            document.layoutSubtreeIfNeeded()
            fixture.window.contentView?.displayIfNeeded()
            let anchorAfter = try #require(layout.layoutAttributesForItem(at: anchorIndex)).frame.minY
            worstPaintedDrift = max(worstPaintedDrift,
                abs(anchorAfter - scroll.contentView.bounds.minY - 16))
            await fixture.settle()
        }
        print("Reflow (work=\(isWorkProcess)): worst painted reading drift \(worstPaintedDrift)")
        #expect(worstPaintedDrift < 1)
    }

    @Test
    func userScrollingCancelsAQueuedViewportRestore() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        fixture.context = "new-reading-context"
        fixture.memory.save(.init(offsetY: 120, followsLatest: false), for: fixture.context)
        fixture.coordinator.update(parent: fixture.parent())
        fixture.collectionView.layoutSubtreeIfNeeded()
        fixture.scrollView.onUserScroll?()
        fixture.scroll(to: 400)
        await fixture.settle()
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - 400) < 1)
        #expect(abs((fixture.memory.snapshot(for: fixture.context)?.offsetY ?? 0) - 400) < 1)
    }

    @Test
    func appendingWorkDoesNotPublishProvisionalScrollGeometry() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        var blocks = (0..<160).map { index in
            TranscriptBlock(id: "append-work-\(index)", kind: .thought,
                text: "Step \(index). " + String(repeating: "Wrapped operation text. ", count: index % 9 + 1))
        }
        func update() {
            fixture.items = [.workProcess(ConversationWorkProcess(id: "appending-work", blocks: blocks))]
            fixture.coordinator.update(parent: fixture.parent())
        }
        fixture.memory.save(.init(offsetY: 0, followsLatest: false), for: fixture.context)
        update()
        fixture.window.makeKeyAndOrderFront(nil)
        await fixture.settle()
        await fixture.settle()
        let item = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0))
            as? WorkProcessCollectionItem)
        let inner = try #require(item.view as? FadingWorkScrollView)
        let document = try #require(inner.documentView as? NSCollectionView)
        let reading = document.frame.height - inner.contentSize.height - 400
        inner.contentView.scroll(to: NSPoint(x: 0, y: reading))
        inner.reflectScrolledClipView(inner.contentView)
        await fixture.settle()
        var publishedOffsets: [CGFloat] = []
        let recordOffset: @MainActor @Sendable () -> Void = {
            publishedOffsets.append(inner.contentView.bounds.minY)
        }
        let observer = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
            object: inner.contentView, queue: .main) { _ in
                MainActor.assumeIsolated { recordOffset() }
            }
        defer { NotificationCenter.default.removeObserver(observer) }
        for index in 0..<20 {
            blocks.append(TranscriptBlock(id: "added-\(index)", kind: .output,
                text: String(repeating: "New work arriving at the end. ", count: 25), activityType: "assistant_progress"))
            update()
            await fixture.settle()
        }
        let drift = publishedOffsets.map { abs($0 - reading) }.max() ?? 0
        print("Appended work scroll: published offset drift \(drift), samples \(publishedOffsets.count)")
        #expect(drift < 1)
        #expect(abs(inner.contentView.bounds.minY - reading) < 1)
    }

    @Test(arguments: [TranscriptAppearance.dark, .light])
    func switchingCandidatesKeepsTheDocumentPromptAndTabsMounted(appearance: TranscriptAppearance) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.appearance = appearance
        let agents = try (1...2).map { agent in
            try JSONDecoder().decode(RunAgentSummary.self, from: Data("""
            {"agent":\(agent),"name":"Agent \(agent)","status":"success","reasoningTokenCounts":{},
             "rejected":false,"recommended":false,"finalized":false}
            """.utf8))
        }
        func show(_ agent: Int) {
            fixture.context = transcriptScrollContextID(machineID: "machine", workspaceID: "workspace",
                conversationID: "conversation", runID: "run", agentID: String(agent))
            fixture.items = [
                .message(TranscriptBlock(id: "history-prompt", kind: .user, text: "Earlier question", isHistorical: true)),
                .message(TranscriptBlock(id: "history", kind: .output, text: "Accepted history", isHistorical: true)),
                .message(TranscriptBlock(id: "current-prompt", kind: .user, text: "Compare two candidates")),
                .agentSwitch(ConversationAgentSwitchState(runID: "run", agents: agents, selectedAgent: agent,
                    recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)),
                .message(TranscriptBlock(id: "answer-\(agent)", kind: .output, text: "Candidate \(agent) answer")),
            ]
            fixture.coordinator.update(parent: fixture.parent())
        }
        show(1)
        await fixture.settle()
        let document = fixture.collectionView
        let history = try #require(document.item(at: IndexPath(item: 1, section: 0)))
        let question = try #require(document.item(at: IndexPath(item: 2, section: 0)))
        let tabs = try #require(document.item(at: IndexPath(item: 3, section: 0)))
        let rail = fixture.scrollView.historyRail
        let historyButtons = try #require(rail.accessibilityChildren() as? [NSAccessibilityElement])
        #expect(historyButtons.count == 2)
        let tabY = try fixture.viewportY(for: "agent-switch:run")
        for agent in [2, 1, 2, 1, 2] {
            show(agent)
            // Check before yielding: no blank/replacement frame may escape.
            #expect(fixture.collectionView === document)
            #expect(!document.isHidden)
            #expect(document.item(at: IndexPath(item: 1, section: 0)) === history)
            #expect(document.item(at: IndexPath(item: 2, section: 0)) === question)
            #expect(document.item(at: IndexPath(item: 3, section: 0)) === tabs)
            let buttons = try #require(rail.accessibilityChildren() as? [NSAccessibilityElement])
            #expect(buttons.count == historyButtons.count)
            #expect(zip(buttons, historyButtons).allSatisfy { $0 === $1 })
            #expect(visibleText(in: document).contains("Candidate \(agent) answer"))
            #expect(!visibleText(in: document).contains("Candidate \(3 - agent) answer"))
            #expect(abs(try fixture.viewportY(for: "agent-switch:run") - tabY) < 2)
            try fixture.expectVisibleFramesMatchLayout()
            await fixture.settle()
            try fixture.expectVisibleFramesMatchLayout()
        }
    }

    @Test
    func switchingCandidatesRestoresTheirOwnReadingPositionBeforePainting() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        func context(_ agent: String) -> String {
            transcriptScrollContextID(machineID: "machine", workspaceID: "workspace",
                conversationID: "conversation", runID: "run", agentID: agent)
        }
        fixture.context = context("1")
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        fixture.scroll(to: 100)
        await fixture.settle()
        fixture.memory.save(.init(offsetY: 350, followsLatest: false), for: context("2"))
        let document = fixture.collectionView
        for (agent, offset) in [("2", 350.0), ("1", 100.0), ("2", 350.0)] {
            fixture.context = context(agent)
            fixture.coordinator.update(parent: fixture.parent())
            #expect(fixture.collectionView === document)
            #expect(abs(fixture.scrollView.contentView.bounds.minY - offset) < 2)
            await fixture.settle()
            #expect(abs(fixture.scrollView.contentView.bounds.minY - offset) < 2)
        }
    }

    @Test
    func typographyDuringStreamingDoesNotLeaveHistoryNavigationBusy() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = (0..<12).flatMap { index -> [ConversationTimelineItem] in
            [
                .message(TranscriptBlock(id: "q-\(index)", kind: .user, text: "Question \(index)")),
                .message(TranscriptBlock(id: "a-\(index)", kind: .output,
                    text: "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."))
            ]
        }
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let rail = fixture.scrollView.historyRail
        rail.activateEntry(at: 3)
        await fixture.settle()
        fixture.items.append(.message(TranscriptBlock(id: "stream", kind: .output,
            text: "An additional streaming response.")))
        fixture.coordinator.update(parent: fixture.parent())
        fixture.textSize = .extraLarge
        fixture.coordinator.update(parent: fixture.parent())
        try await Task.sleep(for: .milliseconds(160))
        await fixture.settle()
        rail.activateEntry(at: 7)
        await fixture.settle()
        #expect(abs(try fixture.viewportY(for: "message:q-7") - 16) < 2)
        #expect(rail.activeIndex == 7)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
    }

    @Test
    func typographyReplacesTheDrawingSurfaceWithoutLosingTheReadingAnchor() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.scroll(to: fixture.maximumY - 200)
        await fixture.settle()
        NotificationCenter.default.post(name: NSView.boundsDidChangeNotification,
            object: fixture.scrollView.contentView)
        let snapshot = try #require(fixture.memory.snapshot(for: fixture.context))
        let anchor = try #require(snapshot.anchorID)
        let originalY = try fixture.viewportY(for: anchor)
        let previousDocument = fixture.collectionView
        fixture.textSize = .extraLarge
        fixture.coordinator.update(parent: fixture.parent())
        try await Task.sleep(for: .milliseconds(160))
        await fixture.settle()
        #expect(fixture.collectionView !== previousDocument)
        #expect(previousDocument.superview == nil)
        let restoredY = try fixture.viewportY(for: anchor)
        #expect(abs(restoredY - originalY) < 2, "Anchor \(anchor): \(originalY) -> \(restoredY); before \(snapshot), after \(String(describing: fixture.memory.snapshot(for: fixture.context)))")
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
    }

    @Test
    func changingConversationRepaintsTheEntireNativeViewport() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.window.contentView?.wantsLayer = true
        for appearance in [TranscriptAppearance.dark, .light] {
            fixture.appearance = appearance
            fixture.context = "long-\(appearance)"
            fixture.items = [.message(TranscriptBlock(id: "old", kind: .output,
                text: String(repeating: "Previous conversation must disappear.\n\n", count: 80)))]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            fixture.scroll(to: 120)
            fixture.context = "empty-\(appearance)"
            fixture.items = []
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            let viewport = fixture.scrollView
            let bitmap = try #require(viewport.bitmapImageRepForCachingDisplay(in: viewport.bounds))
            viewport.cacheDisplay(in: viewport.bounds, to: bitmap)
            let canvas = try #require(ParallelCodexVisualStyle.resolvedNSColor(
                ParallelCodexVisualStyle.canvasNSColor, for: appearance
            ).usingColorSpace(.deviceRGB))
            #expect(fixture.collectionView.backgroundColors.first == canvas)
            let referencePixel = try #require(bitmap.colorAt(x: 0, y: 0)?.usingColorSpace(.deviceRGB))
            var incorrectPixels = 0
            for y in stride(from: 0, to: bitmap.pixelsHigh, by: 4) {
                for x in stride(from: 0, to: bitmap.pixelsWide, by: 4) {
                    let pixel = try #require(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                    if pixel.alphaComponent < 0.99
                        || abs(pixel.redComponent - referencePixel.redComponent) > 0.02
                        || abs(pixel.greenComponent - referencePixel.greenComponent) > 0.02
                        || abs(pixel.blueComponent - referencePixel.blueComponent) > 0.02 {
                        incorrectPixels += 1
                    }
                }
            }
            #expect(incorrectPixels == 0, "The viewport left \(incorrectPixels) samples unpainted or retained old content for \(appearance).")
        }
    }

    @Test
    func changingConversationDuringStreamingLeavesOnlyCurrentRows() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.window.contentView?.wantsLayer = true
        let longText = String(repeating: "A previous conversation.\n\n", count: 60)
        for cycle in 0..<20 {
            fixture.context = "old-\(cycle)"
            fixture.items = (0..<12).map {
                .message(TranscriptBlock(id: "old-\($0)", kind: .output, text: longText))
            }
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            fixture.coordinator.scrollToHistoryQuestion(id: "message:old-0")
            fixture.scroll(to: 120)
            fixture.items[0] = .message(TranscriptBlock(id: "old-0", kind: .output, text: longText + "STREAM UPDATE"))
            fixture.coordinator.update(parent: fixture.parent())
            let previousDocument = fixture.collectionView
            fixture.context = "current-\(cycle)"
            fixture.items = (0..<4).map {
                .message(TranscriptBlock(id: "current-\($0)", kind: .output, text: "CURRENT REPLY \($0)"))
            }
            fixture.coordinator.update(parent: fixture.parent())
            #expect(fixture.collectionView !== previousDocument)
            #expect(previousDocument.superview == nil)
            #expect(previousDocument.isHidden)
            #expect(previousDocument.dataSource == nil)
            await fixture.settle()
            try fixture.expectVisibleFramesMatchLayout()
            for view in nativeTextViews(in: fixture.collectionView)
                where !view.isHiddenOrHasHiddenAncestor && !view.visibleRect.isEmpty {
                #expect(view.string.hasPrefix("CURRENT REPLY"), "Orphaned drawing view: \(view.string.prefix(80))")
            }
        }
    }

    private func nativeTextViews(in view: NSView) -> [NSTextView] {
        if let text = view as? NSTextView { return [text] }
        return view.subviews.flatMap { nativeTextViews(in: $0) }
    }

    @Test
    func disclosurePlanPreservesTheAnswerAndUsesNewIndicesForChangedRows() {
        let summary = ConversationTimelineItem.message(TranscriptBlock(
            id: "work", kind: .activity, text: "Work", activityType: "work_summary"))
        let expanded = ConversationTimelineItem.message(TranscriptBlock(
            id: "work", kind: .activity, text: "Work", activityType: "work_summary_expanded"))
        let thought = ConversationTimelineItem.message(TranscriptBlock(id: "thought", kind: .thought, text: "Planning"))
        let answer = ConversationTimelineItem.message(TranscriptBlock(id: "answer", kind: .output, text: "Answer"))
        let streamed = ConversationTimelineItem.message(TranscriptBlock(id: "answer", kind: .output, text: "Answer continues"))
        #expect(ConversationTimelineUpdatePlan.makeForInlineDisclosure(
            old: [summary, answer], new: [expanded, thought, streamed]
        ) == ConversationTimelineUpdatePlan(reloadAll: false, changedIndices: [0, 2], removedIndices: [], insertedIndices: [1]))
        #expect(ConversationTimelineUpdatePlan.makeForInlineDisclosure(
            old: [expanded, thought, answer], new: [summary, answer]
        ) == ConversationTimelineUpdatePlan(reloadAll: false, changedIndices: [0], removedIndices: [1], insertedIndices: []))
    }

    @Test
    func asynchronousDetailsUseTheirOperationAsTheInlineControl() {
        let operation = WorkOperationRecord(id: "read", eventId: 42, kind: .fileRead, status: .completed, detailsAvailable: true)
        let row = ConversationTimelineItem.message(TranscriptBlock(
            id: "read", kind: .command, text: "Read review.txt", activityType: "work_operation_expanded", operation: operation))
        let loading = ConversationTimelineItem.message(TranscriptBlock(
            id: "read:details", kind: .activity, text: "Loading...", activityType: "operation_details"))
        let loaded = ConversationTimelineItem.message(TranscriptBlock(
            id: "read:details", kind: .activity, text: "alpha old", activityType: "operation_details"))
        #expect(ConversationTimelineProjection.changedInlineControlID(old: [row, loading], new: [row, loaded]) == "message:read")
        #expect(ConversationTimelineProjection.changedInlineControlID(old: [row, loaded], new: [row, loaded]) == nil)
    }

    @Test
    func intermediateRepliesKeepTheirViewsWhileAdjacentWorkTogglesAndTextStreams() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        var blocks = [
            TranscriptBlock(id: "thought", kind: .thought, text: "Planning"),
            TranscriptBlock(id: "progress", kind: .output, text: "Found the cause.", activityType: "assistant_progress"),
            TranscriptBlock(id: "command", kind: .command, text: "swift test"),
            TranscriptBlock(id: "next", kind: .output, text: "Checking the result.", activityType: "assistant_progress"),
        ]
        func project(_ expanded: Bool) -> [ConversationTimelineItem] {
            ConversationTimelineProjection.make(blocks: TranscriptFormatter.blocksWithWorkDisclosure(
                blocks, runID: "run", agent: 1, expanded: false,
                sectionOverrides: ["work-summary-run-1-thought": expanded]), switchState: nil)
        }
        fixture.items = project(false)
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let progressView = try #require(fixture.collectionView.item(at: IndexPath(item: 1, section: 0)))
        let nextView = try #require(fixture.collectionView.item(at: IndexPath(item: 3, section: 0)))
        for index in 0..<10 {
            let expanded = index.isMultiple(of: 2)
            fixture.coordinator.prepareInlineControlMutation(id: "message:work-summary-run-1-thought")
            blocks[1].text = "Found the cause. Update \(index)."
            fixture.items = project(expanded)
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            let replyIndex = try #require(fixture.items.firstIndex { $0.id == "message:progress" })
            let nextIndex = try #require(fixture.items.firstIndex { $0.id == "message:next" })
            #expect(fixture.collectionView.item(at: IndexPath(item: replyIndex, section: 0)) === progressView)
            #expect(fixture.collectionView.item(at: IndexPath(item: nextIndex, section: 0)) === nextView)
            #expect(visibleText(in: progressView.view).contains(blocks[1].text))
            #expect(visibleText(in: nextView.view).contains(blocks[3].text))
            try fixture.expectVisibleFramesMatchLayout()
        }
    }

    @Test
    func rapidDisclosureKeepsVisibleHistoryAndAnswerViewsMounted() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let question = ConversationTimelineItem.message(TranscriptBlock(id: "question", kind: .user, text: "Question"))
        let summary = TranscriptBlock(id: "work", kind: .activity, text: "Work process", activityType: "work_summary")
        var expanded = summary
        expanded.activityType = "work_summary_expanded"
        let thought = ConversationTimelineItem.message(TranscriptBlock(id: "thought", kind: .thought, text: "Planning the next step"))
        var answerBlock = TranscriptBlock(id: "answer", kind: .output, text: "The final answer.")
        let answer = ConversationTimelineItem.message(answerBlock)
        fixture.items = [question, .message(summary), answer]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let questionView = try #require(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)))
        let summaryView = try #require(fixture.collectionView.item(at: IndexPath(item: 1, section: 0)))
        let answerView = try #require(fixture.collectionView.item(at: IndexPath(item: 2, section: 0)))
        let anchorY = try fixture.viewportY(for: "message:work")
        for index in 0..<60 {
            let isExpanded = index.isMultiple(of: 2)
            fixture.coordinator.prepareInlineControlMutation(id: "message:work")
            answerBlock.text = "Answer update \(index)"
            fixture.items = isExpanded ? [question, .message(expanded), thought, .message(answerBlock)] : [question, .message(summary), .message(answerBlock)]
            fixture.coordinator.update(parent: fixture.parent())
            for _ in 0..<2 {
                #expect(fixture.collectionView.item(at: IndexPath(item: 0, section: 0)) === questionView)
                #expect(fixture.collectionView.item(at: IndexPath(item: 1, section: 0)) === summaryView)
                #expect(fixture.collectionView.item(at: IndexPath(item: isExpanded ? 3 : 2, section: 0)) === answerView)
                #expect(visibleText(in: answerView.view).contains(answerBlock.text))
                #expect(abs(try fixture.viewportY(for: "message:work") - anchorY) < 1)
                try fixture.expectVisibleFramesMatchLayout()
                await Task.yield()
            }
        }
        await fixture.settle()
        try fixture.expectVisibleFramesMatchLayout()
    }

    @Test
    func reopeningWorkRemeasuresContentChangedWhileHidden() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let summary = TranscriptBlock(id: "work", kind: .activity, text: "Work process", activityType: "work_summary")
        var expanded = summary
        expanded.activityType = "work_summary_expanded"
        for text in ["One line", String(repeating: "Another line of reasoning.\n", count: 100), "Short again"] {
            fixture.items = [.message(summary)]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            fixture.coordinator.prepareInlineControlMutation(id: "message:work")
            fixture.items = [.message(expanded), .message(TranscriptBlock(id: "thought", kind: .thought, text: text))]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            let path = IndexPath(item: 1, section: 0)
            let attributes = try #require(fixture.collectionView.collectionViewLayout?.layoutAttributesForItem(at: path))
            let expected = TranscriptCollectionItem.preferredHeight(
                for: TranscriptBlock(id: "thought", kind: .thought, text: text),
                textSize: .standard, accentColor: .orange, appearance: .dark, availableWidth: attributes.size.width
            )
            #expect(abs(attributes.size.height - expected) < 1)
            try fixture.expectVisibleFramesMatchLayout()
        }
    }

    @Test
    func historyRailNavigationPreservesBranchAndReadingPositionDuringUpdates() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = (0..<12).flatMap { index -> [ConversationTimelineItem] in
            [
                .message(TranscriptBlock(id: "q-\(index)", kind: .user, text: "Question \(index)")),
                .message(TranscriptBlock(id: "a-\(index)", kind: .output,
                                         text: "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."))
            ]
        }
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let rail = fixture.scrollView.historyRail
        #expect(rail.entries.count == 12)
        #expect(rail.activeIndex == 11)
        let before = fixture.scrollView.contentView.bounds.minY
        rail.hover(at: 3)
        #expect(fixture.scrollView.contentView.bounds.minY == before)
        rail.activateEntry(at: 3)
        await fixture.settle()
        #expect(abs(try fixture.viewportY(for: "message:q-3") - 16) < 2)
        #expect(rail.activeIndex == 3)
        #expect(fixture.memory.snapshot(for: "first")?.followsLatest == false)
        #expect(fixture.agentSelections.isEmpty)

        fixture.items.append(.message(TranscriptBlock(id: "stream", kind: .output,
                                                      text: "An additional streaming response.")))
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(abs(try fixture.viewportY(for: "message:q-3") - 16) < 2)
        fixture.context = "another-conversation"
        fixture.items = [.message(TranscriptBlock(id: "other", kind: .user, text: "A new question."))]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(rail.isHidden)
        #expect(rail.entries.map(\.id) == ["message:other"])
        fixture.coordinator.scrollToHistoryQuestion(id: "message:q-3")
        #expect(fixture.agentSelections.isEmpty)
        #expect(fixture.scrollView.contentView.bounds.minY == 0)
    }

    @Test
    func expandingWorkNeverCompositesOldTextOverNewRows() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let summary = TranscriptBlock(
            id: "work", kind: .activity, text: "Work process",
            activityType: "work_summary"
        )
        let answer = TranscriptBlock(id: "answer", kind: .output, text: "The final answer.")
        let history: [ConversationTimelineItem] = (0..<12).map {
            .message(TranscriptBlock(id: "history-\($0)", kind: .output,
                                     text: "Earlier message \($0).\n\nA second paragraph."))
        }
        fixture.items = history + [.message(summary), .message(answer)]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()

        for appearance in Array(repeating: [TranscriptAppearance.dark, .light], count: 15).flatMap({ $0 }) {
            fixture.appearance = appearance
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            fixture.scroll(to: fixture.maximumY)
            let originalY = try fixture.viewportY(for: "message:work")
            fixture.coordinator.prepareInlineControlMutation(id: "message:work")
            #expect(fixture.scrollView.superview?.subviews.compactMap { $0 as? NSImageView }.isEmpty == true)
            fixture.items = history + [.message(TranscriptBlock(
                id: "work", kind: .activity, text: "Work process", activityType: "work_summary_expanded"
            ))] + (0..<30).map {
                .message(TranscriptBlock(id: "reasoning-\($0)", kind: .thought,
                                         text: "Reasoning \($0)\nAnother line of reasoning."))
            } + [.message(answer)]
            fixture.coordinator.update(parent: fixture.parent())
            #expect(abs(try fixture.viewportY(for: "message:work") - originalY) < 1)
            for _ in 0..<10 {
                try await Task.sleep(for: .milliseconds(16))
                #expect(abs(try fixture.viewportY(for: "message:work") - originalY) < 1)
                try fixture.expectVisibleFramesMatchLayout()
            }
            await fixture.settle()
            #expect(abs(try fixture.viewportY(for: "message:work") - originalY) < 1)
            try await Task.sleep(for: .milliseconds(160))
            #expect(fixture.scrollView.superview?.subviews.compactMap { $0 as? NSImageView }.isEmpty == true)
            for index in 1..<fixture.items.count {
                let previous = try #require(fixture.collectionView.collectionViewLayout?
                    .layoutAttributesForItem(at: IndexPath(item: index - 1, section: 0)))
                let current = try #require(fixture.collectionView.collectionViewLayout?
                    .layoutAttributesForItem(at: IndexPath(item: index, section: 0)))
                #expect(previous.frame.maxY <= current.frame.minY)
            }
            fixture.items = history + [.message(summary), .message(answer)]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            try await Task.sleep(for: .milliseconds(160))
        }
    }

    @Test
    func operationDetailsPreserveTheirRowWhileLoadingAndAfterArrival() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let history = fixture.items
        let operation = WorkOperationRecord(id: "read", eventId: 42, kind: .fileRead,
            status: .completed, targets: [WorkOperationTarget(path: "review.txt")], detailsAvailable: true)
        let row = TranscriptBlock(id: "read", kind: .command, text: "Read review.txt",
            activityType: "work_operation", operation: operation)
        let answer = TranscriptBlock(id: "answer", kind: .output,
            text: String(repeating: "The final answer has more paragraphs.\n\n", count: 30))
        fixture.items = history + [.message(row), .message(answer)]
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        let rowY = try fixture.viewportY(for: "message:read") + fixture.scrollView.contentView.bounds.minY
        fixture.scroll(to: rowY - 120)
        await fixture.settle()
        let originalY = try fixture.viewportY(for: "message:read")
        fixture.coordinator.prepareInlineControlMutation(id: "message:read")
        var expanded = row
        expanded.activityType = "work_operation_expanded"
        var mountedDetail: NSCollectionViewItem?
        for text in ["Loading...", String(repeating: "alpha old\n", count: 60)] {
            fixture.items = history + [.message(expanded), .message(TranscriptBlock(
                id: "read:details", kind: .activity, text: text, activityType: "operation_details"
            )), .message(answer)]
            fixture.coordinator.update(parent: fixture.parent())
            for _ in 0..<10 {
                try await Task.sleep(for: .milliseconds(16))
                #expect(abs(try fixture.viewportY(for: "message:read") - originalY) < 1)
            }
            let detailView = try #require(fixture.collectionView.item(at: IndexPath(item: history.count + 1, section: 0)))
            if let mountedDetail { #expect(detailView === mountedDetail) }
            else { mountedDetail = detailView }
        }
    }

    @Test
    func persistedScrollDoesNotSuppressTheLiveVisibilityUpdate() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.published.removeAll()
        fixture.liveValue = nil

        fixture.scroll(to: 100)
        await fixture.settle()
        #expect(fixture.memory.snapshot(for: "first")?.followsLatest == false)
        #expect(fixture.published == [false])
        #expect(fixture.liveValue == false)

        fixture.scroll(to: 150)
        await fixture.settle()
        #expect(fixture.published == [false])

        fixture.scroll(to: fixture.maximumY)
        await fixture.settle()
        #expect(fixture.published == [false, true])
        #expect(fixture.liveValue == true)
    }

    @Test
    func cachedThousandOperationExpansionHasBoundedLayoutCost() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let summary = TranscriptBlock(id: "work", kind: .activity, text: "Work process", activityType: "work_summary")
        let expandedSummary = TranscriptBlock(id: "work", kind: .activity, text: "Work process", activityType: "work_summary_expanded")
        let operations: [ConversationTimelineItem] = (0..<1000).map { index in
            let operation = WorkOperationRecord(id: "op-\(index)", eventId: index, kind: .fileRead, status: .completed,
                targets: [WorkOperationTarget(path: "src/file-\(index).swift")])
            return .message(TranscriptBlock(id: "op-\(index)", kind: .command,
                text: WorkOperationPresentation.summary(operation, language: .english), operation: operation))
        }
        var samples: [Double] = []
        for index in 0..<31 {
            fixture.items = [.message(summary)]
            fixture.coordinator.update(parent: fixture.parent())
            await fixture.settle()
            fixture.coordinator.prepareInlineControlMutation(id: "message:work")
            fixture.items = [.message(expandedSummary)] + operations
            let started = ProcessInfo.processInfo.systemUptime
            fixture.coordinator.update(parent: fixture.parent())
            let milliseconds = (ProcessInfo.processInfo.systemUptime - started) * 1000
            if index > 0 { samples.append(milliseconds) }
            #expect(abs(try fixture.viewportY(for: "message:work") - 24) < 1)
        }
        let p95 = samples.sorted()[Int(Double(samples.count - 1) * 0.95)]
        print("Work layout benchmark: 1000 operations, \(samples.count) warm samples, P95 \(p95) ms")
        if ProcessInfo.processInfo.environment["PCR_ENFORCE_WORK_LAYOUT_BUDGET"] == "1" {
            #expect(p95 < 50)
        }
    }

    @Test
    func pendingScrollPublicationDoesNotUpdateTheNextConversation() async {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.scroll(to: 100)
        fixture.context = "second"
        fixture.liveValue = nil
        fixture.published.removeAll()
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()

        #expect(!fixture.published.contains(false))
        #expect(fixture.liveValue == true)
        #expect(fixture.memory.snapshot(for: "first")?.followsLatest == false)
        #expect(fixture.memory.snapshot(for: "second")?.followsLatest == true)
    }

    @Test
    func nextTurnPreservesHistoricalReadingButNotAnotherCandidatesOldPosition() async {
        let fixture = Fixture()
        defer { fixture.close() }
        func context(_ turn: String, _ agent: String) -> String {
            transcriptScrollContextID(
                machineID: "machine", workspaceID: "workspace", conversationID: "conversation",
                runID: turn, agentID: agent
            )
        }
        fixture.context = context("first-turn", "1")
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        fixture.scroll(to: 100)
        await fixture.settle()
        fixture.memory.save(.init(offsetY: 300, followsLatest: false), for: context("first-turn", "2"))

        fixture.context = context("next-turn", "1")
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - 100) < 2)

        fixture.context = context("next-turn", "2")
        fixture.coordinator.update(parent: fixture.parent())
        await fixture.settle()
        #expect(abs(fixture.scrollView.contentView.bounds.minY - fixture.maximumY) < 2)
        #expect(fixture.memory.snapshot(for: context("first-turn", "2"))?.offsetY == 300)
    }

    @MainActor
    private func visibleText(in view: NSView) -> String {
        guard !view.isHidden, view.alphaValue > 0 else { return "" }
        if let text = view as? NSTextView { return text.string }
        return view.subviews.map { visibleText(in: $0) }.joined(separator: "\n")
    }

    @MainActor
    private final class Fixture {
        let defaults: UserDefaults
        let suite = "TranscriptScrollPublicationTests.\(UUID().uuidString)"
        let memory: TranscriptScrollMemory
        let window: NSWindow
        let scrollView = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 480))
        var collectionView: WidthAwareCollectionView {
            scrollView.documentView as! WidthAwareCollectionView
        }
        var coordinator: ConversationTimelineView.Coordinator!
        var context = "first"
        var liveValue: Bool?
        var published: [Bool] = []
        var agentSelections: [Int] = []
        var appearance: TranscriptAppearance = .dark
        var textSize: AppTextSize = .standard
        var itemsRevision: UInt64?
        var jumpRequest = 0
        var items: [ConversationTimelineItem] = (0..<20).map {
            .message(TranscriptBlock(id: "row-\($0)", kind: .output,
                                     text: "A message with enough content to scroll.\n\nSecond paragraph."))
        }

        init() {
            _ = NSApplication.shared
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
            window = NSWindow(
                contentRect: scrollView.frame, styleMask: [.titled], backing: .buffered, defer: false
            )
            window.isReleasedWhenClosed = false
            coordinator = ConversationTimelineView.Coordinator(parent: parent())
            let collectionView = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            collectionView.frame = scrollView.bounds
            scrollView.documentView = collectionView
            window.contentView = NSView(frame: scrollView.frame)
            window.contentView?.addSubview(scrollView)
            coordinator.attach(scrollView: scrollView, collectionView: collectionView)
            coordinator.update(parent: parent())
        }

        var maximumY: CGFloat {
            max(0, collectionView.frame.height - scrollView.contentView.bounds.height)
        }

        func parent() -> ConversationTimelineView {
            let key = context
            return ConversationTimelineView(
                language: .english, textSize: textSize, accentColor: .orange, appearance: appearance,
                contextID: key,
                items: items,
                contentIsLoading: false, jumpToLatestRequest: jumpRequest, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in },
                selectAgent: { [unowned self] in agentSelections.append($0) }, toggleResults: {},
                itemsRevision: itemsRevision,
                isNearBottom: Binding(
                    get: { [unowned self] in liveValue ?? memory.snapshot(for: key)?.followsLatest ?? true },
                    set: { [unowned self] in liveValue = $0; published.append($0) }
                ),
                inlineAgentSwitchPosition: .constant(.missing)
            )
        }

        func scroll(to y: CGFloat) {
            scrollView.contentView.scroll(to: NSPoint(x: 0, y: y))
            scrollView.reflectScrolledClipView(scrollView.contentView)
        }

        func viewportY(for id: String) throws -> CGFloat {
            let index = try #require(items.firstIndex(where: { $0.id == id }))
            let attributes = try #require(collectionView.collectionViewLayout?
                .layoutAttributesForItem(at: IndexPath(item: index, section: 0)))
            return attributes.frame.minY - scrollView.contentView.bounds.minY
        }

        func expectVisibleFramesMatchLayout() throws {
            for path in collectionView.indexPathsForVisibleItems() {
                let item = try #require(collectionView.item(at: path))
                let attributes = try #require(collectionView.collectionViewLayout?.layoutAttributesForItem(at: path))
                #expect(abs(item.view.frame.minY - attributes.frame.minY) < 1)
                #expect(abs(item.view.frame.height - attributes.frame.height) < 1)
            }
        }

        func settle() async {
            try? await Task.sleep(for: .milliseconds(30))
            collectionView.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(30))
        }

        func close() {
            coordinator.detach()
            memory.freezeForTermination()
            window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }
}
