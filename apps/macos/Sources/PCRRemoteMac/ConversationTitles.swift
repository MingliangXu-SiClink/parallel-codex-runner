import Foundation

struct ConversationRenameRequest: Identifiable {
    let id = UUID()
    let machineID: String
    let workspaceID: String
    let conversation: WorkspaceConversationSummary
}

struct RenameConversationBody: Encodable {
    let workspaceId: String
    let conversationId: String
    let runId: String?
    let resumeSessionId: String?
    let title: String
}

struct RenameConversationResponse: Decodable {
    let conversation: ConversationRecord
}

struct ConversationTitlesPage: Decodable {
    let conversations: [ConversationRecord]
    let hasMore: Bool?
    let nextCursor: String?
}

struct ConversationTitlesBody: Encodable {
    let workspaceId: String
    let cursor: String?
    let limit = 100
}

enum ConversationTitles {
    static func normalized(_ title: String) -> String {
        title.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    static func key(machineID: String, workspaceID: String, conversationID: String) -> String {
        MachineContextKey.workspace(machineID: machineID, workspaceID: workspaceID)
            + "\u{1f}" + conversationID
    }

    static func matching(
        _ conversation: WorkspaceConversationSummary,
        records: [ConversationRecord]
    ) -> ConversationRecord? {
        let conversationIDs = Set([conversation.id] + conversation.runs.compactMap(\.conversationId))
        let turnIDs = Set(conversation.runs.map { $0.conversationSnapshot?.turn.turnId ?? "turn-\($0.runId)" })
        let sessionIDs = Set(conversation.runs.flatMap(conversationSessionIDs)
            + [conversation.resumeSession?.sessionId].compactMap { $0 })
        return records.filter { record in
            record.customTitle != nil && (
                conversationIDs.contains(record.conversationId)
                    || !turnIDs.isDisjoint(with: record.turnIds)
                    || record.resumeSessionId.map(sessionIDs.contains) == true
            )
        }.max { lhs, rhs in
            let leftExact = conversationIDs.contains(lhs.conversationId)
            let rightExact = conversationIDs.contains(rhs.conversationId)
            if leftExact != rightExact { return !leftExact }
            return (lhs.titleRevision ?? 0) < (rhs.titleRevision ?? 0)
        }
    }
}
