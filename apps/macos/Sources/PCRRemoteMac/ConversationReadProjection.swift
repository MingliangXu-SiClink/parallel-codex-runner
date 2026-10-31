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
    case conversation, work, recentWork, metadata
}

struct ConversationAgentRead: Sendable {
    let agent: Int
    let requested: AgentTranscript
    let status: String?
    let detail: RunAgentDetail?
    var mode: ConversationReadMode = .work
    var bodyPage: RemoteTranscriptPage? = nil

    var succeeded: Bool { detail != nil || bodyPage != nil }
}

struct ConversationHistoryRead: Sendable {
    struct Page: Sendable {
        let entries: [ResumeHistoryEntry]
        let complete: Bool
    }
    let pages: AsyncThrowingStream<Page, Error>
    let task: Task<Void, Never>
}

/// Pure readers and projection work have no navigation state or MainActor isolation.
enum ConversationReadProjection {
    static func mergeRunPage(_ page: RunTranscriptPage, into base: AgentTranscript,
                             runID: String, agent: Int) -> AgentTranscript? {
        guard page.runId == runID, base.sourceRunID == runID, base.sourceAgent == agent,
              page.cursor <= base.cursor, page.nextCursor >= base.cursor,
              page.nextCursor >= page.cursor, !page.hasMore || page.nextCursor > page.cursor else { return nil }
        var transcript = base
        let items = page.agents.first { $0.agent == agent }?.items ?? []
        let incoming = items.filter { $0.eventId >= base.cursor && $0.eventId < page.nextCursor
            && !base.eventIDs.contains($0.eventId) }.sorted { $0.eventId < $1.eventId }
        transcript.eventIDs.formUnion(incoming.map(\.eventId))
        TranscriptFormatter.append(items: incoming, to: &transcript.blocks)
        transcript.cursor = page.nextCursor
        transcript.hasMore = page.hasMore
        return transcript
    }

    static func agentPage(client: PcrdClient, target: PcrdRequestTarget,
                          run: RunSummary, agent: Int, transcript: AgentTranscript,
                          mode: ConversationReadMode = .work,
                          independentBody: Bool = false,
                          demand: ClientReadDemand? = nil) async -> ConversationAgentRead {
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
                if independentBody, mode == .conversation || mode == .recentWork {
                    let page: RunTranscriptPage = try await client.call(target: target, "runs.events",
                        body: RunTranscriptBody(runId: run.runId, cursor: cursor,
                            agent: agent, conversationItemsOnly: mode == .conversation, limit: eventLimit),
                        priority: .utility, readDemand: demand)
                    guard page.runId == run.runId, page.cursor == cursor,
                          page.agents.allSatisfy({ $0.agent == agent }) else { break }
                    let body = RemoteTranscriptPage(schemaVersion: 1, cursor: page.cursor,
                        nextCursor: page.nextCursor, hasMore: page.hasMore, total: page.total,
                        items: page.agents.flatMap(\.items), serializedBytes: nil,
                        byteLimit: nil, oversizedEvent: nil)
                    return ConversationAgentRead(agent: agent, requested: transcript, status: status,
                        detail: nil, mode: mode, bodyPage: body)
                }
                let detail: RunAgentDetail = try await client.call(target: target, "runs.agent",
                    body: GetAgentBody(runId: run.runId, agent: agent, includeDiff: false,
                        includeEvents: mode != .metadata,
                        cursor: cursor,
                        eventLimit: eventLimit,
                        transcriptItemsOnly: true, conversationItemsOnly: mode == .conversation,
                        finalMessageCursor: finalCursor), priority: mode == .work ? .background : .utility,
                        readDemand: demand)
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
        guard page.succeeded, base.sourceRunID == run.runId,
              base.sourceAgent == page.agent,
              page.detail.map({ $0.agent == page.agent && $0.runId == run.runId }) ?? true else { return nil }
        var transcript = base
        if let detail = page.detail,
           transcript.mergeFinalMessage(detail, requested: page.requested, requestedStatus: page.status) {
            // A replacement is publishable only with its first page. Keep the
            // last readable answer if the replacement read has not succeeded.
            transcript.copyFinalMessage(from: base)
        }
        // Work and answer readers may overlap. Only the next contiguous page can advance work.
        if page.mode == .recentWork {
            guard let requested = page.requested.recentWork else { return nil }
            var recent = transcript.recentWork ?? AgentRecentWork(
                startCursor: requested.startCursor, cursor: requested.startCursor)
            if let output = page.bodyPage ?? page.detail?.transcriptPage, recent.startCursor == requested.startCursor,
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
            if let output = page.bodyPage ?? page.detail?.transcriptPage, output.cursor == conversation.cursor,
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
        } else if page.mode == .work, let work = page.detail?.transcriptPage, work.cursor == transcript.cursor,
           work.nextCursor >= work.cursor, !work.hasMore || work.nextCursor > work.cursor {
            let items = work.items.filter { !transcript.eventIDs.contains($0.eventId) }
                .sorted { $0.eventId < $1.eventId }
            transcript.eventIDs.formUnion(items.map(\.eventId))
            TranscriptFormatter.append(items: items, to: &transcript.blocks)
            transcript.cursor = work.nextCursor
            transcript.hasMore = work.hasMore
        } else if page.mode == .work, let work = page.detail?.eventPage, work.cursor == transcript.cursor,
                  work.nextCursor >= work.cursor, !work.hasMore || work.nextCursor > work.cursor {
            let events = work.events.filter { !transcript.eventIDs.contains($0.eventId) }
                .sorted { $0.eventId < $1.eventId }
            transcript.eventIDs.formUnion(events.map(\.eventId))
            TranscriptFormatter.append(events: events, to: &transcript.blocks)
            transcript.cursor = work.nextCursor
            transcript.hasMore = work.hasMore
        }
        if let detail = page.detail, base.error == page.requested.error,
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
                        runID: String, demand: ClientReadDemand? = nil,
                        cachedHistory: @escaping @Sendable () async -> [ResumeHistoryEntry]? = { nil }) -> ConversationHistoryRead {
        let (pages, continuation) = AsyncThrowingStream<ConversationHistoryRead.Page, Error>
            .makeStream(bufferingPolicy: .bufferingNewest(1))
        // The latest cumulative page supersedes intermediate paints, not history.
        // Read sequential bounded pages without waiting for AppKit to render each.
        let task = Task.detached(priority: .utility) {
            do {
                if let cached = await cachedHistory() {
                    try Task.checkCancellation()
                    continuation.yield(.init(entries: cached, complete: true))
                    continuation.finish()
                    return
                }
                var cursor = 0
                var entries: [ResumeHistoryEntry] = []
                while !Task.isCancelled {
                    let page: ResumeHistoryPage = try await client.call(target: target, "runs.history",
                        body: RunHistoryBody(runId: runID, cursor: cursor, limit: 100,
                            maxBytes: 512 * 1024, conversationItemsOnly: true),
                        priority: .utility, readDemand: demand)
                    try Task.checkCancellation()
                    guard page.cursor == cursor, page.nextCursor >= cursor,
                          !page.hasMore || page.nextCursor > cursor else { throw PcrdClientError.invalidResponse }
                    entries.append(contentsOf: page.entries)
                    if case .terminated = continuation.yield(.init(entries: entries, complete: !page.hasMore)) { return }
                    if !page.hasMore {
                        continuation.finish()
                        return
                    }
                    cursor = page.nextCursor
                }
                throw CancellationError()
            } catch { continuation.finish(throwing: error) }
        }
        continuation.onTermination = { @Sendable _ in task.cancel() }
        return ConversationHistoryRead(pages: pages, task: task)
    }

    static func prepare(run: RunSummary, snapshot: WorkspaceRunViewSnapshot,
                        agents: Set<Int>? = nil,
                        previous: [Int: PreparedConversationProjection] = [:]) async -> [Int: PreparedConversationProjection] {
        var prepared: [Int: PreparedConversationProjection] = [:]
        for (agent, transcript) in snapshot.transcripts where agents?.contains(agent) != false {
            prepared[agent] = PreparedConversationProjection(run: run, history: snapshot.conversationHistory,
                transcript: transcript, previous: previous[agent])
        }
        return prepared
    }

}
