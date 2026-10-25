package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.InternetAvailability
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.PersistedQueuedFollowUp
import com.parallelcodexrunner.remote.data.MAX_AGENT_COUNT
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.normalized
import java.net.ConnectException
import java.time.Instant
import java.time.ZoneOffset
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MainViewModelLogicTest {
    @Test
    fun conversationPickerCanSupersedeASlowOpenWithoutDuplicatingRequests() {
        assertTrue(
            canOpenWorkspaceConversation(
                visibleConversationId = "conversation-current",
                openingConversationId = "conversation-slow",
                requestedConversationId = "conversation-next",
            )
        )
        assertFalse(
            canOpenWorkspaceConversation(
                visibleConversationId = "conversation-current",
                openingConversationId = "conversation-slow",
                requestedConversationId = "conversation-slow",
            )
        )
        assertFalse(
            canOpenWorkspaceConversation(
                visibleConversationId = "conversation-current",
                openingConversationId = null,
                requestedConversationId = "conversation-current",
            )
        )
        assertFalse(
            canOpenWorkspaceConversation(
                visibleConversationId = null,
                openingConversationId = null,
                requestedConversationId = "  ",
            )
        )
    }

    @Test
    fun promptDraftsAreScopedToTheVisibleConversation() {
        val first = promptDraftContextKey("mac-1", "workspace-1", "conversation-1", "NO")
        val second = promptDraftContextKey("mac-1", "workspace-1", "conversation-2", "NO")
        val fresh = promptDraftContextKey("mac-1", "workspace-1", null, "NO")
        val resumed = promptDraftContextKey("mac-1", "workspace-1", null, "session-1")

        assertTrue(first.endsWith("\u001fconversation\u001fconversation-1"))
        assertFalse(first == second)
        assertFalse(first == fresh)
        assertFalse(fresh == resumed)
    }

    @Test
    fun legacyWorkspaceDraftMigratesOnlyToTheFirstOpenedConversation() {
        val legacyKey = workspaceSettingsKey("mac-1", "workspace-1")
        val drafts = mutableMapOf(legacyKey to "unfinished question")

        assertEquals(
            "unfinished question",
            restorePromptDraft(drafts, "mac-1", "workspace-1", "conversation-2", "NO"),
        )
        assertFalse(drafts.containsKey(legacyKey))
        assertEquals(
            "unfinished question",
            drafts[promptDraftContextKey("mac-1", "workspace-1", "conversation-2", "NO")],
        )
        assertEquals(
            "",
            restorePromptDraft(drafts, "mac-1", "workspace-1", "conversation-3", "NO"),
        )
    }

    @Test
    fun coldRunHydrationRestoresTheConversationDraftWithoutNetwork() {
        val key = promptDraftContextKey("mac-1", "workspace-1", "conversation-1", "NO")
        val drafts = mutableMapOf(key to "unfinished offline question")
        val loading = MainUiState(
            selectedMacId = "mac-1",
            selectedWorkspaceId = "workspace-1",
            visibleRunId = "run-1",
            runSettings = RemoteRunSettings(resumeSessionId = "session-1"),
        )

        assertEquals("", restorePromptDraft(
            drafts, "mac-1", "workspace-1", null, "session-1", "run-1",
        ))
        val restored = loading.projectRun(
            WorkspaceRunSnapshot(conversationId = "conversation-1"),
            promptDrafts = drafts,
        )
        assertEquals("unfinished offline question", restored.prompt)
        assertEquals("conversation-1", restored.visibleConversationId)
        assertEquals("unfinished offline question", drafts[key])

        val edited = restored.copy(prompt = "")
        assertEquals("", edited.projectRun(
            WorkspaceRunSnapshot(conversationId = "conversation-1", status = "finalized"),
            promptDrafts = drafts,
        ).prompt)
    }

    @Test
    fun hydrationPreservesEditsAndClearsMadeBeforeConversationIdentityArrives() {
        for (edit in listOf("typed while loading", "")) {
            val unresolvedKey = promptDraftContextKey("mac", "workspace", null, "NO", "run")
            val conversationKey = promptDraftContextKey("mac", "workspace", "conversation", "NO")
            val newChatKey = promptDraftContextKey("mac", "workspace", null, "NO")
            val drafts = mutableMapOf(
                conversationKey to "older draft",
                unresolvedKey to edit,
                newChatKey to "unrelated new chat draft",
            )
            val loading = MainUiState(
                selectedMacId = "mac", selectedWorkspaceId = "workspace", visibleRunId = "run",
                prompt = edit,
            )
            val restored = loading.projectRun(
                WorkspaceRunSnapshot(conversationId = "conversation"), promptDrafts = drafts,
            )

            assertEquals(edit, restored.prompt)
            assertFalse(drafts.containsKey(unresolvedKey))
            assertEquals(edit.takeIf(String::isNotEmpty), drafts[conversationKey])
            assertEquals("unrelated new chat draft", drafts[newChatKey])
        }
    }

    @Test
    fun hydrationDoesNotBorrowDraftsFromAnotherMachineWorkspaceOrRun() {
        val drafts = mutableMapOf(
            promptDraftContextKey("other-mac", "workspace", "conversation", "NO") to "other Mac",
            promptDraftContextKey("mac", "other-workspace", "conversation", "NO") to "other workspace",
            promptDraftContextKey("mac", "workspace", null, "NO", "other-run") to "other run",
            promptDraftContextKey("mac", "workspace", null, "NO") to "new conversation",
        )
        val loading = MainUiState(
            selectedMacId = "mac", selectedWorkspaceId = "workspace", visibleRunId = "run",
        )
        assertEquals("", loading.projectRun(
            WorkspaceRunSnapshot(conversationId = "conversation"), promptDrafts = drafts,
        ).prompt)
        assertEquals(4, drafts.size)
    }

    @Test
    fun conversationScreenKeepsDiscoveringNewTurnsAfterEventPollingEnds() {
        assertEquals(5_000L, workspaceOverviewRefreshInterval(PANE_WORKSPACES))
        assertEquals(10_000L, workspaceOverviewRefreshInterval(PANE_RUN))
        assertNull(workspaceOverviewRefreshInterval(PANE_SETTINGS))
    }

    @Test
    fun restoresTheLastOpenConversationOnlyForTheSameMachineAndWorkspace() {
        val remembered = workspacePaneContextKey("mac-1", "workspace-1")

        assertTrue(shouldRestoreRunPane(remembered, "mac-1", "workspace-1"))
        assertFalse(shouldRestoreRunPane(remembered, "mac-2", "workspace-1"))
        assertFalse(shouldRestoreRunPane(remembered, "mac-1", "workspace-2"))
        assertFalse(shouldRestoreRunPane(null, "mac-1", "workspace-1"))
        assertNull(workspacePaneContextKey("", "workspace-1"))
    }

    @Test
    fun transientMessagesDismissAfterTwoSeconds() {
        assertEquals(2_000L, TRANSIENT_MESSAGE_TIMEOUT_MILLIS)
    }

    @Test
    fun cachedOfflineStartupDoesNotCoverTheConversationWithARefreshError() {
        val offline = ConnectException("offline")
        assertFalse(shouldShowMachineRefreshError(offline, false, true))
        assertTrue(shouldShowMachineRefreshError(offline, true, true))
        assertTrue(shouldShowMachineRefreshError(offline, false, false))
        assertTrue(shouldShowMachineRefreshError(
            RemoteCommandException("unauthorized", "Sign in again"), false, true,
        ))
    }

    @Test
    fun offlineRefreshDoesNotReplaceATerminalResultStatusWithReconnecting() {
        val state = MainUiState(
            selectedMacId = "mac", selectedWorkspaceId = "workspace", visibleRunId = "run",
            liveUpdateInterruptedContext = WorkspaceRunKey("mac", "workspace", "run").encoded,
        )
        for (status in listOf("completed", "finalized", "failed", "cancelled")) {
            assertFalse(status, state.copy(runStatus = status).isVisibleRunUpdateInterrupted())
        }
        assertTrue(state.copy(runStatus = "running").isVisibleRunUpdateInterrupted())
    }

    @Test
    fun initialSyncProgressTracksCompletedRemoteStages() {
        val expected = listOf(
            InitialSyncStage.CONNECTING to 0f,
            InitialSyncStage.DEVICES to 0.25f,
            InitialSyncStage.COMPUTER to 0.5f,
            InitialSyncStage.WORKSPACES to 0.75f,
            InitialSyncStage.COMPLETE to 1f,
        )

        expected.forEach { (stage, fraction) ->
            assertEquals(fraction, InitialSyncProgress(stage).fraction, 0f)
        }
        assertEquals(4, InitialSyncProgress.TOTAL_STEPS)
        assertEquals(4, InitialSyncStage.WORKSPACES.currentStep)
    }

    @Test
    fun promptRetryUsesTheSameUserFacingSendingState() {
        assertEquals("正在提交", pendingSubmissionLabel(PendingPromptSubmission.STATUS_PENDING))
        assertEquals("正在提交", pendingSubmissionLabel(PendingPromptSubmission.STATUS_RETRYING))
        assertEquals(
            "等待空间确认",
            pendingSubmissionLabel(PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION),
        )
        assertEquals("发送失败", pendingSubmissionLabel(PendingPromptSubmission.STATUS_FAILED))
    }

    @Test
    fun socketAddressDetailsAreNotShownForTransientRelayFailures() {
        val error = ConnectException("Failed to connect to /8.153.193.1:443")

        assertEquals(
            TRANSIENT_RELAY_CONNECTION_MESSAGE,
            transientRelayConnectionMessage(error),
        )
        assertNull(transientRelayConnectionMessage(IllegalStateException("invalid setting")))
    }

    @Test
    fun onlyConnectivityFailuresInterruptLiveRunUpdates() {
        assertTrue(isLiveUpdateConnectionFailure(ConnectException("connection refused")))
        assertTrue(
            isLiveUpdateConnectionFailure(
                RemoteCommandException("machine_offline", "Mac is offline"),
            ),
        )
        assertFalse(
            isLiveUpdateConnectionFailure(
                RemoteCommandException("pcr_error", "Run artifacts are not available"),
            ),
        )
        assertFalse(
            isLiveUpdateConnectionFailure(
                RemoteCommandException("response_too_large", "response is too large"),
            ),
        )
    }

    @Test
    fun pollingReplacesTheOfflineStartupSnapshotWithTheLatestDeviceState() {
        val cached = relayDevice(online = false, lastSeenAt = "old")
        val refreshed = relayDevice(online = true, lastSeenAt = "new")

        assertEquals(refreshed, latestPollingDevice(listOf(refreshed), cached))
        assertEquals(cached, latestPollingDevice(emptyList(), cached))
    }

    @Test
    fun presenceRefreshKeepsDeviceOrderAndMarksMissingDevicesOffline() {
        val selected = relayDevice(online = true, lastSeenAt = "old")
        val missing = selected.copy(deviceId = "mac-2", name = "Second Mac")
        val added = selected.copy(deviceId = "mac-3", name = "Third Mac")
        val refreshedSelected = selected.copy(online = false, lastSeenAt = "new")

        val merged = mergeMachinePresence(
            current = listOf(selected, missing),
            refreshed = listOf(refreshedSelected, added),
        )

        assertEquals(listOf("mac-1", "mac-2", "mac-3"), merged.map(RelayDevice::deviceId))
        assertEquals(refreshedSelected, merged[0])
        assertEquals(false, merged[1].online)
        assertEquals(added, merged[2])
    }

    @Test
    fun runToolbarKeepsRunStatusSeparateFromConnectionProblems() {
        val offlineDevice = relayDevice(online = false, lastSeenAt = "old")
        val deviceOffline = MainUiState(
            internetAvailability = InternetAvailability.AVAILABLE,
            macs = listOf(offlineDevice),
            selectedMacId = offlineDevice.deviceId,
            runStatus = "finalized",
        )

        assertEquals(
            RunToolbarConnectionIssue.DEVICE_OFFLINE,
            runToolbarConnectionIssue(deviceOffline),
        )
        assertEquals(
            RunToolbarConnectionIssue.INTERNET_UNAVAILABLE,
            runToolbarConnectionIssue(
                deviceOffline.copy(internetAvailability = InternetAvailability.UNAVAILABLE),
            ),
        )
        assertNull(
            runToolbarConnectionIssue(
                deviceOffline.copy(
                    internetAvailability = InternetAvailability.UNKNOWN,
                    macs = listOf(offlineDevice.copy(online = null)),
                ),
            ),
        )
    }

    @Test
    fun machineFileSystemFailuresUseActionableChineseMessages() {
        assertEquals(
            "目标设备拒绝访问这个文件夹，请检查权限后重试。",
            remoteFileSystemMessage(
                "The operating system denied access to directory: /private/project",
            ),
        )
        assertEquals(
            "这个文件夹不存在或已被移动。",
            remoteFileSystemMessage("Directory does not exist: /missing"),
        )
        assertNull(remoteFileSystemMessage("unrelated failure"))
    }

    @Test
    fun unavailableWorkspaceStillExposesRemovalActions() {
        assertFalse(
            workspaceRowSelectionEnabled(
                workspaceAvailable = false,
                selectionInProgress = false,
            ),
        )
        assertTrue(
            workspaceRowActionsEnabled(
                deviceOnline = true,
                selectionInProgress = false,
            ),
        )
        assertFalse(
            workspaceRowActionsEnabled(
                deviceOnline = true,
                selectionInProgress = true,
            ),
        )
        assertFalse(
            workspaceRowActionsEnabled(
                deviceOnline = false,
                selectionInProgress = false,
            ),
        )
    }

    private fun relayDevice(online: Boolean, lastSeenAt: String) = RelayDevice(
        deviceId = "mac-1",
        kind = "mac",
        name = "Mac",
        publicKey = "key",
        status = "active",
        online = online,
        lastSeenAt = lastSeenAt,
    )

    @Test
    fun uncertainFollowUpRetryReusesTheSameCommandIdentity() {
        val item = queuedItem(attempt = 2, dispatching = true)

        val retry = item.afterDispatchFailure(
            remoteRejected = false,
            nowMillis = 100_000L,
            errorMessage = "network unavailable",
        )

        assertEquals(item.commandId, retry.commandId)
        assertTrue(retry.dispatching)
        assertEquals(1, retry.failureCount)
        assertEquals(105_000L, retry.retryAtMillis)
        assertEquals("network unavailable", retry.lastError)
        assertFalse(retry.paused)
    }

    @Test
    fun rejectedFollowUpRetryAdvancesItsCommandIdentity() {
        val item = queuedItem(attempt = 2, dispatching = true)

        val retry = item.afterDispatchFailure(
            remoteRejected = true,
            nowMillis = 100_000L,
        )

        assertEquals(3, retry.attempt)
        assertEquals("follow-up-item-1-attempt-3", retry.commandId)
        assertFalse(retry.dispatching)
    }

    @Test
    fun repeatedRemoteRejectionsPauseAQueuedFollowUpInsteadOfHammeringTheRelay() {
        val item = queuedItem(attempt = 2).copy(failureCount = 2)

        val retry = item.afterDispatchFailure(
            remoteRejected = true,
            nowMillis = 100_000L,
            errorMessage = "invalid continuation",
        )

        assertEquals(3, retry.attempt)
        assertEquals(3, retry.failureCount)
        assertEquals(0L, retry.retryAtMillis)
        assertTrue(retry.paused)
        assertEquals("invalid continuation", retry.lastError)
    }

    @Test
    fun followUpRetryBackoffIsBounded() {
        assertEquals(5_000L, followUpRetryDelayMillis(1))
        assertEquals(10_000L, followUpRetryDelayMillis(2))
        assertEquals(300_000L, followUpRetryDelayMillis(99))
    }

    @Test
    fun queueMigrationKeepsOrderWithoutDuplicatingItems() {
        val first = queuedItem(id = "first")
        val second = queuedItem(id = "second")
        val third = queuedItem(id = "third")

        assertEquals(
            listOf("first", "second", "third"),
            mergeFollowUpQueues(
                queued = listOf(first, second),
                existing = listOf(second, third),
            ).map(PersistedQueuedFollowUp::id),
        )
    }

    @Test
    fun continuesFromTheAgentTheUserIsViewing() {
        val source = continuationSourceAgent(
            terminalState(
                selectedAgent = 3,
                recommendedAgent = 2,
                agents = listOf(agent(2), agent(3)),
            )
        )

        assertEquals(3, source?.number)
    }

    private fun queuedItem(
        id: String = "item-1",
        attempt: Int = 0,
        dispatching: Boolean = false,
    ) = PersistedQueuedFollowUp(
        id = id,
        prompt = "continue",
        attempt = attempt,
        dispatching = dispatching,
    )

    @Test
    fun fallsBackToTheRecommendationWhenSelectionCannotContinue() {
        val source = continuationSourceAgent(
            terminalState(
                selectedAgent = 3,
                recommendedAgent = 2,
                agents = listOf(agent(2), agent(3, canContinue = false)),
            )
        )

        assertEquals(2, source?.number)
    }

    @Test
    fun activeRunContinuesOnlyFromTheVisibleSuccessfulAgent() {
        assertEquals(
            1,
            continuationSourceAgent(
                terminalState(agents = listOf(agent(1))).copy(runStatus = "running")
            )?.number,
        )
        assertNull(
            continuationSourceAgent(
                terminalState(
                    selectedAgent = 2,
                    recommendedAgent = 1,
                    agents = listOf(
                        agent(1),
                        agent(2, canContinue = false),
                        agent(3, canContinue = false).copy(status = "running"),
                    ),
                ).copy(runStatus = "running")
            )
        )
        assertEquals(
            1,
            continuationSourceAgent(
                terminalState(agents = listOf(agent(1))).copy(finalizedAgent = 1)
            )?.number,
        )
    }

    @Test
    fun normalizesEveryEditableMobileSetting() {
        val settings = RemoteRunSettings(
            agents = 99,
            synthesisAgents = -1,
            followUpDelay = -4,
            execution = "unknown",
            maxParallel = 99,
            subagentsLimit = 99,
            recommendBy = "unknown",
            fastMode = "invalid",
            synthesisFastMode = "invalid",
        ).normalized()

        assertEquals(99, settings.agents)
        assertEquals(0, settings.synthesisAgents)
        assertEquals(0, settings.followUpDelay)
        assertEquals("parallel", settings.execution)
        assertEquals(99, settings.maxParallel)
        assertEquals(99, settings.subagentsLimit)
        assertEquals("reasoning_tokens", settings.recommendBy)
        assertEquals("automatic", settings.fastMode)
        assertEquals("automatic", settings.synthesisFastMode)
    }

    @Test
    fun normalizesAgentCountsToTheSharedEngineeringGuard() {
        val settings = RemoteRunSettings(
            agents = MAX_AGENT_COUNT + 1,
            synthesisAgents = MAX_AGENT_COUNT + 1,
            maxParallel = MAX_AGENT_COUNT + 1,
            subagentsLimit = MAX_AGENT_COUNT + 1,
        ).normalized()

        assertEquals(MAX_AGENT_COUNT, settings.agents)
        assertEquals(MAX_AGENT_COUNT, settings.synthesisAgents)
        assertEquals(MAX_AGENT_COUNT, settings.maxParallel)
        assertEquals(MAX_AGENT_COUNT, settings.subagentsLimit)
    }

    @Test
    fun defaultConversationSettingsNeverCarryWorkspaceSpecificState() {
        val defaults = RemoteRunSettings(
            agents = 7,
            resumeSessionId = "session-from-another-workspace",
            resumeIncludeNonInteractive = true,
            runsDir = "/tmp/custom-runs",
            codexBin = "/tmp/custom-codex",
        ).asDefaultConversationSettings()

        assertEquals(7, defaults.agents)
        assertTrue(defaults.configured)
        assertEquals("NO", defaults.resumeSessionId)
        assertFalse(defaults.resumeIncludeNonInteractive)
        assertEquals("", defaults.runsDir)
        assertEquals("codex", defaults.codexBin)
    }

    @Test
    fun explicitNewConversationDoesNotFallBackToTheLastSession() {
        assertEquals(
            "",
            effectiveResumeSessionId(
                RemoteRunSettings(resumeSessionId = "NO"),
                "session-old",
            ),
        )
        assertEquals(
            "session-old",
            effectiveResumeSessionId(RemoteRunSettings(), "session-old"),
        )
    }

    @Test
    fun recognizesEveryTerminalRunStateCaseInsensitively() {
        listOf(
            "completed",
            "FAILED",
            "cancelled",
            "canceled",
            "expired",
            "interrupted",
            "cleanup_failed",
            "sync_ambiguous",
        ).forEach { assertTrue(it, isTerminalRunStatus(it)) }
        assertFalse(isTerminalRunStatus("running"))
    }

    @Test
    fun onlyOffersAgentStopAfterTheAgentIsActuallyRunning() {
        assertTrue(isAgentStopAvailable("RUNNING"))
        listOf("starting", "copying", "queued", "stopping", "success").forEach {
            assertFalse(it, isAgentStopAvailable(it))
        }
        assertTrue(isActiveAgentStatus("Starting"))
        assertTrue(isTerminalAgentStatus("CANCELED"))
    }

    @Test
    fun finishedAgentsDoNotBecomeRunningAgainWhileTheRunTransitions() {
        val finished = MainUiState(
            visibleRunId = "run-1",
            runStatus = "running",
            runSynthesisAgents = 0,
            selectedAgent = 1,
            recommendedAgent = 1,
            agentSummaries = listOf(
                agent(1).copy(canContinue = false, workspaceAvailable = true),
            ),
        )

        assertFalse(runAwaitingAgentResults(finished))
        assertEquals(1, continuationSourceAgent(finished)?.number)
        assertEquals("全部完成", compactRunProgressLabel(finished))
        assertEquals(
            "正在采用结果",
            compactRunProgressLabel(finished.copy(runStatus = "finalizing")),
        )
        assertEquals(
            SystemBlue,
            runSummaryColor(finished.copy(runStatus = "finalizing")),
        )
        assertFalse(continuationStopsRunningAgents(finished))
    }

    @Test
    fun composerWarnsAboutStoppingAgentsOnlyWhileOtherAgentsAreStillRunning() {
        val running = MainUiState(
            visibleRunId = "run-1",
            runStatus = "running",
            selectedAgent = 1,
            agentSummaries = listOf(
                agent(1).copy(canContinue = true),
                agent(2).copy(status = "running", canContinue = false),
            ),
        )

        assertTrue(continuationStopsRunningAgents(running))
        assertFalse(
            continuationStopsRunningAgents(
                running.copy(runStatus = "completed", finalizedAgent = 1),
            ),
        )
        assertFalse(
            continuationStopsRunningAgents(
                running.copy(
                    followUpQueue = listOf(
                        QueuedFollowUp(id = "queued-1", prompt = "next"),
                    ),
                ),
            ),
        )
    }

    @Test
    fun configuredSynthesisKeepsWaitingUntilSynthesisAgentsAppear() {
        val primaryFinished = MainUiState(
            visibleRunId = "run-1",
            runStatus = "running",
            runSynthesisAgents = 2,
            agentSummaries = listOf(agent(1), agent(2)),
        )

        assertTrue(runAwaitingAgentResults(primaryFinished))
        assertFalse(
            runAwaitingAgentResults(
                primaryFinished.copy(
                    agentSummaries = primaryFinished.agentSummaries +
                        agent(3).copy(role = "synthesis"),
                ),
            ),
        )
    }

    @Test
    fun terminalTurnWithoutContinuationCannotUseAnotherConversationsDefault() {
        listOf(
            "stopped", "cancelled", "canceled", "interrupted", "failed", "error",
            "expired", "discarded", "cleanup_failed", "sync_ambiguous", "completed", "finalized",
        ).forEach { status ->
            val state = terminalState(
                agents = listOf(agent(1, canContinue = false).copy(status = "killed")),
            ).copy(
                visibleConversationId = "stopped-conversation",
                runStatus = status,
                runSettings = RemoteRunSettings(resumeSessionId = "unrelated-old-session"),
                prompt = "Keep this draft",
            )
            assertNotNull(status, promptContinuationBlockReason(state))
            assertEquals("Keep this draft", state.prompt)
        }
    }

    @Test
    fun followUpComposerStaysAvailableWhilePreviousResultIsSaving() {
        val saving = terminalState(agents = listOf(agent(1))).copy(
            runStatus = "finalizing", runActionInProgress = true,
            prompt = "A new question while saving",
        )
        assertTrue(canPersistFollowUp(saving))
        assertTrue(canSubmitComposer(saving))
        assertFalse(canSubmitComposer(saving.copy(visibleRunId = null)))
        assertFalse(canSubmitComposer(saving.copy(prompt = " ")))
    }

    @Test
    fun validContinuationAndActiveQueuesRemainAvailable() {
        val state = terminalState(agents = listOf(agent(1)))
        assertNull(promptContinuationBlockReason(state))
        assertNull(promptContinuationBlockReason(state.copy(runStatus = "finalized", finalizedAgent = 1)))
        assertNull(promptContinuationBlockReason(state.copy(
            runStatus = "running",
            agentSummaries = listOf(agent(1, canContinue = false).copy(status = "running")),
        )))
    }

    @Test
    fun explicitNewConversationAndImportedHistoryDoNotRequireAnAgent() {
        assertNull(promptContinuationBlockReason(MainUiState(
            runSettings = RemoteRunSettings(resumeSessionId = "NO"),
        )))
        assertNull(promptContinuationBlockReason(MainUiState(
            visibleConversationId = "imported-history",
            runSettings = RemoteRunSettings(resumeSessionId = "selected-history-session"),
        )))
    }

    @Test
    fun transientFollowUpFailuresDoNotConsumePermanentRejectionAttempts() {
        val offlineForAWhile = PersistedQueuedFollowUp(
            id = "queue-1",
            prompt = "continue",
            failureCount = 20,
            attempt = 0,
        )

        val firstRemoteRejection = offlineForAWhile.afterDispatchFailure(
            remoteRejected = true,
            nowMillis = 1_000,
            errorMessage = "temporarily rejected",
        )

        assertEquals(1, firstRemoteRejection.attempt)
        assertEquals(21, firstRemoteRejection.failureCount)
        assertFalse(firstRemoteRejection.paused)
        assertTrue(firstRemoteRejection.retryAtMillis > 1_000)
    }

    @Test
    fun conversationFollowsOutputUntilTheUserScrollsAway() {
        assertTrue(
            nextConversationFollowLatest(
                current = true,
                scrolling = false,
                programmatic = false,
                atEnd = false,
            )
        )
        assertFalse(
            nextConversationFollowLatest(
                current = true,
                scrolling = true,
                programmatic = false,
                atEnd = true,
            )
        )
        assertFalse(
            nextConversationFollowLatest(
                current = false,
                scrolling = false,
                programmatic = false,
                atEnd = false,
            )
        )
        assertTrue(
            nextConversationFollowLatest(
                current = false,
                scrolling = false,
                programmatic = false,
                atEnd = true,
            )
        )
        assertTrue(
            nextConversationFollowLatest(
                current = true,
                scrolling = true,
                programmatic = true,
                atEnd = false,
            )
        )
    }

    @Test
    fun emptyConversationLayoutDoesNotDiscardTheRestoredReadingPosition() {
        assertFalse(
            conversationIsAtEnd(
                totalItemsCount = 0,
                lastVisibleIndex = null,
                lastVisibleEnd = null,
                viewportEndOffset = 0,
            )
        )
        assertTrue(
            conversationIsAtEnd(
                totalItemsCount = 8,
                lastVisibleIndex = 7,
                lastVisibleEnd = 600,
                viewportEndOffset = 600,
            )
        )
    }

    @Test
    fun jumpToLatestOnlyAppearsWhenTheViewportIsFarFromTheEnd() {
        assertFalse(
            conversationShouldShowJumpToLatest(
                scrollOffset = 1_900,
                contentSize = 3_000,
                viewportSize = 1_000,
            )
        )
        assertFalse(
            conversationShouldShowJumpToLatest(
                scrollOffset = 1_500,
                contentSize = 3_000,
                viewportSize = 1_000,
            )
        )
        assertTrue(
            conversationShouldShowJumpToLatest(
                scrollOffset = 1_499,
                contentSize = 3_000,
                viewportSize = 1_000,
            )
        )
        assertFalse(
            conversationShouldShowJumpToLatest(
                scrollOffset = 0,
                contentSize = 800,
                viewportSize = 1_000,
            )
        )
    }

    @Test
    fun readsThePromotedSessionFromAnAcceptedAgent() {
        val result = buildJsonObject {
            put("session_promotion", buildJsonObject {
                put("session_id", "session-promoted")
            })
        }

        assertEquals("session-promoted", promotedSessionId(result))
    }

    @Test
    fun showsResumeHistoryBeforeAnyRemoteRunExists() {
        val history = listOf(
            MobileTranscriptBlock(
                id = "history-0",
                kind = MobileTranscriptKind.USER,
                text = "之前在 CLI 中的问题",
            )
        )

        assertEquals(
            history,
            conversationBlocks(MainUiState(conversationHistory = history)),
        )
    }

    @Test
    fun keepsAcceptedPromptVisibleBeforeFirstAgentAppears() {
        val history = listOf(
            MobileTranscriptBlock(
                id = "history-0",
                kind = MobileTranscriptKind.OUTPUT,
                text = "previous answer",
            )
        )

        val blocks = conversationBlocks(
            MainUiState(
                visibleRunId = "run-starting",
                runStatus = "starting",
                runPrompt = "new question",
                conversationHistory = history,
                selectedAgent = null,
                agentSummaries = emptyList(),
            )
        )

        assertEquals(listOf("previous answer", "new question"), blocks.map { it.text })
        assertEquals(MobileTranscriptKind.USER, blocks.last().kind)
        assertEquals("current-prompt", blocks.last().id)
    }

    @Test
    fun appendsOneDurablePendingPromptAfterExistingConversation() {
        val history = listOf(
            MobileTranscriptBlock(
                id = "history-0",
                kind = MobileTranscriptKind.OUTPUT,
                text = "previous answer",
            )
        )
        val submission = PendingPromptSubmission(
            id = "submission-1",
            macId = "mac-1",
            workspaceId = "workspace-1",
            prompt = "next question",
            settings = RemoteRunSettings(),
        )

        val blocks = conversationBlocks(
            MainUiState(
                conversationHistory = history,
                pendingSubmission = submission,
            )
        )

        assertEquals(2, blocks.size)
        assertEquals(history.single().copy(isHistorical = true, isFinalAnswer = true), blocks.first())
        assertEquals("current-prompt", blocks.last().id)
        assertEquals(MobileTranscriptKind.USER, blocks.last().kind)
        assertEquals("next question", blocks.last().text)
        assertEquals("正在提交", blocks.last().status)
    }

    @Test
    fun pendingPromptFreezesOnlyTheSelectedConversationBranch() {
        val submission = PendingPromptSubmission(
            id = "submission-2",
            macId = "mac-1",
            workspaceId = "workspace-1",
            prompt = "next question",
            settings = RemoteRunSettings(),
        )
        val state = MainUiState(
            visibleRunId = "run-1",
            runPrompt = "current question",
            selectedAgent = 2,
            agentSummaries = listOf(
                agent(1),
                agent(2),
            ),
            agentTranscripts = mapOf(
                1 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock("wrong", MobileTranscriptKind.OUTPUT, "wrong branch"),
                    ),
                ),
                2 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock("thought", MobileTranscriptKind.THOUGHT, "private work"),
                        MobileTranscriptBlock("update", MobileTranscriptKind.OUTPUT, "progress update"),
                        MobileTranscriptBlock("command", MobileTranscriptKind.COMMAND, "rg --files"),
                        MobileTranscriptBlock("answer", MobileTranscriptKind.OUTPUT, "chosen answer"),
                    ),
                ),
            ),
            pendingSubmission = submission,
        )

        val blocks = conversationBlocks(state)

        assertEquals(
            listOf("current question", "progress update", "chosen answer", "next question"),
            blocks.map { it.text },
        )
        assertTrue(blocks.dropLast(1).all { it.isHistorical })
        assertFalse(blocks.last().isHistorical)
        assertEquals("current-prompt", blocks.last().id)
        assertFalse(blocks.any { it.text == "wrong branch" || it.text == "private work" || it.text == "rg --files" })
        assertNull(conversationAgentSwitchState(state))
    }

    @Test
    fun pendingPromptNeverExposesTheInternalRetryStateInConversation() {
        val submission = PendingPromptSubmission(
            id = "submission-retry",
            macId = "mac-1",
            workspaceId = "workspace-1",
            prompt = "please retry",
            settings = RemoteRunSettings(),
            status = PendingPromptSubmission.STATUS_RETRYING,
        )

        val block = conversationBlocks(
            MainUiState(pendingSubmission = submission),
        ).single()

        assertEquals("正在提交", block.status)
    }

    @Test
    fun currentReplyActionTargetsOnlyTheSelectedSuccessfulAgent() {
        val state = terminalState(
            selectedAgent = 2,
            agents = listOf(agent(1), agent(2)),
        )

        assertEquals(2, currentAcceptableAgent(state))
        assertNull(currentAcceptableAgent(state.copy(selectedAgent = null)))
        assertNull(
            currentAcceptableAgent(
                state.copy(
                    selectedAgent = 2,
                    agentSummaries = listOf(agent(1), agent(2, canContinue = false)),
                ),
            ),
        )
    }

    @Test
    fun singleAgentDoesNotConsumeAConversationRowForSwitching() {
        val single = terminalState(agents = listOf(agent(1)))
        val multiple = terminalState(agents = listOf(agent(1), agent(2)))

        assertNull(conversationAgentSwitchState(single))
        assertEquals(2, conversationAgentSwitchState(multiple)?.agents?.size)
    }

    @Test
    fun completedAgentIsUnreadUntilThatExactBranchHasBeenViewed() {
        val first = agent(1)
        val second = agent(2)
        val state = terminalState(
            selectedAgent = 1,
            agents = listOf(first, second),
        )
        val secondKey = agentConversationPreferenceKey(
            macId = "mac-1",
            workspaceId = "workspace-1",
            runId = "run-1",
            agent = 2,
        )!!

        assertFalse(state.isAgentCompletionUnread(first))
        assertTrue(state.isAgentCompletionUnread(second))
        assertFalse(
            state.copy(viewedAgentCompletions = setOf(secondKey))
                .isAgentCompletionUnread(second)
        )
        assertFalse(
            state.isAgentCompletionUnread(second.copy(status = "running"))
        )
    }

    @Test
    fun workExpansionIsSharedByWorkspaceAndUsesTheGlobalDefault() {
        val key = "mac-1\u001fworkspace-1"
        val legacyKey = agentConversationPreferenceKey(
            macId = "mac-1",
            workspaceId = "workspace-1",
            runId = "run-1",
            agent = 1,
        )!!
        val state = terminalState(agents = listOf(agent(1)))

        assertFalse(state.isWorkspaceWorkExpanded())
        assertTrue(state.copy(workExpandedByDefault = true).isWorkspaceWorkExpanded())
        assertFalse(
            state.copy(workExpansionStates = mapOf(key to false))
                .isWorkspaceWorkExpanded()
        )
        assertTrue(
            state.copy(workExpansionStates = mapOf(key to true))
                .isWorkspaceWorkExpanded()
        )
        assertTrue(
            state.copy(workExpansionStates = mapOf(legacyKey to true))
                .isWorkspaceWorkExpanded()
        )
    }

    @Test
    fun lateCompletionOfAnOlderRunDoesNotCreateUnreadWorkspaceState() {
        assertTrue(shouldMarkRunCompletionUnread("run-2", "run-2"))
        assertTrue(shouldMarkRunCompletionUnread("run-2", null))
        assertFalse(shouldMarkRunCompletionUnread("run-1", "run-2"))
    }

    @Test
    fun currentReplyActionDisappearsAfterFinalization() {
        val state = terminalState(
            selectedAgent = 2,
            agents = listOf(agent(1), agent(2)),
        )

        assertNull(currentAcceptableAgent(state.copy(finalizedAgent = 2)))
    }

    @Test
    fun pendingPromptFreezesEveryMutationOfThePreviousRun() {
        val state = terminalState(
            selectedAgent = 2,
            agents = listOf(agent(1), agent(2)),
        )
        val submission = PendingPromptSubmission(
            id = "submission-next-turn",
            macId = "mac-1",
            workspaceId = "workspace-1",
            prompt = "next question",
            settings = RemoteRunSettings(),
        )

        assertTrue(state.canMutateVisibleRun())
        assertEquals(2, currentAcceptableAgent(state))

        val submitting = state.copy(pendingSubmission = submission)
        assertFalse(submitting.canMutateVisibleRun())
        assertNull(currentAcceptableAgent(submitting))
        assertFalse(state.copy(runActionInProgress = true).canMutateVisibleRun())
    }

    @Test
    fun pendingPromptOverridesThePreviousCompletedRunSummary() {
        val completed = terminalState(
            selectedAgent = 2,
            agents = listOf(agent(1), agent(2)),
        )
        val submission = PendingPromptSubmission(
            id = "submission-next-turn",
            macId = "mac-1",
            workspaceId = "workspace-1",
            prompt = "next question",
            settings = RemoteRunSettings(),
        )
        val submitting = completed.copy(pendingSubmission = submission)

        assertEquals("submitting", runSummaryStatus(submitting))
        assertEquals("正在提交", compactRunProgressLabel(submitting))
        assertEquals(
            "awaiting_confirmation",
            runSummaryStatus(
                submitting.copy(
                    pendingSubmission = submission.copy(
                        status = PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION,
                    ),
                ),
            ),
        )
        assertEquals(
            "failed",
            runSummaryStatus(
                submitting.copy(
                    pendingSubmission = submission.copy(
                        status = PendingPromptSubmission.STATUS_FAILED,
                    ),
                ),
            ),
        )
    }

    @Test
    fun prioritizesLocalWorkspaceActivityOverRemoteRunProgress() {
        val workspace = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "running",
            runWorkerActive = true,
            runAgentsTotal = 4,
            runAgentsFinished = 2,
        )

        assertEquals("运行中 2/4", workspaceRunLabel(workspace))
        assertEquals("排队 2", workspaceRunLabel(workspace, queuedPrompts = 2))
        assertEquals(
            "发送失败",
            workspaceRunLabel(
                workspace,
                pendingPromptState = PendingPromptSubmission.STATUS_FAILED,
            ),
        )
        assertEquals(
            "Running 2/4",
            workspaceRunLabel(workspace, language = AppLanguage.ENGLISH),
        )
    }

    @Test
    fun usesACompactParentFolderToDisambiguateDuplicateWorkspaces() {
        assertEquals(
            "Desktop",
            workspaceLocationHint("/Users/mingliangxu/Desktop/EyeTheia"),
        )
        assertNull(workspaceLocationHint("/EyeTheia"))
        assertNull(workspaceLocationHint("/"))
        assertNull(workspaceLocationHint(""))
    }

    @Test
    fun workspaceSearchMatchesNamesAndHiddenPathsWithoutReorderingResults() {
        val workspaces = listOf(
            RemoteWorkspace("workspace-1", "EyeTheia", "/Users/me/Desktop/EyeTheia"),
            RemoteWorkspace("workspace-2", "Weekly notes", "/Users/me/SiClink周一例会"),
            RemoteWorkspace("workspace-3", "MouseViewDataset", "/Volumes/Data/MouseViewDataset"),
        )

        assertEquals(workspaces, filterWorkspaces(workspaces, "  "))
        assertEquals(listOf(workspaces[0]), filterWorkspaces(workspaces, "eyetheia"))
        assertEquals(listOf(workspaces[1]), filterWorkspaces(workspaces, "SiClink"))
        assertTrue(filterWorkspaces(workspaces, "missing").isEmpty())
    }

    @Test
    fun stoppedWorkspaceUsesAReadableTerminalLabel() {
        val workspace = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "stopped",
        )

        assertEquals("已停止", workspaceRunLabel(workspace))
    }

    @Test
    fun summarizesCompletedWorkspaceAgentFailuresWithoutCallingTheRunFailed() {
        val partiallySuccessful = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "completed",
            runAgentsTotal = 4,
            runAgentsFinished = 4,
            runAgentsFailed = 1,
        )

        assertEquals("已完成 · 1 失败", workspaceRunLabel(partiallySuccessful))
        assertEquals(
            "运行失败",
            workspaceRunLabel(partiallySuccessful.copy(runAgentsFailed = 4)),
        )
        assertEquals(
            "Completed · 1 failed",
            workspaceRunLabel(partiallySuccessful, language = AppLanguage.ENGLISH),
        )
    }

    @Test
    fun workspaceCompletionBadgeUsesTheWorkspaceRunInsteadOfAgentChips() {
        val workspace = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "completed",
        )
        val key = runCompletionPreferenceKey("mac-1", "workspace-1", "run-1")
        val state = MainUiState(
            selectedMacId = "mac-1",
            unreadRunCompletions = setOf(key),
        )

        assertTrue(state.isWorkspaceRunCompletionUnread(workspace))
        assertFalse(
            state.isWorkspaceRunCompletionUnread(workspace.copy(lastRunId = "run-2"))
        )
        assertFalse(
            state.isWorkspaceRunCompletionUnread(
                workspace.copy(runStatus = "starting", runWorkerActive = true)
            )
        )
    }

    @Test
    fun displayModeOverridesOrFollowsTheSystem() {
        assertTrue(usesDarkTheme(AppColorMode.SYSTEM, systemDark = true))
        assertFalse(usesDarkTheme(AppColorMode.SYSTEM, systemDark = false))
        assertFalse(usesDarkTheme(AppColorMode.LIGHT, systemDark = true))
        assertTrue(usesDarkTheme(AppColorMode.DARK, systemDark = false))
    }

    @Test
    fun accentColorRestoresAValidChoiceAndFallsBackToBlue() {
        assertEquals(AppAccentColor.TEAL, AppAccentColor.fromCode("teal"))
        assertEquals(AppAccentColor.BLUE, AppAccentColor.fromCode("unknown"))
    }

    @Test
    fun runToolbarReportsTheWholeRunInsteadOfOnlyTheSelectedAgent() {
        val state = terminalState(
            selectedAgent = 1,
            agents = listOf(
                agent(1),
                agent(2).copy(status = "running"),
                agent(3).copy(status = "queued"),
            ),
        ).copy(runStatus = "completed")

        assertEquals("正在运行中 · 1/3 完成", compactRunProgressLabel(state))
    }

    @Test
    fun runToolbarDistinguishesSuccessFailureAndStoppedAgents() {
        val state = terminalState(
            agents = listOf(
                agent(1),
                agent(2),
                agent(3).copy(status = "failed"),
                agent(4).copy(status = "killed"),
            ),
        )

        assertEquals(
            "已完成 · 2 成功 · 1 失败 · 1 已停止",
            compactRunProgressLabel(state),
        )
        assertEquals(
            "全部完成",
            compactRunProgressLabel(
                state.copy(agentSummaries = listOf(agent(1), agent(2)))
            ),
        )
    }

    @Test
    fun runToolbarNamesFinalizationAndPreparationExplicitly() {
        assertEquals(
            "正在运行中",
            compactRunProgressLabel(MainUiState(runStatus = "copying")),
        )
        assertEquals(
            "正在运行中",
            compactRunProgressLabel(MainUiState(runStatus = "preparing")),
        )
        assertEquals(
            "正在运行中",
            compactRunProgressLabel(MainUiState(runStatus = "finalizing")),
        )
        assertEquals(
            "结果已采用 · Agent 1",
            compactRunProgressLabel(
                terminalState(agents = listOf(agent(1))).copy(
                    runStatus = "finalized",
                    finalizedAgent = 1,
                )
            ),
        )
        assertEquals(
            "Result applied · Agent 1",
            compactRunProgressLabel(
                terminalState(agents = listOf(agent(12))).copy(
                    runStatus = "finalized",
                    finalizedAgent = 12,
                ),
                AppLanguage.ENGLISH,
            ),
        )
        assertEquals(
            "结果已采用 · 综合 1",
            compactRunProgressLabel(
                terminalState(
                    agents = listOf(agent(1), agent(2).copy(role = "synthesis")),
                ).copy(
                    runStatus = "finalized",
                    finalizedAgent = 2,
                )
            ),
        )
        assertEquals(
            "同步待确认",
            compactRunProgressLabel(
                terminalState(agents = listOf(agent(1))).copy(runStatus = "sync_ambiguous")
            ),
        )
        assertEquals(
            "任务失败",
            compactRunProgressLabel(
                terminalState(agents = listOf(agent(1))).copy(runStatus = "failed")
            ),
        )
    }

    @Test
    fun restoresFollowUpCountdownFromItsAbsoluteDeadline() {
        assertEquals(60, followUpCountdownSeconds(160_000L, 100_000L))
        assertEquals(1, followUpCountdownSeconds(100_001L, 100_000L))
        assertEquals(0, followUpCountdownSeconds(99_999L, 100_000L))
    }

    @Test
    fun formatsRecentDirectoryModificationTimes() {
        val now = Instant.parse("2026-08-23T12:00:00Z")
        assertEquals(
            "11:01",
            directoryModifiedLabel(
                "2026-08-23T11:01:00Z",
                now,
                ZoneOffset.UTC,
            ),
        )
        assertEquals(
            "昨天",
            directoryModifiedLabel(
                "2026-08-22T11:01:00Z",
                now,
                ZoneOffset.UTC,
            ),
        )
    }

    @Test
    fun workspaceRemovalBlocksActiveRunsAndWarnsBeforeDiscardingResults() {
        val active = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "running",
            runWorkerActive = true,
        )
        val reviewable = active.copy(
            runStatus = "completed",
            runWorkerActive = false,
        )
        val finalized = reviewable.copy(runStatus = "finalized")

        assertTrue(workspaceRemovalLooksActive(active))
        assertFalse(workspaceRemovalMayDiscardResults(active))
        assertTrue(workspaceRemovalMayDiscardResults(reviewable))
        assertFalse(workspaceRemovalMayDiscardResults(finalized))
    }

    @Test
    fun workspaceOverviewOnlyRestartsDetailPollingWhenItCanChange() {
        val completed = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "completed",
            runWorkerActive = false,
        )

        assertTrue(workspaceNeedsDetailPolling(completed, contextChanged = true))
        assertFalse(workspaceNeedsDetailPolling(completed, contextChanged = false))
        assertTrue(
            workspaceNeedsDetailPolling(
                completed,
                contextChanged = false,
                detailMissing = true,
            )
        )
        assertTrue(
            workspaceNeedsDetailPolling(
                completed,
                contextChanged = false,
                forceRefresh = true,
            )
        )
        assertTrue(
            workspaceNeedsDetailPolling(
                completed.copy(runStatus = "running", runWorkerActive = true),
                contextChanged = false,
            )
        )
        assertFalse(
            workspaceNeedsDetailPolling(
                completed.copy(lastRunId = ""),
                contextChanged = true,
            )
        )
    }

    @Test
    fun everyActiveWorkspaceGetsACompletionMonitorEvenWhenItIsNotSelected() {
        val running = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/tmp/project",
            lastRunId = "run-1",
            runStatus = "running",
            runWorkerActive = true,
        )

        assertTrue(workspaceNeedsCompletionMonitoring(running))
        assertTrue(
            workspaceNeedsCompletionMonitoring(
                running.copy(runStatus = "preparing", runWorkerActive = false),
            )
        )
        assertFalse(
            workspaceNeedsCompletionMonitoring(
                running.copy(runStatus = "completed", runWorkerActive = false),
            )
        )
        assertFalse(workspaceNeedsCompletionMonitoring(running.copy(lastRunId = "")))
    }

    @Test
    fun diffSummaryKeepsOnlyTheFileHeaderInsteadOfThePatchBody() {
        val summary = parseAgentDiffSummary(
            """A  src/New.kt
M  src/Main.kt

2 files changed (1 added, 1 modified, 0 deleted, 0 type changed)

--- a/src/Main.kt
+++ b/src/Main.kt
@@ -1 +1 @@
"""
        )

        assertEquals(
            listOf(
                AgentFileChange("A", "src/New.kt"),
                AgentFileChange("M", "src/Main.kt"),
            ),
            summary.changes,
        )
        assertEquals(2, summary.totalFiles)
        assertFalse(summary.truncated)
    }

    @Test
    fun diffSummaryMarksAnIncompleteLongFileHeaderAsTruncated() {
        val summary = parseAgentDiffSummary("A  one.txt\nM  two.txt")

        assertEquals(2, summary.totalFiles)
        assertTrue(summary.truncated)
    }

    private fun terminalState(
        selectedAgent: Int? = 1,
        recommendedAgent: Int? = 1,
        agents: List<AgentSummary>,
    ) = MainUiState(
        selectedMacId = "mac-1",
        selectedWorkspaceId = "workspace-1",
        visibleRunId = "run-1",
        runStatus = "completed",
        selectedAgent = selectedAgent,
        recommendedAgent = recommendedAgent,
        agentSummaries = agents,
    )

    private fun agent(number: Int, canContinue: Boolean = true) = AgentSummary(
        number = number,
        status = if (canContinue) "success" else "failed",
        reasoningTokens = null,
        seconds = null,
        recommended = false,
        rejected = false,
        workspaceAvailable = canContinue,
        canAccept = canContinue,
        canContinue = canContinue,
    )
}
