import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkspaceRemovalTests {
    @Test(arguments: ["run_not_found", "unknown_run", "pcr_error"], [true, false])
    func missingHistoricalRunDoesNotPreventRemovingBookmark(code: String, selected: Bool) async throws {
        let fixture = try Fixture(lookup: [
            "ok": false,
            "error": ["code": code, "message": "Unknown PCR run: missing-run"],
        ])
        defer { fixture.close() }
        if !selected {
            fixture.model.selectedWorkspaceID = fixture.other.workspaceId
            fixture.model.prompt = "Keep this other workspace draft"
        }

        await fixture.model.removeWorkspace(fixture.workspace.workspaceId)

        let requests = await fixture.server.value
        #expect(requests.contains("workspaces.remove"))
        #expect(!requests.contains("runs.discard"))
        // The mutation succeeded, but the following refresh deliberately fails.
        #expect(!fixture.model.workspaces.contains { $0.workspaceId == fixture.workspace.workspaceId })
        #expect(fixture.model.selectedWorkspaceID == fixture.other.workspaceId)
        if !selected { #expect(fixture.model.prompt == "Keep this other workspace draft") }
    }

    @Test(arguments: [
        "Permission denied", "Run state is unreadable", "Unknown PCR run: another-run",
    ])
    func otherLookupFailuresStayProtectedAndVisibleForUnselectedWorkspace(message: String) async throws {
        let fixture = try Fixture(lookup: [
            "ok": false, "error": ["code": "pcr_error", "message": message],
        ])
        defer { fixture.close() }
        fixture.model.selectedWorkspaceID = fixture.other.workspaceId

        await fixture.model.removeWorkspace(fixture.workspace.workspaceId, discardReviewableResults: true)

        let requests = await fixture.server.value
        #expect(!requests.contains("workspaces.remove"))
        #expect(!requests.contains("runs.discard"))
        #expect(fixture.model.workspaces.contains { $0.workspaceId == fixture.workspace.workspaceId })
        #expect(fixture.model.errorMessage?.contains(fixture.workspace.name) == true)
        #expect(fixture.model.errorMessage?.contains(fixture.model.appLanguage.localizedClientMessage(message)) == true)
    }

    @Test(arguments: ["running", "sync_ambiguous", "completed"])
    func liveRunProtectionRemainsAuthoritative(status: String) async throws {
        let fixture = try Fixture(lookup: ["ok": true, "body": [
            "run_id": "missing-run", "status": status, "workspace": "/qa", "prompt": "Question",
            "created_at": "2026-09-12T00:00:00Z", "updated_at": "2026-09-12T00:00:00Z", "config": [:],
            "synced_back": false, "workspaces_deleted": false, "codex_homes_deleted": false,
            "worker_active": status == "running", "worker_available": true, "event_count": 1,
            "agents": [["agent": 1, "name": "Agent 1", "status": "success", "can_accept": true,
                        "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false]],
        ]])
        defer { fixture.close() }
        fixture.model.selectedWorkspaceID = fixture.other.workspaceId

        await fixture.model.removeWorkspace(fixture.workspace.workspaceId)

        let requests = await fixture.server.value
        #expect(!requests.contains("workspaces.remove"))
        #expect(!requests.contains("runs.discard"))
        #expect(fixture.model.workspaces.contains { $0.workspaceId == fixture.workspace.workspaceId })
        #expect(fixture.model.errorMessage?.contains(fixture.workspace.name) == true)
        let expected = status == "running" ? "Stop this workspace's running task"
            : status == "sync_ambiguous" ? "Confirm the workspace sync status" : "Confirm that unaccepted Agent results"
        #expect(fixture.model.errorMessage?.contains(expected) == true)
    }

    @Test
    func serverRejectionDoesNotRemoveLocalWorkspace() async throws {
        let fixture = try Fixture(
            lookup: ["ok": false, "error": ["code": "run_not_found", "message": "Missing run"]],
            removal: ["ok": false, "error": ["code": "workspace_busy", "message": "A task just started"]]
        )
        defer { fixture.close() }
        fixture.model.selectedWorkspaceID = fixture.other.workspaceId
        fixture.model.prompt = "Keep this draft"

        await fixture.model.removeWorkspace(fixture.workspace.workspaceId)

        #expect(await fixture.server.value.contains("workspaces.remove"))
        #expect(fixture.model.workspaces.contains { $0.workspaceId == fixture.workspace.workspaceId })
        #expect(fixture.model.selectedWorkspaceID == fixture.other.workspaceId)
        #expect(fixture.model.prompt == "Keep this draft")
        #expect(fixture.model.errorMessage?.contains(fixture.workspace.name) == true)
    }

    @Test
    func removingLastWorkspaceClearsSelectionDespiteRefreshFailure() async throws {
        let fixture = try Fixture(lookup: [
            "ok": false, "error": ["code": "unknown_run", "message": "Missing run"],
        ])
        defer { fixture.close() }
        fixture.model.workspaces = [fixture.workspace]
        fixture.model.prompt = "Draft in the removed workspace"
        fixture.model.selectedWorkspaceByMachine[fixture.model.currentMachineContextID] = fixture.workspace.workspaceId

        await fixture.model.removeWorkspace(fixture.workspace.workspaceId)

        #expect(await fixture.server.value.contains("workspaces.remove"))
        #expect(fixture.model.workspaces.isEmpty)
        #expect(fixture.model.selectedWorkspaceID == nil)
        #expect(fixture.model.selectedWorkspaceByMachine[fixture.model.currentMachineContextID] == nil)
        #expect(fixture.model.prompt.isEmpty)
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let listener: Int32
        let model: AppModel
        let workspace: RemoteWorkspace
        let other: RemoteWorkspace
        let server: Task<[String], Never>
        let windowID = UUID()

        init(lookup: [String: Any], removal: [String: Any]? = nil) throws {
            directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("pcr-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("test-token".utf8).write(to: tokenURL)
            let listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            self.listener = listener
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let bytes = Array(socketURL.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { target in
                bytes.withUnsafeBytes { target.copyBytes(from: $0) }
            }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            #expect(bound == 0)
            #expect(Darwin.listen(listener, 4) == 0)
            let suffix = UUID().uuidString
            workspace = try Self.workspace(id: "remove-\(suffix)", name: "Removal Target", runID: "missing-run")
            other = try Self.workspace(id: "keep-\(suffix)", name: "Other Workspace", runID: "")
            let lookupReply = try JSONSerialization.data(withJSONObject: lookup) + Data([10])
            let removeReply = try JSONSerialization.data(withJSONObject: removal ?? ["ok": true, "body": [
                "revision": 2, "removed_workspace_id": workspace.workspaceId,
                "selected_workspace_id": other.workspaceId,
            ]]) + Data([10])
            let refreshReply = Data("{\"ok\":false,\"error\":{\"code\":\"server_not_configured\",\"message\":\"Refresh unavailable\"}}\n".utf8)
            let descriptor = listener
            server = Task.detached {
                var actions: [String] = []
                while true {
                    var pending = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                    guard Darwin.poll(&pending, 1, actions.isEmpty ? 3_000 : 500) > 0 else { break }
                    let client = Darwin.accept(descriptor, nil, nil)
                    guard client >= 0 else { break }
                    defer { Darwin.close(client) }
                    var timeout = timeval(tv_sec: 2, tv_usec: 0)
                    _ = withUnsafePointer(to: &timeout) {
                        Darwin.setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
                    }
                    var buffer = [UInt8](repeating: 0, count: 8192)
                    let count = Darwin.read(client, &buffer, buffer.count)
                    guard count > 0,
                          let request = try? JSONSerialization.jsonObject(with: Data(buffer.prefix(count))) as? [String: Any],
                          let action = request["action"] as? String else { break }
                    actions.append(action)
                    let reply = action == "turns.get" ? lookupReply
                        : action == "workspaces.remove" ? removeReply : refreshReply
                    _ = reply.withUnsafeBytes { Darwin.write(client, $0.baseAddress, $0.count) }
                }
                return actions
            }
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: windowID)
            model.appLanguage = .english
            model.workspaces = [workspace, other]
            model.selectedWorkspaceID = workspace.workspaceId
        }

        func close() {
            model.stopWindowObservation()
            if model.desktopStateStore.flushPendingWrites(),
               let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
                try? FileManager.default.removeItem(at: support.appendingPathComponent(
                    "Parallel Codex/window-\(windowID.uuidString).v3.json"
                ))
            }
            Darwin.close(listener)
            try? FileManager.default.removeItem(at: directory)
        }

        private static func workspace(id: String, name: String, runID: String) throws -> RemoteWorkspace {
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": id, "path": "/qa/\(id)", "name": name,
                "lastRunId": runID, "lastResumeSessionId": "", "selected": false,
                "available": true, "readable": true, "writable": true,
            ]))
        }
    }
}
