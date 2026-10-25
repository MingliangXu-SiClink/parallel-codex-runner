import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ComposerControlsTests {
    @Test func actionSymbolsKeepOneCircularSizeAcrossAllStatesAppearancesAndZooms() throws {
        _ = NSApplication.shared
        for size in AppTextSize.allCases {
            for scheme in [ColorScheme.dark, .light] {
                for kind in [ComposerPrimaryAction.Kind.send, .queue, .stop, .sending] {
                    #expect(NSImage(systemSymbolName: kind == .stop ? "stop.fill" : "arrow.up",
                        accessibilityDescription: nil) != nil)
                    let host = NSHostingView(rootView: Button(action: { Issue.record("Preview must not submit or stop") }) {
                        ComposerActionSymbol(kind: kind)
                    }.buttonStyle(ComposerActionButtonStyle()).environment(\.appTextSize, size)
                        .environment(\.colorScheme, scheme))
                    #expect(abs(host.fittingSize.width - 28 * size.scale) < 1)
                    #expect(abs(host.fittingSize.height - 28 * size.scale) < 1)
                }
                let host = NSHostingView(rootView: HStack(spacing: 16) {
                    ForEach(0..<3) { index in
                        Button(action: {}) { ComposerActionSymbol(kind: [ComposerPrimaryAction.Kind.send, .stop, .queue][index]) }
                            .buttonStyle(ComposerActionButtonStyle())
                    }
                    Button(action: {}) { ComposerActionSymbol(kind: .send) }
                        .buttonStyle(ComposerActionButtonStyle()).disabled(true)
                }.padding(20).background(ParallelCodexVisualStyle.composerSurface)
                    .environment(\.appTextSize, size).environment(\.colorScheme, scheme))
                let window = makeWindow(host: host, size: host.fittingSize, scheme: scheme)
                defer { window.close() }
                try save(host, name: "actions-\(size)-\(scheme).png")
                #expect(!window.isVisible && !window.isKeyWindow)
            }
        }
    }

    @Test func actualComposerKeepsCompleteSummaryBesideTheFixedPrimaryActionWithoutAnotherRow() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        for running in [false, true] {
            try fixture.configure(running: running, draft: true)
            for language in [AppLanguage.chinese, .english] {
                fixture.model.appLanguage = language
                for size in [AppTextSize.standard, .extraLarge] {
                    for width: CGFloat in [350, 560, 960] {
                        let host = NSHostingView(rootView: PromptComposer().environmentObject(fixture.model)
                            .environment(\.appLanguage, language).environment(\.appTextSize, size)
                            .environment(\.colorScheme, .dark).frame(width: width))
                        let window = makeWindow(host: host, size: host.fittingSize, scheme: .dark)
                        host.layoutSubtreeIfNeeded()
                        try await Task.sleep(for: .milliseconds(30))
                        let anchors = descendants(host).filter { $0.identifier?.rawValue == "composer.execution-anchor" }
                        let anchor = try #require(anchors.first)
                        #expect(anchors.count == 1)
                        let first = anchor.convert(anchor.bounds, to: host)
                        #expect(first.width > 80)
                        #expect(abs(first.height - 28 * size.scale) < 1)
                        let image = try bitmap(host)
                        let ratio = CGFloat(image.pixelsWide) / host.bounds.width
                        let y = Int((host.isFlipped ? first.midY : host.bounds.maxY - first.midY)
                            * CGFloat(image.pixelsHigh) / host.bounds.height)
                        let afterSummary = Int((first.maxX + 2) * ratio)
                        let background = try #require(image.colorAt(x: afterSummary, y: y)?.usingColorSpace(.sRGB))
                        let buttonPixels = (afterSummary..<image.pixelsWide).filter { x in
                            guard let color = image.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { return false }
                            return abs(color.redComponent - background.redComponent) > 0.4
                        }
                        let left = CGFloat(try #require(buttonPixels.first)) / ratio
                        let right = CGFloat(try #require(buttonPixels.last) + 1) / ratio
                        #expect(abs(right - left - 28 * size.scale) < 2,
                            "The primary circle remains beside the summary, at full diameter on the same baseline.")
                        #expect(abs(left - first.maxX - 12) < 2)
                        #expect(host.fittingSize.width <= width + 1)
                        let description = ComposerExecutionConfigurationSummary.description(in: fixture.model)
                        #expect(description.contains("GPT-6.1 Sol") && description.contains("GPT-6 Astra"))
                        #expect(description.contains("8") && description.contains("3"))
                        #expect(!description.contains("…") && !description.contains("..."))
                        #expect(fixture.model.prompt == fixture.draft && fixture.model.pendingPromptSubmission == nil)
                        try save(host, name: "composer-\(running)-\(language)-\(size)-\(Int(width)).png")
                        #expect(!window.isVisible && !window.isKeyWindow)
                        window.close()
                    }
                }
            }
        }
    }

    @Test func headerlessEditorPresentsUpwardFromTheActualFooterAndPreservesTheDraft() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        try fixture.configure(running: false, draft: true)
        fixture.model.appLanguage = .chinese
        for scheme in [ColorScheme.dark, .light] {
            let host = NSHostingView(rootView: VStack(spacing: 0) {
                Spacer(minLength: 0)
                PromptComposer()
            }.environmentObject(fixture.model).environment(\.appLanguage, .chinese)
                .environment(\.appTextSize, .standard).environment(\.colorScheme, scheme)
                .background(ParallelCodexVisualStyle.canvas))
            let window = makeWindow(host: host, size: CGSize(width: 960, height: 320), scheme: scheme)
            defer { window.close() }
            try await Task.sleep(for: .milliseconds(40))
            let anchor = try #require(descendants(host).first { $0.identifier?.rawValue == "composer.execution-anchor" })
            let original = anchor.convert(anchor.bounds, to: nil)
            let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
            editor.setSelectedRange(NSRange(location: 3, length: 0))
            var presented = true
            let presentation = ExecutionConfigurationAnchor(isPresented: Binding(get: { presented }, set: { presented = $0 }),
                preferredWidth: 368) { width in
                ExecutionConfigurationPopover(width: width).environmentObject(fixture.model)
                    .environment(\.appLanguage, .chinese).environment(\.appTextSize, .standard)
                    .environment(\.colorScheme, scheme)
            }
            let coordinator = presentation.makeCoordinator()
            coordinator.anchor = anchor
            coordinator.update(presentation)
            defer { coordinator.remove() }
            try await Task.sleep(for: .milliseconds(60))
            let container = try #require(host.superview)
            let surface = try #require(container.subviews.first { $0.identifier?.rawValue == "configuration.surface" })
            #expect(window.contentLayoutRect.contains(container.convert(surface.frame, to: nil)))
            #expect(container.convert(surface.frame, to: nil).minY > original.maxY)
            #expect(anchor.convert(anchor.bounds, to: nil) == original)
            #expect(window.childWindows?.isEmpty ?? true)
            let content = NSHostingView(rootView: ComposerExecutionConfiguration().environmentObject(fixture.model)
                .environment(\.appTextSize, .standard).environment(\.appLanguage, .chinese))
            #expect(abs(surface.frame.height - content.fittingSize.height - 24) < 1)
            #expect(descendants(surface).compactMap { $0 as? NSPopUpButton }.count == 8)
            try save(container, name: "composer-open-\(scheme).png")
            presented = false
            coordinator.update(presentation)
            try await Task.sleep(for: .milliseconds(40))
            #expect(surface.superview == nil)
            #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
            #expect(editor.string == fixture.draft && editor.selectedRange().location == 3)
            #expect(fixture.model.prompt == fixture.draft && fixture.model.pendingPromptSubmission == nil)
            #expect(fixture.model.promptOutboxByWorkspace.isEmpty && fixture.model.followUpQueue.isEmpty)
            #expect(!window.isVisible && !window.isKeyWindow)
        }
    }

    @Test func actualComposerPreviewIncludesEmptyRunningAndQueuedActionsWithoutSending() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.appLanguage = .chinese
        for (name, running, draft) in [("send", false, true), ("empty", false, false),
                                       ("stop", true, false), ("queue", true, true)] {
            try fixture.configure(running: running, draft: draft)
            for width: CGFloat in [560, 960] {
                let host = NSHostingView(rootView: PromptComposer().environmentObject(fixture.model)
                    .environment(\.appLanguage, .chinese).environment(\.colorScheme, .dark).frame(width: width))
                let window = makeWindow(host: host, size: host.fittingSize, scheme: .dark)
                defer { window.close() }
                try await Task.sleep(for: .milliseconds(40))
                #expect(host.fittingSize.height <= 117)
                let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
                #expect(editor.font?.pointSize == 15)
                try save(host, name: "preview-\(name)-\(Int(width)).png")
                #expect(!window.isVisible && !window.isKeyWindow)
            }
            let action = ComposerPrimaryAction.make(hasDraft: draft, canSend: fixture.model.canSubmitPrompt,
                runIsStoppable: running, queuesMessage: fixture.model.promptSubmissionPlan == .enqueue,
                canStop: running, submissionInFlight: false)
            #expect(action.kind == (running ? (draft ? .queue : .stop) : .send))
            #expect(action.enabled == (running || draft))
            #expect(fixture.model.pendingPromptSubmission == nil && fixture.model.promptOutboxByWorkspace.isEmpty)
        }
    }

    @Test func emptyInputHasAFlatSurfaceAndRetainsItsTextInsetInBothAppearances() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        try fixture.configure(running: false, draft: false)
        for scheme in [ColorScheme.dark, .light] {
            let host = NSHostingView(rootView: PromptComposer().environmentObject(fixture.model)
                .environment(\.appLanguage, .chinese).environment(\.colorScheme, scheme).frame(width: 560))
            let window = makeWindow(host: host, size: host.fittingSize, scheme: scheme)
            defer { window.close() }
            try await Task.sleep(for: .milliseconds(30))
            let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
            let scroll = try #require(editor.enclosingScrollView)
            let rect = scroll.convert(scroll.bounds, to: host)
            #expect(editor.textContainerInset == NSSize(width: 5, height: 5))
            #expect(editor.textContainer?.lineFragmentPadding == 0)
            #expect(abs(rect.minX - 32) < 1)
            #expect(host.fittingSize.height <= 117)
            let image = try bitmap(host)
            let ratio = CGFloat(image.pixelsWide) / host.bounds.width
            let colors = try [rect.minY + 6, rect.midY, rect.maxY - 6].map { y in
                let pixelY = (host.isFlipped ? y : host.bounds.height - y) * ratio
                return try #require(image.colorAt(x: Int((rect.maxX - 8) * ratio), y: Int(pixelY))?.usingColorSpace(.sRGB))
            }
            #expect(colors.allSatisfy { abs($0.redComponent - colors[0].redComponent) < 0.01 })
            let expected = try #require(NSColor(calibratedWhite: (scheme == .dark ? 52 : 244) / 255,
                alpha: 1).usingColorSpace(.sRGB))
            #expect(abs(colors[0].redComponent - expected.redComponent) < 0.01)
            #expect(fixture.model.prompt.isEmpty && fixture.model.pendingPromptSubmission == nil)
            try save(host, name: "surface-\(scheme).png")
            #expect(!window.isVisible && !window.isKeyWindow)
        }
    }

    private func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }

    private func makeWindow<Content: View>(host: NSHostingView<Content>, size: CGSize, scheme: ColorScheme) -> NSWindow {
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        return window
    }

    private func save(_ view: NSView, name: String) throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_COMPOSER_CONTROLS_EVIDENCE"] else { return }
        view.layoutSubtreeIfNeeded()
        let directory = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let image = try bitmap(view)
        try #require(image.representation(using: .png, properties: [:])).write(to: directory.appendingPathComponent(name))
    }

    private func bitmap(_ view: NSView) throws -> NSBitmapImageRep {
        let image = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: image)
        return image
    }

    @MainActor private final class Fixture {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("composer-controls-\(UUID().uuidString)")
        let model: AppModel
        let draft = "请继续检查这个方案。"

        init() throws {
            model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
                tokenURL: directory.appendingPathComponent("absent.token")), windowID: UUID(),
                desktopStateStore: DesktopStateStore(directory: directory))
            model.settings.agents = 8
            model.settings.synthesisAgents = 3
            model.settings.model = "gpt-6.1-sol"
            model.settings.synthesisModel = "gpt-6-astra"
            model.settings.effort = "max"
            model.settings.synthesisEffort = "max"
            model.settings.fast = .off
            model.settings.synthesisFast = .off
        }

        func configure(running: Bool, draft: Bool) throws {
            model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
            {"workspaceId":"qa","path":"/qa","name":"QA","lastRunId":"\(running ? "run" : "")",
             "lastResumeSessionId":"","selected":true,"available":true,"readable":true,"writable":true}
            """.utf8))]
            model.selectedWorkspaceID = "qa"
            if running {
                let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
                {"runId":"run","conversationId":"conversation","status":"running","workspace":"/qa",
                 "prompt":"Fixture","createdAt":"2026-10-20T00:00:00Z","updatedAt":"2026-10-20T00:00:00Z",
                 "config":{},"syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
                 "workerActive":true,"workerAvailable":true,"eventCount":0,"canUpdateSynthesis":true,
                 "agents":[{"agent":1,"name":"Agent 1","status":"running","reasoningTokenCounts":{},
                             "rejected":false,"recommended":false,"finalized":false}]}
                """.utf8))
                model.activeRun = run
                model.workspaceConversations = workspaceConversationSummaries(from: [run])
                model.selectedConversationID = "conversation"
                model.selectedAgent = 1
            } else {
                model.activeRun = nil
                model.workspaceConversations = []
                model.selectedConversationID = AppModel.newConversationSelectionID
                model.selectedAgent = nil
            }
            model.daemonOnline = true
            model.preparePromptHistoryContext()
            model.prompt = draft ? self.draft : ""
        }

        func close() {
            model.promptDraftSaveTask?.cancel()
            model.stopWindowObservation()
            model.settingsPersistenceTasks.values.forEach { $0.cancel() }
            model.synthesisSettingsTasks.values.forEach { $0.cancel() }
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
