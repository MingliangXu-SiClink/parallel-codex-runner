import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct TranscriptJumpOverlayTests {
    @Test(arguments: [420.0, 980.0], [AppTextSize.standard, .extraLarge])
    func jumpControlOverlaysTheUnchangedNativeViewport(width: CGFloat, size: AppTextSize) async throws {
        for scheme in [ColorScheme.dark, .light] {
            try await verifyOverlay(width: width, size: size, scheme: scheme)
        }
    }

    private func verifyOverlay(width: CGFloat, size: AppTextSize, scheme: ColorScheme) async throws {
        let fixture = try Fixture(width: width, size: size, scheme: scheme)
        defer { fixture.close() }
        await fixture.settle()
        let scroll = try #require(fixture.scroll)
        let initial = scroll.convert(scroll.bounds, to: fixture.host)
        #expect(abs(initial.height - fixture.host.bounds.height) < 1,
            "The jump control must not reserve a separate row below the native transcript.")

        for x in [CGFloat(70), width - 70] {
            let point = NSPoint(x: x, y: fixture.host.isFlipped ? fixture.host.bounds.maxY - 24 : 24)
            let hit = try #require(fixture.host.hitTest(fixture.host.convert(point, to: fixture.host.superview)))
            #expect(hit === scroll || hit.isDescendant(of: scroll),
                "The bottom area outside the circular control must still reach the transcript, not a full-width lane.")
        }
        try fixture.capture("reading")
        for _ in 0..<3 {
            scroll.onUserScroll?()
            scroll.contentView.scroll(to: NSPoint(x: 0, y: fixture.maximumY))
            scroll.reflectScrolledClipView(scroll.contentView)
            await fixture.settle()
            #expect(fixture.scroll === scroll)
            #expect(scroll.convert(scroll.bounds, to: fixture.host) == initial)
            #expect(fixture.model.transcriptScrollMemory.snapshot(for: fixture.context)?.followsLatest == true)
            scroll.onUserScroll?()
            scroll.contentView.scroll(to: NSPoint(x: 0, y: 280))
            scroll.reflectScrolledClipView(scroll.contentView)
            await fixture.settle()
            #expect(scroll.convert(scroll.bounds, to: fixture.host) == initial)
            #expect(abs(scroll.contentView.bounds.minY - 280) < 1)
            #expect(fixture.model.transcriptScrollMemory.snapshot(for: fixture.context)?.followsLatest == false)
        }
        #expect(!fixture.window.isVisible && !fixture.window.isKeyWindow)
        #expect(fixture.model.selectedAgent == 1 && fixture.model.activeRun?.runId == "overlay-fixture")
    }

    @MainActor private final class Fixture {
        let directory: URL
        let windowID = UUID()
        let model: AppModel
        let context: String
        let host: NSView
        let window: NSWindow
        let size: AppTextSize
        let width: CGFloat
        let scheme: ColorScheme

        init(width: CGFloat, size: AppTextSize, scheme: ColorScheme) throws {
            self.size = size; self.width = width; self.scheme = scheme
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-overlay-\(UUID().uuidString)")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let client = PcrdClient(socketURL: directory.appendingPathComponent("unused.sock"),
                                    tokenURL: directory.appendingPathComponent("unused.token"))
            model = AppModel(client: client, windowID: windowID,
                desktopStateStore: DesktopStateStore(directory: directory.appendingPathComponent("desktop")),
                runViewStateStore: WorkspaceRunViewStateStore(diskCache: WorkspaceRunDiskCache(directory: directory.appendingPathComponent("cache"))),
                questionAnswers: AgentAnswerOutbox(file: AgentAnswerFile(url: directory.appendingPathComponent("answers.json"))))
            model.appTextSize = size
            model.appLanguage = .english
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            let run = try decoder.decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "run_id": "overlay-fixture", "status": "completed", "workspace": directory.path,
                "prompt": "Question for the overlay fixture", "created_at": "2026-10-20T00:00:00Z",
                "updated_at": "2026-10-20T00:00:00Z", "config": [:], "synced_back": false,
                "workspaces_deleted": false, "codex_homes_deleted": false,
                "worker_active": false, "worker_available": true, "event_count": 0, "agents": []]))
            let workspace = try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "overlay-workspace", "name": "Overlay fixture", "path": directory.path,
                "lastRunId": run.runId, "lastResumeSessionId": "", "selected": true,
                "available": true, "readable": true, "writable": true]))
            model.workspaces = [workspace]
            model.selectedWorkspaceID = workspace.workspaceId
            model.selectedConversationID = "overlay-conversation"
            model.activeRun = run; model.selectedAgent = 1
            model.resumeHistory = [.init(category: "user", text: "Earlier fixture question"),
                                   .init(category: "output", text: "Earlier fixture reply")]
            model.transcripts[1] = AgentTranscript(sourceRunID: run.runId, sourceAgent: 1,
                finalMessage: (0..<160).map { "Transcript line \($0): the conversation continues behind the floating control." }.joined(separator: "\n\n"),
                finalMessageHasMore: false, finalMessageReadStatus: "success")
            context = transcriptScrollContextID(machineID: model.currentMachineContextID,
                workspaceID: workspace.workspaceId, conversationID: "overlay-conversation", runID: run.runId, agentID: "1")
            model.transcriptScrollMemory.save(.init(offsetY: 280, followsLatest: false), for: context)
            host = NSHostingView(rootView: AgentTranscriptView(questionAnswers: model.questionAnswers, workspace: workspace,
                resultsInspectorVisible: .constant(false), findPresented: .constant(false),
                findCommandRequest: .idle, inlineAgentSwitchPosition: .constant(.missing))
                .environmentObject(model).environment(\.appLanguage, .english).environment(\.appTextSize, size)
                .preferredColorScheme(scheme))
            host.frame = NSRect(x: 0, y: 0, width: width, height: 520)
            window = NSWindow(contentRect: host.frame, styleMask: .borderless, backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
        }

        var scroll: ConversationTimelineScrollView? {
            descendants(host).compactMap { $0 as? ConversationTimelineScrollView }.first
        }
        var maximumY: CGFloat { max(0, (scroll?.documentView?.frame.height ?? 0) - (scroll?.contentSize.height ?? 0)) }
        func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
        func settle() async {
            for _ in 0..<5 { host.layoutSubtreeIfNeeded(); try? await Task.sleep(for: .milliseconds(25)) }
        }
        func capture(_ suffix: String) throws {
            guard let output = ProcessInfo.processInfo.environment["PCR_OVERLAY_QA_OUTPUT"] else { return }
            host.displayIfNeeded()
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            try #require(bitmap.representation(using: .png, properties: [:])).write(to:
                URL(fileURLWithPath: output).appendingPathComponent("jump-\(Int(width))-\(size.rawValue)-\(scheme == .dark ? "dark" : "light")-\(suffix).png"))
        }
        func close() {
            model.stopWindowObservation()
            model.transcriptScrollMemory.freezeForTermination()
            window.close()
            UserDefaults.standard.removeObject(forKey: "pcr.transcript-scroll-memory.v1.\(windowID.uuidString)")
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
