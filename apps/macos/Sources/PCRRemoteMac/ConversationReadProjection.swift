import Foundation

struct PreparedConversationProjection: Sendable {
    let input: TranscriptProjectionInput
    let blocks: [TranscriptBlock]

    init(input: TranscriptProjectionInput, blocks: [TranscriptBlock]) {
        self.input = input
        self.blocks = blocks
    }

    init(run: RunSummary, history: [ResumeHistoryEntry], transcript: AgentTranscript,
         previous: Self? = nil) {
        input = TranscriptProjectionInput(runID: run.runId, runStatus: run.status,
            prompt: run.prompt, history: history, transcript: transcript,
            runError: run.error, workerError: run.workerError, pendingSubmission: nil)
        if let previous, previous.input == input {
            blocks = previous.blocks
        } else {
            blocks = TranscriptFormatter.preparedConversationBlocks(input)
        }
    }
}

enum ConversationReadMode: Hashable, Sendable {
    case conversation, work, recentWork
}

struct ConversationAgentRead: Sendable {
    let agent: Int
    let requested: AgentTranscript
    let status: String?
    let detail: RunAgentDetail?
    var mode: ConversationReadMode = .work
}

/// Pure readers and projection work have no navigation state or MainActor isolation.
enum ConversationReadProjection {
    static func agentPage(client: PcrdClient, target: PcrdRequestTarget,
                          run: RunSummary, agent: Int, transcript: AgentTranscript,
                          mode: ConversationReadMode = .work) async -> ConversationAgentRead {
        var transcript = transcript
        if mode == .recentWork, transcript.recentWork == nil {
            let start = max(transcript.cursor, run.eventCount - AgentRecentWork.initialEventWindow)
            transcript.recentWork = AgentRecentWork(startCursor: start, cursor: start)
        }
        let cursor = mode == .conversation ? transcript.conversation?.cursor ?? 0
            : mode == .recentWork ? transcript.recentWork!.cursor : transcript.cursor
        var eventLimit = 256
        var finalCursor = transcript.finalMessageCursor
        var restarted = false
        let status = run.agents.first { $0.agent == agent }?.normalizedStatus
        while !Task.isCancelled {
            do {
                let detail: RunAgentDetail = try await client.call(target: target, "runs.agent",
                    body: GetAgentBody(runId: run.runId, agent: agent, includeDiff: false,
                        includeEvents: true,
                        cursor: cursor,
                        eventLimit: eventLimit,
                        transcriptItemsOnly: true, conversationItemsOnly: mode == .conversation,
                        finalMessageCursor: finalCursor), priority: mode == .work ? .background : .utility)
                var probe = transcript
                if probe.mergeFinalMessage(detail, requested: transcript, requestedStatus: status) {
                    guard !restarted else { break }
                    restarted = true
                    finalCursor = 0
                    continue
                }
                return ConversationAgentRead(agent: agent, requested: transcript, status: status,
                    detail: detail, mode: mode)
            } catch let PcrdClientError.remote(code, _) where code == "response_too_large" && eventLimit > 1 {
                eventLimit = max(1, eventLimit / 2)
            } catch { break }
        }
        return ConversationAgentRead(agent: agent, requested: transcript, status: status,
            detail: nil, mode: mode)
    }

    static func merge(_ page: ConversationAgentRead, into base: AgentTranscript,
                      run: RunSummary, history: [ResumeHistoryEntry],
                      previous: PreparedConversationProjection? = nil,
                      onlyWhenChanged: Bool = false) async -> PreparedConversationProjection? {
        guard let transcript = await mergeTranscript(page, into: base, run: run),
              !onlyWhenChanged || transcript != base else { return nil }
        return PreparedConversationProjection(run: run, history: history, transcript: transcript, previous: previous)
    }

    static func mergeTranscript(_ page: ConversationAgentRead, into base: AgentTranscript,
                                run: RunSummary) async -> AgentTranscript? {
        guard let detail = page.detail, base.sourceRunID == run.runId,
              base.sourceAgent == page.agent, detail.agent == page.agent,
              detail.runId == run.runId else { return nil }
        var transcript = base
        if transcript.mergeFinalMessage(detail, requested: page.requested, requestedStatus: page.status) {
            // A replacement is publishable only with its first page. Keep the
            // last readable answer if the replacement read has not succeeded.
            transcript.copyFinalMessage(from: base)
        }
        // Work and answer readers may overlap. Only the next contiguous page can advance work.
        if page.mode == .recentWork {
            guard let requested = page.requested.recentWork else { return nil }
            var recent = transcript.recentWork ?? AgentRecentWork(
                startCursor: requested.startCursor, cursor: requested.startCursor)
            if let output = detail.transcriptPage, recent.startCursor == requested.startCursor,
               output.cursor == requested.cursor, output.cursor == recent.cursor,
               output.nextCursor >= output.cursor, !output.hasMore || output.nextCursor > output.cursor {
                var known = Set(recent.items.map(\.eventID))
                for item in output.items.sorted(by: { $0.eventId < $1.eventId })
                    where (item.kind == "command" || item.operation != nil) && known.insert(item.eventId).inserted {
                    var blocks: [TranscriptBlock] = []
                    TranscriptFormatter.append(items: [item], to: &blocks)
                    if let block = blocks.first {
                        let identity = block.operation.map { operation in
                            transcript.blocks.first { $0.operation?.id == operation.id }?.id
                                ?? recent.items.first { $0.block.operation?.id == operation.id }?.block.id
                                ?? "recent-operation-\(operation.id)"
                        } ?? block.id
                        recent.items.append(.init(eventID: item.eventId, block: TranscriptBlock(
                            id: identity, kind: block.kind, text: block.text, status: block.status,
                            operationId: block.operationId, activityType: block.activityType, operation: block.operation)))
                    }
                }
                recent.cursor = output.nextCursor
                recent.hasMore = output.hasMore
                transcript.recentWork = recent
            }
        } else if page.mode == .conversation {
            var conversation = transcript.conversation ?? AgentConversationPage()
            if let output = detail.transcriptPage, output.cursor == conversation.cursor,
               output.nextCursor >= output.cursor, !output.hasMore || output.nextCursor > output.cursor {
                var known = Set(conversation.items.map(\.eventID))
                for item in output.items.sorted(by: { $0.eventId < $1.eventId })
                    where ["output", "warning", "error"].contains(item.kind) && known.insert(item.eventId).inserted {
                    var blocks: [TranscriptBlock] = []
                    TranscriptFormatter.append(items: [item], to: &blocks)
                    if let block = blocks.first {
                        conversation.items.append(.init(eventID: item.eventId, block: block))
                    }
                }
                conversation.cursor = output.nextCursor
                conversation.hasMore = output.hasMore
                transcript.conversation = conversation
            }
        } else if let work = detail.transcriptPage, work.cursor == transcript.cursor,
           work.nextCursor >= work.cursor, !work.hasMore || work.nextCursor > work.cursor {
            let items = work.items.filter { !transcript.eventIDs.contains($0.eventId) }
                .sorted { $0.eventId < $1.eventId }
            transcript.eventIDs.formUnion(items.map(\.eventId))
            TranscriptFormatter.append(items: items, to: &transcript.blocks)
            transcript.cursor = work.nextCursor
            transcript.hasMore = work.hasMore
        } else if let work = detail.eventPage, work.cursor == transcript.cursor,
                  work.nextCursor >= work.cursor, !work.hasMore || work.nextCursor > work.cursor {
            let events = work.events.filter { !transcript.eventIDs.contains($0.eventId) }
                .sorted { $0.eventId < $1.eventId }
            transcript.eventIDs.formUnion(events.map(\.eventId))
            TranscriptFormatter.append(events: events, to: &transcript.blocks)
            transcript.cursor = work.nextCursor
            transcript.hasMore = work.hasMore
        }
        if base.error == page.requested.error,
           run.agents.first(where: { $0.agent == page.agent })?.normalizedStatus == page.status {
            transcript.error = detail.error ?? ""
        }
        return transcript
    }

    static func prepare(run: RunSummary, history: [ResumeHistoryEntry], transcript: AgentTranscript,
                        previous: PreparedConversationProjection? = nil) async -> PreparedConversationProjection {
        PreparedConversationProjection(run: run, history: history, transcript: transcript, previous: previous)
    }

    static func history(client: PcrdClient, target: PcrdRequestTarget,
                        runID: String,
                        onPage: @Sendable ([ResumeHistoryEntry], Bool) async -> Void) async throws {
        var cursor = 0
        var entries: [ResumeHistoryEntry] = []
        while !Task.isCancelled {
            let page: ResumeHistoryPage = try await client.call(target: target, "runs.history",
                body: RunHistoryBody(runId: runID, cursor: cursor, limit: 100,
                    maxBytes: 512 * 1024, conversationItemsOnly: true), priority: .utility)
            guard page.cursor == cursor, page.nextCursor >= cursor,
                  !page.hasMore || page.nextCursor > cursor else { throw PcrdClientError.invalidResponse }
            entries.append(contentsOf: page.entries)
            await onPage(entries, !page.hasMore)
            guard page.hasMore else { return }
            cursor = page.nextCursor
        }
        throw CancellationError()
    }

    static func prepare(run: RunSummary, snapshot: WorkspaceRunViewSnapshot) async -> [Int: PreparedConversationProjection] {
        snapshot.transcripts.mapValues {
            PreparedConversationProjection(run: run, history: snapshot.conversationHistory, transcript: $0)
        }
    }

}
