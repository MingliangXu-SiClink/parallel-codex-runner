import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct FocusedReadTests {
    @Test func sharedRunPagePreservesAgentBoundariesAndDoesNotRegressAnAdvancedReader() throws {
        let first = RemoteTranscriptItem(id: "event-5", eventId: 5, kind: "output", text: "one",
            status: nil, operationId: nil, activityType: nil)
        let second = RemoteTranscriptItem(id: "event-7", eventId: 7, kind: "output", text: "two",
            status: nil, operationId: nil, activityType: nil)
        let page = RunTranscriptPage(runId: "run-a", cursor: 4, nextCursor: 10, hasMore: true, total: 20,
            agents: [.init(agent: 1, items: [first]), .init(agent: 2, items: [second])])
        let a = AgentTranscript(sourceRunID: "run-a", sourceAgent: 1, cursor: 4, hasMore: true)
        let b = AgentTranscript(sourceRunID: "run-a", sourceAgent: 2, cursor: 6, hasMore: true)
        let resultA = try #require(ConversationReadProjection.mergeRunPage(page, into: a, runID: "run-a", agent: 1))
        let resultB = try #require(ConversationReadProjection.mergeRunPage(page, into: b, runID: "run-a", agent: 2))
        #expect(resultA.blocks.map(\.text) == ["one"])
        #expect(resultB.blocks.map(\.text) == ["two"])
        #expect(resultA.cursor == 10 && resultB.cursor == 10)
        #expect(ConversationReadProjection.mergeRunPage(page, into: resultA, runID: "run-a", agent: 1) == resultA)
        #expect(ConversationReadProjection.mergeRunPage(page, into: a, runID: "run-b", agent: 1) == nil)
        var gap = a
        gap.cursor = 2
        #expect(ConversationReadProjection.mergeRunPage(page, into: gap, runID: "run-a", agent: 1) == nil)
        var ahead = a
        ahead.cursor = 12
        #expect(ConversationReadProjection.mergeRunPage(page, into: ahead, runID: "run-a", agent: 1) == nil)
        let empty = AgentTranscript(sourceRunID: "run-a", sourceAgent: 3, cursor: 4, hasMore: true)
        let resultC = try #require(ConversationReadProjection.mergeRunPage(page, into: empty, runID: "run-a", agent: 3))
        #expect(resultC.blocks.isEmpty && resultC.cursor == 10)
    }

    private final class Counter: @unchecked Sendable {
        let lock = NSLock()
        var count = 0
        func increment() { lock.lock(); count += 1; lock.unlock() }
        var value: Int { lock.lock(); defer { lock.unlock() }; return count }
    }

    @Test(arguments: [false, true])
    func switchingFocusDoesNotWaitForThePreviousFocusedRPC(isLiveContent: Bool) async throws {
        let backgroundStarted = Counter(), previousStarted = Counter(), delivered = Counter()
        let releaseBackground = DispatchSemaphore(value: 0), releasePrevious = DispatchSemaphore(value: 0)
        defer { releaseBackground.signal(); releaseBackground.signal(); releasePrevious.signal() }
        let background = (0..<2).map { _ in Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: true, isLiveContent: isLiveContent) {
                backgroundStarted.increment()
                _ = releaseBackground.wait(timeout: .now() + 5)
            }
        } }
        var limit = ContinuousClock.now + .seconds(2)
        while backgroundStarted.value < 2 && ContinuousClock.now < limit {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(backgroundStarted.value == 2)
        let previousDemand = ClientReadDemand()
        previousDemand.setFocused(true)
        let previous = Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: true,
                isLiveContent: isLiveContent, demand: previousDemand) {
                previousStarted.increment()
                _ = releasePrevious.wait(timeout: .now() + 5)
            }
        }
        limit = ContinuousClock.now + .seconds(2)
        while previousStarted.value == 0 && ContinuousClock.now < limit {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(previousStarted.value == 1)
        previousDemand.setFocused(false)
        let latestDemand = ClientReadDemand()
        latestDemand.setFocused(true)
        let began = ContinuousClock.now
        let latest = Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: true,
                isLiveContent: isLiveContent, demand: latestDemand) { delivered.increment() }
        }
        limit = began + .seconds(1)
        while delivered.value == 0 && ContinuousClock.now < limit {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(delivered.value == 1)
        #expect(began.duration(to: .now) < .seconds(1))
        releasePrevious.signal(); releaseBackground.signal(); releaseBackground.signal()
        try await previous.value
        try await latest.value
        for task in background { try await task.value }
        #expect(previousStarted.value == 1 && delivered.value == 1)
    }

    @Test func promotionBypassesOccupiedBackgroundSlotsWithoutDuplicatingRPC() async throws {
        let release = DispatchSemaphore(value: 0), blocked = Counter(), delivered = Counter()
        let first = (0..<2).map { _ in Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: true, isLiveContent: true) {
                blocked.increment()
                _ = release.wait(timeout: .now() + 5)
            }
        } }
        defer { release.signal(); release.signal() }
        let limit = ContinuousClock.now + .seconds(2)
        while blocked.value != 2 && ContinuousClock.now < limit { try await Task.sleep(for: .milliseconds(5)) }
        #expect(blocked.value == 2)
        let demand = ClientReadDemand()
        let selected = Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: true,
                isLiveContent: true, demand: demand) { delivered.increment() }
        }
        try await Task.sleep(for: .milliseconds(30))
        #expect(delivered.value == 0)
        let began = ContinuousClock.now
        demand.setFocused(true)
        demand.setFocused(false)
        demand.setFocused(true)
        try await selected.value
        #expect(began.duration(to: .now) < .seconds(1))
        release.signal(); release.signal()
        for task in first { try await task.value }
        #expect(delivered.value == 1)
    }
}
