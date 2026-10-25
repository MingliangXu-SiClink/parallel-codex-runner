import CryptoKit
import Foundation
import SwiftUI

struct VisualFileVersionInfo: Decodable, Equatable, Sendable {
    let state: String
    var fileId: String? = nil
    var sha256: String? = nil
    var sizeBytes: Int64? = nil
}

struct VisualFileResponse: Decodable, Sendable {
    let runId: String
    let agent: Int
    let path: String
    let contentType: String
    var live = false
    var before: VisualFileVersionInfo? = nil
    var after: VisualFileVersionInfo? = nil
    var side: String? = nil
    var fileId: String? = nil
    var sizeBytes: Int64? = nil
    var offset: Int64? = nil
    var nextOffset: Int64? = nil
    var hasMore: Bool? = nil
    var contentBase64: String? = nil
    var chunkSha256: String? = nil
}

struct VisualFileRequest: Encodable, Sendable {
    let runId: String
    let agent: Int
    let path: String
    var side: String? = nil
    var fileId: String? = nil
    var offset: Int64? = nil
    var maxBytes: Int? = nil
}

struct VisualComparisonKey: Hashable { let context: ReviewContext; let path: String }
struct VisualFileVersion {
    let info: VisualFileVersionInfo
    var url: URL? = nil
    var lease: WorkspaceFileLease? = nil
}
struct VisualComparisonSnapshot {
    let before: VisualFileVersion
    let after: VisualFileVersion
    let live: Bool
    let capturedAt: Date
    var bytes: Int64 { (before.info.sizeBytes ?? 0) + (after.info.sizeBytes ?? 0) }
}

@MainActor
final class FileComparisonCache {
    private var entries: [VisualComparisonKey: VisualComparisonSnapshot] = [:]
    private var order: [VisualComparisonKey] = []
    func snapshot(_ key: VisualComparisonKey) -> VisualComparisonSnapshot? { entries[key] }
    func store(_ value: VisualComparisonSnapshot, for key: VisualComparisonKey) {
        entries[key] = value
        order.removeAll { $0 == key }; order.append(key)
        while order.count > 8 || entries.values.reduce(Int64(0), { $0 + $1.bytes }) > 512 * 1024 * 1024 {
            guard let first = order.first else { break }
            order.removeFirst(); entries.removeValue(forKey: first)
        }
    }
}

@MainActor
final class FileComparisonLoader: ObservableObject {
    @Published private(set) var snapshot: VisualComparisonSnapshot?
    @Published private(set) var loading = false
    @Published private(set) var error: String?
    private var key: VisualComparisonKey?
    private var generation = UUID()

    func load(client: PcrdClient, key: VisualComparisonKey, cache: FileComparisonCache,
              language: AppLanguage, refresh: Bool = false) async {
        await load(key: key, cache: cache, language: language, refresh: refresh) { request in
            try await client.call(target: key.context.target, "runs.file.preview", body: request, timeoutSeconds: 60)
        }
    }

    func load(key requested: VisualComparisonKey, cache: FileComparisonCache, language: AppLanguage,
              refresh: Bool = false,
              fetch: @MainActor (VisualFileRequest) async throws -> VisualFileResponse) async {
        let token = UUID(); generation = token
        if key != requested { key = requested; snapshot = cache.snapshot(requested); error = nil }
        if !refresh, let snapshot, !snapshot.live || Date().timeIntervalSince(snapshot.capturedAt) < 8 { return }
        guard let run = requested.context.runID, let agent = requested.context.agent else { return }
        loading = true; error = nil
        defer { if generation == token { loading = false } }
        do {
            let request = VisualFileRequest(runId: run, agent: agent, path: requested.path)
            let manifest = try await fetch(request)
            try Task.checkCancellation()
            guard manifest.runId == run, manifest.agent == agent, manifest.path == requested.path,
                  let before = manifest.before, let after = manifest.after else { throw FilePreviewError.invalid }
            let left = try await download(before, side: "before", request: request, mime: manifest.contentType, fetch: fetch)
            let right = try await download(after, side: "after", request: request, mime: manifest.contentType, fetch: fetch)
            try Task.checkCancellation()
            guard generation == token else { return }
            let value = VisualComparisonSnapshot(before: left, after: right, live: manifest.live, capturedAt: Date())
            cache.store(value, for: requested)
            // Unchanged polling never recreates the renderer or its reading position.
            if snapshot?.before.info != before || snapshot?.after.info != after { snapshot = value }
            else if let old = snapshot {
                snapshot = VisualComparisonSnapshot(before: old.before, after: old.after,
                                                    live: value.live, capturedAt: value.capturedAt)
            }
        } catch is CancellationError { }
        catch {
            guard generation == token, !Task.isCancelled else { return }
            self.error = language.text("暂时无法打开前后版本，请重试。", "The before and after versions could not be opened. Try again.")
        }
    }

    private func download(_ info: VisualFileVersionInfo, side: String, request: VisualFileRequest, mime: String,
                          fetch: @MainActor (VisualFileRequest) async throws -> VisualFileResponse) async throws -> VisualFileVersion {
        guard info.state == "available" else {
            guard ["absent", "unavailable", "too_large"].contains(info.state) else { throw FilePreviewError.invalid }
            return VisualFileVersion(info: info)
        }
        guard let id = info.fileId, let digest = info.sha256, let size = info.sizeBytes,
              id == digest, id.count == 64, id.allSatisfy({ $0.isHexDigit && !$0.isUppercase }),
              size >= 0, size <= 256 * 1024 * 1024 else { throw FilePreviewError.invalid }
        let directory = try workspaceFileCacheDirectory()
        cleanWorkspaceFileCache(directory)
        let ext = (request.path as NSString).pathExtension.lowercased()
        let destination = directory.appendingPathComponent("version-\(id).\(ext)")
        let lease = WorkspaceFileLease(destination)
        if FileManager.default.fileExists(atPath: destination.path),
           try await Self.matches(destination, digest: digest, size: size) {
            return VisualFileVersion(info: info, url: destination, lease: lease)
        }
        let temporary = directory.appendingPathComponent(".\(UUID()).part")
        let temporaryLease = WorkspaceFileLease(temporary)
        FileManager.default.createFile(atPath: temporary.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let handle = try FileHandle(forWritingTo: temporary)
        defer { try? handle.close(); try? FileManager.default.removeItem(at: temporary); _ = temporaryLease }
        var offset: Int64 = 0
        var hash = SHA256()
        while true {
            try Task.checkCancellation()
            var body = request; body.side = side; body.fileId = id; body.offset = offset; body.maxBytes = 384 * 1024
            let page = try await fetch(body)
            try Task.checkCancellation()
            guard page.runId == request.runId, page.agent == request.agent, page.path == request.path,
                  page.side == side, page.fileId == id, page.sizeBytes == size, page.contentType == mime,
                  page.offset == offset, let encoded = page.contentBase64,
                  let data = Data(base64Encoded: encoded), data.count <= 384 * 1024,
                  Self.hex(SHA256.hash(data: data)) == page.chunkSha256 else { throw FilePreviewError.invalid }
            try handle.write(contentsOf: data); hash.update(data: data)
            offset += Int64(data.count)
            guard page.nextOffset == offset, offset <= size, let more = page.hasMore else { throw FilePreviewError.invalid }
            if !more { break }
            guard !data.isEmpty else { throw FilePreviewError.invalid }
        }
        guard offset == size, Self.hex(hash.finalize()) == digest else { throw FilePreviewError.invalid }
        try handle.synchronize(); try handle.close()
        if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
        try FileManager.default.moveItem(at: temporary, to: destination)
        return VisualFileVersion(info: info, url: destination, lease: lease)
    }

    private nonisolated static func hex(_ digest: SHA256.Digest) -> String { digest.map { String(format: "%02x", $0) }.joined() }
    private nonisolated static func matches(_ url: URL, digest: String, size: Int64) async throws -> Bool {
        try await Task.detached {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            var hash = SHA256(), count: Int64 = 0
            while let data = try handle.read(upToCount: 384 * 1024), !data.isEmpty { hash.update(data: data); count += Int64(data.count) }
            return count == size && hex(hash.finalize()) == digest
        }.value
    }
}
