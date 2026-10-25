import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkScrollChainingTests {
    @Test(arguments: [true, false], [1500.0, 150000.0])
    func onlyOneVisibleRowImmediatelyScrollsTheConversation(fromBottom: Bool, historyHeight: CGFloat) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.inner.documentView?.setFrameSize(NSSize(width: 400, height: historyHeight))
        fixture.position(600)
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: fromBottom
            ? 200 + fixture.outer.contentSize.height - 24
            : 200 - fixture.inner.frame.height + 24))
        #expect(abs(fixture.visibleWorkHeight - 24) < 1)
        let outerReading = fixture.outer.contentView.bounds.minY
        fixture.inner.scrollWheel(with: try wheel(fromBottom ? -40 : 40, phase: .began))
        #expect(fixture.inner.contentView.bounds.minY == 600)
        #expect(abs(fixture.outer.contentView.bounds.minY - (outerReading + (fromBottom ? 40 : -40))) < 1)
        #expect(fixture.outerScrollCount == 1)
    }

    @Test
    func conversationOwnedGestureCannotBeRecapturedWhenTheWorkLogBecomesVisible() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(600)
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: 200 + fixture.outer.contentSize.height - 24))
        fixture.inner.scrollWheel(with: try wheel(-40, phase: .began, timestamp: 100))
        // The next native event can hit the newly revealed child instead of its parent.
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: fixture.outer.contentView.bounds.minY + 40))
        #expect(abs(fixture.visibleWorkHeight - fixture.inner.bounds.height) < 1)
        let reading = fixture.outer.contentView.bounds.minY
        fixture.inner.scrollWheel(with: try wheel(-30, phase: .changed, timestamp: 100.05))
        fixture.inner.scrollWheel(with: try wheel(0, phase: .ended, timestamp: 100.1))
        fixture.inner.scrollWheel(with: try wheel(-20, momentum: 1, timestamp: 100.4))
        fixture.inner.scrollWheel(with: try wheel(-10, momentum: 2, timestamp: 100.45))
        fixture.inner.scrollWheel(with: try wheel(0, momentum: 3, timestamp: 100.5))
        #expect(fixture.inner.contentView.bounds.minY == 600)
        #expect(abs(fixture.outer.contentView.bounds.minY - reading - 60) < 1)
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: fixture.outer.contentView.bounds.minY + 40))
        fixture.inner.scrollWheel(with: try wheel(-25, phase: .began, timestamp: 101))
        #expect(fixture.inner.contentView.bounds.minY == 625)
    }

    @Test
    func conversationOwnedGestureStartsOutsideTheWorkLog() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(600)
        fixture.outer.scrollWheel(with: try wheel(-20, phase: .began))
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: fixture.outer.contentView.bounds.minY + 40))
        let reading = fixture.outer.contentView.bounds.minY
        fixture.inner.scrollWheel(with: try wheel(-30, phase: .changed))
        #expect(fixture.inner.contentView.bounds.minY == 600)
        #expect(abs(fixture.outer.contentView.bounds.minY - reading - 30) < 1)
    }

    @Test
    func conversationOwnershipSurvivesAlternatingNativeHitTargets() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(600)
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: 200 + fixture.outer.contentSize.height - 24))
        fixture.inner.scrollWheel(with: try wheel(-40, phase: .began))
        fixture.outer.scrollWheel(with: try wheel(-30, phase: .changed))
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: fixture.outer.contentView.bounds.minY + 40))
        fixture.inner.scrollWheel(with: try wheel(-20, phase: .changed))
        fixture.outer.scrollWheel(with: try wheel(0, phase: .ended))
        fixture.inner.scrollWheel(with: try wheel(-10, momentum: 1))
        fixture.outer.scrollWheel(with: try wheel(-15, momentum: 2))
        fixture.inner.scrollWheel(with: try wheel(0, momentum: 3))
        #expect(fixture.inner.contentView.bounds.minY == 600)
        #expect(abs(fixture.outer.contentView.bounds.minY - 315) < 1)
    }

    @Test
    func lostPhasesAndDetachedConversationDoNotLeaveAnInfiniteScrollOwner() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(600)
        fixture.outer.scrollWheel(with: try wheel(-20, phase: .began, timestamp: 100))
        fixture.inner.scrollWheel(with: try wheel(-30, phase: .changed, timestamp: 102))
        #expect(fixture.inner.contentView.bounds.minY == 630)
        fixture.outer.claimScrollGesture()
        fixture.outer.removeFromSuperview()
        #expect(!fixture.outer.ownsScrollGesture)
    }

    @Test
    func interiorScrollingDoesNotMoveTheConversation() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        var innerPhases = 0
        var outerPhases = 0
        fixture.inner.onScrollEvent = { _ in innerPhases += 1 }
        fixture.outer.onScrollEvent = { _ in outerPhases += 1 }
        fixture.position(600)
        fixture.inner.scrollWheel(with: try wheel(-40))
        #expect(fixture.inner.contentView.bounds.minY == 640)
        fixture.inner.scrollWheel(with: try wheel(25))
        #expect(fixture.inner.contentView.bounds.minY == 615)
        #expect(fixture.outerScrollCount == 0)
        #expect(fixture.outer.contentView.bounds.minY == 200)
        #expect(innerPhases == 2 && outerPhases == 2)
    }

    @Test(arguments: [true, false])
    func continuingAtEitherEdgeReachesTheConversation(precise: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        var innerNotifications = 0
        var outerNotifications = 0
        fixture.inner.onUserScroll = { innerNotifications += 1 }
        fixture.outer.onUserScroll = { outerNotifications += 1 }
        fixture.position(fixture.maximum)
        let down = try wheel(precise ? -40 : -3, precise: precise)
        fixture.inner.scrollWheel(with: down)
        await fixture.settle()
        #expect(fixture.inner.contentView.bounds.minY == fixture.maximum)
        #expect(fixture.outer.contentView.bounds.minY > 200)
        fixture.position(0)
        let beforeUp = fixture.outer.contentView.bounds.minY
        let up = try wheel(precise ? 40 : 3, precise: precise)
        fixture.inner.scrollWheel(with: up)
        await fixture.settle()
        #expect(fixture.inner.contentView.bounds.minY == 0)
        #expect(fixture.outer.contentView.bounds.minY < beforeUp)
        #expect(innerNotifications == 2)
        #expect(outerNotifications == 2)
    }

    @Test(arguments: [true, false], [true, false])
    func crossingAnEdgeTransfersOnlyTheUnusedDeltaAndPreservesTheEvent(down: Bool, momentum: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(down ? fixture.maximum - 8 : 8)
        let direction: CGFloat = down ? -1 : 1
        let event = try wheel(down ? -24 : 24, phase: momentum ? [] : .changed, momentum: momentum ? 2 : 0)
        fixture.inner.scrollWheel(with: event)
        await fixture.settle()
        #expect(fixture.inner.contentView.bounds.minY == (down ? fixture.maximum : 0))
        #expect(abs(event.scrollingDeltaY - direction * 24) < 0.01)
        #expect(event.phase == (momentum ? [] : .changed))
        #expect(event.momentumPhase == (momentum ? .changed : []))
        #expect(fixture.outerScrollCount == 1)
        #expect(abs(fixture.outer.contentView.bounds.minY - (200 - direction * 16)) < 1)
        // The parent's native handler first receives a changed phase, never a began.
        fixture.outer.scrollWheel(with: try wheel(down ? -20 : 20,
            phase: momentum ? [] : .changed, momentum: momentum ? 2 : 0))
        #expect(abs(fixture.outer.contentView.bounds.minY - (200 - direction * 36)) < 1)
        fixture.inner.scrollWheel(with: try wheel(down ? -25 : 25,
            phase: momentum ? [] : .changed, momentum: momentum ? 2 : 0))
        #expect(abs(fixture.outer.contentView.bounds.minY - (200 - direction * 61)) < 1)
        #expect(fixture.inner.contentView.bounds.minY == (down ? fixture.maximum : 0))
    }

    @Test
    func aStreamingUpdateDoesNotStealTheHandedOffGestureOrItsMomentum() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(fixture.maximum)
        fixture.inner.scrollWheel(with: try wheel(-25, phase: .began))
        let reading = fixture.inner.contentView.bounds.minY
        fixture.inner.documentView?.setFrameSize(NSSize(width: 400, height: 1800))
        fixture.inner.scrollWheel(with: try wheel(-20, phase: .changed))
        fixture.inner.scrollWheel(with: try wheel(0, phase: .ended))
        fixture.inner.scrollWheel(with: try wheel(-15, momentum: 1))
        fixture.inner.scrollWheel(with: try wheel(-10, momentum: 2))
        fixture.inner.scrollWheel(with: try wheel(0, momentum: 3))
        #expect(fixture.inner.contentView.bounds.minY == reading)
        #expect(fixture.outerScrollCount == 6)
        #expect(abs(fixture.outer.contentView.bounds.minY - 270) < 1)

        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: 310))
        fixture.inner.scrollWheel(with: try wheel(-30, phase: .began))
        #expect(fixture.inner.contentView.bounds.minY == reading + 30)
        #expect(fixture.outerScrollCount == 6)
    }

    @Test
    func reversingTheFingerDirectionKeepsConversationOwnershipUntilANewGesture() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(fixture.maximum)
        fixture.inner.scrollWheel(with: try wheel(-20, phase: .began))
        await fixture.settle()
        let outerReading = fixture.outer.contentView.bounds.minY
        fixture.inner.scrollWheel(with: try wheel(30, phase: .changed))
        #expect(fixture.inner.contentView.bounds.minY == fixture.maximum)
        #expect(fixture.outer.contentView.bounds.minY == outerReading - 30)
        #expect(fixture.outerScrollCount == 2)
        fixture.inner.scrollWheel(with: try wheel(30, phase: .began))
        #expect(fixture.inner.contentView.bounds.minY == fixture.maximum - 30)
        #expect(fixture.outerScrollCount == 2)
    }

    @Test
    func shortWorkLogsNeverTrapScrolling() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.inner.documentView?.setFrameSize(NSSize(width: 400, height: 100))
        fixture.inner.scrollWheel(with: try wheel(-35))
        await fixture.settle()
        #expect(fixture.outerScrollCount == 1)
        #expect(fixture.outer.contentView.bounds.minY > 200)
        #expect(fixture.inner.contentView.bounds.minY == 0)
    }

    @Test
    func diagonalRemaindersKeepTheirAxisAndWheelOwnershipExpiresAfterTheBurst() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(fixture.maximum - 8)
        fixture.inner.scrollWheel(with: try wheel(-24, horizontal: 20, timestamp: 100))
        await fixture.settle()
        #expect(abs(fixture.outer.contentView.bounds.minY - 216) < 1)
        #expect(fixture.outer.contentView.bounds.minX == 0)
        let reading = fixture.inner.contentView.bounds.minY
        let outerReading = fixture.outer.contentView.bounds.minY
        fixture.inner.documentView?.setFrameSize(NSSize(width: 400, height: 1800))
        fixture.inner.scrollWheel(with: try wheel(-30, timestamp: 100.05))
        await fixture.settle()
        #expect(fixture.inner.contentView.bounds.minY == reading)
        #expect(fixture.outer.contentView.bounds.minY == outerReading + 30)
        #expect(fixture.outerScrollCount == 2)
        fixture.inner.scrollWheel(with: try wheel(-30, timestamp: 100.3))
        #expect(fixture.inner.contentView.bounds.minY == reading + 30)
        #expect(fixture.outer.contentView.bounds.minY == outerReading + 30)
        #expect(fixture.outerScrollCount == 2)
    }

    @Test(arguments: [true, false])
    func cancellationReleasesOwnershipButSameConversationViewReuseDoesNot(reuse: Bool) async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(fixture.maximum)
        fixture.inner.scrollWheel(with: try wheel(-20, phase: .began))
        if reuse {
            fixture.inner.removeFromSuperview()
            fixture.outer.documentView?.addSubview(fixture.inner)
        } else {
            let cancelled = try wheel(0, phase: .cancelled)
            fixture.inner.scrollWheel(with: cancelled)
            #expect(fixture.outerScrollCount == 2)
        }
        fixture.inner.documentView?.setFrameSize(NSSize(width: 400, height: 1800))
        let reading = fixture.inner.contentView.bounds.minY
        let count = fixture.outerScrollCount
        fixture.inner.scrollWheel(with: try wheel(-30, momentum: 1))
        #expect(fixture.inner.contentView.bounds.minY == reading + (reuse ? 0 : 30))
        #expect(fixture.outerScrollCount == count + (reuse ? 1 : 0))
    }

    @Test
    func aStickyAgentBarCannotMakeAClippedWorkViewportOwnTheGesture() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        await fixture.settle()
        fixture.position(600)
        fixture.inner.setFrameOrigin(NSPoint(x: 50, y: 220))
        #expect(fixture.visibleWorkHeight == fixture.inner.bounds.height)
        fixture.outer.obscuredTopHeight = 50
        fixture.inner.scrollWheel(with: try wheel(-25, phase: .began))
        #expect(fixture.inner.contentView.bounds.minY == 600)
        #expect(fixture.outer.contentView.bounds.minY == 225)
    }

    @Test
    func ownershipDoesNotFollowARecycledCellIntoAnotherConversation() async throws {
        let first = Fixture()
        let second = Fixture()
        defer { first.close(); second.close() }
        await first.settle()
        await second.settle()
        first.position(first.maximum)
        first.inner.scrollWheel(with: try wheel(-20, phase: .began))
        first.inner.removeFromSuperview()
        second.inner.removeFromSuperview()
        second.outer.documentView?.addSubview(first.inner)
        first.inner.documentView?.setFrameSize(NSSize(width: 400, height: 1800))
        let reading = first.inner.contentView.bounds.minY
        first.inner.scrollWheel(with: try wheel(-30, phase: .changed))
        #expect(first.inner.contentView.bounds.minY == reading + 30)
        #expect(second.outer.contentView.bounds.minY == 200)
    }

    private func wheel(_ delta: Int32, horizontal: Int32 = 0, precise: Bool = true, phase: NSEvent.Phase = [],
                       momentum: Int64 = 0, timestamp: TimeInterval? = nil) throws -> NSEvent {
        let event = try #require(CGEvent(scrollWheelEvent2Source: nil,
            units: precise ? .pixel : .line, wheelCount: 2, wheel1: delta, wheel2: horizontal, wheel3: 0))
        let scrollPhase: CGScrollPhase
        switch phase {
        case .began: scrollPhase = .began
        case .changed: scrollPhase = .changed
        case .ended: scrollPhase = .ended
        case .cancelled: scrollPhase = .cancelled
        case .mayBegin: scrollPhase = .mayBegin
        default: scrollPhase = CGScrollPhase(rawValue: 0)!
        }
        event.setIntegerValueField(.scrollWheelEventScrollPhase, value: Int64(scrollPhase.rawValue))
        event.setIntegerValueField(.scrollWheelEventMomentumPhase, value: momentum)
        if let timestamp { event.timestamp = CGEventTimestamp(timestamp * 1_000_000_000) }
        return try #require(NSEvent(cgEvent: event))
    }

    private final class FlippedView: NSView {
        override var isFlipped: Bool { true }
    }

    @MainActor
    private final class Fixture {
        let window: NSWindow
        let outer = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 480))
        let inner = FadingWorkScrollView(frame: NSRect(x: 50, y: 260, width: 400, height: 360))
        var outerScrollCount = 0

        init() {
            _ = NSApplication.shared
            window = NSWindow(contentRect: outer.frame, styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = outer
            outer.hasVerticalScroller = true
            outer.scrollerStyle = .overlay
            outer.verticalScrollElasticity = .none
            let conversation = FlippedView(frame: NSRect(x: 0, y: 0, width: 640, height: 2500))
            outer.documentView = conversation
            conversation.addSubview(inner)
            inner.documentView = FlippedView(frame: NSRect(x: 0, y: 0, width: 400, height: 1500))
            outer.contentView.scroll(to: NSPoint(x: 0, y: 200))
            outer.reflectScrolledClipView(outer.contentView)
            outer.onUserScroll = { [weak self] in self?.outerScrollCount += 1 }
            // This responder-routing test needs native geometry, not a shown
            // window or desktop input. Never take focus from the user's work.
        }

        var maximum: CGFloat { max(0, (inner.documentView?.bounds.height ?? 0) - inner.contentSize.height) }

        var visibleWorkHeight: CGFloat {
            inner.bounds.intersection(inner.convert(outer.contentView.documentVisibleRect,
                from: outer.documentView)).height
        }

        func position(_ y: CGFloat) {
            inner.contentView.scroll(to: NSPoint(x: 0, y: y))
            inner.reflectScrolledClipView(inner.contentView)
        }

        func settle() async {
            try? await Task.sleep(for: .milliseconds(30))
            #expect(!window.isVisible && !window.isKeyWindow)
            outer.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(30))
        }

        func close() { window.close() }
    }
}
