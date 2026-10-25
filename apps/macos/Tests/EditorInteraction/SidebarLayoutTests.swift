import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct SidebarLayoutTests {
    @Test(arguments: [false, true])
    func windowResizeKeepsTheUsersSidebarDimensions(isVertical: Bool) async throws {
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        defer { removeSavedFrames(name) }
        let window = await makeWindow(name: name, isVertical: isVertical)
        defer { window.close() }
        let split = try #require(findSplit(in: window.contentView!, isVertical: isVertical))
        let expected: CGFloat = isVertical ? 228 : 194
        try await dragDivider(split, to: expected)

        for delta: CGFloat in [200, 80, 0] {
            window.setContentSize(NSSize(
                width: isVertical ? 1040 + delta : 280,
                height: isVertical ? 600 : 600 + delta
            ))
            window.contentView?.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(40))
            window.contentView?.layoutSubtreeIfNeeded()
            let size = split.arrangedSubviews[0].frame.size
            #expect(abs((isVertical ? size.width : size.height) - expected) < 1)
            #expect(UserDefaults.standard.double(forKey: name + ".position") == Double(expected))
        }

        try await dragDivider(split, to: expected + 40)
        let dragged = split.arrangedSubviews[0].frame.size
        #expect(abs((isVertical ? dragged.width : dragged.height) - (expected + 40)) < 1)
    }

    @Test
    func dividerCanGiveEitherListMostOfTheHeight() async throws {
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        defer { removeSavedFrames(name) }
        let window = await makeWindow(name: name)
        defer { window.close() }
        let split = try #require(findSplit(in: window.contentView!))
        #expect(split.arrangedSubviews.count == 2)
        #expect(split.dividerStyle == .paneSplitter)

        try await dragDivider(split, to: 90)
        split.layoutSubtreeIfNeeded()
        #expect(split.arrangedSubviews[0].frame.height < 120)
        #expect(split.arrangedSubviews[1].frame.height > 450)
        #expect(UserDefaults.standard.double(forKey: name + ".position") == 90)

        try await dragDivider(split, to: 480)
        split.layoutSubtreeIfNeeded()
        #expect(split.arrangedSubviews[0].frame.height > 450)
        #expect(split.arrangedSubviews[1].frame.height >= SidebarLayout.workspaceMinimumHeight)
        #expect(split.arrangedSubviews[1].frame.height < 150)
    }

    @Test
    func nativeTrackingLoopReleaseDoesNotSaveLaterAutomaticLayout() async throws {
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        defer { removeSavedFrames(name) }
        let window = await makeWindow(name: name)
        defer { window.close() }
        let split = try #require(findSplit(in: window.contentView!))
        try await dragDivider(split, to: 194)
        #expect(UserDefaults.standard.double(forKey: name + ".position") == 194)
        // NSSplitView may consume mouse-up inside its native tracking loop.
        // Returning to default mode completes the drag without monitored mouse-up.
        try await Task.sleep(for: .milliseconds(50))
        split.setPosition(184.5, ofDividerAt: 0)
        split.layoutSubtreeIfNeeded()
        #expect(UserDefaults.standard.double(forKey: name + ".position") == 194)
    }

    @Test
    func contentRefreshDoesNotResetTheUsersDivider() async throws {
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        defer { removeSavedFrames(name) }
        let window = await makeWindow(name: name)
        defer { window.close() }
        let host = try #require(window.contentView as? NSHostingView<SidebarFixture>)
        let split = try #require(findSplit(in: host))
        try await dragDivider(split, to: 160)
        host.layoutSubtreeIfNeeded()
        let height = split.arrangedSubviews[0].frame.height

        host.rootView = SidebarFixture(name: name, rows: 100)
        host.layoutSubtreeIfNeeded()
        #expect(abs(split.arrangedSubviews[0].frame.height - height) < 1)
        #expect(split.arrangedSubviews[1].frame.height >= SidebarLayout.workspaceMinimumHeight)
    }

    @Test
    func nativeDividerPositionSurvivesReopening() async throws {
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        defer { removeSavedFrames(name) }
        let first = await makeWindow(name: name)
        let split = try #require(findSplit(in: first.contentView!))
        try await dragDivider(split, to: 180)
        first.contentView?.layoutSubtreeIfNeeded()
        await Task.yield()
        first.contentView?.layoutSubtreeIfNeeded()
        let expected = split.arrangedSubviews[0].frame.height
        #expect(abs(expected - 180) < 1)
        first.close()
        await Task.yield()

        let second = await makeWindow(name: name)
        defer { second.close() }
        let restored = try #require(findSplit(in: second.contentView!))
        second.contentView?.layoutSubtreeIfNeeded()
        #expect(abs(restored.arrangedSubviews[0].frame.height - expected) < 1)
    }

    private func makeWindow(name: String, isVertical: Bool = false) async -> NSWindow {
        _ = NSApplication.shared
        let size = NSSize(width: isVertical ? 1040 : 280, height: 600)
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let host = NSHostingView(rootView: SidebarFixture(name: name, rows: 4, isVertical: isVertical))
        window.contentView = host
        host.frame.size = size
        host.layoutSubtreeIfNeeded()
        await Task.yield()
        host.layoutSubtreeIfNeeded()
        return window
    }

    private func findSplit(in view: NSView, isVertical: Bool = false) -> NSSplitView? {
        if let split = view as? NSSplitView, split.isVertical == isVertical { return split }
        return view.subviews.lazy.compactMap { findSplit(in: $0, isVertical: isVertical) }.first
    }

    private func dragDivider(_ split: NSSplitView, to position: CGFloat) async throws {
        func configuration(in view: NSView) -> SidebarSplitConfigurationView? {
            if let value = view as? SidebarSplitConfigurationView { return value }
            return view.subviews.lazy.compactMap { configuration(in: $0) }.first
        }
        let tracker = try #require(configuration(in: split))
        let first = try #require(split.arrangedSubviews.first)
        let y = split.isFlipped ? first.frame.maxY : first.frame.minY - split.dividerThickness
        let dividerPoint = split.isVertical
            ? NSPoint(x: first.frame.maxX + split.dividerThickness / 2, y: split.bounds.midY)
            : NSPoint(x: split.bounds.midX, y: y + split.dividerThickness / 2)
        let point = split.convert(dividerPoint, to: nil)
        let event = try #require(NSEvent.mouseEvent(
            with: .leftMouseDown, location: point, modifierFlags: [], timestamp: 0,
            windowNumber: split.window?.windowNumber ?? 0, context: nil,
            eventNumber: 0, clickCount: 1, pressure: 1
        ))
        tracker.trackDividerEvent(event)
        split.setPosition(position, ofDividerAt: 0)
        await withCheckedContinuation { continuation in
            RunLoop.main.perform(inModes: [.default]) { continuation.resume() }
        }
    }

    private func removeSavedFrames(_ name: String) {
        for key in UserDefaults.standard.dictionaryRepresentation().keys where key.contains(name) {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }
}

private struct SidebarFixture: View {
    let name: String
    let rows: Int
    var isVertical = false

    @ViewBuilder var body: some View {
        if isVertical {
            HSplitView {
                Text("Sidebar")
                    .frame(minWidth: 220, idealWidth: 280, maxWidth: 360, maxHeight: .infinity)
                    .layoutPriority(-1)
                    .background(SidebarSplitPersistence(name: name, isVertical: true))
                Text("Conversation")
                    .frame(minWidth: 540, maxWidth: .infinity, maxHeight: .infinity)
            }
        } else {
            sections
        }
    }

    private var sections: some View {
        SidebarSectionsSplit(autosaveName: name) {
            ScrollView {
                LazyVStack {
                    ForEach(0..<rows, id: \.self) { Text("Conversation \($0)") }
                }
            }
        } workspaces: {
            ScrollView {
                LazyVStack {
                    ForEach(0..<20) { Text("Workspace \($0)") }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
