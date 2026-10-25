import AppKit
import SwiftUI

struct InspectorScrollEdges: Equatable {
    var top = false
    var bottom = false

    init() {}

    init(content: CGRect, viewportHeight: CGFloat) {
        guard viewportHeight > 0, content.height > viewportHeight + 0.5 else { return }
        top = content.minY < -0.5
        bottom = content.maxY > viewportHeight + 0.5
    }
}

struct InspectorScrollView<Content: View>: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.appTextSize) private var textSize
    @State private var edges = InspectorScrollEdges()
    @ViewBuilder let content: Content

    var body: some View {
        GeometryReader { viewport in
            ScrollView(.vertical) {
                content
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(InspectorScrollObservation(edges: $edges))
            }
            .mask {
                let fade = min(18 * textSize.scale, max(0, viewport.size.height / 4))
                VStack(spacing: 0) {
                    LinearGradient(colors: [edges.top ? .clear : .black, .black],
                                   startPoint: .top, endPoint: .bottom)
                        .frame(height: fade)
                    Color.black
                    LinearGradient(colors: [.black, edges.bottom ? .clear : .black],
                                   startPoint: .top, endPoint: .bottom)
                        .frame(height: fade)
                }
                .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: edges)
            }
        }
    }
}

private struct InspectorScrollObservation: NSViewRepresentable {
    @Binding var edges: InspectorScrollEdges

    func makeNSView(context: Context) -> InspectorScrollObservationView {
        InspectorScrollObservationView { edges = $0 }
    }

    func updateNSView(_ view: InspectorScrollObservationView, context: Context) {
        view.onChange = { edges = $0 }
        view.attach()
    }
}

// macOS hosts ScrollView content separately, so SwiftUI geometry preferences do
// not reliably cross the native scroll container. Observe its actual clip view.
final class InspectorScrollObservationView: NSView {
    var onChange: (InspectorScrollEdges) -> Void
    private(set) var edges = InspectorScrollEdges()
    private weak var scrollView: NSScrollView?
    private weak var documentView: NSView?

    init(onChange: @escaping (InspectorScrollEdges) -> Void) {
        self.onChange = onChange
        super.init(frame: .zero)
        setAccessibilityElement(false)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    deinit { NotificationCenter.default.removeObserver(self) }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        attach()
    }

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        attach()
    }

    func attach() {
        guard let scroll = enclosingScrollView, let document = scroll.documentView else { return }
        if scrollView !== scroll || documentView !== document {
            NotificationCenter.default.removeObserver(self)
            scrollView = scroll
            documentView = document
            scroll.contentView.postsBoundsChangedNotifications = true
            document.postsFrameChangedNotifications = true
            NotificationCenter.default.addObserver(self, selector: #selector(updateEdges),
                name: NSView.boundsDidChangeNotification, object: scroll.contentView)
            NotificationCenter.default.addObserver(self, selector: #selector(updateEdges),
                name: NSView.frameDidChangeNotification, object: document)
        }
        updateEdges()
    }

    @objc private func updateEdges() {
        guard let scrollView, let documentView else { return }
        let visible = scrollView.documentVisibleRect
        let offset = documentView.isFlipped
            ? visible.minY : documentView.bounds.height - visible.maxY
        let next = InspectorScrollEdges(
            content: CGRect(x: 0, y: -offset, width: visible.width, height: documentView.bounds.height),
            viewportHeight: visible.height
        )
        guard edges != next else { return }
        edges = next
        DispatchQueue.main.async { [weak self] in
            guard let self, self.edges == next else { return }
            self.onChange(next)
        }
    }
}
