import AppKit

@MainActor
final class NativeScrollMotion {
    private var timer: Timer?
    private weak var scrollView: NSScrollView?
    private var origin = NSPoint.zero
    private var destination = NSPoint.zero
    private var started: TimeInterval = 0
    private let duration: TimeInterval = 0.16
    private let time: () -> TimeInterval
    private let allowsAnimation: (NSScrollView) -> Bool
    var onCompletion: (() -> Void)?
    var isAnimating: Bool { timer != nil }

    init(time: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
         allowsAnimation: @escaping (NSScrollView) -> Bool = {
             $0.window?.isVisible == true && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
         }) {
        self.time = time
        self.allowsAnimation = allowsAnimation
    }

    func move(to point: NSPoint, in scroll: NSScrollView, animated: Bool) {
        cancel()
        scrollView = scroll
        origin = scroll.contentView.bounds.origin
        destination = point
        guard animated, allowsAnimation(scroll),
              hypot(point.x - origin.x, point.y - origin.y) > 1 else {
            apply(point)
            onCompletion?()
            return
        }
        started = time()
        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            let timerID = ObjectIdentifier(timer)
            MainActor.assumeIsolated {
                guard self.timer.map(ObjectIdentifier.init) == timerID else { return }
                self.step()
            }
        }
        self.timer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func cancel() {
        timer?.invalidate()
        timer = nil
    }

    private func step() {
        guard scrollView?.window != nil else { cancel(); return }
        let progress = min(1, (time() - started) / duration)
        let fraction = 1 - pow(1 - progress, 3)
        apply(NSPoint(x: origin.x + (destination.x - origin.x) * fraction,
                      y: origin.y + (destination.y - origin.y) * fraction))
        if progress >= 1 {
            cancel()
            onCompletion?()
        }
    }

    private func apply(_ point: NSPoint) {
        guard let scrollView else { return }
        scrollView.contentView.scroll(to: point)
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }
}
