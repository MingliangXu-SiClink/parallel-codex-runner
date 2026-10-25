import AppKit
import Testing
@testable import PCRRemoteMac

struct WorkProcessTests {
    @Test
    func streamedAnswerReplacesSnapshotsRatherThanRepeatingThem() {
        var blocks: [TranscriptBlock] = []
        for (event, text) in ["RE", "REVIEW", "REVIEW-FEATURES-OK"].enumerated() {
            TranscriptFormatter.append(items: [RemoteTranscriptItem(id: "text-attempt:message", eventId: event,
                kind: "output", text: text, status: nil, operationId: nil, activityType: nil)], to: &blocks)
        }
        #expect(blocks.count == 1)
        #expect(blocks.first?.text == "REVIEW-FEATURES-OK")
    }

    @Test
    func updatesPreserveRowIdentityAndIgnoreStaleEvents() {
        var blocks: [TranscriptBlock] = []
        func item(_ id: String, event: Int, status: WorkOperationStatus) -> RemoteTranscriptItem {
            RemoteTranscriptItem(id: "event-\(event)", eventId: event, kind: "command", text: "cat a.tex",
                status: nil, operationId: "item_0", activityType: nil,
                operation: WorkOperationRecord(id: id, eventId: event, kind: .fileRead, status: status))
        }
        TranscriptFormatter.append(items: [item("execution1:item0", event: 1, status: .running)], to: &blocks)
        TranscriptFormatter.append(items: [item("execution1:item0", event: 3, status: .completed)], to: &blocks)
        TranscriptFormatter.append(items: [item("execution1:item0", event: 2, status: .running)], to: &blocks)
        TranscriptFormatter.append(items: [item("execution2:item0", event: 4, status: .running)], to: &blocks)
        #expect(blocks.map(\.id) == ["event-1", "event-4"])
        #expect(blocks[0].operation?.status == .completed)
    }

    @Test
    func workSegmentsKeepRepliesInChronologicalOrderAndToggleIndependently() {
        let blocks = [TranscriptBlock(id: "read", kind: .command, text: "cat a.tex"),
            TranscriptBlock(id: "reply", kind: .output, text: "Found the cause."),
            TranscriptBlock(id: "edit", kind: .command, text: "apply patch")]
        let projected = TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1, expanded: true,
            sectionOverrides: ["work-summary-run-1-read": false])
        #expect(projected.map(\.id) == ["work-summary-run-1-read", "reply", "work-summary-run-1-edit", "edit"])
    }

    @Test
    func commentaryStaysVisibleBetweenIndependentlyCollapsedWorkSections() throws {
        let progress = TranscriptBlock(id: "progress", kind: .output, text: "Found the cause.",
                                       activityType: "assistant_progress")
        let blocks = [
            TranscriptBlock(id: "thought", kind: .thought, text: "Checking"),
            TranscriptBlock(id: "read", kind: .command, text: "cat a.swift"),
            progress,
            TranscriptBlock(id: "edit", kind: .activity, text: "Edited a.swift", activityType: "file_change"),
            TranscriptBlock(id: "reply", kind: .output, text: "Checking the result.", activityType: "assistant_progress"),
            TranscriptBlock(id: "test", kind: .command, text: "swift test"),
            TranscriptBlock(id: "final", kind: .output, text: "Done.", isFinalAnswer: true),
        ]
        for expanded in [false, true] {
            let projected = TranscriptFormatter.coalescedVisibleConversation(
                TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1, expanded: expanded))
            let items = ConversationTimelineProjection.make(blocks: projected, switchState: nil)
            let replies = items.compactMap { item -> TranscriptBlock? in
                guard case .message(let block) = item, block.kind == .output else { return nil }
                return block
            }
            #expect(replies.map(\.id) == ["progress", "reply", "final"])
            #expect(replies.first == progress)
            #expect(items.filter {
                guard case .message(let block) = $0 else { return false }
                return block.activityType?.hasPrefix("work_summary") == true
            }.count == 3)
            let groups = items.compactMap { item -> ConversationWorkProcess? in
                guard case .workProcess(let group) = item else { return nil }
                return group
            }
            #expect(groups.count == (expanded ? 3 : 0))
            #expect(groups.flatMap(\.blocks).allSatisfy { $0.kind != .output })
            let toggled = TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1,
                expanded: expanded, sectionOverrides: ["work-summary-run-1-edit": !expanded])
            #expect(toggled.first { $0.id == "progress" } == progress)
            #expect(toggled.first { $0.id == "work-summary-run-1-thought" }?.activityType
                == (expanded ? "work_summary_expanded" : "work_summary"))
            #expect(toggled.first { $0.id == "work-summary-run-1-edit" }?.activityType
                == (expanded ? "work_summary" : "work_summary_expanded"))
        }
        #expect(!TranscriptFormatter.isAgentWorkBlock(progress))
    }

    @Test
    func commentarySnapshotsAndOperationCompletionDoNotRegroupEarlierReplies() throws {
        var blocks: [TranscriptBlock] = []
        func operation(event: Int, status: WorkOperationStatus) -> RemoteTranscriptItem {
            RemoteTranscriptItem(id: "event-\(event)", eventId: event, kind: "command", text: "swift test",
                status: nil, operationId: "test", activityType: nil,
                operation: WorkOperationRecord(id: "test", eventId: event, kind: .command, status: status))
        }
        func progress(event: Int, text: String) -> RemoteTranscriptItem {
            RemoteTranscriptItem(id: "message", eventId: event, kind: "output", text: text,
                status: nil, operationId: nil, activityType: "assistant_progress")
        }
        func timeline() -> [ConversationTimelineItem] {
            ConversationTimelineProjection.make(blocks: TranscriptFormatter.blocksWithWorkDisclosure(
                blocks, runID: "run", agent: 1, expanded: true), switchState: nil)
        }
        TranscriptFormatter.append(items: [operation(event: 1, status: .running),
                                          progress(event: 2, text: "Checking")], to: &blocks)
        let before = timeline()
        TranscriptFormatter.append(items: [progress(event: 3, text: "Checking the result.")], to: &blocks)
        let streamed = timeline()
        #expect(before.map(\.id) == streamed.map(\.id))
        #expect(ConversationTimelineUpdatePlan.make(old: before, new: streamed).reloadAll == false)
        TranscriptFormatter.append(items: [operation(event: 4, status: .completed)], to: &blocks)
        #expect(timeline().map(\.id) == before.map(\.id))
        TranscriptFormatter.append(items: [RemoteTranscriptItem(id: "final", eventId: 5, kind: "output", text: "Done.",
            status: nil, operationId: nil, activityType: "assistant_final")], to: &blocks)
        #expect(Array(timeline().dropLast()).map(\.id) == before.map(\.id))
        #expect(blocks.filter(\.isAssistantProgress).map(\.text) == ["Checking the result."])
    }

    @Test
    func rawCommentaryAndCachedLegacyRepliesAreNeverWorkEvents() throws {
        let event = RunEvent(eventId: 1, timestamp: nil, type: "agent_line", idx: 1, stream: "stdout",
            text: #"{"type":"item.completed","item":{"id":"message","type":"agent_message","phase":"commentary","text":"An intermediate reply."}}"#,
            message: nil, status: nil, reasoningTokens: nil)
        var blocks: [TranscriptBlock] = []
        TranscriptFormatter.append(events: [event], to: &blocks)
        let restored = try JSONDecoder().decode([TranscriptBlock].self, from: JSONEncoder().encode(blocks))
        #expect(restored.first?.isAssistantProgress == true)
        #expect(TranscriptFormatter.blocksWithWorkDisclosure(restored, runID: "run", agent: 1, expanded: false) == restored)
        let history = TranscriptBlock(id: "history", kind: .output, text: "A previous reply.",
            activityType: "assistant_progress", isHistorical: true)
        #expect(TranscriptFormatter.blocksWithWorkDisclosure([history], runID: "run", agent: 1, expanded: false) == [history])
    }

    @Test
    @MainActor
    func compactRowsFitAtEveryTextSizeAndDoNotLeakStylesWhenReused() throws {
        _ = NSApplication.shared
        let item = TranscriptCollectionItem()
        _ = item.view
        let operation = WorkOperationRecord(id: "op", eventId: 1, kind: .fileRead, status: .completed,
            command: "cat very-long-path.tex", targets: [WorkOperationTarget(path: "folder/" + String(repeating: "file", count: 50) + ".tex")])
        let block = TranscriptBlock(id: "op", kind: .command,
            text: WorkOperationPresentation.summary(operation, language: .chinese), operation: operation)
        for appearance in [TranscriptAppearance.light, .dark] {
            for size in AppTextSize.allCases {
                for width: CGFloat in [280, 760, 1400] {
                    let height = TranscriptCollectionItem.preferredHeight(for: block, language: .chinese,
                        textSize: size, accentColor: .orange, appearance: appearance, availableWidth: width)
                    item.configure(block: block, language: .chinese, textSize: size, accentColor: .orange,
                        appearance: appearance, findQuery: "", currentFindOccurrence: nil,
                        availableWidth: width, openURL: { _ in }, activateBlock: { _ in })
                    item.view.frame = NSRect(x: 0, y: 0, width: width, height: height)
                    item.view.layoutSubtreeIfNeeded()
                    func descendants(_ view: NSView) -> [NSView] { view.subviews.flatMap { [$0] + descendants($0) } }
                    let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                    #expect(text.textContainer?.maximumNumberOfLines == 1)
                    #expect(height < 100 * size.scale)
                    let container = try #require(text.textContainer)
                    text.layoutManager?.ensureLayout(for: container)
                    #expect((text.layoutManager?.usedRect(for: container).maxY ?? 0) <= text.bounds.height + 0.5)
                }
            }
        }
        let reply = TranscriptBlock(id: "answer", kind: .output, text: String(repeating: "A long answer.\n\n", count: 50))
        item.configure(block: reply, language: .chinese, textSize: .standard, accentColor: .orange,
            appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 760, openURL: { _ in }, activateBlock: { _ in })
        #expect(item.view.subviews.isEmpty == false)
    }

    @Test
    @MainActor
    func plainThoughtTextDoesNotBecomeItalic() throws {
        let rendered = TranscriptTextRenderer.attributedText(
            for: TranscriptBlock(id: "thought", kind: .thought, text: "Planning the next step"),
            textSize: .standard, accentColor: .orange, appearance: .dark
        )
        let font = try #require(rendered.attribute(.font, at: 0, effectiveRange: nil) as? NSFont)
        #expect(!NSFontManager.shared.traits(of: font).contains(.italicFontMask))
    }

    @Test
    func missingStatisticsStayMissingAndLanguageIsClientOwned() {
        let op = WorkOperationRecord(id: "op", eventId: 1, kind: .fileChange, status: .completed,
            targets: [WorkOperationTarget(path: "a.tex", change: .create)])
        #expect(WorkOperationPresentation.summary(op, language: .chinese) == "已创建 a.tex")
        #expect(WorkOperationPresentation.summary(op, language: .english) == "Created a.tex")
    }
}
