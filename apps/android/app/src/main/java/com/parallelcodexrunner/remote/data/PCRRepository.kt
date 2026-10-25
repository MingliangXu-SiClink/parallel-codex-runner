package com.parallelcodexrunner.remote.data

import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot
import kotlinx.serialization.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

class RemoteCommandException(
    val code: String,
    message: String,
) : IllegalStateException(message)

data class WorkspaceFileDownload(
    val file: File,
    val name: String,
    val relativePath: String,
    val contentType: String,
    val sizeBytes: Long,
    val line: Int? = null,
)

internal fun shouldRejectOfflineRead(online: Boolean?, action: String): Boolean =
    online == false && action in READ_ONLY_REMOTE_ACTIONS

internal fun isPermanentRemoteCommandRejection(error: Throwable): Boolean =
    error is RemoteCommandException && error.code !in RETRYABLE_REMOTE_COMMAND_CODES

class PCRRepository(
    val relayApi: RelayApi,
    private val transport: RelayTransport,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val capabilitiesByMac = ConcurrentHashMap<String, SystemCapabilities>()

    val signedIn: Boolean
        get() = relayApi.authenticated

    suspend fun pair(code: String): PairingResult {
        transport.close()
        capabilitiesByMac.clear()
        return relayApi.pair(PairingCode.parse(code)).also {
            // A poll may reconnect while the QR approval is in flight.
            transport.close()
        }
    }

    fun rememberPendingPairing(code: String) = relayApi.rememberPendingPairing(code)

    fun pendingPairing(): String? = relayApi.pendingPairing()

    fun clearPendingPairing() = relayApi.clearPendingPairing()

    suspend fun macs(): List<RelayDevice> {
        val devices = relayApi.devices()
        transport.rememberDevices(devices)
        return devices.filter {
            it.kind.equals("mac", ignoreCase = true) &&
                it.status.equals("active", ignoreCase = true)
        }
    }

    suspend fun revokeMac(deviceId: String) {
        relayApi.revokeDevice(deviceId)
        capabilitiesByMac.remove(deviceId)
    }

    suspend fun systemCapabilities(
        mac: RelayDevice,
        refresh: Boolean = false,
    ): SystemCapabilities {
        if (!refresh) capabilitiesByMac[mac.deviceId]?.let { return it }
        val capabilities = decode<SystemCapabilities>(
            command(mac, "system.capabilities", checkCapability = false)
        )
        if (!capabilities.compatible) {
            throw RemoteCommandException(
                code = "incompatible_protocol",
                message = "目标设备上的 Parallel Codex 与当前手机版本不兼容，请更新两端应用。",
            )
        }
        capabilitiesByMac[mac.deviceId] = capabilities
        return capabilities
    }

    fun cachedSupports(macId: String, action: String): Boolean? =
        capabilitiesByMac[macId]?.supports(action)

    suspend fun checkAndroidUpdate(mac: RelayDevice): AndroidUpdateCheckResponse =
        relayApi.checkAndroidUpdate(
            macDeviceId = mac.deviceId,
            currentVersionCode = com.parallelcodexrunner.remote.BuildConfig.VERSION_CODE.toLong(),
        )

    suspend fun androidUpdateHistory(
        mac: RelayDevice,
        cursor: Int = 0,
    ): AndroidUpdateHistoryResponse = relayApi.androidUpdateHistory(
        macDeviceId = mac.deviceId,
        cursor = cursor,
    )

    fun cachedAndroidUpdateHistory(mac: RelayDevice): AndroidUpdateHistoryResponse? =
        relayApi.cachedAndroidUpdateHistory(mac.deviceId)

    suspend fun downloadAndroidUpdate(
        mac: RelayDevice,
        update: AndroidUpdateInfo,
        destination: File,
        onProgress: (downloadedBytes: Long, totalBytes: Long) -> Unit,
    ): File {
        val ticket = relayApi.createAndroidUpdateTicket(
            macDeviceId = mac.deviceId,
            releaseId = update.releaseId,
        )
        require(ticket.release.releaseId == update.releaseId) {
            "Relay returned a different Android release"
        }
        require(
            ticket.release.packageName == update.packageName &&
                ticket.release.versionCode == update.versionCode &&
                ticket.release.sizeBytes == update.sizeBytes &&
                ticket.release.sha256.equals(update.sha256, ignoreCase = true) &&
                ticket.release.signingCertSha256.equals(
                    update.signingCertSha256,
                    ignoreCase = true,
                )
        ) { "Android release changed while preparing the download" }
        return relayApi.downloadAndroidUpdate(ticket, destination, onProgress)
    }

    suspend fun workspaces(mac: RelayDevice): WorkspaceList =
        decode(command(mac, "workspaces.list"))

    suspend fun workspaceConversations(
        mac: RelayDevice,
        workspaceId: String,
        cursor: String = "",
        limit: Int = 100,
    ): ConversationListResponse = decode(
        command(
            mac,
            "conversations.list",
            conversationListBody(workspaceId, cursor, limit),
        )
    )

    suspend fun conversation(
        mac: RelayDevice,
        conversationId: String,
    ): ConversationDetailResponse = decode(
        command(
            mac,
            "conversations.get",
            buildJsonObject { put("conversation_id", conversationId) },
        )
    )

    suspend fun followUpQueue(
        mac: RelayDevice,
        workspaceId: String,
    ): RemoteFollowUpQueueResponse = decode(
        command(
            mac,
            "followups.get",
            buildJsonObject { put("workspace_id", workspaceId) },
        )
    )

    suspend fun followUpQueues(mac: RelayDevice): RemoteFollowUpQueueListResponse =
        decode(command(mac, "followups.list"))

    suspend fun replaceFollowUpQueue(
        mac: RelayDevice,
        workspaceId: String,
        runId: String,
        items: List<PersistedQueuedFollowUp>,
        selectedAgent: Int?,
        resetCountdown: Boolean,
        commandId: String = "follow-up-queue-${UUID.randomUUID()}",
    ): RemoteFollowUpQueueResponse = decode(
        command(
            mac = mac,
            action = "followups.replace",
            body = followUpReplaceBody(
                workspaceId = workspaceId,
                runId = runId,
                items = items,
                selectedAgent = selectedAgent,
                resetCountdown = resetCountdown,
            ),
            commandId = commandId,
        )
    )

    suspend fun enqueueFollowUp(
        mac: RelayDevice,
        workspaceId: String,
        runId: String,
        item: PersistedQueuedFollowUp,
        selectedAgent: Int?,
        commandId: String = "follow-up-enqueue-${item.id}",
    ): RemoteFollowUpQueueResponse = decode(
        command(
            mac = mac,
            action = "followups.enqueue",
            body = followUpEnqueueBody(workspaceId, runId, item, null),
            commandId = commandId,
        )
    )

    suspend fun selectFollowUpAgent(
        mac: RelayDevice, workspaceId: String, runId: String, agent: Int,
    ): RemoteFollowUpQueueResponse = decode(command(
        mac = mac,
        action = "followups.select_agent",
        body = buildJsonObject {
            put("workspace_id", workspaceId)
            put("run_id", runId)
            put("agent", agent)
        },
        commandId = "follow-up-select-${UUID.randomUUID()}",
    ))

    suspend fun clearFollowUpQueue(
        mac: RelayDevice,
        workspaceId: String,
        runId: String,
        commandId: String = "follow-up-clear-${UUID.randomUUID()}",
    ): RemoteFollowUpQueueResponse = decode(
        command(
            mac = mac,
            action = "followups.clear",
            body = buildJsonObject {
                put("workspace_id", workspaceId)
                put("run_id", runId)
            },
            commandId = commandId,
        )
    )

    suspend fun roots(mac: RelayDevice): FileSystemRoots =
        decode(command(mac, "filesystem.roots"))

    suspend fun directory(
        mac: RelayDevice,
        path: String,
        includeHidden: Boolean,
        cursor: Int = 0,
    ): DirectoryPage = decode(
        command(
            mac,
            "filesystem.list",
            buildJsonObject {
                put("path", path)
                put("cursor", cursor)
                put("limit", 500)
                put("include_hidden", includeHidden)
                put("directories_only", true)
                put("sort", "modified_desc")
            },
        )
    )

    suspend fun createDirectory(
        mac: RelayDevice,
        parent: String,
        name: String,
    ): CreatedDirectory = decode(
        command(
            mac,
            "filesystem.mkdir",
            buildJsonObject {
                put("parent", parent)
                put("name", name)
            },
        )
    )

    suspend fun addWorkspace(
        mac: RelayDevice,
        path: String,
    ): JsonObject = command(
        mac,
        "workspaces.add",
        buildJsonObject {
            put("path", path)
            // Workspace selection belongs to each client. Mutating the
            // daemon-wide selection here would move other connected clients.
            put("select", false)
        },
    )

    suspend fun removeWorkspace(
        mac: RelayDevice,
        workspaceId: String,
    ): JsonObject = command(
        mac,
        "workspaces.remove",
        buildJsonObject { put("workspace_id", workspaceId) },
    )

    suspend fun updateWorkspaceSettings(
        mac: RelayDevice,
        workspaceId: String,
        settings: RemoteRunSettings,
        fields: Set<String> = WorkspaceSettingFields.all,
    ): RemoteRunSettings {
        val normalizedFields = WorkspaceSettingFields.normalized(fields)
        val result = command(
            mac,
            "workspaces.settings.update",
            buildJsonObject {
                put("workspace_id", workspaceId)
                if (WorkspaceSettingFields.AGENTS in normalizedFields) {
                    put("agents", settings.agents)
                }
                if (WorkspaceSettingFields.SYNTHESIS_AGENTS in normalizedFields) {
                    put("synthesis_agents", settings.synthesisAgents)
                }
                if (WorkspaceSettingFields.FOLLOW_UP_DELAY in normalizedFields) {
                    put("follow_up_delay", settings.followUpDelay)
                }
                if (WorkspaceSettingFields.EXECUTION in normalizedFields) {
                    put("execution", settings.execution)
                }
                if (WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL in normalizedFields) {
                    put("automatic_max_parallel", settings.automaticMaxParallel)
                }
                if (WorkspaceSettingFields.MAX_PARALLEL in normalizedFields) {
                    put("max_parallel", settings.maxParallel)
                }
                if (WorkspaceSettingFields.SUBAGENTS in normalizedFields) {
                    put("subagents", settings.subagents)
                }
                if (WorkspaceSettingFields.SUBAGENTS_LIMIT in normalizedFields) {
                    put("subagents_limit", settings.subagentsLimit)
                }
                if (WorkspaceSettingFields.RECOMMEND_BY in normalizedFields) {
                    put("recommend_by", settings.recommendBy)
                }
                if (WorkspaceSettingFields.MODEL in normalizedFields) {
                    put("model", settings.model)
                }
                if (WorkspaceSettingFields.EFFORT in normalizedFields) {
                    put("effort", settings.effort)
                }
                if (WorkspaceSettingFields.FAST_MODE in normalizedFields) {
                    put("fast_mode", settings.fastMode)
                }
                if (WorkspaceSettingFields.SYNTHESIS_MODEL in normalizedFields) {
                    put("synthesis_model", settings.synthesisModel)
                }
                if (WorkspaceSettingFields.SYNTHESIS_EFFORT in normalizedFields) {
                    put("synthesis_effort", settings.synthesisEffort)
                }
                if (WorkspaceSettingFields.SYNTHESIS_FAST_MODE in normalizedFields) {
                    put("synthesis_fast_mode", settings.synthesisFastMode)
                }
                if (WorkspaceSettingFields.SYNC_BACK in normalizedFields) {
                    put("sync_back", settings.syncBack)
                }
                if (WorkspaceSettingFields.KEEP_WORKSPACES in normalizedFields) {
                    put("keep_workspaces", settings.keepWorkspaces)
                }
                if (
                    WorkspaceSettingFields.RESUME_INCLUDE_NON_INTERACTIVE in normalizedFields
                ) {
                    put("resume_include_non_interactive", settings.resumeIncludeNonInteractive)
                }
                if (WorkspaceSettingFields.RUNS_DIR in normalizedFields) {
                    put("runs_dir", settings.runsDir)
                }
                if (WorkspaceSettingFields.CODEX_BIN in normalizedFields) {
                    put("codex_bin", settings.codexBin)
                }
                if (WorkspaceSettingFields.RESUME_SESSION_ID in normalizedFields) {
                    put("resume_session_id", settings.resumeSessionId.ifBlank { "NO" })
                }
            },
        )
        return decode<WorkspaceSettingsResponse>(result).settings
    }

    suspend fun workspaceSettings(
        mac: RelayDevice,
        workspaceId: String,
    ): RemoteRunSettings = decode<WorkspaceSettingsResponse>(
        command(
            mac,
            "workspaces.settings.get",
            buildJsonObject { put("workspace_id", workspaceId) },
        )
    ).settings

    suspend fun defaultConversationSettings(
        mac: RelayDevice,
    ): RemoteRunSettings = decode<DefaultConversationSettingsResponse>(
        command(mac, "settings.defaults.get")
    ).settings

    suspend fun updateDefaultConversationSettings(
        mac: RelayDevice,
        settings: RemoteRunSettings,
        fields: Set<String> = WorkspaceSettingFields.defaultConversation,
    ): RemoteRunSettings = decode<DefaultConversationSettingsResponse>(
        command(
            mac,
            "settings.defaults.update",
            conversationSettingsBody(settings, fields),
        )
    ).settings

    suspend fun modelOptions(
        mac: RelayDevice,
        model: String,
        effort: String,
    ): CodexModelOptions = decode(
        command(
            mac,
            "codex.model_options",
            buildJsonObject {
                if (model.isNotBlank()) put("model", model)
                if (effort.isNotBlank()) put("effort", effort)
            },
        )
    )

    private fun conversationSettingsBody(
        settings: RemoteRunSettings,
        fields: Set<String>,
    ): JsonObject {
        val normalizedFields = WorkspaceSettingFields.normalized(fields)
            .intersect(WorkspaceSettingFields.defaultConversation)
        return buildJsonObject {
            if (WorkspaceSettingFields.AGENTS in normalizedFields) put("agents", settings.agents)
            if (WorkspaceSettingFields.SYNTHESIS_AGENTS in normalizedFields) {
                put("synthesis_agents", settings.synthesisAgents)
            }
            if (WorkspaceSettingFields.FOLLOW_UP_DELAY in normalizedFields) {
                put("follow_up_delay", settings.followUpDelay)
            }
            if (WorkspaceSettingFields.EXECUTION in normalizedFields) {
                put("execution", settings.execution)
            }
            if (WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL in normalizedFields) {
                put("automatic_max_parallel", settings.automaticMaxParallel)
            }
            if (WorkspaceSettingFields.MAX_PARALLEL in normalizedFields) {
                put("max_parallel", settings.maxParallel)
            }
            if (WorkspaceSettingFields.SUBAGENTS in normalizedFields) {
                put("subagents", settings.subagents)
            }
            if (WorkspaceSettingFields.SUBAGENTS_LIMIT in normalizedFields) {
                put("subagents_limit", settings.subagentsLimit)
            }
            if (WorkspaceSettingFields.RECOMMEND_BY in normalizedFields) {
                put("recommend_by", settings.recommendBy)
            }
            if (WorkspaceSettingFields.MODEL in normalizedFields) put("model", settings.model)
            if (WorkspaceSettingFields.EFFORT in normalizedFields) put("effort", settings.effort)
            if (WorkspaceSettingFields.FAST_MODE in normalizedFields) {
                put("fast_mode", settings.fastMode)
            }
            if (WorkspaceSettingFields.SYNTHESIS_MODEL in normalizedFields) {
                put("synthesis_model", settings.synthesisModel)
            }
            if (WorkspaceSettingFields.SYNTHESIS_EFFORT in normalizedFields) {
                put("synthesis_effort", settings.synthesisEffort)
            }
            if (WorkspaceSettingFields.SYNTHESIS_FAST_MODE in normalizedFields) {
                put("synthesis_fast_mode", settings.synthesisFastMode)
            }
            if (WorkspaceSettingFields.SYNC_BACK in normalizedFields) {
                put("sync_back", settings.syncBack)
            }
            if (WorkspaceSettingFields.KEEP_WORKSPACES in normalizedFields) {
                put("keep_workspaces", settings.keepWorkspaces)
            }
        }
    }

    suspend fun resumeSessions(
        mac: RelayDevice,
        workspaceId: String,
        includeNonInteractive: Boolean = false,
        limit: Int = 100,
    ): ResumeSessionList = decode(
        command(
            mac,
            "codex.resume_sessions",
            buildJsonObject {
                put("workspace_id", workspaceId)
                put("include_non_interactive", includeNonInteractive)
                put("limit", limit)
            },
            maxAttempts = 1,
        )
    )

    suspend fun resumeHistory(
        mac: RelayDevice,
        workspaceId: String,
        sessionId: String,
        cursor: Int,
    ): ResumeHistoryPage = decode(
        command(
            mac,
            "codex.resume_history",
            buildJsonObject {
                put("workspace_id", workspaceId)
                put("session_id", sessionId)
                put("cursor", cursor)
                put("limit", 100)
                put("max_bytes", 512 * 1024)
                put("conversation_items_only", true)
            },
        )
    )

    suspend fun runHistory(
        mac: RelayDevice,
        runId: String,
        cursor: Int,
    ): ResumeHistoryPage = decode(
        command(
            mac,
            "runs.history",
            buildJsonObject {
                put("run_id", runId)
                put("cursor", cursor)
                put("limit", 100)
                put("max_bytes", 512 * 1024)
                put("conversation_items_only", true)
            },
        )
    )

    suspend fun startRun(
        mac: RelayDevice,
        workspaceId: String,
        prompt: String,
        agents: Int,
        synthesisAgents: Int,
        settings: RemoteRunSettings,
        resumeSessionId: String,
        confirmLargeRun: Boolean,
        commandId: String? = null,
    ): JsonObject = command(
        mac,
        "turns.start",
        buildJsonObject {
            put("workspace_id", workspaceId)
            put("prompt", prompt)
            put("num_agents", agents)
            put("synthesis_agents", synthesisAgents)
            if (!settings.automaticMaxParallel) {
                put("max_parallel", settings.maxParallel)
            }
            put("serial", settings.execution == "serial")
            put("recommend_by", settings.recommendBy)
            if (settings.model.isNotBlank()) put("model", settings.model)
            if (settings.effort.isNotBlank()) put("effort", settings.effort)
            modeBoolean(settings.fastMode)?.let { put("fast", it) }
            put("follow_up_delay", settings.followUpDelay)
            if (settings.synthesisModel.isNotBlank()) {
                put("synthesis_model", settings.synthesisModel)
            }
            if (settings.synthesisEffort.isNotBlank()) {
                put("synthesis_effort", settings.synthesisEffort)
            }
            modeBoolean(settings.synthesisFastMode)?.let { put("synthesis_fast", it) }
            put("sync_back", settings.syncBack)
            put("keep_workspaces", settings.keepWorkspaces)
            put("confirm_large_run", confirmLargeRun)
            put("subagents", settings.subagents)
            put("subagents_limit", settings.subagentsLimit)
            if (settings.runsDir.isNotBlank()) put("runs_dir", settings.runsDir)
            put("codex_bin", settings.codexBin.ifBlank { "codex" })
            if (resumeSessionId.isNotBlank()) {
                put("resume_session_id", resumeSessionId)
            }
        },
        commandId = commandId,
    )

    suspend fun continueRun(
        mac: RelayDevice,
        runId: String,
        agent: Int,
        prompt: String,
        agents: Int,
        synthesisAgents: Int,
        settings: RemoteRunSettings,
        confirmLargeRun: Boolean,
        commandId: String? = null,
    ): JsonObject = command(
        mac,
        "turns.continue",
        buildJsonObject {
            put("run_id", runId)
            put("agent", agent)
            put("prompt", prompt)
            put("num_agents", agents)
            put("synthesis_agents", synthesisAgents)
            put("automatic_max_parallel", settings.automaticMaxParallel)
            if (!settings.automaticMaxParallel) {
                put("max_parallel", settings.maxParallel)
            }
            put("serial", settings.execution == "serial")
            put("recommend_by", settings.recommendBy)
            put("model", settings.model)
            put("effort", settings.effort)
            put("fast_mode", settings.fastMode)
            put("follow_up_delay", settings.followUpDelay)
            put("synthesis_model", settings.synthesisModel)
            put("synthesis_effort", settings.synthesisEffort)
            put("synthesis_fast_mode", settings.synthesisFastMode)
            put("sync_back", settings.syncBack)
            put("keep_workspaces", settings.keepWorkspaces)
            put("subagents", settings.subagents)
            put("subagents_limit", settings.subagentsLimit)
            put("runs_dir", settings.runsDir)
            put("codex_bin", settings.codexBin.ifBlank { "codex" })
            put("confirm_large_run", confirmLargeRun)
        },
        commandId = commandId,
    )

    suspend fun updateSynthesisSettings(
        mac: RelayDevice,
        runId: String,
        settings: RemoteRunSettings,
    ): JsonObject = command(
        mac,
        "runs.update_synthesis",
        buildJsonObject {
            put("run_id", runId)
            put("synthesis_agents", settings.synthesisAgents)
            put("synthesis_model", settings.synthesisModel)
            put("synthesis_effort", settings.synthesisEffort)
            modeBoolean(settings.synthesisFastMode)?.let {
                put("synthesis_fast", it)
            }
        },
    )

    suspend fun run(mac: RelayDevice, runId: String): JsonObject = command(
        mac,
        "turns.get",
        buildJsonObject {
            put("run_id", runId)
            put("include_events", false)
        },
    )

    fun conversationSnapshot(value: JsonObject): ConversationSnapshot? {
        val payload = value["conversation_snapshot"]
            ?: (value["run"] as? JsonObject)?.get("conversation_snapshot")
            ?: return null
        return runCatching {
            json.decodeFromJsonElement(ConversationSnapshot.serializer(), payload)
        }.getOrNull()
    }

    suspend fun agent(
        mac: RelayDevice,
        runId: String,
        agent: Int,
        cursor: Int,
        finalMessageCursor: Int,
    ): JsonObject {
        var eventLimit = 200
        while (true) {
            try {
                return command(
                    mac,
                    "runs.agent",
                    buildJsonObject {
                        put("run_id", runId)
                        put("agent", agent)
                        put("include_diff", false)
                        put("include_events", true)
                        put("cursor", cursor)
                        put("event_limit", eventLimit)
                        put("event_max_bytes", 512 * 1024)
                        put("transcript_items_only", true)
                        put("final_message_cursor", finalMessageCursor)
                        put("final_message_max_bytes", 192 * 1024)
                    },
                )
            } catch (error: RemoteCommandException) {
                if (error.code != "response_too_large" || eventLimit == 1) throw error
                eventLimit = maxOf(1, eventLimit / 2)
            }
        }
    }

    suspend fun stopRun(mac: RelayDevice, runId: String): JsonObject = command(
        mac,
        "runs.stop",
        buildJsonObject {
            put("run_id", runId)
            put("wait_seconds", 10)
        },
    )

    suspend fun acceptAgent(
        mac: RelayDevice,
        runId: String,
        agent: Int,
        settings: RemoteRunSettings,
    ): JsonObject =
        command(
            mac,
            "turns.finalize",
            buildJsonObject {
                put("run_id", runId)
                put("agent", agent)
                put("sync_back", settings.syncBack)
                put("keep_workspaces", settings.keepWorkspaces)
            },
        )

    suspend fun recoverFinalization(
        mac: RelayDevice,
        runId: String,
        syncWasApplied: Boolean?,
    ): JsonObject = command(
        mac,
        if (syncWasApplied == null) "runs.verify_finalization" else "runs.recover_finalization",
        buildJsonObject {
            put("run_id", runId)
            if (syncWasApplied != null) put("sync_was_applied", syncWasApplied)
        },
    )

    suspend fun finishFinalizationCleanup(
        mac: RelayDevice,
        runId: String,
        agent: Int,
    ): JsonObject = command(
        mac,
        "turns.finalize",
        buildJsonObject {
            put("run_id", runId)
            put("agent", agent)
        },
    )

    suspend fun killAgent(mac: RelayDevice, runId: String, agent: Int): JsonObject =
        command(
            mac,
            "runs.kill_agent",
            buildJsonObject {
                put("run_id", runId)
                put("agent", agent)
            },
        )

    suspend fun retryAgent(mac: RelayDevice, runId: String, agent: Int): JsonObject =
        command(
            mac,
            "runs.retry_agent",
            buildJsonObject {
                put("run_id", runId)
                put("agent", agent)
            },
        )

    suspend fun rejectAgent(
        mac: RelayDevice,
        runId: String,
        agent: Int,
        rejected: Boolean,
    ): JsonObject = command(
        mac,
        "runs.reject_agent",
        buildJsonObject {
            put("run_id", runId)
            put("agent", agent)
            put("rejected", rejected)
        },
    )

    suspend fun addAgents(
        mac: RelayDevice,
        runId: String,
        count: Int,
        confirmLargeRun: Boolean,
    ): JsonObject = command(
        mac,
        "runs.add_agents",
        buildJsonObject {
            put("run_id", runId)
            put("count", count)
            put("confirm_large_run", confirmLargeRun)
        },
    )

    suspend fun discardRun(
        mac: RelayDevice,
        runId: String,
        keepWorkspaces: Boolean,
    ): JsonObject = command(
        mac,
        "runs.discard",
        buildJsonObject {
            put("run_id", runId)
            put("keep_workspaces", keepWorkspaces)
            put("wait_seconds", 20)
        },
    )

    suspend fun diff(
        mac: RelayDevice,
        runId: String,
        agent: Int,
        cursor: Int,
    ): JsonObject = command(
        mac,
        "runs.diff",
        buildJsonObject {
            put("run_id", runId)
            put("agent", agent)
            put("cursor", cursor)
            put("limit", 20_000)
        },
    )

    suspend fun workOperation(
        mac: RelayDevice, runId: String, agent: Int, eventId: Int, cursor: Int,
    ): com.parallelcodexrunner.remote.data.generated.WorkOperationDetailPage = decode(command(
        mac, "runs.operation", buildJsonObject {
            put("run_id", runId)
            put("agent", agent)
            put("event_id", eventId)
            put("cursor", cursor)
        },
    ))

    suspend fun storageSummary(mac: RelayDevice): StorageSummary = decode(
        command(
            mac,
            "storage.summary",
            buildJsonObject { put("limit", 100) },
        )
    )

    suspend fun cleanupRunStorage(
        mac: RelayDevice,
        runId: String,
        discardUnselected: Boolean,
    ): JsonObject = command(
        mac,
        "storage.cleanup",
        buildJsonObject {
            put("run_id", runId)
            put("discard_unselected", discardUnselected)
            put("wait_seconds", 30)
        },
    )

    suspend fun submitFeedback(
        clientFeedbackId: String,
        kind: String,
        description: String,
        diagnostics: JsonObject,
        relatedDeviceId: String,
    ): FeedbackItem = relayApi.submitFeedback(
        clientFeedbackId = clientFeedbackId,
        kind = kind,
        description = description,
        diagnostics = diagnostics,
        relatedDeviceId = relatedDeviceId,
    )

    suspend fun feedback(cursor: String = ""): FeedbackListResponse =
        relayApi.feedback(cursor)

    suspend fun addFeedbackNote(
        feedbackId: String,
        text: String,
        commandId: String,
    ): FeedbackItem = relayApi.addFeedbackNote(
        feedbackId = feedbackId,
        text = text,
        clientNoteId = commandId,
    )

    suspend fun addFeedbackAttachment(
        feedbackId: String,
        contentType: String,
        encoded: String,
        digest: String,
    ): FeedbackItem = relayApi.addFeedbackAttachment(
        feedbackId = feedbackId,
        contentType = contentType,
        encoded = encoded,
    ).also { item ->
        require(item.attachments.any { it.sha256 == digest }) {
            "Relay did not retain the uploaded screenshot"
        }
    }

    suspend fun removeFeedbackAttachment(
        feedbackId: String,
        attachmentId: String,
    ): FeedbackItem = relayApi.removeFeedbackAttachment(
        feedbackId,
        attachmentId,
    )

    suspend fun withdrawFeedback(feedbackId: String): FeedbackItem =
        relayApi.withdrawFeedback(feedbackId)

    suspend fun downloadWorkspaceFile(
        mac: RelayDevice,
        workspaceId: String,
        runId: String,
        agent: Int?,
        reference: String,
        destinationDirectory: File,
        onProgress: (downloadedBytes: Long, totalBytes: Long) -> Unit = { _, _ -> },
    ): WorkspaceFileDownload {
        destinationDirectory.mkdirs()
        check(destinationDirectory.isDirectory) {
            "Cannot prepare local workspace file storage"
        }
        var offset = 0L
        var fileId = ""
        var name = ""
        var relativePath = ""
        var contentType = "application/octet-stream"
        var sizeBytes = -1L
        var line: Int? = null
        var temporary: File? = null
        var output: FileOutputStream? = null
        try {
            while (true) {
                val page = decode<WorkspaceFilePage>(
                    command(
                        mac,
                        "workspace.file.read",
                        buildJsonObject {
                            put("workspace_id", workspaceId)
                            if (runId.isNotBlank() && agent != null) {
                                put("run_id", runId)
                                put("agent", agent)
                            }
                            put("reference", reference)
                            put("offset", offset)
                            put("max_bytes", WORKSPACE_FILE_CHUNK_BYTES)
                            if (fileId.isNotBlank()) put("expected_file_id", fileId)
                        },
                    )
                )
                if (fileId.isBlank()) {
                    check(page.offset == 0L && page.fileId.isNotBlank()) {
                        "The workspace file transfer did not start correctly"
                    }
                    check(page.sizeBytes in 0..MAX_WORKSPACE_FILE_BYTES) {
                        "This file is too large to open on this phone"
                    }
                    fileId = page.fileId
                    name = safeWorkspaceFileName(page.name)
                    relativePath = page.relativePath
                    contentType = page.contentType.ifBlank { "application/octet-stream" }
                    sizeBytes = page.sizeBytes
                    line = page.line
                    temporary = File(
                        destinationDirectory,
                        ".${fileId.take(16)}-${System.nanoTime()}.part",
                    )
                    output = FileOutputStream(temporary)
                }
                check(page.fileId == fileId && page.offset == offset && page.sizeBytes == sizeBytes) {
                    "The workspace file changed while it was being downloaded"
                }
                check(
                    page.name == name &&
                        page.relativePath == relativePath &&
                        page.contentType.ifBlank { "application/octet-stream" } == contentType &&
                        page.line == line
                ) {
                    "The workspace file metadata changed while it was being downloaded"
                }
                val content = Base64.getDecoder().decode(page.contentBase64)
                check(content.size <= WORKSPACE_FILE_CHUNK_BYTES) {
                    "The workspace file page is too large"
                }
                val digest = MessageDigest.getInstance("SHA-256")
                    .digest(content)
                    .joinToString("") { "%02x".format(it) }
                check(digest.equals(page.chunkSha256, ignoreCase = true)) {
                    "The workspace file page failed its integrity check"
                }
                output?.write(content)
                offset += content.size
                check(page.nextOffset == offset && offset <= sizeBytes) {
                    "The workspace file page sequence is invalid"
                }
                onProgress(offset, sizeBytes)
                if (!page.hasMore) break
                check(content.isNotEmpty()) { "The workspace file transfer made no progress" }
            }
            output?.fd?.sync()
            output?.close()
            output = null
            check(offset == sizeBytes) { "The workspace file download is incomplete" }
            val destination = uniqueWorkspaceFile(destinationDirectory, name)
            check(temporary?.renameTo(destination) == true) {
                "Cannot finish the workspace file download"
            }
            temporary = null
            return WorkspaceFileDownload(
                file = destination,
                name = name,
                relativePath = relativePath,
                contentType = contentType,
                sizeBytes = sizeBytes,
                line = line,
            )
        } finally {
            output?.close()
            temporary?.delete()
        }
    }

    private suspend fun command(
        mac: RelayDevice,
        action: String,
        body: JsonObject = JsonObject(emptyMap()),
        expectedRevision: Int? = null,
        commandId: String? = null,
        checkCapability: Boolean = true,
        maxAttempts: Int = 2,
    ): JsonObject {
        if (shouldRejectOfflineRead(mac.online, action)) {
            throw RemoteCommandException(
                code = "machine_offline",
                message = "这台设备当前未连接，请确认机器端在线后重试。",
            )
        }
        if (checkCapability) {
            val cached = capabilitiesByMac[mac.deviceId] ?: systemCapabilities(mac)
            if (!cached.supports(action)) {
                val refreshed = systemCapabilities(mac, refresh = true)
                if (!refreshed.supports(action)) {
                    throw RemoteCommandException(
                        code = "unsupported_action",
                        message = "目标设备上的 Parallel Codex 版本不支持此功能，请更新机器端。",
                    )
                }
            }
        }
        val responseValue = transport.command(
            mac,
            action,
            body,
            expectedRevision,
            timeoutMillis = remoteCommandTimeout(action),
            commandId = commandId,
            maxAttempts = maxAttempts,
        )

        val response = json.decodeFromJsonElement(RemoteResponse.serializer(), responseValue)
        if (!response.ok) {
            throw RemoteCommandException(
                code = response.error?.code.orEmpty(),
                message = response.error?.message ?: "Remote command failed",
            )
        }
        return response.body ?: JsonObject(emptyMap())
    }

    private inline fun <reified T> decode(value: JsonObject): T =
        json.decodeFromJsonElement(serializer<T>(), value)

    private fun modeBoolean(value: String): Boolean? = when (value.lowercase()) {
        "on" -> true
        "off" -> false
        else -> null
    }

    private fun remoteCommandTimeout(action: String): Long = when (action) {
        "runs.start",
        "runs.continue",
        "runs.accept_agent",
        "turns.start",
        "turns.continue",
        "turns.finalize",
        "runs.recover_finalization",
        "runs.discard",
        "storage.cleanup",
        "runs.add_agents",
        "runs.retry_agent",
        "runs.stop",
        -> 15 * 60_000L

        "runs.estimate",
        "filesystem.inspect",
        "storage.summary",
        "conversations.list",
        -> 3 * 60_000L

        "codex.resume_sessions" -> 30_000L

        else -> 60_000L
    }
}

internal fun followUpReplaceBody(
    workspaceId: String,
    runId: String,
    items: List<PersistedQueuedFollowUp>,
    selectedAgent: Int?,
    resetCountdown: Boolean,
): JsonObject = buildJsonObject {
    put("workspace_id", workspaceId)
    put("run_id", runId)
    putJsonArray("items") {
        items.forEach { item -> add(followUpItemBody(item)) }
    }
    selectedAgent?.let { put("selected_agent", it) }
    put("reset_countdown", resetCountdown)
}

internal fun conversationListBody(
    workspaceId: String,
    cursor: String = "",
    limit: Int = 100,
): JsonObject = buildJsonObject {
    put("workspace_id", workspaceId)
    put("limit", limit.coerceIn(1, 100))
    if (cursor.isNotBlank()) put("cursor", cursor)
}

internal fun followUpEnqueueBody(
    workspaceId: String,
    runId: String,
    item: PersistedQueuedFollowUp,
    selectedAgent: Int?,
): JsonObject = buildJsonObject {
    put("workspace_id", workspaceId)
    put("run_id", runId)
    put("item", followUpItemBody(item))
    selectedAgent?.let { put("selected_agent", it) }
}

private fun followUpItemBody(item: PersistedQueuedFollowUp): JsonObject =
    buildJsonObject {
        put("id", item.id)
        put("prompt", item.prompt)
        put(
            "settings",
            Json.encodeToJsonElement(
                RemoteFollowUpSettings.serializer(),
                RemoteFollowUpSettings.from(item.settings ?: RemoteRunSettings()),
            ),
        )
        put("attempt", item.attempt.coerceAtLeast(0))
    }

private val RETRYABLE_REMOTE_COMMAND_CODES = setOf(
    "machine_offline",
    "pcr_operation_pending",
)

private val READ_ONLY_REMOTE_ACTIONS = setOf(
    "system.health",
    "system.capabilities",
    "filesystem.roots",
    "filesystem.list",
    "filesystem.inspect",
    "workspaces.list",
    "workspaces.settings.get",
    "workspace.file.read",
    "settings.defaults.get",
    "devices.list",
    "runs.estimate",
    "runs.list",
    "runs.get",
    "conversations.list",
    "conversations.get",
    "followups.get",
    "followups.list",
    "turns.get",
    "runs.history",
    "runs.events",
    "runs.agent",
    "runs.operation",
    "runs.diff",
    "storage.summary",
    "codex.resume_sessions",
    "codex.resume_history",
    "codex.model_options",
    "prompt_history.list",
    "feedback.list",
    "feedback.get",
    "feedback.attachment",
    "relay.status",
)

private const val WORKSPACE_FILE_CHUNK_BYTES = 384 * 1024
private const val MAX_WORKSPACE_FILE_BYTES = 256L * 1024L * 1024L

private fun safeWorkspaceFileName(value: String): String {
    val name = value.trim().substringAfterLast('/').substringAfterLast('\\')
    return name.takeIf {
        it.isNotBlank() && it !in setOf(".", "..") && '\u0000' !in it
    } ?: "workspace-file"
}

private fun uniqueWorkspaceFile(directory: File, name: String): File {
    val base = name.substringBeforeLast('.', name)
    val extension = name.substringAfterLast('.', "").takeIf { it.isNotBlank() }
    var candidate = File(directory, name)
    var counter = 2
    while (candidate.exists()) {
        val suffix = extension?.let { ".${it}" }.orEmpty()
        candidate = File(directory, "$base ($counter)$suffix")
        counter += 1
    }
    return candidate
}
