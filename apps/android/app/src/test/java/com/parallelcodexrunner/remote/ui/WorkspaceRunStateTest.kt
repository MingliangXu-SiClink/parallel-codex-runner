package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkspaceRunStateTest {
    @Test
    fun coldStartDoesNotPresentPersistedDevicePresenceAsLiveState() {
        val cached = listOf(
            RelayDevice(
                deviceId = "mac-a",
                kind = "mac",
                name = "Mac",
                publicKey = "key",
                status = "paired",
                online = true,
            ),
            RelayDevice(
                deviceId = "mac-b",
                kind = "mac",
                name = "Offline Mac",
                publicKey = "key",
                status = "paired",
                online = false,
            ),
        )

        val restored = cachedDevicesForColdStart(cached)

        assertTrue(restored.all { it.online == null })
        assertEquals(cached.map(RelayDevice::deviceId), restored.map(RelayDevice::deviceId))
    }

    @Test
    fun runShellOnlyLoadsDuringAnActualReadAndKeepsCachedContentVisible() {
        val key = WorkspaceRunKey("mac", "workspace", "run-new")
        val shell = MainUiState(
            selectedMacId = key.macId,
            selectedWorkspaceId = key.workspaceId,
            visibleRunId = key.runId,
            selectedAgent = 1,
            runStatus = "completed",
            conversationReads = mapOf(key.encoded to ConversationReadState(
                metadata = ConversationRead(ConversationReadPhase.LOADING),
            )),
        )

        assertTrue(shell.isVisibleConversationContentLoading())
        assertFalse(shell.copy(runPrompt = "question").isVisibleConversationContentLoading())
        assertFalse(
            shell.copy(
                conversationHistory = listOf(
                    MobileTranscriptBlock("user", MobileTranscriptKind.USER, "question")
                )
            ).isVisibleConversationContentLoading()
        )
        assertFalse(
            shell.copy(
                agentTranscripts = mapOf(
                    1 to MobileAgentTranscript(finalMessage = "answer")
                )
            ).isVisibleConversationContentLoading()
        )
        assertFalse(MainUiState().isVisibleConversationContentLoading())
    }

    @Test
    fun encryptedConversationCacheSurvivesAStoreRestartWithoutTruncatingText() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache").toFile()
        try {
            val keyBytes = ByteArray(32) { index -> (index + 1).toByte() }
            val runKey = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
            val longAnswer = "完整回答".repeat(20_000)
            val snapshot = WorkspaceRunSnapshot(
                eventCount = 88,
                status = "completed",
                selectedAgent = 3,
                conversationHistory = listOf(
                    MobileTranscriptBlock("history", MobileTranscriptKind.USER, "问题"),
                ),
                agentTranscripts = mapOf(
                    3 to MobileAgentTranscript(
                        blocks = listOf(
                            MobileTranscriptBlock(
                                "answer",
                                MobileTranscriptKind.OUTPUT,
                                longAnswer,
                            ),
                        ),
                        cursor = 88,
                        hasMore = false,
                        finalMessageHasMore = false,
                    ),
                ),
            )
            val writer = WorkspaceRunDiskCache(directory, keyBytes)
            writer.writeNow(runKey, snapshot)

            val raw = writer.cacheFileForTest(runKey).readBytes()
            assertTrue(!raw.toString(Charsets.UTF_8).contains("完整回答"))

            val reader = WorkspaceRunDiskCache(directory, keyBytes)
            val restored = reader.load(runKey)

            assertEquals(88, restored?.eventCount)
            assertEquals(longAnswer, restored?.agentTranscripts?.get(3)?.blocks?.single()?.text)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun conversationCacheRemovesWholeWorkspaceWithoutCrossMachineLeakage() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache-remove").toFile()
        try {
            val cache = WorkspaceRunDiskCache(directory, ByteArray(32) { 7 })
            val removed = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
            val retained = WorkspaceRunKey("mac-b", "workspace-a", "run-a")
            cache.writeNow(removed, WorkspaceRunSnapshot(prompt = "remove me"))
            cache.writeNow(retained, WorkspaceRunSnapshot(prompt = "keep me"))

            cache.removeWhereNow { it.belongsToWorkspace("mac-a", "workspace-a") }

            assertNull(cache.load(removed))
            assertEquals("keep me", cache.load(retained)?.prompt)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun encryptedConversationIndexSurvivesRestartAndFollowsWorkspaceRemoval() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-index-cache").toFile()
        try {
            val keyBytes = ByteArray(32) { 9 }
            val writer = WorkspaceRunDiskCache(directory, keyBytes)
            val conversations = listOf(
                ConversationRecord(
                    conversationId = "conversation-a",
                    workspaceId = "workspace-a",
                    title = "Private cached title",
                    updatedAt = "2026-09-03T04:00:00+00:00",
                    activeTurnId = "turn-a",
                    turnIds = listOf("turn-a"),
                ),
            )
            writer.writeConversationIndex("mac-a", "workspace-a", conversations)
            writer.writeConversationIndex("mac-b", "workspace-a", conversations)

            val raw = writer.conversationIndexFileForTest(
                "mac-a",
                "workspace-a",
            ).readBytes()
            assertFalse(raw.toString(Charsets.UTF_8).contains("Private cached title"))

            val reader = WorkspaceRunDiskCache(directory, keyBytes)
            assertEquals(
                conversations,
                reader.loadConversationIndex("mac-a", "workspace-a"),
            )

            reader.removeWhereNow { it.belongsToWorkspace("mac-a", "workspace-a") }

            assertNull(reader.loadConversationIndex("mac-a", "workspace-a"))
            assertEquals(
                conversations,
                reader.loadConversationIndex("mac-b", "workspace-a"),
            )
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun stateStoreHydratesACompleteConversationBeforeNetworkRefresh() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache-hydrate").toFile()
        try {
            val keyBytes = ByteArray(32) { 11 }
            val runKey = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
            WorkspaceRunDiskCache(directory, keyBytes).writeNow(
                runKey,
                WorkspaceRunSnapshot(
                    eventCount = 17,
                    status = "running",
                    prompt = "cached prompt",
                    selectedAgent = 2,
                    agentTranscripts = mapOf(
                        2 to MobileAgentTranscript(
                            blocks = listOf(
                                MobileTranscriptBlock(
                                    "cached-output",
                                    MobileTranscriptKind.OUTPUT,
                                    "cached answer",
                                ),
                            ),
                        ),
                    ),
                ),
            )

            val store = WorkspaceRunStateStore(
                WorkspaceRunDiskCache(directory, keyBytes),
            )
            val restored = store.hydrate(runKey)

            assertEquals("cached prompt", restored?.prompt)
            assertEquals("cached answer", restored?.agentTranscripts?.get(2)?.blocks?.single()?.text)
            assertEquals(restored, store[runKey])
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun conversationCacheFindsTheNewestMatchingRunWithoutCrossContextLeakage() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache-lookup").toFile()
        try {
            val cache = WorkspaceRunDiskCache(directory, ByteArray(32) { 17 })
            val older = WorkspaceRunKey("mac-a", "workspace-a", "pcr-20260101T000000Z-a")
            val newer = WorkspaceRunKey("mac-a", "workspace-a", "pcr-20260101T000001Z-b")
            cache.writeNow(
                older,
                WorkspaceRunSnapshot(conversationId = "conversation-a", prompt = "older"),
            )
            cache.writeNow(
                WorkspaceRunKey("mac-b", "workspace-a", "run-other-mac"),
                WorkspaceRunSnapshot(conversationId = "conversation-a", prompt = "other mac"),
            )
            cache.writeNow(
                WorkspaceRunKey("mac-a", "workspace-b", "run-other-workspace"),
                WorkspaceRunSnapshot(conversationId = "conversation-a", prompt = "other workspace"),
            )
            cache.writeNow(
                newer,
                WorkspaceRunSnapshot(conversationId = "conversation-a", prompt = "newer"),
            )

            val restored = cache.loadConversation("mac-a", "workspace-a", "conversation-a")

            assertEquals(newer, restored?.key)
            assertEquals("newer", restored?.snapshot?.prompt)
            assertNull(cache.loadConversation("mac-a", "workspace-a", "missing"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun stateStoreCanHydrateAConversationWithoutKnowingItsRunId() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache-by-id").toFile()
        try {
            val keyBytes = ByteArray(32) { 19 }
            val runKey = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
            WorkspaceRunDiskCache(directory, keyBytes).writeNow(
                runKey,
                WorkspaceRunSnapshot(
                    eventCount = 23,
                    conversationId = "conversation-a",
                    conversationTitle = "Cached conversation",
                    prompt = "cached prompt",
                ),
            )
            val store = WorkspaceRunStateStore(
                WorkspaceRunDiskCache(directory, keyBytes),
            )

            val restored = store.hydrateConversation(
                macId = "mac-a",
                workspaceId = "workspace-a",
                conversationId = "conversation-a",
            )

            assertEquals(runKey, restored?.key)
            assertEquals("Cached conversation", restored?.snapshot?.conversationTitle)
            assertEquals(restored?.snapshot, store[runKey])
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun identicalSnapshotsDoNotScheduleAnotherCacheWrite() {
        val store = WorkspaceRunStateStore()
        val key = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
        val snapshot = WorkspaceRunSnapshot(
            eventCount = 12,
            status = "completed",
            prompt = "question",
        )

        assertTrue(store.put(key, snapshot))
        assertEquals(false, store.put(key, snapshot.copy()))
    }

    @Test
    fun visiblePollingRecognizesAnUnchangedRunProjection() {
        val snapshot = WorkspaceRunSnapshot(
            eventCount = 12,
            status = "completed",
            prompt = "question",
            selectedAgent = 1,
            agentTranscripts = mapOf(
                1 to MobileAgentTranscript(finalMessage = "answer"),
            ),
        )
        val projected = MainUiState().projectRun(snapshot)

        assertTrue(projected.hasSameRunProjection(snapshot))
        assertEquals(
            false,
            projected.copy(runPrompt = "stale").hasSameRunProjection(snapshot),
        )
    }

    @Test
    fun staleCursorWithoutCachedTextRestartsTranscriptPaging() {
        val recovered = MobileAgentTranscript(
            cursor = 79,
            hasMore = false,
            finalMessageCursor = 3239,
            finalMessageHasMore = false,
            finalMessageId = "message-id",
        ).recoverMissingCachedContent()

        assertEquals(0, recovered.cursor)
        assertTrue(recovered.hasMore)
        assertEquals(0, recovered.finalMessageCursor)
        assertTrue(recovered.finalMessageHasMore)
        assertEquals("", recovered.finalMessageId)
    }

    @Test
    fun authoritativeSnapshotCanReplaceALargerCachedEventCount() {
        val store = WorkspaceRunStateStore()
        val key = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
        store.put(
            key,
            WorkspaceRunSnapshot(eventCount = 80, status = "completed", prompt = "stale"),
        )

        store.put(
            key,
            checkNotNull(store[key]).copy(
                eventCount = 79,
                prompt = "authoritative",
            ),
        )

        assertEquals(79, store[key]?.eventCount)
        assertEquals("authoritative", store[key]?.prompt)
    }

    @Test
    fun completeCachedTranscriptKeepsItsPagingState() {
        val transcript = MobileAgentTranscript(
            blocks = listOf(
                MobileTranscriptBlock("output", MobileTranscriptKind.OUTPUT, "answer"),
            ),
            cursor = 79,
            hasMore = false,
            finalMessage = "answer",
            finalMessageCursor = 3239,
            finalMessageHasMore = false,
            finalMessageId = "message-id",
        )

        assertEquals(transcript, transcript.recoverMissingCachedContent())
    }

    @Test
    fun corruptConversationCacheIsDiscardedInsteadOfBreakingWorkspaceOpen() = runBlocking {
        val directory = Files.createTempDirectory("pcr-conversation-cache-corrupt").toFile()
        try {
            val cache = WorkspaceRunDiskCache(directory, ByteArray(32) { 13 })
            val runKey = WorkspaceRunKey("mac-a", "workspace-a", "run-a")
            cache.writeNow(runKey, WorkspaceRunSnapshot(prompt = "valid"))
            val file = cache.cacheFileForTest(runKey)
            file.writeText("not a conversation cache")

            assertNull(cache.load(runKey))
            assertTrue(!file.exists())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun terminalPollingContinuesUntilTheSelectedTranscriptIsComplete() {
        val pending = WorkspaceRunSnapshot(
            status = "completed",
            selectedAgent = 2,
            agentTranscripts = mapOf(
                2 to MobileAgentTranscript(hasMore = true),
            ),
        )
        val complete = pending.copy(
            agentTranscripts = mapOf(
                2 to MobileAgentTranscript(hasMore = false, finalMessageHasMore = false),
            ),
        )

        assertEquals(true, selectedAgentDetailHasMore(pending))
        assertEquals(false, selectedAgentDetailHasMore(complete))
    }

    @Test
    fun encodedKeyRoundTripsAndRejectsMalformedValues() {
        val key = WorkspaceRunKey("mac-a", "workspace-a", "run-a")

        assertEquals(key, WorkspaceRunKey.decode(key.encoded))
        assertNull(WorkspaceRunKey.decode("mac-a\u001fworkspace-a"))
        assertNull(WorkspaceRunKey.decode("mac-a\u001f\u001frun-a"))
    }

    @Test
    fun notificationNavigationOpensTheNotifiedRunInsteadOfTheLatestRun() {
        val navigation = PendingRunNavigation("mac-1", "workspace-1", "run-old")

        assertEquals(
            "run-old",
            displayedRunIdForNavigation(
                macId = "mac-1",
                workspaceId = "workspace-1",
                latestRunId = "run-new",
                navigation = navigation,
            ),
        )
        assertEquals(
            "run-new",
            displayedRunIdForNavigation(
                macId = "mac-1",
                workspaceId = "workspace-2",
                latestRunId = "run-new",
                navigation = navigation,
            ),
        )
    }

    @Test
    fun notificationNavigationDetectsEveryVisibleContextChange() {
        val current = MainUiState(
            selectedMacId = "mac-1",
            selectedWorkspaceId = "workspace-1",
            visibleRunId = "run-1",
        )

        assertFalse(
            notificationNavigationChangesContext(
                current,
                PendingRunNavigation("mac-1", "workspace-1", "run-1"),
            )
        )
        assertTrue(
            notificationNavigationChangesContext(
                current,
                PendingRunNavigation("mac-1", "workspace-1", "run-2"),
            )
        )
        assertTrue(
            notificationNavigationChangesContext(
                current,
                PendingRunNavigation("mac-1", "workspace-2", "run-1"),
            )
        )
        assertTrue(
            notificationNavigationChangesContext(
                current,
                PendingRunNavigation("mac-2", "workspace-1", "run-1"),
            )
        )
    }

    @Test
    fun uncachedRunProjectionCannotRetainThePreviousConversation() {
        val previous = MainUiState(
            visibleConversationId = "conversation-old",
            visibleConversationTitle = "Old conversation",
            runPrompt = "old prompt",
            conversationHistory = listOf(
                MobileTranscriptBlock(
                    id = "old-user",
                    kind = MobileTranscriptKind.USER,
                    text = "old message",
                )
            ),
            agentTranscripts = mapOf(1 to MobileAgentTranscript(finalMessage = "old answer")),
        )

        val target = previous.projectRun(snapshot = null).copy(visibleRunId = "run-new")

        assertEquals("run-new", target.visibleRunId)
        assertNull(target.visibleConversationId)
        assertEquals("", target.visibleConversationTitle)
        assertEquals("", target.runPrompt)
        assertTrue(target.conversationHistory.isEmpty())
        assertTrue(target.agentTranscripts.isEmpty())
    }

    @Test
    fun finalizedWorkspaceStillRestoresItsLastPcrRun() {
        val workspace = RemoteWorkspace(
            workspaceId = "workspace-1",
            name = "Project",
            path = "/project",
            lastRunId = "run-finalized",
            lastResumeSessionId = "resume-session",
            runStatus = "finalized",
        )

        assertEquals("run-finalized", latestWorkspaceRunId(workspace))
        assertNull(latestWorkspaceRunId(workspace, conversationDetached = true))
    }

    @Test
    fun newConversationRemovesThePreviousRunProjection() {
        val detached = MainUiState(
            visibleRunId = "run-1",
            runStatus = "completed",
            canAddAgents = true,
            canUpdateSynthesis = true,
            runPrompt = "old prompt",
            agentSummaries = listOf(
                AgentSummary(
                    number = 1,
                    status = "success",
                    reasoningTokens = 10,
                    seconds = 1.0,
                    recommended = true,
                    rejected = false,
                    workspaceAvailable = true,
                    canAccept = true,
                    canContinue = true,
                )
            ),
            selectedAgent = 1,
            conversationHistory = listOf(
                MobileTranscriptBlock("old", MobileTranscriptKind.USER, "old prompt")
            ),
            agentTranscripts = mapOf(1 to MobileAgentTranscript()),
        ).withoutVisibleConversation()

        assertNull(detached.visibleRunId)
        assertEquals("", detached.runStatus)
        assertEquals(emptyList<AgentSummary>(), detached.agentSummaries)
        assertEquals(emptyList<MobileTranscriptBlock>(), detached.conversationHistory)
        assertEquals(emptyMap<Int, MobileAgentTranscript>(), detached.agentTranscripts)
    }

    @Test
    fun liveUpdateWarningOnlyAppliesToItsMachineWorkspaceAndRun() {
        val key = WorkspaceRunKey("mac-1", "workspace-1", "run-1")
        val state = MainUiState(
            selectedMacId = key.macId,
            selectedWorkspaceId = key.workspaceId,
            visibleRunId = key.runId,
            liveUpdateInterruptedContext = key.encoded,
        )

        assertTrue(state.isVisibleRunUpdateInterrupted())
        assertFalse(state.copy(visibleRunId = "run-2").isVisibleRunUpdateInterrupted())
        assertFalse(
            state.copy(selectedWorkspaceId = "workspace-2")
                .isVisibleRunUpdateInterrupted()
        )
        assertFalse(state.copy(selectedMacId = "mac-2").isVisibleRunUpdateInterrupted())
    }

    @Test
    fun snapshotsRemainIndependentAcrossMachinesAndWorkspaces() {
        val store = WorkspaceRunStateStore()
        val first = WorkspaceRunKey("mac-1", "workspace-1", "run-1")
        val second = WorkspaceRunKey("mac-1", "workspace-2", "run-2")
        val third = WorkspaceRunKey("mac-2", "workspace-1", "run-3")

        store.put(first, WorkspaceRunSnapshot(status = "running", selectedAgent = 1))
        store.put(second, WorkspaceRunSnapshot(status = "completed", selectedAgent = 2))
        store.put(third, WorkspaceRunSnapshot(status = "failed", selectedAgent = 3))

        assertEquals("running", store[first]?.status)
        assertEquals(2, store[second]?.selectedAgent)
        assertEquals("failed", store[third]?.status)

        store.removeWorkspace("mac-1", "workspace-1")

        assertNull(store[first])
        assertEquals("completed", store[second]?.status)
        assertEquals("failed", store[third]?.status)
    }

    @Test
    fun projectingAStoredRunRestoresTheCompleteVisibleConversation() {
        val agent = AgentSummary(
            number = 4,
            status = "success",
            reasoningTokens = 1200,
            seconds = 12.5,
            recommended = true,
            rejected = false,
            workspaceAvailable = true,
            canAccept = true,
            canContinue = true,
        )
        val history = listOf(
            MobileTranscriptBlock("history-1", MobileTranscriptKind.USER, "Earlier prompt"),
        )
        val transcript = MobileAgentTranscript(
            blocks = listOf(
                MobileTranscriptBlock("output-1", MobileTranscriptKind.OUTPUT, "Answer"),
            ),
            cursor = 8,
            hasMore = false,
        )
        val snapshot = WorkspaceRunSnapshot(
            status = "completed",
            canAddAgents = true,
            canUpdateSynthesis = true,
            prompt = "Current prompt",
            agents = listOf(agent),
            selectedAgent = 4,
            recommendedAgent = 4,
            conversationHistory = history,
            agentTranscripts = mapOf(4 to transcript),
        )

        val projected = MainUiState(visibleRunId = "run-1").projectRun(snapshot)

        assertEquals("completed", projected.runStatus)
        assertEquals("Current prompt", projected.runPrompt)
        assertEquals(listOf(agent), projected.agentSummaries)
        assertEquals(4, projected.selectedAgent)
        assertEquals(history, projected.conversationHistory)
        assertEquals(transcript, projected.agentTranscripts[4])
    }

    @Test
    fun capturePreservesVisibleRunBeforeChangingWorkspace() {
        val store = WorkspaceRunStateStore()
        val key = WorkspaceRunKey("mac-1", "workspace-1", "run-1")
        val state = MainUiState(
            visibleRunId = key.runId,
            runStatus = "running",
            runPrompt = "Inspect the project",
            selectedAgent = 2,
            conversationHistory = listOf(
                MobileTranscriptBlock("history", MobileTranscriptKind.USER, "Previous"),
            ),
        )

        store.capture(key, state)

        assertEquals("running", store[key]?.status)
        assertEquals("Inspect the project", store[key]?.prompt)
        assertEquals(2, store[key]?.selectedAgent)
        assertEquals(1, store[key]?.conversationHistory?.size)
    }

    @Test
    fun captureKeepsTheAuthoritativeEventSequence() {
        val store = WorkspaceRunStateStore()
        val key = WorkspaceRunKey("mac-1", "workspace-1", "run-1")
        store.put(key, WorkspaceRunSnapshot(eventCount = 42, status = "running"))

        store.capture(key, MainUiState(runStatus = "completed"))

        assertEquals(42, store[key]?.eventCount)
        assertEquals("completed", store[key]?.status)
    }

    @Test
    fun authoritativeHistoryExtendsTheOptimisticConversationAndStabilizesIds() {
        val current = listOf(
            MobileTranscriptBlock("carried-user", MobileTranscriptKind.USER, "first"),
            MobileTranscriptBlock("carried-output", MobileTranscriptKind.OUTPUT, "answer"),
        )
        val authoritative = listOf(
            MobileTranscriptBlock("history-0", MobileTranscriptKind.USER, "first"),
            MobileTranscriptBlock("history-1", MobileTranscriptKind.OUTPUT, "answer"),
            MobileTranscriptBlock("history-2", MobileTranscriptKind.USER, "follow-up"),
        )

        val merged = mergeConversationHistory(current, authoritative)

        assertEquals(listOf("history-0", "history-1", "history-2"), merged.map { it.id })
        assertEquals(listOf("first", "answer", "follow-up"), merged.map { it.text })
    }

    @Test
    fun shorterAuthoritativeHistoryDoesNotEraseOptimisticBlocks() {
        val current = listOf(
            MobileTranscriptBlock("carried-user", MobileTranscriptKind.USER, "first"),
            MobileTranscriptBlock("carried-output", MobileTranscriptKind.OUTPUT, "answer"),
        )
        val authoritative = listOf(
            MobileTranscriptBlock("history-0", MobileTranscriptKind.USER, "first"),
        )

        assertEquals(current, mergeConversationHistory(current, authoritative))
    }

    @Test
    fun terminalRunCannotLeaveTheDetailAgentRunning() {
        val running = AgentSummary(
            number = 1,
            status = "running",
            reasoningTokens = null,
            seconds = null,
            recommended = false,
            rejected = false,
            workspaceAvailable = true,
            canAccept = false,
            canContinue = false,
        )

        val reconciled = reconcileTerminalRunAgents(
            runStatus = "completed",
            agents = listOf(running),
        )

        assertEquals("failed", reconciled.single().status)
        assertEquals(
            "running",
            reconcileTerminalRunAgents("running", listOf(running)).single().status,
        )
    }

    @Test
    fun terminalRunSealsDetailEvenWhileWorkerIsStillCleaningUp() {
        val running = AgentSummary(
            number = 6,
            status = "running",
            reasoningTokens = 42,
            seconds = 3.0,
            recommended = true,
            rejected = false,
            workspaceAvailable = true,
            canAccept = false,
            canContinue = false,
        )

        assertEquals(
            "failed",
            reconcileTerminalRunAgents("completed", listOf(running)).single().status,
        )
    }

    @Test
    fun successfulAgentWithoutCachedAnswerKeepsDetailPollingAlive() {
        val agent = AgentSummary(
            number = 1,
            status = "success",
            reasoningTokens = 12,
            seconds = 2.0,
            recommended = true,
            rejected = false,
            workspaceAvailable = true,
            canAccept = true,
            canContinue = true,
        )
        val missing = WorkspaceRunSnapshot(
            status = "completed",
            agents = listOf(agent),
            selectedAgent = 1,
            recommendedAgent = 1,
        )

        assertTrue(missing.selectedAgentDetailMissing())
        assertTrue(selectedAgentDetailHasMore(missing))

        val loaded = missing.copy(
            agentTranscripts = mapOf(
                1 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock(
                            "answer",
                            MobileTranscriptKind.OUTPUT,
                            "完整回答",
                        ),
                    ),
                    hasMore = false,
                    finalMessageHasMore = false,
                ),
            ),
        )

        assertTrue(!loaded.selectedAgentDetailMissing())
        assertTrue(!selectedAgentDetailHasMore(loaded))
    }

    @Test
    fun legacyReadErrorMigrationKeepsCachedAnswerAndRestoresPaging() {
        val cached = MobileAgentTranscript(
            blocks = listOf(
                MobileTranscriptBlock(
                    "answer",
                    MobileTranscriptKind.OUTPUT,
                    "已经缓存的回答",
                ),
            ),
            cursor = 50,
            hasMore = false,
            finalMessageHasMore = false,
            error = "Unknown PCR run: run-a",
        )

        val recovered = WorkspaceRunSnapshot(agentTranscripts = mapOf(1 to cached))
            .recoverLegacyReadErrors().agentTranscripts.getValue(1)

        assertEquals(cached.blocks, recovered.blocks)
        assertEquals(cached.cursor, recovered.cursor)
        assertTrue(recovered.hasMore)
        assertTrue(recovered.finalMessageHasMore)
        assertEquals("", recovered.error)
    }

    @Test
    fun legacyReadErrorMigrationNeverRemovesAuthoritativeOrUnrecognizedAgentErrors() {
        val actual = MobileAgentTranscript(error = "Unknown PCR run: reported by Agent", errorFromAgentResponse = true)
        val unknown = MobileAgentTranscript(error = "Compilation failed")
        val original = WorkspaceRunSnapshot(agentTranscripts = mapOf(1 to actual, 2 to unknown))
        assertEquals(original, original.recoverLegacyReadErrors())
    }

    @Test
    fun authoritativeHistoryDropsAttemptOnlyWorkFromAcceptedTurns() {
        val current = listOf(
            MobileTranscriptBlock("user", MobileTranscriptKind.USER, "question"),
            MobileTranscriptBlock("thought", MobileTranscriptKind.THOUGHT, "reasoning"),
            MobileTranscriptBlock("command", MobileTranscriptKind.COMMAND, "rg issue"),
            MobileTranscriptBlock("answer", MobileTranscriptKind.OUTPUT, "answer"),
        )
        val authoritative = listOf(
            MobileTranscriptBlock("history-0", MobileTranscriptKind.USER, "question"),
            MobileTranscriptBlock("history-1", MobileTranscriptKind.OUTPUT, "answer"),
        )

        assertEquals(authoritative, mergeConversationHistory(current, authoritative))
    }

    @Test
    fun repeatedConversationTextKeepsAuthoritativeTurnIdentity() {
        val current = listOf(
            MobileTranscriptBlock("carried-0", MobileTranscriptKind.USER, "继续"),
            MobileTranscriptBlock("carried-1", MobileTranscriptKind.OUTPUT, "完成"),
            MobileTranscriptBlock("carried-2", MobileTranscriptKind.USER, "继续"),
            MobileTranscriptBlock("carried-3", MobileTranscriptKind.OUTPUT, "完成"),
        )
        val authoritative = listOf(
            MobileTranscriptBlock("turn-a-user", MobileTranscriptKind.USER, "继续"),
            MobileTranscriptBlock("turn-a-answer", MobileTranscriptKind.OUTPUT, "完成"),
            MobileTranscriptBlock("turn-b-user", MobileTranscriptKind.USER, "继续"),
            MobileTranscriptBlock("turn-b-answer", MobileTranscriptKind.OUTPUT, "完成"),
        )

        assertEquals(authoritative, mergeConversationHistory(current, authoritative))
    }

    @Test
    fun continuationConversationKeepsTheSelectedAgentBranch() {
        val history = listOf(
            MobileTranscriptBlock("old-user", MobileTranscriptKind.USER, "earlier"),
        )
        val snapshot = WorkspaceRunSnapshot(
            prompt = "current question",
            selectedAgent = 2,
            agentTranscripts = mapOf(
                1 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock("one", MobileTranscriptKind.OUTPUT, "wrong branch"),
                    ),
                ),
                2 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock(
                            "thought",
                            MobileTranscriptKind.THOUGHT,
                            "private working notes",
                        ),
                        MobileTranscriptBlock(
                            "command",
                            MobileTranscriptKind.COMMAND,
                            "rg issue",
                        ),
                        MobileTranscriptBlock(
                            "update",
                            MobileTranscriptKind.OUTPUT,
                            "intermediate update",
                        ),
                        MobileTranscriptBlock("two", MobileTranscriptKind.OUTPUT, "chosen branch"),
                    ),
                ),
            ),
            conversationHistory = history,
        )

        val carried = continuationConversation(snapshot, sourceAgent = 2)

        assertEquals(
            listOf("earlier", "current question", "intermediate update", "chosen branch"),
            carried.map { it.text },
        )
        assertEquals(
            listOf(
                MobileTranscriptKind.USER,
                MobileTranscriptKind.USER,
                MobileTranscriptKind.OUTPUT,
                MobileTranscriptKind.OUTPUT,
            ),
            carried.map { it.kind },
        )
        assertTrue(carried.all { it.isHistorical })
        assertTrue(carried.last().isFinalAnswer)
    }

    @Test
    fun storageConfirmationsRemainBoundToTheirRunContext() {
        val store = PendingStorageActionStore()
        val mac = RelayDevice("mac-1", "mac", "Mac", "key", "active")
        val first = RunMutationContext(mac, "workspace-1", "run-1", RemoteRunSettings())
        val second = RunMutationContext(mac, "workspace-2", "run-2", RemoteRunSettings())
        val action = PendingStorageAction.More(
            count = 3,
            context = first,
            message = "需要确认",
        )

        store.put(action)

        assertEquals(action, store[first])
        assertNull(store[second])
        store.removeWhere { it.belongsToWorkspace("mac-1", "workspace-2") }
        assertEquals(action, store[first])
        store.removeWhere { it.belongsToWorkspace("mac-1", "workspace-1") }
        assertNull(store[first])
    }
}
