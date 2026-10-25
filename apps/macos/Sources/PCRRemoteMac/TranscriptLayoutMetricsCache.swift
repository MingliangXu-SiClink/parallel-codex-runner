import CoreGraphics
import CryptoKit
import Foundation

struct TranscriptLayoutMetrics: Codable, Equatable, Sendable {
    let bodyWidth: Double
    let textWidth: Double
    let textHeight: Double
    let padding: Double

    init(
        bodyWidth: CGFloat,
        textWidth: CGFloat,
        textHeight: CGFloat,
        padding: CGFloat
    ) {
        self.bodyWidth = Double(bodyWidth)
        self.textWidth = Double(textWidth)
        self.textHeight = Double(textHeight)
        self.padding = Double(padding)
    }

    var cgBodyWidth: CGFloat { CGFloat(bodyWidth) }
    var cgTextWidth: CGFloat { CGFloat(textWidth) }
    var cgTextHeight: CGFloat { CGFloat(textHeight) }
    var cgPadding: CGFloat { CGFloat(padding) }
}

struct TranscriptLayoutMetricKeyInput: Sendable {
    let kind: String
    let text: String
    let statusPresent: Bool
    let activityType: String?
    let isHistorical: Bool
    let isFinalAnswer: Bool
    let textSize: String
    let maximumBodyWidth: CGFloat
}

func transcriptLayoutMetricKey(
    _ input: TranscriptLayoutMetricKeyInput,
    schemaVersion: Int = 15,
    operatingSystemVersion: OperatingSystemVersion = ProcessInfo.processInfo.operatingSystemVersion,
    preferredLanguages: [String] = Locale.preferredLanguages,
    preferredLocalizations: [String] = Bundle.main.preferredLocalizations
) -> String {
    var digest = SHA256()
    let components = [
        "transcript-layout-v\(schemaVersion)",
        "\(operatingSystemVersion.majorVersion).\(operatingSystemVersion.minorVersion)",
        // AppKit's fallback fonts depend on the process localization. A size
        // measured in Chinese can wrap differently after launching in English,
        // even when the message and system font point size have not changed.
        preferredLanguages.joined(separator: ","),
        preferredLocalizations.joined(separator: ","),
        input.kind,
        input.text,
        input.statusPresent ? "1" : "0",
        input.activityType ?? "",
        input.isHistorical ? "1" : "0",
        input.isFinalAnswer ? "1" : "0",
        input.textSize,
        String(Double(input.maximumBodyWidth)),
    ]
    for component in components {
        let bytes = Array(component.utf8)
        var byteCount = UInt64(bytes.count).bigEndian
        withUnsafeBytes(of: &byteCount) { digest.update(bufferPointer: $0) }
        digest.update(data: Data(bytes))
    }
    return digest.finalize().map { String(format: "%02x", $0) }.joined()
}

final class TranscriptLayoutMetricsStore: @unchecked Sendable {
    static let shared = TranscriptLayoutMetricsStore.standard()

    private struct Entry: Codable, Sendable {
        let metrics: TranscriptLayoutMetrics
        let updatedAt: TimeInterval
    }

    private struct Archive: Codable, Sendable {
        let entries: [String: Entry]
    }

    private let lock = NSLock()
    private let fileURL: URL
    private let maximumEntries: Int
    private let writeQueue = DispatchQueue(
        label: "com.parallelcodexrunner.mac.transcript-layout-metrics",
        qos: .utility
    )
    private var entries: [String: Entry]
    private var generation = 0
    private var persistenceWorkItem: DispatchWorkItem?

    init(fileURL: URL, maximumEntries: Int = 12_000) {
        self.fileURL = fileURL
        self.maximumEntries = max(1, maximumEntries)
        if let data = try? Data(contentsOf: fileURL),
           let archive = try? JSONDecoder().decode(Archive.self, from: data) {
            entries = archive.entries
        } else {
            entries = [:]
        }
        trimEntries()
    }

    func metrics(forKey key: String) -> TranscriptLayoutMetrics? {
        lock.withLock { entries[key]?.metrics }
    }

    func store(_ metrics: TranscriptLayoutMetrics, forKey key: String) {
        let scheduled = lock.withLock { () -> (Int, DispatchWorkItem)? in
            if entries[key]?.metrics == metrics { return nil }
            entries[key] = Entry(
                metrics: metrics,
                updatedAt: Date().timeIntervalSince1970
            )
            trimEntries()
            generation += 1
            let scheduledGeneration = generation
            persistenceWorkItem?.cancel()
            let workItem = DispatchWorkItem { [weak self] in
                self?.persist(generation: scheduledGeneration)
            }
            persistenceWorkItem = workItem
            return (scheduledGeneration, workItem)
        }
        guard let (_, workItem) = scheduled else { return }
        writeQueue.asyncAfter(deadline: .now() + 0.8, execute: workItem)
    }

    func flush() {
        let snapshot = lock.withLock { () -> Archive in
            persistenceWorkItem?.cancel()
            persistenceWorkItem = nil
            return Archive(entries: entries)
        }
        writeQueue.sync {
            Self.write(snapshot, to: fileURL)
        }
    }

    var count: Int {
        lock.withLock { entries.count }
    }

    private func persist(generation scheduledGeneration: Int) {
        let snapshot = lock.withLock { () -> Archive? in
            guard generation == scheduledGeneration else { return nil }
            persistenceWorkItem = nil
            return Archive(entries: entries)
        }
        guard let snapshot else { return }
        Self.write(snapshot, to: fileURL)
    }

    private func trimEntries() {
        guard entries.count > maximumEntries else { return }
        let retained = entries
            .sorted { $0.value.updatedAt > $1.value.updatedAt }
            .prefix(maximumEntries)
        entries = Dictionary(uniqueKeysWithValues: retained.map { ($0.key, $0.value) })
    }

    private static func write(_ archive: Archive, to fileURL: URL) {
        guard let data = try? JSONEncoder().encode(archive) else { return }
        do {
            let directory = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try data.write(to: fileURL, options: .atomic)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: fileURL.path
            )
        } catch {
            return
        }
    }

    private static func standard() -> TranscriptLayoutMetricsStore {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        return TranscriptLayoutMetricsStore(
            fileURL: base
                .appendingPathComponent("Parallel Codex", isDirectory: true)
                .appendingPathComponent("transcript-layout-metrics.v1.json")
        )
    }
}
