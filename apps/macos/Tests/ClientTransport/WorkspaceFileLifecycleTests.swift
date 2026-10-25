import Combine
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceFileLifecycleTests {
    private func model(in directory: URL) -> AppModel {
        let client = PcrdClient(socketURL: directory.appendingPathComponent("missing-socket"),
                               tokenURL: directory.appendingPathComponent("missing-token"))
        let model = AppModel(client: client, windowID: UUID())
        model.selectedWorkspaceID = "file-lifecycle-workspace"
        return model
    }

    @Test func repeatedRetryRetainsItsPanelAndErrorUntilACompleteResultArrives() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-retry-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = model(in: directory)
        defer { model.stopWindowObservation() }
        let file = directory.appendingPathComponent("eventually.md")
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        #expect(model.reviewColumnPresented && model.workspaceFileIssue != nil)
        for _ in 0..<3 {
            let issue = try #require(model.workspaceFileIssue)
            issue.retry?()
            #expect(model.workspaceFileLoading && model.reviewColumnPresented)
            #expect(model.workspaceFileIssue?.message == issue.message)
            await model.workspaceFileTask?.value
            #expect(model.reviewColumnPresented && !model.workspaceFileLoading)
        }
        try Data("# RECOVERED".utf8).write(to: file)
        var visible: [Bool] = []
        let observation = model.objectWillChange.sink { visible.append(model.reviewColumnPresented) }
        defer { observation.cancel() }
        model.workspaceFileIssue?.retry?()
        await model.workspaceFileTask?.value
        #expect(!visible.isEmpty && visible.allSatisfy { $0 })
        #expect(model.workspaceDocument?.content == "# RECOVERED")
        #expect(model.workspaceFileIssue == nil && model.reviewColumnPresented)
        model.dismissWorkspaceDocument()
        #expect(!model.reviewColumnPresented)
    }

    @Test func closingARetryCancelsItAndTheLateResultCannotReopenThePanel() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-close-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = model(in: directory)
        defer { model.stopWindowObservation() }
        let file = directory.appendingPathComponent("late.txt")
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        try Data("Must not reopen after closing".utf8).write(to: file)
        model.workspaceFileIssue?.retry?()
        let pending = try #require(model.workspaceFileTask)
        model.dismissWorkspaceFileIssue()
        await pending.value
        #expect(!model.workspaceFileLoading && !model.reviewColumnPresented)
        #expect(model.workspaceDocument == nil && model.workspaceFileIssue == nil)
    }

    @Test func sameFileReloadKeepsItsIdentityButNewWorkspaceDoesNotReuseIt() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-identity-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = model(in: directory)
        defer { model.stopWindowObservation() }
        let file = directory.appendingPathComponent("same.txt")
        try Data("Original".utf8).write(to: file)
        model.diffPresented = true
        var visible: [Bool] = []
        let observation = model.objectWillChange.sink { visible.append(model.reviewColumnPresented) }
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        observation.cancel()
        #expect(visible.allSatisfy { $0 })
        #expect(!model.diffPresented && model.reviewColumnPresented)
        let identity = try #require(model.workspaceDocument?.id)
        try Data("Updated".utf8).write(to: file)
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        #expect(model.workspaceDocument?.id == identity)
        #expect(model.workspaceDocument?.content == "Updated")
        model.selectedWorkspaceID = "another-workspace"
        model.openTranscriptLink(file)
        await model.workspaceFileTask?.value
        #expect(model.workspaceDocument?.id != identity)
    }

    @Test func closingAnIssueKeepsTheReadableFileAndStopsThePendingReplacement() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-retain-\(UUID())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let model = model(in: directory)
        defer { model.stopWindowObservation() }
        let oldFile = directory.appendingPathComponent("old.txt")
        let nextFile = directory.appendingPathComponent("next.txt")
        try Data("Keep this readable file".utf8).write(to: oldFile)
        model.openTranscriptLink(oldFile)
        await model.workspaceFileTask?.value
        let identity = model.workspaceDocument?.id
        model.openTranscriptLink(nextFile)
        await model.workspaceFileTask?.value
        try Data("Do not replace it after the issue is dismissed".utf8).write(to: nextFile)
        model.workspaceFileIssue?.retry?()
        let pending = try #require(model.workspaceFileTask)
        model.dismissWorkspaceFileIssue()
        await pending.value
        #expect(model.workspaceDocument?.id == identity)
        #expect(model.reviewColumnPresented && model.workspaceFileIssue == nil)
        model.resetRunView()
        #expect(!model.reviewColumnPresented && model.workspaceDocument == nil)
    }
}
