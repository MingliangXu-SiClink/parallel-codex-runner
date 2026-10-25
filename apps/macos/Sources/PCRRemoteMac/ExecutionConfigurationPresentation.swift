import AppKit
import SwiftUI

// Native popovers may flip below their anchor and are bounded by the screen, not the app.
// The window-owned surface keeps configuration above the composer and inside the app.
struct ExecutionConfigurationAnchor<Content: View>: NSViewRepresentable {
    @Binding var isPresented: Bool
    let preferredWidth: CGFloat
    @ViewBuilder let content: (CGFloat) -> Content

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> AnchorView {
        let view = AnchorView()
        view.identifier = NSUserInterfaceItemIdentifier("composer.execution-anchor")
        view.changed = { [weak coordinator = context.coordinator] in coordinator?.scheduleLayout() }
        context.coordinator.anchor = view
        return view
    }

    func updateNSView(_ view: AnchorView, context: Context) {
        context.coordinator.update(self)
    }

    static func dismantleNSView(_ view: AnchorView, coordinator: Coordinator) { coordinator.remove() }

    static func placement(anchor: CGRect, bounds: CGRect, preferred: CGSize) -> CGRect {
        let area = bounds.insetBy(dx: 8, dy: 8)
        let bottom = max(area.minY, anchor.maxY + 6)
        let width = min(preferred.width, max(0, area.width))
        return CGRect(x: min(max(anchor.minX, area.minX), area.maxX - width),
                      y: bottom, width: width, height: min(preferred.height, max(0, area.maxY - bottom)))
    }

    final class AnchorView: NSView {
        var changed: (() -> Void)?
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); changed?() }
        override func layout() { super.layout(); changed?() }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }

    @MainActor final class Coordinator {
        weak var anchor: NSView?
        private var parent: ExecutionConfigurationAnchor?
        private var host: NSHostingView<AnyView>?
        private var surface: NSScrollView?
        private weak var observedWindow: NSWindow?
        private weak var previousResponder: NSResponder?
        private var observers: [NSObjectProtocol] = []
        private var eventMonitor: Any?
        private var layoutScheduled = false
        private var width: CGFloat = 0

        func update(_ parent: ExecutionConfigurationAnchor) {
            self.parent = parent
            guard parent.isPresented else { remove(); return }
            layout(refreshContent: true)
        }

        func scheduleLayout() {
            guard !layoutScheduled else { return }
            layoutScheduled = true
            DispatchQueue.main.async { [weak self] in
                self?.layoutScheduled = false
                self?.layout(refreshContent: false)
            }
        }

        private func layout(refreshContent: Bool) {
            guard let parent, parent.isPresented, let anchor, let window = anchor.window,
                  let container = window.contentView?.superview else { remove(); return }
            let anchorRect = anchor.convert(anchor.bounds, to: nil)
            let available = SelfAnchor.placement(anchor: anchorRect, bounds: window.contentLayoutRect,
                preferred: CGSize(width: parent.preferredWidth, height: .greatestFiniteMagnitude))
            guard available.width > 0, available.height >= 44 else { dismiss(); return }
            let changedWidth = width != available.width
            width = available.width
            if host == nil || refreshContent || changedWidth {
                let content = AnyView(parent.content(width).fixedSize(horizontal: false, vertical: true)
                    .background(GeometryReader { [weak self] geometry in
                        Color.clear.onChange(of: geometry.size) { _ in self?.scheduleLayout() }
                    }))
                if let host { host.rootView = content }
                else {
                    let host = NSHostingView(rootView: content)
                    self.host = host
                    let scroll = NSScrollView()
                    scroll.identifier = NSUserInterfaceItemIdentifier("configuration.surface")
                    scroll.drawsBackground = false
                    scroll.hasVerticalScroller = true
                    scroll.autohidesScrollers = true
                    scroll.scrollerStyle = .overlay
                    scroll.wantsLayer = true
                    scroll.layer?.cornerRadius = 8
                    scroll.layer?.masksToBounds = true
                    scroll.layer?.borderWidth = 1
                    scroll.layer?.borderColor = NSColor.separatorColor.cgColor
                    scroll.documentView = host
                    surface = scroll
                }
            }
            guard let host, let surface else { return }
            if observedWindow !== window {
                removeObservers()
                observedWindow = window
                previousResponder = window.firstResponder
                let center = NotificationCenter.default
                for name in [NSWindow.didResizeNotification, NSWindow.didEndLiveResizeNotification] {
                    observers.append(center.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                        MainActor.assumeIsolated { self?.scheduleLayout() }
                    })
                }
                for name in [NSWindow.willCloseNotification, NSWindow.didResignKeyNotification] {
                    observers.append(center.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                        MainActor.assumeIsolated { self?.dismiss() }
                    })
                }
                eventMonitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .keyDown]) { [weak self] event in
                    let consumed = MainActor.assumeIsolated { self?.handle(event) ?? false }
                    return consumed ? nil : event
                }
            }
            // SwiftUI owns NSHostingView's hit-testing; the native surface must be its sibling.
            if surface.superview !== container { container.addSubview(surface, positioned: .above, relativeTo: nil) }
            let size = CGSize(width: width, height: host.fittingSize.height)
            let placement = SelfAnchor.placement(anchor: anchorRect, bounds: window.contentLayoutRect,
                preferred: size)
            surface.frame = container.convert(placement, from: nil)
            host.setFrameSize(size)
        }

        private typealias SelfAnchor = ExecutionConfigurationAnchor<Content>

        private func handle(_ event: NSEvent) -> Bool {
            guard let window = observedWindow, let anchor, let surface, let container = window.contentView?.superview else { return false }
            if event.type == .keyDown {
                if event.window === window, event.keyCode == 53 {
                    dismiss(); return true
                }
            } else {
                let point = event.locationInWindow
                if event.window !== window || (!container.convert(surface.frame, to: nil).contains(point)
                    && !anchor.convert(anchor.bounds, to: nil).contains(point)) { dismiss() }
            }
            return false
        }

        private func dismiss() {
            let binding = parent?._isPresented
            remove()
            binding?.wrappedValue = false
        }

        func remove() {
            if let window = observedWindow, let surface, let responder = window.firstResponder as? NSView,
               window.isKeyWindow {
                var views: [NSView] = [surface]
                var editingSurface = responder.isDescendant(of: surface)
                while let view = views.popLast() {
                    if (view as? NSControl)?.currentEditor() === responder { editingSurface = true }
                    views.append(contentsOf: view.subviews)
                }
                if editingSurface { window.makeFirstResponder(previousResponder) }
            }
            surface?.removeFromSuperview()
            surface = nil
            host = nil
            width = 0
            removeObservers()
            observedWindow = nil
        }

        private func removeObservers() {
            for observer in observers { NotificationCenter.default.removeObserver(observer) }
            observers.removeAll()
            if let eventMonitor { NSEvent.removeMonitor(eventMonitor) }
            eventMonitor = nil
        }
    }
}
