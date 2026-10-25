package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.CodexModelOptions
import com.parallelcodexrunner.remote.data.RelayDevice
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.WorkspaceSettingFields
import com.parallelcodexrunner.remote.data.changedFields
import com.parallelcodexrunner.remote.data.mergingRemote
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

internal suspend fun <T> remoteResult(block: suspend () -> T): Result<T> = try {
    Result.success(block())
} catch (error: CancellationException) {
    throw error
} catch (error: Throwable) {
    Result.failure(error)
}

private fun MainViewModel.defaultOptionsError(
    primary: Result<CodexModelOptions>,
    synthesis: Result<CodexModelOptions>,
): String? {
    val failures = buildList {
        primary.exceptionOrNull()?.let {
            add(state.value.appLanguage.text("主阶段：", "Agents: ") +
                friendlyError(it, state.value.appLanguage))
        }
        synthesis.exceptionOrNull()?.let {
            add(state.value.appLanguage.text("综合阶段：", "Synthesis: ") +
                friendlyError(it, state.value.appLanguage))
        }
    }
    return failures.takeIf { it.isNotEmpty() }?.joinToString(
        prefix = state.value.appLanguage.text(
            "Codex 选项未完整加载（",
            "Some Codex options did not load (",
        ),
        postfix = state.value.appLanguage.text("）", ")"),
        separator = state.value.appLanguage.text("；", "; "),
    )
}

fun MainViewModel.loadDefaultConversationSettings(macId: String) {
    val mac = state.value.macs.firstOrNull { it.deviceId == macId } ?: run {
        mutableState.update { current ->
            current.copy(defaultSettingsError = current.appLanguage.text(
                "这台设备已断开或被移除。",
                "This device is offline or has been removed.",
            ))
        }
        return
    }
    val requestId = ++defaultSettingsRequestId
    val cached = defaultSettingsByMac[macId]
    val saveInProgress = defaultSettingsSaveJobs[macId]?.isActive == true
    mutableState.update {
        it.copy(
            defaultSettingsMacId = macId,
            defaultRunSettings = cached ?: RemoteRunSettings(),
            defaultPrimaryModelOptions = defaultPrimaryOptionsByMac[macId],
            defaultSynthesisModelOptions = defaultSynthesisOptionsByMac[macId],
            defaultSettingsLoading = !saveInProgress,
            defaultSettingsSaving = saveInProgress,
            defaultSettingsError = defaultSettingsErrorsByMac[macId],
            defaultSettingsOptionsError = defaultOptionsErrorsByMac[macId],
        )
    }
    if (saveInProgress) return
    viewModelScope.launch {
        try {
            val remoteSettings = repository.defaultConversationSettings(mac)
                .asDefaultConversationSettings()
            val pendingFields = dirtyDefaultSettingFieldsByMac[macId]
                ?.let(WorkspaceSettingFields::normalized)
                ?.intersect(WorkspaceSettingFields.defaultConversation)
                .orEmpty()
            val localSettings = defaultSettingsByMac[macId]
            val settings = if (localSettings != null && pendingFields.isNotEmpty()) {
                localSettings.mergingRemote(
                    remote = remoteSettings,
                    preservingFields = pendingFields,
                ).asDefaultConversationSettings()
            } else {
                remoteSettings
            }
            val (primaryResult, synthesisResult) = coroutineScope {
                val primaryRequest = async {
                    remoteResult {
                        repository.modelOptions(mac, settings.model, settings.effort)
                    }
                }
                val synthesisRequest = async {
                    remoteResult {
                        repository.modelOptions(
                            mac,
                            settings.synthesisModel,
                            settings.synthesisEffort,
                        )
                    }
                }
                primaryRequest.await() to synthesisRequest.await()
            }
            if (requestId != defaultSettingsRequestId ||
                state.value.defaultSettingsMacId != macId
            ) return@launch
            val normalized = settings.asDefaultConversationSettings()
            val primary = primaryResult.getOrNull()
            val synthesis = synthesisResult.getOrNull()
            val optionsError = defaultOptionsError(primaryResult, synthesisResult)
            val clearPrimaryEffort = primary != null &&
                normalized.effort.isNotBlank() &&
                primary.efforts.none { it.value == normalized.effort }
            val clearSynthesisEffort = synthesis != null &&
                normalized.synthesisEffort.isNotBlank() &&
                synthesis.efforts.none { it.value == normalized.synthesisEffort }
            val validated = normalized.copy(
                effort = if (clearPrimaryEffort) "" else normalized.effort,
                synthesisEffort = if (clearSynthesisEffort) {
                    ""
                } else {
                    normalized.synthesisEffort
                },
            ).asDefaultConversationSettings()
            val validationFields = buildSet {
                if (clearPrimaryEffort) add(WorkspaceSettingFields.EFFORT)
                if (clearSynthesisEffort) add(WorkspaceSettingFields.SYNTHESIS_EFFORT)
            }
            if (validationFields.isNotEmpty()) {
                dirtyDefaultSettingFieldsByMac
                    .getOrPut(macId, ::mutableSetOf)
                    .addAll(validationFields)
            }
            defaultSettingsByMac[macId] = validated
            primary?.let { defaultPrimaryOptionsByMac[macId] = it }
            synthesis?.let { defaultSynthesisOptionsByMac[macId] = it }
            if (optionsError == null) {
                defaultOptionsErrorsByMac.remove(macId)
            } else {
                defaultOptionsErrorsByMac[macId] = optionsError
            }
            defaultSettingsErrorsByMac.remove(macId)
            scheduleMobileStateSave()
            mutableState.update {
                it.copy(
                    defaultRunSettings = validated,
                    defaultPrimaryModelOptions = primary
                        ?: defaultPrimaryOptionsByMac[macId],
                    defaultSynthesisModelOptions = synthesis
                        ?: defaultSynthesisOptionsByMac[macId],
                    defaultSettingsLoading = false,
                    defaultSettingsError = null,
                    defaultSettingsOptionsError = optionsError,
                )
            }
            if (dirtyDefaultSettingFieldsByMac[macId]?.isNotEmpty() == true) {
                scheduleDefaultConversationSettingsSave()
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (requestId != defaultSettingsRequestId ||
                state.value.defaultSettingsMacId != macId
            ) return@launch
            val message = friendlyError(error, state.value.appLanguage)
            defaultSettingsErrorsByMac[macId] = message
            mutableState.update {
                it.copy(
                    defaultSettingsLoading = false,
                    defaultSettingsError = message,
                )
            }
        }
    }
}

fun MainViewModel.dismissDefaultConversationSettings() {
    defaultSettingsRequestId += 1
    mutableState.update {
        it.copy(
            defaultSettingsMacId = null,
            defaultSettingsLoading = false,
            defaultSettingsSaving = false,
            defaultSettingsError = null,
            defaultSettingsOptionsError = null,
        )
    }
}

fun MainViewModel.retryDefaultConversationSettings() {
    val macId = state.value.defaultSettingsMacId ?: return
    if (dirtyDefaultSettingFieldsByMac[macId]?.isNotEmpty() == true) {
        scheduleDefaultConversationSettingsSave()
    } else {
        loadDefaultConversationSettings(macId)
    }
}

fun MainViewModel.reloadDefaultConversationModelOptions() {
    if (state.value.defaultSettingsMacId == null) return
    refreshDefaultConversationModelOptions()
}

fun MainViewModel.updateDefaultConversationSettings(
    refreshModelOptions: Boolean = false,
    transform: (RemoteRunSettings) -> RemoteRunSettings,
) {
    val macId = state.value.defaultSettingsMacId ?: return
    var changedFields = emptySet<String>()
    mutableState.update { current ->
        val previous = current.defaultRunSettings.asDefaultConversationSettings()
        val updated = transform(previous).asDefaultConversationSettings()
        changedFields = updated.changedFields(previous)
            .intersect(WorkspaceSettingFields.defaultConversation)
        current.copy(
            defaultRunSettings = updated,
            defaultSettingsError = null,
        )
    }
    defaultSettingsByMac[macId] = state.value.defaultRunSettings
    defaultSettingsErrorsByMac.remove(macId)
    if (changedFields.isNotEmpty()) {
        defaultSettingsAutoRetryAfter.remove(macId)
        dirtyDefaultSettingFieldsByMac
            .getOrPut(macId, ::mutableSetOf)
            .addAll(changedFields)
        scheduleDefaultConversationSettingsSave()
    }
    if (refreshModelOptions) refreshDefaultConversationModelOptions()
}

private fun MainViewModel.refreshDefaultConversationModelOptions() {
    val macId = state.value.defaultSettingsMacId ?: return
    val mac = state.value.macs.firstOrNull { it.deviceId == macId } ?: return
    val requestId = ++defaultSettingsRequestId
    val settings = state.value.defaultRunSettings
    mutableState.update { it.copy(defaultSettingsOptionsError = null) }
    viewModelScope.launch {
        val (primaryResult, synthesisResult) = coroutineScope {
            val primaryRequest = async {
                remoteResult {
                    repository.modelOptions(mac, settings.model, settings.effort)
                }
            }
            val synthesisRequest = async {
                remoteResult {
                    repository.modelOptions(
                        mac,
                        settings.synthesisModel,
                        settings.synthesisEffort,
                    )
                }
            }
            primaryRequest.await() to synthesisRequest.await()
        }
        if (requestId != defaultSettingsRequestId ||
            state.value.defaultSettingsMacId != macId
        ) return@launch
        val primary = primaryResult.getOrNull()
        val synthesis = synthesisResult.getOrNull()
        val optionsError = defaultOptionsError(primaryResult, synthesisResult)
        val current = state.value.defaultRunSettings
        val clearPrimaryEffort = primary != null &&
            current.effort.isNotBlank() &&
            primary.efforts.none { it.value == current.effort }
        val clearSynthesisEffort = synthesis != null &&
            current.synthesisEffort.isNotBlank() &&
            synthesis.efforts.none { it.value == current.synthesisEffort }
        primary?.let { defaultPrimaryOptionsByMac[macId] = it }
        synthesis?.let { defaultSynthesisOptionsByMac[macId] = it }
        if (optionsError == null) {
            defaultOptionsErrorsByMac.remove(macId)
        } else {
            defaultOptionsErrorsByMac[macId] = optionsError
        }
        val updatedSettings = current.copy(
            effort = if (clearPrimaryEffort) "" else current.effort,
            synthesisEffort = if (clearSynthesisEffort) {
                ""
            } else {
                current.synthesisEffort
            },
        ).asDefaultConversationSettings()
        defaultSettingsByMac[macId] = updatedSettings
        mutableState.update {
            it.copy(
                defaultRunSettings = updatedSettings,
                defaultPrimaryModelOptions = primary
                    ?: it.defaultPrimaryModelOptions,
                defaultSynthesisModelOptions = synthesis
                    ?: it.defaultSynthesisModelOptions,
                defaultSettingsOptionsError = optionsError,
            )
        }
        if (clearPrimaryEffort || clearSynthesisEffort) {
            defaultSettingsAutoRetryAfter.remove(macId)
            val fields = dirtyDefaultSettingFieldsByMac
                .getOrPut(macId, ::mutableSetOf)
            if (clearPrimaryEffort) fields.add(WorkspaceSettingFields.EFFORT)
            if (clearSynthesisEffort) {
                fields.add(WorkspaceSettingFields.SYNTHESIS_EFFORT)
            }
            scheduleDefaultConversationSettingsSave()
        }
    }
}

private fun MainViewModel.scheduleDefaultConversationSettingsSave(
    requestedMacId: String? = null,
) {
    val macId = requestedMacId ?: state.value.defaultSettingsMacId ?: return
    val mac = state.value.macs.firstOrNull { it.deviceId == macId } ?: return
    val requestId = (defaultSettingsSaveRequestIds[macId] ?: 0) + 1
    defaultSettingsSaveRequestIds[macId] = requestId
    val settings = if (state.value.defaultSettingsMacId == macId) {
        state.value.defaultRunSettings
    } else {
        defaultSettingsByMac[macId] ?: return
    }
    val fields = dirtyDefaultSettingFieldsByMac[macId]
        ?.let(WorkspaceSettingFields::normalized)
        ?.intersect(WorkspaceSettingFields.defaultConversation)
        .orEmpty()
    if (fields.isEmpty()) return
    dirtyDefaultSettingFieldsByMac[macId] = fields.toMutableSet()
    defaultSettingsByMac[macId] = settings
    scheduleMobileStateSave()
    defaultSettingsSaveJobs[macId]?.cancel()
    if (state.value.defaultSettingsMacId == macId) {
        mutableState.update { it.copy(defaultSettingsSaving = true) }
    }
    defaultSettingsSaveJobs[macId] = viewModelScope.launch {
        var retryDelay = 300L
        var attempt = 0
        try {
            while (requestId == defaultSettingsSaveRequestIds[macId]) {
                delay(retryDelay)
                try {
                    val saved = repository.updateDefaultConversationSettings(
                        mac = mac,
                        settings = settings,
                        fields = fields,
                    )
                        .asDefaultConversationSettings()
                    if (requestId != defaultSettingsSaveRequestIds[macId]) return@launch
                    defaultSettingsByMac[macId] = saved
                    defaultSettingsAutoRetryAfter.remove(macId)
                    defaultSettingsErrorsByMac.remove(macId)
                    dirtyDefaultSettingFieldsByMac.remove(macId)
                    scheduleMobileStateSave()
                    if (state.value.defaultSettingsMacId == macId) {
                        mutableState.update {
                            it.copy(
                                defaultRunSettings = saved,
                                defaultSettingsSaving = false,
                                defaultSettingsError = null,
                            )
                        }
                    }
                    return@launch
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Throwable) {
                    if (requestId != defaultSettingsSaveRequestIds[macId]) return@launch
                    attempt += 1
                    val detail = friendlyError(error, state.value.appLanguage)
                    val message = state.value.appLanguage.text(
                        if (attempt < 5) {
                            "默认设置尚未同步，正在自动重试：$detail"
                        } else {
                            "默认设置将在设备恢复后继续同步：$detail"
                        },
                        if (attempt < 5) {
                            "Default settings have not synced yet. Retrying automatically: $detail"
                        } else {
                            "Default settings will continue syncing when the device reconnects: $detail"
                        },
                    )
                    defaultSettingsErrorsByMac[macId] = message
                    if (state.value.defaultSettingsMacId == macId) {
                        mutableState.update {
                            it.copy(
                                defaultSettingsSaving = attempt < 5,
                                defaultSettingsError = message,
                            )
                        }
                    }
                    if (attempt >= 5) return@launch
                    retryDelay = (retryDelay * 2).coerceAtMost(3_000L)
                }
            }
        } catch (error: CancellationException) {
            throw error
        } finally {
            if (requestId == defaultSettingsSaveRequestIds[macId]) {
                defaultSettingsSaveJobs.remove(macId)
                if (attempt >= 5) {
                    defaultSettingsAutoRetryAfter[macId] =
                        System.currentTimeMillis() + 60_000L
                }
            }
        }
    }
}

internal fun MainViewModel.retryDirtyDefaultSettings(macs: List<RelayDevice>) {
    val now = System.currentTimeMillis()
    macs.asSequence()
        .filter { it.online != false }
        .filter { dirtyDefaultSettingFieldsByMac[it.deviceId]?.isNotEmpty() == true }
        .filter { defaultSettingsSaveJobs[it.deviceId]?.isActive != true }
        .filter { now >= (defaultSettingsAutoRetryAfter[it.deviceId] ?: 0L) }
        .forEach { scheduleDefaultConversationSettingsSave(it.deviceId) }
}

fun MainViewModel.resetDefaultSettingsContext(cancelSaves: Boolean = false) {
    defaultSettingsRequestId += 1
    if (cancelSaves) {
        defaultSettingsSaveJobs.values.forEach { it.cancel() }
        defaultSettingsSaveJobs.clear()
        defaultSettingsSaveRequestIds.clear()
        defaultSettingsAutoRetryAfter.clear()
        defaultSettingsByMac.clear()
        defaultPrimaryOptionsByMac.clear()
        defaultSynthesisOptionsByMac.clear()
        defaultSettingsErrorsByMac.clear()
        defaultOptionsErrorsByMac.clear()
        dirtyDefaultSettingFieldsByMac.clear()
        scheduleMobileStateSave()
    }
}

fun MainViewModel.forgetDefaultSettingsForMac(macId: String) {
    defaultSettingsSaveJobs.remove(macId)?.cancel()
    defaultSettingsSaveRequestIds.remove(macId)
    defaultSettingsAutoRetryAfter.remove(macId)
    defaultSettingsByMac.remove(macId)
    defaultPrimaryOptionsByMac.remove(macId)
    defaultSynthesisOptionsByMac.remove(macId)
    defaultSettingsErrorsByMac.remove(macId)
    defaultOptionsErrorsByMac.remove(macId)
    dirtyDefaultSettingFieldsByMac.remove(macId)
    scheduleMobileStateSave()
    if (state.value.defaultSettingsMacId == macId) {
        dismissDefaultConversationSettings()
    }
    if (state.value.storageMacId == macId) {
        dismissStorageManagement()
    }
}
