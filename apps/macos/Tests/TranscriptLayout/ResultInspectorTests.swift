import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ResultInspectorTests {
    @Test(arguments: [false, true])
    func stoppedConversationInspectorAndComposerAgreeWithoutOpeningAWindow(_ hasSavedConversation: Bool) async throws {
        _ = NSApplication.shared
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("stopped-inspector-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("unused.sock"),
            tokenURL: directory.appendingPathComponent("unused.token")), windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: directory))
        defer {
            model.stopWindowObservation()
            try? FileManager.default.removeItem(at: directory)
        }
        let session = hasSavedConversation ? "\"saved-conversation-session\"" : "null"
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
            {"runId":"stopped-inspector","status":"discarded","conversationId":"saved-conversation",
             "workspace":"/tmp/stopped-inspector","prompt":"The cancelled request",
             "config":{"resumeSessionId":\(session),"syncBack":true},
             "createdAt":"2026-10-09","updatedAt":"2026-10-09","syncedBack":false,
             "workspacesDeleted":true,"codexHomesDeleted":true,"artifactsAvailable":false,
             "workerActive":false,"workerAvailable":false,"eventCount":0,
             "agents":[{"agent":1,"name":"Agent 1","status":"cancelled","rejected":false,
                        "recommended":false,"finalized":false,"reasoningTokenCounts":{},
                        "canContinue":false,"workspaceAvailable":false}]}
            """.utf8))
        model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"stopped-inspector","name":"Stopped conversation","path":"/tmp/stopped-inspector",
             "lastRunId":"stopped-inspector","lastResumeSessionId":"unrelated-session",
             "selected":true,"available":true,"readable":true,"writable":true}
            """.utf8))]
        model.selectedWorkspaceID = "stopped-inspector"
        model.selectedConversationID = "saved-conversation"
        model.workspaceConversations = [.init(id: "saved-conversation", title: "Conversation", runs: [run], resumeSession: nil)]
        model.activeRun = run
        model.selectedAgent = 1
        model.appLanguage = .chinese
        model.daemonOnline = true
        model.settings.resumeSessionID = "unrelated-session"
        model.prompt = "A new question, not the cancelled request"
        let plan = model.promptSubmissionPlan
        #expect(plan == .start(resuming: hasSavedConversation))
        let canSend = model.canSubmitPrompt
        #expect(canSend)
        let label = ResultAgentContext.text(run: run, viewedAgent: 1, submissionPlan: plan, language: .chinese)
        #expect(label == (hasSavedConversation ? "本轮已停止；下一条消息从已保存的对话继续" : "本轮已停止；下一条消息将开始新对话"))
        #expect(!run.agents[0].mayContinue)
        for scheme in [ColorScheme.light, .dark] {
            for size in [AppTextSize.standard, .extraLarge] {
                let host = NSHostingView(rootView: ConversationResultsInspector(run: run)
                    .environmentObject(model).environment(\.appLanguage, .chinese)
                    .environment(\.appTextSize, size).environment(\.colorScheme, scheme))
                let window = makeWindow()
                defer { window.close() }
                window.setContentSize(NSSize(width: 440, height: 600))
                window.contentView = host
                try await settle(host)
                #expect(!window.isVisible)
                #expect(descendants(host, of: NSProgressIndicator.self).isEmpty)
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                let colors = Set(stride(from: 0, to: image.pixelsHigh, by: 16).flatMap { y in
                    stride(from: 0, to: image.pixelsWide, by: 16).compactMap { x -> String? in
                        image.colorAt(x: x, y: y)?.usingColorSpace(.sRGB)?.description
                    }
                })
                #expect(colors.count > 4, "The real inspector must render nonblank content offscreen.")
                if let path = ProcessInfo.processInfo.environment["PCR_STOPPED_CONTINUATION_EVIDENCE"] {
                    let output = URL(fileURLWithPath: path, isDirectory: true)
                    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
                    let filename = "stopped-\(hasSavedConversation ? "saved" : "new")-\(scheme)-\(size).png"
                    try #require(image.representation(using: .png, properties: [:])).write(to: output.appendingPathComponent(filename))
                }
                #expect(!window.isVisible)
            }
        }
    }

    @Test
    func executionSymbolsDistinguishWorkPreparationWaitingAndCompletion() throws {
        let states = [
            "running": "circle.fill", "RUNNING": "circle.fill",
            "starting": "gearshape", "copying": "gearshape",
            "waiting_for_models": "clock", "queued": "clock",
            "stopping": "stop.circle", "success": "circle.fill",
            "failed": "xmark.circle.fill", "error": "xmark.circle.fill",
            "unavailable": "exclamationmark.circle.fill", "unknown": "circle",
        ]
        for (status, symbol) in states {
            #expect(ResultStatusGlyph.symbol(for: status) == symbol)
            #expect(NSImage(systemSymbolName: symbol, accessibilityDescription: nil) != nil)
        }
        for status in ["killed", "cancelled", "canceled", "interrupted", "stopped"] {
            #expect(ResultStatusGlyph.symbol(for: status) == "stop.circle.fill")
        }
        #expect(ResultStatusGlyph.color(for: "running") == .blue)
        #expect(ResultStatusGlyph.color(for: "success") == .green)
        #expect(ResultStatusGlyph.color(for: "stopping") == .orange)
        #expect(ResultStatusGlyph.color(for: "failed") == .red)
        var value = agent(seconds: nil)
        value.status = "waiting_for_models"
        #expect(value.compactStatusLabel(language: .chinese) == "等待模型")
        #expect(value.compactStatusLabel(language: .english) == "Waiting for model")
        value.status = "queued"
        #expect(value.compactStatusLabel(language: .chinese) == "排队中")
        #expect(value.compactStatusLabel(language: .english) == "Queued")
        value.status = "stopping"
        #expect(value.compactStatusLabel(language: .chinese) == "正在停止")
    }

    @Test
    func executionGlyphsHaveStableSizeAndNeverCreateLoadingIndicators() async throws {
        _ = NSApplication.shared
        let window = makeWindow()
        defer { window.close() }
        for scheme in [ColorScheme.light, .dark] {
            for size in AppTextSize.allCases {
                for status in ["running", "starting", "copying", "waiting_for_models", "queued",
                               "stopping", "success", "failed", "unavailable", "interrupted"] {
                    let host = NSHostingView(rootView: ResultStatusGlyph(status: status)
                        .environment(\.appTextSize, size).environment(\.colorScheme, scheme))
                    window.contentView = host
                    try await settle(host)
                    #expect(descendants(host, of: NSProgressIndicator.self).isEmpty)
                    #expect(abs(host.fittingSize.width - 12 * size.scale) < 1)
                    #expect(abs(host.fittingSize.height - 12 * size.scale) < 1)
                }
            }
        }
    }

    @Test
    func phaseLabelsKeepTheSelectedAgentVisibleInNarrowSelectors() async throws {
        _ = NSApplication.shared
        let agents = (1...8).map { index -> RunAgentSummary in
            var value = agent(seconds: nil, number: index)
            value.status = "running"
            return value
        }
        func content(selected: Int) -> some View {
            VStack {
                selector(selected: selected)
                selector(selected: selected)
            }
        }
        func selector(selected: Int) -> some View {
            ConversationAgentSwitchBar(language: .english, textSize: .standard, accentColor: .blue,
                state: ConversationAgentSwitchState(runID: "status-tabs", agents: agents,
                    selectedAgent: selected, recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true),
                resultsInspectorVisible: false, accessibilityHidden: false, selectAgent: { _ in }, toggleResults: {})
                .frame(width: 320, height: 52)
        }
        let host = NSHostingView(rootView: content(selected: 1))
        let window = makeWindow()
        defer { window.close() }
        window.contentView = host
        try await settle(host)
        let scrolls = descendants(host, of: NSScrollView.self)
        #expect(scrolls.count == 2, "Inline and sticky selectors can coexist for the same turn.")
        #expect(scrolls.allSatisfy { $0.documentView!.frame.width > $0.contentSize.width })
        host.rootView = content(selected: 8)
        try await Task.sleep(for: .milliseconds(300))
        try await settle(host)
        #expect(scrolls.allSatisfy { $0.contentView.bounds.minX > 0 },
                "Both selectors must reveal their own selected Agent, without sharing scroll target identities.")
    }

    @Test
    func actualAgentTabsMountEightNativeBlueDotsAndStopWhenConnectionIsUnconfirmed() async throws {
        _ = NSApplication.shared
        let agents = (1...8).map { index -> RunAgentSummary in
            var value = agent(seconds: nil, number: index, recommended: false)
            value.status = "running"
            return value
        }
        func content(confirmed: Bool) -> some View {
            ConversationAgentSwitchBar(language: .chinese, textSize: .standard, accentColor: .orange,
                state: ConversationAgentSwitchState(runID: "native-motion-qa", agents: agents,
                    selectedAgent: 2, recommendedAgent: nil, finalizedAgent: nil,
                    selectionEnabled: true, executionConfirmed: confirmed),
                resultsInspectorVisible: false, accessibilityHidden: false, selectAgent: { _ in }, toggleResults: {})
                .frame(width: 1200, height: 52)
        }
        let host = NSHostingView(rootView: content(confirmed: true))
        let window = makeWindow()
        defer { window.close() }
        window.setContentSize(NSSize(width: 1200, height: 100))
        window.contentView = host
        try await settle(host)
        #expect(!window.isVisible, "Native validation must never order a QA window onto the desktop.")
        let dots = descendants(host, of: NativeExecutionActivityView.self)
        #expect(dots.count == 8, "Every Agent tab must own the new native dot, not just the header.")
        #expect(descendants(host, of: NSProgressIndicator.self).isEmpty)
        for dot in dots {
            dot.updateVisibility(true)
            #expect(dot.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey) != nil)
            #expect(dot.indicatorLayer.frame.size == CGSize(width: 8, height: 8))
        }
        let frames = dots.map(\.frame)
        host.rootView = content(confirmed: false)
        try await settle(host)
        for dot in descendants(host, of: NativeExecutionActivityView.self) {
            dot.updateVisibility(true)
            #expect(dot.indicatorLayer.animationKeys()?.isEmpty != false)
        }
        #expect(descendants(host, of: NativeExecutionActivityView.self).map(\.frame) == frames)
    }

    @Test
    func sidebarDotsRetainTheirColorAndSizeWhenActivityStops() async throws {
        _ = NSApplication.shared
        func content(pulsing: Bool) -> some View {
            HStack {
                ParallelCodexStatusDot(color: .orange, size: 5, isPulsing: pulsing)
                ParallelCodexStatusDot(color: .green, size: 5)
            }
        }
        let host = NSHostingView(rootView: content(pulsing: true))
        let window = makeWindow()
        defer { window.close() }
        window.contentView = host
        try await settle(host)
        #expect(!window.isVisible)
        let dots = descendants(host, of: NativeExecutionActivityView.self)
        #expect(dots.count == 2)
        for dot in dots {
            dot.updateVisibility(true)
            #expect(dot.indicatorLayer.frame.size == CGSize(width: 5, height: 5))
        }
        #expect(dots.first?.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey) != nil)
        #expect(dots.last?.indicatorLayer.animationKeys()?.isEmpty != false)
        let colors = dots.map { $0.indicatorLayer.backgroundColor }
        #expect(colors[0] != colors[1], "Working workspace dots keep their existing accent color.")
        host.rootView = content(pulsing: false)
        try await settle(host)
        let reduced = descendants(host, of: NativeExecutionActivityView.self)
        for dot in reduced {
            dot.updateVisibility(true)
            #expect(dot.indicatorLayer.animationKeys()?.isEmpty != false)
            #expect(dot.indicatorLayer.opacity == 1)
            #expect(dot.indicatorLayer.frame.size == CGSize(width: 5, height: 5))
        }
        #expect(reduced.map { $0.indicatorLayer.backgroundColor } == colors)
    }

    @Test
    func metricsUseTheEntireReasoningHistoryAndKeepUnknownDistinctFromZero() {
        let metrics = ResultDetailMetrics(agent: agent(seconds: 181, tokens: 27, counts: ["1545": 1, "516": 2, "27": 1]))
        #expect(metrics.duration == "3m 1s")
        #expect(metrics.reasoningTokens == 2604)
        let unknown = ResultDetailMetrics(agent: agent(seconds: nil, tokens: nil))
        #expect(unknown.duration == nil)
        #expect(unknown.reasoningTokens == nil)
        let zero = ResultDetailMetrics(agent: agent(seconds: 0, tokens: 0))
        #expect(zero.duration == "0s")
        #expect(zero.reasoningTokens == 0)
        #expect(ResultDetailMetrics(agent: agent(seconds: -.infinity)).duration == nil)
        #expect(ResultDetailMetrics(agent: agent(seconds: -1e100)).duration == "0s")
    }

    @Test
    func elapsedUsesTheActualAgentStartAndKeepsIncreasingAcrossReopening() throws {
        var running = agent(seconds: nil)
        running.status = "running"
        running.startedAt = "2026-09-18T10:00:00Z"
        let now = try #require(parseInternetDate("2026-09-18T10:03:01Z"))
        let timing = ResultElapsedTime(agent: running)
        #expect(timing.isLive)
        #expect(ResultDetailMetrics(agent: running, now: now).duration == "3m 1s")
        #expect(ResultDetailMetrics(agent: running, now: now.addingTimeInterval(1)).duration == "3m 2s")
        #expect(ResultDetailMetrics(agent: running, now: now.addingTimeInterval(60)).duration == "4m 1s")
        #expect(ResultDetailMetrics(agent: running, now: now.addingTimeInterval(3600)).duration == "1h 3m 1s")
        running.startedAt = "2026-09-18T10:03:00Z"
        #expect(ResultDetailMetrics(agent: running, now: now).duration == "1s", "Retry starts a new attempt")
    }

    @Test
    func queuedFinishedInterruptedAndMissingStartDoNotRunAFalseClock() throws {
        let now = try #require(parseInternetDate("2026-09-18T10:10:00Z"))
        var value = agent(seconds: nil)
        value.startedAt = "2026-09-18T10:00:00Z"
        for status in ["queued", "copying", "starting", "resuming", "submitting"] {
            value.status = status
            #expect(ResultElapsedTime(agent: value).isWaiting)
            #expect(!ResultElapsedTime(agent: value).isLive)
            #expect(ResultDetailMetrics(agent: value, now: now).duration == nil)
        }
        for status in ["success", "failed", "interrupted", "cancelled"] {
            value.status = status
            #expect(!ResultElapsedTime(agent: value).isLive)
            #expect(ResultDetailMetrics(agent: value, now: now).duration == nil)
        }
        value.finishedAt = "2026-09-18T10:03:01Z"
        #expect(ResultDetailMetrics(agent: value, now: now).duration == "3m 1s")
        #expect(ResultDetailMetrics(agent: value, now: now.addingTimeInterval(500)).duration == "3m 1s")
        var completed = agent(seconds: 182)
        completed.startedAt = value.startedAt
        completed.finishedAt = value.finishedAt
        #expect(ResultDetailMetrics(agent: completed, now: now).duration == "3m 2s")
        value.status = "running"
        value.finishedAt = nil
        value.startedAt = nil
        #expect(!ResultElapsedTime(agent: value).isLive)
        #expect(ResultDetailMetrics(agent: value, now: now).duration == nil)
        value.startedAt = "invalid date"
        #expect(!ResultElapsedTime(agent: value).isLive)
        value.startedAt = "2026-09-18T10:11:00Z"
        #expect(ResultDetailMetrics(agent: value, now: now).duration == "0s")
    }

    @Test
    func acceptedTurnAndMeasuredExecutionExcludePreparation() throws {
        var value = agent(seconds: 900)
        value.status = "waiting_for_output"
        value.startedAt = "2026-09-18T10:00:00Z"
        let now = try #require(parseInternetDate("2026-09-18T10:00:12Z"))
        #expect(value.isRunning)
        #expect(ResultElapsedTime(agent: value).isLive)
        #expect(ResultDetailMetrics(agent: value, now: now).duration == "12s")
        value.status = "success"
        value.finishedAt = "2026-09-18T10:02:00Z"
        value.phaseTimings = ["resuming": 800, "waiting_for_output": 10, "working": 90, "cleanup": 5]
        #expect(ResultDetailMetrics(agent: value, now: now).duration == "1m 40s")
    }

    @Test
    func optionalTimingFieldsDecodeFromNewAndOldDaemonSummaries() throws {
        let encoded = try JSONEncoder().encode(agent(seconds: nil))
        var payload = try #require(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        payload["startedAt"] = "2026-09-18T10:00:00.125+00:00"
        payload["finishedAt"] = "2026-09-18T10:00:10.125+00:00"
        let decoded = try JSONDecoder().decode(RunAgentSummary.self,
            from: JSONSerialization.data(withJSONObject: payload))
        #expect(decoded.startedAt == payload["startedAt"] as? String)
        #expect(ResultElapsedTime(agent: decoded).seconds(at: .now) == 10)
        let legacy = try JSONDecoder().decode(RunAgentSummary.self, from: encoded)
        #expect(legacy.startedAt == nil && legacy.finishedAt == nil)
    }

    @Test
    func compactInspectorStaysBoundedInsteadOfReplacingTheEntireConversation() {
        for available in [CGSize(width: 700, height: 800), CGSize(width: 320, height: 380)] {
            for textSize in AppTextSize.allCases {
                let size = ResultInspectorLayout.overlaySize(available: available, textScale: textSize.scale)
                #expect(size.width <= available.width - 32)
                #expect(size.height <= available.height - 32)
                #expect(size.width > 0 && size.height > 0)
            }
        }
        #expect(ResultInspectorLayout.overlaySize(available: CGSize(width: 900, height: 800), textScale: 1).width == 440)
    }

    @Test
    func detailsRenderAcrossAppearanceLanguageAndFontSizeWithoutInvokingActions() async throws {
        _ = NSApplication.shared
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"result-qa","status":"completed","workspace":"/tmp/qa","prompt":"QA",
         "createdAt":"2026-09-08","updatedAt":"2026-09-08","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "artifactsAvailable":true,"workerActive":false,"workerAvailable":false,
         "eventCount":0,"agents":[]}
        """.utf8))
        let summary = AgentDiffSummary(changes: [
            AgentFileChange(status: "M", path: "Sources/ConversationResultsInspector.swift")
        ])
        var actions = 0
        for scheme in [ColorScheme.light, .dark] {
            for language in [AppLanguage.english, .chinese] {
                for textSize in AppTextSize.allCases {
                    let content = ConversationResultDetails(
                        liveMetrics: ActiveRunMetrics(),
                        run: run,
                        agent: agent(seconds: 181, tokens: 27, counts: ["1545": 1, "516": 2, "27": 1]),
                        diffSummary: summary, diffLoading: false,
                        showDiff: { actions += 1 }
                    )
                    .environment(\.appLanguage, language)
                    .environment(\.appTextSize, textSize)
                    .environment(\.colorScheme, scheme)
                    let image = try await renderNative(content)
                    #expect(image.pixelsWide >= 320)
                    #expect(image.pixelsHigh >= 520)
                }
            }
        }
        #expect(actions == 0, "Inspecting a result must not invoke a file or branch action.")
    }

    @Test
    func liveElapsedRendersInBothThemesWithoutGrowingTheInspector() async throws {
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"timing-qa","status":"running","workspace":"/tmp/qa","prompt":"QA",
         "createdAt":"2026-09-18","updatedAt":"2026-09-18","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "artifactsAvailable":true,"workerActive":true,"workerAvailable":true,
         "eventCount":0,"agents":[]}
        """.utf8))
        var running = agent(seconds: nil, tokens: 1545)
        running.status = "running"
        running.startedAt = ISO8601DateFormatter().string(from: Date.now.addingTimeInterval(-3621))
        for scheme in [ColorScheme.dark, .light] {
            let content = ConversationResultDetails(liveMetrics: ActiveRunMetrics(), run: run, agent: running,
                diffSummary: nil, diffLoading: false, showDiff: {})
                .background(scheme == .dark ? Color(white: 0.075) : Color.white)
                .environment(\.appLanguage, .chinese)
                .environment(\.colorScheme, scheme)
            let image = try await renderNative(content)
            if let directory = ProcessInfo.processInfo.environment["PCR_QA_ARTIFACT_DIR"] {
                let data = try #require(image.representation(using: .png, properties: [:]))
                try data.write(to: URL(fileURLWithPath: directory)
                    .appendingPathComponent("result-timing-\(scheme).png"))
            }
        }
    }

    @Test
    func scrollFadesOnlyEdgesWithContentOutsideTheViewport() {
        func edges(y: CGFloat, height: CGFloat = 500, viewport: CGFloat = 100) -> InspectorScrollEdges {
            InspectorScrollEdges(content: CGRect(x: 0, y: y, width: 320, height: height),
                                 viewportHeight: viewport)
        }
        #expect(!edges(y: 0).top && edges(y: 0).bottom)
        #expect(edges(y: -100).top && edges(y: -100).bottom)
        #expect(edges(y: -400).top && !edges(y: -400).bottom)
        #expect(!edges(y: 0, height: 80).top && !edges(y: 0, height: 80).bottom)
        #expect(!edges(y: 0, viewport: 600).bottom, "Resizing until all content fits removes the fade.")
        #expect(!edges(y: 0, viewport: 0).bottom)
        #expect(!edges(y: -0.2).top, "Subpixel layout changes must not flicker the fade.")
        #expect(!edges(y: 0, height: 100.2).bottom)
    }

    @Test
    func nativeScrollAndResizeUpdateTheFadeEdges() async throws {
        _ = NSApplication.shared
        let content = InspectorScrollView {
            VStack(spacing: 0) {
                ForEach(0..<30) { index in Text("Row \(index)").frame(height: 30) }
            }
        }
        let host = NSHostingView(rootView: content)
        let window = makeWindow()
        defer { window.close() }
        window.contentView = host
        try await settle(host)
        let scroll = try #require(descendants(host, of: NSScrollView.self).first)
        let observation = try #require(descendants(host, of: InspectorScrollObservationView.self).first)
        #expect(!observation.edges.top && observation.edges.bottom)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 100))
        scroll.reflectScrolledClipView(scroll.contentView)
        try await settle(host)
        #expect(observation.edges.top && observation.edges.bottom)
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 380))
        scroll.reflectScrolledClipView(scroll.contentView)
        try await settle(host)
        #expect(observation.edges.top && !observation.edges.bottom)
        window.setContentSize(NSSize(width: 320, height: 1000))
        try await settle(host)
        #expect(!observation.edges.top && !observation.edges.bottom)
    }

    @Test
    func longUsageAndFilesStayInIndependentBoundedNativeScrollViews() async throws {
        _ = NSApplication.shared
        let suite = "ResultInspectorTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(true, forKey: "parallel-codex.usage-breakdown-expanded")
        let run = try fixtureRun()
        let counts = Dictionary(uniqueKeysWithValues: (1...500).map { (String($0 * 100), 1) })
        let summary = AgentDiffSummary(changes: (1...100).map { index in
            AgentFileChange(status: "M", path: "Sources/file\(index).swift")
        })
        let content = ConversationResultDetails(liveMetrics: ActiveRunMetrics(), run: run, agent: agent(seconds: 30, counts: counts),
            diffSummary: summary, diffLoading: false, showDiff: {})
            .defaultAppStorage(defaults)
        let host = NSHostingView(rootView: content)
        let window = makeWindow()
        defer { window.close() }
        window.contentView = host
        try await settle(host)
        let split = try #require(descendants(host, of: NSSplitView.self).first)
        #expect(split.arrangedSubviews.count == 2)
        #expect(split.frame.height < 520)
        let scrolls = descendants(host, of: NSScrollView.self)
        #expect(scrolls.count == 2)
        for scroll in scrolls {
            #expect(scroll.frame.height > 10 && scroll.frame.height < 300)
            #expect(scroll.documentView!.frame.height > scroll.contentSize.height)
            #expect(scroll.borderType == .noBorder)
        }
        #expect(host.frame.height == 520)
    }

    @Test
    func foldingUsageDoesNotLoseTheDraggableSectionPosition() async throws {
        _ = NSApplication.shared
        let key = ResultInspectorLayout.sectionsAutosaveName + ".position"
        let previous = UserDefaults.standard.object(forKey: key)
        defer { UserDefaults.standard.set(previous, forKey: key) }
        UserDefaults.standard.set(160.0, forKey: key)
        let suite = "ResultInspectorTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(true, forKey: "parallel-codex.usage-breakdown-expanded")
        let content = ConversationResultDetails(liveMetrics: ActiveRunMetrics(), run: try fixtureRun(),
            agent: agent(seconds: 30, counts: ["100": 4, "200": 3]),
            diffSummary: nil, diffLoading: false, showDiff: {})
            .defaultAppStorage(defaults)
        let host = NSHostingView(rootView: content)
        let window = makeWindow()
        defer { window.close() }
        window.contentView = host
        try await settle(host)
        let split = try #require(descendants(host, of: NSSplitView.self).first)
        #expect(abs(split.arrangedSubviews[0].frame.height - 160) < 1)
        defaults.set(false, forKey: "parallel-codex.usage-breakdown-expanded")
        try await settle(host)
        #expect(abs(split.arrangedSubviews[0].frame.height - 44) < 1)
        #expect(UserDefaults.standard.double(forKey: key) == 160)
        defaults.set(true, forKey: "parallel-codex.usage-breakdown-expanded")
        try await settle(host)
        #expect(abs(split.arrangedSubviews[0].frame.height - 160) < 1)
        #expect(UserDefaults.standard.double(forKey: key) == 160)
    }

    private func fixtureRun() throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"inspector-scroll-qa","status":"completed","workspace":"/tmp/qa","prompt":"QA",
         "createdAt":"2026-09-19","updatedAt":"2026-09-19","config":{},
         "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "artifactsAvailable":true,"workerActive":false,"workerAvailable":false,
         "eventCount":0,"agents":[]}
        """.utf8))
    }

    private func makeWindow() -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 520),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }

    private func settle(_ host: NSView) async throws {
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(100))
        host.layoutSubtreeIfNeeded()
    }

    private func renderNative<Content: View>(_ content: Content) async throws -> NSBitmapImageRep {
        let window = makeWindow()
        defer { window.close() }
        let host = NSHostingView(rootView: content)
        window.contentView = host
        try await settle(host)
        #expect(host.frame.size == CGSize(width: 320, height: 520))
        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: image)
        return image
    }

    private func descendants<T: NSView>(_ view: NSView, of type: T.Type) -> [T] {
        (view as? T).map { [$0] } ?? view.subviews.flatMap { descendants($0, of: type) }
    }

    private func agent(seconds: Double?, tokens: Int? = nil, counts: [String: Int] = [:], number: Int = 1,
                       recommended: Bool = true) -> RunAgentSummary {
        RunAgentSummary(
            agent: number, name: "AGENT-001", role: nil, status: "success", seconds: seconds,
            reasoningTokens: tokens, reasoningTokenCounts: counts, codexThreadId: nil,
            error: nil, rejected: false, stale: nil, recommended: recommended, finalized: false,
            workspaceAvailable: true, canAccept: true, canContinue: true, canRetry: false
        )
    }
}
