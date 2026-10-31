package com.parallelcodexrunner.remote.ui

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RunMutationFlowTest {
    @Test fun saveFenceEndsBeforeDelayedHistoryAndCannotReleaseALaterMutation() = runBlocking {
        val saved = CompletableDeferred<Unit>()
        val reading = CompletableDeferred<Unit>()
        val history = CompletableDeferred<Unit>()
        var busy = true
        var releases = 0
        val operation = launch {
            performRunMutation(
                mutate = { saved.await() },
                release = { releases++; busy = false },
                refresh = { reading.complete(Unit); history.await() },
            )
        }
        assertTrue(busy)
        saved.complete(Unit)
        reading.await()
        assertFalse(busy)
        busy = true // A later operation starts while old history is still loading.
        history.complete(Unit)
        operation.join()
        assertTrue(busy)
        assertEquals(1, releases)
    }

    @Test fun failedOrCancelledMutationReleasesWithoutRefreshing() = runBlocking {
        var releases = 0
        var refreshes = 0
        val failed = runCatching {
            performRunMutation({ error("not accepted") }, { releases++ }, { refreshes++ })
        }
        assertTrue(failed.isFailure)
        val entered = CompletableDeferred<Unit>()
        val operation = launch {
            performRunMutation({ entered.complete(Unit); CompletableDeferred<Unit>().await() },
                { releases++ }, { refreshes++ })
        }
        entered.await()
        operation.cancelAndJoin()
        assertEquals(2, releases)
        assertEquals(0, refreshes)
    }

    @Test fun historyFailureDoesNotUndoACommittedSave() = runBlocking {
        var busy = true
        var saved = false
        val result = runCatching {
            performRunMutation({ saved = true }, { busy = false }, { error("history unavailable") })
        }
        assertTrue(result.isFailure)
        assertTrue(saved)
        assertFalse(busy)
    }

    @Test fun scopedSaveReceiptUpdatesOnlyConfirmedMetadataAndPreservesConversation() {
        val key = WorkspaceRunKey("mac", "workspace", "run")
        val snapshot = WorkspaceRunSnapshot(status = "completed", prompt = "question", selectedAgent = 2,
            agents = (1..2).map { AgentSummary(it, "success", reasoningTokens = null,
                seconds = null, recommended = false, rejected = false, workspaceAvailable = true,
                canAccept = true, canContinue = true) },
            agentTranscripts = mapOf(2 to MobileAgentTranscript(finalMessage = "original answer")))
        val receipt = buildJsonObject {
            put("run_id", key.runId)
            put("status", "finalized")
            put("finalized_agent", 2)
            put("worker_active", false)
            putJsonObject("session_promotion") { put("session_id", "original-native-thread") }
        }
        assertEquals(snapshot, snapshot.applyingSaveReceipt(key.copy(runId = "another-run"), 2, receipt))
        assertEquals(snapshot, snapshot.applyingSaveReceipt(key, 1, receipt))
        val saved = snapshot.applyingSaveReceipt(key, 2, receipt)
        assertEquals("finalized", saved.status)
        assertEquals(2, saved.finalizedAgent)
        assertEquals(snapshot.prompt, saved.prompt)
        assertEquals(snapshot.agentTranscripts, saved.agentTranscripts)
        assertEquals("original-native-thread", saved.adoptedSessionId)
        assertTrue(saved.agents.none { it.canAccept || it.canRetry })
        assertEquals(listOf(2), saved.agents.filter { it.canContinue }.map { it.number })
        val pending = snapshot.applyingSaveReceipt(key, 2, buildJsonObject {
            put("run_id", key.runId); put("status", "finalizing"); put("finalizing_agent", 2)
        })
        assertEquals("finalizing", pending.status)
        assertTrue(pending.workerActive)
        assertEquals(null, pending.finalizedAgent)
    }
}
