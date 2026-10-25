import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ExecutionConfigurationTests {
    @Test func automaticEffortLabelsUnderstandTheActualServiceWireValue() {
        #expect(executionEffortTitle(selection: "", effective: "auto (max)", language: .chinese) == "自动（最高）")
        #expect(executionEffortTitle(selection: "", effective: "auto (max)", language: .english) == "Auto (Maximum)")
        #expect(executionEffortTitle(selection: "", effective: "auto", language: .english) == "Auto")
        #expect(executionEffortTitle(selection: "", effective: nil, language: .chinese) == "自动")
        #expect(executionEffortTitle(selection: "high", effective: "auto (max)", language: .chinese) == "高")
    }

    @Test func onlyValidCountEditsCanApplyAndRangesAreExact() {
        for text in ["", "-1", "1.5", "4x", "9223372036854775808", "０８"] {
            #expect(ExecutionCountEditor.validated(text, range: 1...4096) == nil)
        }
        #expect(ExecutionCountEditor.validated("0", range: 1...4096) == nil)
        #expect(ExecutionCountEditor.validated("0", range: 0...4096) == 0)
        #expect(ExecutionCountEditor.validated(" 12 ", range: 1...4096) == 12)
        #expect(ExecutionCountEditor.validated("4096", range: 1...4096) == 4096)
        #expect(ExecutionCountEditor.validated("4097", range: 1...4096) == nil)
    }

    @Test func directEditsKeepTheTwoStagesIndependentAndPersistWithoutSendingMessages() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.prompt = "Keep this draft"
        let context = ExecutionEditContext(model)
        #expect(await model.editExecutionConfiguration(.count(12), stage: .candidates, context: context))
        #expect(await model.editExecutionConfiguration(.count(0), stage: .synthesis, context: context))
        #expect(await model.editExecutionConfiguration(.fast(.off), stage: .candidates, context: context))
        #expect(await model.editExecutionConfiguration(.fast(.on), stage: .synthesis, context: context))
        #expect(model.settings.agents == 12 && model.settings.synthesisAgents == 0)
        #expect(model.settings.fast == .off && model.settings.synthesisFast == .on)
        #expect(model.prompt == "Keep this draft")
        #expect(model.pendingPromptSubmission == nil && model.followUpQueue.isEmpty)
        let saved = fixture.store.loadSnapshot().settingsByWorkspace.values.first
        #expect(saved?.agents == 12 && saved?.synthesisFast == .on)
        #expect(!model.settingsPresented)
    }

    @Test func staleEditorsCannotAffectAnotherWorkspaceRunOrNavigationGeneration() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let context = ExecutionEditContext(model)
        model.selectedWorkspaceID = "other"
        #expect(!(await model.editExecutionConfiguration(.count(33), stage: .candidates, context: context)))
        model.selectedWorkspaceID = "qa"
        model.selectedWorkspaceID = "another-workspace"
        #expect(!(await model.editExecutionConfiguration(.fast(.on), stage: .synthesis, context: context)))
        model.workspaceContextGeneration = context.generation
        model.activeRun = try run(locked: false)
        #expect(!(await model.editExecutionConfiguration(.count(33), stage: .synthesis, context: context)))
        #expect(model.settings.agents == 8 && model.settings.synthesisAgents == 3)
    }

    @Test func synthesisLockShowsTheActualRunAndDoesNotLockCandidateConfiguration() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.activeRun = try run(locked: true)
        let context = ExecutionEditContext(model)
        #expect(ExecutionStage.synthesis.isLocked(in: model))
        #expect(!ExecutionStage.candidates.isLocked(in: model))
        let displayed = ExecutionStage.synthesis.displayedSettings(in: model)
        #expect(displayed.synthesisAgents == 2 && displayed.synthesisModel == "gpt-6-astra")
        #expect(displayed.synthesisEffort == "high" && displayed.synthesisFast == .off)
        let original = model.settings
        for edit in [ExecutionConfigurationEdit.count(0), .model("gpt-6.1-sol"), .effort("max"), .fast(.on)] {
            #expect(!(await model.editExecutionConfiguration(edit, stage: .synthesis, context: context)))
        }
        #expect(model.settings == original)
        #expect(await model.editExecutionConfiguration(.count(16), stage: .candidates, context: context))
        #expect(model.settings.agents == 16 && model.settings.synthesisAgents == 3)
    }

    @Test func summaryDescribesBothStagesAndUsesTheActualLockedSynthesis() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let original = model.settings
        for language in [AppLanguage.chinese, .english] {
            model.appLanguage = language
            let description = ComposerExecutionConfigurationSummary.description(in: model)
            #expect(description.contains("\(ExecutionStage.candidates.title(language)) × 8"))
            #expect(description.contains("\(ExecutionStage.synthesis.title(language)) × 3"))
            #expect(description.contains("GPT-6.1 Sol") && description.contains("GPT-6 Astra"))
            #expect(description.contains(original.fast.title(language: language)))
        }
        model.activeRun = try run(locked: true)
        let description = ComposerExecutionConfigurationSummary.description(in: model)
        #expect(description.contains("Synthesis × 2"))
        #expect(description.contains("High") && description.contains("Off"))
        #expect(model.settings == original && model.pendingPromptSubmission == nil)
    }

    @Test func collapsedSummaryStaysOneLineAtEveryZoomAndTheEditorIsAnIndependentSurface() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let original = model.settings
        for language in [AppLanguage.chinese, .english] {
            model.appLanguage = language
            for size in AppTextSize.allCases {
                for scheme in [ColorScheme.dark, .light] {
                    for width: CGFloat in [260, 350, 960] {
                        var entrySize: CGSize?
                        let host = NSHostingView(rootView: ComposerExecutionConfigurationSummary().environmentObject(model)
                            .environment(\.appTextSize, size).environment(\.appLanguage, language)
                            .background(GeometryReader { geometry in
                                Color.clear.onAppear { entrySize = geometry.size }
                                    .onChange(of: geometry.size) { entrySize = $0 }
                            })
                            .environment(\.colorScheme, scheme).frame(width: width, alignment: .leading)
                            .background(ParallelCodexVisualStyle.canvas))
                        let fitting = host.fittingSize
                        #expect(fitting.width <= width + 1)
                        #expect(abs(fitting.height - 28 * size.scale) < 1)
                        let window = makeWindow(size: fitting, scheme: scheme)
                        window.contentView = host
                        host.layoutSubtreeIfNeeded()
                        try await Task.sleep(for: .milliseconds(20))
                        host.layoutSubtreeIfNeeded()
                        let entry = try #require(entrySize)
                        #expect(entry.width > 120 && entry.width <= width + 1)
                        if width == 960 {
                            #expect(entry.width < width - 80, "Only the summary content may be interactive, not the empty row.")
                        }
                        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                        host.cacheDisplay(in: host.bounds, to: image)
                        try save(image, name: "summary-\(language)-\(size)-\(Int(width))-\(scheme).png")
                        #expect(!window.isVisible && !window.isKeyWindow)
                        #expect(descendants(host).compactMap { $0 as? NSTextField }.filter(\.isEditable).isEmpty)
                        window.close()
                    }
                    let host = NSHostingView(rootView: ExecutionConfigurationPopover()
                        .environmentObject(model).environment(\.appTextSize, size)
                        .environment(\.appLanguage, language).environment(\.colorScheme, scheme))
                    #expect(abs(host.fittingSize.width - 368 * size.scale) < 1)
                    #expect(host.fittingSize.height > 68 * size.scale && host.fittingSize.height < 248 * size.scale)
                    let window = makeWindow(size: host.fittingSize, scheme: scheme)
                    window.contentView = host
                    host.layoutSubtreeIfNeeded()
                    let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                    host.cacheDisplay(in: host.bounds, to: image)
                    try save(image, name: "popover-\(language)-\(size)-\(scheme).png")
                    #expect(!window.isVisible && !window.isKeyWindow)
                    window.close()
                }
            }
        }
        #expect(model.settings == original && model.prompt.isEmpty && model.pendingPromptSubmission == nil)
    }

    @Test func fullModelNamesStayOnOneSummaryLineAndWrapInsideTheCompactConfiguration() throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let longNames = ["GPT Candidate Complete Model Name With Extended Reasoning Support",
                         "GPT Synthesis Complete Model Name With Independent Reasoning Support"]
        for language in [AppLanguage.chinese, .english] {
            model.appLanguage = language
            for size in [AppTextSize.standard, .extraLarge] {
                for scheme in [ColorScheme.dark, .light] {
                    model.settings.model = "gpt-6.1-sol"
                    model.settings.synthesisModel = "gpt-6-astra"
                    let shortSummary = NSHostingView(rootView: ComposerExecutionConfigurationSummary()
                        .environmentObject(model).environment(\.appTextSize, size).frame(width: 260)).fittingSize
                    let shortPanel = NSHostingView(rootView: ExecutionConfigurationPopover()
                        .environmentObject(model).environment(\.appTextSize, size)).fittingSize
                    model.settings.model = longNames[0]
                    model.settings.synthesisModel = longNames[1]
                    let description = ComposerExecutionConfigurationSummary.description(in: model)
                    #expect(longNames.allSatisfy { description.contains($0) })
                    let cases: [(String, AnyView, CGFloat)] = [
                        ("summary", AnyView(ComposerExecutionConfigurationSummary().environmentObject(model)
                            .frame(width: 260, alignment: .leading)), shortSummary.height),
                        ("popover", AnyView(ExecutionConfigurationPopover().environmentObject(model)),
                         shortPanel.height + 30 * size.scale),
                    ]
                    for (name, view, minimumHeight) in cases {
                        let host = NSHostingView(rootView: view.environment(\.appTextSize, size)
                            .environment(\.appLanguage, language).environment(\.colorScheme, scheme)
                            .background(ParallelCodexVisualStyle.subtleSurface))
                        if name == "summary" {
                            #expect(abs(host.fittingSize.height - 28 * size.scale) < 1)
                        } else {
                            #expect(host.fittingSize.height > minimumHeight, "Expanded labels gain height rather than truncate.")
                        }
                        #expect(host.fittingSize.width <= (name == "popover" ? 368 * size.scale : 260) + 1)
                        let window = makeWindow(size: host.fittingSize, scheme: scheme)
                        window.contentView = host
                        host.layoutSubtreeIfNeeded()
                        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                        host.cacheDisplay(in: host.bounds, to: image)
                        try save(image, name: "full-names-\(name)-\(language)-\(size)-\(scheme).png")
                        #expect(!window.isVisible && !window.isKeyWindow)
                        window.close()
                    }
                }
            }
        }
        model.settings.synthesisAgents = 0
        model.settings.synthesisModel = model.settings.model
        let summary = ComposerExecutionConfigurationSummary.description(in: model)
        #expect(summary.components(separatedBy: model.settings.model).count == 3,
                "Both model names remain present when identical or synthesis is disabled.")
        #expect(model.pendingPromptSubmission == nil && model.followUpQueue.isEmpty)
    }

    @Test func narrowSummaryScrollsHorizontallyWithoutWrappingTruncatingOrChangingConfiguration() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.settings.model = "GPT Candidate Complete Model Name With Extended Reasoning Support"
        fixture.model.settings.synthesisModel = "GPT Synthesis Complete Model Name With Independent Reasoning Support"
        let original = fixture.model.settings
        for language in [AppLanguage.chinese, .english] {
            fixture.model.appLanguage = language
            for size in [AppTextSize.standard, .extraLarge] {
                let host = NSHostingView(rootView: ComposerExecutionConfigurationSummary().environmentObject(fixture.model)
                    .environment(\.appTextSize, size).environment(\.appLanguage, language)
                    .environment(\.colorScheme, .dark).frame(width: 260, alignment: .leading)
                    .background(ParallelCodexVisualStyle.canvas))
                let window = makeWindow(size: host.fittingSize, scheme: .dark)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(30))
                let scroll = try #require(descendants(host).compactMap { $0 as? NSScrollView }.first)
                let document = try #require(scroll.documentView)
                #expect(document.bounds.width > scroll.contentView.bounds.width + 100)
                #expect(abs(host.fittingSize.height - 28 * size.scale) < 1)
                scroll.contentView.scroll(to: NSPoint(x: document.bounds.maxX - scroll.contentView.bounds.width, y: 0))
                scroll.reflectScrolledClipView(scroll.contentView)
                host.layoutSubtreeIfNeeded()
                #expect(scroll.contentView.bounds.minX > 0, "Both complete model names remain reachable on the same line.")
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                try save(image, name: "single-line-scrolled-\(language)-\(size).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
        #expect(fixture.model.settings == original && fixture.model.pendingPromptSubmission == nil)
    }

    @Test func compactPipelineKeepsBothStagesAdjacentAndStillStacksAtNarrowWidths() async throws {
        _ = NSApplication.shared
        for size in AppTextSize.allCases {
            for wide in [true, false] {
                var frames: [Int: CGRect] = [:]
                let host = NSHostingView(rootView: ExecutionPipelineLayout(scale: size.scale) {
                    ForEach(0..<2, id: \.self) { index in
                        Color.clear.frame(height: 64 * size.scale)
                            .background(GeometryReader { geometry in
                                Color.clear.onAppear { frames[index] = geometry.frame(in: .named("pipeline")) }
                                    .onChange(of: geometry.frame(in: .named("pipeline"))) { frames[index] = $0 }
                            })
                    }
                }
                .coordinateSpace(name: "pipeline")
                .frame(width: (wide ? 344 : 318) * size.scale))
                let window = makeWindow(size: host.fittingSize, scheme: .dark)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                await Task.yield()
                let first = try #require(frames[0]), last = try #require(frames[1])
                if wide {
                    #expect(abs(first.minY - last.minY) < 1)
                    #expect(abs(first.width - 164 * size.scale) < 1)
                    #expect(abs(last.minX - first.maxX - 16 * size.scale) < 1)
                } else {
                    #expect(abs(last.minY - first.maxY - 20 * size.scale) < 1)
                    #expect(abs(first.minX) < 1)
                    #expect(abs(first.width - host.bounds.width) < 1)
                }
                #expect(last.maxX <= host.bounds.width + 1)
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
    }

    @Test func compactPopoverRendersBothRunningStatesWithoutChangingEitherStage() throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model, original = fixture.model.settings
        for language in [AppLanguage.chinese, .english] {
            model.appLanguage = language
            for size in AppTextSize.allCases {
                for scheme in [ColorScheme.dark, .light] {
                    for locked in [false, true] {
                        model.activeRun = try run(locked: locked)
                        let host = NSHostingView(rootView: ExecutionConfigurationPopover()
                            .environmentObject(model).environment(\.appTextSize, size)
                            .environment(\.appLanguage, language).environment(\.colorScheme, scheme))
                        #expect(abs(host.fittingSize.width - 368 * size.scale) < 1)
                        #expect(host.fittingSize.height < 230 * size.scale)
                        let window = makeWindow(size: host.fittingSize, scheme: scheme)
                        window.contentView = host
                        host.layoutSubtreeIfNeeded()
                        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                        host.cacheDisplay(in: host.bounds, to: image)
                        try save(image, name: "popover-state-\(language)-\(size)-\(scheme)-\(locked).png")
                        #expect(!window.isVisible && !window.isKeyWindow)
                        window.close()
                    }
                }
            }
        }
        #expect(model.settings == original && model.prompt.isEmpty && model.pendingPromptSubmission == nil)
    }

    @Test func summaryEmphasisChangesOnlyForegroundWithoutAFullRowFillOrLayoutShift() throws {
        _ = NSApplication.shared
        for scheme in [ColorScheme.dark, .light] {
            var images: [NSBitmapImageRep] = []
            var sizes: [CGSize] = []
            for emphasized in [false, true] {
                var entrySize: CGSize?
                let host = NSHostingView(rootView: Button(action: { Issue.record("Rendering must not execute commands") }) {
                    HStack(spacing: 8) {
                        Image(systemName: "square.stack.3d.up").foregroundStyle(.orange)
                        Text("Candidates 8")
                        Image(systemName: "chevron.down")
                    }
                    .appScaledSystemFont(size: 11, weight: .medium)
                    .foregroundStyle(.secondary)
                    .frame(width: 220, height: 28)
                    .contentShape(Rectangle())
                }
                .buttonStyle(ExecutionSummaryButtonStyle(selected: emphasized))
                .background(GeometryReader { geometry in
                    Color.clear.onAppear { entrySize = geometry.size }
                        .onChange(of: geometry.size) { entrySize = $0 }
                })
                .frame(width: 800, alignment: .leading).padding(10)
                .background(ParallelCodexVisualStyle.canvas).environment(\.colorScheme, scheme))
                let window = makeWindow(size: host.fittingSize, scheme: scheme)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                sizes.append(try #require(entrySize))
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                images.append(image)
                try save(image, name: "summary-emphasis-\(emphasized)-\(scheme).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
            #expect(sizes[0] == sizes[1] && sizes[0].width == 220)
            let resting = images[0], emphasized = images[1]
            #expect(resting.pixelsWide == emphasized.pixelsWide && resting.pixelsHigh == emphasized.pixelsHigh)
            var changed = 0
            for y in 0..<resting.pixelsHigh {
                for x in 0..<resting.pixelsWide {
                    if resting.colorAt(x: x, y: y) != emphasized.colorAt(x: x, y: y) { changed += 1 }
                }
            }
            #expect(changed > 10, "Emphasis must visibly brighten the existing foreground.")
            #expect(changed < resting.pixelsWide * resting.pixelsHigh / 20,
                "Emphasis must not paint a rectangle behind the entry or the unused row.")
        }
    }

    @Test func nativeSurfaceFitsBothLanguagesAppearancesAndAllZoomsWithoutMutatingSettings() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let original = model.settings
        for language in [AppLanguage.chinese, .english] {
            model.appLanguage = language
            for size in AppTextSize.allCases {
                for width: CGFloat in [350, 960] {
                    for scheme in [ColorScheme.dark, .light] {
                        let content = ComposerExecutionConfiguration().environmentObject(model)
                            .environment(\.appTextSize, size).environment(\.appLanguage, language)
                            .environment(\.colorScheme, scheme).tint(.orange)
                            .padding(16).background(ParallelCodexVisualStyle.composerSurface)
                            .frame(width: width)
                        let host = NSHostingView(rootView: content)
                        let fitting = host.fittingSize
                        #expect(fitting.width <= width + 1)
                        #expect(fitting.height > 60 && fitting.height < 320)
                        let window = makeWindow(size: fitting, scheme: scheme)
                        window.contentView = host
                        host.layoutSubtreeIfNeeded()
                        try await Task.sleep(for: .milliseconds(40))
                        host.layoutSubtreeIfNeeded()
                        window.setContentSize(host.fittingSize)
                        host.layoutSubtreeIfNeeded()
                        if language == .chinese, size == .standard, width == 350 {
                            #expect(host.fittingSize.height < 180, "Narrow stages should use two baselines when their values fit.")
                        }
                        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                        host.cacheDisplay(in: host.bounds, to: image)
                        #expect(image.pixelsWide > 0 && image.pixelsHigh > 0)
                        if language == .chinese, size == .standard, width == 350, scheme == .dark {
                            #expect(inkBandsInFirstStage(image) == 2, "A compact native stage must actually paint two rows, not compress three rows into a shorter frame.")
                        }
                        let colors = Set(stride(from: 0, to: image.pixelsHigh, by: 4).flatMap { y in
                            stride(from: 0, to: image.pixelsWide, by: 4).compactMap { x in image.colorAt(x: x, y: y)?.description }
                        })
                        #expect(colors.count > 10)
                        try save(image, name: "execution-\(language)-\(size)-\(Int(width))-\(scheme).png")
                        #expect(!window.isVisible && !window.isKeyWindow)
                        window.close()
                    }
                }
            }
        }
        #expect(model.settings == original && model.pendingPromptSubmission == nil)
    }

    @Test func optionMenusKeepOnlyTheirLabelInThePanelRegardlessOfOptionCount() throws {
        _ = NSApplication.shared
        for size in [AppTextSize.standard, .extraLarge] {
            let models = ["Codex 默认", "GPT-6.1 Sol", "GPT-6 Astra", "GPT-6 Sol", "GPT-6 Luna"].map {
                CodexOption(label: $0, value: $0, selected: false)
            }
            var previousSize: CGSize?
            for choices in [models, models + (0..<100).map { CodexOption(label: "Extra model \($0)", value: "extra-\($0)", selected: false) }] {
                let host = NSHostingView(rootView: ExecutionOptionMenu(title: "Models", choices: choices, selection: "GPT-6.1 Sol",
                    select: { _ in Issue.record("Rendering is read only") }) {
                        Text("GPT-6.1 Sol").appScaledSystemFont(size: 15, weight: .semibold)
                            .frame(minHeight: 24 * size.scale)
                    }.frame(width: 164 * size.scale).environment(\.appTextSize, size)
                    .environment(\.appLanguage, .chinese).environment(\.colorScheme, .dark).tint(.orange))
                let window = makeWindow(size: host.fittingSize, scheme: .dark)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                #expect(host.fittingSize.height < 40 * size.scale)
                if let previousSize { #expect(host.fittingSize == previousSize) }
                previousSize = host.fittingSize
                #expect(!descendants(host).contains { ($0 as? NSTextField)?.isEditable == true })
                try save(image, name: "option-menu-\(choices.count)-\(size).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
    }

    @Test func runningScopesRemainSeparateAndLockedSynthesisIsStillReadable() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        for locked in [false, true] {
            fixture.model.activeRun = try run(locked: locked)
            for width: CGFloat in [460, 900] {
                let host = NSHostingView(rootView: ComposerExecutionConfiguration().environmentObject(fixture.model)
                    .environment(\.appTextSize, .standard).environment(\.appLanguage, .chinese)
                    .environment(\.colorScheme, .dark).padding(16).background(ParallelCodexVisualStyle.composerSurface).frame(width: width))
                let window = makeWindow(size: host.fittingSize, scheme: .dark)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(40))
                host.layoutSubtreeIfNeeded()
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                try save(image, name: "running-\(locked ? "locked" : "editable")-\(Int(width)).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
    }

    @Test func countEditorAppliesValidTypingImmediatelyWithoutConfirmationOrReflowDuplicates() async throws {
        _ = NSApplication.shared
        var commits: [Int] = []
        let host = NSHostingView(rootView: ExecutionCountEditor(title: "Candidates", value: 8, range: 1...4096,
            commit: { commits.append($0) }).frame(width: 300))
        let window = makeWindow(size: host.fittingSize, scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let field = try #require(descendants(host).compactMap { $0 as? NSTextField }.first { $0.isEditable })
        for text in ["", "x", "4097", "1", "8", "12"] {
            field.stringValue = text
            field.delegate?.controlTextDidChange?(Notification(name: NSControl.textDidChangeNotification, object: field))
            await Task.yield()
            host.layoutSubtreeIfNeeded()
        }
        #expect(commits == [1, 8, 12], "Returning to the initial count must still apply when the model callback is delayed.")
        window.setContentSize(CGSize(width: 340, height: 260))
        host.layoutSubtreeIfNeeded()
        #expect(descendants(host).contains { $0 === field })
        #expect(field.stringValue == "12" && commits == [1, 8, 12])
        if let action = field.action { field.sendAction(action, to: field.target) }
        await Task.yield()
        #expect(commits == [1, 8, 12], "Enter must not require or duplicate a confirmation.")
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func nativeCountMenuAppliesDirectlyWithoutAnotherPanelOrPermanentInput() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let root = VStack {
            Spacer()
            Color.clear.frame(height: 28)
                .background(ExecutionConfigurationAnchor(isPresented: .constant(true), preferredWidth: 368) { width in
                    ExecutionConfigurationPopover(width: width).environmentObject(model)
                        .environment(\.appTextSize, .standard).environment(\.appLanguage, .chinese)
                })
            Color.clear.frame(height: 100)
        }
        let host = NSHostingView(rootView: root.environment(\.colorScheme, .dark))
        let window = makeWindow(size: CGSize(width: 760, height: 480), scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(80))
        let container = try #require(host.superview)
        let parent = try #require(container.subviews.compactMap { $0 as? NSScrollView }.first)
        let frame = parent.frame
        let visibleWindows = NSApplication.shared.windows.filter(\.isVisible).map(ObjectIdentifier.init)
        let buttons = countButtons(in: parent)
            .sorted { $0.convert($0.bounds, to: container).minX < $1.convert($1.bounds, to: container).minX }
        #expect(buttons.count == 2)
        #expect(!descendants(parent).contains { ($0 as? NSTextField)?.isEditable == true })
        let candidate = try #require(buttons.first)
        let synthesis = try #require(buttons.last)
        let candidateFrame = candidate.convert(candidate.bounds, to: container)
        let captureBounds = frame.insetBy(dx: -8, dy: -8)
        let image = try #require(container.bitmapImageRepForCachingDisplay(in: captureBounds))
        container.cacheDisplay(in: captureBounds, to: image)
        try save(image, name: "count-popup-rest.png")
        for count in [1, 3, 8] {
            try selectCountMenu(candidate, title: String(count))
            try await Task.sleep(for: .milliseconds(20))
            parent.documentView?.layoutSubtreeIfNeeded()
            #expect(model.settings.agents == count)
            #expect(parent.frame == frame && candidate.convert(candidate.bounds, to: container) == candidateFrame)
            #expect(container.subviews.compactMap { $0 as? NSScrollView }.count == 1)
            #expect(NSApplication.shared.windows.filter(\.isVisible).map(ObjectIdentifier.init) == visibleWindows)
            #expect(window.childWindows?.isEmpty ?? true)
        }
        #expect(model.settings.agents == 8 && parent.superview === container)
        try selectCountMenu(candidate, title: "自定")
        try await Task.sleep(for: .milliseconds(40))
        parent.documentView?.layoutSubtreeIfNeeded()
        let input = try #require(descendants(parent).compactMap { $0 as? NSTextField }.first { $0.isEditable })
        #expect(window.makeFirstResponder(input))
        input.stringValue = "invalid"
        input.delegate?.controlTextDidChange?(Notification(name: NSControl.textDidChangeNotification, object: input))
        try await Task.sleep(for: .milliseconds(20))
        #expect(window.makeFirstResponder(nil))
        try await Task.sleep(for: .milliseconds(20))
        #expect(input.stringValue == "8" && model.settings.agents == 8,
                "Leaving an invalid edit must restore the last valid count without sending a new value.")
        #expect(!descendants(parent).contains { ($0 as? NSTextField)?.isEditable == true })
        try selectCountMenu(synthesis, title: "0")
        try await Task.sleep(for: .milliseconds(40))
        #expect(model.settings.synthesisAgents == 0 && parent.frame == frame)
        let currentCandidate = try #require(countButtons(in: parent)
            .first { $0 !== synthesis })
        let originalMenu = try #require(currentCandidate.menu)
        model.selectedWorkspaceID = "another-workspace"
        try await Task.sleep(for: .milliseconds(60))
        parent.documentView?.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(60))
        #expect(!currentCandidate.isDescendant(of: parent), "Changing context must retire the old native count menu.")
        #expect(currentCandidate.target == nil && currentCandidate.action == nil)
        if let index = originalMenu.items.firstIndex(where: { $0.title == "3" }) {
            originalMenu.performActionForItem(at: index)
        }
        try await Task.sleep(for: .milliseconds(20))
        #expect(model.settings.agents == 8 && model.promptOutboxByWorkspace.isEmpty)
        #expect(parent.superview === container && container.subviews.compactMap { $0 as? NSScrollView }.count == 1)
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func customCountFitsFourDigitsAtEveryTextSizeAndDoesNotResizeWhileTyping() async throws {
        _ = NSApplication.shared
        for size in AppTextSize.allCases {
            for scheme in [ColorScheme.light, .dark] {
                var commits: [Int] = []
                let host = NSHostingView(rootView: ExecutionCountEditor(title: "候选", value: 4096, range: 1...4096,
                    commit: { commits.append($0) })
                    .padding(10 * size.scale).background(ParallelCodexVisualStyle.subtleSurface)
                    .environment(\.appTextSize, size).environment(\.appLanguage, .chinese).environment(\.colorScheme, scheme))
                let window = makeWindow(size: host.fittingSize, scheme: scheme)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                let field = try #require(descendants(host).compactMap { $0 as? NSTextField }.first { $0.isEditable })
                #expect(!descendants(host).contains { $0 is NSStepper })
                #expect(field.stringValue == "4096" && !field.isBezeled)
                let textWidth = (field.stringValue as NSString).size(withAttributes: [.font: field.font!]).width
                #expect(field.bounds.width >= textWidth, "The complete count must fit without ellipsis or a smaller font.")
                let original = host.fittingSize
                field.stringValue = "4095"
                field.delegate?.controlTextDidChange?(Notification(name: NSControl.textDidChangeNotification, object: field))
                await Task.yield()
                host.layoutSubtreeIfNeeded()
                #expect(commits == [4095], "\(commits); native field: \(field.stringValue)")
                #expect(field.stringValue == "4095")
                #expect(host.fittingSize == original)
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                try save(image, name: "count-inline-\(size)-\(scheme).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
    }

    @Test func countPopUpRendersFullValuesAtEveryTextSizeAndAppearance() throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.settings.agents = 4096
        fixture.model.settings.synthesisAgents = 4096
        for size in AppTextSize.allCases {
            for scheme in [ColorScheme.light, .dark] {
                let host = NSHostingView(rootView: ExecutionConfigurationPopover().environmentObject(fixture.model)
                    .environment(\.appLanguage, .chinese).environment(\.appTextSize, size).environment(\.colorScheme, scheme))
                let window = makeWindow(size: host.fittingSize, scheme: scheme)
                window.contentView = host
                host.layoutSubtreeIfNeeded()
                let buttons = countButtons(in: host)
                #expect(buttons.count == 2)
                for button in buttons {
                    let menu = try #require(button.menu)
                    menu.delegate?.menuNeedsUpdate?(menu)
                    menu.update()
                    #expect(menu.items.contains { $0.title == "4096" })
                    #expect(button.title == "4096")
                    let textWidth = (button.title as NSString).size(withAttributes: [.font: button.font!]).width
                    #expect(try #require(button.cell).titleRect(forBounds: button.bounds).width >= textWidth)
                }
                #expect(!descendants(host).contains { ($0 as? NSTextField)?.isEditable == true })
                let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
                host.cacheDisplay(in: host.bounds, to: image)
                try save(image, name: "count-popup-full-\(size)-\(scheme).png")
                #expect(!window.isVisible && !window.isKeyWindow)
                window.close()
            }
        }
    }

    @Test func lockedNativeSynthesisCountIsDisabledAndShowsTheActualConfiguration() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.activeRun = try run(locked: true)
        let actual = ExecutionStage.synthesis.displayedSettings(in: fixture.model).synthesisAgents
        let host = NSHostingView(rootView: ExecutionConfigurationPopover().environmentObject(fixture.model)
            .environment(\.appLanguage, .chinese).environment(\.appTextSize, .standard).environment(\.colorScheme, .dark))
        let window = makeWindow(size: host.fittingSize, scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let buttons = countButtons(in: host)
            .sorted { $0.convert($0.bounds, to: host).minX < $1.convert($1.bounds, to: host).minX }
        #expect(buttons.count == 2)
        let candidates = try #require(buttons.first)
        let synthesis = try #require(buttons.last)
        #expect(candidates.isEnabled && !synthesis.isEnabled)
        let menu = try #require(synthesis.menu)
        menu.delegate?.menuNeedsUpdate?(menu)
        menu.update()
        #expect(menu.items.contains { $0.title == String(actual) })
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func fullComposerKeepsTheNativeDraftAndMarkedTextAcrossPipelineReflow() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.promptDraftContext = "offscreen-execution"
        fixture.model.prompt = "A retained draft"
        let host = NSHostingView(rootView: PromptComposer().environmentObject(fixture.model)
            .environment(\.appTextSize, .standard).environment(\.colorScheme, .dark).tint(.orange))
        #expect(host.fittingSize.height < 230, "The collapsed composer must not reserve space for the pipeline editor.")
        let window = makeWindow(size: CGSize(width: 960, height: 350), scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let editor = try #require(descendants(host).compactMap { $0 as? NSTextView }.first)
        await Task.yield()
        window.makeFirstResponder(editor)
        await Task.yield()
        editor.setSelectedRange(NSRange(location: 1, length: 0))
        editor.setMarkedText("输入", selectedRange: NSRange(location: 2, length: 0), replacementRange: editor.selectedRange())
        let text = editor.string, selection = editor.selectedRange()
        for width: CGFloat in [520, 960, 350, 820] {
            window.setContentSize(CGSize(width: width, height: 420))
            await Task.yield()
            host.layoutSubtreeIfNeeded()
            #expect(descendants(host).compactMap { $0 as? NSTextView }.first === editor)
            #expect(editor.string == text && editor.selectedRange() == selection && editor.hasMarkedText())
        }
        editor.unmarkText()
        editor.didChangeText()
        let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: image)
        try save(image, name: "composer-native.png")
        #expect(fixture.model.settings.agents == 8 && fixture.model.settings.synthesisAgents == 3)
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func nativeOptionSelectionsApplyInPlaceWithoutGrowingOrReplacingTheConfigurationSurface() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.prompt = "Retained draft"
        let root = VStack {
            Spacer()
            Color.clear.frame(height: 28)
                .background(ExecutionConfigurationAnchor(isPresented: .constant(true), preferredWidth: 368) { width in
                    ExecutionConfigurationPopover(width: width).environmentObject(model)
                        .environment(\.appTextSize, .standard).environment(\.appLanguage, .chinese)
                })
            Color.clear.frame(height: 100)
        }
        let host = NSHostingView(rootView: root.environment(\.colorScheme, .dark))
        let window = makeWindow(size: CGSize(width: 760, height: 480), scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(80))
        let container = try #require(host.superview)
        let surface = try #require(container.subviews.compactMap { $0 as? NSScrollView }.first)
        let original = surface.frame
        let visibleWindows = NSApplication.shared.windows.filter(\.isVisible).map(ObjectIdentifier.init)
        let candidate = try optionButton("candidates.fast", in: surface)
        let synthesis = try optionButton("synthesis.fast", in: surface)
        let point = candidate.convert(NSPoint(x: candidate.bounds.midX, y: candidate.bounds.midY), to: container)
        let hit = try #require(container.hitTest(point))
        #expect(hit === candidate || hit.isDescendant(of: candidate), "The complete value must open its native menu.")
        for (button, value) in [(candidate, "off"), (synthesis, "on"), (candidate, "automatic")] {
            try selectOption(button, value: value)
            try await Task.sleep(for: .milliseconds(40))
            surface.documentView?.layoutSubtreeIfNeeded()
            #expect(surface.frame == original)
            #expect(container.subviews.compactMap { $0 as? NSScrollView }.count == 1)
            #expect(NSApplication.shared.windows.filter(\.isVisible).map(ObjectIdentifier.init) == visibleWindows)
            #expect(window.childWindows?.isEmpty ?? true)
        }
        #expect(model.settings.fast == .automatic && model.settings.synthesisFast == .on)
        try selectOption(try optionButton("candidates.effort", in: surface), value: "")
        try selectOption(try optionButton("synthesis.effort", in: surface), value: "")
        try await Task.sleep(for: .milliseconds(40))
        #expect(model.settings.effort.isEmpty && model.settings.synthesisEffort.isEmpty)
        try selectOption(try optionButton("candidates.model", in: surface), value: "")
        try selectOption(try optionButton("synthesis.model", in: surface), value: "")
        try await Task.sleep(for: .milliseconds(60))
        #expect(model.settings.model.isEmpty && model.settings.synthesisModel.isEmpty)
        #expect(surface.frame == original)
        #expect(model.prompt == "Retained draft" && model.pendingPromptSubmission == nil)
        #expect(model.promptOutboxByWorkspace.isEmpty && model.followUpQueue.isEmpty)
        #expect(fixture.store.loadSnapshot().settingsByWorkspace.values.first?.fast == .automatic)
        #expect(!descendants(surface).contains { ($0 as? NSTextField)?.isEditable == true })
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func nativeOptionMenusRetireOnNavigationAndRejectSelectionsAfterSynthesisLocks() async throws {
        _ = NSApplication.shared
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.activeRun = try run(locked: false)
        let host = NSHostingView(rootView: ExecutionConfigurationPopover().environmentObject(model)
            .environment(\.appTextSize, .standard).environment(\.appLanguage, .chinese))
        let window = makeWindow(size: host.fittingSize, scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let candidate = try optionButton("candidates.fast", in: host)
        let synthesis = try optionButton("synthesis.fast", in: host)
        let candidateMenu = try #require(candidate.menu)
        let original = model.settings
        model.activeRun = try run(locked: true)
        try await Task.sleep(for: .milliseconds(40))
        host.layoutSubtreeIfNeeded()
        #expect(candidate.isEnabled && !synthesis.isEnabled)
        let lockedModel = try optionButton("synthesis.model", in: host)
        #expect(!lockedModel.isEnabled && lockedModel.selectedItem?.representedObject as? String == "gpt-6-astra")
        try selectOption(synthesis, value: "on")
        try await Task.sleep(for: .milliseconds(30))
        #expect(model.settings == original)
        model.selectedWorkspaceID = "another-workspace"
        try await Task.sleep(for: .milliseconds(60))
        host.layoutSubtreeIfNeeded()
        #expect(!candidate.isDescendant(of: host) && candidate.target == nil && candidate.action == nil)
        if let index = candidateMenu.items.firstIndex(where: { $0.representedObject as? String == "off" }) {
            candidateMenu.performActionForItem(at: index)
        }
        try await Task.sleep(for: .milliseconds(30))
        #expect(model.settings == original && model.promptOutboxByWorkspace.isEmpty)
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func nativeOptionMenuKeepsCompleteTitlesAndDispatchesOnlyTheChosenValue() throws {
        _ = NSApplication.shared
        var selections: [String] = []
        let names = ["GPT Candidate Complete Model Name With Extended Reasoning Support", "GPT-6 Astra"]
        let choices = names.map { CodexOption(label: $0, value: $0, selected: false) }
        let host = NSHostingView(rootView: ExecutionOptionMenu(title: "Model", choices: choices,
            selection: names[0], select: { selections.append($0) }) {
                Text(names[0]).fixedSize(horizontal: false, vertical: true)
                    .appScaledSystemFont(size: 15).frame(width: 164)
            })
        let window = makeWindow(size: host.fittingSize, scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let button = try #require(descendants(host).compactMap { $0 as? NSPopUpButton }.first)
        #expect(button.isAccessibilityElement() && button.accessibilityLabel() == "Model")
        #expect(button.itemArray.map(\.title) == names)
        #expect(button.alignmentRect(forFrame: button.frame).width <= 164,
                "Long menu items must not enlarge the control beyond the label's native alignment rectangle.")
        #expect(button.title == names[0] && selections.isEmpty)
        #expect(host.fittingSize.height > 40, "The visible model label wraps instead of native Menu truncation.")
        try selectOption(button, value: names[1])
        #expect(selections == [names[1]])
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func nativeMenuKeepsDistinctWireValuesEvenWhenCatalogLabelsMatch() throws {
        _ = NSApplication.shared
        var selections: [String] = []
        let host = NSHostingView(rootView: ExecutionOptionMenu(title: "Model", choices: [
            CodexOption(label: "Same model label", value: "model-a", selected: false),
            CodexOption(label: "Same model label", value: "model-b", selected: false),
        ], selection: "model-a", select: { selections.append($0) }) { Text("Same model label") })
        let window = makeWindow(size: host.fittingSize, scheme: .dark)
        defer { window.close() }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let button = try #require(descendants(host).compactMap { $0 as? NSPopUpButton }.first)
        #expect(button.itemArray.map(\.title) == ["Same model label", "Same model label"])
        try selectOption(button, value: "model-b")
        #expect(selections == ["model-b"])
    }

    private func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }

    private func countButtons(in view: NSView) -> [NSPopUpButton] {
        descendants(view).compactMap { $0 as? NSPopUpButton }.filter {
            $0.accessibilityLabel()?.hasSuffix("数量") == true || $0.accessibilityLabel()?.hasSuffix("Count") == true
        }
    }

    private func optionButton(_ field: String, in view: NSView) throws -> NSPopUpButton {
        try #require(descendants(view).compactMap { $0 as? NSPopUpButton }
            .first { $0.identifier?.rawValue == "configuration.\(field)" })
    }

    private func selectOption(_ button: NSPopUpButton, value: String) throws {
        let index = try #require(button.itemArray.firstIndex { $0.representedObject as? String == value })
        button.selectItem(at: index)
        #expect(button.sendAction(try #require(button.action), to: button.target))
    }

    private func selectCountMenu(_ button: NSPopUpButton, title: String) throws {
        let menu = try #require(button.menu)
        menu.delegate?.menuNeedsUpdate?(menu)
        menu.update()
        let index = try #require(menu.items.firstIndex { $0.title == title })
        button.selectItem(at: index)
        #expect(button.sendAction(try #require(button.action), to: button.target))
    }

    private func inkBandsInFirstStage(_ image: NSBitmapImageRep) -> Int {
        let background = image.colorAt(x: 0, y: 0)!.usingColorSpace(.sRGB)!
        var bands = 0, previous = -10
        for y in 0..<(image.pixelsHigh / 2) {
            let pixels = stride(from: image.pixelsWide / 8, to: image.pixelsWide, by: 2).filter { x in
                guard let color = image.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { return false }
                return max(abs(color.redComponent - background.redComponent),
                    abs(color.greenComponent - background.greenComponent), abs(color.blueComponent - background.blueComponent)) > 0.22
            }
            if pixels.count >= 3 {
                if y - previous > 3 { bands += 1 }
                previous = y
            }
        }
        return bands
    }

    private func save(_ image: NSBitmapImageRep, name: String) throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_EXECUTION_SURFACE_EVIDENCE"] else { return }
        let directory = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try #require(image.representation(using: .png, properties: [:])).write(to: directory.appendingPathComponent(name))
    }

    private func makeWindow(size: CGSize, scheme: ColorScheme) -> NSWindow {
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
        return window
    }

    private func run(locked: Bool) throws -> RunSummary {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
            "run_id": "qa-run", "status": "running", "workspace": "/tmp/qa", "prompt": "Fixture",
            "created_at": "2026-10-17T00:00:00Z", "updated_at": "2026-10-17T00:00:00Z",
            "config": ["synthesis_agents": 2, "synthesis_model": "gpt-6-astra", "synthesis_effort": "high", "synthesis_fast": false],
            "synced_back": false, "workspaces_deleted": false, "codex_homes_deleted": false,
            "worker_active": true, "worker_available": true, "event_count": 0,
            "can_update_synthesis": !locked, "agents": [],
        ]))
    }

    @MainActor private final class Fixture {
        let directory: URL
        let store: DesktopStateStore
        let model: AppModel
        init() throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("execution-config-\(UUID().uuidString)")
            store = DesktopStateStore(directory: directory)
            model = AppModel(client: PcrdClient(socketURL: directory.appendingPathComponent("absent.sock"),
                tokenURL: directory.appendingPathComponent("absent.token")), windowID: UUID(), desktopStateStore: store)
            model.selectedWorkspaceID = "qa"
            model.settings.agents = 8
            model.settings.synthesisAgents = 3
            model.settings.model = "gpt-6.1-sol"
            model.settings.effort = "max"
            model.settings.fast = .on
            model.settings.synthesisModel = "gpt-6-astra"
            model.settings.synthesisEffort = "ultra"
            model.settings.synthesisFast = .automatic
        }
        func close() {
            model.stopWindowObservation()
            model.synthesisSettingsTasks.values.forEach { $0.cancel() }
            model.settingsPersistenceTasks.values.forEach { $0.cancel() }
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
