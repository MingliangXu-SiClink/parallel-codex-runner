import Foundation

private func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError(message) }
}

@main
private enum DesktopStateTests {
    static func main() {
        let blockedQueue = DispatchQueue(label: "desktop-state-drain-test")
        let releaseBlockedQueue = DispatchSemaphore(value: 0)
        blockedQueue.async {
            releaseBlockedQueue.wait()
        }
        let drainStart = Date()
        require(
            !BoundedQueueDrain.wait(for: blockedQueue, timeout: 0.05),
            "A blocked state queue was reported as drained"
        )
        require(
            Date().timeIntervalSince(drainStart) < 0.5,
            "A blocked state queue held application termination hostage"
        )
        releaseBlockedQueue.signal()
        require(
            BoundedQueueDrain.wait(for: blockedQueue, timeout: 1),
            "A released state queue did not drain"
        )

        let submission = PersistedPromptSubmission(
            id: "submission-1",
            machineID: "mac-1",
            workspaceID: "workspace-1",
            prompt: "continue",
            settings: RunSettings(),
            sourceRunID: "run-1",
            sourceAgent: 2,
            resumeSessionID: "session-1",
            confirmLargeRun: false,
            attempt: 0,
            status: .pending,
            error: ""
        )
        var accepted = PersistedAcceptedPromptResult(
            submission: submission,
            runID: "run-2",
            continuedSessionID: "session-2",
            workspaceRevision: 7
        )
        accepted.submission.receiptUncertain = false
        accepted.submission.status = .opening
        var state = PersistedDesktopState()
        state.promptOutboxByWorkspace[submission.contextKey] = submission
        require(
            state.recordAcceptedPromptResult(
                contextKey: submission.contextKey,
                expected: submission,
                result: accepted
            ),
            "An accepted prompt was not moved out of the durable outbox"
        )
        require(
            state.promptOutboxByWorkspace[submission.contextKey] == nil,
            "The accepted prompt remained in the durable outbox"
        )
        require(
            state.acceptedPromptResultsByWorkspace[submission.contextKey] == accepted,
            "The accepted task handoff was not retained"
        )
        let roundTrip = try! JSONDecoder().decode(
            PersistedDesktopState.self,
            from: JSONEncoder().encode(state)
        )
        require(
            roundTrip.acceptedPromptResultsByWorkspace[submission.contextKey] == accepted,
            "The accepted task handoff did not survive persistence"
        )

        var staleState = PersistedDesktopState()
        staleState.promptOutboxByWorkspace[submission.contextKey] = submission
        var staleSubmission = submission
        staleSubmission.attempt = 1
        require(
            !staleState.recordAcceptedPromptResult(
                contextKey: submission.contextKey,
                expected: staleSubmission,
                result: accepted
            ),
            "A stale command consumed a newer prompt submission"
        )
        require(
            staleState.promptOutboxByWorkspace[submission.contextKey] == submission,
            "A stale command removed the current prompt submission"
        )

        print("macOS desktop state tests passed")
    }
}
