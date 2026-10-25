package com.parallelcodexrunner.remote.data

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class FollowUpQueueWorkerTest {
    @Test
    fun contextRoundTripsAcrossPersistentKey() {
        val context = FollowUpQueueContext("mac-1", "workspace-2", "run-3")

        assertEquals(context, FollowUpQueueContext.parse(context.key))
    }

    @Test
    fun malformedContextIsRejected() {
        assertNull(FollowUpQueueContext.parse("mac-only"))
        assertNull(FollowUpQueueContext.parse("mac\u001f\u001frun"))
    }

    @Test
    fun duplicateWorkersForSameQueueAreSerialized() = runBlocking {
        val firstEntered = CompletableDeferred<Unit>()
        val releaseFirst = CompletableDeferred<Unit>()
        val secondEntered = CompletableDeferred<Unit>()
        val first = async {
            FollowUpWorkerSerializers.run("mac\u001fworkspace\u001frun") {
                firstEntered.complete(Unit)
                releaseFirst.await()
            }
        }
        firstEntered.await()
        val second = async {
            FollowUpWorkerSerializers.run("mac\u001fworkspace\u001frun") {
                secondEntered.complete(Unit)
            }
        }

        yield()
        assertFalse(secondEntered.isCompleted)
        releaseFirst.complete(Unit)
        first.await()
        second.await()
        assertEquals(true, secondEntered.isCompleted)
    }

    @Test
    fun daemonHandoffOnlyResubmitsUnacknowledgedItems() {
        val pending = PersistedQueuedFollowUp(
            id = "pending",
            prompt = "send me",
            daemonAcknowledged = false,
        )
        val legacy = PersistedQueuedFollowUp(
            id = "legacy",
            prompt = "migrate me",
        )
        val acknowledged = PersistedQueuedFollowUp(
            id = "acknowledged",
            prompt = "already remote",
            daemonAcknowledged = true,
        )

        assertEquals(
            listOf("pending", "legacy"),
            pendingDaemonFollowUps(
                remoteItemIDs = emptySet(),
                localQueue = listOf(pending, legacy, acknowledged),
            ).map(PersistedQueuedFollowUp::id),
        )
        assertEquals(
            listOf("legacy"),
            pendingDaemonFollowUps(
                remoteItemIDs = setOf("pending"),
                localQueue = listOf(pending, legacy, acknowledged),
            ).map(PersistedQueuedFollowUp::id),
        )
    }
}
