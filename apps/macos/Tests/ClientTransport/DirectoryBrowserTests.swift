import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct DirectoryBrowserTests {
    @Test
    func pathsResolveAgainstTheSelectedMachineNotTheLocalHome() {
        #expect(resolvedDirectoryBrowserPath("~/work", current: "/remote/other", home: "/remote/home") == "/remote/home/work")
        #expect(resolvedDirectoryBrowserPath("~", current: nil, home: "/remote/home") == "/remote/home")
        #expect(resolvedDirectoryBrowserPath(" child folder ", current: "/remote/work", home: nil) == "/remote/work/child folder")
        #expect(resolvedDirectoryBrowserPath("child", current: "/", home: nil) == "/child")
        #expect(resolvedDirectoryBrowserPath(" /absolute/path ", current: nil, home: nil) == "/absolute/path")
        #expect(resolvedDirectoryBrowserPath(" ", current: "/remote", home: nil) == nil)
        #expect(resolvedDirectoryBrowserPath("~/work", current: "/remote", home: nil) == nil)
    }

    @Test
    func failedNavigationKeepsTheLastReadableFolderAndBlocksAccidentalAdding() async {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let windowID = UUID()
        let model = AppModel(client: PcrdClient(
            socketURL: directory.appendingPathComponent("missing-socket"),
            tokenURL: directory.appendingPathComponent("missing-token")
        ), windowID: windowID)
        defer {
            model.stopWindowObservation()
            if model.desktopStateStore.flushPendingWrites(),
               let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent(
                    "Parallel Codex/window-\(windowID.uuidString).v3.json"
                ))
            }
        }
        model.browserPresented = true
        model.browserPage = DirectoryPage(
            name: "Readable folder", path: "/previous", parent: "/", readable: true,
            writable: true, isGitRepository: false, cursor: 0, nextCursor: 0,
            hasMore: false, total: 0, entries: []
        )

        await model.navigateDirectory("/unavailable")

        #expect(model.browserPage?.path == "/previous")
        #expect(model.browserError != nil)
        #expect(!model.browserLoading)
        let navigationError = model.browserError
        await model.addCurrentDirectory()
        #expect(model.browserError == navigationError)
        #expect(!model.browserAdding)
        #expect(model.browserPresented)
    }

    @Test(arguments: [false, true])
    func addedWorkspaceUsesTheSameIsolatedSelectionAsTheSidebar(alreadyRegistered: Bool) async throws {
        let windowID = UUID()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(windowID.uuidString)
        let model = AppModel(client: PcrdClient(
            socketURL: directory.appendingPathComponent("missing-socket"),
            tokenURL: directory.appendingPathComponent("missing-token")
        ), windowID: windowID)
        defer {
            model.stopWindowObservation()
            if model.desktopStateStore.flushPendingWrites(),
               let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent(
                    "Parallel Codex/window-\(windowID.uuidString).v3.json"
                ))
            }
        }
        func workspace(_ suffix: String) throws -> RemoteWorkspace {
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "\(windowID)-\(suffix)", "path": "/qa/\(suffix)", "name": suffix,
                "lastRunId": "", "lastResumeSessionId": "", "selected": false,
                "available": true, "readable": true, "writable": true,
            ]))
        }
        let old = try workspace("old")
        let added = try workspace("added")
        model.workspaces = alreadyRegistered ? [old, added] : [old]
        model.selectedWorkspaceID = old.workspaceId
        let session = ResumeSession(sessionId: "old-session", title: "Old conversation", cwd: old.path,
            updatedAt: 2, createdAt: 1, source: "cli", model: "", rolloutPath: "", preview: "", tokensUsed: nil)
        model.workspaceConversations = [WorkspaceConversationSummary(id: "old-conversation",
            title: "Old conversation", runs: [], resumeSession: session)]
        model.selectedConversationID = "old-conversation"
        model.settings.resumeSessionID = session.sessionId
        model.preparePromptHistoryContext()
        model.updatePrompt("Keep this old draft")
        let draftContext = model.promptDraftContext
        model.resumeHistory = [ResumeHistoryEntry(category: "output", text: "Old workspace answer")]
        let generation = model.workspaceContextGeneration

        await model.selectAddedWorkspace(added)

        #expect(model.workspaces.count == 2)
        #expect(model.selectedWorkspaceID == added.workspaceId)
        #expect(model.workspaceContextGeneration > generation)
        #expect(model.workspaceConversations.isEmpty)
        #expect(model.selectedConversationID == nil)
        #expect(model.resumeHistory.isEmpty)
        #expect(model.prompt.isEmpty)
        #expect(model.promptDraftsByContext[draftContext] == "Keep this old draft")
        #expect(model.settings.resumeSessionID != session.sessionId)
        await model.workspaceContextTask?.value
        #expect(model.connectionErrorMessage != nil)
    }
}
