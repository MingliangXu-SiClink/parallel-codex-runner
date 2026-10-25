package com.parallelcodexrunner.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolJsonTest {
    @Test
    fun conversationListPaginationFieldsAreBackwardCompatible() {
        val json = Json { ignoreUnknownKeys = true }
        val legacy = json.decodeFromString<ConversationListResponse>(
            """{"conversations":[],"total":0}"""
        )
        val paged = json.decodeFromString<ConversationListResponse>(
            """{"conversations":[],"total":123,"cursor":"one","next_cursor":"two","has_more":true}"""
        )

        assertFalse(legacy.hasMore)
        assertEquals("", legacy.nextCursor)
        assertTrue(paged.hasMore)
        assertEquals("one", paged.cursor)
        assertEquals("two", paged.nextCursor)
    }

    @Test
    fun devicePresenceIsOptionalForOlderRelays() {
        val online = Json.decodeFromString<RelayDevice>(
            """{"device_id":"mac-1","kind":"mac","name":"Mac","public_key":"key","status":"active","online":true}"""
        )
        val legacy = Json.decodeFromString<RelayDevice>(
            """{"device_id":"mac-2","kind":"mac","name":"Mac","public_key":"key","status":"active"}"""
        )

        assertEquals(true, online.online)
        assertEquals(null, legacy.online)
    }

    @Test
    fun canonicalJsonSortsProtocolKeys() {
        val value = buildJsonObject {
            put("version", 1)
            put("pairing_id", "pair-1")
            put("android_device_id", "android-1")
        }

        assertEquals(
            "{\"android_device_id\":\"android-1\",\"pairing_id\":\"pair-1\",\"version\":1}",
            canonicalProtocolJson(value),
        )
    }

    @Test
    fun decodesCompleteWorkspaceSettingsAndDynamicOptions() {
        val settings = Json.decodeFromString<RemoteRunSettings>(
            """
            {
              "configured": true,
              "agents": 6,
              "synthesis_agents": 3,
              "follow_up_delay": 25,
              "execution": "serial",
              "automatic_max_parallel": false,
              "max_parallel": 2,
              "subagents": true,
              "subagents_limit": 12,
              "recommend_by": "duration",
              "model": "sol",
              "effort": "high",
              "fast_mode": "on",
              "synthesis_model": "luna",
              "synthesis_effort": "medium",
              "synthesis_fast_mode": "off",
              "sync_back": false,
              "keep_workspaces": true,
              "resume_session_id": "session-1",
              "resume_include_non_interactive": true,
              "runs_dir": "/Volumes/External/.codex_parallel_runs",
              "codex_bin": "/opt/homebrew/bin/codex"
            }
            """.trimIndent(),
        )
        val options = Json.decodeFromString<CodexModelOptions>(
            """
            {
              "effective_model": "sol",
              "effective_effort": "auto (high)",
              "effective_fast": "AUTO (STANDARD)",
              "models": [{"label": "sol", "value": "sol", "selected": true}],
              "efforts": [{"label": "high", "value": "high", "selected": true}]
            }
            """.trimIndent(),
        )

        assertEquals(6, settings.agents)
        assertEquals(3, settings.synthesisAgents)
        assertEquals("serial", settings.execution)
        assertEquals("duration", settings.recommendBy)
        assertEquals("sol", settings.model)
        assertEquals("on", settings.fastMode)
        assertEquals("session-1", settings.resumeSessionId)
        assertEquals(true, settings.resumeIncludeNonInteractive)
        assertEquals("/Volumes/External/.codex_parallel_runs", settings.runsDir)
        assertEquals("/opt/homebrew/bin/codex", settings.codexBin)
        assertEquals("sol", options.effectiveModel)
        assertEquals("high", options.efforts.single().value)
    }

    @Test
    fun decodesDirectoryModificationTime() {
        val entry = Json.decodeFromString<DirectoryEntry>(
            """
            {
              "name": "Recent Project",
              "path": "/Users/example/Recent Project",
              "kind": "directory",
              "readable": true,
              "hidden": false,
              "modified_at": "2026-08-23T05:12:00+00:00"
            }
            """.trimIndent(),
        )

        assertEquals("2026-08-23T05:12:00+00:00", entry.modifiedAt)
    }

    @Test
    fun capabilitiesNegotiateProtocolSchemaAndActions() {
        val capabilities = Json.decodeFromString<SystemCapabilities>(
            """
            {
              "daemon_name": "pcrd",
              "daemon_version": "0.1.7",
              "protocol_version": 1,
              "minimum_client_protocol_version": 1,
              "supported_protocol_versions": [1],
              "transcript_schema_version": 1,
              "maximum_agent_count": 128,
              "transport": "relay",
              "platform": "linux",
              "actions": ["system.capabilities", "workspaces.list"]
            }
            """.trimIndent(),
        )

        assertTrue(capabilities.compatible)
        assertEquals(128, capabilities.clientMaximumAgentCount)
        assertEquals("linux", capabilities.platform)
        assertTrue(capabilities.supports("workspaces.list"))
        assertFalse(capabilities.supports("relay.clear"))
        assertFalse(capabilities.copy(transcriptSchemaVersion = 2).compatible)
        assertFalse(
            capabilities.copy(minimumClientProtocolVersion = 2).compatible,
        )
    }
}
