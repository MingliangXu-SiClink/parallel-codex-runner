import AppKit
import CryptoKit
import Foundation
import MarkdownUI
import SwiftUI
import UniformTypeIdentifiers

private let workspaceFileChunkBytes = 384 * 1024
private let maximumWorkspaceFileBytes: Int64 = 256 * 1024 * 1024
private let maximumNativeMarkdownBytes: Int64 = 4 * 1024 * 1024
private let workspaceFileCacheBytes: Int64 = 512 * 1024 * 1024
private let workspaceFileCacheAge: TimeInterval = 7 * 24 * 60 * 60

@MainActor
final class WorkspaceFileLease: NSObject {
    private static let active = NSHashTable<WorkspaceFileLease>.weakObjects()
    var url: URL

    init(_ url: URL) {
        self.url = url
        super.init()
        Self.active.add(self)
    }

    static var protectedPaths: Set<String> {
        Set(active.allObjects.map { $0.url.standardizedFileURL.resolvingSymlinksInPath().path })
    }
}

struct WorkspaceFileDocument: Identifiable {
    var id = UUID()
    let name: String
    let content: String
    let fileURL: URL
    let relativePath: String
    let line: Int?
    var previewKind: WorkspacePreviewKind = .markdown
    var context: ReviewContext? = nil
    var cacheLease: WorkspaceFileLease? = nil
    var directory: WorkspaceItem? = nil
}

struct WorkspaceFileIssue {
    let reference: String
    let message: String
    let retry: (() -> Void)?
}

struct WorkspaceItemRequest: Encodable, Sendable {
    let workspaceId: String
    let runId: String?
    let agent: Int?
    let reference: String
    var offset = 0
}

struct WorkspaceDirectoryEntry: Decodable, Sendable, Identifiable {
    let name: String
    let path: String
    let kind: String
    var id: String { name }
}

struct WorkspaceItem: Decodable, Sendable {
    let name: String
    let path: String
    let relativePath: String
    let kind: String
    let scope: String
    let contentType: String
    let sizeBytes: Int64
    let line: Int?
    var entries: [WorkspaceDirectoryEntry]
    var nextOffset: Int
    var hasMore: Bool

    func localURL(for target: PcrdRequestTarget) -> URL? {
        // A path from a remote Mac or Linux machine is never a local path,
        // even when an identically named file happens to exist on this Mac.
        switch target {
        case .local, .localMachine:
            return path.hasPrefix("/") ? URL(fileURLWithPath: path) : nil
        case .relay:
            return nil
        }
    }
}

func localFileLinkItem(_ reference: String, target: PcrdRequestTarget) throws -> WorkspaceItem? {
    switch target {
    case .relay: return nil
    case .local, .localMachine: break
    }
    let reference = reference.trimmingCharacters(in: .whitespacesAndNewlines)
    var path: String
    var fragment: String?
    if reference.lowercased().hasPrefix("file:") {
        guard let url = URL(string: reference), url.isFileURL,
              url.host == nil || url.host == "" || url.host?.lowercased() == "localhost" else {
            throw LocalFileLinkError.invalid
        }
        path = url.path
        fragment = url.fragment
    } else {
        guard reference.hasPrefix("/") || reference.hasPrefix("~/") else { return nil }
        let parts = reference.split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false)
        path = String(parts[0])
        fragment = parts.count > 1 ? String(parts[1]) : nil
    }
    path = (path as NSString).expandingTildeInPath
    var line = fragment.flatMap { value -> Int? in
        guard let range = value.range(of: #"^L?[0-9]+"#, options: .regularExpression) else { return nil }
        return Int(value[range].replacingOccurrences(of: "L", with: ""))
    }
    if !FileManager.default.fileExists(atPath: path),
       let match = path.range(of: #":[0-9]+(?::[0-9]+)?$"#, options: .regularExpression) {
        line = line ?? Int(path[match].dropFirst().split(separator: ":")[0])
        path.removeSubrange(match)
    }
    let url = URL(fileURLWithPath: path).standardizedFileURL
    let values = try url.resolvingSymlinksInPath().resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey, .fileSizeKey, .contentTypeKey])
    guard values.isDirectory == true || values.isRegularFile == true else {
        throw LocalFileLinkError.unsupportedKind
    }
    // This is an explicit path on this Mac, not a remote workspace capability.
    // Keep it absolute so links inside an external Markdown document stay local.
    return WorkspaceItem(name: url.lastPathComponent, path: url.path, relativePath: url.path,
        kind: values.isDirectory == true ? "directory" : "file", scope: "local",
        contentType: values.contentType?.preferredMIMEType ?? "application/octet-stream",
        sizeBytes: Int64(values.fileSize ?? 0), line: line.flatMap { $0 > 0 ? $0 : nil },
        entries: [], nextOffset: 0, hasMore: false)
}

private enum LocalFileLinkError: LocalizedError {
    case invalid
    case unsupportedKind

    var errorDescription: String? {
        switch self {
        case .invalid: "This file link is not a path on this Mac."
        case .unsupportedKind: "Only regular files and folders can be opened."
        }
    }
}

private struct WorkspaceFileReadBody: Encodable, Sendable {
    let workspaceId: String
    let runId: String?
    let agent: Int?
    let reference: String
    let offset: Int64
    let maxBytes: Int
    let expectedFileId: String?
    let literalPaths = true

    enum CodingKeys: String, CodingKey {
        case workspaceId = "workspace_id"
        case runId = "run_id"
        case agent
        case reference
        case offset
        case maxBytes = "max_bytes"
        case expectedFileId = "expected_file_id"
        case literalPaths = "literal_paths"
    }
}

struct WorkspaceFilePage: Decodable, Sendable {
    let name: String
    let relativePath: String
    let contentType: String
    let sizeBytes: Int64
    let fileId: String
    let offset: Int64
    let nextOffset: Int64
    let hasMore: Bool
    let contentBase64: String
    let chunkSha256: String
    let line: Int?
}

private struct WorkspaceFileDownload {
    let name: String
    let relativePath: String
    let contentType: String
    let sizeBytes: Int64
    let line: Int?
    let fileURL: URL
    var cacheLease: WorkspaceFileLease? = nil

    var isMarkdown: Bool {
        if contentType.caseInsensitiveCompare("text/markdown") == .orderedSame { return true }
        return ["md", "markdown", "mdown", "mkd"].contains(
            fileURL.pathExtension.lowercased()
        )
    }
}

extension AppModel {
    func openTranscriptLink(_ url: URL, relativeTo documentPath: String? = nil, reviewContext: ReviewContext? = nil) {
        let rawReference = workspaceLinkReference(url)
        switch workspaceLinkKind(rawReference) {
        case .external:
            guard let externalURL = externalWorkspaceLinkURL(rawReference),
                  NSWorkspace.shared.open(externalURL) else {
                workspaceFileIssue = WorkspaceFileIssue(reference: rawReference, message: appLanguage.text(
                    "这台 Mac 上没有可打开该链接的应用。",
                    "No application on this Mac can open that link."
                ), retry: { [weak self] in self?.openTranscriptLink(url, relativeTo: documentPath, reviewContext: reviewContext) })
                return
            }
        case .unsupported:
            workspaceFileIssue = WorkspaceFileIssue(reference: rawReference, message: appLanguage.text(
                "出于安全考虑，无法打开该链接。",
                "This link cannot be opened safely."
            ), retry: nil)
        case .workspaceFile:
            downloadWorkspaceFile(
                resolveWorkspaceLink(rawReference, relativeTo: documentPath),
                navigationParent: documentPath == workspaceDocument?.relativePath
                    ? workspaceDocument : nil,
                reviewContext: reviewContext
            )
        }
    }

    func cancelWorkspaceFileDownload() {
        workspaceFileTask?.cancel()
        workspaceFileTask = nil
        workspaceFileRequestID = UUID()
        workspaceFileLoading = false
        workspaceFileProgress = nil
    }

    func dismissWorkspaceDocument() {
        cancelWorkspaceFileDownload()
        workspaceFileIssue = nil
        workspaceDocument = nil
        workspaceDocumentHistory = []
        workspaceFilePresented = false
    }

    func dismissWorkspaceFileIssue() {
        cancelWorkspaceFileDownload()
        workspaceFileIssue = nil
        workspaceFilePresented = workspaceDocument != nil
    }

    func navigateBackWorkspaceDocument() {
        cancelWorkspaceFileDownload()
        workspaceFileIssue = nil
        guard let previous = workspaceDocumentHistory.popLast() else {
            dismissWorkspaceDocument()
            return
        }
        workspaceDocument = previous
    }

    private func presentWorkspaceDocument(_ document: WorkspaceFileDocument, navigationParent: WorkspaceFileDocument?) {
        var document = document
        if let previous = workspaceDocument,
           previous.context == document.context,
           previous.relativePath == document.relativePath,
           previous.previewKind == document.previewKind,
           previous.line == document.line {
            document.id = previous.id
        } else if let navigationParent, workspaceDocument?.id == navigationParent.id {
            workspaceDocumentHistory.append(navigationParent)
        }
        workspaceDocument = document
        diffPresented = false
        workspaceFileIssue = nil
    }

    func openWorkspaceDocumentExternally() {
        guard let document = workspaceDocument,
              NSWorkspace.shared.open(document.fileURL) else {
            workspaceFileIssue = WorkspaceFileIssue(reference: workspaceDocument?.name ?? "", message: appLanguage.text(
                "这台 Mac 上没有可打开该文件的应用。",
                "No application on this Mac can open that file."
            ), retry: { [weak self] in self?.openWorkspaceDocumentExternally() })
            return
        }
    }

    private func downloadWorkspaceFile(
        _ reference: String,
        navigationParent: WorkspaceFileDocument? = nil,
        reviewContext: ReviewContext? = nil
    ) {
        guard let workspaceID = selectedWorkspaceID else { return }
        cancelWorkspaceFileDownload()
        let target = client.currentTarget()
        if let reviewContext,
           (reviewContext.target != target || reviewContext.workspaceID != workspaceID) {
            workspaceFileIssue = WorkspaceFileIssue(reference: reference, message: appLanguage.text("请在该文件所属的设备和工作区中打开。", "Open this file from its device and workspace."), retry: nil)
            return
        }
        let machineID = target.machineID
        let runID = reviewContext?.runID
        let conversationID = selectedConversationID
        let agent = reviewContext?.agent
        let generation = workspaceContextGeneration
        let requestID = workspaceFileRequestID
        let retry: () -> Void = { [weak self] in
            guard let self, self.isCurrentMachine(machineID),
                  self.isCurrentWorkspaceContext(workspaceID, generation: generation),
                  self.selectedConversationID == conversationID else { return }
            self.downloadWorkspaceFile(reference, navigationParent: navigationParent, reviewContext: reviewContext)
        }

        workspaceFileLoading = true
        workspaceFileProgress = 0
        workspaceFileTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.workspaceFileRequestID == requestID {
                    self.workspaceFileLoading = false
                    self.workspaceFileProgress = nil
                    self.workspaceFileTask = nil
                }
            }
            do {
                let item: WorkspaceItem
                if let local = try localFileLinkItem(reference, target: target) {
                    item = local
                } else {
                    item = try await self.client.call(
                        target: target, "workspace.item.resolve",
                        body: WorkspaceItemRequest(workspaceId: workspaceID, runId: runID,
                            agent: agent, reference: reference)
                    )
                }
                try Task.checkCancellation()
                guard self.isCurrentMachine(machineID),
                      self.isCurrentWorkspaceContext(workspaceID, generation: generation),
                      self.selectedConversationID == conversationID else { return }
                let independentFile = item.scope == "source" || item.scope == "local"
                let context = ReviewContext(target: target, workspaceID: workspaceID,
                    runID: independentFile ? nil : runID,
                    agent: independentFile ? nil : agent, conversationID: conversationID)
                if item.kind == "directory", let localURL = item.localURL(for: target) {
                    guard NSWorkspace.shared.open(localURL) else {
                        throw WorkspaceFileTransferError.cannotOpenFolder
                    }
                    self.workspaceFileIssue = nil
                    self.workspaceFilePresented = self.workspaceDocument != nil
                    return
                }
                if item.kind == "directory" {
                    self.presentWorkspaceDocument(WorkspaceFileDocument(name: item.name, content: "",
                        fileURL: URL(fileURLWithPath: item.path), relativePath: item.relativePath,
                        line: nil, previewKind: .directory, context: context, directory: item),
                        navigationParent: navigationParent)
                    return
                }
                let download: WorkspaceFileDownload
                if let localURL = item.localURL(for: target) {
                    download = WorkspaceFileDownload(name: item.name, relativePath: item.relativePath,
                        contentType: item.contentType, sizeBytes: item.sizeBytes, line: item.line,
                        fileURL: localURL)
                } else {
                    download = try await self.fetchWorkspaceFile(target: target, workspaceID: workspaceID,
                        runID: context.runID, agent: context.agent, reference: reference)
                }
                try Task.checkCancellation()
                guard self.isCurrentMachine(machineID),
                      self.isCurrentWorkspaceContext(workspaceID, generation: generation),
                      self.selectedConversationID == conversationID else { return }
                var kind = WorkspacePreviewKind.resolve(download.fileURL, contentType: download.contentType)
                if kind != .external {
                    let content = (kind == .text || kind == .markdown) && download.sizeBytes <= maximumNativeMarkdownBytes
                        ? (try? String(contentsOf: download.fileURL, encoding: .utf8)) : nil
                    if content == nil && (kind == .text || kind == .markdown) { kind = .quickLook }
                    self.presentWorkspaceDocument(WorkspaceFileDocument(
                        name: download.name,
                        content: content ?? "",
                        fileURL: download.fileURL,
                        relativePath: download.relativePath,
                        line: download.line,
                        previewKind: kind,
                        context: context,
                        cacheLease: download.cacheLease
                    ), navigationParent: navigationParent)
                } else if !NSWorkspace.shared.open(download.fileURL) {
                    self.workspaceFileIssue = WorkspaceFileIssue(reference: reference, message: self.appLanguage.text(
                        "这台 Mac 上没有可打开 \(download.name) 的应用。",
                        "No application on this Mac can open \(download.name)."
                    ), retry: retry)
                } else {
                    self.workspaceFileIssue = nil
                    self.workspaceFilePresented = self.workspaceDocument != nil
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.workspaceFileRequestID == requestID, !Task.isCancelled,
                      self.isCurrentMachine(machineID),
                      self.isCurrentWorkspaceContext(workspaceID, generation: generation),
                      self.selectedConversationID == conversationID else { return }
                let detail = self.appLanguage.userMessage(for: error)
                self.workspaceFileIssue = WorkspaceFileIssue(reference: reference, message: detail, retry: retry)
            }
        }
    }

    func loadMoreWorkspaceDirectory() async {
        guard let document = workspaceDocument, let directory = document.directory,
              directory.hasMore, let context = document.context, !workspaceFileLoading else { return }
        let requestID = UUID()
        workspaceFileRequestID = requestID
        workspaceFileLoading = true
        defer {
            if workspaceFileRequestID == requestID { workspaceFileLoading = false }
        }
        do {
            let page: WorkspaceItem = try await client.call(target: context.target, "workspace.item.resolve",
                body: WorkspaceItemRequest(workspaceId: context.workspaceID, runId: context.runID,
                    agent: context.agent, reference: directory.path, offset: directory.nextOffset))
            guard workspaceFileRequestID == requestID, workspaceDocument?.id == document.id,
                  !Task.isCancelled else { return }
            let existing = Set(directory.entries.map(\.id))
            workspaceDocument?.directory?.entries += page.entries.filter { !existing.contains($0.id) }
            workspaceDocument?.directory?.nextOffset = page.nextOffset
            workspaceDocument?.directory?.hasMore = page.hasMore
            workspaceFileIssue = nil
        } catch {
            guard workspaceFileRequestID == requestID, workspaceDocument?.id == document.id,
                  !Task.isCancelled else { return }
            workspaceFileIssue = WorkspaceFileIssue(reference: document.name,
                message: appLanguage.userMessage(for: error), retry: { [weak self] in Task { await self?.loadMoreWorkspaceDirectory() } })
        }
    }

    private func fetchWorkspaceFile(
        target: PcrdRequestTarget,
        workspaceID: String,
        runID: String?,
        agent: Int?,
        reference: String
    ) async throws -> WorkspaceFileDownload {
        let cache = try workspaceFileCacheDirectory()
        cleanWorkspaceFileCache(cache)
        let temporary = cache.appendingPathComponent(".\(UUID().uuidString).part")
        let lease = WorkspaceFileLease(temporary)
        FileManager.default.createFile(atPath: temporary.path, contents: nil)
        let handle = try FileHandle(forWritingTo: temporary)
        var keepTemporary = false
        defer {
            try? handle.close()
            if !keepTemporary { try? FileManager.default.removeItem(at: temporary) }
        }

        var offset: Int64 = 0
        var fileID = ""
        var name = ""
        var relativePath = ""
        var contentType = "application/octet-stream"
        var sizeBytes: Int64 = -1
        var line: Int?
        while true {
            try Task.checkCancellation()
            let page: WorkspaceFilePage = try await client.call(
                target: target,
                "workspace.file.read",
                body: WorkspaceFileReadBody(
                    workspaceId: workspaceID,
                    runId: runID,
                    agent: agent,
                    reference: reference,
                    offset: offset,
                    maxBytes: workspaceFileChunkBytes,
                    expectedFileId: fileID.isEmpty ? nil : fileID
                ),
                timeoutSeconds: 60
            )
            try Task.checkCancellation()
            if fileID.isEmpty {
                guard page.offset == 0,
                      !page.fileId.isEmpty,
                      page.sizeBytes >= 0,
                      page.sizeBytes <= maximumWorkspaceFileBytes else {
                    throw WorkspaceFileTransferError.invalidResponse
                }
                let safeName = (page.name as NSString).lastPathComponent
                guard safeName == page.name, safeName != ".", safeName != ".." else {
                    throw WorkspaceFileTransferError.invalidResponse
                }
                fileID = page.fileId
                name = safeName
                relativePath = page.relativePath
                contentType = page.contentType.isEmpty
                    ? "application/octet-stream" : page.contentType
                sizeBytes = page.sizeBytes
                line = page.line
            }
            guard page.fileId == fileID,
                  page.offset == offset,
                  page.sizeBytes == sizeBytes,
                  page.name == name,
                  page.relativePath == relativePath,
                  (page.contentType.isEmpty ? "application/octet-stream" : page.contentType)
                    == contentType,
                  page.line == line,
                  let content = Data(base64Encoded: page.contentBase64),
                  content.count <= workspaceFileChunkBytes,
                  sha256(content) == page.chunkSha256.lowercased() else {
                throw WorkspaceFileTransferError.invalidResponse
            }
            try handle.write(contentsOf: content)
            offset += Int64(content.count)
            guard page.nextOffset == offset, offset <= sizeBytes else {
                throw WorkspaceFileTransferError.invalidResponse
            }
            workspaceFileProgress = sizeBytes == 0
                ? 1 : min(1, Double(offset) / Double(sizeBytes))
            if !page.hasMore { break }
            guard !content.isEmpty else { throw WorkspaceFileTransferError.invalidResponse }
        }
        guard offset == sizeBytes else { throw WorkspaceFileTransferError.incomplete }
        try handle.synchronize()
        try handle.close()

        let destination = uniqueWorkspaceFile(cache: cache, name: name)
        try FileManager.default.moveItem(at: temporary, to: destination)
        lease.url = destination
        keepTemporary = true
        return WorkspaceFileDownload(
            name: name,
            relativePath: relativePath,
            contentType: contentType,
            sizeBytes: sizeBytes,
            line: line,
            fileURL: destination,
            cacheLease: lease
        )
    }
}

private enum WorkspaceFileTransferError: LocalizedError {
    case invalidResponse
    case incomplete
    case cannotOpenFolder

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "The workspace file changed or failed its integrity check."
        case .incomplete:
            return "The workspace file download did not finish."
        case .cannotOpenFolder:
            return "This folder could not be opened in Finder."
        }
    }
}

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func workspaceFileCacheDirectory() throws -> URL {
    let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Parallel Codex", isDirectory: true)
        .appendingPathComponent("Workspace Files", isDirectory: true)
    try FileManager.default.createDirectory(
        at: cache,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    return cache
}

@MainActor
func cleanWorkspaceFileCache(_ directory: URL) {
    let manager = FileManager.default
    let protected = WorkspaceFileLease.protectedPaths
    let keys: Set<URLResourceKey> = [.isRegularFileKey, .contentModificationDateKey, .fileSizeKey]
    guard var files = try? manager.contentsOfDirectory(
        at: directory,
        includingPropertiesForKeys: Array(keys),
        options: []
    ) else { return }
    let now = Date()
    for file in files {
        guard !protected.contains(file.standardizedFileURL.resolvingSymlinksInPath().path) else { continue }
        guard let values = try? file.resourceValues(forKeys: keys),
              values.isRegularFile == true else { continue }
        if now.timeIntervalSince(values.contentModificationDate ?? .distantPast)
            > (file.pathExtension == "part" ? 24 * 60 * 60 : workspaceFileCacheAge) {
            try? manager.removeItem(at: file)
        }
    }
    files = (try? manager.contentsOfDirectory(
        at: directory,
        includingPropertiesForKeys: Array(keys),
        options: []
    )) ?? []
    let records = files.compactMap { file -> (URL, Date, Int64)? in
        guard !protected.contains(file.standardizedFileURL.resolvingSymlinksInPath().path), file.pathExtension != "part" else { return nil }
        guard let values = try? file.resourceValues(forKeys: keys),
              values.isRegularFile == true else { return nil }
        return (
            file,
            values.contentModificationDate ?? .distantPast,
            Int64(values.fileSize ?? 0)
        )
    }.sorted { $0.1 < $1.1 }
    var total = records.reduce(Int64(0)) { $0 + $1.2 }
    for record in records where total > workspaceFileCacheBytes {
        if (try? manager.removeItem(at: record.0)) != nil { total -= record.2 }
    }
}

private func uniqueWorkspaceFile(cache: URL, name: String) -> URL {
    let manager = FileManager.default
    var candidate = cache.appendingPathComponent(name)
    guard manager.fileExists(atPath: candidate.path) else { return candidate }
    let stem = (name as NSString).deletingPathExtension
    let ext = (name as NSString).pathExtension
    for index in 2...10_000 {
        let next = ext.isEmpty ? "\(stem) \(index)" : "\(stem) \(index).\(ext)"
        candidate = cache.appendingPathComponent(next)
        if !manager.fileExists(atPath: candidate.path) { return candidate }
    }
    return cache.appendingPathComponent("\(UUID().uuidString)-\(name)")
}

struct WorkspaceFileProgressOverlay: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(model.appLanguage.localized("Opening workspace file"))
                    .appScaledTextStyle(.headline)
                Spacer()
                Button(action: model.cancelWorkspaceFileDownload) {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.plain)
                .help(model.appLanguage.localized("Cancel"))
                .accessibilityLabel(model.appLanguage.localized("Cancel"))
            }
            ProgressView(value: model.workspaceFileProgress ?? 0)
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .frame(width: 280 * appTextSize.scale)
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(.separator, lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.12), radius: 16, y: 6)
        .padding(20)
    }
}

struct WorkspaceFileIssueView: View {
    @EnvironmentObject private var model: AppModel
    let issue: WorkspaceFileIssue
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(model.appLanguage.text("无法打开文件或链接", "Could not open file or link"), systemImage: "exclamationmark.circle")
                Spacer()
                Button(action: model.dismissWorkspaceFileIssue) { Image(systemName: "xmark") }
                    .help(model.appLanguage.localized("Close"))
            }
            Text(issue.reference).lineLimit(2).truncationMode(.middle)
            Text(issue.message).foregroundStyle(.secondary).textSelection(.enabled)
            if let retry = issue.retry {
                Button(action: retry) {
                    Label(model.appLanguage.text("重试", "Retry"), systemImage: "arrow.clockwise")
                }
                .disabled(model.workspaceFileLoading)
                .accessibilityIdentifier("review.file-retry")
            }
        }.appScaledTextStyle(.callout).padding(16)
    }
}

struct WorkspaceDocumentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize
    let document: WorkspaceFileDocument

    private var sourceLabel: String {
        if document.directory != nil { return model.appLanguage.text("服务器文件夹", "Server folder") }
        if let agent = document.context?.agent {
            return model.appLanguage.text("Agent \(agent) 的文件", "Agent \(agent) file")
        }
        if let target = document.context?.target {
            switch target {
            case .local, .localMachine: return model.appLanguage.text("本机文件", "Local file")
            case .relay: break
            }
        }
        return model.appLanguage.text("工作区文件", "Workspace file")
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: document.directory == nil ? "doc.text" : "folder")
                    .foregroundStyle(.tint)
                Text(document.name)
                    .appScaledTextStyle(.headline)
                    .lineLimit(1)
                Text(sourceLabel)
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let line = document.line {
                    Text(model.appLanguage.text("第 \(line) 行", "Line \(line)"))
                        .appScaledTextStyle(.caption, design: .monospaced)
                        .foregroundStyle(.secondary)
                }
                if document.directory == nil {
                    Button(action: model.openWorkspaceDocumentExternally) {
                        Image(systemName: "arrow.up.forward.app")
                    }
                    .help(model.appLanguage.localized("Open with another app"))
                }
                if !model.workspaceDocumentHistory.isEmpty {
                    Button(action: model.navigateBackWorkspaceDocument) {
                        Image(systemName: "chevron.backward")
                    }
                    .help(model.appLanguage.localized("Back to previous document"))
                }
                Button(action: model.dismissWorkspaceDocument) {
                    Image(systemName: "xmark")
                }
                .help(model.appLanguage.localized("Close"))
            }
            .padding(.horizontal, 18 * appTextSize.scale)
            .frame(minHeight: 52 * appTextSize.scale)
            Divider()
            if let directory = document.directory {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(directory.entries) { entry in
                            Button {
                                model.openTranscriptLink(URL(fileURLWithPath: entry.path),
                                    relativeTo: document.relativePath, reviewContext: document.context)
                            } label: {
                                Label(entry.name, systemImage: entry.kind == "directory" ? "folder" : "doc")
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.vertical, 8).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                        if directory.entries.isEmpty && !directory.hasMore {
                            Text(model.appLanguage.text("空文件夹", "Empty folder"))
                                .foregroundStyle(.secondary).padding(.vertical, 12)
                        }
                        if directory.hasMore {
                            Button(model.appLanguage.text("显示更多", "Show more")) {
                                Task { await model.loadMoreWorkspaceDirectory() }
                            }
                            .disabled(model.workspaceFileLoading)
                            .padding(.vertical, 8)
                        }
                    }
                    .padding(.horizontal, 18)
                }
            } else if document.previewKind == .markdown {
              ScrollView {
                Markdown(document.content)
                    .markdownTheme(.gitHub)
                    .markdownTextStyle {
                        FontSize(16 * appTextSize.scale)
                    }
                    .textSelection(.enabled)
                    .frame(maxWidth: 900, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(24)
                    .environment(\.openURL, OpenURLAction { url in
                        model.openTranscriptLink(url, relativeTo: document.relativePath, reviewContext: document.context)
                        return .handled
                    })
              }
            } else if document.previewKind == .text {
                ReviewTextView(text: document.content, isDiff: false, fontSize: 13 * appTextSize.scale,
                    filePath: document.relativePath,
                    initialLine: document.line, onSelection: { selection in
                        model.addReviewReference(context: document.context, file: document.relativePath, selection: selection)
                    })
            } else if document.previewKind == .visual {
                FileVisualPreview(url: document.fileURL, onLink: { url in
                    model.openTranscriptLink(url, relativeTo: document.relativePath, reviewContext: document.context)
                })
            } else {
                WorkspaceQuickLookPreview(url: document.fileURL)
            }
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .frame(minWidth: 280, minHeight: 240)
    }
}
