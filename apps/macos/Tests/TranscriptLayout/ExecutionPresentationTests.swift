import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ExecutionPresentationTests {
    @Test func placementIsAlwaysAboveAndInsideTheWindowRatherThanTheScreen() {
        for bounds in [CGRect(x: 0, y: 0, width: 920, height: 640), CGRect(x: 50, y: 30, width: 350, height: 420)] {
            for x in [bounds.minX, bounds.midX, bounds.maxX - 100] {
                let anchor = CGRect(x: x, y: bounds.minY + 160, width: 200, height: 28)
                for size in [CGSize(width: 392, height: 140), CGSize(width: 588, height: 900)] {
                    let frame = ExecutionConfigurationAnchor<EmptyView>.placement(anchor: anchor, bounds: bounds, preferred: size)
                    #expect(bounds.contains(frame))
                    #expect(frame.minY >= anchor.maxY + 6)
                    #expect(frame.maxY <= bounds.maxY - 8)
                    #expect(frame.width <= size.width && frame.height <= size.height)
                }
            }
        }
    }

    @Test func oneNativeSurfaceGrowsUpwardClipsLongEditorsAndSurvivesResizingWithoutAWindow() async throws {
        _ = NSApplication.shared
        let state = SurfaceState()
        let host = NSHostingView(rootView: SurfaceFixture(state: state))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 920, height: 640),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(60))
        let container = try #require(host.superview)
        let surface = try #require(container.subviews.compactMap { $0 as? NSScrollView }.first)
        let hit = try #require(container.hitTest(NSPoint(x: surface.frame.midX, y: surface.frame.midY)))
        #expect(hit === surface || hit.isDescendant(of: surface), "The native surface must receive clicks over the transcript.")
        let original = surface.frame
        let originalInWindow = container.convert(original, to: nil)
        #expect(original.width == 392 && original.height == 140)
        state.content.height = 1000
        try await Task.sleep(for: .milliseconds(80))
        host.layoutSubtreeIfNeeded()
        #expect(container.convert(surface.frame, to: nil).minY == originalInWindow.minY,
                "Growing an editor must keep its bottom above the summary.")
        #expect(surface.frame.height > original.height)
        #expect(container.bounds.contains(surface.frame))
        #expect(try #require(surface.documentView).frame.height == 1000)
        #expect(surface.contentView.bounds.height < 1000, "Long option lists must scroll inside the window.")
        for size in [CGSize(width: 350, height: 420), CGSize(width: 1040, height: 680)] {
            window.setContentSize(size)
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(80))
            #expect(container.bounds.contains(surface.frame))
            #expect(surface.frame.width <= min(392, size.width - 16))
            #expect(container.subviews.compactMap { $0 as? NSScrollView }.count == 1)
        }
        state.presented = false
        try await Task.sleep(for: .milliseconds(40))
        #expect(surface.superview == nil)
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    private final class SurfaceState: ObservableObject {
        @Published var presented = true
        let content = EditorState()
    }

    private final class EditorState: ObservableObject {
        @Published var height: CGFloat = 140
    }

    private struct EditorContent: View {
        @ObservedObject var state: EditorState
        let width: CGFloat
        var body: some View { Color.gray.frame(width: width, height: state.height) }
    }

    private struct SurfaceFixture: View {
        @ObservedObject var state: SurfaceState
        var body: some View {
            VStack(alignment: .leading) {
                Spacer()
                Text("Summary").frame(width: 200, height: 28)
                    .background(ExecutionConfigurationAnchor(isPresented: $state.presented, preferredWidth: 392) { width in
                        EditorContent(state: state.content, width: width)
                    })
                Color.clear.frame(height: 160)
            }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
    }
}
