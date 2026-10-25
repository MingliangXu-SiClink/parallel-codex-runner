import Foundation

struct AgentFileChange: Identifiable, Equatable, Hashable, Sendable {
    let status: String
    let path: String

    var id: String { "\(status)\u{1f}\(path)" }

    var category: AgentFileCategory { AgentFileCategory.classify(path) }
}

enum AgentFileCategory: String, CaseIterable, Identifiable {
    case project, generated, runtime
    var id: Self { self }

    static func classify(_ path: String) -> Self {
        let components = path.replacingOccurrences(of: "\\", with: "/")
            .lowercased().split(separator: "/").map(String.init)
        let directories = Set(components.dropLast())
        let name = components.last ?? ""
        if !directories.isDisjoint(with: ["node_modules", ".venv", "venv", "dist", "build", ".build", ".next", ".nuxt", "__pycache__", ".gradle", "deriveddata"])
            || name.hasSuffix(".pyc") || name.hasSuffix(".map") {
            return .generated
        }
        if !directories.isDisjoint(with: [".local", "logs", ".cache", ".pytest_cache"])
            || [".log", ".sqlite", ".sqlite3", ".db", "-wal", "-shm", ".pid"].contains(where: name.hasSuffix) {
            return .runtime
        }
        return .project
    }

    func title(_ language: AppLanguage) -> String {
        switch self {
        case .project: return language.text("源码与文档", "Source & documents")
        case .generated: return language.text("构建与依赖", "Build & dependencies")
        case .runtime: return language.text("运行数据与日志", "Runtime data & logs")
        }
    }
}

struct AgentDiffSummary: Equatable, Sendable {
    let changes: [AgentFileChange]
    let totalFiles: Int
    let truncated: Bool
    let unavailable: Bool

    init(
        changes: [AgentFileChange] = [],
        totalFiles: Int? = nil,
        truncated: Bool = false,
        unavailable: Bool = false
    ) {
        self.changes = changes
        self.totalFiles = max(changes.count, totalFiles ?? changes.count)
        self.truncated = truncated
        self.unavailable = unavailable
    }

    static func parse(_ text: String) -> AgentDiffSummary {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty
            || trimmed == "No workspace changes."
            || trimmed == "No file changes." {
            return AgentDiffSummary()
        }

        let headerEnd = text.range(of: "\n\n")?.lowerBound
        let header = headerEnd.map { String(text[..<$0]) } ?? text
        let changes = header.split(separator: "\n", omittingEmptySubsequences: true)
            .compactMap(parseFileChange)
        let totalFiles = parseTotalFileCount(text) ?? changes.count
        return AgentDiffSummary(
            changes: changes,
            totalFiles: totalFiles,
            truncated: headerEnd == nil || totalFiles > changes.count
        )
    }

    private static func parseFileChange(_ line: Substring) -> AgentFileChange? {
        guard line.count >= 4 else { return nil }
        let status = String(line.prefix(1))
        guard ["A", "C", "D", "M", "R", "T", "U"].contains(status),
              line.dropFirst().hasPrefix("  ") else { return nil }
        let path = line.dropFirst(3).trimmingCharacters(in: .whitespaces)
        guard !path.isEmpty else { return nil }
        return AgentFileChange(status: status, path: path)
    }

    private static func parseTotalFileCount(_ text: String) -> Int? {
        for line in text.split(separator: "\n") {
            let words = line.split(whereSeparator: \.isWhitespace)
            guard words.count >= 3,
                  let count = Int(words[0]),
                  words[1] == "file" || words[1] == "files",
                  words[2].hasPrefix("changed") else { continue }
            return count
        }
        return nil
    }
}
