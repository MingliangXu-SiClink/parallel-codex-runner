import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct TranscriptRenderingLifecycleTests {
    @Test
    func representableUpdateDoesNotRetireTheFocusedDocumentInsideTheGraphCallback() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "old", kind: .output, text: "Old workspace."))]
        fixture.update()
        await fixture.settle()
        let original = fixture.collection
        #expect(fixture.window.makeFirstResponder(original))
        fixture.context = "next-workspace"
        fixture.items = [.message(.init(id: "new", kind: .output, text: "New workspace."))]
        fixture.coordinator.scheduleRepresentableUpdate(parent: fixture.parent())
        #expect(fixture.collection === original,
            "The SwiftUI callback must not detach a focused document and reenter its focus graph")
        #expect(!original.isHidden)
        #expect(fixture.coordinator.parent.contextID == "initial")
        await fixture.settle()
        #expect(fixture.collection !== original)
        #expect(fixture.coordinator.parent.contextID == "next-workspace")
        try fixture.expectCurrentTextOnly()
    }

    @Test
    func representableUpdatesCommitOnlyTheLatestSnapshotAndDetachRevokesThem() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.update()
        let original = fixture.collection
        for revision in 1...20 {
            fixture.context = "workspace-\(revision)"
            fixture.items = [.message(.init(id: "answer", kind: .output, text: "Snapshot \(revision)."))]
            fixture.coordinator.scheduleRepresentableUpdate(parent: fixture.parent())
        }
        #expect(fixture.collection === original)
        #expect(fixture.coordinator.parent.contextID == "initial")
        await fixture.settle()
        #expect(fixture.coordinator.parent.contextID == "workspace-20")
        #expect(fixture.coordinator.parent.items == fixture.items)
        let committed = fixture.collection
        fixture.context = "detached-workspace"
        fixture.coordinator.scheduleRepresentableUpdate(parent: fixture.parent())
        fixture.coordinator.detach()
        await fixture.settle()
        #expect(fixture.collection === committed)
        #expect(fixture.coordinator.parent.contextID == "workspace-20")
    }

    @Test
    func newerDirectUpdateRevokesAQueuedRepresentableSnapshot() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.update()
        fixture.context = "queued-old"
        fixture.coordinator.scheduleRepresentableUpdate(parent: fixture.parent())
        fixture.context = "newer-direct"
        fixture.update()
        await fixture.settle()
        #expect(fixture.coordinator.parent.contextID == "newer-direct")
    }

    @Test
    func queuedRepresentableSnapshotSupersedesAnOlderNativeCompletionDrain() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Initial."))]
        fixture.update()
        await fixture.settle()
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "In flight.")))
        fixture.update()
        await fixture.settle()
        #expect(document.completions.count == 1)
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Old deferred."))
        fixture.update()
        document.finish()
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Newest representable."))
        fixture.coordinator.scheduleRepresentableUpdate(parent: fixture.parent())
        await fixture.settle()
        document.finish()
        await fixture.settle()
        #expect(fixture.coordinator.parent.items == fixture.items)
        try fixture.expectCurrentTextOnly()
    }

    @Test
    func realHostingGraphCanReplaceFocusedTranscriptsAndRestyleWithoutReentry() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.coordinator.detach()
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Hosted initial."))]
        func root(appearance: TranscriptAppearance) -> AnyView {
            let p = fixture.parent()
            return AnyView(VStack {
                ConversationTimelineView(language: p.language, textSize: p.textSize,
                    accentColor: p.accentColor, appearance: appearance, contextID: p.contextID,
                    items: p.items, contentIsLoading: false, jumpToLatestRequest: 0,
                    findQuery: "", findMatch: nil, findNavigationRequest: 0, memory: p.memory,
                    resultsInspectorVisible: false, openURL: p.openURL, activateBlock: p.activateBlock,
                    selectAgent: p.selectAgent, toggleResults: p.toggleResults,
                    isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
                TextField("Unsent draft", text: .constant("Keep this draft."))
            })
        }
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        let host = NSHostingView(rootView: root(appearance: .dark))
        fixture.window.contentView = host
        func ready() async throws -> NSCollectionView {
            for _ in 0..<100 {
                host.layoutSubtreeIfNeeded()
                if let scroll = descendants(host).compactMap({ $0 as? ConversationTimelineScrollView }).first,
                   let document = scroll.documentView as? NSCollectionView {
                    document.layoutSubtreeIfNeeded()
                    return document
                }
                try await Task.sleep(for: .milliseconds(10))
            }
            throw NSError(domain: "HostedTranscriptMissing", code: 1)
        }
        var document = try await ready()
        for revision in 1...40 {
            let externalEditor = revision.isMultiple(of: 3)
                ? descendants(host).compactMap({ $0 as? NSTextField }).first : nil
            #expect(fixture.window.makeFirstResponder(externalEditor ?? document))
            let responder = fixture.window.firstResponder
            // Alternate workspace replacement with same-context restyling.
            fixture.context = "hosted-workspace-\(revision / 2)"
            fixture.items = [.message(.init(id: "answer", kind: .output, text: "Hosted revision \(revision)."))]
            host.rootView = root(appearance: revision.isMultiple(of: 2) ? .dark : .light)
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(25))
            let next = try await ready()
            #expect(next !== document)
            let coordinator = try #require(next.dataSource as? ConversationTimelineView.Coordinator)
            #expect(coordinator.parent.contextID == fixture.context)
            #expect(coordinator.parent.items == fixture.items)
            #expect(!next.isHidden)
            #expect(document.window == nil)
            if externalEditor != nil {
                #expect(fixture.window.firstResponder === responder,
                    "Replacing the transcript must not steal focus from the composer")
            }
            document = next
        }
        let coordinator = try #require(document.dataSource as? ConversationTimelineView.Coordinator)
        let fields = descendants(host).compactMap { $0 as? NSTextField }
        #expect(fields.contains { $0.stringValue == "Keep this draft." })
        let commits = coordinator.geometryCommitCount
        for _ in 0..<5 {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(25))
        }
        #expect(coordinator.geometryCommitCount == commits,
            "A stable input must not turn the synchronous cycle into an async layout loop")
        coordinator.detach()
        fixture.window.contentView = nil
    }

    @Test
    func pendingNativeCompletionKeepsItsSnapshotAndCoalescesNewerUpdates() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Initial answer."))]
        fixture.update()
        await fixture.settle()
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "Submitted snapshot.")))
        fixture.update()
        await fixture.settle()
        #expect(document.completions.count == 1)
        for revision in 1...30 {
            fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Newer snapshot \(revision)."))
            fixture.update()
        }
        #expect(fixture.coordinator.parent.items[1] == .message(.init(id: "next", kind: .output,
            text: "Submitted snapshot.")), "A pending native batch must not read a later snapshot")
        document.finish()
        await fixture.settle()
        #expect(fixture.coordinator.parent.items == fixture.items)
        try fixture.expectCurrentTextOnly()
        try fixture.expectReplyMatchesFreshItem()
    }

    @Test
    func aNewerSnapshotCannotBeOverwrittenByAQueuedCompletionDrain() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Initial answer."))]
        fixture.update()
        await fixture.settle()
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "Submitted snapshot.")))
        fixture.update()
        await fixture.settle()
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Superseded deferred snapshot."))
        fixture.update()
        document.finish()
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Latest direct snapshot."))
        fixture.update()
        await fixture.settle()
        #expect(fixture.coordinator.parent.items == fixture.items)
        try fixture.expectCurrentTextOnly()
        try fixture.expectReplyMatchesFreshItem()
    }

    @Test
    func aPendingReversionSupersedesAnIntermediateSnapshot() async throws {
        for restoreInitial in [false, true] {
            let document = DelayedCompletionCollectionView()
            let fixture = Fixture(document: document)
            defer { fixture.close() }
            let initial: [ConversationTimelineItem] = [
                .message(.init(id: "answer", kind: .output, text: "Initial answer.")),
            ]
            fixture.items = initial
            fixture.update()
            await fixture.settle()
            fixture.items.append(.message(.init(id: "next", kind: .output, text: "Submitted snapshot.")))
            let submitted = fixture.items
            fixture.update()
            await fixture.settle()
            fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Superseded intermediate snapshot."))
            fixture.update()
            fixture.items = restoreInitial ? initial : submitted
            fixture.update()
            document.finish()
            await fixture.settle()
            document.finish()
            await fixture.settle()
            #expect(fixture.coordinator.parent.items == fixture.items)
            #expect(document.completions.isEmpty)
            try fixture.expectCurrentTextOnly()
            try fixture.expectReplyMatchesFreshItem()
        }
    }

    @Test
    func interruptedNativeAnimationStillFinishesItsSnapshot() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Initial answer."))]
        fixture.update()
        await fixture.settle()
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "Submitted snapshot.")))
        fixture.update()
        await fixture.settle()
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Latest snapshot."))
        fixture.update()
        document.finish(result: false)
        await fixture.settle()
        #expect(fixture.coordinator.parent.items == fixture.items)
        try fixture.expectCurrentTextOnly()
        try fixture.expectReplyMatchesFreshItem()
    }

    @Test
    func userScrollingRevokesRestorationWaitingForNativeCompletion() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: Self.answer(3, count: 40)))]
        fixture.update()
        await fixture.settle()
        fixture.scroll.contentView.scroll(to: NSPoint(x: 0, y: 400))
        fixture.scroll.reflectScrolledClipView(fixture.scroll.contentView)
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "Appended answer.")))
        fixture.update()
        await fixture.settle()
        #expect(document.completions.count == 1)
        // Invoke the view's navigation hook directly, without desktop events.
        fixture.scroll.onUserScroll?()
        fixture.scroll.contentView.scroll(to: NSPoint(x: 0, y: 640))
        fixture.scroll.reflectScrolledClipView(fixture.scroll.contentView)
        document.finish()
        await fixture.settle()
        #expect(abs(fixture.scroll.contentView.bounds.minY - 640) < 1)
        try fixture.expectCurrentTextOnly()
    }

    @Test
    func detachedHostRejectsItsPendingNativeCompletion() async throws {
        let document = DelayedCompletionCollectionView()
        let fixture = Fixture(document: document)
        defer { fixture.close() }
        fixture.items = [.message(.init(id: "answer", kind: .output, text: "Initial answer."))]
        fixture.update()
        await fixture.settle()
        fixture.items.append(.message(.init(id: "next", kind: .output, text: "Submitted snapshot.")))
        fixture.update()
        await fixture.settle()
        let committed = fixture.coordinator.parent.items
        fixture.items[1] = .message(.init(id: "next", kind: .output, text: "Never mount after detach."))
        fixture.update()
        fixture.coordinator.detach()
        document.finish()
        await fixture.settle()
        #expect(fixture.coordinator.parent.items == committed)
    }

    @Test
    func drawingANewReplyErasesThePreviousGlyphsWithoutParentRepainting() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let item = TranscriptCollectionItem()
        fixture.window.contentView?.addSubview(item.view)
        func configure(_ marker: String) throws -> NSTextView {
            let block = TranscriptBlock(id: "answer", kind: .output,
                text: String(repeating: marker + "正常的中文段落，编号与比较对象均应清楚。\n\n", count: 12))
            let width: CGFloat = 920
            item.configure(block: block, language: .chinese, textSize: .standard,
                accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                availableWidth: width, openURL: { _ in }, activateBlock: { _ in })
            item.view.frame = NSRect(x: 0, y: 0, width: width, height:
                TranscriptCollectionItem.preferredHeight(for: block, textSize: .standard,
                    accentColor: .orange, appearance: .dark, availableWidth: width))
            item.view.layoutSubtreeIfNeeded()
            return try #require(item.view.subviews.flatMap { $0.subviews }
                .flatMap { $0.subviews }.compactMap { $0 as? NSTextView }.first)
        }
        let text = try configure("甲：")
        let retained = try #require(text.bitmapImageRepForCachingDisplay(in: text.bounds))
        text.cacheDisplay(in: text.bounds, to: retained)
        _ = try configure("乙：")
        let fresh = try #require(text.bitmapImageRepForCachingDisplay(in: text.bounds))
        // This is a drawing-ownership contract, not a claimed reproduction of
        // the window server. An opaque text surface must replace old pixels.
        text.cacheDisplay(in: text.bounds, to: retained)
        text.cacheDisplay(in: text.bounds, to: fresh)
        #expect(text.drawsBackground && text.backgroundColor.alphaComponent == 1)
        #expect(retained.representation(using: .png, properties: [:])
            == fresh.representation(using: .png, properties: [:]))
    }

    @Test
    func retiredAndReusedItemBecomesVisibleWithTheSameText() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let item = TranscriptCollectionItem()
        fixture.window.contentView?.addSubview(item.view)
        let block = TranscriptBlock(id: "answer", kind: .output, text: "The same cached answer.")
        func configure() {
            item.configure(block: block, language: .english, textSize: .standard,
                accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                availableWidth: 920, openURL: { _ in }, activateBlock: { _ in })
        }
        configure()
        item.view.isHidden = true
        configure()
        #expect(!item.view.isHidden)
        item.prepareForReuse()
        #expect(item.view.isHidden)
        configure()
        #expect(!item.view.isHidden)
        let text = try #require(item.view.subviews.flatMap { $0.subviews }
            .flatMap { $0.subviews }.compactMap { $0 as? NSTextView }.first)
        #expect(text.string == block.text)
    }

    @Test
    func opaqueTextSurfaceTracksAppearanceAndSemanticTint() throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let item = TranscriptCollectionItem()
        fixture.window.contentView?.addSubview(item.view)
        for appearance: TranscriptAppearance in [.dark, .light] {
            for kind: TranscriptKind in [.output, .user, .warning, .error] {
                let block = TranscriptBlock(id: "same", kind: kind, text: "Unchanged content.")
                item.configure(block: block, language: .english, textSize: .standard,
                    accentColor: .orange, appearance: appearance, findQuery: "", currentFindOccurrence: nil,
                    availableWidth: 920, openURL: { _ in }, activateBlock: { _ in })
                let text = try #require(item.view.subviews.flatMap { $0.subviews }
                    .flatMap { $0.subviews }.compactMap { $0 as? NSTextView }.first)
                let canvas = ParallelCodexVisualStyle.resolvedNSColor(
                    ParallelCodexVisualStyle.canvasNSColor, for: appearance)
                let background = try #require(text.superview?.superview?.layer?.backgroundColor)
                let surface = try #require(NSColor(cgColor: background))
                let expected = try #require(canvas.blended(withFraction: surface.alphaComponent,
                    of: surface.withAlphaComponent(1))?.usingColorSpace(.deviceRGB))
                let actual = try #require(text.backgroundColor.usingColorSpace(.deviceRGB))
                #expect(text.drawsBackground && text.backgroundColor.alphaComponent == 1)
                #expect(abs(actual.redComponent - expected.redComponent) < 0.0001)
                #expect(abs(actual.greenComponent - expected.greenComponent) < 0.0001)
                #expect(abs(actual.blueComponent - expected.blueComponent) < 0.0001)
                #expect(text.string == block.text)
            }
        }
    }

    @Test
    func rapidCandidateChangesLeaveOnlyCurrentNativeReplyViews() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        let agents = try (1...3).map { agent in
            try JSONDecoder().decode(RunAgentSummary.self, from: Data("""
            {"agent":\(agent),"name":"Agent \(agent)","status":"success","reasoningTokenCounts":{},
             "rejected":false,"recommended":false,"finalized":false}
            """.utf8))
        }
        for cycle in 0..<24 {
            let agent = cycle % 3 + 1
            fixture.context = transcriptScrollContextID(machineID: "fixture", workspaceID: "fixture",
                conversationID: "fixture", runID: "fixture", agentID: String(agent))
            fixture.items = [
                .message(.init(id: "history", kind: .output, text: "Earlier accepted answer.", isHistorical: true)),
                .message(.init(id: "question", kind: .user, text: "Compare the answers.")),
                .agentSwitch(.init(runID: "fixture", agents: agents, selectedAgent: agent,
                    recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)),
                .message(.init(id: "answer-\(agent)", kind: .output,
                    text: Self.answer(agent, count: 8 + agent), isFinalAnswer: true)),
            ]
            fixture.update()
            // Deliberately submit the next branch without waiting for a batch
            // completion, as a cached Agent switch and a refresh can do.
            fixture.collection.layoutSubtreeIfNeeded()
            try fixture.expectCurrentTextOnly()
        }
        await fixture.settle()
        try fixture.expectCurrentTextOnly()
        try fixture.expectReplyMatchesFreshItem()
    }

    @Test
    func streamingRestylingAndWidthChangesKeepOneConsistentGlyphLayout() async throws {
        let fixture = Fixture()
        defer { fixture.close() }
        fixture.context = "streaming"
        for cycle in 0..<18 {
            let width: CGFloat = [720, 1280, 960][cycle % 3]
            fixture.scroll.frame.size.width = width
            fixture.items = [.message(.init(id: "answer", kind: .output,
                text: Self.answer(cycle % 3 + 1, count: 7 + cycle % 5), isFinalAnswer: cycle % 2 == 0))]
            fixture.update()
            await fixture.settle()
            try fixture.expectCurrentTextOnly()
            try fixture.expectReplyMatchesFreshItem()
        }
    }

    static func answer(_ agent: Int, count: Int) -> String {
        "Agent \(agent): 当前回答。\n\n" + (1...count).map {
            "\($0). **项目 \($0)** 保留原始内容与明确的比较对象。This answer belongs to Agent \(agent). "
                + String(repeating: "文字、编号和段落应完整显示，刷新不能叠加之前的回答。 ", count: agent + 1)
        }.joined(separator: "\n") + "\n\n最后一段必须只显示一次。"
    }

    @MainActor
    private final class Fixture {
        let suite = "TranscriptRenderingLifecycleTests.\(UUID().uuidString)"
        let defaults: UserDefaults
        let memory: TranscriptScrollMemory
        let window: NSWindow
        let scroll = ConversationTimelineScrollView(frame: NSRect(x: 0, y: 0, width: 1280, height: 1500))
        var collection: NSCollectionView { scroll.documentView as! NSCollectionView }
        var coordinator: ConversationTimelineView.Coordinator!
        var context = "initial"
        var items: [ConversationTimelineItem] = []

        init(document: NSCollectionView? = nil) {
            _ = NSApplication.shared
            defaults = UserDefaults(suiteName: suite)!
            memory = TranscriptScrollMemory(defaults: defaults)
            // An unshown window supplies AppKit's backing scale without ever
            // ordering a window, activating an app, or sending desktop input.
            window = NSWindow(contentRect: scroll.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = NSView(frame: scroll.frame)
            window.contentView?.addSubview(scroll)
            coordinator = ConversationTimelineView.Coordinator(parent: parent())
            let collection = document ?? ConversationTimelineView.makeCollectionView(coordinator: coordinator)
            if document != nil {
                let layout = TranscriptFlowLayout()
                layout.minimumLineSpacing = 22
                layout.sectionInset = NSEdgeInsets(top: 24, left: 24, bottom: 34, right: 24)
                collection.collectionViewLayout = layout
                collection.dataSource = coordinator
                collection.delegate = coordinator
                collection.register(TranscriptCollectionItem.self, forItemWithIdentifier: TranscriptCollectionItem.identifier)
            }
            collection.frame = scroll.bounds
            scroll.documentView = collection
            coordinator.attach(scrollView: scroll, collectionView: collection)
        }

        func parent() -> ConversationTimelineView {
            ConversationTimelineView(language: .chinese, textSize: .standard,
                accentColor: .orange, appearance: .dark, contextID: context, items: items,
                contentIsLoading: false, jumpToLatestRequest: 0, findQuery: "", findMatch: nil,
                findNavigationRequest: 0, memory: memory, resultsInspectorVisible: false,
                openURL: { _, _ in }, activateBlock: { _ in }, selectAgent: { _ in }, toggleResults: {},
                isNearBottom: .constant(false), inlineAgentSwitchPosition: .constant(.missing))
        }

        func update() {
            memory.save(.init(offsetY: 0, followsLatest: false), for: context)
            coordinator.update(parent: parent())
        }

        func settle() async {
            try? await Task.sleep(for: .milliseconds(35))
            collection.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(35))
        }

        func expectCurrentTextOnly() throws {
            let expected = coordinator.parent.items.compactMap { item -> String? in
                guard case .message(let block) = item else { return nil }
                return TranscriptTextRenderer.attributedText(for: block, textSize: .standard,
                    accentColor: .orange, appearance: .dark).string
            }
            let actual = visibleTextViews(collection).map(\.string)
            for text in actual {
                #expect(expected.contains(text), "An obsolete native text view is still mounted: \(text.prefix(90))")
                #expect(actual.filter { $0 == text }.count == 1, "A reply has multiple native drawing views")
            }
            for path in collection.indexPathsForVisibleItems() {
                let item = try #require(collection.item(at: path))
                let layout = try #require(collection.collectionViewLayout?.layoutAttributesForItem(at: path))
                #expect(abs(item.view.frame.minY - layout.frame.minY) < 1)
                #expect(abs(item.view.frame.height - layout.frame.height) < 1)
            }
        }

        func expectReplyMatchesFreshItem() throws {
            for path in collection.indexPathsForVisibleItems() {
                guard case .message(let block) = coordinator.parent.items[path.item], block.kind == .output,
                      let actual = collection.item(at: path) as? TranscriptCollectionItem else { continue }
                let fresh = TranscriptCollectionItem()
                window.contentView?.addSubview(fresh.view)
                defer { fresh.view.removeFromSuperview() }
                fresh.configure(block: block, language: .chinese, textSize: .standard,
                    accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                    availableWidth: actual.view.bounds.width, openURL: { _ in }, activateBlock: { _ in })
                fresh.view.frame = actual.view.bounds
                fresh.view.layoutSubtreeIfNeeded()
                actual.view.layoutSubtreeIfNeeded()
                let text = try #require(visibleTextViews(actual.view).first)
                let layout = try #require(text.layoutManager)
                let container = try #require(text.textContainer)
                layout.ensureLayout(for: container)
                let used = layout.usedRect(for: container)
                #expect(used.maxY <= text.bounds.height + 1)
                #expect(used.maxX <= text.bounds.width + 1)
                var previousBottom: CGFloat = 0
                layout.enumerateLineFragments(forGlyphRange: NSRange(location: 0, length: layout.numberOfGlyphs)) {
                    rect, _, _, _, _ in
                    #expect(rect.minY >= previousBottom - 0.5, "Consecutive text lines overlap")
                    previousBottom = rect.maxY
                }
                let painted = text.convert(used.offsetBy(dx: text.textContainerOrigin.x,
                    dy: text.textContainerOrigin.y), to: collection)
                #expect(actual.view.frame.insetBy(dx: -1, dy: -1).contains(painted),
                    "A reply's glyphs extend beyond the collection row assigned to it")
                let a = try bitmap(actual.view)
                let b = try bitmap(fresh.view)
                #expect(a == b, "The current native reply differs from a freshly rendered identical reply")
            }
        }

        private func bitmap(_ view: NSView) throws -> Data {
            let bitmap = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
            view.cacheDisplay(in: view.bounds, to: bitmap)
            return try #require(bitmap.representation(using: .png, properties: [:]))
        }

        private func visibleTextViews(_ view: NSView) -> [NSTextView] {
            guard !view.isHidden, view.alphaValue > 0 else { return [] }
            if let text = view as? NSTextView { return [text] }
            return view.subviews.flatMap(visibleTextViews)
        }

        func close() {
            coordinator.detach()
            memory.freezeForTermination()
            window.close()
            defaults.removePersistentDomain(forName: suite)
        }
    }

    private final class DelayedCompletionCollectionView: NSCollectionView {
        var completions: [(Bool?) -> Void] = []

        override func performBatchUpdates(_ updates: (() -> Void)?, completionHandler: ((Bool) -> Void)? = nil) {
            super.performBatchUpdates(updates) { [weak self] finished in
                if let completionHandler {
                    self?.completions.append { result in completionHandler(result ?? finished) }
                }
            }
        }

        func finish(result: Bool? = nil) {
            let pending = completions
            completions.removeAll()
            for completion in pending { completion(result) }
        }
    }
}
