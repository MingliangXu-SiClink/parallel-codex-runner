import Combine
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ActiveRunPublicationTests {
    @Test
    func liveMetricsStayCurrentWithoutInvalidatingTheConversation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let model = AppModel(windowID: UUID(), desktopStateStore: DesktopStateStore(directory: directory))
        defer { model.stopWindowObservation(); try? FileManager.default.removeItem(at: directory) }
        var value = try run()
        model.activeRun = value
        model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"workspace","path":"/qa","name":"QA","lastRunId":"run",
         "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
        """.utf8))]
        model.selectedWorkspaceID = "workspace"
        model.recordRunInConversationIndex(value, select: false)
        var readerPublications = 0
        var metricPublications = 0
        let reader = model.objectWillChange.sink { readerPublications += 1 }
        let metrics = model.activeRunMetrics.objectWillChange.sink { metricPublications += 1 }
        defer { reader.cancel(); metrics.cancel() }
        for index in 1...200 {
            value.eventCount = index
            value.updatedAt = "poll-\(index)"
            value.agents[0].seconds = Double(index)
            value.agents[0].reasoningTokens = index
            value.agents[0].reasoningTokenCounts = [String(index): 1]
            value.agents[0].phaseTimings = ["resuming": Double(index)]
            value.phaseTimings = ["baseline": Double(index)]
            model.activeRun = value
            model.recordRunInConversationIndex(value, select: false)
        }
        #expect(readerPublications == 0)
        #expect(metricPublications == 200)
        #expect(model.activeRun?.eventCount == 200)
        #expect(model.activeRunMetrics.agent(runID: "run", index: 1)?.reasoningTokens == 200)
        #expect(model.activeRunMetrics.agent(runID: "other-run", index: 1) == nil)
        #expect(model.activeRunMetrics.runPhaseTimings == ["baseline": 200])
        #expect(model.activeRunMetrics.agent(runID: "run", index: 1)?.phaseTimings == ["resuming": 200])
        value.agents[0].status = "success"
        model.activeRun = value
        #expect(readerPublications == 1)
        value.agents[0].reasoningTokens = 201
        model.activeRun = value
        #expect(readerPublications == 2)
        value = try replacing(value, changes: ["status": "completed"])
        model.activeRun = value
        #expect(readerPublications == 3)
        value.continuationAgent = 1
        model.activeRun = value
        #expect(readerPublications == 4)
    }

    @Test
    func canonicalPollingTimestampsAndMetricsDoNotHideSemanticChanges() throws {
        func snapshot(at time: String, tokens: Int = 1, phase: TurnPhase = .primaryRunning,
                      title: String = "Question", canContinue: Bool = true) -> ConversationSnapshot {
            ConversationSnapshot(machine: MachineRecord(machineId: "m", updatedAt: time),
                workspace: WorkspaceRecord(workspaceId: "workspace", updatedAt: time),
                conversation: ConversationRecord(conversationId: "c", title: title, updatedAt: time),
                turn: TurnRecord(turnId: "t", runId: "run", phase: phase, updatedAt: time),
                candidates: [AgentCandidateRecord(candidateId: "a", agentIndex: 1,
                    status: .running, seconds: Double(tokens), reasoningTokens: Int64(tokens),
                    reasoningTokenCounts: [String(tokens): 1], canContinue: canContinue)])
        }
        func withSnapshot(_ value: ConversationSnapshot) throws -> RunSummary {
            let object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(value))
            return try replacing(run(), changes: ["conversationSnapshot": object])
        }
        let baseline = try withSnapshot(snapshot(at: "first"))
        let polled = try withSnapshot(snapshot(at: "second", tokens: 100))
        #expect(baseline != polled)
        #expect(baseline.readerState == polled.readerState)
        for changed in [snapshot(at: "third", phase: .finalizing),
                        snapshot(at: "third", title: "Renamed"),
                        snapshot(at: "third", canContinue: false)] {
            #expect(try withSnapshot(changed).readerState != baseline.readerState)
        }
    }

    private func replacing(_ value: RunSummary, changes: [String: Any]) throws -> RunSummary {
        var object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as? [String: Any])
        object.merge(changes) { _, new in new }
        return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func run() throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"run","status":"running","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-10-04T00:00:00Z","updatedAt":"2026-10-04T00:00:00Z","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":true,"workerAvailable":true,"eventCount":0,
         "agents":[{"agent":1,"name":"Agent 1","status":"running","seconds":0,
         "reasoningTokenCounts":{},"rejected":false,"recommended":false,"finalized":false}]}
        """.utf8))
    }
}
