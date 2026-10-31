import SwiftUI
import UniformTypeIdentifiers

struct WorkspaceSidebarOrder: Codable, Equatable {
    static let defaultsKey = "parallel-codex.macos.workspace-order-v1"
    private var machines: [String: [String]] = [:]

    init(data: Data = Data()) {
        self = (try? JSONDecoder().decode(Self.self, from: data)) ?? Self(machines: [:])
    }

    private init(machines: [String: [String]]) { self.machines = machines }

    var data: Data { (try? JSONEncoder().encode(self)) ?? Data() }

    func orderedIDs(_ available: [String], machine: String) -> [String] {
        let present = Set(available)
        var seen = Set<String>()
        return ((machines[machine] ?? []) + available).filter {
            present.contains($0) && seen.insert($0).inserted
        }
    }

    mutating func move(_ source: String, relativeTo target: String, after: Bool,
                       available: [String], machine: String) {
        guard source != target, available.contains(source), available.contains(target) else { return }
        if machines[machine] == nil { machines[machine] = available }
        includeNewWorkspaces(available, machine: machine)
        var ids = machines[machine] ?? []
        guard let from = ids.firstIndex(of: source),
              let to = ids.firstIndex(of: target) else { return }
        ids.insert(ids.remove(at: from), at: to - (from < to ? 1 : 0) + (after ? 1 : 0))
        machines[machine] = ids
    }

    mutating func includeNewWorkspaces(_ available: [String], machine: String) {
        guard var ids = machines[machine] else { return }
        let known = Set(ids)
        ids += available.filter { !known.contains($0) }
        machines[machine] = ids
    }
}

struct WorkspaceSidebarDrag: Codable, Equatable, Sendable {
    static let type = UTType(exportedAs: "com.parallelcodexrunner.sidebar-workspace", conformingTo: .data)
    let machine: String
    let workspace: String

}

@MainActor
final class WorkspaceSidebarReorder: ObservableObject {
    struct Session {
        let source: String
        let original: [String]
        let midpoints: [CGFloat]
        let sourceIndex: Int
        var insertionIndex: Int
        var inside = true

        var displayedIDs: [String] {
            guard inside else { return original }
            var ids = original
            ids.remove(at: sourceIndex)
            ids.insert(source, at: insertionIndex)
            return ids
        }
    }

    @Published private(set) var session: Session?

    func begin(source: String, ids: [String], frames: [String: CGRect]) -> Bool {
        guard let index = ids.firstIndex(of: source), ids.allSatisfy({ frames[$0] != nil }) else { return false }
        session = Session(source: source, original: ids, midpoints: ids.map { frames[$0]!.midY },
                          sourceIndex: index, insertionIndex: index)
        return true
    }

    func update(y: CGFloat) {
        guard var next = session else { return }
        // Freeze the hit-test anchors: animated rows must not move their own drop boundaries.
        let boundary = next.midpoints.prefix { $0 < y }.count
        let index = boundary - (next.sourceIndex < boundary ? 1 : 0)
        guard index != next.insertionIndex || !next.inside else { return }
        next.insertionIndex = index
        next.inside = true
        session = next
    }

    func exit() {
        guard var next = session, next.inside else { return }
        next.inside = false
        session = next
    }

    func finish(_ commit: (String, String, Bool) -> Void) -> Bool {
        guard let current = session, current.inside else { return false }
        // Commit the visible gap, not a new interpretation of the mouse-up coordinates.
        let others = current.original.filter { $0 != current.source }
        if current.insertionIndex != current.sourceIndex, let last = others.last {
            let after = current.insertionIndex == others.count
            commit(current.source, after ? last : others[current.insertionIndex], after)
        }
        cancel()
        return true
    }

    func cancel() { session = nil }
}

struct WorkspaceSidebarFrames: PreferenceKey {
    static let defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

struct WorkspaceSidebarDrop: DropDelegate {
    let reorder: WorkspaceSidebarReorder
    let commit: (String, String, Bool) -> Void

    func validateDrop(info: DropInfo) -> Bool {
        reorder.session != nil && info.hasItemsConforming(to: [WorkspaceSidebarDrag.type])
    }
    func dropEntered(info: DropInfo) { reorder.update(y: info.location.y) }
    func dropUpdated(info: DropInfo) -> DropProposal? {
        reorder.update(y: info.location.y)
        return DropProposal(operation: .move)
    }
    func dropExited(info: DropInfo) { reorder.exit() }
    func performDrop(info: DropInfo) -> Bool { reorder.finish(commit) }
}

struct WorkspaceSidebarDragSource: NSViewRepresentable {
    let payload: WorkspaceSidebarDrag
    let begin: () -> Bool
    let end: () -> Void
    let click: () -> Void

    func makeNSView(context: Context) -> WorkspaceSidebarDragView { WorkspaceSidebarDragView() }
    func updateNSView(_ view: WorkspaceSidebarDragView, context: Context) {
        view.payload = payload
        view.begin = begin
        view.end = end
        view.click = click
    }
}

final class WorkspaceSidebarDragView: NSView, NSDraggingSource {
    var payload = WorkspaceSidebarDrag(machine: "", workspace: "")
    var begin: () -> Bool = { false }
    var end: () -> Void = {}
    var click: () -> Void = {}
    private var mousePress: NSEvent?

    override func mouseDown(with event: NSEvent) { mousePress = event }

    override func mouseUp(with event: NSEvent) {
        defer { mousePress = nil }
        if mousePress != nil, bounds.contains(convert(event.locationInWindow, from: nil)) { click() }
    }

    override func mouseDragged(with event: NSEvent) {
        guard let press = mousePress,
              hypot(event.locationInWindow.x - press.locationInWindow.x,
                    event.locationInWindow.y - press.locationInWindow.y) >= 4 else { return }
        mousePress = nil
        guard let image = rowImage(), begin() else { return }
        let writer = NSPasteboardItem()
        writer.setData((try? JSONEncoder().encode(payload)) ?? Data(),
                       forType: NSPasteboard.PasteboardType(WorkspaceSidebarDrag.type.identifier))
        let item = NSDraggingItem(pasteboardWriter: writer)
        item.setDraggingFrame(bounds, contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession,
                         sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .withinApplication ? .move : []
    }

    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        end()
    }

    private func rowImage() -> NSImage? {
        guard let root = window?.contentView else { return nil }
        let rect = convert(bounds, to: root)
        guard let bitmap = root.bitmapImageRepForCachingDisplay(in: rect) else { return nil }
        root.cacheDisplay(in: rect, to: bitmap)
        let image = NSImage(size: bounds.size)
        image.addRepresentation(bitmap)
        return image
    }
}
