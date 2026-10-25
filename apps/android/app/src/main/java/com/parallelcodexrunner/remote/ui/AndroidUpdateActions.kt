package com.parallelcodexrunner.remote.ui

import android.app.Application
import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.BuildConfig
import com.parallelcodexrunner.remote.data.AndroidUpdateInfo
import com.parallelcodexrunner.remote.data.InstallPermissionRequired
import com.parallelcodexrunner.remote.data.RelayDevice
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext

fun MainViewModel.checkForAndroidUpdate() {
    val mac = selectedMac()
    if (mac == null) {
        showError(state.value.appLanguage.text("请先选择一台设备。", "Choose a device first."))
        return
    }
    startAndroidUpdateCheck(mac, silent = false)
}

fun MainViewModel.openAndroidUpdateHistory() {
    val mac = selectedMac()
    if (mac == null) {
        showError(state.value.appLanguage.text("请先选择一台设备。", "Choose a device first."))
        return
    }
    val cached = repository.cachedAndroidUpdateHistory(mac)?.releases.orEmpty()
    mutableState.update {
        it.copy(
            updateHistoryOpen = true,
            updateHistory = cached,
            updateHistoryLoading = true,
            updateHistoryError = null,
            updateHistoryNextCursor = 0,
            updateHistoryHasMore = false,
        )
    }
    loadAndroidUpdateHistory(mac, cursor = 0)
}

fun MainViewModel.dismissAndroidUpdateHistory() {
    updateHistoryRequestId += 1
    mutableState.update {
        it.copy(updateHistoryOpen = false, updateHistoryLoading = false)
    }
}

fun MainViewModel.loadMoreAndroidUpdateHistory() {
    val snapshot = state.value
    if (!snapshot.updateHistoryOpen || snapshot.updateHistoryLoading ||
        !snapshot.updateHistoryHasMore
    ) return
    val mac = selectedMac() ?: return
    loadAndroidUpdateHistory(mac, snapshot.updateHistoryNextCursor)
}

private fun MainViewModel.loadAndroidUpdateHistory(mac: RelayDevice, cursor: Int) {
    val requestId = ++updateHistoryRequestId
    mutableState.update { it.copy(updateHistoryLoading = true) }
    viewModelScope.launch {
        try {
            val page = repository.androidUpdateHistory(mac, cursor)
            if (requestId != updateHistoryRequestId ||
                state.value.selectedMacId != mac.deviceId ||
                !state.value.updateHistoryOpen
            ) return@launch
            mutableState.update { current ->
                val releases = if (cursor == 0) {
                    page.releases
                } else {
                    (current.updateHistory + page.releases)
                        .distinctBy(AndroidUpdateInfo::releaseId)
                }
                current.copy(
                    updateHistory = releases,
                    updateHistoryError = null,
                    updateHistoryNextCursor = page.nextCursor,
                    updateHistoryHasMore = page.hasMore,
                )
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Throwable) {
            if (requestId == updateHistoryRequestId && state.value.updateHistoryOpen) {
                mutableState.update { current ->
                    current.copy(
                        updateHistoryError = if (current.updateHistory.isEmpty()) {
                            current.appLanguage.text(
                                "暂时无法加载更新记录。",
                                "Update history is temporarily unavailable.",
                            )
                        } else {
                            current.appLanguage.text(
                                "暂时无法刷新，正在显示上次保存的记录。",
                                "Refresh failed. Showing the last saved update history.",
                            )
                        },
                    )
                }
            }
        } finally {
            if (requestId == updateHistoryRequestId) {
                mutableState.update { it.copy(updateHistoryLoading = false) }
            }
        }
    }
}

fun MainViewModel.installAndroidUpdate() {
    if (state.value.updateDownloading) return
    val mac = selectedMac()
    if (mac == null) {
        showError(state.value.appLanguage.text("请先选择一台设备。", "Choose a device first."))
        return
    }
    val update = state.value.availableUpdate
    if (update == null) {
        showError(state.value.appLanguage.text(
            "当前没有可安装的更新。",
            "No update is currently available.",
        ))
        return
    }
    try {
        updateInstaller.ensureInstallPermission()
    } catch (_: InstallPermissionRequired) {
        mutableState.update {
            it.copy(
                updatePermissionPending = true,
                message = state.value.appLanguage.text(
                    "请允许 Parallel Codex 安装应用；返回后会继续更新。",
                    "Allow Parallel Codex to install apps. The update will continue when you return.",
                ),
                messageIsError = false,
            )
        }
        return
    }

    mutableState.update {
        it.copy(updateDownloading = true, updateProgress = 0)
    }
    viewModelScope.launch {
        val destination = File(
            getApplication<Application>().cacheDir,
            "android-updates/parallel-codex-update.apk",
        )
        try {
            repository.downloadAndroidUpdate(mac, update, destination) { downloaded, total ->
                val progress = if (total <= 0) 0 else {
                    ((downloaded * 100) / total).coerceIn(0, 100).toInt()
                }
                mutableState.update { current ->
                    if (current.updateDownloading) current.copy(updateProgress = progress)
                    else current
                }
            }
            withContext(Dispatchers.IO) {
                updateInstaller.verifyAndInstall(destination, update)
            }
            mutableState.update {
                it.copy(
                    updateProgress = 100,
                    updatePermissionPending = false,
                    message = state.value.appLanguage.text(
                        "更新已准备好，请在 Android 系统界面确认安装。",
                        "The update is ready. Confirm installation in the Android system screen.",
                    ),
                    messageIsError = false,
                )
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            showError(friendlyError(error, state.value.appLanguage))
        } finally {
            destination.delete()
            mutableState.update {
                it.copy(updateDownloading = false, updateProgress = null)
            }
        }
    }
}

internal fun MainViewModel.startAndroidUpdateCheck(mac: RelayDevice, silent: Boolean) {
    val requestId = ++updateRequestId
    mutableState.update { it.copy(updateChecking = true) }
    viewModelScope.launch {
        try {
            val response = repository.checkAndroidUpdate(mac)
            if (requestId != updateRequestId ||
                state.value.selectedMacId != mac.deviceId
            ) return@launch
            val update = response.update?.takeIf {
                response.available && it.versionCode > BuildConfig.VERSION_CODE.toLong()
            }
            if (update != null) validateAndroidUpdate(update)
            mutableState.update { current ->
                current.copy(
                    availableUpdate = update,
                    message = if (!silent && update == null) {
                        current.appLanguage.text(
                            "当前已是最新版本 ${BuildConfig.VERSION_NAME}。",
                            "Parallel Codex ${BuildConfig.VERSION_NAME} is up to date.",
                        )
                    } else {
                        current.message
                    },
                    messageIsError = if (!silent && update == null) {
                        false
                    } else {
                        current.messageIsError
                    },
                )
            }
        } catch (error: Throwable) {
            if (requestId == updateRequestId &&
                state.value.selectedMacId == mac.deviceId &&
                !silent
            ) {
                showError(friendlyError(error, state.value.appLanguage))
            }
        } finally {
            if (requestId == updateRequestId) {
                mutableState.update { it.copy(updateChecking = false) }
            }
        }
    }
}

private fun validateAndroidUpdate(update: AndroidUpdateInfo) {
    require(update.packageName == BuildConfig.APPLICATION_ID) {
        "Relay returned an update for a different Android app"
    }
    require(update.versionName.isNotBlank()) {
        "Android update version is invalid"
    }
    require(update.sizeBytes in 1..MAX_ANDROID_UPDATE_BYTES) {
        "Android update size is invalid"
    }
    require(update.sha256.matches(SHA256_PATTERN)) {
        "Android update checksum is invalid"
    }
}

private const val MAX_ANDROID_UPDATE_BYTES = 512L * 1024 * 1024
private val SHA256_PATTERN = Regex("[0-9a-fA-F]{64}")
