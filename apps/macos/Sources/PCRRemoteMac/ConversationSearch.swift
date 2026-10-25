import Foundation

struct ConversationSearchNavigation: Equatable, Sendable {
    let id = UUID()
    let machineID: String
    let workspaceID: String
    let conversationID: String
    let query: String
}

func normalizedConversationSearchText(_ text: String) -> String {
    text.folding(
        options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive],
        locale: .current
    )
}

func conversationSearchMatches(
    _ conversation: WorkspaceConversationSummary,
    query: String,
    snapshotsByRunID: [String: WorkspaceRunViewSnapshot] = [:],
    resumeSnapshot: WorkspaceRunViewSnapshot? = nil
) -> Bool {
    if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return true }
    return conversationSearchMatchText(
        conversation, query: query, snapshotsByRunID: snapshotsByRunID,
        resumeSnapshot: resumeSnapshot
    ) != nil
}

func conversationSearchMatchText(
    _ conversation: WorkspaceConversationSummary,
    query: String,
    snapshotsByRunID: [String: WorkspaceRunViewSnapshot] = [:],
    resumeSnapshot: WorkspaceRunViewSnapshot? = nil
) -> String? {
    let query = normalizedConversationSearchText(
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    )
    guard !query.isEmpty else { return nil }

    func matches(_ text: String?) -> Bool {
        guard let text, !text.isEmpty else { return false }
        return normalizedConversationSearchText(text).contains(query)
    }

    func transcriptMatch(_ transcript: AgentTranscript) -> String? {
        if matches(transcript.finalMessage) {
            return transcript.finalMessage
        }
        return transcript.blocks.first { block in
            switch block.kind {
            case .user:
                return matches(block.text)
            case .output:
                return matches(block.text)
            case .thought, .command, .activity, .warning, .error:
                return false
            }
        }?.text
    }

    for text in [conversation.title, conversation.resumeSession?.displayTitle,
                 conversation.resumeSession?.preview].compactMap({ $0 }) {
        if matches(text) { return text }
    }

    for run in conversation.runs {
        for text in [run.prompt, run.conversationSnapshot?.turn.prompt].compactMap({ $0 }) {
            if matches(text) { return text }
        }
        guard let snapshot = snapshotsByRunID[run.runId] else { continue }
        if let entry = snapshot.conversationHistory.first(where: { matches($0.text) }) {
            return entry.text
        }
        guard let transcript = preferredSearchTranscript(run: run, snapshot: snapshot) else {
            continue
        }
        if let text = transcriptMatch(transcript) { return text }
    }

    guard let resumeSnapshot else { return nil }
    if let entry = resumeSnapshot.conversationHistory.first(where: { matches($0.text) }) {
        return entry.text
    }
    let visibleAgent = resumeSnapshot.selectedAgent ?? resumeSnapshot.transcripts.keys.min()
    return visibleAgent.flatMap { resumeSnapshot.transcripts[$0] }
        .flatMap(transcriptMatch)
}

func conversationSearchSnippet(_ text: String, query: String) -> String? {
    let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !query.isEmpty, let match = text.range(
        of: query, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive],
        locale: .current
    ) else { return nil }
    let start = text.index(match.lowerBound, offsetBy: -48, limitedBy: text.startIndex)
        ?? text.startIndex
    let end = text.index(match.upperBound, offsetBy: 120, limitedBy: text.endIndex)
        ?? text.endIndex
    let excerpt = text[start..<end].split(whereSeparator: \.isWhitespace).joined(separator: " ")
    return (start > text.startIndex ? "…" : "") + excerpt + (end < text.endIndex ? "…" : "")
}

private func preferredSearchTranscript(
    run: RunSummary,
    snapshot: WorkspaceRunViewSnapshot
) -> AgentTranscript? {
    let preferredAgent = run.finalizedAgent
        ?? snapshot.selectedAgent
        ?? run.recommendedAgent
        ?? run.agents.first(where: \.isSuccessful)?.agent
        ?? snapshot.transcripts.keys.min()
    return preferredAgent.flatMap { snapshot.transcripts[$0] }
}
