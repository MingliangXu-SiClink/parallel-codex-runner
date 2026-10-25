import SwiftUI
import UniformTypeIdentifiers

struct QueuedMessageDrag: Codable, Equatable, Sendable {
    static let type = UTType(exportedAs: "com.parallelcodexrunner.queued-message", conformingTo: .data)
    let context: String
    let id: UUID

    var provider: NSItemProvider {
        let data = (try? JSONEncoder().encode(self)) ?? Data()
        return NSItemProvider(item: data as NSData, typeIdentifier: Self.type.identifier)
    }

    static func decode(_ data: Data, context: String, target: UUID) -> Self? {
        guard let value = try? JSONDecoder().decode(Self.self, from: data),
              value.context == context, !context.isEmpty, value.id != target else { return nil }
        return value
    }
}

struct QueuedMessageDrop: DropDelegate {
    let context: String
    let target: UUID
    let height: CGFloat
    let locked: Bool
    @Binding var insertAfter: Bool?
    let commit: (UUID, QueuedFollowUpChange) async -> Bool

    func validateDrop(info: DropInfo) -> Bool {
        !locked && !context.isEmpty && info.hasItemsConforming(to: [QueuedMessageDrag.type])
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        guard validateDrop(info: info) else {
            insertAfter = nil
            return DropProposal(operation: .forbidden)
        }
        insertAfter = info.location.y >= height / 2
        return DropProposal(operation: .move)
    }

    func dropEntered(info: DropInfo) {
        if validateDrop(info: info) { insertAfter = info.location.y >= height / 2 }
    }

    func dropExited(info: DropInfo) { insertAfter = nil }

    func performDrop(info: DropInfo) -> Bool {
        insertAfter = nil
        guard validateDrop(info: info),
              let provider = info.itemProviders(for: [QueuedMessageDrag.type]).first else { return false }
        return receive(provider, after: info.location.y >= height / 2)
    }

    // Hover is only an insertion preview. Decode and submit once, after a drop.
    func receive(_ provider: NSItemProvider, after: Bool) -> Bool {
        guard !locked, provider.hasItemConformingToTypeIdentifier(QueuedMessageDrag.type.identifier) else { return false }
        provider.loadDataRepresentation(forTypeIdentifier: QueuedMessageDrag.type.identifier) { data, _ in
            guard let data, let drag = QueuedMessageDrag.decode(data, context: context, target: target) else { return }
            Task { @MainActor in
                _ = await commit(drag.id, .place(relativeTo: target, after: after))
            }
        }
        return true
    }
}
