import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

private struct TransportReply: Decodable, Sendable {
    let value: String
}

private func unixAddress(_ path: String) -> sockaddr_un {
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8CString)
    withUnsafeMutableBytes(of: &address.sun_path) { target in
        bytes.withUnsafeBytes { target.copyBytes(from: $0) }
    }
    return address
}

private func connect(_ descriptor: Int32, path: String) -> Int32 {
    var address = unixAddress(path)
    return withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
}

struct ClientTransportTests {
    @Test func aPeerClosingDuringRequestWriteReturnsAnErrorWithoutTerminatingTheClient() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-pipe-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socketURL = directory.appendingPathComponent("s")
        let tokenURL = directory.appendingPathComponent("t")
        try Data("test-token".utf8).write(to: tokenURL)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        try #require(listener >= 0)
        defer { Darwin.close(listener) }
        var address = unixAddress(socketURL.path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0 && Darwin.listen(listener, 1) == 0)
        let server = Task {
            try await ClientBlockingIO.transport(priority: .utility, isObservation: false) {
                var pending = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
                guard Darwin.poll(&pending, 1, 5_000) > 0 else { return false }
                let socket = Darwin.accept(listener, nil, nil)
                guard socket >= 0 else { return false }
                defer { Darwin.close(socket) }
                var byte: UInt8 = 0
                guard Darwin.read(socket, &byte, 1) == 1 else { return false }
                Darwin.shutdown(socket, SHUT_RDWR)
                return true
            }
        }
        struct Body: Encodable, Sendable { let padding: String }
        do {
            let _: TransportReply = try await PcrdClient(socketURL: socketURL, tokenURL: tokenURL)
                .callLocal("codex.model_options", body: Body(padding: String(repeating: "x", count: 2 * 1_024 * 1_024)))
            Issue.record("A disconnected peer must not produce a successful response")
        } catch let error as PcrdClientError {
            guard case .connection = error else {
                Issue.record("Expected a connection error, received \(error)")
                return
            }
        }
        #expect(try await server.value)
    }

    @Test @MainActor
    func explicitLocalFileOpensWithMissingRunAndUnavailableDaemon() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-outside-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("outside.md")
        try Data("# Local file outside selected workspace".utf8).write(to: file)
        let client = PcrdClient(socketURL: directory.appendingPathComponent("missing-socket"),
            tokenURL: directory.appendingPathComponent("missing-token"))
        let model = AppModel(client: client, windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "some-other-workspace"
        model.selectedAgent = 9
        model.openTranscriptLink(file, reviewContext: ReviewContext(target: .local,
            workspaceID: "some-other-workspace", runID: "removed-run", agent: 2))
        for _ in 0..<100 where model.workspaceFileTask != nil { try await Task.sleep(for: .milliseconds(10)) }
        #expect(model.workspaceFileIssue == nil)
        #expect(model.workspaceDocument?.content == "# Local file outside selected workspace")
        #expect(model.workspaceDocument?.relativePath == file.path)
        #expect(model.workspaceDocument?.context?.runID == nil)
        #expect(model.workspaceDocument?.context?.agent == nil)
        let retained = model.workspaceDocument?.id
        model.selectedAgent = 1
        #expect(model.workspaceDocument?.id == retained)
        model.openTranscriptLink(directory.appendingPathComponent("missing.md"))
        for _ in 0..<100 where model.workspaceFileTask != nil { try await Task.sleep(for: .milliseconds(10)) }
        #expect(model.workspaceFileIssue != nil)
        #expect(model.workspaceDocument?.id == retained)
    }

    @Test @MainActor
    func fileOpeningUsesTheLinksOriginInsteadOfTheVisibleRunOrAgent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-link-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("answer.md")
        try Data("Original candidate answer".utf8).write(to: file)
        let socketURL = directory.appendingPathComponent("s")
        let tokenURL = directory.appendingPathComponent("t")
        try Data("test-token".utf8).write(to: tokenURL)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        defer { Darwin.close(listener) }
        var address = unixAddress(socketURL.path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0 && Darwin.listen(listener, 1) == 0)
        let server = Task.detached {
            var descriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
            guard Darwin.poll(&descriptor, 1, 5_000) > 0 else { return false }
            let socket = Darwin.accept(listener, nil, nil)
            guard socket >= 0 else { return false }
            defer { Darwin.close(socket) }
            var bytes = [UInt8](repeating: 0, count: 8192)
            let count = Darwin.read(socket, &bytes, bytes.count)
            guard count > 0,
                  let request = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Any],
                  request["action"] as? String == "workspace.item.resolve",
                  let body = request["body"] as? [String: Any],
                  body["run_id"] as? String == "origin-run", body["agent"] as? Int == 2 else { return false }
            let reply = try JSONSerialization.data(withJSONObject: ["ok": true, "body": [
                "name": "answer.md", "path": file.path, "relative_path": "answer.md",
                "kind": "file", "scope": "candidate", "content_type": "text/markdown",
                "size_bytes": 25, "entries": [], "next_offset": 0, "has_more": false,
            ]]) + Data([10])
            try await Task.sleep(for: .milliseconds(150))
            return reply.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) } == reply.count
        }
        let client = PcrdClient(socketURL: socketURL, tokenURL: tokenURL)
        let model = AppModel(client: client, windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "workspace"
        model.selectedAgent = 9
        model.openTranscriptLink(URL(string: "answer.md")!, reviewContext: ReviewContext(
            target: .local, workspaceID: "workspace", runID: "origin-run", agent: 2, conversationID: "old-conversation"))
        try await Task.sleep(for: .milliseconds(50))
        model.selectedAgent = 7
        model.activeRun = nil
        #expect(try await server.value)
        for _ in 0..<100 where model.workspaceFileTask != nil { try await Task.sleep(for: .milliseconds(10)) }
        #expect(model.workspaceDocument?.content == "Original candidate answer")
        #expect(model.workspaceDocument?.context?.runID == "origin-run")
        #expect(model.workspaceDocument?.context?.agent == 2)
        #expect(model.workspaceDocument?.fileURL == file)
        #expect(model.errorMessage == nil)
    }

    @Test @MainActor
    func fileReviewLoadsEveryPageOfTheRequestedFile() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socketURL = directory.appendingPathComponent("s")
        let tokenURL = directory.appendingPathComponent("t")
        try Data("test-token".utf8).write(to: tokenURL)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        defer { Darwin.close(listener) }
        var address = unixAddress(socketURL.path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        try #require(bound == 0 && Darwin.listen(listener, 1) == 0)
        let chunks = [
            "diff --git \"a/first.txt\" \"b/first.txt\"\n--- a/first.txt\n+++ b/first.txt\n@@ -1 +1 @@\n-old\n+new\n",
            "diff --git \"a/last file.txt\" \"b/last file.txt\"\n--- a/last file.txt\n+++ b/last file.txt\n@@ -1 +1 @@\n-before\n",
            "+complete requested change\n",
        ]
        let server = Task.detached {
            var cursor = 0
            for (index, chunk) in chunks.enumerated() {
                var descriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
                guard Darwin.poll(&descriptor, 1, 5_000) > 0 else { return false }
                let socket = Darwin.accept(listener, nil, nil)
                guard socket >= 0 else { return false }
                defer { Darwin.close(socket) }
                var bytes = [UInt8](repeating: 0, count: 8192)
                let count = Darwin.read(socket, &bytes, bytes.count)
                guard count > 0,
                      let request = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Any],
                      request["action"] as? String == "runs.diff",
                      let body = request["body"] as? [String: Any],
                      body["full_context"] as? Bool == true,
                      body["cursor"] as? Int == cursor else { return false }
                let reply = try JSONSerialization.data(withJSONObject: ["ok": true, "body": [
                    "run_id": "qa", "agent": 1, "cursor": cursor, "next_cursor": cursor + chunk.utf8.count,
                    "has_more": index < chunks.count - 1, "total_chars": chunks.joined().utf8.count, "text": chunk,
                ]]) + Data([10])
                cursor += chunk.utf8.count
                guard reply.withUnsafeBytes({ Darwin.write(socket, $0.baseAddress, $0.count) }) == reply.count else { return false }
            }
            return true
        }
        let client = PcrdClient(socketURL: socketURL, tokenURL: tokenURL)
        let context = ReviewContext(target: client.currentTarget(), workspaceID: "qa", runID: "qa", agent: 1)
        let loader = AgentReviewLoader()
        await loader.load(client: client, context: context, mode: .changes, language: .english,
                          cache: AgentReviewCache(), requestedFile: "last file.txt")
        #expect(try await server.value)
        #expect(loader.error == nil && !loader.loading && loader.snapshot?.hasMore == false)
        #expect(loader.snapshot?.completeFiles.map(\.path) == ["first.txt", "last file.txt"])
        #expect(loader.snapshot?.completeFiles.last?.text.contains("+complete requested change") == true)
        #expect(loader.loadedKey?.context == context)
    }

    @Test @MainActor
    func failedDetailRequestDoesNotBecomeSavedAgentOutput() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socketURL = directory.appendingPathComponent("s")
        let tokenURL = directory.appendingPathComponent("t")
        try Data("test-token".utf8).write(to: tokenURL)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        defer { Darwin.close(listener) }
        var address = unixAddress(socketURL.path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        #expect(bound == 0)
        #expect(Darwin.listen(listener, 1) == 0)
        let reply = Data("""
        {"ok":false,"error":{"code":"pcr_error","message":"Unknown PCR run: old-run"}}

        """.utf8)
        let server = Task.detached {
            var descriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
            guard Darwin.poll(&descriptor, 1, 3_000) > 0 else { return false }
            let client = Darwin.accept(listener, nil, nil)
            guard client >= 0 else { return false }
            defer { Darwin.close(client) }
            var timeout = timeval(tv_sec: 2, tv_usec: 0)
            _ = withUnsafePointer(to: &timeout) {
                Darwin.setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
            }
            var bytes = [UInt8](repeating: 0, count: 8192)
            guard Darwin.read(client, &bytes, bytes.count) > 0 else { return false }
            return reply.withUnsafeBytes { Darwin.write(client, $0.baseAddress, $0.count) } == reply.count
        }
        let model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: UUID())
        defer { model.stopWindowObservation() }
        model.selectedWorkspaceID = "workspace"
        model.selectedAgent = 1
        model.activeRun = try JSONDecoder().decode(RunSummary.self, from: Data("""
        {"runId":"old-run","status":"finalized","workspace":"/qa","prompt":"Question",
         "createdAt":"2026-09-12T00:00:00Z","updatedAt":"2026-09-12T00:00:00Z","config":{},
         "syncedBack":true,"workspacesDeleted":true,"codexHomesDeleted":true,
         "workerActive":false,"workerAvailable":false,"eventCount":1,"agents":[]}
        """.utf8))
        let original = AgentTranscript(sourceRunID: "old-run", sourceAgent: 1,
            blocks: [TranscriptBlock(id: "answer", kind: .output, text: "Saved answer")])
        model.transcripts = [1: original]
        let loaded = await model.loadAgentDetail(1, maxPages: 1)
        #expect(await server.value)
        #expect(!loaded)
        #expect(model.transcripts[1] == original)
        #expect(model.errorNotice != nil)
    }

    @Test
    func cachedLookupErrorsAreRemovedWithoutDiscardingAnswersOrRealAgentErrors() {
        let key = WorkspaceRunViewKey(machineID: nil, workspaceID: "workspace", runID: "old-run")
        let answer = TranscriptBlock(id: "answer", kind: .output, text: "Saved answer")
        let cached = AgentTranscript(sourceRunID: "old-run", sourceAgent: 1,
            blocks: [answer], error: "The command failed", artifactError: "Unknown PCR run: old-run")
        let validated = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [1: cached],
            conversationHistory: []).validated(for: key)
        #expect(validated.transcripts[1]?.blocks == [answer])
        #expect(validated.transcripts[1]?.error == "The command failed")
        #expect(validated.transcripts[1]?.artifactError == "")
        var genuineArtifactError = cached
        genuineArtifactError.artifactError = "The final answer file could not be read"
        let genuine = WorkspaceRunViewSnapshot(selectedAgent: 1, transcripts: [1: genuineArtifactError],
            conversationHistory: []).validated(for: key)
        #expect(genuine.transcripts[1] == genuineArtifactError)
    }

    @Test func workspaceFileReplyUsesTheTransportSnakeCaseDecoder() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = Data("""
        {
          "name": "qa-note.txt",
          "relative_path": "folder/qa-note.txt",
          "content_type": "text/plain",
          "size_bytes": 5,
          "file_id": "file-version",
          "offset": 0,
          "next_offset": 5,
          "has_more": false,
          "content_base64": "aGVsbG8=",
          "chunk_sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
          "workspace_id": "workspace",
          "agent": 1
        }
        """.utf8)
        let page = try decoder.decode(WorkspaceFilePage.self, from: response)
        #expect(page.relativePath == "folder/qa-note.txt")
        #expect(page.contentType == "text/plain")
        #expect(page.sizeBytes == 5)
        #expect(page.fileId == "file-version")
        #expect(page.offset == 0 && page.nextOffset == 5 && !page.hasMore)
        #expect(Data(base64Encoded: page.contentBase64) == Data("hello".utf8))
        #expect(page.chunkSha256.count == 64)
        #expect(page.line == nil)
    }

    @Test func occupiedLocalBacklogDoesNotLookLikeAnOfflineServer() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let socketURL = directory.appendingPathComponent("s")
        let tokenURL = directory.appendingPathComponent("t")
        try Data("test-token".utf8).write(to: tokenURL)
        let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        #expect(listener >= 0)
        defer { Darwin.close(listener) }
        var address = unixAddress(socketURL.path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        #expect(bound == 0)
        #expect(Darwin.listen(listener, 1) == 0)
        let blocker = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        defer { Darwin.close(blocker) }
        #expect(connect(blocker, path: socketURL.path) == 0)

        // Darwin refuses connect() while a healthy Unix server's accept queue
        // is full. Keep it full beyond an immediate retry, then drain it.
        let finished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            defer { finished.signal() }
            Thread.sleep(forTimeInterval: 0.15)
            let first = Darwin.accept(listener, nil, nil)
            if first >= 0 { Darwin.close(first) }
            var pollDescriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
            guard Darwin.poll(&pollDescriptor, 1, 2_000) > 0 else { return }
            let client = Darwin.accept(listener, nil, nil)
            guard client >= 0 else { return }
            defer { Darwin.close(client) }
            var timeout = timeval(tv_sec: 2, tv_usec: 0)
            _ = withUnsafePointer(to: &timeout) {
                Darwin.setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
            }
            var bytes = [UInt8](repeating: 0, count: 4096)
            guard Darwin.read(client, &bytes, bytes.count) > 0 else { return }
            let reply = Data("{\"ok\":true,\"body\":{\"value\":\"ready\"}}\n".utf8)
            _ = reply.withUnsafeBytes { Darwin.write(client, $0.baseAddress, $0.count) }
        }
        let result: Result<TransportReply, Error>
        do {
            result = .success(try await PcrdClient(socketURL: socketURL, tokenURL: tokenURL)
                .callLocal("codex.model_options", body: EmptyBody()))
        } catch {
            result = .failure(error)
        }
        let didFinish = await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: finished.wait(timeout: .now() + 3) == .success)
            }
        }
        #expect(didFinish)
        #expect(try result.get().value == "ready")
    }
}
