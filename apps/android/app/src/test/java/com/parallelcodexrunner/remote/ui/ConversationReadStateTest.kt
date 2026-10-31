package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteCommandException
import java.io.IOException
import java.nio.file.Files
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withContext
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class ConversationReadStateTest {
    @Test fun liveAgentBacklogPagesDoNotWaitForTheAgentToFinishOrAnotherPollInterval() {
        val pending = MobileAgentTranscript(cursor = 100, hasMore = true, finalMessageHasMore = false)
        assertTrue(shouldReadNextAgentPage(pending, true, 1, 4))
        assertTrue(shouldReadNextAgentPage(pending.copy(hasMore = false, finalMessageHasMore = true), true, 3, 4))
        assertFalse(shouldReadNextAgentPage(pending, true, 4, 4))
        assertFalse(shouldReadNextAgentPage(pending, false, 1, 4))
        assertFalse(shouldReadNextAgentPage(pending.copy(hasMore = false), true, 1, 4))
        assertFalse(shouldReadNextAgentPage(pending, true, 1, 0))
    }

    private val key = WorkspaceRunKey("mac", "workspace", "run")

    @Test fun focusedTranscriptCanAdvanceWhileMetadataAndOtherWorkspaceReadsArePending() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val metadataStarted = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            val background = launch {
                reads.read(key, ConversationReadTarget.Metadata) {
                    metadataStarted.complete(Unit)
                    release.await()
                }
            }
            metadataStarted.await()
            var cursor = 42
            withTimeout(1_000) {
                reads.readShared(owner, key, ConversationReadTarget.Agent(2), pageLimit = 20) {
                    cursor += 1
                }
                reads.readShared(owner, key, ConversationReadTarget.History) { Unit }
            }
            assertEquals(43, cursor)
            assertEquals(ConversationReadPhase.LOADING, reads[key].metadata.phase)
            assertEquals(ConversationReadPhase.READY, reads[key].agents[2]?.phase)
            assertEquals(ConversationReadPhase.READY, reads[key].history.phase)
            release.complete(Unit)
            background.join()
        } finally { owner.cancel() }
    }

    @Test fun returningToHistoryJoinsItsOriginalPagesAndDoesNotBlockCurrentOutput() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val firstPage = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            var issuedIdentities = 0
            val pages = mutableListOf<Int>()
            val initial = launch {
                reads.readShared(owner, key, ConversationReadTarget.History) {
                    issuedIdentities += 1
                    pages += 0
                    firstPage.complete(Unit)
                    release.await()
                    pages += 100
                }
            }
            firstPage.await()
            val requestId = reads[key].history.requestId
            val returning = launch {
                reads.readShared(owner, key, ConversationReadTarget.History) {
                    issuedIdentities += 1
                    fail("Returning navigation must not invalidate the original history pages")
                }
            }
            yield()
            initial.cancelAndJoin()
            withTimeout(1_000) {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { Unit }
                reads.readShared(owner, key.copy(workspaceId = "other"), ConversationReadTarget.History) { Unit }
            }
            assertEquals(requestId, reads[key].history.requestId)
            assertEquals(ConversationReadPhase.LOADING, reads[key].history.phase)
            release.complete(Unit)
            returning.join()
            assertEquals(1, issuedIdentities)
            assertEquals(listOf(0, 100), pages)
            assertEquals(ConversationReadPhase.READY, reads[key].history.phase)
        } finally { owner.cancel() }
    }

    @Test fun removedReaderCannotBorrowItsReplacementsPageBudgetOrReleaseItsSlot() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val oldStarted = CompletableDeferred<Unit>()
            val newStarted = CompletableDeferred<Unit>()
            val releaseOld = CompletableDeferred<Unit>()
            val releaseNew = CompletableDeferred<Unit>()
            val old = launch {
                runCatching {
                    reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 4) { limit ->
                        withContext(NonCancellable) {
                            oldStarted.complete(Unit)
                            releaseOld.await()
                            assertEquals(4, limit())
                        }
                    }
                }
            }
            oldStarted.await()
            reads.removeWhere { it == key }
            val replacement = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 20) { limit ->
                    newStarted.complete(Unit)
                    releaseNew.await()
                    assertEquals(20, limit())
                }
            }
            newStarted.await()
            val replacementId = reads[key].agents[1]!!.requestId
            releaseOld.complete(Unit)
            old.join()
            assertEquals(replacementId, reads[key].agents[1]!!.requestId)
            assertEquals(ConversationReadPhase.LOADING, reads[key].agents[1]!!.phase)
            val joined = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 4) {
                    fail("Late completion must not evict the replacement")
                }
            }
            yield()
            releaseNew.complete(Unit)
            replacement.join()
            joined.join()
            assertEquals(ConversationReadPhase.READY, reads[key].agents[1]!!.phase)
        } finally { owner.cancel() }
    }

    @Test fun selectingAnAgentPromotesTheExistingPollWithoutRestartingItsCursor() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val started = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            var cursor = 80
            var requests = 0
            val poll = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 4) { limit ->
                    requests += 1
                    assertEquals(4, limit())
                    cursor += 1
                    started.complete(Unit)
                    release.await()
                    assertEquals(20, limit())
                    repeat(limit() - 1) { cursor += 1 }
                }
            }
            started.await()
            val selection = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 20) {
                    fail("Selection must join the original request")
                }
            }
            yield()
            val lowerPriority = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1), pageLimit = 4) {
                    fail("A later poll must neither restart nor demote selection")
                }
            }
            yield()
            release.complete(Unit)
            poll.join()
            selection.join()
            lowerPriority.join()
            assertEquals(100, cursor)
            assertEquals(1, requests)
            assertEquals(ConversationReadPhase.READY, reads[key].agents[1]?.phase)
        } finally { owner.cancel() }
    }

    @Test fun navigationAndPollingShareAReadWithoutBlockingOtherAgents() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val started = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            var requests = 0
            val navigation = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1)) {
                    requests += 1
                    started.complete(Unit)
                    release.await()
                }
            }
            started.await()
            val polling = launch {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { requests += 1 }
            }
            yield()
            navigation.cancelAndJoin()
            withTimeout(1_000) {
                reads.readShared(owner, key, ConversationReadTarget.Agent(2)) { requests += 1 }
                reads.readShared(owner, key.copy(workspaceId = "other"), ConversationReadTarget.Agent(1)) {
                    requests += 1
                }
            }
            assertEquals(3, requests)
            assertEquals(ConversationReadPhase.LOADING, reads[key].agents[1]?.phase)
            release.complete(Unit)
            polling.join()
            assertEquals(ConversationReadPhase.READY, reads[key].agents[1]?.phase)
            reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { requests += 1 }
            assertEquals(4, requests)
        } finally {
            owner.cancel()
        }
    }

    @Test fun removedAndFailedSharedReadsCanBeRetried() = runBlocking {
        val owner = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val reads = ConversationReadStateStore()
            val started = CompletableDeferred<Unit>()
            val old = launch {
                runCatching {
                    reads.readShared(owner, key, ConversationReadTarget.Agent(1)) {
                        started.complete(Unit)
                        awaitCancellation()
                    }
                }
            }
            started.await()
            reads.removeWhere { it == key }
            reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { Unit }
            old.join()
            assertEquals(ConversationReadPhase.READY, reads[key].agents[1]?.phase)
            runCatching {
                reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { throw IOException("offline") }
            }
            assertEquals(ConversationReadPhase.FAILED, reads[key].agents[1]?.phase)
            reads.readShared(owner, key, ConversationReadTarget.Agent(1)) { Unit }
            assertEquals(ConversationReadPhase.READY, reads[key].agents[1]?.phase)
        } finally {
            owner.cancel()
        }
    }

    private fun state(reads: Map<String, ConversationReadState> = emptyMap()) = MainUiState(
        selectedMacId = key.macId, selectedWorkspaceId = key.workspaceId,
        visibleRunId = key.runId, runStatus = "finalized", conversationReads = reads,
    )

    @Test
    fun emptyContentIsNotARequestAndFailureAlwaysEndsTheSpinner() = runBlocking {
        var ui = state()
        val store = ConversationReadStateStore { ui = ui.copy(conversationReads = it) }
        assertFalse(ui.isVisibleConversationContentLoading())
        runCatching {
            store.read(key, ConversationReadTarget.Metadata) {
                assertTrue(ui.isVisibleConversationContentLoading())
                throw RemoteCommandException("command_failed", "Unknown PCR run: run")
            }
        }
        assertFalse(ui.isVisibleConversationContentLoading())
        assertTrue(ui.visibleConversationReadIssue()!!.unavailable)
        assertFalse(ui.visibleConversationReadIssue()!!.retryAutomatically)
        assertTrue(ui.isVisibleConversationReadBlocked())
        assertNotEquals("本轮没有可续接的回复", promptContinuationBlockReason(ui))
        store.read(key, ConversationReadTarget.Metadata) { Unit }
        assertFalse(ui.isVisibleConversationContentLoading())
        assertNull(ui.visibleConversationReadIssue())
    }

    @Test
    fun detailFailureDoesNotWriteAnAgentErrorOrSealPartialCachedContent() = runBlocking {
        val runs = WorkspaceRunStateStore()
        val partial = MobileAgentTranscript(finalMessage = "cached answer", cursor = 12, finalMessageCursor = 13)
        runs.put(key, WorkspaceRunSnapshot(selectedAgent = 1, agentTranscripts = mapOf(1 to partial)))
        var ui = state().copy(selectedAgent = 1).projectRun(runs[key])
        val reads = ConversationReadStateStore { ui = ui.copy(conversationReads = it) }
        runCatching {
            reads.read(key, ConversationReadTarget.Agent(1)) { throw IOException("connection refused") }
        }
        assertEquals(partial, runs[key]!!.agentTranscripts[1])
        assertTrue(selectedAgentDetailHasMore(runs[key]!!))
        assertTrue(ui.hasVisibleConversationContent())
        assertFalse(ui.isVisibleConversationContentLoading())
        assertTrue(ui.visibleConversationReadIssue()!!.retryAutomatically)
        assertEquals("", ui.agentTranscripts[1]!!.error)
        reads.read(key, ConversationReadTarget.Agent(1)) { Unit }
        assertNull(ui.visibleConversationReadIssue())
    }

    @Test
    fun transientRefreshFailureDoesNotBlockCachedConversationSubmission() = runBlocking {
        var ui = state().copy(runPrompt = "cached question", agentSummaries = listOf(AgentSummary(
            number = 1, status = "running", reasoningTokens = null, seconds = null,
            recommended = false, rejected = false, workspaceAvailable = true,
            canAccept = false, canContinue = false,
        )))
        val reads = ConversationReadStateStore { ui = ui.copy(conversationReads = it) }
        runCatching { reads.read(key, ConversationReadTarget.Metadata) { throw IOException("network is unreachable") } }
        assertFalse(ui.isVisibleConversationReadBlocked())
        assertNotNull(ui.visibleConversationReadIssue())
    }

    @Test
    fun oldFailuresCannotReplaceNewSuccessOrLeakAcrossContextsAndAgents() = runBlocking {
        var ui = state()
        val store = ConversationReadStateStore { ui = ui.copy(conversationReads = it) }
        val release = CompletableDeferred<Unit>()
        val old = launch {
            runCatching {
                store.read(key, ConversationReadTarget.Metadata) {
                    release.await()
                    throw IOException("old failure")
                }
            }
        }
        yield()
        store.removeWhere { it == key }
        store.read(key, ConversationReadTarget.Metadata) { Unit }
        release.complete(Unit)
        old.join()
        assertEquals(ConversationReadPhase.READY, store[key].metadata.phase)
        assertNull(ui.visibleConversationReadIssue())
        runCatching { store.read(key, ConversationReadTarget.Agent(2)) { error("agent 2 failure") } }
        assertNull(ui.copy(selectedAgent = 1).visibleConversationReadIssue())
        assertNotNull(ui.copy(selectedAgent = 2).visibleConversationReadIssue())
        assertNull(ui.copy(visibleRunId = "another-run", selectedAgent = 2).visibleConversationReadIssue())
    }

    @Test
    fun historyFailureCanRetryInsteadOfBeingStuckBehindARequestId() = runBlocking {
        val store = ConversationReadStateStore()
        runCatching { store.read(key, ConversationReadTarget.History) { throw IOException("network is unreachable") } }
        assertTrue(store[key].needsHistoryRetry())
        store.read(key, ConversationReadTarget.History) { Unit }
        assertFalse(store[key].needsHistoryRetry())
        runCatching {
            store.read(key, ConversationReadTarget.History) {
                throw RemoteCommandException("unknown_run", "gone")
            }
        }
        assertFalse(store[key].needsHistoryRetry())
    }

    @Test
    fun unavailableTerminalDetailStopsAutomaticPollingWithoutClaimingContentIsComplete() = runBlocking {
        val store = ConversationReadStateStore()
        val snapshot = WorkspaceRunSnapshot(status = "finalized", selectedAgent = 1,
            agentTranscripts = mapOf(1 to MobileAgentTranscript(finalMessage = "cached answer")))
        runCatching {
            store.read(key, ConversationReadTarget.Agent(1)) {
                throw RemoteCommandException("command_failed", "Run artifacts are not available")
            }
        }
        assertTrue(selectedAgentDetailHasMore(snapshot))
        assertTrue(isConversationPollingComplete(snapshot, store[key]))
        assertFalse(isConversationPollingComplete(snapshot.copy(status = "running"), store[key]))
        store.read(key, ConversationReadTarget.Agent(1)) { Unit }
        assertFalse(isConversationPollingComplete(snapshot, store[key]))
    }

    @Test
    fun timeoutIsFailureButCancellationIsNot() = runBlocking {
        val store = ConversationReadStateStore()
        runCatching {
            store.read(key, ConversationReadTarget.Metadata) { withTimeout(1) { awaitCancellation() } }
        }
        assertEquals("read_timeout", store[key].metadata.issue?.code)
        assertEquals(ConversationReadPhase.FAILED, store[key].metadata.phase)
        val job = launch { store.read(key, ConversationReadTarget.Agent(1)) { awaitCancellation() } }
        yield()
        job.cancel()
        job.join()
        assertEquals(ConversationReadPhase.IDLE, store[key].agents[1]?.phase)
    }

    @Test
    fun encryptedCacheMigrationKeepsAnswersAndActualAgentErrors() = runBlocking {
        val directory = Files.createTempDirectory("pcr-read-migration").toFile()
        try {
            val cache = WorkspaceRunDiskCache(directory, ByteArray(32) { 1 })
            val answer = "Saved answer"
            cache.writeNow(key, WorkspaceRunSnapshot(agentTranscripts = mapOf(
                1 to MobileAgentTranscript(
                    finalMessage = answer, error = "Unknown PCR run: run", hasMore = false,
                    finalMessageHasMore = false,
                ),
                2 to MobileAgentTranscript(error = "Unknown PCR run: real output", errorFromAgentResponse = true),
            )))
            val restored = cache.load(key)!!
            assertEquals(answer, restored.agentTranscripts[1]!!.finalMessage)
            assertEquals("", restored.agentTranscripts[1]!!.error)
            assertTrue(restored.agentTranscripts[1]!!.finalMessageHasMore)
            assertEquals("Unknown PCR run: real output", restored.agentTranscripts[2]!!.error)
        } finally {
            directory.deleteRecursively()
        }
    }
}
