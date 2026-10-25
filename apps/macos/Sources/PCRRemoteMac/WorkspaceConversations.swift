import Foundation

func sameConversationWorkspace(_ path: String, _ workspacePath: String) -> Bool {
    // Paths belong to the connected machine, not necessarily this Mac. Do not
    // resolve symlinks, fold case, or treat a parent directory as a match.
    func withoutTrailingSlash(_ value: String) -> Substring {
        var result = value[...]
        while result.count > 1 && result.last == "/" { result.removeLast() }
        return result
    }
    return !path.isEmpty && !workspacePath.isEmpty
        && withoutTrailingSlash(path) == withoutTrailingSlash(workspacePath)
}

struct WorkspaceConversationSummary: Identifiable, Equatable, Sendable {
    let id: String
    var title: String
    let runs: [RunSummary]
    let resumeSession: ResumeSession?
    var unavailableRunIDs: Set<String> = []

    var latestRun: RunSummary? { runs.last }
    var latestRunID: String { latestRun?.runId ?? "" }
    var updatedAt: String {
        if opensResumeHistory, let updated = resumeSession?.updatedAt {
            return Date(timeIntervalSince1970: normalizedEpoch(updated))
                .formatted(.iso8601)
        }
        return latestRun?.updatedAt ?? ""
    }
    var turnCount: Int {
        if runs.isEmpty { return 1 }
        guard hasReliableTurnIndexes else { return runs.count }
        return max(runs.count, (runs.compactMap(\.turnIndex).max() ?? -1) + 1)
    }
    var isWorking: Bool { !unavailableRunIDs.contains(latestRunID) && latestRun?.isWorking == true }
    var opensResumeHistory: Bool {
        guard let resumeSession else { return false }
        guard let latestRun else { return true }
        if unavailableRunIDs.contains(latestRun.runId) { return true }
        guard !latestRun.isWorking,
              !latestRun.isReviewable,
              !latestRun.hasUnresolvedFinalization else { return false }
        guard let sessionUpdated = resumeSession.updatedAt,
              let runUpdated = parseInternetDate(latestRun.updatedAt) else { return false }
        return normalizedEpoch(sessionUpdated) > runUpdated.timeIntervalSince1970 + 1
    }

    func turnNumber(for runID: String) -> Int? {
        guard let index = runs.firstIndex(where: { $0.runId == runID }) else {
            return nil
        }
        if hasReliableTurnIndexes, let turnIndex = runs[index].turnIndex {
            return turnIndex + 1
        }
        return index + 1
    }

    private var hasReliableTurnIndexes: Bool {
        let values = runs.compactMap(\.turnIndex)
        return values.count == runs.count && Set(values).count == values.count
    }

    var relativeUpdatedText: String {
        relativeUpdatedText(language: .english)
    }

    func relativeUpdatedText(language: AppLanguage) -> String {
        guard let date = parseInternetDate(updatedAt) else {
            return language.text("最近", "Recent")
        }
        if Date().timeIntervalSince(date) < 60 {
            return language.text("刚刚", "Just now")
        }
        let relative = RelativeDateTimeFormatter()
        relative.locale = language.locale
        relative.unitsStyle = .short
        return relative.localizedString(for: date, relativeTo: Date())
    }

    private func normalizedEpoch(_ value: Int) -> TimeInterval {
        let raw = TimeInterval(value)
        return raw > 10_000_000_000 ? raw / 1_000 : raw
    }
}

extension RunSummary {
    var conversationKey: String {
        let value = conversationId?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !value.isEmpty, value != runId || turnIndex != nil { return value }
        if let resume = usableResumeSessionID(config.resumeSessionId ?? "") {
            return resume
        }
        if let promoted = usableResumeSessionID(sessionPromotion?.sessionId ?? "") {
            return promoted
        }
        return value.isEmpty ? runId : value
    }
}

func workspaceConversationSummaries(
    from runs: [RunSummary],
    resumeSessions: [ResumeSession] = [],
    unavailableRunIDs: Set<String> = []
) -> [WorkspaceConversationSummary] {
    let uniqueRuns = Dictionary(
        runs.map { ($0.runId, $0) },
        uniquingKeysWith: { _, newer in newer }
    ).values
    let groups = conversationRunGroups(Array(uniqueRuns))
    var claimedSessionIDs = Set<String>()
    var summaries = groups
        .map { conversationID, values in
            let ordered = values.sorted(by: runPrecedes)
            // Prefer the adopted output session over the session used as the
            // input to an older turn. Array/dictionary order is not lineage.
            let session = ordered.reversed().lazy.compactMap { run in
                [run.sessionPromotion?.sessionId, run.config.resumeSessionId]
                    .compactMap { usableResumeSessionID($0 ?? "") }
                    .compactMap { id in resumeSessions.first { $0.sessionId == id } }
                    .first
            }.first
            if let session { claimedSessionIDs.insert(session.sessionId) }
            return WorkspaceConversationSummary(
                id: conversationID,
                title: preferredConversationTitle(from: ordered.map(\.prompt)),
                runs: ordered,
                resumeSession: session,
                unavailableRunIDs: unavailableRunIDs.intersection(ordered.map(\.runId))
            )
        }
    summaries += resumeSessions
        .filter { !claimedSessionIDs.contains($0.sessionId) }
        .map { session in
            WorkspaceConversationSummary(
                id: "resume:\(session.sessionId)",
                title: conversationTitle(session.displayTitle),
                runs: [],
                resumeSession: session
            )
        }
    let activityDates = Dictionary(uniqueKeysWithValues: summaries.map {
        ($0.id, parseInternetDate($0.updatedAt) ?? .distantPast)
    })
    return summaries
        .sorted { left, right in
            let leftDate = activityDates[left.id] ?? .distantPast
            let rightDate = activityDates[right.id] ?? .distantPast
            if leftDate != rightDate {
                return leftDate > rightDate
            }
            return left.id > right.id
        }
}

func conversationRefreshMayOpenLatestTurn(
    visibleRunID: String?,
    previousLatestRunID: String?,
    latestRunID: String,
    opensResumeHistory: Bool = false
) -> Bool {
    !opensResumeHistory
        && (visibleRunID == nil
        || visibleRunID == latestRunID
        || (previousLatestRunID != nil && visibleRunID == previousLatestRunID))
}

func conversationSelectionContainsRun(
    _ conversations: [WorkspaceConversationSummary],
    selectedConversationID: String?,
    runID: String
) -> Bool {
    guard let selectedConversationID,
          let conversation = conversationMatchingNavigationID(
              selectedConversationID,
              in: conversations
          ) else { return false }
    return conversation.runs.contains { $0.runId == runID }
}

func conversationMatchingNavigationID(
    _ navigationID: String,
    in conversations: [WorkspaceConversationSummary]
) -> WorkspaceConversationSummary? {
    if let exact = conversations.first(where: {
        $0.id == navigationID || $0.runs.contains { $0.runId == navigationID }
    }) {
        return exact
    }

    let sessionID = navigationID.hasPrefix("resume:")
        ? String(navigationID.dropFirst("resume:".count))
        : navigationID
    guard !sessionID.isEmpty else { return nil }
    return conversations.first { conversation in
        conversation.resumeSession?.sessionId == sessionID
            || conversation.runs.contains { run in
                conversationSessionIDs(run).contains(sessionID)
            }
    }
}

func cachedResumeConversationRecord(
    from records: [WorkspaceRunViewCacheRecord],
    navigationID: String?
) -> WorkspaceRunViewCacheRecord? {
    guard let navigationID, !navigationID.isEmpty else { return nil }
    let sessionID = navigationID.hasPrefix("resume:")
        ? String(navigationID.dropFirst("resume:".count))
        : navigationID
    if let exact = records.first(where: {
        $0.key.runID == "resume-session:\(sessionID)"
    }) {
        return exact
    }

    // Navigation persists a Conversation or Turn ID, not necessarily its
    // promoted Codex session ID. Resolve the same lineage used by the live index.
    guard let conversation = conversationMatchingNavigationID(
        navigationID,
        in: workspaceConversationSummaries(from: records.compactMap(\.runSummary))
    ) else { return nil }
    let sessionKeys = Set(conversation.runs.flatMap(conversationSessionIDs).map {
        "resume-session:\($0)"
    })
    return records.filter { sessionKeys.contains($0.key.runID) }.max { left, right in
        func activityTime(_ record: WorkspaceRunViewCacheRecord) -> Int {
            let value = record.snapshot.conversationUpdatedAt ?? 0
            return value > 10_000_000_000 ? value / 1_000 : value
        }
        let leftTime = activityTime(left)
        let rightTime = activityTime(right)
        if leftTime != rightTime { return leftTime < rightTime }
        return left.key.runID < right.key.runID
    }
}

private func conversationRunGroups(
    _ runs: [RunSummary]
) -> [String: [RunSummary]] {
    guard !runs.isEmpty else { return [:] }
    let runsByID = Dictionary(uniqueKeysWithValues: runs.map { ($0.runId, $0) })
    var parent = Dictionary(uniqueKeysWithValues: runs.map { ($0.runId, $0.runId) })

    func root(_ runID: String) -> String {
        var value = runID
        while let next = parent[value], next != value { value = next }
        return value
    }

    func join(_ left: String, _ right: String) {
        let leftRoot = root(left)
        let rightRoot = root(right)
        guard leftRoot != rightRoot else { return }
        parent[rightRoot] = leftRoot
    }

    var conversationOwners: [String: String] = [:]
    var sessionOwners: [String: String] = [:]
    for run in runs {
        let conversationID = normalizedConversationID(run.conversationId)
        if let owner = conversationID.flatMap({ conversationOwners[$0] }) {
            join(owner, run.runId)
        } else if let conversationID {
            conversationOwners[conversationID] = run.runId
        }

        if let previous = normalizedConversationID(run.continuedFromRunId),
           runsByID[previous] != nil {
            join(previous, run.runId)
        }

        for sessionID in conversationSessionIDs(run) {
            if let owner = sessionOwners[sessionID] {
                join(owner, run.runId)
            } else {
                sessionOwners[sessionID] = run.runId
            }
        }
    }

    let components = Dictionary(grouping: runs) { root($0.runId) }
    return Dictionary(
        uniqueKeysWithValues: components.values.map { values in
            let ordered = values.sorted(by: runPrecedes)
            return (canonicalConversationID(for: ordered), ordered)
        }
    )
}

private func canonicalConversationID(for runs: [RunSummary]) -> String {
    let runIDs = Set(runs.map(\.runId))
    let referencedRoot = runs
        .compactMap { normalizedConversationID($0.conversationId) }
        .first { runIDs.contains($0) }
    if let referencedRoot { return referencedRoot }

    guard let first = runs.first else { return "" }
    return normalizedConversationID(first.conversationId)
        ?? first.runId
}

func conversationSessionIDs(_ run: RunSummary) -> [String] {
    var values: [String] = []
    for rawValue in [
        run.config.resumeSessionId,
        run.sessionPromotion?.sessionId,
    ] {
        guard let value = usableResumeSessionID(rawValue ?? ""),
              !values.contains(value) else { continue }
        values.append(value)
    }
    return values
}

private func normalizedConversationID(_ value: String?) -> String? {
    let normalized = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return normalized.isEmpty ? nil : normalized
}

private func runPrecedes(_ left: RunSummary, _ right: RunSummary) -> Bool {
    let leftTurn = left.turnIndex ?? 0
    let rightTurn = right.turnIndex ?? 0
    if leftTurn != rightTurn { return leftTurn < rightTurn }
    if left.createdAt != right.createdAt { return left.createdAt < right.createdAt }
    return left.runId < right.runId
}

func cachedConversationTitle(
    from history: [ResumeHistoryEntry]
) -> String? {
    let prompts = history.filter {
        $0.category.lowercased().contains("user")
    }.map(\.text)
    guard prompts.contains(where: {
        !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }) else { return nil }
    return preferredConversationTitle(from: prompts)
}

func preferredCachedConversationTitle(
    runIDs: [String],
    persisted: [String: String],
    fallback: [String: String]
) -> String? {
    // History begins at the real root of the Codex conversation. A persisted
    // title from an older client may instead describe only the first PCR Turn.
    runIDs.compactMap { fallback[$0] }.first
        ?? runIDs.compactMap { persisted[$0] }.first
}

func conversationTitle(_ prompt: String) -> String {
    let collapsed = prompt
        .split(whereSeparator: \.isWhitespace)
        .joined(separator: " ")
    guard !collapsed.isEmpty else { return "Untitled conversation" }

    let maximumLength = 56
    let sentenceEndings: Set<Character> = ["。", "！", "？", ".", "!", "?"]
    if let ending = collapsed.indices.first(where: {
        sentenceEndings.contains(collapsed[$0])
    }) {
        let end = collapsed.index(after: ending)
        let sentence = String(collapsed[..<end])
        if sentence.count >= 8, sentence.count <= maximumLength {
            return sentence
        }
    }
    guard collapsed.count > maximumLength else { return collapsed }
    return String(collapsed.prefix(maximumLength - 1)) + "…"
}

private func preferredConversationTitle(from prompts: [String]) -> String {
    let values = prompts.map {
        $0.trimmingCharacters(in: .whitespacesAndNewlines)
    }.filter { !$0.isEmpty }
    guard let first = values.first else { return conversationTitle("") }
    return conversationTitle(
        values.first(where: { !isGenericConversationOpener($0) }) ?? first
    )
}

private func isGenericConversationOpener(_ value: String) -> Bool {
    let punctuation = CharacterSet(charactersIn: " \t\r\n,.!?，。！？~～")
    let normalized = value
        .trimmingCharacters(in: punctuation)
        .split(whereSeparator: \.isWhitespace)
        .joined(separator: " ")
        .lowercased()
    return normalized.isEmpty || [
        "hello", "hey", "hi", "在吗", "在么", "您好", "你好", "你好呀", "嗨", "哈喽",
    ].contains(normalized)
}
