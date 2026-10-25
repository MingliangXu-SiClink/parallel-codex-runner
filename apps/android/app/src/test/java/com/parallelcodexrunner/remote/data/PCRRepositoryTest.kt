package com.parallelcodexrunner.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PCRRepositoryTest {
    @Test
    fun conversationListRequestCarriesStablePaginationCursor() {
        val first = conversationListBody("workspace-1")
        val next = conversationListBody("workspace-1", cursor = "opaque-cursor", limit = 500)

        assertEquals("workspace-1", first.getValue("workspace_id").jsonPrimitive.content)
        assertEquals(100, first.getValue("limit").jsonPrimitive.int)
        assertFalse("cursor" in first)
        assertEquals("opaque-cursor", next.getValue("cursor").jsonPrimitive.content)
        assertEquals(100, next.getValue("limit").jsonPrimitive.int)
    }

    @Test
    fun offlinePollingDoesNotCreateStaleRelayRequests() {
        assertTrue(shouldRejectOfflineRead(false, "runs.get"))
        assertTrue(shouldRejectOfflineRead(false, "workspaces.list"))
        assertTrue(shouldRejectOfflineRead(false, "codex.resume_history"))
    }

    @Test
    fun offlineMutationsRemainEligibleForDurableRelayDelivery() {
        assertFalse(shouldRejectOfflineRead(false, "runs.start"))
        assertFalse(shouldRejectOfflineRead(false, "runs.continue"))
        assertFalse(shouldRejectOfflineRead(false, "runs.accept_agent"))
    }

    @Test
    fun unknownPresenceDoesNotBreakOlderRelayServers() {
        assertFalse(shouldRejectOfflineRead(null, "runs.get"))
        assertFalse(shouldRejectOfflineRead(true, "runs.get"))
    }

    @Test
    fun offlineFollowUpFailuresKeepRetryingWithoutConsumingCommandAttempts() {
        assertFalse(
            isPermanentRemoteCommandRejection(
                RemoteCommandException("machine_offline", "temporarily offline")
            )
        )
        assertTrue(
            isPermanentRemoteCommandRejection(
                RemoteCommandException("invalid_argument", "invalid settings")
            )
        )
        assertFalse(
            isPermanentRemoteCommandRejection(
                RemoteCommandException(
                    "pcr_operation_pending",
                    "finalization is still running",
                )
            )
        )
        assertFalse(isPermanentRemoteCommandRejection(IllegalStateException("network lost")))
    }

    @Test
    fun daemonQueueRequestUsesCanonicalDurableSettings() {
        val body = followUpReplaceBody(
            workspaceId = "workspace-1",
            runId = "run-1",
            items = listOf(
                PersistedQueuedFollowUp(
                    id = "message-1",
                    prompt = "continue",
                    settings = RemoteRunSettings(
                        agents = 7,
                        recommendBy = "duration",
                        fastMode = "on",
                        synthesisFastMode = "off",
                        runsDir = "/tmp/runs",
                        codexBin = "/usr/local/bin/codex",
                    ),
                    attempt = 2,
                )
            ),
            selectedAgent = 3,
            resetCountdown = true,
        )

        val item = body.getValue("items").jsonArray.single().jsonObject
        val settings = item.getValue("settings").jsonObject
        assertEquals("workspace-1", body.getValue("workspace_id").jsonPrimitive.content)
        assertEquals("run-1", body.getValue("run_id").jsonPrimitive.content)
        assertEquals(3, body.getValue("selected_agent").jsonPrimitive.int)
        assertTrue(body.getValue("reset_countdown").jsonPrimitive.boolean)
        assertEquals(7, settings.getValue("agents").jsonPrimitive.int)
        assertEquals("duration", settings.getValue("recommendation").jsonPrimitive.content)
        assertEquals("on", settings.getValue("fast").jsonPrimitive.content)
        assertEquals("off", settings.getValue("synthesis_fast").jsonPrimitive.content)
        assertEquals("/tmp/runs", settings.getValue("runs_directory").jsonPrimitive.content)
        assertEquals(
            "/usr/local/bin/codex",
            settings.getValue("codex_executable").jsonPrimitive.content,
        )
    }

    @Test
    fun daemonQueueEnqueueSendsOneStableMessageInsteadOfReplacingTheQueue() {
        val body = followUpEnqueueBody(
            workspaceId = "workspace-1",
            runId = "run-1",
            item = PersistedQueuedFollowUp(
                id = "message-1",
                prompt = "continue",
                settings = RemoteRunSettings(agents = 6),
            ),
            selectedAgent = 2,
        )

        val item = body.getValue("item").jsonObject
        assertEquals("workspace-1", body.getValue("workspace_id").jsonPrimitive.content)
        assertEquals("run-1", body.getValue("run_id").jsonPrimitive.content)
        assertEquals(2, body.getValue("selected_agent").jsonPrimitive.int)
        assertEquals("message-1", item.getValue("id").jsonPrimitive.content)
        assertEquals("continue", item.getValue("prompt").jsonPrimitive.content)
        assertEquals(
            6,
            item.getValue("settings").jsonObject.getValue("agents").jsonPrimitive.int,
        )
        assertFalse("items" in body)
    }

    @Test
    fun daemonQueueResponseRestoresAndroidSettingsAndRetryState() {
        val response = Json { ignoreUnknownKeys = true }.decodeFromString(
            RemoteFollowUpQueueResponse.serializer(),
            """
            {
              "revision": 9,
              "queue": {
                "workspace_id": "workspace-1",
                "run_id": "run-2",
                "state": "waiting",
                "deadline_epoch_ms": 1900000000000,
                "items": [{
                  "id": "message-1",
                  "prompt": "continue",
                  "attempt": 4,
                  "failure_count": 2,
                  "retry_at_epoch_ms": 1900000005000,
                  "last_error": "offline",
                  "paused": true,
                  "settings": {
                    "agents": 5,
                    "synthesis_agents": 1,
                    "follow_up_delay": 12,
                    "recommendation": "duration",
                    "fast": "on",
                    "synthesis_fast": "off",
                    "runs_directory": "/runs",
                    "codex_executable": "/bin/codex"
                  }
                }]
              }
            }
            """.trimIndent(),
        )

        assertNotNull(response.queue)
        val queue = response.queue!!
        val item = queue.items.single()
        val settings = item.settings.asRunSettings()
        assertEquals(9, response.revision)
        assertEquals("run-2", queue.runId)
        assertEquals(4, item.attempt)
        assertEquals(2, item.failureCount)
        assertEquals(1_900_000_005_000L, item.retryAtEpochMillis)
        assertTrue(item.paused)
        assertEquals(5, settings.agents)
        assertEquals("duration", settings.recommendBy)
        assertEquals("on", settings.fastMode)
        assertEquals("off", settings.synthesisFastMode)
        assertEquals("/runs", settings.runsDir)
        assertEquals("/bin/codex", settings.codexBin)
    }
}
