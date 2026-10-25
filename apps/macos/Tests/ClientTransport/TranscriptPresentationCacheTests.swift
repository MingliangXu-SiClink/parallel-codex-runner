import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct TranscriptPresentationCacheTests {
    @Test func activeBranchBodiesSurviveHistoryPressureAndReleaseTheirReservation() {
        var cache = TranscriptPresentationCache(maximumCost: 32 * 1024 * 1024)
        let keys = (0..<32).map {
            TranscriptPresentationKey(machineID: "local", workspaceID: "workspace-\($0 / 8)",
                conversationID: "background", runID: "run-\($0 / 8)", agent: $0 % 8 + 1)
        }
        cache.retainReadyRuns(Set(keys.map {
            WorkspaceRunViewKey(machineID: $0.machineID, workspaceID: $0.workspaceID, runID: $0.runID)
        }))
        let history = [ResumeHistoryEntry(category: "assistant", text: String(repeating: "Long shared history ", count: 40_000))]
        var inputs: [TranscriptProjectionInput] = []
        for (index, key) in keys.enumerated() {
            let input = TranscriptProjectionInput(runID: key.runID, runStatus: "running", prompt: "Question",
                history: history, transcript: AgentTranscript(sourceRunID: key.runID, sourceAgent: key.agent,
                    finalMessage: "Unique branch \(index)"), runError: nil, workerError: nil, pendingSubmission: nil)
            inputs.append(input)
            cache.put(.init(input: input, blocks: [.init(id: "branch", kind: .output, text: "Unique branch \(index)")],
                generation: UInt64(index + 1), timelineInput: nil, timelineBlocks: [], timelineGeneration: 0,
                itemsInput: nil, items: [], itemsGeneration: 0), for: key)
        }
        #expect(cache.totalCost > 32 * 1024 * 1024)
        for (key, input) in zip(keys, inputs) { #expect(cache.value(for: key, matching: input) != nil) }
        let alias = TranscriptPresentationKey(machineID: keys[0].machineID, workspaceID: keys[0].workspaceID,
            conversationID: "reconciled-alias", runID: keys[0].runID, agent: keys[0].agent)
        #expect(cache.value(for: alias, matching: inputs[0])?.blocks.first?.text == "Unique branch 0")
        let otherHistory = TranscriptProjectionInput(runID: inputs[0].runID, runStatus: "running", prompt: "Question",
            history: [.init(category: "user", text: "Different history")], transcript: inputs[0].transcript,
            runError: nil, workerError: nil, pendingSubmission: nil)
        #expect(cache.value(for: alias, matching: otherHistory) == nil)
        cache.retainReadyRuns([])
        #expect(cache.totalCost <= 32 * 1024 * 1024)
    }

    @Test func warmBranchRoundTripsReuseAllThreeProjectionStages() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        let first = model.selectedTimelineItems
        let firstRevision = model.conversationTimelineItemsProjectionGeneration
        model.selectedAgent = 2
        let second = model.selectedTimelineItems
        let sequence = model.presentationProjectionSequence
        #expect(first != second)
        for _ in 0..<15 {
            model.selectedAgent = 1
            #expect(model.selectedTimelineItems == first)
            #expect(model.conversationTimelineItemsProjectionGeneration == firstRevision)
            model.selectedAgent = 2
            #expect(model.selectedTimelineItems == second)
            #expect(model.presentationProjectionSequence == sequence)
        }
    }

    @Test func contentLanguageAndWorkChangesInvalidateOnlyMatchingProjections() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        _ = model.selectedTimelineItems
        model.selectedAgent = 2
        let second = model.selectedTimelineItems
        let secondRevision = model.conversationTimelineItemsProjectionGeneration
        model.selectedAgent = 1
        model.transcripts[1]?.finalMessage = "Changed first answer"
        #expect(model.selectedTranscriptBlocks.contains { $0.text.contains("Changed first answer") })
        model.appLanguage = .english
        _ = model.selectedTimelineItems
        let firstRevision = model.conversationTimelineItemsProjectionGeneration
        model.appLanguage = .chinese
        _ = model.selectedTimelineItems
        #expect(model.conversationTimelineItemsProjectionGeneration != firstRevision)
        model.selectedAgent = 2
        #expect(model.selectedTranscriptBlocks.contains { $0.text.contains("Candidate 2") })
        #expect(model.selectedTimelineItems == second)
        #expect(model.conversationTimelineItemsProjectionGeneration == secondRevision)
        let rawRevision = model.transcriptProjectionGeneration
        model.workProcess.revision &+= 1
        _ = model.selectedTimelineItems
        #expect(model.transcriptProjectionGeneration == rawRevision)
        #expect(model.conversationTimelineItemsProjectionGeneration != secondRevision)
    }

    @Test func cacheBoundsAndContextIdentityPreventUnrelatedReuse() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.transcriptPresentationCache = TranscriptPresentationCache(capacity: 1, maximumCost: 1024 * 1024)
        model.selectedAgent = 1
        _ = model.selectedTimelineItems
        model.selectedAgent = 2
        _ = model.selectedTimelineItems
        model.selectedWorkspaceID = "different-workspace"
        _ = model.selectedTimelineItems
        let sequence = model.presentationProjectionSequence
        model.selectedAgent = 1
        _ = model.selectedTimelineItems
        #expect(model.presentationProjectionSequence > sequence)
        #expect(model.transcriptPresentationCache.totalCost <= 1024 * 1024)
    }

    @Test func fortySmallBranchesStayWarmAndOlderVisibleSnapshotsCannotReplaceNewerPreparation() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        let input = TranscriptProjectionInput(runID: "run", runStatus: "completed", prompt: "Question",
            history: [], transcript: AgentTranscript(finalMessage: "Complete answer"),
            runError: nil, workerError: nil, pendingSubmission: nil)
        let blocks = [TranscriptBlock(id: "answer", kind: .output, text: "Complete answer", isFinalAnswer: true)]
        func snapshot(_ generation: UInt64) -> TranscriptPresentationSnapshot {
            TranscriptPresentationSnapshot(input: input, blocks: blocks, generation: generation,
                timelineInput: nil, timelineBlocks: [], timelineGeneration: 0,
                itemsInput: nil, items: [], itemsGeneration: 0)
        }
        let keys = (0..<40).map { index in
            TranscriptPresentationKey(machineID: "local", workspaceID: "workspace-\(index / 8)",
                conversationID: "conversation", runID: "run", agent: index % 8 + 1)
        }
        for key in keys { model.transcriptPresentationCache.put(snapshot(2), for: key) }
        for key in keys {
            model.transcriptPresentationCache.put(snapshot(1), for: key)
            #expect(model.transcriptPresentationCache.value(for: key, matching: input)?.generation == 2)
        }
        #expect(model.transcriptPresentationCache.totalCost <= 32 * 1024 * 1024)
    }

    @Test func pendingMessageLooksUpTheRawInputBeforeBuildingBranchHistory() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        model.pendingPromptSubmission = pending(model)
        let blocks = model.selectedTranscriptBlocks
        let usesRawHistory = model.transcriptProjectionInput?.history == model.resumeHistory
        #expect(usesRawHistory)
        #expect(blocks.filter { $0.text == "Next question" }.count == 1)
        #expect(blocks.contains { $0.text == "Candidate 1" })
        let generation = model.transcriptProjectionGeneration
        for _ in 0..<30 { #expect(model.selectedTranscriptBlocks == blocks) }
        #expect(model.transcriptProjectionGeneration == generation)
        model.selectedAgent = 2
        #expect(model.selectedTranscriptBlocks.contains { $0.text == "Candidate 2" })
        #expect(!model.selectedTranscriptBlocks.contains { $0.text == "Candidate 1" })
        model.resumeHistory.append(.init(category: "user", text: "Later history", id: "later"))
        #expect(model.selectedTranscriptBlocks.contains { $0.text == "Later history" })
    }

    @Test func longWorkHistoryDoesNotGetReprocessedForEveryViewRead() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        model.pendingPromptSubmission = pending(model)
        let payload = String(repeating: "work output with whitespace ", count: 160)
        model.transcripts[1]?.blocks = (0..<1200).map {
            TranscriptBlock(id: "work-\($0)", kind: .thought, text: "\($0): \(payload)")
        }
        let start = ContinuousClock.now
        let blocks = model.selectedTranscriptBlocks
        let cold = start.duration(to: .now)
        let warmStart = ContinuousClock.now
        for _ in 0..<30 { #expect(model.selectedTranscriptBlocks == blocks) }
        let warm = warmStart.duration(to: .now)
        print("Long pending reading: cold=\(cold), 30 warm getters=\(warm)")
        #expect(warm < .milliseconds(100))
        #expect(model.transcripts[1]?.blocks.count == 1200)
        #expect(model.pendingPromptSubmission?.id == "pending-reading-only")
    }

    @Test func lazyWhitespaceComparisonKeepsTheExistingUnicodeAndTurnSemantics() {
        let texts = ["a", "a b", " a\t b\n", "a\u{00a0}b", "a\u{2003}b", "a\u{2028}b",
            "ab", "a bc", "a b c", "中文 测试", "中文\n测试", "é", "e\u{301}", "é x", "e\u{301}\t x"]
        for lhs in texts {
            for rhs in texts {
                let expected = lhs.split(whereSeparator: \Character.isWhitespace).joined(separator: " ")
                    == rhs.split(whereSeparator: \Character.isWhitespace).joined(separator: " ")
                let transcript = AgentTranscript(blocks: [
                    .init(id: "first", kind: .thought, text: lhs),
                    .init(id: "second", kind: .thought, text: rhs)])
                let result = TranscriptFormatter.blocks(history: [], prompt: "", transcript: transcript)
                #expect(result.count == (expected ? 1 : 2))
            }
        }
        let repeated = AgentTranscript(blocks: [.init(id: "first", kind: .user, text: "Again"),
            .init(id: "second", kind: .user, text: "Again")])
        #expect(TranscriptFormatter.blocks(history: [], prompt: "", transcript: repeated).count == 2)
    }

    @Test func transportChangesCannotInvalidateBodyOrTimelineButContentChangesMust() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        let first = model.selectedTimelineItems
        let generation = model.presentationProjectionSequence
        model.transcripts[1]?.cursor += 1000
        model.transcripts[1]?.eventIDs.insert(999)
        model.transcripts[1]?.hasMore.toggle()
        model.transcripts[1]?.finalMessageCursor += 1
        model.transcripts[1]?.finalMessageReadStatus = "success"
        model.transcripts[1]?.finalMessageID = "replacement-artifact-with-same-visible-body"
        model.followUpSecondsRemaining = 3
        #expect(model.selectedTimelineItems == first)
        #expect(model.presentationProjectionSequence == generation)
        model.transcripts[1]?.finalMessageHasMore.toggle()
        _ = model.selectedTimelineItems
        #expect(model.presentationProjectionSequence > generation)
        model.transcripts[1]?.finalMessage = "Newest visible answer"
        #expect(model.selectedTranscriptBlocks.contains { $0.text.contains("Newest visible answer") })
        model.transcripts[1]?.error = "A real execution error"
        #expect(model.selectedTranscriptBlocks.contains { $0.text == "A real execution error" })
        model.transcripts[1]?.sourceAgent = 2
        let beforeIdentity = model.transcriptProjectionGeneration
        _ = model.selectedTranscriptBlocks
        #expect(model.transcriptProjectionGeneration != beforeIdentity)
    }

    @Test func backgroundCursorOnlyMergeReusesProjectionButStillAdvancesTransport() async throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        let run = try #require(model.activeRun)
        let base = try #require(model.transcripts[1])
        let input = TranscriptProjectionInput(runID: run.runId, runStatus: run.status, prompt: run.prompt,
            history: model.resumeHistory, transcript: base, runError: run.error, workerError: run.workerError,
            pendingSubmission: nil)
        let sentinel = [TranscriptBlock(id: "already-prepared", kind: .output, text: "Ready snapshot")]
        let previous = PreparedConversationProjection(input: input, blocks: sentinel)
        let detail = try JSONDecoder().decode(RunAgentDetail.self, from: Data("""
            {"runId":"cache-run","agent":1,"name":"Agent 1","status":"success",
             "reasoningTokenCounts":{},"rejected":false,"recommended":false,"finalized":false,
             "stdoutTail":"","stderrTail":"","finalMessage":"Candidate 1",
             "transcriptPage":{"schemaVersion":1,"cursor":200,"nextCursor":201,
                               "hasMore":false,"total":201,"items":[]}}
            """.utf8))
        let page = ConversationAgentRead(agent: 1, requested: base, status: "success", detail: detail)
        let merged = try #require(await ConversationReadProjection.merge(page, into: base, run: run,
            history: model.resumeHistory, previous: previous))
        #expect(merged.input.transcript.cursor == 201)
        #expect(merged.blocks == sentinel)
        let late = try #require(await ConversationReadProjection.merge(page, into: merged.input.transcript,
            run: run, history: model.resumeHistory, previous: merged))
        #expect(late.input.transcript.cursor == 201)
        #expect(late.blocks == sentinel)
        var changed = base
        changed.blocks.append(.init(id: "new", kind: .output, text: "A new reply"))
        let newer = PreparedConversationProjection(run: run, history: model.resumeHistory,
            transcript: changed, previous: previous)
        #expect(newer.blocks != sentinel)
        #expect(newer.blocks.contains { $0.text == "A new reply" })
    }

    @Test func equalTextCannotReuseAnotherMessageIdentityOrHideChangedHistoryAndRunErrors() throws {
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedAgent = 1
        model.transcripts[1]?.finalMessage = ""
        model.transcripts[1]?.blocks = [.init(id: "message-before", kind: .output, text: "Same text")]
        _ = model.selectedTranscriptBlocks
        model.transcripts[1]?.blocks = [.init(id: "message-after", kind: .output, text: "Same text")]
        let rebound = model.selectedTranscriptBlocks
        #expect(rebound.contains { $0.id == "message-after" })
        #expect(!rebound.contains { $0.id == "message-before" })
        model.resumeHistory[0] = .init(category: "user", text: "Same source, changed history", id: "source-new")
        #expect(model.selectedTranscriptBlocks.contains { $0.id == "source-new" && $0.text == "Same source, changed history" })
        let currentRun = try #require(model.activeRun)
        let encodedRun = try JSONEncoder().encode(currentRun)
        var runBody = try #require(JSONSerialization.jsonObject(with: encodedRun) as? [String: Any])
        runBody["error"] = "Actual run error"
        runBody["status"] = "failed"
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: runBody))
        #expect(model.selectedTranscriptBlocks.contains { $0.id == "run-error" && $0.text == "Actual run error" })
        runBody["status"] = "discarded"
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: runBody))
        #expect(!model.selectedTranscriptBlocks.contains { $0.id == "run-error" })
    }

    @Test func suppliedReadOnlyCacheReplaysWithoutProductionCallsOrWrites() throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_CLIENT_HANG_CACHE_PATH"] else { return }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .millisecondsSince1970
        let record = try decoder.decode(WorkspaceRunViewCacheRecord.self,
            from: Data(contentsOf: URL(fileURLWithPath: path)))
        let model = try model()
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = record.key.workspaceID
        model.selectedConversationID = record.key.runID
        model.activeRun = record.runSummary
        model.resumeHistory = record.snapshot.conversationHistory
        model.transcripts = record.snapshot.transcripts
        model.selectedAgent = record.snapshot.selectedAgent
        model.pendingPromptSubmission = pending(model)
        let start = ContinuousClock.now
        let blocks = model.selectedTranscriptBlocks
        let cold = start.duration(to: .now)
        let warmStart = ContinuousClock.now
        for _ in 0..<30 { #expect(model.selectedTranscriptBlocks == blocks) }
        let warm = warmStart.duration(to: .now)
        print("Read-only production-sized cache: cold=\(cold), 30 warm getters=\(warm), blocks=\(blocks.count)")
        #expect(warm < .milliseconds(100))
    }

    private func pending(_ model: AppModel) -> PersistedPromptSubmission {
        PersistedPromptSubmission(id: "pending-reading-only", machineID: model.currentMachineContextID,
            workspaceID: model.selectedWorkspaceID ?? "", prompt: "Next question", settings: RunSettings(),
            sourceRunID: model.activeRun?.runId, sourceAgent: 1, resumeSessionID: "",
            confirmLargeRun: false, attempt: 0, status: .failed, error: "Receipt unknown")
    }

    private func model() throws -> AppModel {
        let model = AppModel(windowID: UUID())
        model.appLanguage = .chinese
        model.selectedWorkspaceID = "cache-workspace"
        model.selectedConversationID = "cache-conversation"
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: Data("""
            {"runId":"cache-run","status":"completed","workspace":"/qa","prompt":"Compare answers",
             "createdAt":"2026-10-04T00:00:00Z","updatedAt":"2026-10-04T00:00:00Z","config":{},
             "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
             "workerActive":false,"workerAvailable":false,"eventCount":200,
             "agents":[{"agent":1,"name":"Agent 1","status":"success","reasoningTokenCounts":{},
                        "rejected":false,"recommended":false,"finalized":false},
                       {"agent":2,"name":"Agent 2","status":"success","reasoningTokenCounts":{},
                        "rejected":false,"recommended":false,"finalized":false}]}
            """.utf8))
        model.resumeHistory = (0..<200).map { ResumeHistoryEntry(category: "assistant", text: "Long shared history \($0)") }
        for agent in 1...2 {
            model.transcripts[agent] = AgentTranscript(sourceRunID: "cache-run", sourceAgent: agent,
                cursor: 200, hasMore: false, finalMessage: "Candidate \(agent)", finalMessageHasMore: false)
        }
        return model
    }
}
