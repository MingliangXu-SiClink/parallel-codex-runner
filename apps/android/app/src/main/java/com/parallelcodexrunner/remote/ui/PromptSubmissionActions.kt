package com.parallelcodexrunner.remote.ui

import androidx.lifecycle.viewModelScope
import androidx.work.WorkInfo
import com.parallelcodexrunner.remote.data.AcceptedPromptResult
import com.parallelcodexrunner.remote.data.PendingPromptSubmission
import com.parallelcodexrunner.remote.data.PromptSubmissionWorker
import com.parallelcodexrunner.remote.data.WorkspaceSettingFields
import com.parallelcodexrunner.remote.data.forRetry
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

internal fun MainViewModel.startRun(confirmLargeRun: Boolean = false) {
    val prompt = state.value.prompt.trim()
    if (prompt.isEmpty()) return
    if (state.value.pendingSubmission == null && canPersistFollowUp(state.value)) {
        enqueueFollowUp(prompt)
        return
    }
    if (state.value.runActionInProgress) {
        showError(state.value.appLanguage.text(
            "当前任务操作完成后再发送。",
            "Wait for the current task action to finish before sending.",
        ))
        return
    }
    if (state.value.pendingSubmission != null) {
        showError(state.value.appLanguage.text(
            "请先等待当前消息提交完成，或处理发送失败的消息。",
            "Wait for the current message to finish sending, or resolve the failed message first.",
        ))
        return
    }
    promptContinuationBlockReason(state.value)?.let { reason ->
        showError(reason)
        return
    }
    val activeTurn = state.value.visibleRunId != null &&
        !isTerminalRunStatus(state.value.runStatus)
    if (state.value.followUpQueue.isNotEmpty()) {
        if (!state.value.runSettings.syncBack) {
            showError(state.value.appLanguage.text(
                "开启工作区同步后才能排队继续提问。",
                "Enable workspace sync to queue another message.",
            ))
            return
        }
        enqueueFollowUp(prompt)
        return
    }
    val sourceAgent = continuationSourceAgent(state.value)
    if (activeTurn && sourceAgent == null) {
        if (!state.value.runSettings.syncBack) {
            showError(state.value.appLanguage.text(
                "开启工作区同步后才能排队继续提问。",
                "Enable workspace sync to queue another message.",
            ))
            return
        }
        enqueueFollowUp(prompt)
        return
    }
    if (activeTurn && !state.value.runSettings.syncBack) {
        showError(state.value.appLanguage.text(
            "开启工作区同步后才能从当前 Agent 继续提问。",
            "Enable workspace sync to continue from the current Agent.",
        ))
        return
    }
    val mac = selectedMac() ?: run {
        showError(state.value.appLanguage.text("请先连接一台设备。", "Connect a device first."))
        return
    }
    val workspaceId = state.value.selectedWorkspaceId ?: run {
        showError(state.value.appLanguage.text("请先选择工作区。", "Choose a workspace first."))
        return
    }
    val settings = settingsSnapshot(state.value)
    val lastResumeSessionId = state.value.workspaces
        .firstOrNull { it.workspaceId == workspaceId }
        ?.lastResumeSessionId
        .orEmpty()
    val submission = PendingPromptSubmission(
        id = UUID.randomUUID().toString(),
        macId = mac.deviceId,
        workspaceId = workspaceId,
        workspaceName = state.value.workspaces
            .firstOrNull { it.workspaceId == workspaceId }
            ?.name
            .orEmpty(),
        prompt = prompt,
        settings = settings,
        sourceRunId = sourceAgent?.let { state.value.visibleRunId }.orEmpty(),
        sourceAgent = sourceAgent?.number,
        resumeSessionId = effectiveResumeSessionId(settings, lastResumeSessionId),
        confirmLargeRun = confirmLargeRun || hasLargeStorageApproval(),
    )
    val contextKey = workspaceSettingsKey(mac.deviceId, workspaceId)
    persistPromptSubmission(contextKey, submission)
    promptDraftKey(state.value)?.let(promptDraftsByContext::remove)
    mutableState.update {
        it.copy(prompt = "", pendingSubmission = submission)
    }
    scheduleMobileStateSave()
    submitPendingSubmission(submission)
}

internal fun MainViewModel.submitPendingSubmission(submission: PendingPromptSubmission) {
    if (!submittingPromptIds.add(submission.commandId)) return
    viewModelScope.launch {
        val contextKey = workspaceSettingsKey(
            submission.macId,
            submission.workspaceId,
        )
        try {
            // WorkManager may start in a fresh process, so persist the
            // encrypted outbox before handing ownership to the worker.
            val persistedSnapshot = mobileStateSnapshot()
            withContext(Dispatchers.IO) {
                persistMobileStateSnapshot(persistedSnapshot)
            }
            val workID = app.promptSubmissionScheduler.enqueue(submission)
            val workInfo = app.promptSubmissionScheduler.workInfo(workID)
                .filterNotNull()
                .onEach { info ->
                    if (info.runAttemptCount > 0 &&
                        info.state in setOf(
                            WorkInfo.State.ENQUEUED,
                            WorkInfo.State.BLOCKED,
                            WorkInfo.State.RUNNING,
                        )
                    ) {
                        val current = promptOutboxByContext[contextKey]
                        if (current?.id == submission.id &&
                            current.commandId == submission.commandId &&
                            current.status != PendingPromptSubmission.STATUS_RETRYING
                        ) {
                            val retrying = current.copy(
                                status = PendingPromptSubmission.STATUS_RETRYING,
                                error = "",
                            )
                            persistPromptSubmission(contextKey, retrying)
                            if (isCurrentSubmissionContext(retrying)) {
                                mutableState.update {
                                    it.copy(pendingSubmission = retrying)
                                }
                            }
                            scheduleMobileStateSave()
                        }
                    }
                }
                .first { it.state.isFinished }
            val output = workInfo.outputData
            val resultStatus = output.getString(
                PromptSubmissionWorker.KEY_RESULT_STATUS
            ).orEmpty()

            val persistedAccepted = mobileStateStore.load()
                .acceptedPromptResults[contextKey]
                ?.takeIf { it.commandId == submission.commandId }
            if (persistedAccepted != null) {
                adoptAcceptedPromptResult(persistedAccepted)
                return@launch
            }

            if (workInfo.state == WorkInfo.State.SUCCEEDED &&
                resultStatus == PromptSubmissionWorker.STATUS_CONFIRMATION_REQUIRED
            ) {
                val message = output.getString(PromptSubmissionWorker.KEY_MESSAGE)
                    ?.takeIf(String::isNotBlank)
                    ?: "此任务需要先确认可用存储空间。"
                val waiting = submission.copy(
                    status = PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION,
                    error = message,
                )
                persistPromptSubmission(contextKey, waiting)
                if (isCurrentSubmissionContext(waiting)) {
                    mutableState.update {
                        it.copy(
                            pendingSubmission = waiting,
                            storageConfirmation = message,
                        )
                    }
                }
                scheduleMobileStateSave()
                return@launch
            }

            if (workInfo.state != WorkInfo.State.SUCCEEDED ||
                resultStatus != PromptSubmissionWorker.STATUS_ACCEPTED
            ) {
                val current = promptOutboxByContext[contextKey]
                if (current?.id == submission.id &&
                    current.commandId == submission.commandId
                ) {
                    val message = output.getString(PromptSubmissionWorker.KEY_MESSAGE)
                        ?.takeIf(String::isNotBlank)
                        ?: if (workInfo.state == WorkInfo.State.CANCELLED) {
                            "Background submission was cancelled"
                        } else {
                            "Message could not be sent"
                        }
                    val failed = current.copy(
                        status = PendingPromptSubmission.STATUS_FAILED,
                        error = message,
                        retryRequiresNewCommandId = output.getBoolean(
                            PromptSubmissionWorker.KEY_REQUIRES_NEW_COMMAND_ID,
                            true,
                        ),
                    )
                    persistPromptSubmission(contextKey, failed)
                    if (isCurrentSubmissionContext(failed)) {
                        mutableState.update { it.copy(pendingSubmission = failed) }
                    }
                    scheduleMobileStateSave()
                }
                return@launch
            }

            val runId = output.getString(PromptSubmissionWorker.KEY_RUN_ID)
                ?.takeIf(String::isNotBlank)
                ?: error("Target machine did not return a run id")
            val continuedSessionId = output.getString(
                PromptSubmissionWorker.KEY_CONTINUED_SESSION_ID
            )
                ?.trim()
            val workspaceRevision = output.getInt(
                PromptSubmissionWorker.KEY_WORKSPACE_REVISION,
                -1,
            )
            adoptAcceptedPromptResult(
                AcceptedPromptResult(
                    submission = submission,
                    runId = runId,
                    continuedSessionId = continuedSessionId.orEmpty(),
                    workspaceRevision = workspaceRevision,
                )
            )
        } catch (error: CancellationException) {
            throw error
        } catch (error: Throwable) {
            val current = promptOutboxByContext[contextKey]
            if (current?.id == submission.id && current.attempt == submission.attempt) {
                val failed = current.copy(
                    status = PendingPromptSubmission.STATUS_FAILED,
                    error = friendlyError(error, state.value.appLanguage),
                    retryRequiresNewCommandId = true,
                )
                persistPromptSubmission(contextKey, failed)
                if (isCurrentSubmissionContext(failed)) {
                    mutableState.update { it.copy(pendingSubmission = failed) }
                }
                scheduleMobileStateSave()
            }
        } finally {
            submittingPromptIds.remove(submission.commandId)
            resumeAcceptedPromptResults()
        }
    }
}

internal fun MainViewModel.resumeAcceptedPromptResults(): Boolean {
    if (!uiForeground) return false
    val snapshot = state.value
    val macId = snapshot.selectedMacId ?: return false
    val workspaceId = snapshot.selectedWorkspaceId ?: return false
    val contextKey = workspaceSettingsKey(macId, workspaceId)
    val result = acceptedPromptResultsByContext[contextKey] ?: return false
    if (result.commandId in submittingPromptIds ||
        !acceptedPromptAdoptionsInFlight.add(result.commandId)
    ) return false
    return try {
        adoptAcceptedPromptResult(result)
    } finally {
        acceptedPromptAdoptionsInFlight.remove(result.commandId)
    }
}

internal fun MainViewModel.adoptAcceptedPromptResult(
    result: AcceptedPromptResult,
): Boolean {
    val submission = result.submission
    if (!isCurrentSubmissionContext(submission)) return false
    val contextKey = workspaceSettingsKey(submission.macId, submission.workspaceId)
    domainRepositories.conversations.attach(contextKey)
    onWorkspaceRunStarted(submission.macId, submission.workspaceId, result.runId)

    if (state.value.visibleRunId == result.runId) {
        persistPromptSubmission(contextKey, null)
        persistAcceptedPromptResult(contextKey, null)
        mutableState.update { it.copy(prompt = "", pendingSubmission = null) }
        beginPolling(result.runId)
        return true
    }

    val carriedConversation = carriedConversationFor(submission)
    val previousQueuedRunId = queuedRunId
    advanceContext(preserveFollowUps = previousQueuedRunId != null)
    val nextRunKey = WorkspaceRunKey(
        submission.macId,
        submission.workspaceId,
        result.runId,
    )
    runStateStore.put(
        nextRunKey,
        WorkspaceRunSnapshot(
            status = "starting",
            prompt = submission.prompt,
            conversationHistory = carriedConversation,
        ),
    )
    val continuedSessionId = result.continuedSessionId.takeIf(String::isNotBlank)
    mutableState.update {
        val nextSettings = if (continuedSessionId != null) {
            submission.settings.copy(
                configured = true,
                resumeSessionId = continuedSessionId,
            )
        } else {
            submission.settings
        }
        it.copy(
            visibleRunId = result.runId,
            prompt = "",
            workspaceRevision = result.workspaceRevision
                .takeIf { revision -> revision >= 0 }
                ?: it.workspaceRevision,
            runStatus = "starting",
            canAddAgents = false,
            canUpdateSynthesis = false,
            runPrompt = submission.prompt,
            selectedAgent = null,
            recommendedAgent = null,
            finalizedAgent = null,
            conversationHistory = carriedConversation,
            agentSummaries = emptyList(),
            agentTranscripts = emptyMap(),
            runSettings = nextSettings,
            pendingSubmission = null,
        )
    }
    if (continuedSessionId != null) {
        localSettingsByWorkspace[contextKey] = settingsSnapshot(state.value)
        dirtyWorkspaceSettingFields
            .getOrPut(contextKey, ::mutableSetOf)
            .add(WorkspaceSettingFields.RESUME_SESSION_ID)
        scheduleWorkspaceSettingsSave()
    }
    if (previousQueuedRunId != null) {
        persistFollowUpQueue(previousQueuedRunId, emptyList())
        queuedRunId = result.runId
        persistFollowUpQueue(result.runId, state.value.followUpQueue)
    }

    persistPromptSubmission(contextKey, null)
    persistAcceptedPromptResult(contextKey, null)
    scheduleMobileStateSave()
    beginPolling(result.runId)
    return true
}

private fun MainViewModel.carriedConversationFor(
    submission: PendingPromptSubmission,
): List<MobileTranscriptBlock> {
    if (state.value.selectedMacId != submission.macId ||
        state.value.selectedWorkspaceId != submission.workspaceId
    ) return emptyList()
    val visible = state.value
    return continuationConversation(
        WorkspaceRunSnapshot(
            status = visible.runStatus,
            prompt = visible.runPrompt,
            agents = visible.agentSummaries,
            selectedAgent = visible.selectedAgent,
            recommendedAgent = visible.recommendedAgent,
            finalizedAgent = visible.finalizedAgent,
            conversationHistory = visible.conversationHistory,
            agentTranscripts = visible.agentTranscripts,
        ),
        submission.sourceAgent,
    )
}

private fun MainViewModel.isCurrentSubmissionContext(submission: PendingPromptSubmission): Boolean =
    state.value.selectedMacId == submission.macId &&
        state.value.selectedWorkspaceId == submission.workspaceId

internal fun MainViewModel.retryPendingSubmission() {
    val current = state.value.pendingSubmission ?: return
    if (current.status != PendingPromptSubmission.STATUS_FAILED) return
    val retrying = current.forRetry()
    persistPromptSubmission(
        workspaceSettingsKey(retrying.macId, retrying.workspaceId),
        retrying,
    )
    mutableState.update { it.copy(pendingSubmission = retrying) }
    scheduleMobileStateSave()
    submitPendingSubmission(retrying)
}

internal fun MainViewModel.editPendingSubmission() {
    val current = state.value.pendingSubmission ?: return
    if (current.status != PendingPromptSubmission.STATUS_FAILED) return
    persistPromptSubmission(
        workspaceSettingsKey(current.macId, current.workspaceId),
        null,
    )
    mutableState.update {
        it.copy(prompt = current.prompt, pendingSubmission = null)
    }
    promptDraftKey(state.value)?.let { key -> promptDraftsByContext[key] = current.prompt }
    scheduleMobileStateSave()
}

internal fun MainViewModel.confirmLargeRun() {
    state.value.pendingSubmission
        ?.takeIf {
            it.status == PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION
        }
        ?.let { current ->
            rememberLargeStorageApproval()
            val confirmed = current.copy(
                confirmLargeRun = true,
                status = PendingPromptSubmission.STATUS_PENDING,
                error = "",
            )
            persistPromptSubmission(
                workspaceSettingsKey(confirmed.macId, confirmed.workspaceId),
                confirmed,
            )
            mutableState.update {
                it.copy(
                    storageConfirmation = null,
                    pendingSubmission = confirmed,
                )
            }
            scheduleMobileStateSave()
            submitPendingSubmission(confirmed)
            return
        }
    val action = currentRunMutationContext()?.let(pendingStorageActions::get) ?: run {
        mutableState.update { it.copy(storageConfirmation = null) }
        return
    }
    rememberLargeStorageApproval()
    pendingStorageActions.remove(action.context)
    mutableState.update { it.copy(storageConfirmation = null) }
    when (action) {
        is PendingStorageAction.More -> {
            executeRunMutation(action.context) {
                repository.addAgents(
                    mac = it.mac,
                    runId = it.runId,
                    count = action.count,
                    confirmLargeRun = true,
                )
                if (isCurrentRunMutation(it)) {
                    loadRun(it.runId)
                    beginPolling(it.runId)
                }
            }
        }
    }
}

internal fun MainViewModel.cancelLargeRun() {
    state.value.pendingSubmission
        ?.takeIf {
            it.status == PendingPromptSubmission.STATUS_AWAITING_CONFIRMATION
        }
        ?.let { current ->
            persistPromptSubmission(
                workspaceSettingsKey(current.macId, current.workspaceId),
                null,
            )
            mutableState.update {
                it.copy(
                    prompt = current.prompt,
                    pendingSubmission = null,
                    storageConfirmation = null,
                )
            }
            promptDraftKey(state.value)?.let { key ->
                promptDraftsByContext[key] = current.prompt
            }
            scheduleMobileStateSave()
            return
        }
    currentRunMutationContext()?.let(pendingStorageActions::remove)
    mutableState.update { it.copy(storageConfirmation = null) }
    if (state.value.followUpQueue.isNotEmpty() &&
        isTerminalRunStatus(state.value.runStatus)
    ) {
        reconcileFollowUpQueue(resetCountdown = true)
    }
}
