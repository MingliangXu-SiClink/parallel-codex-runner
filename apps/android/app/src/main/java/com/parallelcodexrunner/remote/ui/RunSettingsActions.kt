package com.parallelcodexrunner.remote.ui

import kotlinx.coroutines.flow.update

fun MainViewModel.setPrompt(value: String) {
    mutableState.update { current ->
        promptDraftKey(current)?.let { key ->
            // An empty edit before the run's identity loads is intentional, not
            // an invitation to restore an older conversation draft afterward.
            if (value.isEmpty() && (current.visibleRunId == null ||
                    !current.visibleConversationId.isNullOrBlank())) {
                promptDraftsByContext.remove(key)
            } else {
                promptDraftsByContext[key] = value
            }
        }
        current.copy(prompt = value)
    }
    scheduleMobileStateSave()
}

fun MainViewModel.setAgents(value: Int) = updateRunSettings {
    it.copy(agents = value)
}

fun MainViewModel.setSynthesisAgents(value: Int) = updateRunSettings(
    updateActiveSynthesis = true,
) {
    it.copy(synthesisAgents = value)
}

fun MainViewModel.setFollowUpDelay(value: Int) {
    val previousDelay = state.value.runSettings.followUpDelay
    updateRunSettings { it.copy(followUpDelay = value) }
    val snapshot = state.value
    if (snapshot.runSettings.followUpDelay == previousDelay) return
    // The settings acknowledgement updates pcrd's durable queue atomically.
    // Replacing it here would race that update with old per-message settings.
    val macId = snapshot.selectedMacId ?: return
    if (cachedDaemonOwnsFollowUpQueue(macId) == false) {
        mutableState.update { current ->
            current.copy(followUpQueue = current.followUpQueue.map { item ->
                if (item.dispatching) item else item.copy(
                    settings = (item.settings ?: settingsSnapshot(current)).copy(
                        followUpDelay = current.runSettings.followUpDelay,
                    ),
                )
            })
        }
        persistFollowUpQueue(queuedRunId ?: snapshot.visibleRunId, state.value.followUpQueue)
        if (snapshot.visibleRunId != null && isTerminalRunStatus(snapshot.runStatus)) {
            reconcileFollowUpQueue(resetCountdown = true)
        }
    }
}

fun MainViewModel.setExecution(value: String) = updateRunSettings {
    it.copy(execution = value)
}

fun MainViewModel.setAutomaticMaxParallel(value: Boolean) = updateRunSettings {
    it.copy(automaticMaxParallel = value)
}

fun MainViewModel.setMaxParallel(value: Int) = updateRunSettings {
    it.copy(maxParallel = value)
}

fun MainViewModel.setSubagents(value: Boolean) = updateRunSettings {
    it.copy(subagents = value)
}

fun MainViewModel.setSubagentsLimit(value: Int) = updateRunSettings {
    it.copy(subagentsLimit = value)
}

fun MainViewModel.setRecommendBy(value: String) = updateRunSettings {
    it.copy(recommendBy = value)
}

fun MainViewModel.setModel(value: String) {
    val changed = value != settingsSnapshot(state.value).model
    updateRunSettings {
        it.copy(
            model = value,
            effort = if (changed) "" else it.effort,
            fastMode = if (changed) "automatic" else it.fastMode,
        )
    }
    if (changed) {
        mutableState.update {
            it.copy(primaryModelOptions = null, settingsOptionsError = null)
        }
    }
    refreshRunSettingsOptions(loadResumeSessions = false)
}

fun MainViewModel.setEffort(value: String) = updateRunSettings {
    it.copy(effort = value)
}

fun MainViewModel.setFastMode(value: String) = updateRunSettings {
    it.copy(fastMode = value)
}

fun MainViewModel.setSynthesisModel(value: String) {
    val changed = value != settingsSnapshot(state.value).synthesisModel
    updateRunSettings(updateActiveSynthesis = true) {
        it.copy(
            synthesisModel = value,
            synthesisEffort = if (changed) "" else it.synthesisEffort,
            synthesisFastMode = if (changed) "automatic" else it.synthesisFastMode,
        )
    }
    if (changed) {
        mutableState.update {
            it.copy(synthesisModelOptions = null, settingsOptionsError = null)
        }
    }
    refreshRunSettingsOptions(loadResumeSessions = false)
}

fun MainViewModel.setSynthesisEffort(value: String) = updateRunSettings(
    updateActiveSynthesis = true,
) {
    it.copy(synthesisEffort = value)
}

fun MainViewModel.setSynthesisFastMode(value: String) = updateRunSettings(
    updateActiveSynthesis = true,
) {
    it.copy(synthesisFastMode = value)
}

fun MainViewModel.setSyncBack(value: Boolean) {
    if (!value && state.value.followUpQueue.isNotEmpty()) {
        showError(state.value.appLanguage.text(
            "请先完成或清空排队中的提问，再关闭工作区同步。",
            "Finish or clear the queued prompts before turning off workspace sync.",
        ))
        return
    }
    updateRunSettings { it.copy(syncBack = value) }
}

fun MainViewModel.setKeepWorkspaces(value: Boolean) = updateRunSettings {
    it.copy(keepWorkspaces = value)
}

fun MainViewModel.setResumeIncludeNonInteractive(value: Boolean) {
    updateRunSettings { it.copy(resumeIncludeNonInteractive = value) }
    refreshResumeSessions()
}

fun MainViewModel.setCodexExecutable(value: String) = updateRunSettings {
    it.copy(codexBin = value)
}
