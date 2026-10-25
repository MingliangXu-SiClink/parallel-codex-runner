import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ScrollMotionOwnershipTests {
    @Test
    func aGeometryCommitRevokesAnInFlightMotionWithoutLosingFollowIntent() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.jumpRequest = 1
        fixture.update()
        try await fixture.waitUntil { fixture.motion.isAnimating }
        fixture.clock.now = 0.08
        try await fixture.waitUntil { fixture.scroll.contentView.bounds.minY > 100 }
        #expect(fixture.scroll.contentView.bounds.minY < fixture.maximum - 100)

        fixture.items.insert(.message(.init(id: "earlier", kind: .activity,
            text: Array(repeating: "An earlier output page", count: 100).joined(separator: "\n"),
            activityType: "operation_details")), at: 0)
        fixture.update()
        #expect(!fixture.motion.isAnimating)
        #expect(abs(fixture.scroll.contentView.bounds.minY - fixture.maximum) < 1)
        let committed = fixture.scroll.contentView.bounds.minY
        fixture.clock.now = 1
        await fixture.settle()
        #expect(fixture.scroll.contentView.bounds.minY == committed)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == true)
    }

    @Test
    func aQueuedExplicitJumpCannotOvertakeLaterUserNavigation() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.jumpRequest = 1
        fixture.update()
        fixture.scroll.onUserScroll?()
        fixture.scroll.contentView.scroll(to: NSPoint(x: 0, y: 400))
        fixture.scroll.reflectScrolledClipView(fixture.scroll.contentView)
        fixture.clock.now = 1
        try await Task.sleep(for: .milliseconds(240))
        #expect(!fixture.motion.isAnimating)
        #expect(abs(fixture.scroll.contentView.bounds.minY - 400) < 1)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
    }

    @Test
    func anExplicitZeroDistanceJumpChangesIntentButAnOuterWheelHandoffDoesNot() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "output", kind: .activity, text: "Short output",
            activityType: "operation_details"))]
        fixture.update()
        await fixture.settle()
        #expect(fixture.maximum == 0)
        let cg = try #require(CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
            wheelCount: 1, wheel1: -25, wheel2: 0, wheel3: 0))
        fixture.scroll.scrollWheel(with: try #require(NSEvent(cgEvent: cg)))
        try await Task.sleep(for: .milliseconds(240))
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)

        fixture.jumpRequest = 1
        fixture.update()
        await fixture.settle()
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == true)
        fixture.items = Self.longOutput()
        fixture.update()
        #expect(abs(fixture.scroll.contentView.bounds.minY - fixture.maximum) < 1)
        await fixture.settle()
        #expect(!fixture.motion.isAnimating)
    }

    @Test
    func changingContextRetiresTheOldMotionAndRestoresOnlyTheNewContext() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.jumpRequest = 1
        fixture.update()
        try await fixture.waitUntil { fixture.motion.isAnimating }
        fixture.context = "new-context"
        fixture.memory.save(.init(offsetY: 600, followsLatest: false), for: fixture.context)
        fixture.update()
        fixture.clock.now = 1
        await fixture.settle()
        #expect(!fixture.motion.isAnimating)
        #expect(abs(fixture.scroll.contentView.bounds.minY - 600) < 1)
        #expect(fixture.memory.snapshot(for: fixture.context)?.followsLatest == false)
    }

    private static func longOutput() -> [ConversationTimelineItem] {
        [.message(.init(id: "output", kind: .activity,
            text: (0..<300).map { "Line \($0): fixed output content" }.joined(separator: "\n"),
            activityType: "operation_details"))]
    }

    @MainActor
    private final class Clock { var now: TimeInterval = 0 }

    @MainActor
    private final class Fixture {
        let suite = "ScrollMotionOwnershipTests.\(UUID().uuidString)"
        let defaults: UserDefaults
        let memory: TranscriptScrollMemory
        let window: NSWindow
        let clock = Clock()
        let motion: NativeScrollMotion
        let scroll = FadingWorkScrollView(frame: NSRect(x: 0, y: 0, width: 920, height: 360))
        var coordinator: ConversationTimelineView.Coordinator!
        var context = "motion"
        var jumpRequest = 0
        var items = ScrollMotionOwnershipTests.longOutput()

        init() {
            _ = NSApplication.shared
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
            window = NSWindow(contentRect: scroll.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = scroll
            let clock = self.clock
            // Exercise the actual timer/animation while its window stays hidden.
            // Only visibility eligibility and monotonic time are substituted.
            motion = NativeScrollMotion(time: { clock.now }, allowsAnimation: { _ in true })
            memory.save(.init(offsetY: 0, followsLatest: false), for: context)
            coordinator = .init(parent: parent(), scrollMotion: motion)
            let collection = ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            collection.frame = scroll.bounds
            scroll.documentView = collection
            coordinator.attach(scrollView: scroll, collectionView: collection)
            update()
        }

        func parent() -> ConversationTimelineView {
            ConversationTimelineView(language: .english, textSize: .standard, accentColor: .orange,
                appearance: .dark, contextID: context, items: items, contentIsLoading: false,
                jumpToLatestRequest: jumpRequest, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                isWorkProcess: true, isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
        }

        var maximum: CGFloat { max(0, (scroll.documentView?.frame.height ?? 0) - scroll.contentSize.height) }
        func update() { coordinator.update(parent: parent()) }

        func waitUntil(_ condition: () -> Bool) async throws {
            for _ in 0..<50 {
                if condition() { return }
                try await Task.sleep(for: .milliseconds(10))
            }
            #expect(condition(), "The production scroll callback did not run")
        }

        func settle() async {
            for _ in 0..<4 {
                try? await Task.sleep(for: .milliseconds(20))
                window.contentView?.layoutSubtreeIfNeeded()
            }
            #expect(!window.isVisible && !window.isKeyWindow)
        }

        func close() {
            coordinator.detach()
            memory.freezeForTermination()
            window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }
}
