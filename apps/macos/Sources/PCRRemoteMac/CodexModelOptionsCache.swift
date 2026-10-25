import Foundation

struct CodexModelOptionsContext: Hashable, Sendable {
    let machineID: String
    let model: String
}

@MainActor
final class CodexModelOptionsCache {
    private var snapshots: [CodexModelOptionsContext: CodexModelOptions] = [:]
    private var catalogs: [String: [CodexOption]] = [:]
    private var requests: [CodexModelOptionsContext: Task<CodexModelOptions, Error>] = [:]

    func options(for context: CodexModelOptionsContext) -> CodexModelOptions? {
        snapshots[context]
    }

    func models(for machineID: String) -> [CodexOption]? {
        catalogs[machineID]
    }

    // Catalogs belong to the executing machine, not a conversation or an effort
    // selection. Sharing in-flight requests also keeps primary/synthesis loads
    // from asking the same machine for the same capabilities twice.
    func refresh(
        context: CodexModelOptionsContext,
        fetch: @escaping @Sendable () async throws -> CodexModelOptions
    ) async throws -> CodexModelOptions {
        if let request = requests[context] { return try await request.value }
        let request = Task { try await fetch() }
        requests[context] = request
        defer { requests.removeValue(forKey: context) }
        let options = try await request.value
        snapshots[context] = options
        catalogs[context.machineID] = options.models
        return options
    }
}

func codexModelDisplayName(_ value: String) -> String {
    guard value.hasPrefix("gpt-"), value.count > 4 else { return value }
    let parts = value.dropFirst(4).split(separator: "-")
    guard let version = parts.first else { return value }
    let suffix = parts.dropFirst().map { $0.prefix(1).uppercased() + $0.dropFirst() }
    return (["GPT-\(version)"] + suffix).joined(separator: " ")
}
