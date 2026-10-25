package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

private val DIFF_FILE_LINE = Regex("^([AMDT])\\s{2}(.+)$")
private val DIFF_TOTAL_LINE = Regex("(?m)^(\\d+) files? changed(?: |$)")

internal fun parseAgentDiffSummary(text: String): AgentDiffSummary {
    if (text.trim() == "No workspace changes.") return AgentDiffSummary()
    val headerEnd = text.indexOf("\n\n")
    val header = if (headerEnd >= 0) text.substring(0, headerEnd) else text
    val changes = header.lineSequence().mapNotNull { line ->
        val match = DIFF_FILE_LINE.matchEntire(line) ?: return@mapNotNull null
        AgentFileChange(
            status = match.groupValues[1],
            path = match.groupValues[2],
        )
    }.toList()
    val totalFiles = DIFF_TOTAL_LINE.find(text)
        ?.groupValues
        ?.get(1)
        ?.toIntOrNull()
        ?: changes.size
    return AgentDiffSummary(
        changes = changes,
        totalFiles = totalFiles.coerceAtLeast(changes.size),
        truncated = headerEnd < 0 || totalFiles > changes.size,
    )
}

internal fun MainViewModel.prefetchAgentDiffSummaries(
    mac: com.parallelcodexrunner.remote.data.RelayDevice,
    key: WorkspaceRunKey,
    agents: List<AgentSummary>,
) {
    if (diffSummaryPrefetchJobs[key]?.isActive == true) return
    val existing = runStateStore[key]?.agentDiffSummaries.orEmpty()
    val targets = agents.filter { agent ->
        isTerminalAgentStatus(agent.status) &&
            agent.workspaceAvailable &&
            agent.number !in existing
    }
    if (targets.isEmpty()) return
    diffSummaryPrefetchJobs[key] = viewModelScope.launch {
        try {
            targets.forEach { agent ->
                val summary = try {
                    val page = repository.diff(mac, key.runId, agent.number, cursor = 0)
                    parseAgentDiffSummary(
                        page["text"]?.jsonPrimitive?.contentOrNull.orEmpty()
                    )
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Throwable) {
                    AgentDiffSummary(unavailable = true)
                }
                val updated = runStateStore.update(key) { current ->
                    current.copy(
                        agentDiffSummaries = current.agentDiffSummaries +
                            (agent.number to summary)
                    )
                }
                if (isVisibleRun(key)) {
                    mutableState.update { current -> current.projectRun(updated) }
                }
            }
        } finally {
            diffSummaryPrefetchJobs.remove(key)
        }
    }
}

fun MainViewModel.showAgentDiff(agent: Int) {
    val snapshot = state.value
    val runId = snapshot.visibleRunId ?: return
    val mac = selectedMac() ?: return
    if (snapshot.agentSummaries.none { it.number == agent }) return
    val generation = contextGeneration
    val requestId = ++diffRequestId
    val agentName = agentDisplayName(
        agent,
        snapshot.agentSummaries,
        snapshot.appLanguage,
    )
    mutableState.update {
        it.copy(
            diffTitle = it.appLanguage.text(
                "$agentName 的修改",
                "$agentName changes",
            ),
            diffText = "",
            diffLoading = true,
        )
    }
    viewModelScope.launch {
        try {
            var cursor = 0
            val contents = StringBuilder()
            while (true) {
                val page = repository.diff(mac, runId, agent, cursor)
                val chunk = page["text"]?.jsonPrimitive?.contentOrNull.orEmpty()
                if (contents.length + chunk.length > MAX_MOBILE_DIFF_CHARS) {
                    if (requestId == diffRequestId &&
                        generation == contextGeneration &&
                        state.value.visibleRunId == runId
                    ) {
                        mutableState.update { current ->
                            current.copy(
                                diffText = current.appLanguage.text(
                                    "文件修改过大，请在目标设备或命令行中查看完整 Patch。",
                                    "The file changes are too large to display here. View the complete patch on the target device or in the CLI.",
                                ),
                                diffLoading = false,
                            )
                        }
                    }
                    return@launch
                }
                contents.append(chunk)
                val hasMore = page["has_more"]?.jsonPrimitive?.booleanOrNull == true
                val nextCursor = page["next_cursor"]?.jsonPrimitive?.intOrNull ?: cursor
                if (!hasMore || nextCursor <= cursor) break
                cursor = nextCursor
            }
            if (requestId == diffRequestId &&
                generation == contextGeneration &&
                state.value.selectedMacId == mac.deviceId &&
                state.value.visibleRunId == runId
            ) {
                mutableState.update { current ->
                    current.copy(
                        diffText = contents.toString().ifBlank {
                            current.appLanguage.text(
                                "没有发现文件修改。",
                                "No file changes were found.",
                            )
                        },
                        diffLoading = false,
                    )
                }
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (requestId == diffRequestId && generation == contextGeneration) {
                mutableState.update {
                    it.copy(
                        diffText = friendlyError(error, state.value.appLanguage),
                        diffLoading = false,
                    )
                }
            }
        }
    }
}

fun MainViewModel.clearAgentDiff() {
    diffRequestId += 1
    mutableState.update { it.copy(diffText = null, diffLoading = false) }
}

private const val MAX_MOBILE_DIFF_CHARS = 500_000
