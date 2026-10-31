package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.lifecycle.ViewModelStore
import androidx.test.core.app.ApplicationProvider
import com.parallelcodexrunner.remote.PCRRemoteApplication
import com.parallelcodexrunner.remote.data.EnvelopeCrypto
import com.parallelcodexrunner.remote.data.CLIENT_PROTOCOL_VERSION
import com.parallelcodexrunner.remote.data.PCRRepository
import com.parallelcodexrunner.remote.data.RelayApi
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RelayTransport
import com.parallelcodexrunner.remote.data.RemoteFollowUpQueue
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.SecureStore
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

class AgentRetrySelectionFlowTest {
    @get:Rule val compose = createComposeRule()
    private val store = ViewModelStore()
    private lateinit var model: MainViewModel
    private lateinit var wire: ControlledWire
    private val key = WorkspaceRunKey("test-mac", "test-workspace", "test-run")

    @After fun close() { compose.runOnIdle { store.clear() } }

    @Test fun retryKeepsBodyAndReleasesMutationBeforeSlowHistoryRead() {
        setup(failed = true)
        compose.runOnIdle {
            assertTrue(model.state.value.canMutateVisibleRun())
            assertTrue(model.state.value.agentSummaries.first().canRetry)
            model.retryAgent(1)
        }
        compose.waitForIdle()
        assertTrue("calls=${wire.calls}; message=${model.state.value.message}; state=${model.state.value.visibleRunId}",
            wire.calls.any { it.first == "runs.retry_agent" })
        compose.onNodeWithText("Saved answer 1").assertIsDisplayed()
        wire.retry.complete(success("""{"run_id":"test-run","status":"running","worker_active":true,
            "agents":[{"agent":1,"status":"queued"}]}"""))
        compose.waitUntil { !model.state.value.runActionInProgress }
        compose.runOnIdle {
            assertFalse(model.state.value.runActionInProgress)
            assertEquals("queued", model.state.value.agentSummaries.first().status)
            assertEquals(17, model.state.value.agentTranscripts[1]?.cursor)
        }
        compose.onNodeWithText("Saved answer 1").assertIsDisplayed()
        compose.waitForIdle()
        assertTrue("calls=${wire.calls}; message=${model.state.value.message}", wire.calls.any { it.first == "turns.get" })
        assertEquals(1, wire.calls.count { it.first == "runs.retry_agent" })
    }

    @Test fun rejectedRetryKeepsOriginalBodyAndFailureStatus() {
        setup(failed = true)
        compose.runOnIdle { model.retryAgent(1) }
        compose.waitUntil { wire.calls.any { it.first == "runs.retry_agent" } }
        wire.retry.complete(Json.parseToJsonElement("""{"ok":false,"error":{"code":"invalid_state",
            "message":"Original session is unavailable; no new task started"}}""").jsonObject)
        compose.waitUntil { model.state.value.messageIsError }
        compose.onNodeWithText("Saved answer 1").assertIsDisplayed()
        compose.runOnIdle {
            assertEquals("failed", model.state.value.agentSummaries.first().status)
            assertFalse(model.state.value.runActionInProgress)
        }
        assertFalse(wire.calls.any { it.first == "turns.get" })
    }

    @Test fun cachedAgentIsVisibleBeforeContinuationSourceIsConfirmed() {
        setup()
        compose.runOnIdle { model.selectAgent(2) }
        compose.waitUntil { wire.calls.any { it.first == "followups.select_agent" } }
        compose.onNodeWithText("Saved answer 2").assertIsDisplayed()
        compose.waitUntil { wire.calls.any { it.first == "runs.agent" } }
        assertEquals(17, wire.calls.first { it.first == "runs.agent" }.second["cursor"]?.jsonPrimitive?.int)
        compose.runOnIdle {
            assertEquals(2, model.state.value.selectedAgent)
            assertEquals(2, model.state.value.pendingAgentSelection)
            assertFalse(wire.selection.isCompleted)
            model.applyDaemonFollowUpQueue(key.macId, key.workspaceId,
                RemoteFollowUpQueue(key.workspaceId, key.runId, selectedAgent = 1))
            assertEquals(2, model.state.value.selectedAgent)
        }
        compose.onNodeWithText("Saved answer 2").assertIsDisplayed()
    }

    @Test fun uncachedAgentReadsAndRendersWithoutWaitingForSourceRpc() {
        setup(cacheSecond = false)
        compose.runOnIdle { model.selectAgent(2) }
        compose.waitUntil { model.state.value.agentTranscripts[2]?.finalMessage == "Fresh answer 2" }
        compose.onNodeWithText("Fresh answer 2").assertIsDisplayed()
        assertFalse(wire.selection.isCompleted)
        compose.runOnIdle { model.selectAgent(1) }
        wire.selection.complete(success("""{"queue":{"workspace_id":"test-workspace",
            "run_id":"test-run","selected_agent":2}}"""))
        compose.onNodeWithText("Saved answer 1").assertIsDisplayed()
        compose.runOnIdle { assertEquals(1, model.state.value.selectedAgent) }
    }

    private fun setup(failed: Boolean = false, cacheSecond: Boolean = true) {
        val app = ApplicationProvider.getApplicationContext<PCRRemoteApplication>()
        val secure = SecureStore(app)
        val crypto = EnvelopeCrypto(secure)
        val api = RelayApi(secure, crypto)
        wire = ControlledWire(api, crypto, secure)
        compose.runOnIdle {
            model = MainViewModel(app, PCRRepository(api, wire))
            store.put("test", model)
            model.onFirstFrameDrawn()
            val agents = (1..2).map { AgentSummary(it, if (failed && it == 1) "failed" else "success",
                null, null, false, false, true, !failed, !failed, canRetry = failed && it == 1) }
            val transcripts = (if (cacheSecond) 1..2 else 1..1).associateWith {
                MobileAgentTranscript(finalMessage = "Saved answer $it", cursor = 17,
                    blocks = listOf(MobileTranscriptBlock("answer-$it", MobileTranscriptKind.OUTPUT, "Saved answer $it")))
            }
            val cached = model.runStateStore.update(key) { WorkspaceRunSnapshot(status = "completed",
                selectedAgent = 1, reviewSelectionEstablished = true, agents = agents, agentTranscripts = transcripts) }
            model.mutableState.value = MainUiState(
                macs = listOf(RelayDevice(key.macId, "mac", "Test Mac", "", "active", online = true)),
                selectedMacId = key.macId, selectedWorkspaceId = key.workspaceId, visibleRunId = key.runId,
                workspaces = listOf(RemoteWorkspace(key.workspaceId, "Test", "/test", lastRunId = key.runId)),
            ).projectRun(cached)
        }
        compose.setContent {
            val state by model.state.collectAsState()
            val viewport = remember { ConversationViewportStore(app) }
            MaterialTheme {
                AgentConversation(state, Modifier.fillMaxSize(), viewportStore = viewport,
                    onSelectAgent = { model.selectAgent(it) }, onLoadWorkOperation = { _, _ -> error("No commands") })
            }
        }
    }

    private class ControlledWire(api: RelayApi, crypto: EnvelopeCrypto, secure: SecureStore) :
        RelayTransport(api, crypto, secure) {
        val calls = CopyOnWriteArrayList<Pair<String, JsonObject>>()
        val retry = CompletableDeferred<JsonObject>()
        val selection = CompletableDeferred<JsonObject>()
        override suspend fun command(target: RelayDevice, action: String, body: JsonObject,
            expectedRevision: Int?, timeoutMillis: Long, commandId: String?, maxAttempts: Int): JsonObject {
            calls += action to body
            val response = when (action) {
                "system.capabilities" -> success("""{"actions":["runs.retry_agent","runs.agent","turns.get",
                    "runs.history","runs.diff","followups.select_agent"]}""")
                "runs.retry_agent" -> retry.await()
                "followups.select_agent" -> selection.await()
                "runs.agent" -> if (body["cursor"]?.jsonPrimitive?.int == 0) {
                    success("""{"final_message":"Fresh answer ${body["agent"]?.jsonPrimitive?.int}"}""")
                } else success("{}")
                else -> awaitCancellation()
            }
            return buildJsonObject {
                put("protocol_version", CLIENT_PROTOCOL_VERSION)
                put("kind", "response")
                put("request_id", "test-$action")
                put("command_id", commandId ?: "test-$action")
                response.forEach { (key, value) -> put(key, value) }
            }
        }
    }

    companion object {
        private fun success(body: String) = buildJsonObject {
            put("ok", true); put("body", Json.parseToJsonElement(body))
        }
    }
}
