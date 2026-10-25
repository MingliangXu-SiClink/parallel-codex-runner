import AppKit
import SwiftUI

private struct SidebarVisibilityKey: FocusedValueKey {
    typealias Value = Binding<Bool>
}

extension FocusedValues {
    var sidebarVisibility: Binding<Bool>? {
        get { self[SidebarVisibilityKey.self] }
        set { self[SidebarVisibilityKey.self] = newValue }
    }
}

enum SidebarLayout {
    static let columnsAutosaveName = "ParallelCodex.SidebarColumns.v1"
    static let sectionsAutosaveName = "ParallelCodex.SidebarSections.v1"
    static let recentMinimumHeight: CGFloat = 80
    static let workspaceMinimumHeight: CGFloat = 100
}

struct SidebarSectionsSplit<Recent: View, Workspaces: View>: View {
    var autosaveName = SidebarLayout.sectionsAutosaveName
    @ViewBuilder let recent: Recent
    @ViewBuilder let workspaces: Workspaces

    var body: some View {
        VSplitView {
            recent
                .frame(minHeight: SidebarLayout.recentMinimumHeight,
                       idealHeight: 200, maxHeight: .infinity)
                // Let the workspace list absorb window-height changes first.
                .layoutPriority(-1)
                .background(SidebarSplitPersistence(name: autosaveName, isVertical: false))
            workspaces
                .frame(minHeight: SidebarLayout.workspaceMinimumHeight,
                       idealHeight: 300, maxHeight: .infinity)
        }
        .accessibilityIdentifier("sidebar.sections")
    }
}

// Keep SwiftUI's native drag/constraint delegate. Its provisional pane frames
// conflict with NSSplitView's full-frame autosave, so persist only the divider.
struct SidebarSplitPersistence: NSViewRepresentable {
    let name: String
    let isVertical: Bool
    var initialPosition: CGFloat? = nil
    var expanded: Bool? = nil

    func makeNSView(context: Context) -> SidebarSplitConfigurationView {
        SidebarSplitConfigurationView(name: name, isVertical: isVertical,
                                      initialPosition: initialPosition, expanded: expanded)
    }

    func updateNSView(_ view: SidebarSplitConfigurationView, context: Context) {
        view.configureSplitView(expanded: expanded)
    }
}

final class SidebarSplitConfigurationView: NSView {
    private let autosaveName: String
    private let isVertical: Bool
    private let initialPosition: CGFloat?
    private var expanded: Bool?
    private weak var configuredSplit: NSSplitView?
    private var eventMonitor: Any?
    private var dividerDragInProgress = false

    private var positionKey: String { autosaveName + ".position" }

    deinit { NotificationCenter.default.removeObserver(self) }

    init(name: String, isVertical: Bool, initialPosition: CGFloat? = nil, expanded: Bool? = nil) {
        autosaveName = name
        self.isVertical = isVertical
        self.initialPosition = initialPosition
        self.expanded = expanded
        super.init(frame: .zero)
        setAccessibilityElement(false)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let eventMonitor { NSEvent.removeMonitor(eventMonitor) }
        eventMonitor = nil
        dividerDragInProgress = false
        if window != nil {
            eventMonitor = NSEvent.addLocalMonitorForEvents(
                matching: [.leftMouseDown]
            ) { [weak self] event in
                self?.trackDividerEvent(event)
                return event
            }
        }
        configureSplitView()
    }

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        configureSplitView()
    }

    func configureSplitView(expanded: Bool? = nil) {
        let reopened = expanded == true && self.expanded == false
        if let expanded { self.expanded = expanded }
        guard let split = sequence(first: superview, next: { $0?.superview })
            .compactMap({ $0 as? NSSplitView })
            .first(where: { $0.isVertical == isVertical }) else { return }
        let style: NSSplitView.DividerStyle = isVertical ? .thin : .paneSplitter
        if split.dividerStyle != style { split.dividerStyle = style }
        guard configuredSplit !== split else {
            if reopened { restoreDividerPosition(split) }
            return
        }
        NotificationCenter.default.removeObserver(self)
        configuredSplit = split
        NotificationCenter.default.addObserver(
            self, selector: #selector(self.saveDividerPosition),
            name: NSSplitView.didResizeSubviewsNotification, object: split
        )
        restoreDividerPosition(split)
    }

    private func restoreDividerPosition(_ split: NSSplitView) {
        DispatchQueue.main.async { [weak self, weak split] in
            guard let self, let split, self.configuredSplit === split else { return }
            split.window?.contentView?.layoutSubtreeIfNeeded()
            let saved = UserDefaults.standard.double(forKey: self.positionKey)
            let initial = self.initialPosition
                ?? (self.isVertical ? ParallelCodexVisualStyle.Layout.sidebarIdeal : 200)
            let position = saved.isFinite && saved > 0 ? CGFloat(saved) : initial
            split.setPosition(position, ofDividerAt: 0)
            split.layoutSubtreeIfNeeded()
        }
    }

    func trackDividerEvent(_ event: NSEvent) {
        guard let split = configuredSplit, event.window === split.window,
              let first = split.arrangedSubviews.first else { return }
        switch event.type {
        case .leftMouseDown:
            let point = split.convert(event.locationInWindow, from: nil)
            let divider = isVertical
                ? NSRect(x: first.frame.maxX, y: 0,
                         width: split.dividerThickness, height: split.bounds.height)
                : NSRect(x: 0, y: split.isFlipped ? first.frame.maxY : first.frame.minY - split.dividerThickness,
                         width: split.bounds.width, height: split.dividerThickness)
            dividerDragInProgress = divider.insetBy(dx: -3, dy: -3).contains(point)
            guard dividerDragInProgress else { return }
            // NSSplitView consumes mouse-up in its tracking run loop. Finish in
            // default mode after that loop, without persisting teardown layout.
            RunLoop.main.perform(inModes: [.default]) { [weak self] in
                MainActor.assumeIsolated {
                    self?.dividerDragInProgress = false
                }
            }
        default:
            break
        }
    }

    @objc private func saveDividerPosition(_ notification: Notification) {
        guard let split = notification.object as? NSSplitView,
              split === configuredSplit,
              split.arrangedSubviews.count == 2,
              expanded != false,
              dividerDragInProgress else { return }
        let size = split.arrangedSubviews[0].frame.size
        let position = isVertical ? size.width : size.height
        guard position.isFinite, position > 0 else { return }
        // Automatic search/window layout must not replace the user's drag choice.
        UserDefaults.standard.set(Double(position), forKey: positionKey)
    }
}
