import Foundation
import Testing
@testable import PCRRemoteMac

struct ActivityProjectionTests {
    @Test(arguments: ["2026-10-07T08:31:00Z", "2026-10-07T08:31:00.654321+00:00",
                      "2026-10-07T16:31:00.654321+08:00", "2026-10-07T03:31:00-05:00",
                      "not-a-date", "", "2026-10-07T08:31:00.000Z"])
    func projectedDatesPreserveTheWireFormatAndParserSemantics(value: String) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let wire: [String: Any] = ["state": "executing", "observed_at": value,
            "last_output_at": value, "wait_until": value, "active_operation_count": 2]
        let record = try decoder.decode(AgentActivityRecord.self, from: JSONSerialization.data(withJSONObject: wire))
        let original = parseInternetDate(value)
        #expect(record.observedAt == value && record.lastOutputAt == value && record.waitUntil == value)
        // ISO8601DateFormatter truncated sub-millisecond precision; the new
        // immutable parser retains it without changing the displayed minute.
        if let original {
            let projected = try #require(record.observedAtDate)
            #expect(abs(projected.timeIntervalSince(original)) < 0.001)
            #expect(AgentActivityPresentation.clock(value) == AgentActivityPresentation.clock(date: projected))
        } else {
            #expect(record.observedAtDate == nil)
        }
        #expect(record.lastOutputAtDate == record.observedAtDate)
        #expect(record.waitUntilDate == record.observedAtDate)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = try #require(JSONSerialization.jsonObject(with: encoder.encode(record)) as? [String: Any])
        #expect(encoded.keys.sorted() == wire.keys.sorted())
        #expect(encoded["observed_at"] as? String == value)
        #expect(try decoder.decode(AgentActivityRecord.self, from: encoder.encode(record)) == record)
    }

    @Test
    func minuteKeysCompareAcrossOffsetsAndPreserveDatesAndDeadlines() {
        let first = AgentActivityRecord(state: "waiting", observedAt: "2026-10-07T08:31:02Z",
            lastOutputAt: "2026-10-07T08:30:10Z", waitUntil: "2026-10-07T08:40:02Z")
        let sameMinute = AgentActivityRecord(state: "waiting", observedAt: "2026-10-07T16:31:59+08:00",
            lastOutputAt: "2026-10-07T08:30:59.999999Z", waitUntil: first.waitUntil)
        #expect(first != sameMinute && first.readerState == sameMinute.readerState)
        let nextDay = AgentActivityRecord(state: "waiting", observedAt: "2026-10-08T08:31:02Z",
            lastOutputAt: first.lastOutputAt, waitUntil: first.waitUntil)
        #expect(first.readerState != nextDay.readerState)
        let changedDeadline = AgentActivityRecord(state: "waiting", observedAt: first.observedAt,
            lastOutputAt: first.lastOutputAt, waitUntil: "2026-10-07T08:40:03Z")
        #expect(first.readerState != changedDeadline.readerState)
    }

    @Test
    func repeatedFiveAgentEqualityUsesProjectedValuesWithoutClockFormatting() throws {
        let decoder = JSONDecoder()
        var agent = try decoder.decode(RunAgentSummary.self, from: Data("""
        {"agent":1,"name":"Agent 1","status":"running","reasoningTokenCounts":{},
         "recommended":false,"finalized":false,"rejected":false}
        """.utf8))
        agent.activity = AgentActivityRecord(state: "executing",
            observedAt: "2026-10-07T08:31:00.654321+00:00",
            lastOutputAt: "2026-10-07T08:30:10.123456+00:00", activeOperationCount: 2)
        let lhs = ConversationAgentSwitchState(runID: "run", agents: Array(repeating: agent, count: 5),
            selectedAgent: 1, recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
        var changed = agent
        changed.activity = AgentActivityRecord(state: "executing",
            observedAt: "2026-10-07T08:31:59Z", lastOutputAt: "2026-10-07T08:30:59Z", activeOperationCount: 2)
        changed.seconds = 999
        let rhs = ConversationAgentSwitchState(runID: "run", agents: Array(repeating: changed, count: 5),
            selectedAgent: 1, recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
        let start = ContinuousClock.now
        var matches = 0
        for _ in 0..<5_000 { if equal(lhs, rhs) { matches += 1 } }
        #expect(matches == 5_000)
        print("Activity equality: 5000 five-Agent comparisons, \(start.duration(to: .now)); dates decoded before measurement")
    }

    @inline(never)
    private func equal(_ lhs: ConversationAgentSwitchState, _ rhs: ConversationAgentSwitchState) -> Bool { lhs == rhs }
}
