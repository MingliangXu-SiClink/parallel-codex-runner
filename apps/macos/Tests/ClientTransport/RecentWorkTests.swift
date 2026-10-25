import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct RecentWorkTests {
    @Test func recentCommandsInterleaveWithFastRepliesWithoutCertifyingTheMissingHistory() async throws {
        let run = try run()
        var transcript = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8, cursor: 100, hasMore: true)
        transcript.conversation = AgentConversationPage(cursor: 240_000, hasMore: false, items: [
            .init(eventID: 239_900, block: .init(id: "before", kind: .output, text: "Checking the result")),
            .init(eventID: 239_950, block: .init(id: "after", kind: .output, text: "The result is ready"))])
        let read = try recentPage(run: run, requested: transcript, items: [
            operation(239_910, status: "running"), operation(239_920, status: "completed")])
        let prepared = try #require(await ConversationReadProjection.merge(read, into: transcript, run: run, history: []))
        transcript = prepared.input.transcript
        #expect(transcript.cursor == 100 && transcript.hasMore)
        #expect(transcript.conversation?.cursor == 240_000)
        #expect(transcript.recentWork?.startCursor == 237_952)
        #expect(transcript.recentWork?.cursor == 240_000)
        #expect(transcript.eventIDs.isEmpty)
        #expect(prepared.blocks.map(\.id) == ["before", "recent-operation-qa-operation", "after"])
        #expect(prepared.blocks.filter { $0.operation != nil }.count == 1)
        #expect(prepared.blocks.first { $0.operation != nil }?.operation?.status == .completed)
        let visible = TranscriptFormatter.blocksWithWorkDisclosure(prepared.blocks, runID: run.runId,
            agent: 8, expanded: false)
        #expect(visible.map(\.text) == ["Checking the result", "Ran commands", "The result is ready"])
    }

    @Test func lateDuplicateAndForeignRecentPagesCannotRegressOrCrossBranches() async throws {
        let run = try run()
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8, cursor: 100, hasMore: true)
        let first = try recentPage(run: run, requested: initial, items: [operation(239_910, status: "running")])
        let started = try #require(await ConversationReadProjection.mergeTranscript(first, into: initial, run: run))
        let completion = try recentPage(run: run, requested: started,
            items: [operation(240_010, status: "completed")], next: 240_020)
        let completed = try #require(await ConversationReadProjection.mergeTranscript(completion, into: started, run: run))
        let late = try #require(await ConversationReadProjection.mergeTranscript(first, into: completed, run: run))
        #expect(late == completed)
        #expect(late.recentWork?.cursor == 240_020)
        var wrongAgent = completion
        wrongAgent = ConversationAgentRead(agent: 7, requested: completion.requested, status: "running",
            detail: completion.detail, mode: .recentWork)
        #expect(await ConversationReadProjection.mergeTranscript(wrongAgent, into: completed, run: run) == nil)
        let foreign = AgentTranscript(sourceRunID: "another-run", sourceAgent: 8)
        #expect(await ConversationReadProjection.mergeTranscript(completion, into: foreign, run: run) == nil)
    }

    @Test func workBeginningBeforeTheRecentWindowMergesWithItsAlreadyObservedCompletion() async throws {
        let run = try run()
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8)
        let recent = try recentPage(run: run, requested: initial, items: [operation(239_920, status: "completed")])
        let completed = try #require(await ConversationReadProjection.mergeTranscript(recent, into: initial, run: run))
        let full = try page(run: run, requested: initial, mode: .work, cursor: 0, next: 239_000,
            items: [operation(235_000, status: "running")])
        let backfilled = try #require(await ConversationReadProjection.merge(full, into: completed, run: run, history: []))
        let commands = backfilled.blocks.filter { $0.operation != nil }
        #expect(commands.count == 1)
        #expect(commands.first?.id == "recent-operation-qa-operation")
        #expect(commands.first?.operation?.status == .completed)
        #expect(commands.first?.operation?.eventId == 239_920)
        #expect(backfilled.input.transcript.cursor == 239_000)
        let all = try page(run: run, requested: backfilled.input.transcript, mode: .work,
            cursor: 239_000, next: 240_000, items: [operation(239_920, status: "completed")])
        let settled = try #require(await ConversationReadProjection.merge(all, into: backfilled.input.transcript,
            run: run, history: []))
        #expect(settled.blocks.filter { $0.operation != nil }.count == 1)
        #expect(settled.blocks.first { $0.operation != nil }?.id == commands.first?.id)
        #expect(settled.blocks.first { $0.operation != nil }?.operation?.status == .completed)
    }

    @Test func recentCoverageSurvivesRestartAndOlderForegroundSnapshots() async throws {
        let run = try run()
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8, cursor: 100, hasMore: true)
        let recent = try recentPage(run: run, requested: initial, items: [operation(239_920, status: "completed")])
        let merged = try #require(await ConversationReadProjection.mergeTranscript(recent, into: initial, run: run))
        let data = try JSONEncoder().encode(merged)
        let restored = try JSONDecoder().decode(AgentTranscript.self, from: data)
        #expect(restored == merged)
        let key = WorkspaceRunViewKey(machineID: "local", workspaceID: "qa", runID: run.runId)
        let snapshot = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8: restored], conversationHistory: [])
        let old = WorkspaceRunViewSnapshot(selectedAgent: 8, transcripts: [8: initial], conversationHistory: [])
        #expect(snapshot.mergingVisible(old, for: key).transcripts[8]?.recentWork == restored.recentWork)
        let legacyData = try JSONSerialization.data(withJSONObject: ["sourceRunID": run.runId, "sourceAgent": 8,
            "eventIDs": [], "blocks": [], "cursor": 100, "hasMore": true, "finalMessage": "",
            "finalMessageCursor": 0, "finalMessageHasMore": true, "finalMessageID": "", "error": "", "artifactError": ""])
        #expect(try JSONDecoder().decode(AgentTranscript.self, from: legacyData).recentWork == nil)
    }

    @Test func rebuildingFullHistoryKeepsTheTerminalFactThatOnlyItsReaderObserved() async throws {
        let run = try run()
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8)
        let recent = try recentPage(run: run, requested: initial, items: [operation(239_910, status: "running")])
        let started = try #require(await ConversationReadProjection.mergeTranscript(recent, into: initial, run: run))
        let full = try page(run: run, requested: initial, mode: .work, cursor: 0, next: 240_020,
            items: [operation(239_910, status: "running"), operation(240_010, status: "completed")])
        let finished = try #require(await ConversationReadProjection.mergeTranscript(full, into: started, run: run))
        var previousReset = finished
        previousReset.blocks = []; previousReset.eventIDs = []; previousReset.cursor = 0
        #expect(TranscriptFormatter.blocks(history: [], prompt: "", transcript: previousReset)
            .first { $0.operation != nil }?.operation?.status == .running)
        var rebuilt = finished
        rebuilt.blocks = []; rebuilt.eventIDs = []; rebuilt.cursor = 0; rebuilt.hasMore = true
        rebuilt.preserveRecentWorkProgress(from: finished.blocks)
        let restored = try JSONDecoder().decode(AgentTranscript.self, from: JSONEncoder().encode(rebuilt))
        let block = TranscriptFormatter.blocks(history: [], prompt: "", transcript: restored).first { $0.operation != nil }
        #expect(block?.operation?.status == .completed)
        #expect(block?.operation?.eventId == 240_010)
        #expect(block?.id == "recent-operation-qa-operation")
        #expect(restored.recentWork?.cursor == started.recentWork?.cursor)
        #expect(restored.cursor == 0 && restored.hasMore)
        let earlierPage = try page(run: run, requested: restored, mode: .work, cursor: 0, next: 239_950,
            items: [operation(239_910, status: "running")])
        let partial = try #require(await ConversationReadProjection.merge(earlierPage, into: restored,
            run: run, history: []))
        #expect(partial.input.transcript.eventIDs.contains(239_910))
        #expect(!partial.input.transcript.eventIDs.contains(240_010))
        #expect(partial.blocks.first { $0.operation != nil }?.operation?.status == .completed)
        #expect(partial.blocks.filter { $0.operation != nil }.count == 1)
    }

    @Test func recentWorkDoesNotImportRawMediaFragmentsOrDuplicateConversationOutput() async throws {
        let run = try run()
        let initial = AgentTranscript(sourceRunID: run.runId, sourceAgent: 8)
        let read = try recentPage(run: run, requested: initial, items: [
            ["id": "image-fragment", "event_id": 239_900, "kind": "activity", "text": "raw-base64-fragment"],
            ["id": "progress", "event_id": 239_905, "kind": "output", "text": "Already read by the reply lane"],
            operation(239_910, status: "completed")])
        let merged = try #require(await ConversationReadProjection.mergeTranscript(read, into: initial, run: run))
        #expect(merged.recentWork?.items.map(\.eventID) == [239_910])
        #expect(merged.cursor == 0 && merged.hasMore)
    }

    private func run() throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"qa-long-run","status":"running","workspace":"/qa","prompt":"",
         "createdAt":"2026-10-20T00:00:00Z","updatedAt":"2026-10-20T00:00:00Z","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,"workerActive":true,
         "workerAvailable":true,"eventCount":240000,"agents":[{"agent":8,"name":"Agent 8","status":"running",
         "reasoningTokenCounts":{},"rejected":false,"recommended":false,"finalized":false}]}
        """.utf8))
    }

    private func operation(_ event: Int, status: String) -> [String: Any] {
        ["id": "event-\(event)", "event_id": event, "kind": "command", "text": "printf fixture",
         "status": status, "operation": ["id": "qa-operation", "event_id": event, "kind": "command",
             "status": status, "command": "printf fixture", "targets": [], "details_available": true]]
    }

    private func recentPage(run: RunSummary, requested: AgentTranscript, items: [[String: Any]],
                            next: Int = 240_000) throws -> ConversationAgentRead {
        var seeded = requested
        if seeded.recentWork == nil {
            let start = max(requested.cursor, run.eventCount - AgentRecentWork.initialEventWindow)
            seeded.recentWork = AgentRecentWork(startCursor: start, cursor: start)
        }
        return try page(run: run, requested: seeded, mode: .recentWork,
            cursor: seeded.recentWork!.cursor, next: next, items: items)
    }

    private func page(run: RunSummary, requested: AgentTranscript, mode: ConversationReadMode,
                      cursor: Int, next: Int, items: [[String: Any]]) throws -> ConversationAgentRead {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let detail = try decoder.decode(RunAgentDetail.self, from: JSONSerialization.data(withJSONObject: [
            "run_id": run.runId, "agent": 8, "name": "Agent 8", "status": "running",
            "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
            "stdout_tail": "", "stderr_tail": "", "final_message": "",
            "transcript_page": ["schema_version": 1, "cursor": cursor, "next_cursor": next,
                "has_more": next < run.eventCount, "total": max(next, run.eventCount), "items": items]]))
        return ConversationAgentRead(agent: 8, requested: requested, status: "running", detail: detail, mode: mode)
    }
}
