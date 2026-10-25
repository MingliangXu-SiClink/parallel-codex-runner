import AppKit
import Testing
@testable import PCRRemoteMac

struct ReviewWorkspaceTests {
    @Test
    func explicitLocalFilesDoNotRequireWorkspaceOrRunValidation() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("外部 notes.md")
        try Data("# Outside workspace\n[Next](other.md)".utf8).write(to: file)
        for target in [PcrdRequestTarget.local, .localMachine("this-mac")] {
            let item = try #require(try localFileLinkItem(file.path + ":12:3", target: target))
            #expect(item.path == file.path)
            #expect(item.relativePath == file.path)
            #expect(item.scope == "local")
            #expect(item.line == 12)
            #expect(item.kind == "file")
            #expect(item.sizeBytes > 0)
            #expect(resolveWorkspaceLink("other.md", relativeTo: item.relativePath) ==
                directory.appendingPathComponent("other.md").path)
            let urlItem = try #require(try localFileLinkItem(file.absoluteString + "#L7", target: target))
            #expect(urlItem.path == file.path)
            #expect(urlItem.line == 7)
        }
        let folder = try #require(try localFileLinkItem(directory.path, target: .local))
        #expect(folder.kind == "directory")
        let remote = RelayMachine(deviceId: "remote", kind: "machine", name: "Remote",
            publicKey: "", status: "online", online: true, lastSeenAt: nil)
        #expect(try localFileLinkItem(file.path, target: .relay(remote)) == nil)
        #expect(try localFileLinkItem("relative.md", target: .local) == nil)
        #expect(try localFileLinkItem("https://example.com", target: .local) == nil)
    }

    @Test
    func localFileLinksPreserveLiteralNamesAndAllowSymlinkTargets() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        for name in ["notes#section.md", "notes:42"] {
            let file = directory.appendingPathComponent(name)
            try Data("literal filename".utf8).write(to: file)
            let item = try #require(try localFileLinkItem(file.absoluteString, target: .local))
            #expect(item.path == file.path)
            #expect(item.line == nil)
        }
        let link = directory.appendingPathComponent("linked.md")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: directory.appendingPathComponent("notes:42"))
        #expect(try localFileLinkItem(link.path, target: .local)?.kind == "file")
        #expect(try localFileLinkItem("file://localhost" + link.path, target: .local)?.kind == "file")
        #expect(throws: (any Error).self) { try localFileLinkItem("file://another-mac/etc/hosts", target: .local) }
        #expect(throws: (any Error).self) { try localFileLinkItem(directory.appendingPathComponent("missing.md").path, target: .local) }
        #expect(throws: (any Error).self) { try localFileLinkItem("/dev/null", target: .local) }
        #expect(workspaceLinkKind("~/.codex/config.toml") == .workspaceFile)
        #expect(resolveWorkspaceLink("~/.codex/config.toml", relativeTo: "some/doc.md") == "~/.codex/config.toml")
    }

    @Test
    func fileLinkOriginsSurviveContinuationAndDoNotMergeDifferentCandidates() {
        let first = AgentTranscript(sourceRunID: "first", sourceAgent: 2,
            finalMessage: "[File](result.md)")
        let history = branchConversationHistory(history: [], prompt: "First", transcript: first)
        let blocks = TranscriptFormatter.blocks(history: history, prompt: "Next",
            transcript: AgentTranscript(sourceRunID: "second", sourceAgent: 9, finalMessage: "[Other](result.md)"))
        #expect(blocks.first { $0.isHistorical && $0.kind == .output }?.linkOrigin ==
                TranscriptLinkOrigin(runID: "first", agent: 2))
        #expect(blocks.last?.linkOrigin == TranscriptLinkOrigin(runID: "second", agent: 9))
        let answers = blocks.filter { $0.kind == .output }.map { block in
            var value = block
            value.isHistorical = true
            return value
        }
        #expect(TranscriptFormatter.coalescedVisibleConversation(answers).count == 2)
    }

    @Test
    func remoteResourcePathsAreNeverOpenedOnThisMac() {
        let item = WorkspaceItem(name: "outputs", path: "/tmp/outputs", relativePath: "outputs",
            kind: "directory", scope: "source", contentType: "application/octet-stream",
            sizeBytes: 0, line: nil, entries: [], nextOffset: 0, hasMore: false)
        let remote = RelayMachine(deviceId: "remote", kind: "machine", name: "Remote",
            publicKey: "", status: "online", online: true, lastSeenAt: nil)
        #expect(item.localURL(for: .local)?.path == "/tmp/outputs")
        #expect(item.localURL(for: .localMachine("this-mac"))?.path == "/tmp/outputs")
        #expect(item.localURL(for: .relay(remote)) == nil)
    }

    @Test
    func openingOneFileWaitsForItsEntirePatchInsteadOfShowingTheFirstFile() {
        let first = "diff --git \"a/one.txt\" \"b/one.txt\"\n--- a/one.txt\n+++ b/one.txt\n@@ -1 +1 @@\n-old\n+new\n"
        let second = "diff --git \"a/two words.txt\" \"b/two words.txt\"\n--- a/two words.txt\n+++ b/two words.txt\n@@ -1 +1 @@\n-before\n+after\n"
        #expect(ReviewFile.needsMore(patch: first, requestedPath: "two words.txt", hasMore: true))
        #expect(ReviewFile.needsMore(patch: first + second, requestedPath: "two words.txt", hasMore: true))
        #expect(!ReviewFile.needsMore(patch: first + second, requestedPath: "one.txt", hasMore: true))
        #expect(!ReviewFile.needsMore(patch: first + second, requestedPath: "two words.txt", hasMore: false))
        #expect(!ReviewFile.needsMore(patch: first, requestedPath: nil, hasMore: true))
        #expect(!ReviewFile.needsMore(patch: first, requestedPath: "missing.txt", hasMore: false))
        let deleted = "diff --git \"a/deleted.txt\" \"b/deleted.txt\"\n--- a/deleted.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        #expect(ReviewFile.parse(deleted).first?.path == "deleted.txt")
    }

    @Test
    @MainActor
    func previewResizeKeepsLongLinesReachableAndFillsTheViewport() {
        let scroll = ReviewScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 400))
        let editor = ReviewNSTextView(frame: .zero)
        editor.isHorizontallyResizable = true
        editor.isVerticallyResizable = true
        editor.textContainer?.widthTracksTextView = false
        editor.textContainer?.containerSize = NSSize(width: 1_000_000, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainerInset = NSSize(width: 16, height: 14)
        editor.font = .monospacedSystemFont(ofSize: 14, weight: .regular)
        editor.string = String(repeating: "long line ", count: 100)
        scroll.documentView = editor
        scroll.resizeDocumentToFitContent()
        let fullLineWidth = editor.frame.width
        #expect(fullLineWidth > 640)
        scroll.setFrameSize(NSSize(width: 320, height: 300))
        scroll.layout()
        #expect(editor.frame.width == fullLineWidth)
        #expect(editor.frame.height >= scroll.contentSize.height)
        editor.string = "short"
        scroll.resizeDocumentToFitContent()
        #expect(editor.frame.width == scroll.contentSize.width)
        scroll.setFrameSize(NSSize(width: 800, height: 600))
        scroll.layout()
        #expect(editor.frame.width == scroll.contentSize.width)
        #expect(editor.frame.height == scroll.contentSize.height)
    }

    @Test
    @MainActor
    func readingPositionSurvivesHighlightingWithDifferentLineMetrics() throws {
        let scroll = ReviewScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 400))
        let editor = ReviewNSTextView(frame: .zero)
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = true
        editor.textContainer?.widthTracksTextView = false
        editor.textContainer?.containerSize = NSSize(width: 1_000_000, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainerInset = NSSize(width: 16, height: 14)
        editor.string = (0..<1200).map { "print(\"line \($0)\")" }.joined(separator: "\n")
        editor.font = .monospacedSystemFont(ofSize: 14, weight: .regular)
        scroll.documentView = editor
        scroll.resizeDocumentToFitContent()
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 8000))
        editor.setSelectedRange(NSRange(location: 34, length: 8))
        let original = try #require(scroll.readingPosition())
        let anchor = try #require(original.anchor)
        #expect(anchor > 0)

        editor.font = .monospacedSystemFont(ofSize: 19, weight: .regular)
        scroll.resizeDocumentToFitContent()
        scroll.restoreReadingPosition(original)
        let restored = try #require(scroll.readingPosition())
        #expect(restored.anchor == anchor)
        #expect(abs(restored.lineOffset / restored.lineHeight - original.lineOffset / original.lineHeight) < 0.01)
        #expect(restored.origin.y > original.origin.y)
        #expect(restored.selection == original.selection)

        editor.font = .monospacedSystemFont(ofSize: 14, weight: .regular)
        scroll.resizeDocumentToFitContent()
        scroll.restoreReadingPosition(restored)
        #expect(abs(scroll.contentView.bounds.origin.y - original.origin.y) < 1)

        scroll.contentView.scroll(to: NSPoint(x: 0, y: editor.frame.height - scroll.contentSize.height))
        let bottom = try #require(scroll.readingPosition())
        #expect(bottom.atBottom)
        editor.font = .monospacedSystemFont(ofSize: 18, weight: .regular)
        scroll.resizeDocumentToFitContent()
        scroll.restoreReadingPosition(bottom)
        #expect(scroll.readingPosition()?.atBottom == true)
        scroll.setFrameSize(NSSize(width: 520, height: 500))
        scroll.layoutSubtreeIfNeeded()
        #expect(scroll.readingPosition()?.atBottom == true)
    }

    @Test
    @MainActor
    func cacheCleanupPreservesFilesUsedByOtherWindows() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let preview = directory.appendingPathComponent("preview.pdf")
        let partial = directory.appendingPathComponent("download.part")
        let expired = directory.appendingPathComponent("expired.txt")
        for url in [preview, partial, expired] {
            try Data("test".utf8).write(to: url)
            try FileManager.default.setAttributes([.modificationDate: Date.distantPast], ofItemAtPath: url.path)
        }
        let previewLease = WorkspaceFileLease(preview)
        let downloadLease = WorkspaceFileLease(partial)
        withExtendedLifetime([previewLease, downloadLease]) {
            cleanWorkspaceFileCache(directory)
            #expect(FileManager.default.fileExists(atPath: preview.path))
            #expect(FileManager.default.fileExists(atPath: partial.path))
            #expect(!FileManager.default.fileExists(atPath: expired.path))
        }
    }

    @Test
    func diffFilesKeepNamesAndHunkContentSeparate() {
        let patch = """
        M  one.txt
        A  two.txt

        2 files changed

        diff --git "a/one.txt" "b/one.txt"
        --- a/one.txt
        +++ b/one.txt
        @@ -1,2 +1,2 @@
        --- looks like a header
        +++ but is file content
         unchanged
        diff --git "a/two.txt" "b/two.txt"
        new file mode 100644
        --- /dev/null
        +++ b/two.txt
        @@ -0,0 +1 @@
        +new
        """
        let files = ReviewFile.parse(patch)
        #expect(files.map(\.path) == ["one.txt", "two.txt"])
        #expect(files[0].text.contains("+++ but is file content"))
        #expect(!files[0].text.contains("new file mode"))
        let legacy = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n\n--- a/b.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-c\n"
        #expect(ReviewFile.parse(legacy).map(\.path) == ["a.txt", "b.txt"])
    }

    @Test
    func previewTypesDoNotExecuteDocuments() {
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.swift"), contentType: "application/octet-stream") == .text)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.html"), contentType: "text/html") == .text)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.pdf"), contentType: "application/pdf") == .visual)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.png"), contentType: "image/png") == .visual)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.docx"), contentType: "application/octet-stream") == .visual)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.svg"), contentType: "image/svg+xml") == .visual)
        #expect(WorkspacePreviewKind.resolve(URL(fileURLWithPath: "/tmp/file.rtf"), contentType: "text/rtf") == .quickLook)
    }

    @Test
    @MainActor
    func resultInspectorPreferenceSurvivesTurnsAndRelaunchWithoutAffectingOtherWindows() {
        let windowID = UUID()
        let first = AppModel(windowID: windowID)
        let other = AppModel(windowID: UUID())
        defer {
            UserDefaults.standard.removeObject(forKey: first.resultsInspectorVisibilityKey)
            UserDefaults.standard.removeObject(forKey: other.resultsInspectorVisibilityKey)
        }
        #expect(!first.resultsInspectorVisible)
        first.resultsInspectorVisible = true
        first.activeRun = nil
        first.selectedWorkspaceID = "another-workspace"
        #expect(first.resultsInspectorVisible)
        #expect(!other.resultsInspectorVisible)
        let restored = AppModel(windowID: windowID)
        #expect(restored.resultsInspectorVisible)
        restored.resultsInspectorVisible = false
        #expect(!AppModel(windowID: windowID).resultsInspectorVisible)
        #expect(first.resultsInspectorVisible)
    }

    @Test
    @MainActor
    func independentWindowsDoNotShareNavigationOrDrafts() {
        let left = AppModel(windowID: UUID())
        let right = AppModel(windowID: UUID())
        left.selectedWorkspaceID = "left"
        right.selectedWorkspaceID = "right"
        left.prompt = "left draft"
        right.prompt = "right draft"
        #expect(left.prompt == "left draft")
        #expect(right.prompt == "right draft")
        #expect(left.selectedWorkspaceID != right.selectedWorkspaceID)
        #expect(left.domainRepositories !== right.domainRepositories)
        #expect(left.desktopStateStore !== right.desktopStateStore)
    }
}
