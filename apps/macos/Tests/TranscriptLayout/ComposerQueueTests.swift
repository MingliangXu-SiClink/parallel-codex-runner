import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ComposerQueueTests {
    @Test
    func claimedMessageKeepsItsRowSizeAndShowsItsActualDeliveryState() async throws {
        _ = NSApplication.shared
        for width: CGFloat in [350, 1_000] {
            let item = QueuedFollowUp(prompt: "Continue the original conversation without sending it twice")
            let content = ComposerQueueView(items: [item], status: "正在续接原会话", dispatching: true,
                change: { _, _ in Issue.record("A claimed message must not be edited"); return false },
                edit: { _ in Issue.record("A claimed message must not be opened for editing") }, manageQueue: {})
                .environment(\.appLanguage, .chinese).environment(\.colorScheme, .dark).frame(width: width)
            let host = NSHostingView(rootView: content)
            let fitting = host.fittingSize
            #expect(fitting.width <= width + 1 && fitting.height < 150)
            let window = NSWindow(contentRect: NSRect(origin: .zero, size: fitting),
                styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.appearance = NSAppearance(named: .darkAqua)
            window.contentView = host
            try await Task.sleep(for: .milliseconds(40))
            host.layoutSubtreeIfNeeded()
            #expect(ComposerQueueView.pendingCount(items: [item], dispatching: true) == 0)
            let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: image)
            if let output = ProcessInfo.processInfo.environment["PCR_QUEUE_QA_OUTPUT"] {
                try image.representation(using: .png, properties: [:])?.write(to:
                    URL(fileURLWithPath: output).appendingPathComponent("queue-dispatching-\(Int(width)).png"))
            }
            window.close()
        }
    }

    @Test
    func editsOnlyTheRequestedMessageAndPreservesNewlyQueuedItems() throws {
        let first = QueuedFollowUp(prompt: "First")
        let second = QueuedFollowUp(prompt: "Second")
        let newlyQueued = QueuedFollowUp(prompt: "Added while editing")
        let edited = try #require(QueuedFollowUpChange.edit(original: "Second", replacement: "  Updated\nmessage  ")
            .applying(to: [first, second, newlyQueued], id: second.id))
        #expect(edited.map(\.id) == [first.id, second.id, newlyQueued.id])
        #expect(edited.map(\.prompt) == ["First", "Updated\nmessage", "Added while editing"])
    }

    @Test
    func staleEditsCannotResurrectSentMessagesOrOverwriteAnotherEdit() {
        let item = QueuedFollowUp(prompt: "Already edited")
        #expect(QueuedFollowUpChange.edit(original: "Old", replacement: "Replacement")
            .applying(to: [item], id: item.id) == nil)
        #expect(QueuedFollowUpChange.edit(original: item.prompt, replacement: "Replacement")
            .applying(to: [], id: item.id) == nil)
        #expect(QueuedFollowUpChange.edit(original: item.prompt, replacement: " \n ")
            .applying(to: [item], id: item.id) == nil)
    }

    @Test
    func inlineReorderingDeletionAndRetryKeepMessageIdentity() throws {
        let first = QueuedFollowUp(prompt: "First")
        var second = QueuedFollowUp(prompt: "Second", attempt: 2)
        second.paused = true
        second.lastError = "Retry required"
        let items = [first, second]
        #expect(QueuedFollowUpChange.move(-1).applying(to: items, id: second.id)?.map(\.id) == [second.id, first.id])
        #expect(QueuedFollowUpChange.move(1).applying(to: items, id: first.id)?.map(\.id) == [second.id, first.id])
        #expect(QueuedFollowUpChange.move(-1).applying(to: items, id: first.id) == nil)
        #expect(QueuedFollowUpChange.move(1).applying(to: items, id: second.id) == nil)
        #expect(QueuedFollowUpChange.move(Int.max).applying(to: items, id: second.id) == nil)
        #expect(QueuedFollowUpChange.remove.applying(to: items, id: first.id)?.map(\.id) == [second.id])
        let retried = try #require(QueuedFollowUpChange.retry.applying(to: items, id: second.id)?.last)
        #expect(retried.id == second.id && retried.attempt == 3)
        #expect(!retried.isPaused && retried.lastError == nil)
    }

    @Test
    func aDispatchingMessageCannotBeEditedRemovedOrReordered() {
        var sending = QueuedFollowUp(prompt: "Already sending")
        sending.dispatch = QueuedFollowUpDispatch(mode: .continuation, workspaceID: "qa", runID: "run",
                                                agent: 1, settings: RunSettings())
        let waiting = QueuedFollowUp(prompt: "Still queued")
        let items = [sending, waiting]
        #expect(QueuedFollowUpChange.remove.applying(to: items, id: sending.id) == nil)
        #expect(QueuedFollowUpChange.edit(original: sending.prompt, replacement: "Changed")
            .applying(to: items, id: sending.id) == nil)
        #expect(QueuedFollowUpChange.move(-1).applying(to: items, id: waiting.id) == nil)
    }

    @Test
    func draggingUsesMessageAnchorsInsteadOfOldIndexesOrTheHiddenQueueEnd() throws {
        let items = (1...5).map { QueuedFollowUp(prompt: "Message \($0)") }
        let moved = try #require(QueuedFollowUpChange.place(relativeTo: items[2].id, after: true)
            .applying(to: items, id: items[1].id))
        #expect(moved.map(\.id) == [items[0].id, items[2].id, items[1].id, items[3].id, items[4].id])
        let newlyQueued = QueuedFollowUp(prompt: "New message")
        let advanced = [items[1], items[2], items[3], items[4], newlyQueued]
        #expect(QueuedFollowUpChange.place(relativeTo: items[2].id, after: true)
            .applying(to: advanced, id: items[1].id)?.map(\.id)
            == [items[2].id, items[1].id, items[3].id, items[4].id, newlyQueued.id])
        #expect(QueuedFollowUpChange.place(relativeTo: items[0].id, after: false)
            .applying(to: advanced, id: items[1].id) == nil)
        #expect(QueuedFollowUpChange.place(relativeTo: items[2].id, after: true)
            .applying(to: advanced, id: items[0].id) == nil)
    }

    @Test
    func aDragCannotCrossSendingLocksAndDoesNotChangeRetryState() throws {
        let first = QueuedFollowUp(prompt: "Sending")
        var paused = QueuedFollowUp(prompt: "Paused", attempt: 3)
        paused.paused = true
        paused.failureCount = 2
        paused.lastError = "Keep this error"
        paused.retryAt = Date(timeIntervalSince1970: 100)
        let last = QueuedFollowUp(prompt: "Last", settings: RunSettings())
        let items = [first, paused, last]
        let moved = try #require(QueuedFollowUpChange.place(relativeTo: paused.id, after: false)
            .applying(to: items, id: last.id, lockedIDs: [first.id]))
        #expect(moved == [first, last, paused])
        #expect(QueuedFollowUpChange.place(relativeTo: first.id, after: false)
            .applying(to: items, id: last.id, lockedIDs: [first.id]) == nil)
        #expect(QueuedFollowUpChange.place(relativeTo: last.id, after: true)
            .applying(to: items, id: first.id, lockedIDs: [first.id]) == nil)
        #expect(QueuedFollowUpChange.place(relativeTo: last.id, after: true)
            .applying(to: items, id: first.id, lockedIDs: [paused.id]) == nil)
        #expect(QueuedFollowUpChange.place(relativeTo: first.id, after: true)
            .applying(to: [first, last], id: last.id) == [first, last])
    }

    @Test
    func nativeDragPayloadIsPrivateScopedAndDropCommitsExactlyOnce() async throws {
        let source = QueuedFollowUp(prompt: "Source")
        let target = QueuedFollowUp(prompt: "Target")
        var calls: [(UUID, QueuedFollowUpChange)] = []
        let drop = QueuedMessageDrop(context: "queue-context", target: target.id, height: 42,
            locked: false, insertAfter: .constant(nil), commit: { calls.append(($0, $1)); return true })
        #expect(!drop.receive(NSItemProvider(object: "external text" as NSString), after: false))
        #expect(calls.isEmpty)
        let wrong = QueuedMessageDrag(context: "different-conversation", id: source.id).provider
        #expect(drop.receive(wrong, after: false))
        try await Task.sleep(for: .milliseconds(50))
        #expect(calls.isEmpty)
        #expect(drop.receive(QueuedMessageDrag(context: "queue-context", id: source.id).provider, after: true))
        for _ in 0..<100 where calls.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        #expect(calls.count == 1)
        #expect(calls.first?.0 == source.id)
        if case let .place(anchor, after) = calls.first?.1 {
            #expect(anchor == target.id && after)
        } else { Issue.record("The native drop must submit a relative placement") }
        let locked = QueuedMessageDrop(context: "queue-context", target: target.id, height: 42,
            locked: true, insertAfter: .constant(nil), commit: { _, _ in Issue.record("Sending rows must reject drops"); return false })
        #expect(!locked.receive(QueuedMessageDrag(context: "queue-context", id: source.id).provider, after: false))
    }

    @Test
    func queueRemainsBoundedAndScrollableAtEveryTextSizeAndAppearance() async throws {
        _ = NSApplication.shared
        let items = (1...12).map {
            var item = QueuedFollowUp(prompt: "\($0). 队列显示测试 A long queued message with several lines\nSecond line must not grow the row.")
            item.daemonAcknowledged = true
            return item
        }
        for width: CGFloat in [350, 1_000] {
            for size in AppTextSize.allCases {
                for scheme in [ColorScheme.dark, .light] {
                    let content = ComposerQueueView(items: items, status: "30 秒后从综合 2 开始", dispatching: false,
                        change: { _, _ in Issue.record("Rendering must not mutate the queue"); return false }, edit: { _ in }, manageQueue: {})
                        .environment(\.appTextSize, size)
                        .environment(\.appLanguage, .chinese)
                        .environment(\.colorScheme, scheme)
                        .frame(width: width)
                    let host = NSHostingView(rootView: content)
                    let fitting = host.fittingSize
                    #expect(fitting.width <= width + 1)
                    #expect(fitting.height < 280)
                    let window = NSWindow(contentRect: NSRect(origin: .zero, size: fitting),
                                          styleMask: [.titled], backing: .buffered, defer: false)
                    window.isReleasedWhenClosed = false
                    window.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
                    window.contentView = host
                    host.layoutSubtreeIfNeeded()
                    try await Task.sleep(for: .milliseconds(40))
                    host.layoutSubtreeIfNeeded()
                    let scroll = try #require(descendants(host, as: NSScrollView.self).first)
                    #expect(scroll.contentView.bounds.height <= ComposerQueueView.rowHeight * size.scale * 3 + 1)
                    #expect(try #require(scroll.documentView).frame.height > scroll.contentView.bounds.height)
                    let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                    host.cacheDisplay(in: host.bounds, to: image)
                    #expect(image.pixelsWide > 0 && image.pixelsHigh > 0)
                    if let output = ProcessInfo.processInfo.environment["PCR_QUEUE_QA_OUTPUT"],
                       size == .standard, scheme == .dark {
                        try image.representation(using: .png, properties: [:])?.write(to:
                            URL(fileURLWithPath: output).appendingPathComponent("queue-compact-\(Int(width)).png"))
                    }
                    window.close()
                }
            }
        }
    }

    @Test
    func fullManagerIsALiveReadOnlyListWithBoundedHeightNotAnArrayOfTextEditors() async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("queue-render-\(UUID().uuidString)")
        let model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
            tokenURL: directory.appendingPathComponent("absent.token")), windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: directory))
        defer {
            model.stopWindowObservation()
            model.promptDraftSaveTask?.cancel()
            try? FileManager.default.removeItem(at: directory)
        }
        model.queuePresented = true
        model.followUpQueue = (1...12).map {
            var item = QueuedFollowUp(prompt: "\($0). 队列界面验证：保持消息内容和身份，按顺序衔接下一轮。\nA longer message stays bounded.")
            item.daemonAcknowledged = true
            return item
        }
        model.followUpQueue[1].paused = true
        model.followUpQueue[1].lastError = "The queue could not be updated."
        model.followUpQueue[2].daemonAcknowledged = false
        for size in AppTextSize.allCases {
            let content = FollowUpQueueView().environmentObject(model)
                .environment(\.appLanguage, .chinese).environment(\.appTextSize, size)
                .environment(\.colorScheme, .dark).frame(width: 620)
            let host = NSHostingView(rootView: content)
            let fitting = host.fittingSize
            #expect(fitting.width <= 621 && fitting.height <= 600)
            let window = NSWindow(contentRect: NSRect(origin: .zero, size: fitting),
                styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.appearance = NSAppearance(named: .darkAqua)
            window.contentView = host
            try await Task.sleep(for: .milliseconds(40))
            host.layoutSubtreeIfNeeded()
            #expect(descendants(host, as: NSTextView.self).isEmpty)
            let scroll = try #require(descendants(host, as: NSScrollView.self).first)
            #expect(try #require(scroll.documentView).frame.height > scroll.contentView.bounds.height)
            let originalHeight = host.fittingSize.height
            model.followUpQueue.removeLast()
            try await Task.sleep(for: .milliseconds(40))
            host.layoutSubtreeIfNeeded()
            #expect(abs(host.fittingSize.height - originalHeight) <= 1)
            if let output = ProcessInfo.processInfo.environment["PCR_QUEUE_QA_OUTPUT"], size == .standard {
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                try image.representation(using: .png, properties: [:])?.write(to:
                    URL(fileURLWithPath: output).appendingPathComponent("queue-manager.png"))
            }
            window.close()
        }
    }

    private func descendants<T: NSView>(_ view: NSView, as type: T.Type) -> [T] {
        (view as? T).map { [$0] } ?? view.subviews.flatMap { descendants($0, as: type) }
    }
}
