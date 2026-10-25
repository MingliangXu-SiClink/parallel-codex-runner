import Combine
import Foundation

struct DeletedConversation: Codable, Equatable, Identifiable, Sendable {
    let id: UUID
    let machineID: String
    let workspaceID: String
    let machineName: String
    let workspaceName: String
    let title: String
    let deletedAt: Date
    var aliases: Set<String>

    func matches(_ conversation: WorkspaceConversationSummary, machineID: String, workspaceID: String) -> Bool {
        self.machineID == MachineContextKey.normalizeMachineID(machineID)
            && self.workspaceID == workspaceID
            && !aliases.isDisjoint(with: conversationDeletionAliases(conversation))
    }
}

struct ConversationDeleteRequest: Identifiable {
    let id = UUID()
    let machineID: String
    let workspaceID: String
    let conversation: WorkspaceConversationSummary
}

func conversationDeletionAliases(_ conversation: WorkspaceConversationSummary) -> Set<String> {
    var aliases: Set<String> = ["conversation:" + conversation.id]
    for run in conversation.runs {
        aliases.insert("run:" + run.runId)
        for id in [run.conversationId, run.conversationSnapshot?.conversation.conversationId].compactMap({ $0 }) where !id.isEmpty {
            aliases.insert("conversation:" + id)
        }
        if let previous = run.continuedFromRunId, !previous.isEmpty { aliases.insert("run:" + previous) }
        for session in conversationSessionIDs(run) { aliases.insert("session:" + session) }
    }
    if let session = conversation.resumeSession?.sessionId, !session.isEmpty {
        aliases.insert("session:" + session)
    }
    return aliases
}

enum ConversationTrashChange {
    case deleted(DeletedConversation)
    case restored(DeletedConversation)
}

@MainActor
final class ConversationTrashStore: ObservableObject {
    static let shared = ConversationTrashStore()
    @Published private(set) var records: [DeletedConversation] = []
    @Published private(set) var loadError: Error?
    let changes = PassthroughSubject<ConversationTrashChange, Never>()
    private let fileURL: URL

    init(fileURL: URL? = nil) {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
        self.fileURL = fileURL ?? base.appendingPathComponent("Parallel Codex/conversation-trash.v1.json")
        guard FileManager.default.fileExists(atPath: self.fileURL.path) else { return }
        do {
            records = try JSONDecoder().decode([DeletedConversation].self, from: Data(contentsOf: self.fileURL))
        } catch { loadError = error }
    }

    func matching(_ conversation: WorkspaceConversationSummary, machineID: String, workspaceID: String) -> DeletedConversation? {
        records.first { $0.matches(conversation, machineID: machineID, workspaceID: workspaceID) }
    }

    func delete(_ conversation: WorkspaceConversationSummary, machineID: String, workspaceID: String,
                machineName: String, workspaceName: String) throws {
        if let loadError { throw loadError }
        guard matching(conversation, machineID: machineID, workspaceID: workspaceID) == nil else { return }
        let record = DeletedConversation(id: UUID(), machineID: MachineContextKey.normalizeMachineID(machineID),
            workspaceID: workspaceID, machineName: machineName, workspaceName: workspaceName,
            title: conversation.title, deletedAt: Date(), aliases: conversationDeletionAliases(conversation))
        try persist(records + [record])
        changes.send(.deleted(record))
    }

    func restore(_ record: DeletedConversation) throws {
        if let loadError { throw loadError }
        let removed = records.filter {
            $0.machineID == record.machineID && $0.workspaceID == record.workspaceID
                && ($0.id == record.id || !$0.aliases.isDisjoint(with: record.aliases))
        }
        guard !removed.isEmpty else { return }
        let ids = Set(removed.map(\.id))
        try persist(records.filter { !ids.contains($0.id) })
        changes.send(.restored(record))
    }

    // Filtering happens at publication, including cached and late paginated results.
    // Newly discovered lineage aliases are absorbed before they can reappear alone.
    func visible(_ conversations: [WorkspaceConversationSummary], machineID: String,
                 workspaceID: String) -> [WorkspaceConversationSummary] {
        guard loadError == nil else { return [] }
        var next = records
        let scopedMachine = MachineContextKey.normalizeMachineID(machineID)
        let indexed = conversations.map { ($0, conversationDeletionAliases($0)) }
        var expanded = true
        while expanded {
            let previous = next
            for (_, aliases) in indexed {
                let matching = next.indices.filter {
                    next[$0].machineID == scopedMachine && next[$0].workspaceID == workspaceID
                        && !next[$0].aliases.isDisjoint(with: aliases)
                }
                guard let owner = matching.first else { continue }
                next[owner].aliases = matching.reduce(aliases) { $0.union(next[$1].aliases) }
                for index in matching.dropFirst().reversed() { next.remove(at: index) }
            }
            expanded = previous != next
        }
        if next != records {
            do { try persist(next) }
            catch {
                // Retain the expanded identity in memory even if this background write fails.
                // Explicit delete/restore still reports persistence errors instead of succeeding.
                records = next
                loadError = error
            }
        }
        return indexed.filter { _, aliases in
            !next.contains {
                $0.machineID == scopedMachine && $0.workspaceID == workspaceID
                    && !$0.aliases.isDisjoint(with: aliases)
            }
        }.map(\.0)
    }

    private func persist(_ next: [DeletedConversation]) throws {
        let data = try JSONEncoder().encode(next)
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        try data.write(to: fileURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
        records = next
    }
}
