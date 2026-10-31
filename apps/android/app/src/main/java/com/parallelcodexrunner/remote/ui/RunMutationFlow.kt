package com.parallelcodexrunner.remote.ui

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

internal suspend fun performRunMutation(
    mutate: suspend () -> Unit,
    release: () -> Unit,
    refresh: suspend () -> Unit,
) {
    try {
        mutate()
    } finally {
        release()
    }
    refresh()
}

internal fun WorkspaceRunSnapshot.applyingRetryReceipt(
    key: WorkspaceRunKey,
    agent: Int,
    receipt: JsonObject,
): WorkspaceRunSnapshot {
    if (receipt["run_id"]?.jsonPrimitive?.contentOrNull != key.runId) return this
    val status = receipt["status"]?.jsonPrimitive?.contentOrNull ?: return this
    val resumed = receipt["agents"]?.jsonArray?.map { it.jsonObject }
        ?.firstOrNull { it["agent"]?.jsonPrimitive?.intOrNull == agent } ?: return this
    val agentStatus = resumed["status"]?.jsonPrimitive?.contentOrNull ?: return this
    return copy(
        status = status,
        workerActive = receipt["worker_active"]?.jsonPrimitive?.booleanOrNull == true,
        reviewSelectionEstablished = false,
        agents = agents.map { candidate ->
            if (candidate.number != agent) candidate else candidate.copy(
                status = agentStatus, canRetry = false, canContinue = false, canAccept = false,
            )
        },
    )
}

internal fun WorkspaceRunSnapshot.applyingSaveReceipt(
    key: WorkspaceRunKey,
    agent: Int,
    receipt: JsonObject,
): WorkspaceRunSnapshot {
    if (receipt["run_id"]?.jsonPrimitive?.contentOrNull != key.runId) return this
    val status = receipt["status"]?.jsonPrimitive?.contentOrNull ?: return this
    val saved = receipt["finalized_agent"]?.jsonPrimitive?.intOrNull
    val saving = receipt["finalizing_agent"]?.jsonPrimitive?.intOrNull
    if (status == "finalizing" && (saving == agent || saved == agent)) {
        return copy(status = status, workerActive = true)
    }
    if (status != "finalized" || saved != agent) return this
    val session = promotedSessionId(receipt).orEmpty()
    return copy(
        status = status,
        finalizedAgent = saved,
        adoptedSessionId = session.ifEmpty { adoptedSessionId },
        workerActive = receipt["worker_active"]?.jsonPrimitive?.booleanOrNull == true,
        canAddAgents = false,
        canUpdateSynthesis = false,
        agents = agents.map { candidate ->
            candidate.copy(canAccept = false, canRetry = false,
                canContinue = candidate.number == saved && session.isNotBlank())
        },
    )
}
