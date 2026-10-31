import CryptoKit
import Darwin
import Foundation

func workspaceSurfaceNeedsBlockingLoading(
    hydrating: Bool,
    composingNewConversation: Bool,
    hasRenderableContent: @autoclosure () -> Bool,
    hasPendingSubmission: Bool
) -> Bool {
    hydrating
        && !composingNewConversation
        && !hasPendingSubmission
        && !hasRenderableContent()
}

func runViewCacheNeedsHydration(
    activeRunID: String?,
    requestedRunID: String
) -> Bool {
    (activeRunID ?? "") != requestedRunID
}

struct WorkspaceRunViewKey: Codable, Hashable, Sendable {
    let machineID: String
    let workspaceID: String
    let runID: String

    init(machineID: String?, workspaceID: String, runID: String) {
        self.machineID = MachineContextKey.normalizeMachineID(machineID)
        self.workspaceID = workspaceID
        self.runID = runID
    }
}

func resumeConversationViewKey(
    machineID: String?,
    workspaceID: String,
    sessionID: String
) -> WorkspaceRunViewKey {
    WorkspaceRunViewKey(
        machineID: machineID,
        workspaceID: workspaceID,
        runID: "resume-session:\(sessionID.trimmingCharacters(in: .whitespacesAndNewlines))"
    )
}

struct WorkspaceRunViewSnapshot: Codable, Equatable, Sendable {
    var selectedAgent: Int?
    var transcripts: [Int: AgentTranscript]
    var conversationHistory: [ResumeHistoryEntry]
    var conversationTitle: String? = nil
    var conversationUpdatedAt: Int? = nil
    var historyLoaded: Bool? = nil
    var historyProjectionVersion: Int? = 1

    func mergingVisible(_ visible: Self, for key: WorkspaceRunViewKey) -> Self {
        var merged = validated(for: key)
        let visible = visible.validated(for: key)
        merged.selectedAgent = visible.selectedAgent
        merged.transcripts.merge(visible.transcripts) { cached, foreground in
            var newest = foreground
            if cached.cursor > foreground.cursor {
                newest.cursor = cached.cursor
                newest.hasMore = cached.hasMore
                newest.blocks = cached.blocks
                newest.eventIDs = cached.eventIDs
            }
            if let conversation = cached.conversation,
               conversation.cursor > (foreground.conversation?.cursor ?? -1) {
                newest.conversation = conversation
            }
            if let recent = cached.recentWork,
               recent.cursor > (foreground.recentWork?.cursor ?? -1) {
                newest.recentWork = recent
            }
            if cached.finalMessageID == foreground.finalMessageID,
               !cached.finalMessageID.isEmpty || cached.finalMessage == foreground.finalMessage
                || foreground.finalMessage.isEmpty,
               cached.finalMessageCursor > foreground.finalMessageCursor
                || (isTerminalAgentStatus(cached.finalMessageReadStatus)
                    && !isTerminalAgentStatus(foreground.finalMessageReadStatus)) {
                newest.copyFinalMessage(from: cached)
            }
            return newest
        }
        merged.conversationHistory = mergeConversationHistory(current: merged.conversationHistory,
            authoritative: visible.conversationHistory)
        merged.conversationTitle = visible.conversationTitle ?? merged.conversationTitle
        merged.conversationUpdatedAt = visible.conversationUpdatedAt ?? merged.conversationUpdatedAt
        if visible.historyLoaded == true { merged.historyLoaded = true }
        return merged
    }

    func validated(for key: WorkspaceRunViewKey) -> Self {
        var result = self
        if historyProjectionVersion != 1 {
            // Keep the cached conversation on screen while fetching provenance
            // from the source once. Old complete caches are not authoritative.
            result.historyLoaded = false
            result.historyProjectionVersion = 1
        }
        guard !key.runID.hasPrefix("resume-session:") else {
            result.transcripts = [:]
            return result
        }
        result.transcripts = transcripts.reduce(into: [:]) { values, entry in
            let (agent, transcript) = entry
            guard transcript.sourceRunID == key.runID,
                  transcript.sourceAgent == agent else { return }
            var validated = transcript
            // Older clients saved failed run lookups as artifact output. Keep
            // the answer and genuine Agent errors, but discard that view error.
            if validated.artifactError.trimmingCharacters(in: .whitespacesAndNewlines)
                .hasPrefix("Unknown PCR run:") {
                validated.artifactError = ""
            }
            values[agent] = validated
        }
        return result
    }
}

struct WorkspaceRunViewCacheRecord: Codable, Sendable {
    let schemaVersion: Int
    let key: WorkspaceRunViewKey
    let runSummary: RunSummary?
    var snapshot: WorkspaceRunViewSnapshot
    let updatedAt: Date

    init(
        key: WorkspaceRunViewKey,
        runSummary: RunSummary?,
        snapshot: WorkspaceRunViewSnapshot
    ) {
        schemaVersion = WorkspaceRunDiskCache.schemaVersion
        self.key = key
        self.runSummary = runSummary
        self.snapshot = snapshot
        updatedAt = Date()
    }
}

/// Stores complete structured conversations. The byte budget evicts entire
/// records and never truncates prompts, reasoning, commands, or answers.
final class WorkspaceRunDiskCache: @unchecked Sendable {
    static let schemaVersion = 1

    private let directory: URL
    private let capacity: Int
    private let maximumBytes: Int64
    private let lock = NSLock()
    private let writerLock = NSLock()
    private let encodeRecord: @Sendable (WorkspaceRunViewCacheRecord) throws -> Data
    private let decoder: JSONDecoder
    private var removedWorkspaces: [String: Date] = [:]
    private var removedMachines: [String: Date] = [:]
    private var removedAllAt: Date?
    private struct FileRevision: Codable, Equatable {
        let inode: UInt64
        let size: Int
        let modifiedSeconds: Int64
        let modifiedNanoseconds: Int64
    }
    private struct RecordIdentity: Decodable {
        let schemaVersion: Int
        let key: WorkspaceRunViewKey
    }
    private struct CatalogEntry: Codable, Equatable {
        let key: WorkspaceRunViewKey
        let revision: FileRevision
    }
    private struct Catalog: Codable, Equatable {
        var schemaVersion = 1
        var entries: [String: CatalogEntry]
    }
    private final class DecodedRecord {
        let revision: FileRevision
        let record: WorkspaceRunViewCacheRecord
        init(_ record: WorkspaceRunViewCacheRecord, revision: FileRevision) {
            self.record = record
            self.revision = revision
        }
    }
    private let decodedRecords = NSCache<NSURL, DecodedRecord>()
    private var accessDates: [URL: Date] = [:]
    private var decodeCount = 0
    private var identityDecodeCount = 0
    private var catalog: Catalog?
    private var persistedCatalog: Catalog?

    var diskDecodeCount: Int { lock.withLock { decodeCount } }
    var diskIdentityDecodeCount: Int { lock.withLock { identityDecodeCount } }

    init(
        directory: URL,
        capacity: Int = 64,
        maximumBytes: Int64 = 256 * 1024 * 1024,
        encodeRecord: @escaping @Sendable (WorkspaceRunViewCacheRecord) throws -> Data = WorkspaceRunDiskCache.encode
    ) {
        self.directory = directory
        self.capacity = max(1, capacity)
        self.maximumBytes = max(1, maximumBytes)
        decodedRecords.countLimit = 8
        decodedRecords.totalCostLimit = 32 * 1024 * 1024
        self.encodeRecord = encodeRecord
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .millisecondsSince1970
        // Server snapshots use snake_case while older cache records may mix
        // camelCase outer fields with snake_case generated conversation fields.
        decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    static func encode(_ record: WorkspaceRunViewCacheRecord) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .millisecondsSince1970
        return try encoder.encode(record)
    }

    static func standard(windowID: UUID? = nil) -> WorkspaceRunDiskCache {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        return WorkspaceRunDiskCache(
            directory: base
                .appendingPathComponent("parallel-codex-runner", isDirectory: true)
                .appendingPathComponent("remote", isDirectory: true)
                .appendingPathComponent(windowID.map { "conversation-cache-v1-\($0.uuidString)" }
                                       ?? "conversation-cache-v1", isDirectory: true)
        )
    }

    func record(for key: WorkspaceRunViewKey) -> WorkspaceRunViewCacheRecord? {
        lock.withLock {
            let file = fileURL(for: key)
            guard let record = readRecordUnlocked(file, expectedKey: key) else {
                return nil
            }
            // Recency is not a content revision. Touching mtime here made
            // every subsequent navigation decode the same history again.
            accessDates[file] = Date()
            return record
        }
    }

    func records(
        machineID: String?,
        workspaceID: String
    ) -> [WorkspaceRunViewCacheRecord] {
        records(machineID: machineID, workspaceIDs: [workspaceID])
    }

    func records(machineID: String?, workspaceIDs: Set<String>) -> [WorkspaceRunViewCacheRecord] {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        return lock.withLock {
            let files = cacheFilesUnlocked()
            loadCatalogUnlocked()
            let names = Set(files.map(\.lastPathComponent))
            let retainedIdentities = catalog?.entries.filter { names.contains($0.key) } ?? [:]
            catalog?.entries = retainedIdentities
            let records = files
                .compactMap { file -> WorkspaceRunViewCacheRecord? in
                    guard let key = readIdentityUnlocked(file),
                          key.machineID == resolvedMachineID,
                          workspaceIDs.contains(key.workspaceID) else { return nil }
                    return readRecordUnlocked(file, expectedKey: key)
                }
                .sorted { $0.updatedAt > $1.updatedAt }
            persistCatalogUnlocked()
            return records
        }
    }

    @discardableResult
    func put(_ record: WorkspaceRunViewCacheRecord) -> Bool {
        writerLock.withLock {
            do {
                let target = fileURL(for: record.key)
                while true {
                    let baseline = lock.withLock { () -> (Bool, WorkspaceRunViewCacheRecord?, FileRevision?) in
                        guard !wasRemovedUnlocked(record) else { return (false, nil, nil) }
                        let current = readRecordUnlocked(target, expectedKey: record.key)
                        return (true, current, revisionUnlocked(target))
                    }
                    guard baseline.0 else { return false }
                    if let current = baseline.1, current.updatedAt > record.updatedAt { return true }
                    var retained = record
                    if let current = baseline.1 {
                        retained.snapshot = current.snapshot.mergingVisible(record.snapshot, for: record.key)
                    }
                    // Encoding a large immutable snapshot must not hold the
                    // read lock. Publish only against the baseline we merged.
                    let data = try encodeRecord(retained)
                    try FileManager.default.createDirectory(at: directory,
                        withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                    let staging = directory.appendingPathComponent(".\(UUID().uuidString).pcrconversation-write")
                    defer { try? FileManager.default.removeItem(at: staging) }
                    try data.write(to: staging, options: [.withoutOverwriting, .completeFileProtection])
                    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: staging.path)
                    let preparedRevision = revisionUnlocked(staging)
                    let published = try lock.withLock { () throws -> Bool? in
                        guard !wasRemovedUnlocked(record) else { return false }
                        guard revisionUnlocked(target) == baseline.2 else { return nil }
                        guard rename(staging.path, target.path) == 0 else {
                            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
                        }
                        rememberUnlocked(retained, file: target, expectedRevision: preparedRevision)
                        pruneUnlocked(protectedURL: target)
                        persistCatalogUnlocked()
                        return true
                    }
                    if let published { return published }
                }
            } catch {
                // A cache write must never block the live pcrd conversation.
                return false
            }
        }
    }

    private func wasRemovedUnlocked(_ record: WorkspaceRunViewCacheRecord) -> Bool {
        let workspaceRemoval = removedWorkspaces[removalWorkspaceKey(
            machineID: record.key.machineID, workspaceID: record.key.workspaceID)]
        return [removedAllAt, workspaceRemoval, removedMachines[record.key.machineID]]
            .contains { $0.map { record.updatedAt <= $0 } == true }
    }

    func removeWorkspace(machineID: String?, workspaceID: String) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        let removedAt = Date()
        lock.withLock {
            removedWorkspaces[
                removalWorkspaceKey(
                    machineID: resolvedMachineID,
                    workspaceID: workspaceID
                )
            ] = removedAt
            removeWhereUnlocked {
                $0.key.machineID == resolvedMachineID
                    && $0.key.workspaceID == workspaceID
                    && $0.updatedAt <= removedAt
            }
        }
    }

    func removeMachine(_ machineID: String?) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        let removedAt = Date()
        lock.withLock {
            removedMachines[resolvedMachineID] = removedAt
            removeWhereUnlocked {
                $0.key.machineID == resolvedMachineID && $0.updatedAt <= removedAt
            }
        }
    }

    func removeAll() {
        lock.withLock {
            removedAllAt = Date()
            decodedRecords.removeAllObjects()
            accessDates.removeAll()
            catalog = Catalog(entries: [:])
            persistedCatalog = nil
            cacheFilesUnlocked().forEach { try? FileManager.default.removeItem(at: $0) }
            try? FileManager.default.removeItem(at: catalogURL)
        }
    }

    private func removeWhereUnlocked(
        _ predicate: (WorkspaceRunViewCacheRecord) -> Bool
    ) {
        cacheFilesUnlocked().forEach { file in
            guard let record = readRecordUnlocked(file, expectedKey: nil) else { return }
            if predicate(record) {
                try? FileManager.default.removeItem(at: file)
                accessDates.removeValue(forKey: file)
                catalog?.entries.removeValue(forKey: file.lastPathComponent)
            }
        }
        persistCatalogUnlocked()
    }

    private func removalWorkspaceKey(machineID: String, workspaceID: String) -> String {
        machineID + "\u{1f}" + workspaceID
    }

    private func readIdentityUnlocked(_ file: URL) -> WorkspaceRunViewKey? {
        guard let revision = revisionUnlocked(file) else { return nil }
        if let cached = decodedRecords.object(forKey: file as NSURL), cached.revision == revision {
            return cached.record.key
        }
        loadCatalogUnlocked()
        if let entry = catalog?.entries[file.lastPathComponent], entry.revision == revision,
           fileURL(for: entry.key).standardizedFileURL == file.standardizedFileURL { return entry.key }
        // Even decoding only the key parses the whole JSON document. Keep a
        // rebuildable identity catalog so cold navigation never scans unrelated
        // transcript bodies unless their on-disk generation has changed.
        identityDecodeCount += 1
        guard let data = try? Data(contentsOf: file, options: .mappedIfSafe),
              let identity = try? decoder.decode(RecordIdentity.self, from: data),
              identity.schemaVersion == Self.schemaVersion,
              fileURL(for: identity.key).standardizedFileURL == file.standardizedFileURL,
              revisionUnlocked(file) == revision else {
            catalog?.entries.removeValue(forKey: file.lastPathComponent)
            return nil
        }
        catalog?.entries[file.lastPathComponent] = CatalogEntry(key: identity.key, revision: revision)
        return identity.key
    }

    private func readRecordUnlocked(
        _ file: URL,
        expectedKey: WorkspaceRunViewKey?
    ) -> WorkspaceRunViewCacheRecord? {
        guard let revision = revisionUnlocked(file) else {
            decodedRecords.removeObject(forKey: file as NSURL)
            return nil
        }
        // An atomic cache write replaces the inode. Reuse only these exact
        // bytes. External writes and deletions remain visible before merging.
        if let cached = decodedRecords.object(forKey: file as NSURL), cached.revision == revision,
           expectedKey == nil || cached.record.key == expectedKey {
            return cached.record
        }
        do {
            let data = try Data(contentsOf: file, options: .mappedIfSafe)
            decodeCount += 1
            let record = try decoder.decode(WorkspaceRunViewCacheRecord.self, from: data)
            guard record.schemaVersion == Self.schemaVersion,
                  fileURL(for: record.key).standardizedFileURL == file.standardizedFileURL,
                  expectedKey == nil || record.key == expectedKey else {
                throw CocoaError(.fileReadCorruptFile)
            }
            guard revisionUnlocked(file) == revision else { return nil }
            rememberUnlocked(record, file: file, expectedRevision: revision)
            return record
        } catch {
            decodedRecords.removeObject(forKey: file as NSURL)
            if revisionUnlocked(file) == revision {
                try? FileManager.default.removeItem(at: file)
                catalog?.entries.removeValue(forKey: file.lastPathComponent)
            }
            return nil
        }
    }

    private func revisionUnlocked(_ file: URL) -> FileRevision? {
        var attributes = stat()
        guard lstat(file.path, &attributes) == 0,
              attributes.st_mode & S_IFMT == S_IFREG else { return nil }
        return FileRevision(inode: UInt64(attributes.st_ino), size: Int(attributes.st_size),
            modifiedSeconds: Int64(attributes.st_mtimespec.tv_sec),
            modifiedNanoseconds: Int64(attributes.st_mtimespec.tv_nsec))
    }

    private func rememberUnlocked(_ record: WorkspaceRunViewCacheRecord, file: URL, expectedRevision: FileRevision?) {
        guard let revision = revisionUnlocked(file), revision == expectedRevision else { return }
        decodedRecords.setObject(DecodedRecord(record, revision: revision), forKey: file as NSURL, cost: revision.size)
        loadCatalogUnlocked()
        catalog?.entries[file.lastPathComponent] = CatalogEntry(key: record.key, revision: revision)
    }

    private var catalogURL: URL { directory.appendingPathComponent("runs.pcrcacheindex") }

    private func loadCatalogUnlocked() {
        guard catalog == nil else { return }
        if let data = try? Data(contentsOf: catalogURL),
           let saved = try? decoder.decode(Catalog.self, from: data), saved.schemaVersion == 1 {
            catalog = saved
            persistedCatalog = saved
        } else {
            catalog = Catalog(entries: [:])
        }
    }

    private func persistCatalogUnlocked() {
        guard let catalog, catalog != persistedCatalog,
              FileManager.default.fileExists(atPath: directory.path) else { return }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .millisecondsSince1970
        guard let data = try? encoder.encode(catalog) else { return }
        do {
            try data.write(to: catalogURL, options: [.atomic, .completeFileProtection])
            persistedCatalog = catalog
        } catch { return }
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: catalogURL.path)
    }

    private func fileURL(for key: WorkspaceRunViewKey) -> URL {
        let identity = [key.machineID, key.workspaceID, key.runID].joined(separator: "\u{1f}")
        let digest = SHA256.hash(data: Data(identity.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return directory.appendingPathComponent("\(digest).pcrconversation")
    }

    private func cacheFilesUnlocked() -> [URL] {
        (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey],
            options: [.skipsHiddenFiles]
        ))?.filter { $0.pathExtension == "pcrconversation" } ?? []
    }

    private func pruneUnlocked(protectedURL: URL) {
        let files = cacheFilesUnlocked().map { url -> (URL, Date, Int64) in
            let values = try? url.resourceValues(forKeys: [
                .contentModificationDateKey,
                .fileSizeKey,
            ])
            return (
                url,
                max(accessDates[url] ?? .distantPast, values?.contentModificationDate ?? .distantPast),
                Int64(values?.fileSize ?? 0)
            )
        }.sorted { $0.1 > $1.1 }
        let existingFiles = Set(files.map { $0.0 })
        accessDates = accessDates.filter { existingFiles.contains($0.key) }

        var retained = 0
        var retainedBytes: Int64 = 0
        for (url, _, size) in files {
            let mustKeep = url == protectedURL
            let fits = retained < capacity && retainedBytes + size <= maximumBytes
            if mustKeep || fits {
                retained += 1
                retainedBytes += size
            } else {
                try? FileManager.default.removeItem(at: url)
                catalog?.entries.removeValue(forKey: url.lastPathComponent)
            }
        }
    }
}

private extension NSLock {
    func withLock<T>(_ operation: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try operation()
    }
}

func selectedAgentAnswerNeedsRefresh(
    run: RunSummary?,
    selectedAgent: Int?,
    transcripts: [Int: AgentTranscript]
) -> Bool {
    guard let run, let selectedAgent,
          let agent = run.agents.first(where: { $0.agent == selectedAgent }),
          agent.isTerminal else { return false }
    guard let transcript = transcripts[selectedAgent],
          transcript.sourceRunID == run.runId,
          transcript.sourceAgent == selectedAgent else { return true }
    return transcript.finalMessageHasMore
        || transcript.finalMessageReadStatus != agent.normalizedStatus
}

func selectedAgentDetailNeedsRefresh(
    run: RunSummary?,
    selectedAgent: Int?,
    transcripts: [Int: AgentTranscript]
) -> Bool {
    guard let run,
          let selectedAgent,
          run.agents.contains(where: { $0.agent == selectedAgent }) else {
        return false
    }
    guard let transcript = transcripts[selectedAgent],
          transcript.sourceRunID == run.runId,
          transcript.sourceAgent == selectedAgent else { return true }
    return transcript.hasMore
        || transcript.finalMessageHasMore
        || transcript.cursor < run.eventCount
        || selectedAgentAnswerNeedsRefresh(
            run: run, selectedAgent: selectedAgent, transcripts: transcripts
        )
}

func visibleRunRefreshNeedsReload(
    current: RunSummary?,
    remote: RunSummary,
    selectedAgent: Int?,
    transcripts: [Int: AgentTranscript]
) -> Bool {
    guard let current, current.runId == remote.runId else { return true }
    var normalizedRemote = remote
    normalizedRemote.preserveTerminalAgentStates(from: current)
    normalizedRemote.reconcileTerminalAgentStates()
    return normalizedRemote != current
        || selectedAgentDetailNeedsRefresh(
            run: normalizedRemote,
            selectedAgent: selectedAgent,
            transcripts: transcripts
        )
}

final class WorkspaceRunViewStateStore: @unchecked Sendable {
    private struct Entry {
        var snapshot: WorkspaceRunViewSnapshot
        var runSummary: RunSummary?
        var accessOrder: UInt64
    }

    private struct PendingPersistence {
        let token: UUID
        let record: WorkspaceRunViewCacheRecord
        let task: Task<Void, Never>
    }

    private let capacity: Int
    private let diskCache: WorkspaceRunDiskCache?
    private let memoryLock = NSLock()
    private var entries: [WorkspaceRunViewKey: Entry] = [:]
    private var readyRuns: Set<WorkspaceRunViewKey> = []
    private var pendingPersistence: [WorkspaceRunViewKey: PendingPersistence] = [:]
    private var nextAccessOrder: UInt64 = 0

    init(
        capacity: Int = 12,
        diskCache: WorkspaceRunDiskCache? = nil
    ) {
        self.capacity = max(1, capacity)
        self.diskCache = diskCache
    }

    func snapshot(for key: WorkspaceRunViewKey) -> WorkspaceRunViewSnapshot? {
        memoryLock.withLock {
            guard var entry = entries[key] else { return nil }
            entry.accessOrder = advanceAccessOrder()
            entries[key] = entry
            return entry.snapshot
        }
    }

    func retainReadyRuns(_ runs: Set<WorkspaceRunViewKey>) {
        memoryLock.withLock {
            readyRuns = runs
            evictLeastRecentlyUsedIfNeeded()
        }
    }

    /// Navigation reads only warm state. Disk discovery belongs to the reader.
    func cachedRecords(machineID: String?, workspaceID: String) -> [WorkspaceRunViewCacheRecord] {
        let machineID = MachineContextKey.normalizeMachineID(machineID)
        return memoryLock.withLock {
            var records = pendingPersistence.reduce(into: [WorkspaceRunViewKey: WorkspaceRunViewCacheRecord]()) {
                if $1.key.machineID == machineID, $1.key.workspaceID == workspaceID {
                    $0[$1.key] = $1.value.record
                }
            }
            for (key, entry) in entries where key.machineID == machineID && key.workspaceID == workspaceID {
                records[key] = WorkspaceRunViewCacheRecord(
                    key: key, runSummary: entry.runSummary, snapshot: entry.snapshot
                )
            }
            return Array(records.values)
        }
    }

    func put(
        _ snapshot: WorkspaceRunViewSnapshot,
        runSummary: RunSummary? = nil,
        for key: WorkspaceRunViewKey
    ) {
        let snapshot = snapshot.validated(for: key)
        let changed = memoryLock.withLock { () -> Bool in
            if var existing = entries[key],
               existing.snapshot == snapshot,
               existing.runSummary == runSummary {
                existing.accessOrder = advanceAccessOrder()
                entries[key] = existing
                return false
            }
            entries[key] = Entry(
                snapshot: snapshot,
                runSummary: runSummary,
                accessOrder: advanceAccessOrder()
            )
            evictLeastRecentlyUsedIfNeeded()
            return true
        }
        guard changed else { return }
        if let diskCache {
            let record = WorkspaceRunViewCacheRecord(
                key: key,
                runSummary: runSummary,
                snapshot: snapshot
            )
            let token = UUID()
            memoryLock.withLock {
                pendingPersistence.removeValue(forKey: key)?.task.cancel()
                let task = Task.detached(priority: .utility) { [weak self] in
                    try? await Task.sleep(for: .milliseconds(250))
                    guard !Task.isCancelled,
                          self?.isPersistenceCurrent(key: key, token: token) == true else {
                        return
                    }
                    await ClientBlockingIO.cacheWrite { [weak self] in
                        guard self?.isPersistenceCurrent(key: key, token: token) == true else { return }
                        diskCache.put(record)
                    }
                    self?.finishPersistence(key: key, token: token)
                }
                pendingPersistence[key] = PendingPersistence(
                    token: token,
                    record: record,
                    task: task
                )
            }
        }
    }

    /// Receipt settlement must survive a restart even when its page is not open.
    func persistAcceptedRun(
        _ run: RunSummary,
        for key: WorkspaceRunViewKey,
        carriedHistory: [ResumeHistoryEntry] = []
    ) async -> Bool {
        guard let diskCache, run.runId == key.runID else { return false }
        let cached = await hydrate(for: key)
        let matches: @Sendable (RunSummary) -> Bool = {
            $0.runId == run.runId && sameConversationWorkspace($0.workspace, run.workspace)
                && $0.prompt == run.prompt && $0.continuedFromRunId == run.continuedFromRunId
                && $0.continuedFromAgent == run.continuedFromAgent
                && ($0.config.submissionCommand == nil || $0.config.submissionCommand == run.config.submissionCommand)
        }
        guard cached?.runSummary.map(matches) != false else { return false }
        var snapshot = cached?.snapshot ?? WorkspaceRunViewSnapshot(
            selectedAgent: nil, transcripts: [:], conversationHistory: [])
        snapshot.conversationHistory = mergeConversationHistory(
            current: carriedHistory, authoritative: snapshot.conversationHistory)
        let summary = cached?.runSummary.map { run.isOlderProjection(than: $0) ? $0 : run } ?? run
        // The accepted destination owns its input and source history even when
        // another workspace is visible. Commit before retiring the outbox.
        put(snapshot, runSummary: summary, for: key)
        let record = WorkspaceRunViewCacheRecord(key: key, runSummary: summary, snapshot: snapshot)
        guard await ClientBlockingIO.cacheWrite({ [self] in
            // A newer answer may arrive while this write waits for the I/O
            // queue. Persist that page before consuming its durable receipt.
            let latest = warmRecord(for: key) ?? record
            guard latest.runSummary.map(matches) != false else { return false }
            return diskCache.put(WorkspaceRunViewCacheRecord(key: key,
                runSummary: latest.runSummary ?? run, snapshot: latest.snapshot))
        }) else { return false }
        // Hydration fills missing entries without overwriting a newer page or
        // selection that arrived while this durable write was in flight.
        return await hydrate(for: key) != nil
    }

    @discardableResult
    func flushPendingWrites(timeout: TimeInterval = 1.5) -> Bool {
        guard let diskCache else { return true }
        let records = memoryLock.withLock { () -> [WorkspaceRunViewCacheRecord] in
            let pending = Array(pendingPersistence.values)
            pending.forEach { $0.task.cancel() }
            pendingPersistence.removeAll()
            return pending.map(\.record)
        }
        guard !records.isEmpty else { return true }
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            records.forEach { _ = diskCache.put($0) }
            group.leave()
        }
        return group.wait(timeout: .now() + timeout) == .success
    }

    func hydrate(for key: WorkspaceRunViewKey) async -> WorkspaceRunViewCacheRecord? {
        let warm = warmRecord(for: key)
        if warm?.snapshot.historyLoaded == true { return warm }
        guard let diskCache else { return warm }
        let record = await ClientBlockingIO.cache {
            diskCache.record(for: key)
        }
        let validatedRecord = record.map { record in
            var result = record
            result.snapshot = result.snapshot.validated(for: key)
            return result
        }
        return memoryLock.withLock {
            if var entry = entries[key] {
                if let validatedRecord {
                    entry.snapshot = validatedRecord.snapshot.mergingVisible(entry.snapshot, for: key)
                }
                entry.accessOrder = advanceAccessOrder()
                entries[key] = entry
                return WorkspaceRunViewCacheRecord(
                    key: key, runSummary: entry.runSummary, snapshot: entry.snapshot
                )
            }
            if let pending = pendingPersistence[key] {
                var record = pending.record
                if let validatedRecord {
                    record.snapshot = validatedRecord.snapshot.mergingVisible(record.snapshot, for: key)
                }
                entries[key] = Entry(snapshot: record.snapshot, runSummary: record.runSummary,
                    accessOrder: advanceAccessOrder())
                evictLeastRecentlyUsedIfNeeded()
                return record
            }
            guard let validatedRecord else { return nil }
            entries[key] = Entry(
                snapshot: validatedRecord.snapshot,
                runSummary: validatedRecord.runSummary,
                accessOrder: advanceAccessOrder()
            )
            evictLeastRecentlyUsedIfNeeded()
            return validatedRecord
        }
    }

    func hydrateWorkspace(
        machineID: String?,
        workspaceID: String
    ) async -> [WorkspaceRunViewCacheRecord] {
        await hydrateWorkspaces(machineID: machineID, workspaceIDs: [workspaceID])
    }

    func hydrateWorkspaces(
        machineID: String?, workspaceIDs: Set<String>
    ) async -> [WorkspaceRunViewCacheRecord] {
        guard let diskCache else {
            let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
            return memoryLock.withLock {
                entries.compactMap { key, entry in
                    guard key.machineID == resolvedMachineID,
                          workspaceIDs.contains(key.workspaceID) else { return nil }
                    return WorkspaceRunViewCacheRecord(
                        key: key,
                        runSummary: entry.runSummary,
                        snapshot: entry.snapshot
                    )
                }
            }
        }
        let records = await ClientBlockingIO.cache {
            diskCache.records(machineID: machineID, workspaceIDs: workspaceIDs)
        }.map { record in
            var record = record
            record.snapshot = record.snapshot.validated(for: record.key)
            return record
        }
        return memoryLock.withLock {
            // Preserve newer selection/output without discarding older cached
            // history that the warm projection has not loaded yet.
            var merged = Dictionary(uniqueKeysWithValues: records.map { ($0.key, $0) })
            let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
            for (key, pending) in pendingPersistence where key.machineID == resolvedMachineID
                && workspaceIDs.contains(key.workspaceID) {
                var record = pending.record
                if let stored = merged[key] {
                    record.snapshot = stored.snapshot.mergingVisible(record.snapshot, for: key)
                }
                merged[key] = record
            }
            for (key, entry) in entries where key.machineID == resolvedMachineID
                && workspaceIDs.contains(key.workspaceID) {
                merged[key] = WorkspaceRunViewCacheRecord(key: key, runSummary: entry.runSummary,
                    snapshot: merged[key]?.snapshot.mergingVisible(entry.snapshot, for: key) ?? entry.snapshot)
            }
            for record in records.reversed() {
                let preferred = merged[record.key] ?? record
                entries[record.key] = Entry(
                    snapshot: preferred.snapshot,
                    runSummary: preferred.runSummary,
                    accessOrder: advanceAccessOrder()
                )
                evictLeastRecentlyUsedIfNeeded()
            }
            return merged.values.sorted { $0.updatedAt > $1.updatedAt }
        }
    }

    func removeWorkspace(machineID: String?, workspaceID: String) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        memoryLock.withLock {
            entries = entries.filter { key, _ in
                key.machineID != resolvedMachineID || key.workspaceID != workspaceID
            }
            let removed = pendingPersistence.filter { key, _ in
                key.machineID == resolvedMachineID && key.workspaceID == workspaceID
            }
            removed.values.forEach { $0.task.cancel() }
            pendingPersistence = pendingPersistence.filter { key, _ in
                key.machineID != resolvedMachineID || key.workspaceID != workspaceID
            }
        }
        if let diskCache {
            Task(priority: .utility) {
                await ClientBlockingIO.cacheWrite {
                    diskCache.removeWorkspace(
                        machineID: resolvedMachineID,
                        workspaceID: workspaceID
                    )
                }
            }
        }
    }

    func removeMachine(_ machineID: String?) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        memoryLock.withLock {
            entries = entries.filter { $0.key.machineID != resolvedMachineID }
            let removed = pendingPersistence.filter {
                $0.key.machineID == resolvedMachineID
            }
            removed.values.forEach { $0.task.cancel() }
            pendingPersistence = pendingPersistence.filter {
                $0.key.machineID != resolvedMachineID
            }
        }
        if let diskCache {
            Task(priority: .utility) {
                await ClientBlockingIO.cacheWrite { diskCache.removeMachine(resolvedMachineID) }
            }
        }
    }

    var count: Int { memoryLock.withLock { entries.count } }

    private func warmRecord(for key: WorkspaceRunViewKey) -> WorkspaceRunViewCacheRecord? {
        memoryLock.withLock {
            guard var entry = entries[key] else { return pendingPersistence[key]?.record }
            entry.snapshot = entry.snapshot.validated(for: key)
            entry.accessOrder = advanceAccessOrder()
            entries[key] = entry
            return WorkspaceRunViewCacheRecord(key: key, runSummary: entry.runSummary, snapshot: entry.snapshot)
        }
    }

    private func finishPersistence(key: WorkspaceRunViewKey, token: UUID) {
        memoryLock.withLock {
            guard pendingPersistence[key]?.token == token else { return }
            pendingPersistence.removeValue(forKey: key)
        }
    }

    private func isPersistenceCurrent(key: WorkspaceRunViewKey, token: UUID) -> Bool {
        memoryLock.withLock { pendingPersistence[key]?.token == token }
    }

    private func advanceAccessOrder() -> UInt64 {
        if nextAccessOrder == UInt64.max {
            let orderedKeys = entries.keys.sorted {
                (entries[$0]?.accessOrder ?? 0) < (entries[$1]?.accessOrder ?? 0)
            }
            for (index, key) in orderedKeys.enumerated() {
                entries[key]?.accessOrder = UInt64(index)
            }
            nextAccessOrder = UInt64(entries.count)
        }
        defer { nextAccessOrder += 1 }
        return nextAccessOrder
    }

    private func evictLeastRecentlyUsedIfNeeded() {
        while entries.count > capacity,
              let oldest = entries.filter({ !readyRuns.contains($0.key)
                  && $0.value.runSummary?.isWorking != true }).min(by: {
                  $0.value.accessOrder < $1.value.accessOrder
              })?.key {
            entries.removeValue(forKey: oldest)
        }
    }
}
