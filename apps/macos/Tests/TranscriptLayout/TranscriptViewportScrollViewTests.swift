import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct TranscriptViewportScrollViewTests {
    @Test(arguments: [true, false])
    func geometryAndAnchorPublishAsOneNativeScrollerUpdate(following: Bool) throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let scroll = fixture.scroll
        let knob = try #require(scroll.verticalScroller)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: following ? 600 : 300))
        scroll.reflectScrolledClipView(scroll.contentView)
        var positions: [Double] = []
        var proportions: [Double] = []
        let positionObservation = knob.observe(\.doubleValue, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { positions.append(value) } }
        }
        let proportionObservation = knob.observe(\.knobProportion, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { proportions.append(value) } }
        }
        defer { positionObservation.invalidate(); proportionObservation.invalidate() }
        scroll.beginViewportUpdate()
        fixture.document.setFrameSize(NSSize(width: 400, height: 1600))
        scroll.contentView.scroll(to: NSPoint(x: 0, y: following ? 1200 : 900))
        scroll.reflectScrolledClipView(scroll.contentView)
        #expect(positions.isEmpty && proportions.isEmpty)
        scroll.endViewportUpdate()
        #expect(positions.count == 1 && proportions.count == 1)
        #expect(positions.allSatisfy { abs($0 - (following ? 1 : 0.75)) < 0.0001 })
        #expect(proportions.allSatisfy { abs($0 - 0.25) < 0.0001 })
        #expect(!fixture.window.isVisible && !fixture.window.isKeyWindow)
    }

    @Test
    func nestedViewportCommitsReflectOnlyAtTheOutermostBoundary() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let scroll = fixture.scroll
        let knob = try #require(scroll.verticalScroller)
        var positions: [Double] = []
        let observation = knob.observe(\.doubleValue, options: [.new]) { _, change in
            MainActor.assumeIsolated { if let value = change.newValue { positions.append(value) } }
        }
        defer { observation.invalidate() }
        scroll.beginViewportUpdate()
        scroll.beginViewportUpdate()
        fixture.document.setFrameSize(NSSize(width: 400, height: 1600))
        scroll.reflectScrolledClipView(scroll.contentView)
        scroll.endViewportUpdate()
        #expect(positions.isEmpty)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 1200))
        scroll.reflectScrolledClipView(scroll.contentView)
        scroll.endViewportUpdate()
        #expect(positions == [1])
    }

    @Test
    func normalNativeScrollingAndResizingStillReflectImmediately() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let scroll = fixture.scroll
        let knob = try #require(scroll.verticalScroller)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 300))
        scroll.reflectScrolledClipView(scroll.contentView)
        #expect(abs(knob.doubleValue - 0.5) < 0.0001)
        fixture.document.setFrameSize(NSSize(width: 400, height: 1600))
        #expect(abs(knob.knobProportion - 0.25) < 0.0001)
        #expect(abs(knob.doubleValue - 0.25) < 0.0001)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 900))
        scroll.reflectScrolledClipView(scroll.contentView)
        #expect(abs(knob.doubleValue - 0.75) < 0.0001)
    }

    @Test
    func aViewportScopeWithoutGeometryChangesDoesNotRepublishTheScroller() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let knob = try #require(fixture.scroll.verticalScroller)
        var changes = 0
        let observation = knob.observe(\.knobProportion, options: [.new]) { _, _ in
            MainActor.assumeIsolated { changes += 1 }
        }
        defer { observation.invalidate() }
        for _ in 0..<20 {
            fixture.scroll.beginViewportUpdate()
            fixture.scroll.endViewportUpdate()
        }
        #expect(changes == 0)
    }

    @MainActor
    private final class FlippedDocument: NSView {
        override var isFlipped: Bool { true }
    }

    @MainActor
    private final class Fixture {
        let scroll = TranscriptViewportScrollView(frame: NSRect(x: 0, y: 0, width: 400, height: 400))
        let document = FlippedDocument(frame: NSRect(x: 0, y: 0, width: 400, height: 1000))
        let window: NSWindow

        init() {
            _ = NSApplication.shared
            window = NSWindow(contentRect: scroll.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            scroll.hasVerticalScroller = true
            scroll.scrollerStyle = .overlay
            scroll.verticalScrollElasticity = .none
            window.contentView = scroll
            scroll.documentView = document
            scroll.layoutSubtreeIfNeeded()
            scroll.reflectScrolledClipView(scroll.contentView)
        }

        func close() { window.close() }
    }
}
