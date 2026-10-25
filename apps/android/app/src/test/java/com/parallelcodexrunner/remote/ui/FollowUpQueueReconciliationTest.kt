package com.parallelcodexrunner.remote.ui
import com.parallelcodexrunner.remote.data.newestFollowUpSnapshot
import com.parallelcodexrunner.remote.data.reconcileFollowUpItems

import com.parallelcodexrunner.remote.data.PersistedQueuedFollowUp
import com.parallelcodexrunner.remote.data.RemoteFollowUpQueue
import org.junit.Assert.assertEquals
import org.junit.Test

class FollowUpQueueReconciliationTest {
    @Test fun firstReceiptPreservesNextMessage() {
        val first = PersistedQueuedFollowUp(id = "first", prompt = "First", daemonAcknowledged = false)
        val next = PersistedQueuedFollowUp(id = "next", prompt = "Next", daemonAcknowledged = false)
        assertEquals(listOf("first", "next"), reconcileFollowUpItems(
            listOf(first.copy(daemonAcknowledged = true)), listOf(first, next)).map { it.id })
    }

    @Test fun consumedMessageDoesNotReturnWithDelayedReceipt() {
        val first = PersistedQueuedFollowUp(id = "first", prompt = "First", daemonAcknowledged = false)
        assertEquals(emptyList<PersistedQueuedFollowUp>(), reconcileFollowUpItems(
            emptyList(), listOf(first), acknowledged = setOf("first")))
        assertEquals(emptyList<PersistedQueuedFollowUp>(), reconcileFollowUpItems(
            emptyList(), listOf(first), completedId = "first"))
    }

    @Test fun oldGenerationCannotRevertTheTurn() {
        val current = RemoteFollowUpQueue(workspaceId = "w", runId = "next", generation = 8)
        val stale = current.copy(runId = "previous", generation = 7)
        assertEquals(current, newestFollowUpSnapshot(stale, current))
    }
}
