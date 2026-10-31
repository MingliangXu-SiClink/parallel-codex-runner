import Foundation

struct CodexModelOptionsContext: Hashable, Sendable {
    let machineID: String
    let model: String
}

@MainActor
final class CodexModelOptionsCache {
    private struct Key: Codable, Hashable {
        let machineID: String
        let scope: String
        let model: String
    }
    private struct Entry: Codable {
        let key: Key
        let options: CodexModelOptions
        let updatedAt: Date
    }
    private struct Stored: Codable {
        var scopes: [String: String] = [:]
        var entries: [Entry] = []
    }
    private static let storageKey = "parallel-codex.model-options.v1"
    private let defaults: UserDefaults?
    private var scopes: [String: String] = [:]
    private var snapshots: [Key: Entry] = [:]
    private var requests: [Key: Task<CodexModelOptions, Error>] = [:]

    init(defaults: UserDefaults? = nil) {
        self.defaults = defaults
        guard let data = defaults?.data(forKey: Self.storageKey),
              let stored = try? JSONDecoder().decode(Stored.self, from: data) else { return }
        scopes = stored.scopes
        for entry in stored.entries where entry.options.models.contains(where: { !$0.value.isEmpty }) {
            snapshots[entry.key] = entry
        }
    }

    @discardableResult
    func selectScope(_ scope: String?, machineID: String) -> Bool {
        guard let scope, !scope.isEmpty, scopes[machineID] != scope else { return false }
        scopes[machineID] = scope
        persist(machineID: machineID)
        return true
    }

    private func key(for context: CodexModelOptionsContext) -> Key {
        Key(machineID: context.machineID, scope: scopes[context.machineID] ?? "", model: context.model)
    }

    func options(for context: CodexModelOptionsContext) -> CodexModelOptions? {
        guard var options = snapshots[key(for: context)]?.options else { return nil }
        if let models = models(for: context.machineID) { options.models = models }
        return options
    }

    func models(for machineID: String) -> [CodexOption]? {
        snapshots.values.filter { $0.key.machineID == machineID && $0.key.scope == (scopes[machineID] ?? "") }
            .max(by: {
                let first = $0.options.catalogRevision ?? 0, second = $1.options.catalogRevision ?? 0
                return first == second ? $0.updatedAt < $1.updatedAt : first < second
            })?.options.models
    }

    // Catalogs belong to the executing machine, not a conversation or an effort
    // selection. Sharing in-flight requests also keeps primary/synthesis loads
    // from asking the same machine for the same capabilities twice.
    func refresh(
        context: CodexModelOptionsContext,
        fetch: @escaping @Sendable () async throws -> CodexModelOptions
    ) async throws -> CodexModelOptions {
        let requestKey = key(for: context)
        if let request = requests[requestKey] { return try await request.value }
        let originalScope = scopes[context.machineID]
        let request = Task { [self] in
            let options = try await fetch()
            guard scopes[context.machineID] == originalScope
                    || options.catalogContext == scopes[context.machineID] else { throw CancellationError() }
            guard options.models.contains(where: { !$0.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
                throw PcrdClientError.invalidResponse
            }
            selectScope(options.catalogContext, machineID: context.machineID)
            let key = key(for: context)
            if (snapshots[key]?.options.catalogRevision ?? 0) <= (options.catalogRevision ?? 0) {
                snapshots[key] = Entry(key: key, options: options, updatedAt: Date())
            }
            persist(machineID: context.machineID)
            return self.options(for: context) ?? options
        }
        requests[requestKey] = request
        defer { requests.removeValue(forKey: requestKey) }
        return try await request.value
    }

    private func persist(machineID: String) {
        guard let defaults else { return }
        var stored = defaults.data(forKey: Self.storageKey)
            .flatMap { try? JSONDecoder().decode(Stored.self, from: $0) } ?? Stored()
        stored.scopes[machineID] = scopes[machineID]
        // Preserve other windows' machine snapshots when updating this one.
        for entry in snapshots.values where entry.key.machineID == machineID && !entry.key.scope.isEmpty {
            if let existing = stored.entries.first(where: { $0.key == entry.key }),
               (existing.options.catalogRevision ?? 0) > (entry.options.catalogRevision ?? 0) { continue }
            stored.entries.removeAll { $0.key == entry.key }
            var committed = entry.options
            committed.catalogRefreshing = nil
            stored.entries.append(Entry(key: entry.key, options: committed, updatedAt: entry.updatedAt))
        }
        if let data = try? JSONEncoder().encode(stored) { defaults.set(data, forKey: Self.storageKey) }
    }
}
