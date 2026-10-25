package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import com.parallelcodexrunner.remote.data.RelayDevice
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

fun MainViewModel.openBrowser() = execute {
    val mac = selectedMac() ?: error("Choose a Mac first")
    val requestId = ++directoryRequestId
    mutableState.update {
        it.copy(
            browserOpen = true,
            browserRoots = null,
            directory = null,
            directoryLoading = true,
        )
    }
    val roots = try {
        repository.roots(mac)
    } catch (error: Throwable) {
        if (requestId == directoryRequestId) {
            mutableState.update { it.copy(directoryLoading = false) }
        }
        throw error
    }
    if (requestId != directoryRequestId || !state.value.browserOpen) return@execute
    mutableState.update {
        it.copy(browserRoots = roots, browserPath = roots.home)
    }
    loadDirectory(mac, roots.home)
}

fun MainViewModel.closeBrowser() {
    directoryRequestId += 1
    pendingFolderCreation = null
    mutableState.update {
        it.copy(
            browserOpen = false,
            directoryLoading = false,
            folderCreationParent = null,
            folderCreationName = "",
            folderCreating = false,
            folderCreationError = null,
        )
    }
}

fun MainViewModel.requestFolderCreation() {
    val snapshot = state.value
    val macId = snapshot.selectedMacId ?: return
    val directory = snapshot.directory ?: return
    if (!directory.writable || snapshot.directoryLoading) return
    pendingFolderCreation = PendingFolderCreation(macId, directory.path)
    mutableState.update {
        it.copy(
            folderCreationParent = directory.path,
            folderCreationName = "",
            folderCreating = false,
            folderCreationError = null,
        )
    }
}

fun MainViewModel.setFolderCreationName(value: String) = mutableState.update {
    it.copy(folderCreationName = value, folderCreationError = null)
}

fun MainViewModel.cancelFolderCreation() {
    if (state.value.folderCreating) return
    pendingFolderCreation = null
    mutableState.update {
        it.copy(
            folderCreationParent = null,
            folderCreationName = "",
            folderCreationError = null,
        )
    }
}

fun MainViewModel.confirmFolderCreation() {
    val pending = pendingFolderCreation ?: return
    val name = state.value.folderCreationName.trim()
    if (name.isEmpty()) {
        mutableState.update { current ->
            current.copy(folderCreationError = current.appLanguage.text(
                "请输入文件夹名称。",
                "Enter a folder name.",
            ))
        }
        return
    }
    if (state.value.folderCreating) return
    mutableState.update { it.copy(folderCreating = true, folderCreationError = null) }
    viewModelScope.launch {
        try {
            val mac = state.value.macs.firstOrNull { it.deviceId == pending.macId }
                ?: error("The selected device is disconnected")
            val created = repository.createDirectory(mac, pending.parent, name)
            if (!created.created) error("The target device could not create the folder")
            // The folder was created for the captured Mac and parent. If
            // the user has already closed or replaced this browser flow,
            // do not clear or navigate the newer dialog.
            if (pendingFolderCreation != pending) return@launch
            pendingFolderCreation = null
            mutableState.update {
                it.copy(
                    folderCreationParent = null,
                    folderCreationName = "",
                    folderCreating = false,
                    folderCreationError = null,
                )
            }
            if (state.value.browserOpen &&
                state.value.selectedMacId == pending.macId
            ) {
                loadDirectory(mac, created.path)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            if (pendingFolderCreation == pending) {
                mutableState.update {
                    it.copy(
                        folderCreating = false,
                        folderCreationError = friendlyError(error, state.value.appLanguage),
                    )
                }
            }
        }
    }
}

fun MainViewModel.browse(path: String = state.value.browserPath) = execute {
    val mac = selectedMac() ?: error("Choose a Mac first")
    loadDirectory(mac, path)
}

fun MainViewModel.browseParent() {
    state.value.directory?.parent?.let(::browse)
}

fun MainViewModel.browseMore() = execute {
    val mac = selectedMac() ?: error("Choose a Mac first")
    val current = state.value.directory ?: return@execute
    if (!current.hasMore) return@execute
    val requestId = ++directoryRequestId
    mutableState.update { it.copy(directoryLoading = true) }
    val page = try {
        repository.directory(
            mac,
            current.path,
            state.value.showHidden,
            current.nextCursor,
        )
    } catch (error: Throwable) {
        if (requestId == directoryRequestId) {
            mutableState.update { it.copy(directoryLoading = false) }
        }
        throw error
    }
    if (requestId != directoryRequestId ||
        !state.value.browserOpen ||
        page.path != current.path ||
        state.value.directory?.path != current.path
    ) return@execute
    mutableState.update {
        it.copy(
            directory = page.copy(
                cursor = current.cursor,
                entries = current.entries + page.entries,
            ),
            directoryLoading = false,
        )
    }
}

fun MainViewModel.setShowHidden(value: Boolean) {
    mutableState.update { it.copy(showHidden = value) }
    browse()
}

private suspend fun MainViewModel.loadDirectory(mac: RelayDevice, path: String) {
    val requestId = ++directoryRequestId
    mutableState.update {
        it.copy(browserPath = path, directory = null, directoryLoading = true)
    }
    val page = try {
        repository.directory(mac, path, state.value.showHidden)
    } catch (error: Throwable) {
        if (requestId == directoryRequestId) {
            mutableState.update { it.copy(directoryLoading = false) }
        }
        throw error
    }
    if (requestId != directoryRequestId ||
        !state.value.browserOpen ||
        state.value.selectedMacId != mac.deviceId
    ) return
    mutableState.update {
        it.copy(
            directory = page,
            browserPath = page.path,
            directoryLoading = false,
        )
    }
}

fun MainViewModel.addCurrentDirectory() = execute {
    val mac = selectedMac() ?: error("Choose a Mac first")
    val directory = state.value.directory ?: error("Choose a directory")
    val result = repository.addWorkspace(mac, directory.path)
    val workspaceId = result["workspace"]
        ?.jsonObject
        ?.get("workspace_id")
        ?.jsonPrimitive
        ?.contentOrNull
    loadWorkspaces(
        mac,
        contextGeneration,
        preferredWorkspaceId = workspaceId,
    )
    mutableState.update { it.copy(browserOpen = false) }
}
