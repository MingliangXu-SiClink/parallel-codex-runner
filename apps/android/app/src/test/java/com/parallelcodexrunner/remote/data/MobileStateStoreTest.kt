package com.parallelcodexrunner.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import kotlinx.serialization.json.JsonObject

class MobileStateStoreTest {
    @Test
    fun cachedDevicesAreNormalizedAndRemovedWithTheirState() {
        val state = PersistedMobileState(
            selectedMacId = " machine-1 ",
            cachedDevices = listOf(
                RelayDevice(
                    deviceId = " machine-1 ",
                    kind = " mac ",
                    name = " My machine ",
                    publicKey = " public-key ",
                    status = " active ",
                    online = true,
                    lastSeenAt = " now ",
                ),
                RelayDevice(
                    deviceId = "machine-1",
                    kind = "mac",
                    name = "duplicate",
                    publicKey = "duplicate-key",
                    status = "active",
                ),
                RelayDevice("", "mac", "invalid", "key", "active"),
            ),
        ).normalized()

        val device = state.cachedDevices.single()
        assertEquals("machine-1", state.selectedMacId)
        assertEquals("machine-1", device.deviceId)
        assertEquals("My machine", device.name)
        assertEquals("public-key", device.publicKey)
        assertEquals("now", device.lastSeenAt)
        assertEquals(emptyList<RelayDevice>(), state.withoutMac("machine-1").cachedDevices)
    }

    @Test
    fun cachedWorkspacesAreNormalizedAndRemovedWithTheirDevice() {
        val state = PersistedMobileState(
            cachedWorkspacesByMac = mapOf(
                " machine-1 " to listOf(
                    RemoteWorkspace(
                        workspaceId = " workspace-1 ",
                        name = " ",
                        path = " /Users/example/Project ",
                        lastRunId = " run-1 ",
                        settings = RemoteRunSettings(agents = 99),
                    ),
                    RemoteWorkspace(
                        workspaceId = "workspace-1",
                        name = "duplicate",
                        path = "/tmp/duplicate",
                    ),
                ),
                "" to listOf(
                    RemoteWorkspace("ignored", "Ignored", "/tmp/ignored")
                ),
            )
        ).normalized()

        val cached = state.cachedWorkspacesByMac.getValue("machine-1").single()
        assertEquals("workspace-1", cached.workspaceId)
        assertEquals("Project", cached.name)
        assertEquals("/Users/example/Project", cached.path)
        assertEquals("run-1", cached.lastRunId)
        assertEquals(99, cached.settings?.agents)
        assertEquals(
            emptyMap<String, List<RemoteWorkspace>>(),
            state.withoutMac("machine-1").cachedWorkspacesByMac,
        )
    }

    @Test
    fun queuedFollowUpKeepsItsIdempotencyStateAcrossNormalization() {
        val item = PersistedQueuedFollowUp(
            id = " queue-1 ",
            prompt = "continue",
            settings = RemoteRunSettings(agents = 7),
            attempt = 2,
            dispatching = true,
            failureCount = 3,
            retryAtMillis = 123_456L,
            lastError = "  invalid continuation  ",
            paused = true,
            dispatch = PersistedFollowUpDispatch(
                mode = PersistedFollowUpDispatch.MODE_CONTINUE,
                sourceRunId = "run-1",
                sourceAgent = 2,
                settings = RemoteRunSettings(agents = 3),
            ),
        )

        val restored = PersistedMobileState(
            followUpQueueItems = mapOf("context" to listOf(item)),
        ).normalized().followUpQueueItems.getValue("context").single()

        assertEquals("queue-1", restored.id)
        assertEquals(7, restored.settings?.agents)
        assertEquals(2, restored.attempt)
        assertEquals(true, restored.dispatching)
        assertEquals("run-1", restored.dispatch?.sourceRunId)
        assertEquals(2, restored.dispatch?.sourceAgent)
        assertEquals("follow-up-queue-1-attempt-2", restored.commandId)
        assertEquals(3, restored.failureCount)
        assertEquals(123_456L, restored.retryAtMillis)
        assertEquals("invalid continuation", restored.lastError)
        assertEquals(true, restored.paused)
    }

    @Test
    fun legacyIdleWorkerPauseGetsOneAutomaticRecoveryAttempt() {
        val restored = PersistedMobileState(
            followUpQueueItems = mapOf(
                "context" to listOf(
                    PersistedQueuedFollowUp(
                        id = "queue-1",
                        prompt = "continue",
                        paused = true,
                        lastError = "没有可继续的 Agent，排队消息已暂停。",
                    )
                )
            ),
        ).normalized().followUpQueueItems.getValue("context").single()

        assertFalse(restored.paused)
        assertEquals("", restored.lastError)
        assertEquals(true, restored.automaticRecoveryAttempted)

        val pausedAgain = restored.copy(
            paused = true,
            lastError = "没有可继续的 Agent，排队消息已暂停。",
        )
        val normalizedAgain = PersistedMobileState(
            followUpQueueItems = mapOf("context" to listOf(pausedAgain)),
        ).normalized().followUpQueueItems.getValue("context").single()

        assertEquals(true, normalizedAgain.paused)
        assertEquals(true, normalizedAgain.automaticRecoveryAttempted)
    }

    @Test
    fun transportRetryKeepsTheLogicalCommandId() {
        val failed = pendingSubmission().copy(
            status = PendingPromptSubmission.STATUS_FAILED,
            error = "connection lost",
            retryRequiresNewCommandId = false,
        )

        val retrying = failed.forRetry()

        assertEquals(failed.commandId, retrying.commandId)
        assertEquals(PendingPromptSubmission.STATUS_RETRYING, retrying.status)
        assertEquals("", retrying.error)
        assertFalse(retrying.retryRequiresNewCommandId)
    }

    @Test
    fun rejectedCommandRetryUsesANewLogicalCommandId() {
        val failed = pendingSubmission().copy(
            status = PendingPromptSubmission.STATUS_FAILED,
            error = "remote command failed",
            retryRequiresNewCommandId = true,
        )

        val retrying = failed.forRetry()

        assertEquals(1, retrying.attempt)
        assertEquals("submission-1-attempt-1", retrying.commandId)
        assertFalse(retrying.retryRequiresNewCommandId)
    }

    @Test
    fun acceptedPromptAtomicallyLeavesTheOutboxAndKeepsItsRunHandoff() {
        val context = "mac-1\u001fworkspace-1"
        val pending = pendingSubmission().copy(
            status = PendingPromptSubmission.STATUS_RETRYING,
        )
        val result = AcceptedPromptResult(
            submission = pendingSubmission(),
            runId = "run-2",
            continuedSessionId = "session-2",
            workspaceRevision = 7,
        )

        val updated = PersistedMobileState(
            promptOutbox = mapOf(context to pending),
        ).recordingAcceptedPromptResult(context, pendingSubmission(), result)

        assertFalse(updated.promptOutbox.containsKey(context))
        assertEquals(pending, updated.acceptedPromptResults.getValue(context).submission)
        assertEquals("run-2", updated.acceptedPromptResults.getValue(context).runId)
    }

    @Test
    fun staleWorkerCannotConsumeANewerPromptSubmission() {
        val context = "mac-1\u001fworkspace-1"
        val newer = pendingSubmission().copy(id = "submission-2")
        val state = PersistedMobileState(promptOutbox = mapOf(context to newer))

        val updated = state.recordingAcceptedPromptResult(
            contextKey = context,
            expected = pendingSubmission(),
            result = AcceptedPromptResult(pendingSubmission(), "run-old"),
        )

        assertEquals(state, updated)
    }

    @Test
    fun normalizesRestoredDraftsAndQueuesWithoutDroppingValidText() {
        val state = PersistedMobileState(
            selectedMacId = "  mac-1  ",
            selectedWorkspaceByMac = mapOf(
                " mac-1 " to " workspace-2 ",
                "" to "ignored",
                "mac-2" to " ",
            ),
            detachedConversationContexts = setOf(
                "mac-1\u001fworkspace-1",
                "broken",
                "",
            ),
            promptDrafts = mapOf(
                "" to "ignored",
                "mac-1\u001fworkspace-1" to "multi\nline draft",
                "mac-1\u001fworkspace-2" to "",
            ),
            followUpQueues = mapOf(
                "" to listOf("ignored"),
                "mac-1\u001fworkspace-1\u001frun-1" to listOf(
                    "first queued prompt",
                    "",
                    "second\nqueued prompt",
                ),
                "mac-1\u001fworkspace-2\u001frun-2" to listOf("", "   "),
            ),
            followUpDeadlines = mapOf(
                "mac-1\u001fworkspace-1\u001frun-1" to 1_900_000_000_000L,
                "broken" to -1L,
            ),
            workspaceSettings = mapOf(
                "" to RemoteRunSettings(agents = 9),
                "mac-1\u001fworkspace-1" to RemoteRunSettings(
                    agents = 99,
                    synthesisAgents = -1,
                ),
            ),
            dirtyWorkspaceSettingFields = mapOf(
                "" to setOf(WorkspaceSettingFields.AGENTS),
                "mac-1\u001fworkspace-1" to setOf(
                    WorkspaceSettingFields.AGENTS,
                    "unknown_field",
                ),
                "mac-1\u001fworkspace-2" to setOf("unknown_field"),
            ),
            defaultSettingsByMac = mapOf(
                "" to RemoteRunSettings(agents = 7),
                "mac-1" to RemoteRunSettings(agents = 8),
            ),
            dirtyDefaultSettingFieldsByMac = mapOf(
                "mac-2" to setOf(
                    WorkspaceSettingFields.MODEL,
                    WorkspaceSettingFields.RESUME_SESSION_ID,
                    "unknown_field",
                ),
            ),
            dirtyDefaultSettingsMacIds = setOf("", "mac-1"),
            promptOutbox = mapOf(
                "mac-1\u001fworkspace-1" to PendingPromptSubmission(
                    id = " submission-1 ",
                    macId = " mac-1 ",
                    workspaceId = " workspace-1 ",
                    prompt = "  fix it\n",
                    settings = RemoteRunSettings(agents = 99),
                    attempt = -2,
                    status = "unknown",
                ),
                "broken" to PendingPromptSubmission(
                    id = "",
                    macId = "mac-1",
                    workspaceId = "workspace-1",
                    prompt = "ignored",
                    settings = RemoteRunSettings(),
                ),
            ),
            acceptedPromptResults = mapOf(
                "mac-1\u001fworkspace-2" to AcceptedPromptResult(
                    submission = PendingPromptSubmission(
                        id = " accepted-1 ",
                        macId = " mac-1 ",
                        workspaceId = " workspace-2 ",
                        prompt = "continue",
                        settings = RemoteRunSettings(agents = 5),
                    ),
                    runId = " run-2 ",
                    continuedSessionId = " session-2 ",
                    workspaceRevision = -4,
                ),
                "wrong-context" to AcceptedPromptResult(
                    submission = pendingSubmission(),
                    runId = "run-ignored",
                ),
            ),
            feedbackOutbox = mapOf(
                "legacy-key" to PendingFeedbackSubmission(
                    id = " feedback-1 ",
                    relatedDeviceId = " mac-1 ",
                    kind = "BUG",
                    description = "  keep this text  ",
                    diagnostics = JsonObject(emptyMap()),
                    attachments = listOf(
                        PendingFeedbackAttachment(
                            fileName = "${"a".repeat(64)}.jpg",
                            contentType = "image/jpeg",
                            sha256 = "a".repeat(64),
                            sizeBytes = 42,
                        ),
                        PendingFeedbackAttachment(
                            fileName = "../invalid.jpg",
                            contentType = "image/jpeg",
                            sha256 = "a".repeat(64),
                            sizeBytes = 42,
                        ),
                    ),
                    status = "unknown",
                    error = "  temporary failure  ",
                ),
            ),
            selectedAgents = mapOf(
                "mac-1\u001fworkspace-1\u001frun-1" to 3,
                "broken" to 0,
            ),
            workExpansionStates = mapOf(
                "mac-1\u001fworkspace-1" to true,
                "mac-1\u001fworkspace-1\u001frun-1\u001f3" to false,
                "broken" to true,
            ),
            viewedAgentCompletions = setOf(
                "mac-1\u001fworkspace-1\u001frun-1\u001f3",
                "broken",
            ),
        ).normalized()

        assertEquals("mac-1", state.selectedMacId)
        assertEquals(mapOf("mac-1" to "workspace-2"), state.selectedWorkspaceByMac)
        assertEquals(
            setOf("mac-1\u001fworkspace-1"),
            state.detachedConversationContexts,
        )
        assertEquals(
            "multi\nline draft",
            state.promptDrafts["mac-1\u001fworkspace-1"],
        )
        assertFalse(state.promptDrafts.containsKey("mac-1\u001fworkspace-2"))
        assertEquals(
            listOf("first queued prompt", "second\nqueued prompt"),
            state.followUpQueueItems["mac-1\u001fworkspace-1\u001frun-1"]
                ?.map(PersistedQueuedFollowUp::prompt),
        )
        assertNull(state.followUpQueueItems["mac-1\u001fworkspace-2\u001frun-2"])
        assertEquals(emptyMap<String, List<String>>(), state.followUpQueues)
        assertEquals(
            1_900_000_000_000L,
            state.followUpDeadlines["mac-1\u001fworkspace-1\u001frun-1"],
        )
        assertFalse(state.followUpDeadlines.containsKey("broken"))
        assertFalse(state.workspaceSettings.containsKey(""))
        assertEquals(99, state.workspaceSettings["mac-1\u001fworkspace-1"]?.agents)
        assertEquals(0, state.workspaceSettings["mac-1\u001fworkspace-1"]?.synthesisAgents)
        assertEquals(
            setOf(WorkspaceSettingFields.AGENTS),
            state.dirtyWorkspaceSettingFields["mac-1\u001fworkspace-1"],
        )
        assertFalse(state.dirtyWorkspaceSettingFields.containsKey(""))
        assertFalse(
            state.dirtyWorkspaceSettingFields.containsKey("mac-1\u001fworkspace-2")
        )
        assertFalse(state.defaultSettingsByMac.containsKey(""))
        assertEquals(8, state.defaultSettingsByMac["mac-1"]?.agents)
        assertEquals(
            WorkspaceSettingFields.defaultConversation,
            state.dirtyDefaultSettingFieldsByMac["mac-1"],
        )
        assertEquals(
            setOf(WorkspaceSettingFields.MODEL),
            state.dirtyDefaultSettingFieldsByMac["mac-2"],
        )
        assertEquals(emptySet<String>(), state.dirtyDefaultSettingsMacIds)
        val pending = state.promptOutbox["mac-1\u001fworkspace-1"]
        assertEquals("submission-1", pending?.id)
        assertEquals("  fix it\n", pending?.prompt)
        assertEquals(99, pending?.settings?.agents)
        assertEquals(0, pending?.attempt)
        assertEquals(PendingPromptSubmission.STATUS_PENDING, pending?.status)
        assertEquals("submission-1-attempt-0", pending?.commandId)
        assertEquals(
            "submission-1-attempt-0-confirmed",
            pending?.copy(confirmLargeRun = true)?.commandId,
        )
        assertEquals(
            "submission-1-attempt-1",
            pending?.copy(attempt = 1)?.commandId,
        )
        assertFalse(state.promptOutbox.containsKey("broken"))
        val accepted = state.acceptedPromptResults.getValue(
            "mac-1\u001fworkspace-2"
        )
        assertEquals("accepted-1", accepted.submission.id)
        assertEquals("run-2", accepted.runId)
        assertEquals("session-2", accepted.continuedSessionId)
        assertEquals(-1, accepted.workspaceRevision)
        assertFalse(state.acceptedPromptResults.containsKey("wrong-context"))
        val feedback = state.feedbackOutbox.getValue("feedback-1")
        assertEquals("feedback-1", feedback.id)
        assertEquals("mac-1", feedback.relatedDeviceId)
        assertEquals("bug", feedback.kind)
        assertEquals("keep this text", feedback.description)
        assertEquals(1, feedback.attachments.size)
        assertEquals(PendingFeedbackSubmission.STATUS_PENDING, feedback.status)
        assertEquals("temporary failure", feedback.error)
        assertEquals(
            3,
            state.selectedAgents["mac-1\u001fworkspace-1\u001frun-1"],
        )
        assertFalse(state.selectedAgents.containsKey("broken"))
        assertEquals(
            false,
            state.workExpansionStates[
                "mac-1\u001fworkspace-1\u001frun-1\u001f3"
            ],
        )
        assertEquals(true, state.workExpansionStates["mac-1\u001fworkspace-1"])
        assertFalse(state.workExpansionStates.containsKey("broken"))
        assertEquals(
            setOf("mac-1\u001fworkspace-1\u001frun-1\u001f3"),
            state.viewedAgentCompletions,
        )
    }

    @Test
    fun productFeedbackSurvivesWithoutARelatedMachine() {
        val feedback = pendingFeedback("feedback-global", "")

        val state = PersistedMobileState(
            feedbackOutbox = mapOf(feedback.id to feedback),
        ).normalized()

        assertEquals("", state.feedbackOutbox.getValue(feedback.id).relatedDeviceId)
    }

    @Test
    fun removingAMacDropsOnlyStateOwnedByThatMac() {
        val removedPrefix = "mac-1\u001f"
        val retainedPrefix = "mac-10\u001f"
        val state = PersistedMobileState(
            selectedMacId = "mac-1",
            selectedWorkspaceByMac = mapOf(
                "mac-1" to "workspace-1",
                "mac-10" to "workspace-2",
            ),
            detachedConversationContexts = setOf(
                "${removedPrefix}workspace-1",
                "${retainedPrefix}workspace-2",
            ),
            promptDrafts = mapOf(
                "${removedPrefix}workspace-1" to "remove",
                "${retainedPrefix}workspace-2" to "keep",
            ),
            followUpQueueItems = mapOf(
                "${removedPrefix}workspace-1\u001frun-1" to listOf(
                    PersistedQueuedFollowUp("removed", "remove"),
                ),
                "${retainedPrefix}workspace-2\u001frun-2" to listOf(
                    PersistedQueuedFollowUp("retained", "keep"),
                ),
            ),
            followUpDeadlines = mapOf(
                "${removedPrefix}workspace-1\u001frun-1" to 1L,
                "${retainedPrefix}workspace-2\u001frun-2" to 2L,
            ),
            workspaceSettings = mapOf(
                "${removedPrefix}workspace-1" to RemoteRunSettings(agents = 2),
                "${retainedPrefix}workspace-2" to RemoteRunSettings(agents = 3),
            ),
            dirtyWorkspaceSettingFields = mapOf(
                "${removedPrefix}workspace-1" to setOf(WorkspaceSettingFields.AGENTS),
                "${retainedPrefix}workspace-2" to setOf(WorkspaceSettingFields.MODEL),
            ),
            defaultSettingsByMac = mapOf(
                "mac-1" to RemoteRunSettings(agents = 2),
                "mac-10" to RemoteRunSettings(agents = 3),
            ),
            dirtyDefaultSettingFieldsByMac = mapOf(
                "mac-1" to setOf(WorkspaceSettingFields.AGENTS),
                "mac-10" to setOf(WorkspaceSettingFields.MODEL),
            ),
            promptOutbox = mapOf(
                "${removedPrefix}workspace-1" to pendingSubmission(),
                "${retainedPrefix}workspace-2" to pendingSubmission().copy(
                    id = "submission-2",
                    macId = "mac-10",
                    workspaceId = "workspace-2",
                ),
            ),
            acceptedPromptResults = mapOf(
                "${removedPrefix}workspace-1" to AcceptedPromptResult(
                    submission = pendingSubmission(),
                    runId = "run-accepted-1",
                ),
                "${retainedPrefix}workspace-2" to AcceptedPromptResult(
                    submission = pendingSubmission().copy(
                        id = "accepted-2",
                        macId = "mac-10",
                        workspaceId = "workspace-2",
                    ),
                    runId = "run-accepted-2",
                ),
            ),
            feedbackOutbox = mapOf(
                "feedback-1" to pendingFeedback("feedback-1", "mac-1"),
                "feedback-2" to pendingFeedback("feedback-2", "mac-10"),
            ),
            selectedAgents = mapOf(
                "${removedPrefix}workspace-1\u001frun-1" to 1,
                "${retainedPrefix}workspace-2\u001frun-2" to 2,
            ),
            workExpansionStates = mapOf(
                "${removedPrefix}workspace-1\u001frun-1\u001f1" to false,
                "${retainedPrefix}workspace-2" to false,
                "${retainedPrefix}workspace-2\u001frun-2\u001f2" to true,
            ),
            viewedAgentCompletions = setOf(
                "${removedPrefix}workspace-1\u001frun-1\u001f1",
                "${retainedPrefix}workspace-2\u001frun-2\u001f2",
            ),
        ).withoutMac(" mac-1 ")

        assertNull(state.selectedMacId)
        assertEquals(mapOf("mac-10" to "workspace-2"), state.selectedWorkspaceByMac)
        assertEquals(
            setOf("${retainedPrefix}workspace-2"),
            state.detachedConversationContexts,
        )
        assertEquals(setOf("${retainedPrefix}workspace-2"), state.promptDrafts.keys)
        assertEquals(
            setOf("${retainedPrefix}workspace-2\u001frun-2"),
            state.followUpQueueItems.keys,
        )
        assertEquals(
            setOf("${retainedPrefix}workspace-2\u001frun-2"),
            state.followUpDeadlines.keys,
        )
        assertEquals(setOf("${retainedPrefix}workspace-2"), state.workspaceSettings.keys)
        assertEquals(
            setOf("${retainedPrefix}workspace-2"),
            state.dirtyWorkspaceSettingFields.keys,
        )
        assertEquals(setOf("mac-10"), state.defaultSettingsByMac.keys)
        assertEquals(setOf("mac-10"), state.dirtyDefaultSettingFieldsByMac.keys)
        assertEquals(setOf("${retainedPrefix}workspace-2"), state.promptOutbox.keys)
        assertEquals(
            setOf("${retainedPrefix}workspace-2"),
            state.acceptedPromptResults.keys,
        )
        assertEquals(setOf("feedback-1", "feedback-2"), state.feedbackOutbox.keys)
        assertEquals(
            setOf("${retainedPrefix}workspace-2\u001frun-2"),
            state.selectedAgents.keys,
        )
        assertEquals(
            mapOf(
                "${retainedPrefix}workspace-2" to false,
                "${retainedPrefix}workspace-2\u001frun-2\u001f2" to true,
            ),
            state.workExpansionStates,
        )
        assertEquals(
            setOf("${retainedPrefix}workspace-2\u001frun-2\u001f2"),
            state.viewedAgentCompletions,
        )
    }

    private fun pendingSubmission() = PendingPromptSubmission(
        id = "submission-1",
        macId = "mac-1",
        workspaceId = "workspace-1",
        prompt = "fix it",
        settings = RemoteRunSettings(),
    )

    private fun pendingFeedback(id: String, macId: String) = PendingFeedbackSubmission(
        id = id,
        relatedDeviceId = macId,
        kind = "bug",
        description = "feedback",
        diagnostics = JsonObject(emptyMap()),
    )
}
