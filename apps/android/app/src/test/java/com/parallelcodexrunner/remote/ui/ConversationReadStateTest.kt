package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteCommandException
import java.io.IOException
import java.nio.file.Files
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class ConversationReadStateTest {
    private val key = WorkspaceRunKey("mac", "workspace", "run")

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
