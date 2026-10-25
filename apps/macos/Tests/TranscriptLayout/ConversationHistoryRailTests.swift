import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ConversationHistoryRailTests {
    @Test
    func questionsUseMessageIdentityNotTextOrCandidateRows() {
        let items: [ConversationTimelineItem] = [
            .message(.init(id: "question-a", kind: .user, text: "Again", isHistorical: true)),
            .message(.init(id: "output", kind: .output, text: "Again")),
            .message(.init(id: "thought", kind: .thought, text: "Again")),
            .message(.init(id: "question-b", kind: .user, text: "Again")),
            .message(.init(id: "empty", kind: .user, text: " \n "))
        ]
        let entries = ConversationHistoryEntry.make(from: items)
        #expect(entries.map(\.id) == ["message:question-a", "message:question-b"])
        #expect(entries.map(\.itemIndex) == [0, 3])
        #expect(entries.map(\.text) == ["Again", "Again"])
    }

    @Test
    func denseHistoriesRemainBoundedAndEveryQuestionIsAddressable() {
        let geometry = ConversationHistoryRailGeometry(count: 5_000, height: 600)
        #expect(geometry.tickCount <= 90)
        #expect(geometry.entryIndex(forTick: 0) == 0)
        #expect(geometry.entryIndex(forTick: geometry.tickCount - 1) == 4_999)
        for index in 0..<5_000 {
            #expect(geometry.index(at: geometry.y(for: index)) == index)
        }
        #expect(geometry.index(at: -100) == 0)
        #expect(geometry.index(at: 1_000) == 4_999)
    }

    @Test
    func activeQuestionFollowsDocumentPositionNotHover() {
        let offsets: [CGFloat] = [24, 700, 1100]
        #expect(ConversationHistoryRailGeometry.activeIndex(offset: 0, questionOffsets: offsets) == 0)
        #expect(ConversationHistoryRailGeometry.activeIndex(offset: 800, questionOffsets: offsets) == 1)
        #expect(ConversationHistoryRailGeometry.activeIndex(offset: 1100, questionOffsets: offsets) == 2)
        #expect(ConversationHistoryRailGeometry.activeIndex(offset: 1, questionOffsets: []) == nil)
        let width = ConversationHistoryRailGeometry.width
        #expect(width(0, false) > width(12, false))
        #expect(width(12, false) > width(48, false))
        #expect(width(nil, true) > width(nil, false))
    }

    @Test
    func hoveringAnimatesOnlyTheRailAndAccessibilityJumpsToTheSameQuestion() throws {
        let rail = makeRail()
        var selected: [String] = []
        rail.navigate = { selected.append($0) }
        rail.reduceMotion = false
        rail.setActiveIndex(11)
        rail.hover(at: 3)
        #expect(selected.isEmpty)
        #expect(rail.activeIndex == 11)
        #expect(rail.hoveredIndex == 3)
        let ticks = rail.layer?.sublayers?.filter { $0.name == "history-tick" } ?? []
        #expect(ticks.count == 12)
        #expect(ticks.contains { $0.animation(forKey: "history-hover") != nil })
        let preview = try #require(rail.subviews.first)
        #expect(!preview.isHidden)
        #expect(rail.bounds.contains(preview.frame))
        #expect(rail.hitTest(NSPoint(x: 500, y: 200)) == nil,
                "The transparent overlay must not intercept text selection or scrolling.")
        let buttons = try #require(rail.accessibilityChildren() as? [NSAccessibilityElement])
        #expect(buttons.allSatisfy { $0.isAccessibilityEnabled() })
        #expect(buttons[3].accessibilityPerformPress())
        #expect(selected == ["message:question-3"])
        #expect(preview.isHidden)
    }

    @Test
    func escapeRestoresEditorAfterNativeFocusTransfer() throws {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 640, height: 600),
            styleMask: [.titled], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let editor = NSTextView(frame: NSRect(x: 0, y: 500, width: 500, height: 80))
        editor.string = "Unsent draft"
        let rail = makeRail()
        window.contentView?.addSubview(editor)
        window.contentView?.addSubview(rail)
        window.initialFirstResponder = editor
        #expect(window.makeFirstResponder(editor))
        editor.setSelectedRange(NSRange(location: 3, length: 2))
        #expect(window.makeFirstResponder(rail))
        rail.hover(at: 4)
        rail.cancelOperation(nil)
        #expect(window.firstResponder === editor)
        #expect(editor.selectedRange() == NSRange(location: 3, length: 2))
        #expect(editor.string == "Unsent draft")
        #expect(rail.hoveredIndex == nil)
    }

    @Test
    func dismissingMovingPreviewClearsItsPreviousAnimation() throws {
        let rail = makeRail()
        rail.reduceMotion = false
        rail.hover(at: 2)
        rail.hover(at: 8)
        let preview = try #require(rail.subviews.first)
        #expect(preview.layer?.animation(forKey: "history-preview") != nil)
        rail.dismissPreview()
        #expect(preview.isHidden)
        #expect(preview.layer?.animationKeys()?.isEmpty != false)
        rail.hover(at: 1)
        #expect(!preview.isHidden)
        #expect(rail.bounds.contains(preview.frame))
    }

    @Test
    func reduceMotionNarrowLayoutAndContextReplacementStayUsable() throws {
        let rail = makeRail()
        rail.frame.size = NSSize(width: 240, height: 240)
        rail.reduceMotion = true
        rail.configure(entries: rail.entries, language: .english, textSize: .extraLarge,
                       appearance: .light, resetsInteraction: false)
        rail.hover(at: 11)
        let preview = try #require(rail.subviews.first)
        #expect(rail.bounds.contains(preview.frame))
        #expect(rail.layer?.sublayers?.allSatisfy { ($0.animationKeys() ?? []).isEmpty } == true)
        rail.frame.size.height = 160
        rail.configure(entries: [.init(id: "first", itemIndex: 0, text: "First"),
                                 .init(id: "long", itemIndex: 1, text: String(repeating: "Long question. ", count: 200))],
                       language: .english, textSize: .extraLarge, appearance: .light,
                       resetsInteraction: true)
        rail.hover(at: 1)
        #expect(rail.bounds.contains(preview.frame))
        #expect(preview.frame.height > 100, "Long questions must wrap instead of collapsing to one truncated line.")
        rail.configure(entries: [], language: .english, textSize: .standard,
                       appearance: .dark, resetsInteraction: true)
        #expect(rail.isHidden)
        #expect(rail.hoveredIndex == nil)
        #expect(rail.accessibilityChildren()?.isEmpty == true)
        #expect(rail.hitTest(NSPoint(x: 8, y: 100)) == nil)
    }

    private func makeRail() -> ConversationHistoryRailView {
        _ = NSApplication.shared
        let rail = ConversationHistoryRailView(frame: NSRect(x: 0, y: 0, width: 640, height: 480))
        rail.configure(entries: (0..<12).map {
            .init(id: "message:question-\($0)", itemIndex: $0 * 2, text: "Question \($0)\nA second line.")
        }, language: .english, textSize: .standard, appearance: .dark, resetsInteraction: true)
        return rail
    }
}
