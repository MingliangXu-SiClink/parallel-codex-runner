import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct PhaseTimingsDisclosureTests {
    @Test
    func titleChevronGapAndPaddingShareOnePhaseTimingButton() async throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/PCRRemoteMac/ResultPhaseTimings.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        #expect(source.components(separatedBy: "Button {").count == 2)
        #expect(!source.contains("DisclosureGroup("))
        #expect(!source.contains("onTapGesture"))
        #expect(source.contains(".contentShape(Rectangle())"))
        #expect(source.contains(".accessibilityValue(isExpanded ?"))
        for language in [AppLanguage.chinese, .english] {
            for scheme in [ColorScheme.light, .dark] {
                for size in AppTextSize.allCases {
                    for width: CGFloat in [320, 440] {
                        let fixture = Fixture(language: language, scheme: scheme, size: size, width: width)
                        defer { fixture.close() }
                        try await fixture.settle()
                        let frame = try #require(fixture.probe.labelFrame)
                        #expect(frame.width >= width - 40)
                        #expect(frame.height >= 24 * size.scale - 1)
                        #expect(fixture.probe.buttonCount == 1)
                        let points = [
                            NSPoint(x: frame.minX + 6 * size.scale, y: frame.midY),
                            NSPoint(x: frame.minX + 16 * size.scale, y: frame.midY),
                            NSPoint(x: frame.minX + 30 * size.scale, y: frame.midY),
                            NSPoint(x: frame.maxX - 4, y: frame.midY),
                            NSPoint(x: frame.midX, y: frame.minY + 2),
                            NSPoint(x: frame.midX, y: frame.maxY - 2),
                        ]
                        #expect(points.allSatisfy(frame.contains))
                        try fixture.press()
                        try await fixture.settle()
                        #expect(fixture.state.expanded && fixture.scrolls().count == 1)
                    }
                }
            }
        }
    }

    @Test
    func theHeaderExpandsAndCollapsesTheRealTimingRows() async throws {
        for language in [AppLanguage.chinese, .english] {
            for scheme in [ColorScheme.light, .dark] {
                let fixture = Fixture(language: language, scheme: scheme)
                defer { fixture.close() }
                try await fixture.settle()
                #expect(!fixture.state.expanded && fixture.scrolls().isEmpty)
                let collapsed = try fixture.render(state: "collapsed")
                let header = try #require(fixture.probe.labelFrame)
                try fixture.press()
                try await fixture.settle()
                let scroll = try #require(fixture.scrolls().first)
                #expect(fixture.state.expanded && fixture.state.transitions == 1)
                #expect(scroll.frame.height > 30 && scroll.frame.height <= 161)
                #expect(fixture.probe.labelFrame?.height == header.height)
                #expect(try fixture.render(state: "expanded") != collapsed)
                try fixture.press()
                try await fixture.settle()
                #expect(!fixture.state.expanded && fixture.scrolls().isEmpty)
                #expect(fixture.state.transitions == 2)
            }
        }
    }

    @Test
    func liveMetricRefreshPreservesTheUsersExpandedAndCollapsedChoice() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        try await fixture.settle()
        for expanded in [true, false] {
            try fixture.press()
            try await fixture.settle()
            for index in 1...8 {
                fixture.state.workingSeconds = Double(index * 10)
                try await fixture.settle()
                #expect(fixture.state.expanded == expanded)
                #expect((fixture.scrolls().count == 1) == expanded)
            }
        }
        #expect(fixture.state.transitions == 2)
    }

    @MainActor
    private final class State: ObservableObject {
        @Published var expanded = false
        @Published var workingSeconds: Double = 20
        var transitions = 0
    }

    @MainActor
    private final class Probe {
        var trigger: (() -> Void)?
        var labelFrame: CGRect?
        var buttonCount = 0
    }

    private struct ProbeStyle: PrimitiveButtonStyle {
        let probe: Probe

        func makeBody(configuration: Configuration) -> some View {
            // Invoke SwiftUI's actual Button trigger in-process. Unordered
            // NSHostingView windows do not publish SwiftUI accessibility nodes.
            configuration.label
                .background(GeometryReader { geometry in
                    Color.clear.onAppear {
                        probe.labelFrame = geometry.frame(in: .named("phase-test"))
                    }
                })
                .onAppear {
                    probe.buttonCount += 1
                    probe.trigger = configuration.trigger
                }
        }
    }

    private struct Host: View {
        @ObservedObject var state: State
        let probe: Probe

        var body: some View {
            ResultPhaseTimings(isExpanded: Binding(get: { state.expanded }, set: {
                state.expanded = $0
                state.transitions += 1
            }), runTimings: ["baseline": 3],
                agentTimings: ["workspace_copy": 1, "history_preparation": 9,
                    "resuming": 4, "working": state.workingSeconds])
                .buttonStyle(ProbeStyle(probe: probe))
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(Color(nsColor: .textBackgroundColor))
                .coordinateSpace(name: "phase-test")
        }
    }

    @MainActor
    private final class Fixture {
        let window: NSWindow
        let host: NSView
        let state = State()
        let probe = Probe()
        let language: AppLanguage
        let scheme: ColorScheme

        init(language: AppLanguage = .english, scheme: ColorScheme = .dark,
             size: AppTextSize = .standard, width: CGFloat = 440) {
            _ = NSApplication.shared
            self.language = language
            self.scheme = scheme
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 260),
                styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            host = NSHostingView(rootView: Host(state: state, probe: probe)
                .environment(\.appLanguage, language).environment(\.appTextSize, size)
                .environment(\.colorScheme, scheme))
            window.contentView = host
        }

        func settle() async throws {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(80))
            host.layoutSubtreeIfNeeded()
            #expect(!window.isVisible && !window.isKeyWindow)
        }

        func press() throws {
            let trigger = try #require(probe.trigger)
            trigger()
        }

        func scrolls() -> [NSScrollView] {
            func views(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(views) }
            return views(host).compactMap { $0 as? NSScrollView }
        }

        func render(state: String) throws -> Data {
            let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: image)
            let bytes = try #require(image.bitmapData)
            #expect(Set(UnsafeBufferPointer(start: bytes, count: image.bytesPerRow * image.pixelsHigh)).count > 10)
            let png = try #require(image.representation(using: .png, properties: [:]))
            if let directory = ProcessInfo.processInfo.environment["PCR_PHASE_TIMINGS_EVIDENCE"] {
                let url = URL(fileURLWithPath: directory, isDirectory: true)
                try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                try png.write(to: url.appendingPathComponent("phase-timings-\(language)-\(scheme)-\(state).png"))
            }
            return png
        }

        func close() { window.close() }
    }
}
