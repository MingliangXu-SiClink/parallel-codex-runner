package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.RunStorageEntry
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

internal fun MainViewModel.openStorageManagement(macId: String) {
    val mac = state.value.macs.firstOrNull { it.deviceId == macId } ?: run {
        showError(state.value.appLanguage.text(
            "这台设备已断开或被移除。",
            "This device is offline or has been removed.",
        ))
        return
    }
    val preserveCurrentSummary = state.value.storageMacId == macId
    val requestId = ++storageRequestId
    mutableState.update {
        it.copy(
            storageMacId = macId,
            storageSummary = if (preserveCurrentSummary) it.storageSummary else null,
            storageLoading = true,
            storageError = null,
            storageCleaningRunId = null,
        )
    }
    viewModelScope.launch {
        try {
            val summary = repository.storageSummary(mac)
            if (requestId != storageRequestId || state.value.storageMacId != macId) {
                return@launch
            }
            mutableState.update {
                it.copy(
                    storageSummary = summary,
                    storageLoading = false,
                    storageError = null,
                )
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (requestId != storageRequestId || state.value.storageMacId != macId) {
                return@launch
            }
            mutableState.update {
                it.copy(
                    storageLoading = false,
                    storageError = friendlyError(error, state.value.appLanguage),
                )
            }
        }
    }
}

internal fun MainViewModel.refreshStorageManagement() {
    state.value.storageMacId?.let(::openStorageManagement)
}

internal fun MainViewModel.dismissStorageManagement() {
    storageRequestId += 1
    mutableState.update {
        it.copy(
            storageMacId = null,
            storageSummary = null,
            storageLoading = false,
            storageError = null,
            storageCleaningRunId = null,
        )
    }
}

internal fun MainViewModel.cleanupRunStorage(entry: RunStorageEntry) {
    val macId = state.value.storageMacId ?: return
    val mac = state.value.macs.firstOrNull { it.deviceId == macId } ?: run {
        showError(state.value.appLanguage.text(
            "这台设备已断开或被移除。",
            "This device is offline or has been removed.",
        ))
        return
    }
    if (state.value.storageCleaningRunId != null) return
    mutableState.update {
        it.copy(storageCleaningRunId = entry.runId, storageError = null)
    }
    viewModelScope.launch {
        try {
            repository.cleanupRunStorage(
                mac = mac,
                runId = entry.runId,
                discardUnselected = entry.cleanupAction == "discard",
            )
            mutableState.update { it.copy(storageCleaningRunId = null) }
            openStorageManagement(macId)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            mutableState.update {
                it.copy(
                    storageCleaningRunId = null,
                    storageError = friendlyError(error, state.value.appLanguage),
                )
            }
        }
    }
}
