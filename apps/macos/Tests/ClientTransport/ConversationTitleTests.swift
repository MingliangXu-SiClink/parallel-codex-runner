import AppKit
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ConversationTitleTests {
    @Test(arguments: [CGFloat(540), 920, 1500], [CGFloat(24), 144])
    func longTitlesLeaveTheHeaderActionsInsideTheWindow(width: CGFloat, leadingInset: CGFloat) async throws {
        _ = NSApplication.shared
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.workspaces = [try JSONDecoder().decode(RemoteWorkspace.self, from: Data("""
        {"workspaceId":"workspace","name":"QA","path":"/qa","lastRunId":"run","lastResumeSessionId":"",
         "selected":true,"available":true,"readable":true,"writable":true}
        """.utf8))]
        model.selectedWorkspaceID = "workspace"
        var conversation = try conversation()
        conversation.title = String(repeating: "Long conversation title ", count: 8)
        model.workspaceConversations = [conversation]
        model.selectedConversationID = conversation.id
        model.activeRun = conversation.latestRun
        let action = NSView()
        let header = ConversationHeader(leadingInset: leadingInset) {
            HeaderActionProbe(view: action).frame(width: 32, height: 32)
        }.environmentObject(model)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 52),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let host = NSHostingView(rootView: header)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        await Task.yield()
        host.layoutSubtreeIfNeeded()
        let frame = action.convert(action.bounds, to: host)
        #expect(abs(frame.width - 32) < 1)
        #expect(frame.maxX <= width - 23)
        #expect(frame.minX >= leadingInset)
        #expect(frame.minY >= 0 && frame.maxY <= 53)
    }

    @Test
    func olderDaemonRecordsRemainDecodable() throws {
        let record = try JSONDecoder().decode(ConversationRecord.self, from: Data("""
        {"conversationId":"chat","workspaceId":"workspace","title":"Original",
         "createdAt":"2026-09-19T00:00:00Z","updatedAt":"2026-09-19T00:00:00Z","turnIds":[]}
        """.utf8))
        #expect(record.customTitle == nil)
        #expect(record.titleRevision == nil)
        #expect(ConversationTitles.normalized("  PCR\n  开发\t ") == "PCR 开发")
    }

    @Test
    func renamedTitlesPersistWithoutChangingTheOriginalPrompt() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        let conversation = try conversation()
        let machine = model.currentMachineContextID
        model.selectedWorkspaceID = "workspace"
        model.workspaceConversations = [conversation]
        model.selectedConversationID = conversation.id
        let renamed = record(title: "PCR 开发", revision: 2)
        model.ingestConversationTitles([renamed], machineID: machine)
        model.ingestConversationTitles([record(title: "Old name", revision: 1)], machineID: machine)
        model.applyVisibleCustomConversationTitles(machineID: machine, workspaceID: "workspace")
        #expect(model.selectedConversation?.title == "PCR 开发")
        #expect(model.selectedConversation?.latestRun?.prompt == "Original prompt")
        #expect(model.applyingCachedHistoryTitles(to: [conversation], workspaceID: "workspace", machineID: machine).first?.title == "PCR 开发")
        #expect(model.customConversationTitle(conversation, workspaceID: "other", machineID: machine) == nil)
        #expect(model.customConversationTitle(conversation, workspaceID: "workspace", machineID: "other") == nil)

        var persisted = PersistedDesktopState()
        persisted.conversationTitlesByContext = model.domainRepositories.conversations.titlesByContext
        let restored = try JSONDecoder().decode(PersistedDesktopState.self, from: JSONEncoder().encode(persisted))
        #expect(restored.conversationTitlesByContext == persisted.conversationTitlesByContext)
        #expect(try JSONDecoder().decode(PersistedDesktopState.self, from: Data("{}".utf8)).conversationTitlesByContext.isEmpty)
    }

    @Test
    func canonicalTitleWinsOverAnOlderImportedSessionAlias() throws {
        let conversation = try conversation()
        let alias = ConversationRecord(conversationId: "resume:session", workspaceId: "workspace",
                                       title: "Imported", customTitle: "Imported", titleRevision: 99,
                                       turnIds: ["turn-run"])
        #expect(ConversationTitles.matching(conversation, records: [alias, record(title: "Canonical", revision: 1)])?.customTitle == "Canonical")
        let legacy = WorkspaceConversationSummary(id: "legacy", title: "Old", runs: conversation.runs, resumeSession: nil)
        #expect(ConversationTitles.matching(legacy, records: [alias])?.customTitle == "Imported")
    }

    @Test
    func renameSheetCapturesItsOriginalConversationAndWorkspace() throws {
        let model = AppModel(windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "workspace"
        model.requestConversationRename(try conversation())
        let request = try #require(model.conversationRenameRequest)
        model.selectedWorkspaceID = "another-workspace"
        #expect(request.workspaceID == "workspace")
        #expect(request.conversation.id == "chat")
        #expect(request.machineID == model.currentMachineContextID)
    }

    private func record(title: String, revision: Int) -> ConversationRecord {
        ConversationRecord(conversationId: "chat", workspaceId: "workspace", title: title,
                           customTitle: title, titleRevision: revision, turnIds: ["turn-run"])
    }

    private func conversation() throws -> WorkspaceConversationSummary {
        let run = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"run","conversationId":"chat","status":"completed","workspace":"/qa",
         "prompt":"Original prompt","createdAt":"2026-09-19T03:17:00Z","updatedAt":"2026-09-19T03:22:00Z",
         "config":{},"syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
         "workerActive":false,"workerAvailable":true,"eventCount":0,"agents":[]}
        """.utf8))
        return WorkspaceConversationSummary(id: "chat", title: "Original prompt", runs: [run], resumeSession: nil)
    }
}

private struct HeaderActionProbe: NSViewRepresentable {
    let view: NSView
    func makeNSView(context: Context) -> NSView { view }
    func updateNSView(_ view: NSView, context: Context) {}
}
