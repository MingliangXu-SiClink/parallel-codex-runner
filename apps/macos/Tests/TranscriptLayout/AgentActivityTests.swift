import Foundation
import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct AgentActivityTests {
    private func agent(activity: AgentActivityRecord? = nil, status: String = "running") throws -> RunAgentSummary {
        var result = try JSONDecoder().decode(RunAgentSummary.self, from: Data("""
        {"agent":1,"name":"Agent 1","status":"\(status)","reasoningTokenCounts":{},
         "recommended":false,"finalized":false,"rejected":false}
        """.utf8))
        result.activity = activity
        return result
    }

    @Test
    func waitingIsNotExecutionAndDoesNotClaimExternalJobProgress() throws {
        let waiting = try agent(activity: AgentActivityRecord(state: "waiting",
            observedAt: "2026-10-06T08:40:58Z", waitUntil: "2026-10-06T10:10:58Z"))
        #expect(waiting.isRunning && waiting.canStop)
        #expect(waiting.isObservedWaiting && !waiting.hasObservedActiveExecution)
        #expect(waiting.compactStatusLabel(language: .chinese) == "定时等待")
        #expect(ResultStatusGlyph.symbol(for: "waiting_for_output") == "clock")
        let detail = AgentActivityPresentation.detail(waiting, language: .english, live: true,
            now: try #require(parseInternetDate("2026-10-06T09:00:00Z")))
        #expect(detail.contains("Planned wait until"))
        #expect(detail.contains("External progress unknown"))
        #expect(!detail.contains("tools running"))
        let overdue = AgentActivityPresentation.detail(waiting, language: .english, live: true,
            now: try #require(parseInternetDate("2026-10-06T11:00:00Z")))
        #expect(overdue.contains("end unconfirmed"))
        #expect(waiting.activity?.state == "waiting")
        #expect(AgentActivityPresentation.detail(waiting, language: .english, live: false).hasPrefix("Last observed"))
    }

    @Test
    func activityChangesInvalidateTheSwitcherWithoutChangingSelection() throws {
        let waiting = try agent(activity: AgentActivityRecord(state: "waiting"))
        var before = ConversationAgentSwitchState(runID: "run", agents: [waiting], selectedAgent: 1,
            recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
        before.executionConfirmed = true
        var working = waiting
        working.activity = AgentActivityRecord(state: "executing", activeOperationCount: 2)
        var after = ConversationAgentSwitchState(runID: "run", agents: [working], selectedAgent: 1,
            recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
        after.executionConfirmed = true
        #expect(before != after)
        #expect(before.highlightedAgent == after.highlightedAgent)
        #expect(working.hasObservedActiveExecution)
        #expect(working.compactStatusLabel(language: .english) == "Executing")
    }

    @Test
    func oldRecordsDecodeWithoutInventingActivityOrOutputStatistics() throws {
        #expect(try agent().activity == nil)
        let operation = try JSONDecoder().decode(WorkOperationRecord.self, from: Data("""
        {"id":"op","eventId":1,"kind":"command","status":"running","targets":[],"detailsAvailable":true}
        """.utf8))
        #expect(operation.plannedDurationMs == nil && operation.waitUntil == nil && operation.outputBytes == nil)
        #expect(try agent(status: "success").isObservedWaiting == false)
    }

    @Test
    func waitStartAndEndKeepOneRowAndDoNotInventAnActualDuration() throws {
        var blocks: [TranscriptBlock] = []
        func wait(_ event: Int, _ completed: Bool) -> RunEvent {
            RunEvent(eventId: event, timestamp: "2026-10-06T08:40:58Z", type: "agent_line", idx: 1,
                stream: "stdout", text: "{\"type\":\"item.\(completed ? "completed" : "started")\",\"item\":{\"type\":\"sleep\",\"id\":\"wait\",\"durationMs\":5400000}}",
                message: nil, status: nil, reasoningTokens: nil)
        }
        TranscriptFormatter.append(events: [wait(1, false)], to: &blocks)
        let firstID = blocks.first?.id
        let started = try #require(blocks.first?.operation)
        #expect(started.durationMs == nil)
        #expect(started.plannedDurationMs == 5_400_000)
        #expect(WorkOperationPresentation.icon(started) == "clock")
        #expect(WorkOperationPresentation.summary(started, language: .english).contains("planned until"))
        TranscriptFormatter.append(events: [wait(2, true), wait(1, false)], to: &blocks)
        #expect(blocks.count == 1 && blocks.first?.id == firstID)
        #expect(blocks.first?.operation?.status == .completed)
        #expect(WorkOperationPresentation.summary(try #require(blocks.first?.operation), language: .english) == "Wait ended")
    }

    @Test
    func liveOutputUsesOneRowAndResumesVerifiedPrefixes() throws {
        let cached = WorkOperationDetailPage(text: "epoch 1\n", nextCursor: 8, hasMore: false,
            totalBytes: 8, availability: "available")
        let updated = WorkOperationRecord(id: "cmd", eventId: 3, kind: .command, status: .running,
            command: "train", outputBytes: 16)
        #expect(WorkOperationPresentation.reusableOutputPrefix(cached, cachedEvent: 2, operation: updated) == cached)
        let smaller = WorkOperationRecord(id: "cmd", eventId: 3, kind: .command, status: .completed,
            command: "train", outputBytes: 3)
        #expect(WorkOperationPresentation.reusableOutputPrefix(cached, cachedEvent: 2, operation: smaller) == nil)
        var blocks: [TranscriptBlock] = []
        for event in 1...3 {
            TranscriptFormatter.append(items: [RemoteTranscriptItem(id: "event-\(event)", eventId: event,
                kind: "command", text: "train", status: "running", operationId: "cmd", activityType: nil,
                operation: WorkOperationRecord(id: "cmd", eventId: event, kind: .command, status: .running,
                    command: "train", outputBytes: event * 8))], to: &blocks)
        }
        #expect(blocks.count == 1 && blocks.first?.id == "event-1")
        #expect(blocks.first?.operation?.outputBytes == 24)
    }

    @Test
    func observationChangesWithinOneMinuteDoNotRebuildTheReader() {
        let first = AgentActivityRecord(state: "executing", observedAt: "2026-10-06T10:01:02Z",
            lastOutputAt: "2026-10-06T10:01:02Z", activeOperationCount: 1)
        let later = AgentActivityRecord(state: "executing", observedAt: "2026-10-06T10:01:59Z",
            lastOutputAt: "2026-10-06T10:01:59Z", activeOperationCount: 1)
        #expect(first != later && first.readerState == later.readerState)
        #expect(first.readerState != AgentActivityRecord(state: "waiting", waitUntil: "deadline").readerState)
    }

    @Test
    func nativeActivityStripRendersOffscreenWithStableHeightAtNarrowAndWideSizes() throws {
        _ = NSApplication.shared
        let waiting = try agent(activity: AgentActivityRecord(state: "waiting",
            observedAt: "2026-10-06T08:40:58Z", waitUntil: "2099-10-06T10:10:58Z"))
        var executing = waiting
        executing.activity = AgentActivityRecord(state: "executing", lastOutputAt: "2026-10-06T10:01:02Z",
            activeOperationCount: 2)
        for language in [AppLanguage.chinese, .english] {
            for size in AppTextSize.allCases {
                for width: CGFloat in [360, 760] {
                    for candidate in [waiting, executing] {
                        var state = ConversationAgentSwitchState(runID: "activity-qa", agents: [candidate], selectedAgent: 1,
                            recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
                        state.executionConfirmed = true
                        let view = NSHostingView(rootView: ConversationAgentSwitchBar(language: language,
                            textSize: size, accentColor: .orange, state: state, resultsInspectorVisible: false,
                            accessibilityHidden: false, selectAgent: { _ in }, toggleResults: {})
                            .background(Color(nsColor: .windowBackgroundColor)))
                        view.frame = NSRect(x: 0, y: 0, width: width,
                            height: ConversationAgentSwitchBar.minimumHeight(for: size))
                        view.layoutSubtreeIfNeeded()
                        let bitmap = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
                        view.cacheDisplay(in: view.bounds, to: bitmap)
                        #expect(bitmap.pixelsWide > 0 && bitmap.pixelsHigh > 0)
                        let stripHasPixels = (bitmap.pixelsHigh * 2 / 3..<bitmap.pixelsHigh).contains { y in
                            let values = (0..<bitmap.pixelsWide).compactMap { x -> CGFloat? in
                                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { return nil }
                                return color.redComponent + color.greenComponent + color.blueComponent
                            }
                            return (values.max() ?? 0) - (values.min() ?? 0) > 0.3
                        }
                        #expect(stripHasPixels, "The observation line must actually render, not just reserve height.")
                        #expect(view.window == nil, "Native QA must never create or show a window.")
                        if size == .standard, candidate.isObservedWaiting,
                           let directory = ProcessInfo.processInfo.environment["PCR_ACTIVITY_QA_OUTPUT_DIR"] {
                            let url = URL(fileURLWithPath: directory, isDirectory: true)
                            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                            let filename = "activity-\(language.rawValue)-\(Int(width)).png"
                            try #require(bitmap.representation(using: .png, properties: [:])).write(to: url.appendingPathComponent(filename))
                        }
                    }
                }
            }
        }
    }
}
