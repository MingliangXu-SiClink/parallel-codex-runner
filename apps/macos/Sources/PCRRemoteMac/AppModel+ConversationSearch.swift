import Foundation

struct ConversationTextMatch: Decodable, Sendable {
    let runId: String
    let agent: Int?
    let excerpt: String
}

private struct ConversationSearchPage: Decodable, Sendable {
    let matches: [ConversationTextMatch]
    let nextCursor: String?
    let hasMore: Bool
}

private struct ConversationSearchBody: Encodable, Sendable {
    let workspaceId: String
    let query: String
    let cursor: String
}

@MainActor
extension AppModel {
    func searchSavedConversationText(_ query: String) async {
        let target = client.currentTarget()
        let workspaceID = selectedWorkspaceID
        let token = UUID()
        conversationSearchGeneration = token
        conversationSearchMatchesByRun = [:]
        conversationSearchError = nil
        conversationSearchLoading = false
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, let workspaceID else { return }
        conversationSearchLoading = true
        defer { if conversationSearchGeneration == token { conversationSearchLoading = false } }
        do {
            try await Task.sleep(for: .milliseconds(300))
            var cursor = ""
            var seen = Set<String>()
            repeat {
                let page: ConversationSearchPage = try await client.call(target: target, "conversations.search",
                    body: ConversationSearchBody(workspaceId: workspaceID, query: query, cursor: cursor))
                try Task.checkCancellation()
                guard conversationSearchGeneration == token, client.currentTarget() == target,
                      selectedWorkspaceID == workspaceID else { return }
                for match in page.matches { conversationSearchMatchesByRun[match.runId] = match }
                guard page.hasMore else { break }
                let next = page.nextCursor ?? ""
                guard !next.isEmpty, next != cursor, seen.insert(next).inserted else { throw URLError(.cannotParseResponse) }
                cursor = next
            } while !Task.isCancelled
        } catch is CancellationError {
        } catch {
            if conversationSearchGeneration == token { conversationSearchError = appLanguage.userMessage(for: error) }
        }
    }

    func searchExcerpt(_ conversation: WorkspaceConversationSummary, query: String? = nil) -> String? {
        if let remote = conversation.runs.compactMap({ conversationSearchMatchesByRun[$0.runId]?.excerpt }).first {
            return remote
        }
        guard let query,
              let text = workspaceConversationMatchText(conversation, query: query),
              text != conversation.title else { return nil }
        return conversationSearchSnippet(text, query: query)
    }

    func openConversationSearchResult(_ conversation: WorkspaceConversationSummary, query: String) async {
        guard let workspaceID = selectedWorkspaceID else { return }
        let target = client.currentTarget()
        let generation = conversationSearchGeneration
        let match = conversation.runs.compactMap { conversationSearchMatchesByRun[$0.runId] }.first
        await selectConversation(conversation.id)
        guard target == client.currentTarget(), workspaceID == selectedWorkspaceID,
              selectedConversationID == conversation.id, generation == conversationSearchGeneration else { return }
        if let match {
            await selectConversationTurn(match.runId)
            if activeRun?.runId == match.runId, let agent = match.agent { await selectAgent(agent) }
        }
        guard target == client.currentTarget(), workspaceID == selectedWorkspaceID,
              selectedConversationID == conversation.id, generation == conversationSearchGeneration else { return }
        conversationSearchNavigation = ConversationSearchNavigation(
            machineID: currentMachineContextID, workspaceID: workspaceID,
            conversationID: conversation.id, query: query
        )
    }

    func workspaceConversationMatchesSearch(
        _ conversation: WorkspaceConversationSummary,
        query: String
    ) -> Bool {
        query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || workspaceConversationMatchText(conversation, query: query) != nil
    }

    private func workspaceConversationMatchText(
        _ conversation: WorkspaceConversationSummary,
        query: String
    ) -> String? {
        guard let workspaceID = selectedWorkspaceID else {
            return conversationSearchMatchText(conversation, query: query)
        }

        var snapshotsByRunID: [String: WorkspaceRunViewSnapshot] = [:]
        for run in conversation.runs {
            if activeRun?.runId == run.runId {
                snapshotsByRunID[run.runId] = WorkspaceRunViewSnapshot(
                    selectedAgent: selectedAgent,
                    transcripts: transcripts,
                    conversationHistory: resumeHistory
                )
                continue
            }
            let key = WorkspaceRunViewKey(
                machineID: currentMachineContextID,
                workspaceID: workspaceID,
                runID: run.runId
            )
            snapshotsByRunID[run.runId] = runViewStateStore.snapshot(for: key)
        }

        let visibleHistorySnapshot = selectedConversationID == conversation.id
            && !resumeHistory.isEmpty
            ? WorkspaceRunViewSnapshot(
                selectedAgent: selectedAgent,
                transcripts: transcripts,
                conversationHistory: resumeHistory
            )
            : nil
        let resumeSnapshot = visibleHistorySnapshot ?? conversation.resumeSession.flatMap { session in
            runViewStateStore.snapshot(
                for: resumeConversationViewKey(
                    machineID: currentMachineContextID,
                    workspaceID: workspaceID,
                    sessionID: session.sessionId
                )
            )
        }
        return conversationSearchMatchText(
            conversation,
            query: query,
            snapshotsByRunID: snapshotsByRunID,
            resumeSnapshot: resumeSnapshot
        )
    }
}
