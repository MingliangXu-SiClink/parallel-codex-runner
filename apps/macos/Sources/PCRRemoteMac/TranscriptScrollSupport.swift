import SwiftUI

func transcriptConversationContextID(
    selectedConversationID: String?,
    activeRunID: String?,
    workspaceLastRunID: String?
) -> String {
    for candidate in [selectedConversationID, activeRunID, workspaceLastRunID] {
        let normalized = candidate?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !normalized.isEmpty {
            return normalized
        }
    }
    return "empty"
}

func transcriptAgentContextID(
    selectedAgent: Int?,
    restoredAgent: Int?
) -> String {
    (selectedAgent ?? restoredAgent).map(String.init) ?? "history"
}

func transcriptScrollContextID(
    machineID: String,
    workspaceID: String,
    conversationID: String,
    runID: String,
    agentID: String
) -> String {
    [machineID, workspaceID, conversationID, runID, agentID].joined(separator: "\u{1f}")
}

func transcriptContextContinuesSameConversation(from old: String, to new: String) -> Bool {
    let previous = old.components(separatedBy: "\u{1f}")
    let next = new.components(separatedBy: "\u{1f}")
    return previous.count == 5 && next.count == 5
        && previous.prefix(3) == next.prefix(3)
        && previous[3] != next[3]
}

func transcriptContextChangesOnlyAgent(from old: String, to new: String) -> Bool {
    let previous = old.components(separatedBy: "\u{1f}")
    let next = new.components(separatedBy: "\u{1f}")
    return previous.count == 5 && next.count == 5
        && previous.prefix(4) == next.prefix(4)
        && previous[4] != next[4]
}

func transcriptIsNearBottom(
    distance: CGFloat,
    viewportHeight: CGFloat
) -> Bool {
    let threshold = min(120, max(72, viewportHeight * 0.12))
    return distance <= threshold
}

func transcriptFollowsLatest(
    liveValue: Bool?,
    persistedValue: Bool?
) -> Bool {
    liveValue ?? persistedValue ?? true
}

func transcriptMeasurementWidth(
    _ width: CGFloat,
    duringLiveResize: Bool,
    liveResizeStep: CGFloat = 12
) -> CGFloat {
    let width = max(1, floor(width))
    guard duringLiveResize, liveResizeStep > 1 else { return width }
    return max(liveResizeStep, floor(width / liveResizeStep) * liveResizeStep)
}

func transcriptWidthBecameUsable(
    previousWidth: CGFloat,
    currentWidth: CGFloat
) -> Bool {
    previousWidth <= 1 && currentWidth > 1
}

func transcriptRestoreWidthIsUsable(_ width: CGFloat) -> Bool {
    width.isFinite && width > 1
}

func transcriptHorizontalInsets(
    width: CGFloat,
    maximumContentWidth: CGFloat,
    minimumEdge: CGFloat
) -> (leading: CGFloat, trailing: CGFloat) {
    let minimumEdge = max(0, minimumEdge)
    let centeredEdge = floor((width - maximumContentWidth) / 2)
    let edge = max(minimumEdge, centeredEdge)
    return (edge, edge)
}

func transcriptEstimatedLiveResizeHeight(
    measuredHeight: CGFloat,
    measuredWidth: CGFloat,
    targetWidth: CGFloat
) -> CGFloat {
    guard measuredHeight > 0, measuredWidth > 0, targetWidth > 0 else {
        return max(1, ceil(measuredHeight))
    }
    guard targetWidth < measuredWidth else { return ceil(measuredHeight) }
    return ceil(measuredHeight * (measuredWidth / targetWidth) + 8)
}

enum TranscriptInlineControlPosition: Equatable, Sendable {
    case missing
    case aboveViewport
    case visible
    case belowViewport
}

func transcriptInlineControlAccessibilityHidden(
    _ position: TranscriptInlineControlPosition
) -> Bool {
    position == .aboveViewport
}

func transcriptInlineControlPosition(
    itemMinY: CGFloat,
    itemMaxY: CGFloat,
    visibleMinY: CGFloat,
    visibleMaxY: CGFloat
) -> TranscriptInlineControlPosition {
    if itemMaxY < visibleMinY { return .aboveViewport }
    if itemMinY > visibleMaxY { return .belowViewport }
    return .visible
}

func transcriptAnchorGeometryIsResolved(
    anchorIndex: Int,
    firstItemMinY: CGFloat?,
    anchorMinY: CGFloat,
    anchorHeight: CGFloat
) -> Bool {
    guard anchorHeight > 0 else { return false }
    guard anchorIndex > 0 else { return true }
    guard let firstItemMinY else { return false }
    return anchorMinY > firstItemMinY + 0.5
}

struct TranscriptViewportAnchorCandidate: Equatable, Sendable {
    let itemIndex: Int
    let minY: CGFloat
    let maxY: CGFloat
}

func transcriptPreferredViewportAnchor(
    visibleMinY: CGFloat,
    visibleMaxY: CGFloat,
    candidates: [TranscriptViewportAnchorCandidate]
) -> TranscriptViewportAnchorCandidate? {
    let visible = candidates.filter {
        $0.maxY >= visibleMinY && $0.minY <= visibleMaxY
    }
    // Prefer the first row whose leading edge is actually on screen. Anchoring
    // a very tall row that begins above the viewport keeps the row frame fixed,
    // but its visible text moves when a font-size change reflows that row.
    return visible
        .filter { $0.minY >= visibleMinY - 0.5 }
        .min { lhs, rhs in lhs.minY < rhs.minY }
        ?? visible.min { lhs, rhs in lhs.minY < rhs.minY }
}

@MainActor
final class TranscriptScrollMemory: ObservableObject {
    struct TextAnchor: Codable, Equatable, Sendable {
        let character: Int
        let lineOffset: Double
    }
    struct Snapshot {
        let offsetY: CGFloat
        let followsLatest: Bool
        let anchorID: String?
        let anchorOffset: CGFloat
        let textAnchor: TextAnchor?

        init(
            offsetY: CGFloat,
            followsLatest: Bool,
            anchorID: String? = nil,
            anchorOffset: CGFloat = 0,
            textAnchor: TextAnchor? = nil
        ) {
            self.offsetY = offsetY
            self.followsLatest = followsLatest
            self.anchorID = anchorID
            self.anchorOffset = anchorOffset
            self.textAnchor = textAnchor
        }
    }

    private struct StoredSnapshot: Codable {
        let offsetY: Double
        let followsLatest: Bool
        let anchorID: String?
        let anchorOffset: Double?
        let updatedAt: TimeInterval
        var textAnchor: TextAnchor? = nil
    }

    private var snapshots: [String: StoredSnapshot]
    private var persistenceTask: Task<Void, Never>?
    private var isFrozenForTermination = false
    private var isSuspendedForViewTeardown = false
    private let defaults: UserDefaults
    private let storageKey: String

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "pcr.transcript-scroll-memory.v1"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
        if let data = defaults.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode(
                [String: StoredSnapshot].self,
                from: data
           ) {
            snapshots = decoded
        } else {
            snapshots = [:]
        }
        trimSnapshots()
    }

    func snapshot(for context: String) -> Snapshot? {
        if snapshots[context] == nil { migrateLegacySnapshots(for: context) }
        return snapshots[context].map {
            Snapshot(
                offsetY: CGFloat($0.offsetY),
                followsLatest: $0.followsLatest,
                anchorID: $0.anchorID,
                anchorOffset: CGFloat($0.anchorOffset ?? 0),
                textAnchor: $0.textAnchor
            )
        }
    }

    func mostRecentlyUsedAgent(for conversationContext: String, runID: String? = nil) -> Int? {
        if let runID {
            migrateLegacySnapshots(for: conversationContext + "\u{1f}" + runID + "\u{1f}history")
        }
        let prefix = conversationContext + "\u{1f}" + (runID.map { $0 + "\u{1f}" } ?? "")
        return snapshots.compactMap { key, snapshot -> (Int, TimeInterval)? in
            guard key.hasPrefix(prefix),
                  let agent = Int(key.dropFirst(prefix.count)) else { return nil }
            return (agent, snapshot.updatedAt)
        }
        .max { $0.1 < $1.1 }?
        .0
    }

    func save(_ snapshot: Snapshot, for context: String) {
        guard !isFrozenForTermination, !isSuspendedForViewTeardown else { return }
        snapshots[context] = StoredSnapshot(
            offsetY: Double(snapshot.offsetY),
            followsLatest: snapshot.followsLatest,
            anchorID: snapshot.anchorID,
            anchorOffset: Double(snapshot.anchorOffset),
            updatedAt: Date().timeIntervalSince1970,
            textAnchor: snapshot.textAnchor
        )
        trimSnapshots()
        schedulePersistence()
    }

    func flush() {
        persistenceTask?.cancel()
        persistenceTask = nil
        persist()
    }

    func freezeForTermination() {
        guard !isFrozenForTermination else { return }
        isFrozenForTermination = true
        flush()
    }

    func suspendForViewTeardown() {
        guard !isFrozenForTermination, !isSuspendedForViewTeardown else { return }
        isSuspendedForViewTeardown = true
        flush()
    }

    func resumeAfterViewTeardown() {
        guard !isFrozenForTermination else { return }
        isSuspendedForViewTeardown = false
    }

    private func migrateLegacySnapshots(for context: String) {
        let parts = context.components(separatedBy: "\u{1f}")
        guard parts.count == 5 else { return }
        let conversationPrefix = parts.prefix(3).joined(separator: "\u{1f}") + "\u{1f}"
        let turnPrefix = conversationPrefix + parts[3] + "\u{1f}"
        let legacy = snapshots.filter { key, _ in
            key.hasPrefix(conversationPrefix)
                && !key.dropFirst(conversationPrefix.count).contains("\u{1f}")
        }
        guard !legacy.isEmpty else { return }
        // Preserve existing reading positions once, without lending a previous
        // turn's Agent 2 position to every future Agent 2 in this conversation.
        for (key, snapshot) in legacy {
            let target = turnPrefix + key.dropFirst(conversationPrefix.count)
            if snapshots[target] == nil { snapshots[target] = snapshot }
            snapshots.removeValue(forKey: key)
        }
        schedulePersistence()
    }

    private func trimSnapshots() {
        guard snapshots.count > Self.maximumSnapshots else { return }
        let retained = snapshots
            .sorted { $0.value.updatedAt > $1.value.updatedAt }
            .prefix(Self.maximumSnapshots)
        snapshots = Dictionary(uniqueKeysWithValues: retained.map { ($0.key, $0.value) })
    }

    private func schedulePersistence() {
        persistenceTask?.cancel()
        persistenceTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled, let self else { return }
            self.persist()
        }
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(snapshots) else { return }
        defaults.set(data, forKey: storageKey)
    }

    private static let maximumSnapshots = 96
}
