package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.WorkspaceSettingFields
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

internal fun MainViewModel.scheduleWorkspaceSettingsSave(retry: Boolean = false) {
    val snapshot = state.value
    val mac = selectedMac() ?: return
    val workspaceId = snapshot.selectedWorkspaceId ?: return
    val settings = settingsSnapshot(snapshot)
    val key = workspaceSettingsKey(mac.deviceId, workspaceId)
    val fields = dirtyWorkspaceSettingFields[key]?.toSet()
        ?: if (key in localSettingsByWorkspace) {
            WorkspaceSettingFields.all.also {
                dirtyWorkspaceSettingFields[key] = it.toMutableSet()
            }
        } else {
            emptySet()
        }
    if (fields.isEmpty()) return
    scheduleWorkspaceSettingsSave(
        mac = mac,
        workspaceId = workspaceId,
        settings = settings,
        changedFields = fields,
        retry = retry,
    )
}

internal fun MainViewModel.scheduleWorkspaceSettingsSave(
    mac: RelayDevice,
    workspaceId: String,
    settings: RemoteRunSettings,
    changedFields: Set<String>,
    retry: Boolean = false,
) {
    if (changedFields.isEmpty()) return
    val key = workspaceSettingsKey(mac.deviceId, workspaceId)
    localSettingsByWorkspace[key] = settings
    dirtyWorkspaceSettingFields
        .getOrPut(key, ::mutableSetOf)
        .addAll(changedFields)
    val fields = dirtyWorkspaceSettingFields.getValue(key).toSet()
    scheduleMobileStateSave()
    if (!retry) {
        settingsSaveFailures.remove(key)
        reportedSettingsSaveFailures.remove(key)
    }
    val requestId = (settingsSaveRequestIds[key] ?: 0) + 1
    settingsSaveRequestIds[key] = requestId
    settingsSaveJobs[key]?.cancel()
    settingsSaveJobs[key] = viewModelScope.launch {
        var retryDelay = 250L
        try {
            while (settingsSaveRequestIds[key] == requestId) {
                delay(retryDelay)
                try {
                    val saved = repository.updateWorkspaceSettings(
                        mac = mac,
                        workspaceId = workspaceId,
                        settings = settings,
                        fields = fields,
                    )
                    if (settingsSaveRequestIds[key] == requestId) {
                        // Retire the pending edit only after the cold-start cache contains
                        // the acknowledged settings, including the draft's resume identity.
                        domainRepositories.workspaces.cacheSavedSettings(
                            mac.deviceId, workspaceId, saved,
                        )
                        mutableState.update { current ->
                            if (current.selectedMacId != mac.deviceId) current
                            else current.copy(workspaces = current.workspaces.map { workspace ->
                                if (workspace.workspaceId == workspaceId) workspace.copy(settings = saved)
                                else workspace
                            })
                        }
                        localSettingsByWorkspace.remove(key)
                        dirtyWorkspaceSettingFields.remove(key)
                        settingsSaveFailures.remove(key)
                        reportedSettingsSaveFailures.remove(key)
                        scheduleMobileStateSave()
                        if (WorkspaceSettingFields.FOLLOW_UP_DELAY in fields) {
                            try {
                                refreshDaemonFollowUpQueue(
                                    mac, workspaceId, fallbackRunId = null,
                                    migrateLocalQueue = false,
                                )
                            } catch (error: CancellationException) {
                                throw error
                            } catch (_: Throwable) {
                                // The durable update succeeded; the queue monitor can retry its projection.
                            }
                        }
                    }
                    break
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Throwable) {
                    if (settingsSaveRequestIds[key] != requestId) break
                    settingsSaveFailures.add(key)
                    localSettingsByWorkspace[key] = settings
                    if (state.value.selectedMacId == mac.deviceId &&
                        state.value.selectedWorkspaceId == workspaceId &&
                        reportedSettingsSaveFailures.add(key)
                    ) {
                        showError(state.value.appLanguage.text(
                            "设置尚未同步到目标设备，将自动重试：",
                            "Settings have not reached the target device yet and will retry automatically: ",
                        ) + friendlyError(error, state.value.appLanguage))
                    }
                    retryDelay = (retryDelay * 2).coerceAtMost(30_000L)
                }
            }
        } catch (_: CancellationException) {
            return@launch
        } finally {
            if (settingsSaveRequestIds[key] == requestId) {
                settingsSaveJobs.remove(key)
            }
        }
    }
}

internal fun MainViewModel.scheduleActiveSynthesisSettingsUpdate() {
    synthesisSettingsUpdateJob?.cancel()
    val snapshot = state.value
    val runId = snapshot.visibleRunId ?: return
    if (!snapshot.canUpdateSynthesis || isTerminalRunStatus(snapshot.runStatus)) return
    val mac = selectedMac() ?: return
    val generation = contextGeneration
    synthesisSettingsUpdateJob = viewModelScope.launch {
        var retryDelay = 200L
        var attempt = 0
        while (attempt < 5) {
            delay(retryDelay)
            val current = state.value
            if (generation != contextGeneration ||
                current.visibleRunId != runId ||
                !current.canUpdateSynthesis
            ) return@launch
            try {
                repository.updateSynthesisSettings(
                    mac = mac,
                    runId = runId,
                    settings = settingsSnapshot(current),
                )
                if (generation == contextGeneration && state.value.visibleRunId == runId) {
                    loadRun(runId)
                }
                return@launch
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (generation != contextGeneration || state.value.visibleRunId != runId) {
                    return@launch
                }
                attempt += 1
                if (attempt == 1) {
                    showError(state.value.appLanguage.text(
                        "综合阶段设置尚未应用，正在自动重试：",
                        "Synthesis settings have not been applied yet and are retrying automatically: ",
                    ) + friendlyError(error, state.value.appLanguage))
                } else if (attempt >= 5) {
                    showError(state.value.appLanguage.text(
                        "综合阶段设置未能应用，请调整后重试：",
                        "Synthesis settings could not be applied. Adjust them and try again: ",
                    ) + friendlyError(error, state.value.appLanguage))
                }
                retryDelay = (retryDelay * 2).coerceAtMost(3_200L)
            }
        }
    }
}
