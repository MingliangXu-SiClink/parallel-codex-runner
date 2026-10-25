import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct AnswerReceiptTests {
    @Test
    func legacyCacheDecodesWithoutInventingATerminalAnswerReceipt() throws {
        let original = AgentTranscript(sourceRunID: "run", sourceAgent: 5,
            cursor: 205, hasMore: false, finalMessageHasMore: false)
        var object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        object.removeValue(forKey: "finalMessageReadStatus")
        let decoded = try JSONDecoder().decode(AgentTranscript.self,
            from: JSONSerialization.data(withJSONObject: object))
        #expect(decoded.finalMessageReadStatus == nil)
        #expect(decoded.cursor == 205 && !decoded.finalMessageHasMore)
    }

    @Test
    func runningEOFDoesNotBecomeATerminalReceiptAtCallbackTime() throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 5)
        let requested = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "", id: "", total: 0),
            requested: requested, requestedStatus: "running"))
        #expect(transcript.finalMessageReadStatus == "running")
        #expect(!isTerminalAgentStatus(transcript.finalMessageReadStatus))
        let terminalRequest = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "", id: "", total: 0),
            requested: terminalRequest, requestedStatus: "success"))
        #expect(transcript.finalMessageReadStatus == "success")
        #expect(!transcript.finalMessageHasMore)
        let saved = try JSONDecoder().decode(AgentTranscript.self, from: JSONEncoder().encode(transcript))
        #expect(saved == transcript)
    }

    @Test(arguments: ["running", "success"])
    func lateEmptyReceiptCannotEraseTheAnswerOrItsUnreadTail(_ status: String) throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 5)
        let requested = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "First", id: "answer", total: 10, more: true),
            requested: requested, requestedStatus: "success"))
        let firstPage = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "", id: "", total: 0),
            requested: firstPage, requestedStatus: status))
        #expect(transcript == firstPage)
        #expect(!transcript.mergeFinalMessage(try detail(text: " Last", id: "answer", total: 10, cursor: 5),
            requested: firstPage, requestedStatus: "success"))
        #expect(transcript.finalMessage == "First Last")
        #expect(transcript.finalMessageCursor == 10 && !transcript.finalMessageHasMore)
        #expect(transcript.finalMessageReadStatus == "success")
    }

    @Test
    func invalidOrUnreadablePageCannotCertifyAnswerCompletion() throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 5)
        let requested = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "", id: "answer", total: 10, more: true),
            requested: requested, requestedStatus: "success"))
        #expect(transcript.finalMessageReadStatus == nil && transcript.finalMessageHasMore)
        #expect(!transcript.mergeFinalMessage(try detail(text: "", id: "", total: 0, error: "Read failed"),
            requested: requested, requestedStatus: "success"))
        #expect(transcript.finalMessageReadStatus == nil && transcript.finalMessageHasMore)
        #expect(transcript.artifactError == "Read failed")
    }

    @Test
    func aReplacedArtifactRestartsFromByteZeroAndKeepsReceiptUnknownUntilRead() throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 5,
            finalMessage: "Old", finalMessageCursor: 3, finalMessageHasMore: true,
            finalMessageID: "old", finalMessageReadStatus: "success")
        let requested = transcript
        #expect(transcript.mergeFinalMessage(try detail(text: " tail", id: "new", total: 8, cursor: 3),
            requested: requested, requestedStatus: "success"))
        #expect(transcript.finalMessage.isEmpty && transcript.finalMessageCursor == 0)
        #expect(transcript.finalMessageReadStatus == nil && transcript.finalMessageHasMore)
        let restarted = transcript
        #expect(!transcript.mergeFinalMessage(try detail(text: "New tail", id: "new", total: 8),
            requested: restarted, requestedStatus: "success"))
        #expect(transcript.finalMessage == "New tail" && !transcript.finalMessageHasMore)
    }

    @Test
    func changedRenderedPathsCanRewindAnOldEOFWithoutDuplicatingTheAnswer() throws {
        let old = "[result](/private/result)\n\u{6587}\u{4f3c}"
        let current = old.replacingOccurrences(of: "/private/", with: "/accepted/")
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 5,
            finalMessage: old, finalMessageCursor: old.utf8.count, finalMessageHasMore: false,
            finalMessageID: "old", finalMessageReadStatus: "success")
        let requested = transcript
        #expect(!transcript.mergeFinalMessage(
            try detail(text: current, id: "new", total: current.utf8.count),
            requested: requested, requestedStatus: "success"))
        #expect(transcript.finalMessage == current)
        #expect(transcript.finalMessageCursor == current.utf8.count)
        #expect(!transcript.finalMessageHasMore && transcript.finalMessageID == "new")
        let refreshed = transcript
        #expect(!transcript.mergeFinalMessage(
            try detail(text: "", id: "old", total: old.utf8.count, cursor: old.utf8.count),
            requested: requested, requestedStatus: "success"))
        #expect(transcript == refreshed)
        #expect(!transcript.mergeFinalMessage(
            try detail(text: "", id: "new", total: current.utf8.count, cursor: current.utf8.count),
            requested: refreshed, requestedStatus: "success"))
        #expect(transcript == refreshed)
    }

    private func detail(text: String, id: String, total: Int, cursor: Int = 0,
                        more: Bool = false, error: String? = nil) throws -> RunAgentDetail {
        var object: [String: Any] = [
            "runId": "run", "agent": 5, "name": "Agent 5", "status": "success",
            "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false,
            "stdoutTail": "", "stderrTail": "", "finalMessage": "",
            "finalMessagePage": ["cursor": cursor, "nextCursor": cursor + text.utf8.count,
                "hasMore": more, "totalBytes": total, "messageId": id, "text": text],
        ]
        if let error { object["artifactError"] = error }
        return try JSONDecoder().decode(RunAgentDetail.self, from: JSONSerialization.data(withJSONObject: object))
    }
}
