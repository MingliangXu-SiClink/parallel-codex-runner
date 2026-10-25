import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkLogUXTests {
    @Test
    func composerUsesOneActionForTheCurrentDraftAndRunState() {
        let idle = ComposerPrimaryAction.make(hasDraft: false, canSend: false,
            runIsStoppable: true, canStop: true, submissionInFlight: false)
        #expect(idle.kind == .stop)
        #expect(idle.enabled)
        let draft = ComposerPrimaryAction.make(hasDraft: true, canSend: true,
            runIsStoppable: true, queuesMessage: true, canStop: true, submissionInFlight: false)
        #expect(draft.kind == .queue)
        #expect(draft.enabled)
        let blockedQueue = ComposerPrimaryAction.make(hasDraft: true, canSend: false,
            runIsStoppable: true, queuesMessage: true, canStop: true, submissionInFlight: false)
        #expect(blockedQueue.kind == .queue && !blockedQueue.enabled,
            "A retained draft must never turn a disabled queue action into a stop action.")
        let empty = ComposerPrimaryAction.make(hasDraft: false, canSend: false,
            runIsStoppable: false, canStop: false, submissionInFlight: false)
        #expect(empty.kind == .send && !empty.enabled)
        let blocked = ComposerPrimaryAction.make(hasDraft: true, canSend: false,
            runIsStoppable: false, canStop: false, submissionInFlight: false)
        #expect(blocked.kind == .send)
        #expect(!blocked.enabled)
        let sending = ComposerPrimaryAction.make(hasDraft: false, canSend: false,
            runIsStoppable: true, canStop: true, submissionInFlight: true)
        #expect(sending.kind == .sending)
        #expect(!sending.enabled)
    }

    @Test
    func summariesDescribeWorkWithoutChangingForEveryInternalEvent() {
        let read = operation(.fileRead)
        let command = operation(.command)
        let blocks = [read, command]
        func summary(_ blocks: [TranscriptBlock]) -> TranscriptBlock? {
            TranscriptFormatter.blocksWithWorkDisclosure(blocks, runID: "run", agent: 1,
                expanded: false, language: .chinese).first
        }
        #expect(summary(blocks)?.text == "读取文件、运行命令")
        #expect(summary(blocks) == summary(blocks + [TranscriptBlock(id: "thought", kind: .thought, text: "Checking")]))
        #expect(!WorkOperationPresentation.summary(read.operation!, language: .chinese).contains("秒"))
        #expect(WorkOperationPresentation.icon(.fileRead) == "book")
    }

    @Test
    func commandRowsStayOneLineAndExposeTheFullCommand() throws {
        let block = operation(.command, command: String(repeating: "python source/analysis.py --validate ", count: 30))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 400),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        for appearance in [TranscriptAppearance.dark, .light] {
            for size in AppTextSize.allCases {
                for width: CGFloat in [320, 900] {
                    item.configure(block: block, language: .chinese, textSize: size, accentColor: .orange,
                        appearance: appearance, findQuery: "", currentFindOccurrence: nil,
                        availableWidth: width, openURL: { _ in }, activateBlock: { _ in })
                    item.view.frame = NSRect(x: 0, y: 0, width: width, height:
                        TranscriptCollectionItem.preferredHeight(for: block, language: .chinese,
                            textSize: size, accentColor: .orange, appearance: appearance, availableWidth: width))
                    item.view.layoutSubtreeIfNeeded()
                    let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                    #expect(text.textContainer?.maximumNumberOfLines == 1)
                    #expect(text.toolTip == block.operation?.command)
                    #expect(item.view.frame.height < 42 * size.transcriptScale)
                    let arrows = item.view.subviews.compactMap { $0 as? NSImageView }.filter { !$0.isHidden }
                    #expect(arrows.count == 2)
                    #expect(arrows.allSatisfy { $0.frame.maxX <= width })
                }
            }
        }
    }

    @Test
    func workSummaryUsesEnoughWidthForItsCompleteLabel() throws {
        let block = TranscriptBlock(id: "summary", kind: .activity,
            text: "读取文件、检索项目、编辑文件、运行命令", activityType: "work_summary")
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 120),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        for size in AppTextSize.allCases {
            item.configure(block: block, language: .chinese, textSize: size, accentColor: .orange,
                appearance: .dark, findQuery: "", currentFindOccurrence: nil,
                availableWidth: 1400, openURL: { _ in }, activateBlock: { _ in })
            item.view.frame = NSRect(x: 0, y: 0, width: 1400, height: 80)
            item.view.layoutSubtreeIfNeeded()
            let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
            let layout = try #require(text.layoutManager)
            let container = try #require(text.textContainer)
            layout.ensureLayout(for: container)
            #expect(layout.truncatedGlyphRange(inLineFragmentForGlyphAt: 0).location == NSNotFound)
        }
    }

    @Test
    func cachedOperationRevalidationDoesNotChangeReadableRows() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "work-log-ux"
        model.activeRun = try run(status: "completed")
        model.selectedAgent = 1
        let block = operation(.command)
        let key = try #require(model.selectedAgentWorkContextKey) + "|" + block.id
        model.workProcess.expanded.insert(key)
        model.workProcess.details[key] = WorkOperationDetailPage(text: "Existing output", nextCursor: 15,
            hasMore: false, totalBytes: 15, availability: "available")
        let before = model.projectWorkOperations([block])
        model.workProcess.loading.insert(key)
        #expect(model.projectWorkOperations([block]) == before)
        #expect(before.contains { $0.text.contains("0.02 s") })
    }

    @Test
    func preparatoryAndSynthesisEventsHaveOneStableHeaderStatus() throws {
        let workspace = try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"work-log-ux","name":"QA","path":"/qa","lastRunId":"run","lastResumeSessionId":"",
         "selected":true,"available":true,"readable":true,"writable":true}
        """.utf8))
        for phase in [ConversationRunPhase.preparing, .primary(completed: 0, total: 3),
                      .primary(completed: 2, total: 3), .synthesis(completed: 0, total: 1), .finishing] {
            let state = ConversationSurfaceState.make(workspace: workspace, conversation: nil,
                composingNewConversation: false, run: try run(status: "running"), selectedAgent: nil,
                isLatestTurn: true, loading: false, presentationOverride:
                    ConversationRunPresentation(phase: phase, label: "Internal stage", systemImage: "clock",
                                                tone: .neutral, animated: true))
            #expect(state.toolbarStatusLabel(language: .chinese) == "正在回复")
        }
    }

    @Test
    func runningInTheSourceWorkspaceIsNotAnUnavailableWarning() throws {
        let run = try run(status: "running")
        let agent = try #require(run.agents.first)
        #expect(conversationAgentGlyphStatus(run: run, agent: agent) == "running")
    }

    @Test
    func legacyCommandsKeepTheirOriginalTextAvailableWithoutRequestingMissingOutput() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "work-log-ux"
        model.activeRun = try run(status: "completed")
        model.selectedAgent = 1
        let original = TranscriptBlock(id: "old-command", kind: .command,
            text: "python test.py\n--verbose", status: "completed")
        let collapsed = try #require(model.projectWorkOperations([original]).first)
        #expect(collapsed.text.contains("python test.py --verbose"))
        #expect(collapsed.status == nil)
        #expect(collapsed.activityType == "legacy_command")
        let key = try #require(model.selectedAgentWorkContextKey) + "|" + original.id
        model.workProcess.expanded.insert(key)
        let expanded = model.projectWorkOperations([original])
        #expect(expanded.last?.text == original.text)
        #expect(expanded.last?.activityType == "operation_details")
        #expect(model.workProcess.loading.isEmpty)
    }

    private func operation(_ kind: WorkOperationKind, command: String = "python test.py") -> TranscriptBlock {
        let value = WorkOperationRecord(id: kind.rawValue, eventId: 1, kind: kind, status: .completed,
            command: command, targets: [WorkOperationTarget(path: "src/test.py")], durationMs: 20,
            detailsAvailable: true)
        return TranscriptBlock(id: kind.rawValue, kind: .command, text: command, operation: value)
    }

    private func run(status: String) throws -> RunSummary {
        try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"run","status":"\(status)","workspace":"/qa","prompt":"QA",
         "createdAt":"2026-09-22","updatedAt":"2026-09-22","config":{},"syncedBack":false,
         "workspacesDeleted":false,"codexHomesDeleted":false,"workerActive":true,
         "workerAvailable":true,"eventCount":1,"agents":[
          {"agent":1,"name":"AGENT-001","status":"running","recommended":false,"finalized":false,
           "rejected":false,"workspaceAvailable":false,"reasoningTokenCounts":{}}]}
        """.utf8))
    }

    private func descendants(_ view: NSView) -> [NSView] {
        view.subviews.flatMap { [$0] + descendants($0) }
    }
}
