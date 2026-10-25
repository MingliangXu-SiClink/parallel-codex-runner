import AppKit
import SwiftUI

enum ConversationWindowChrome {
    static let headerHeight: CGFloat = 52

    @MainActor
    static func configure(_ window: NSWindow, appearance: NSAppearance? = nil) {
        if window.titleVisibility != .hidden {
            window.titleVisibility = .hidden
        }
        // Keep the semantic window title and native controls. The visible title
        // belongs to the conversation pane, not the sidebar's toolbar space.
        if !window.titlebarAppearsTransparent {
            window.titlebarAppearsTransparent = true
        }
        if window.titlebarSeparatorStyle != .none {
            window.titlebarSeparatorStyle = .none
        }
        // Inspector transitions can draw outside the hosting view's appearance
        // context. Resolve the window backing once, not during that draw pass.
        var background = NSColor.windowBackgroundColor
        (appearance ?? window.effectiveAppearance).performAsCurrentDrawingAppearance {
            background = ParallelCodexVisualStyle.canvasNSColor.usingColorSpace(.deviceRGB)
                ?? NSColor.windowBackgroundColor
        }
        if window.backgroundColor != background {
            window.backgroundColor = background
        }
    }
}

@MainActor
final class ConversationWindowPresentation: NSObject, ObservableObject {
    @Published private(set) var isFullScreen = false
    private weak var window: NSWindow?

    func attach(to window: NSWindow?) {
        guard self.window !== window else { return }
        NotificationCenter.default.removeObserver(self)
        self.window = window
        updateFullScreenState()
        guard let window else { return }
        for name in [NSWindow.didEnterFullScreenNotification, NSWindow.didExitFullScreenNotification] {
            NotificationCenter.default.addObserver(
                self, selector: #selector(updateFullScreenState), name: name, object: window
            )
        }
    }

    @objc private func updateFullScreenState() {
        let value = window?.styleMask.contains(.fullScreen) == true
        if value != isFullScreen { isFullScreen = value }
    }
}

struct ConversationSidebarControl: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let isFullScreen: Bool
    let title: String
    let toggle: () -> Void

    static func leadingInset(isFullScreen: Bool) -> CGFloat {
        isFullScreen ? 18 : 88
    }

    var body: some View {
        HStack(spacing: 0) {
            Button(action: toggle) {
                Image(systemName: "sidebar.left")
                    .appScaledSystemFont(size: 17)
            }
            .buttonStyle(ParallelCodexIconButtonStyle(size: 30))
            .help(title)
            .accessibilityLabel(title)
            .accessibilityIdentifier("window.sidebar-toggle")
            Spacer(minLength: 0)
        }
        .padding(.leading, Self.leadingInset(isFullScreen: isFullScreen))
        .frame(height: ConversationWindowChrome.headerHeight)
        .background(ConversationWindowDragRegion())
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: isFullScreen)
    }
}

struct ConversationWindowDragRegion: NSViewRepresentable {
    func makeNSView(context: Context) -> DragView { DragView() }
    func updateNSView(_ view: DragView, context: Context) {}

    final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }

        override func mouseDown(with event: NSEvent) {
            guard let window, !window.styleMask.contains(.fullScreen) else { return }
            if event.clickCount == 2 {
                switch UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") {
                case "Minimize": window.performMiniaturize(nil)
                case "None": break
                default: window.performZoom(nil)
                }
            } else {
                window.performDrag(with: event)
            }
        }
    }
}

extension ToolbarContent {
    @ToolbarContentBuilder
    func plainWindowToolbarItem() -> some ToolbarContent {
        if #available(macOS 26.0, *) {
            self.sharedBackgroundVisibility(.hidden)
        } else {
            self
        }
    }
}
