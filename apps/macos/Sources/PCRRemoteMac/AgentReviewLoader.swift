import Foundation
import SwiftUI

struct AgentReviewKey: Hashable {
    let context: ReviewContext
    let mode: AgentReviewMode
}

struct AgentReviewPage: Sendable {
    let text: String
    let nextCursor: Int
    let hasMore: Bool
    var version: String? = nil
    var live = false
}

struct AgentReviewSnapshot: Sendable {
    var text = ""
    var files: [ReviewFile] = []
    var paths: [String] = []
    var cursor = 0
    var hasMore = true
    var version: String?
    var live = false
    var capturedAt = Date()

    var completeFiles: [ReviewFile] { hasMore ? Array(files.dropLast()) : files }

    func isReady(mode: AgentReviewMode, file: String?) -> Bool {
        guard cursor > 0 || !hasMore else { return false }
        if mode == .answer { return !hasMore }
        guard let path = file ?? paths.first else { return !hasMore }
        return completeFiles.contains { $0.path == path } || !hasMore
    }

    var byteCount: Int { text.utf8.count + files.reduce(0) { $0 + $1.text.utf8.count } }
}

@MainActor
final class AgentReviewCache {
    private var entries: [AgentReviewKey: AgentReviewSnapshot] = [:]
    private var order: [AgentReviewKey] = []
    private var generations: [AgentReviewKey: UUID] = [:]
    private var positions: [AgentReviewKey: [String: ReviewReadingPosition]] = [:]
    let maxBytes: Int
    let maxEntries: Int

    init(maxBytes: Int = 24 * 1024 * 1024, maxEntries: Int = 6) {
        self.maxBytes = maxBytes
        self.maxEntries = maxEntries
    }

    func snapshot(for key: AgentReviewKey) -> AgentReviewSnapshot? {
        if entries[key] != nil { touch(key) }
        return entries[key]
    }

    func begin(_ key: AgentReviewKey) -> UUID {
        let token = UUID()
        generations[key] = token
        return token
    }

    func isCurrent(_ token: UUID, for key: AgentReviewKey) -> Bool { generations[key] == token }

    func finish(_ token: UUID, for key: AgentReviewKey) {
        if isCurrent(token, for: key) { generations.removeValue(forKey: key) }
    }

    func store(_ value: AgentReviewSnapshot, for key: AgentReviewKey, token: UUID) {
        guard isCurrent(token, for: key) else { return }
        entries[key] = value
        touch(key)
        while order.count > maxEntries || entries.values.reduce(0, { $0 + $1.byteCount }) > maxBytes {
            guard let oldest = order.first else { break }
            order.removeFirst()
            entries.removeValue(forKey: oldest)
            positions.removeValue(forKey: oldest)
        }
    }

    func position(for key: AgentReviewKey, file: String) -> ReviewReadingPosition? { positions[key]?[file] }

    func remember(_ position: ReviewReadingPosition, for key: AgentReviewKey, file: String) {
        guard entries[key] != nil else { return }
        positions[key, default: [:]][file] = position
    }

    private func touch(_ key: AgentReviewKey) {
        order.removeAll { $0 == key }
        order.append(key)
    }
}

private enum AgentReviewReadError: Error, Equatable { case changed, invalidPage }

@MainActor
final class AgentReviewLoader: ObservableObject {
    @Published private(set) var snapshot: AgentReviewSnapshot?
    @Published private(set) var loading = false
    @Published private(set) var error: String?
    private(set) var loadedKey: AgentReviewKey?
    private var generation = UUID()

    func load(client: PcrdClient, context: ReviewContext, mode: AgentReviewMode,
              language: AppLanguage, cache: AgentReviewCache, requestedFile: String? = nil,
              refresh: Bool = false) async {
        guard let runID = context.runID, let agent = context.agent else { return }
        await load(key: AgentReviewKey(context: context, mode: mode), language: language,
                   cache: cache, requestedFile: requestedFile, refresh: refresh) { offset, version in
            if mode == .changes {
                let page: RunDiffPage = try await client.call(target: context.target, "runs.diff",
                    body: DiffBody(runId: runID, agent: agent, cursor: offset, limit: 192 * 1024,
                                   fullContext: true, snapshotId: version))
                guard page.runId == runID, page.agent == agent, page.cursor == offset else {
                    throw AgentReviewReadError.invalidPage
                }
                return AgentReviewPage(text: page.text == "No workspace changes." ? "" : page.text,
                    nextCursor: page.nextCursor, hasMore: page.hasMore,
                    version: page.snapshotId, live: page.live ?? false)
            }
            let detail: RunAgentDetail = try await client.call(target: context.target, "runs.agent",
                body: GetAgentBody(runId: runID, agent: agent, includeDiff: false, includeEvents: false,
                    cursor: 0, eventLimit: 1, transcriptItemsOnly: true, conversationItemsOnly: true,
                    finalMessageCursor: offset))
            return AgentReviewPage(text: detail.finalMessagePage?.text ?? detail.finalMessage,
                nextCursor: detail.finalMessagePage?.nextCursor ?? detail.finalMessage.utf8.count,
                hasMore: detail.finalMessagePage?.hasMore ?? false,
                version: detail.finalMessagePage?.messageId)
        }
    }

    func load(key: AgentReviewKey, language: AppLanguage, cache: AgentReviewCache,
              requestedFile: String? = nil, refresh: Bool = false,
              fetch: @MainActor (Int, String?) async throws -> AgentReviewPage) async {
        let token = UUID()
        generation = token
        let cached = cache.snapshot(for: key)
        if loadedKey != key {
            snapshot = cached
            loadedKey = key
            error = nil
        } else if snapshot == nil { snapshot = cached }
        let retained = cached ?? snapshot
        let fresh = retained.map { Date().timeIntervalSince($0.capturedAt) < ($0.live ? 8 : 60) } ?? false
        if !refresh, fresh, let retained, retained.isReady(mode: key.mode, file: requestedFile) {
            snapshot = retained
            loading = false
            showMissingFileIfNeeded(requestedFile, language: language)
            return
        }
        let cacheToken = cache.begin(key)
        loading = true
        defer {
            cache.finish(cacheToken, for: key)
            if generation == token { loading = false }
        }
        let needsContinuation = retained.map { !$0.isReady(mode: key.mode, file: requestedFile) } ?? false
        var extendsRetained = !refresh && retained != nil && (fresh || needsContinuation)
        var working = AgentReviewSnapshot()
        if extendsRetained, let retained { working = retained }
        let retainsReadableContent = retained.map {
            key.mode == .answer ? !$0.hasMore : !$0.completeFiles.isEmpty
        } ?? false
        var restarted = false
        do {
            while true {
                do {
                    let page = try await fetch(working.cursor, working.version)
                    try Task.checkCancellation()
                    guard generation == token else { return }
                    guard page.nextCursor >= working.cursor,
                          !page.hasMore || page.nextCursor > working.cursor else {
                        throw AgentReviewReadError.invalidPage
                    }
                    if working.cursor > 0, working.version != page.version {
                        throw AgentReviewReadError.changed
                    }
                    if working.cursor == 0 { working.capturedAt = Date() }
                    working.text += page.text
                    working.cursor = page.nextCursor
                    working.hasMore = page.hasMore
                    working.version = page.version
                    working.live = page.live
                    if key.mode == .changes {
                        let text = working.text
                        let parsed = await Task.detached {
                            (ReviewFile.parse(text), text.contains("\n\n") ? AgentDiffSummary.parse(text).changes.map(\.path) : [])
                        }.value
                        working.files = parsed.0
                        working.paths = parsed.1.isEmpty ? working.completeFiles.map(\.path) : parsed.1
                    }
                    try Task.checkCancellation()
                    guard generation == token else { return }
                    let ready = working.isReady(mode: key.mode, file: requestedFile)
                    // A refresh must not replace the reopenable complete file with
                    // its unfinished first page. Same-snapshot extensions can resume.
                    if ready || extendsRetained || !retainsReadableContent {
                        cache.store(working, for: key, token: cacheToken)
                    }
                    // Publish only a complete requested file, never a page fragment or
                    // another file under the selected file's title.
                    if ready {
                        snapshot = working
                        showMissingFileIfNeeded(requestedFile, language: language)
                        return
                    }
                } catch {
                    try Task.checkCancellation()
                    guard generation == token else { return }
                    let expired: Bool
                    if case PcrdClientError.remote("pcr_error", let message) = error {
                        expired = message == "This live file preview has expired. Refresh to load the current changes."
                    } else { expired = false }
                    if !restarted && (expired || (error as? AgentReviewReadError) == .changed) {
                        working = AgentReviewSnapshot()
                        extendsRetained = false
                        restarted = true
                        continue
                    }
                    throw error
                }
            }
        } catch is CancellationError {
        } catch {
            guard generation == token else { return }
            let readable = snapshot?.isReady(mode: key.mode, file: requestedFile) == true
            self.error = readable
                ? language.text("暂时无法更新，仍显示上次读取的内容。", "Couldn't update. Showing the last loaded content.")
                : language.text("暂时无法读取完整内容，请重试。", "Couldn't load the complete content. Try again.")
        }
    }

    private func showMissingFileIfNeeded(_ path: String?, language: AppLanguage) {
        if let path, snapshot?.hasMore == false, snapshot?.completeFiles.contains(where: { $0.path == path }) != true {
            error = language.text("未找到 \(path) 的修改记录。", "No recorded changes for \(path).")
        } else {
            error = nil
        }
    }
}
