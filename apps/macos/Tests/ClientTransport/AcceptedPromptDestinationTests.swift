import Foundation
import Testing
@testable import PCRRemoteMac

struct AcceptedPromptDestinationTests {
    @Test(arguments: [false, true])
    func aSessionStartMayHaveNoPCRParentOrACanonicalAdoptedParent(hasParent: Bool) throws {
        let result = accepted(session: "saved-session")
        let run = try destination(result, hasParent: hasParent)
        #expect(acceptedPromptDestinationConflict(run, result: result, workspacePath: "/qa/") == nil)
    }

    @Test(arguments: ["saved-session", "resume:saved-session", "older-turn-navigation-key"])
    func aNavigationAliasMayResolveToItsCanonicalPCRConversation(_ alias: String) throws {
        let result = accepted(session: "saved-session", conversation: alias)
        let run = try destination(result, hasParent: true)
        #expect(run.conversationId == "saved-conversation")
        #expect(acceptedPromptDestinationConflict(run, result: result, workspacePath: "/qa") == nil)
    }

    @Test(arguments: ["", "NO", "  NO\n"])
    func aNewConversationCannotUnexpectedlyResumeAParent(_ noSession: String) throws {
        let result = accepted(session: noSession)
        let fresh = try destination(result)
        #expect(acceptedPromptDestinationConflict(fresh, result: result, workspacePath: "/qa") == nil)
        let inherited = try destination(result, hasParent: true)
        #expect(acceptedPromptDestinationConflict(inherited, result: result, workspacePath: "/qa") == "unexpected_source")
        let resumed = try changing(fresh, config: ["resumeSessionId": "different-session"])
        #expect(acceptedPromptDestinationConflict(resumed, result: result, workspacePath: "/qa") == "resume_session")
    }

    @Test(arguments: [false, true])
    func explicitContinuationStillRequiresTheExactSourceRunAndAgent(wrongAgent: Bool) throws {
        let result = accepted(session: "saved-session", sourceRun: "saved-parent", sourceAgent: 7)
        let run = try destination(result, hasParent: true)
        #expect(acceptedPromptDestinationConflict(run, result: result, workspacePath: "/qa") == nil)
        let other = try changing(run, fields: wrongAgent
            ? ["continuedFromAgent": 8] : ["continuedFromRunId": "another-parent"])
        #expect(acceptedPromptDestinationConflict(other, result: result, workspacePath: "/qa")
            == (wrongAgent ? "source_agent" : "source_run"))
    }

    @Test(arguments: [false, true])
    func aPartialExplicitSourceCannotFallBackToSessionMatching(missingRun: Bool) throws {
        let result = accepted(session: "saved-session", sourceRun: missingRun ? nil : "saved-parent",
            sourceAgent: missingRun ? 7 : nil)
        #expect(acceptedPromptDestinationConflict(try destination(result, hasParent: true),
            result: result, workspacePath: "/qa") == "incomplete_source")
    }

    @Test func aStaleOrMissingSessionCannotBeProvedByTheCanonicalParentAlone() throws {
        let result = accepted(session: "saved-session")
        let run = try destination(result, hasParent: true)
        for session in [NSNull(), "different-session"] as [Any] {
            let changed = try changing(run, config: ["resumeSessionId": session])
            #expect(acceptedPromptDestinationConflict(changed, result: result, workspacePath: "/qa") == "resume_session")
        }
        let partial = try changing(run, fields: ["continuedFromAgent": NSNull()])
        #expect(acceptedPromptDestinationConflict(partial, result: result, workspacePath: "/qa") == "session_parent")
        let invalid = accepted(session: "saved-session", recordedSession: "different-session")
        #expect(acceptedPromptDestinationConflict(run, result: invalid, workspacePath: "/qa") == "request_resume_session")
    }

    @Test func aMatchingSessionCannotAuthorizeAnotherCommandOrWorkspace() throws {
        let result = accepted(session: "saved-session")
        let run = try destination(result, hasParent: true)
        let conflicts: [([String: Any], String)] = [
            (["runId": "another-run"], "run_id"),
            (["prompt": "another-question"], "prompt"),
            (["workspace": "/qa/other"], "workspace_path")
        ]
        for (fields, reason) in conflicts {
            #expect(acceptedPromptDestinationConflict(try changing(run, fields: fields), result: result,
                workspacePath: "/qa") == reason)
        }
        for command in [["clientId": "other-client", "commandId": result.commandID, "fingerprint": "fp"],
                        ["clientId": "mac-app", "commandId": "other-command", "fingerprint": "fp"]] {
            #expect(acceptedPromptDestinationConflict(try changing(run, config: ["submissionCommand": command]),
                result: result, workspacePath: "/qa") == "command")
        }
        let response = RunStartResponse(status: "started", message: nil, runId: run.runId,
            storage: nil, run: run, workspaceId: "another-workspace", workspaceRevision: nil, continuedFrom: nil)
        #expect(acceptedPromptDestinationConflict(run, result: result, workspacePath: "/qa", response: response) == "workspace_id")
        var snapshotRun = run
        snapshotRun.conversationSnapshot = ConversationSnapshot(workspace: WorkspaceRecord(workspaceId: "another-workspace"))
        #expect(acceptedPromptDestinationConflict(snapshotRun, result: result, workspacePath: "/qa") == "workspace_id")
        let snapshotResponse = RunStartResponse(status: "started", message: nil, runId: run.runId,
            storage: nil, run: run, workspaceId: "workspace", workspaceRevision: nil, continuedFrom: nil,
            conversationSnapshot: snapshotRun.conversationSnapshot)
        #expect(acceptedPromptDestinationConflict(run, result: result, workspacePath: "/qa", response: snapshotResponse) == "workspace_id")
    }

    @Test func capturedProductionHandoffMatchesTheOriginalReceiptWhenProvided() throws {
        guard let path = ProcessInfo.processInfo.environment["PCR_ACCEPTED_HANDOFF_FIXTURE"] else { return }
        let json = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let result = try JSONDecoder().decode(PersistedAcceptedPromptResult.self,
            from: JSONSerialization.data(withJSONObject: try #require(json["accepted"])))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let run = try decoder.decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: try #require(json["run"])))
        let response = try decoder.decode(RunStartResponse.self,
            from: JSONSerialization.data(withJSONObject: try #require(json["receipt_result"])))
        #expect(run.continuedFromRunId != nil && run.continuedFromAgent != nil)
        #expect(result.submission.sourceRunID == nil && result.submission.sourceAgent == nil)
        #expect(response.runId == result.runID && response.workspaceId == result.submission.workspaceID)
        #expect(run.config.submissionCommand?.commandId == result.commandID)
        #expect(acceptedPromptDestinationConflict(run, result: result,
            workspacePath: result.submission.workspacePath, response: response) == nil)
        let wrongSession = try changing(run, config: ["resumeSessionId": "another-session"])
        #expect(acceptedPromptDestinationConflict(wrongSession, result: result,
            workspacePath: result.submission.workspacePath) == "resume_session")
    }

    private func accepted(session: String, conversation: String = "saved-conversation",
                          sourceRun: String? = nil, sourceAgent: Int? = nil,
                          recordedSession: String? = nil) -> PersistedAcceptedPromptResult {
        var settings = RunSettings()
        settings.resumeSessionID = session
        return PersistedAcceptedPromptResult(submission: PersistedPromptSubmission(id: "original-message",
            machineID: "local", workspaceID: "workspace", prompt: "original-question", settings: settings,
            sourceRunID: sourceRun, sourceAgent: sourceAgent, resumeSessionID: recordedSession ?? session,
            confirmLargeRun: false, attempt: 0, status: .opening, error: "", conversationID: conversation,
            workspacePath: "/qa"), runID: "accepted", continuedSessionID: "", workspaceRevision: nil)
    }

    private func destination(_ result: PersistedAcceptedPromptResult, hasParent: Bool = false) throws -> RunSummary {
        let base = try JSONDecoder().decode(RunSummary.self, from: Data("""
            {"runId":"accepted","prompt":"original-question","workspace":"/qa","status":"completed",
             "conversationId":"saved-conversation","config":{},"createdAt":"2026-10-15T00:00:00Z",
             "updatedAt":"2026-10-15T00:00:00Z","syncedBack":false,"workspacesDeleted":false,
             "codexHomesDeleted":false,"workerActive":false,"workerAvailable":false,"eventCount":0,"agents":[]}
            """.utf8))
        return try changing(base, fields: hasParent ? ["continuedFromRunId": "saved-parent", "continuedFromAgent": 7] : [:],
            config: ["resumeSessionId": usableResumeSessionID(result.submission.settings.resumeSessionID) as Any? ?? NSNull(),
                     "submissionCommand": ["clientId": "mac-app", "commandId": result.commandID, "fingerprint": "fp"]])
    }

    private func changing(_ run: RunSummary, fields: [String: Any] = [:], config: [String: Any] = [:]) throws -> RunSummary {
        var json = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(run)) as? [String: Any])
        var configuration = try #require(json["config"] as? [String: Any])
        configuration.merge(config) { _, new in new }
        json["config"] = configuration
        json.merge(fields) { _, new in new }
        return try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: json))
    }
}
