import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct SidebarLayoutTests {
    @Test
    func fullscreenStateTracksOnlyItsAttachedWindow() {
        _ = NSApplication.shared
        let first = FullscreenTestWindow(contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        let second = FullscreenTestWindow(contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        first.isReleasedWhenClosed = false
        second.isReleasedWhenClosed = false
        defer { first.close(); second.close() }
        let presentation = ConversationWindowPresentation()
        presentation.attach(to: first)
        #expect(!presentation.isFullScreen)

        second.simulatedFullScreen = true
        NotificationCenter.default.post(name: NSWindow.didEnterFullScreenNotification, object: second)
        #expect(!presentation.isFullScreen)
        first.simulatedFullScreen = true
        NotificationCenter.default.post(name: NSWindow.didEnterFullScreenNotification, object: first)
        #expect(presentation.isFullScreen)

        for _ in 0..<20 { presentation.attach(to: first) }
        first.simulatedFullScreen = false
        NotificationCenter.default.post(name: NSWindow.didExitFullScreenNotification, object: first)
        #expect(!presentation.isFullScreen)
        presentation.attach(to: second)
        #expect(presentation.isFullScreen)
        NotificationCenter.default.post(name: NSWindow.didExitFullScreenNotification, object: first)
        #expect(presentation.isFullScreen)
        presentation.attach(to: nil)
        #expect(!presentation.isFullScreen)
        NotificationCenter.default.post(name: NSWindow.didEnterFullScreenNotification, object: second)
        #expect(!presentation.isFullScreen)
    }

    @Test(arguments: [false, true])
    func sidebarControlHasStableHeightWithoutANativeToolbar(fullScreen: Bool) {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 260, height: 600),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        ConversationWindowChrome.configure(window)
        let host = NSHostingView(rootView: ConversationSidebarControl(
            isFullScreen: fullScreen, title: "Toggle Sidebar", toggle: {}
        ).frame(width: 260).ignoresSafeArea(.container, edges: .top))
        #expect(abs(host.intrinsicContentSize.height - ConversationWindowChrome.headerHeight) < 1)
        window.contentView = host
        host.layoutSubtreeIfNeeded()

        #expect(window.toolbar == nil,
                "The content header must not create a second toolbar surface in fullscreen.")
        #expect(window.standardWindowButton(.closeButton) != nil)
        #expect(window.standardWindowButton(.zoomButton) != nil)
        if !fullScreen, let button = window.standardWindowButton(.zoomButton) {
            let nativeRight = button.convert(button.bounds, to: nil).maxX
            #expect(ConversationSidebarControl.leadingInset(isFullScreen: false) > nativeRight)
        }
    }

    @Test
    func conversationRefreshDoesNotReconfigureTheTitlebar() {
        _ = NSApplication.shared
        let window = ChromeRecordingWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        ConversationWindowChrome.configure(window)
        let changes = window.chromeChanges

        for _ in 0..<20 {
            ConversationWindowChrome.configure(window)
        }

        #expect(window.chromeChanges == changes,
                "Run polling and opening a file must not repeatedly invalidate the native toolbar.")
    }

    @Test(arguments: [NSAppearance.Name.aqua, .darkAqua])
    func windowBackgroundDoesNotFollowAnotherWindowsAppearance(appearanceName: NSAppearance.Name) throws {
        _ = NSApplication.shared
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.appearance = NSAppearance(named: appearanceName)
        ConversationWindowChrome.configure(window)

        for drawingAppearanceName in [NSAppearance.Name.aqua, .darkAqua] {
            let drawingAppearance = try #require(NSAppearance(named: drawingAppearanceName))
            var background: NSColor?
            drawingAppearance.performAsCurrentDrawingAppearance {
                background = window.backgroundColor.usingColorSpace(.deviceRGB)
            }
            let color = try #require(background)
            let level: CGFloat = appearanceName == .darkAqua ? 33.0 / 255 : 1
            let expected = try #require(NSColor(
                srgbRed: level, green: level, blue: level, alpha: 1
            ).usingColorSpace(.deviceRGB))
            #expect(abs(color.redComponent - expected.redComponent) < 0.001)
            #expect(abs(color.greenComponent - expected.greenComponent) < 0.001)
            #expect(abs(color.blueComponent - expected.blueComponent) < 0.001)
        }
    }

    @Test
    func appAppearanceChangesUpdateOnlyTheWindowBacking() throws {
        _ = NSApplication.shared
        let window = ChromeRecordingWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.appearance = NSAppearance(named: .aqua)
        ConversationWindowChrome.configure(window)
        let light = window.backgroundColor
        let changes = window.chromeChanges

        let darkAppearance = try #require(NSAppearance(named: .darkAqua))
        ConversationWindowChrome.configure(window, appearance: darkAppearance)
        #expect(window.backgroundColor != light)
        #expect(window.chromeChanges == changes + 1)
        ConversationWindowChrome.configure(window, appearance: darkAppearance)
        #expect(window.chromeChanges == changes + 1)

        ConversationWindowChrome.configure(window)
        #expect(window.backgroundColor == light)
        #expect(window.chromeChanges == changes + 2)
    }

    @Test(arguments: [false, true])
    func integratedTitlebarPreservesNativeWindowBehavior(fullSizeContent: Bool) {
        _ = NSApplication.shared
        var style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
        if fullSizeContent { style.insert(.fullSizeContentView) }
        let window = NSWindow(
            contentRect: NSRect(x: 120, y: 100, width: 1040, height: 680),
            styleMask: style,
            backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.title = "A conversation"
        window.titleVisibility = .visible
        window.toolbar = NSToolbar(identifier: "SidebarLayoutTests.toolbar")
        window.toolbarStyle = .unified
        window.preservesContentDuringLiveResize = true
        let frame = window.frame
        let contentView = window.contentView

        ConversationWindowChrome.configure(window)
        ConversationWindowChrome.configure(window)

        #expect(window.styleMask == style)
        #expect(window.styleMask.contains(.resizable))
        #expect(window.titlebarAppearsTransparent)
        #expect(window.titlebarSeparatorStyle == .none)
        #expect(window.toolbarStyle == .unified)
        #expect(window.frame == frame)
        #expect(window.contentView === contentView)
        #expect(!window.isVisible)
        #expect(window.title == "A conversation")
        #expect(window.titleVisibility == .hidden)
        #expect(window.preservesContentDuringLiveResize)
        #expect(window.standardWindowButton(.closeButton) != nil)
        #expect(window.standardWindowButton(.miniaturizeButton) != nil)
        #expect(window.standardWindowButton(.zoomButton) != nil)
    }

    @Test
    func temporaryHeaderHeightDoesNotOverwriteTheSavedDivider() async throws {
        _ = NSApplication.shared
        let name = "SidebarLayoutTests.\(UUID().uuidString)"
        let key = name + ".position"
        UserDefaults.standard.set(194.0, forKey: key)
        defer { UserDefaults.standard.removeObject(forKey: key) }
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 280, height: 600),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        func content(headerHeight: CGFloat) -> some View {
            VStack(spacing: 0) {
                Color.clear.frame(height: headerHeight)
                SidebarSectionsSplit(autosaveName: name) {
                    Color.clear
                } workspaces: {
                    Color.clear
                }
            }
        }
        let host = NSHostingView(rootView: content(headerHeight: 40))
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        host.layoutSubtreeIfNeeded()
        #expect(UserDefaults.standard.double(forKey: key) == 194)

        host.rootView = content(headerHeight: 78)
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        host.layoutSubtreeIfNeeded()
        #expect(UserDefaults.standard.double(forKey: key) == 194,
                "Opening search must not replace a user drag preference with a temporary frame.")
        host.rootView = content(headerHeight: 40)
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        #expect(UserDefaults.standard.double(forKey: key) == 194)
    }
}

@MainActor
private final class FullscreenTestWindow: NSWindow {
    var simulatedFullScreen = false
    override var styleMask: NSWindow.StyleMask {
        get { simulatedFullScreen ? super.styleMask.union(.fullScreen) : super.styleMask }
        set { super.styleMask = newValue }
    }
}

@MainActor
private final class ChromeRecordingWindow: NSWindow {
    var chromeChanges = 0

    override var titleVisibility: NSWindow.TitleVisibility {
        didSet { chromeChanges += 1 }
    }

    override var titlebarAppearsTransparent: Bool {
        didSet { chromeChanges += 1 }
    }

    override var titlebarSeparatorStyle: NSTitlebarSeparatorStyle {
        didSet { chromeChanges += 1 }
    }

    override var backgroundColor: NSColor! {
        didSet { chromeChanges += 1 }
    }
}
