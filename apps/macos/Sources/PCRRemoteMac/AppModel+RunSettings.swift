import Foundation

@MainActor
extension AppModel {
    func updatePrimaryModel(_ model: String) async {
        let workspaceContext = ExecutionEditContext(self)
        let changed = settings.model != model
        var next = settings
        next.model = model
        if changed {
            next.fast = .automatic
            let context = CodexModelOptionsContext(machineID: currentMachineContextID, model: model)
            if let options = modelOptionsCache.options(for: context),
               !options.efforts.contains(where: { $0.value == next.effort }) {
                next.effort = ""
            }
        }
        settings = next
        let context = primaryModelOptionsContext
        rememberCurrentSettings()
        await loadPrimaryModelOptions()
        guard primaryModelOptionsContext == context, ExecutionEditContext(self) == workspaceContext else { return }
        if changed, let options = primaryModelOptions,
           !settings.effort.isEmpty,
           !options.efforts.contains(where: { $0.value == settings.effort }) {
            settings.effort = ""
            rememberCurrentSettings()
        }
    }

    func loadStorageSummary() async {
        storageSummaryRequestID += 1
        let requestID = storageSummaryRequestID
        storageSummaryLoading = true
        storageSummaryError = nil
        var target = client.currentTarget()
        do {
            if !daemonOnline {
                await refresh(reportErrors: false)
            }
            guard daemonOnline else {
                throw PcrdClientError.connection("Server unavailable")
            }
            target = client.currentTarget()
            try await requireSystemCapability("storage.summary")
            let response: StorageSummary = try await client.call(
                target: target,
                "storage.summary",
                body: ["limit": 100]
            )
            guard isCurrentMachine(target.machineID),
                  requestID == storageSummaryRequestID else { return }
            storageSummary = response
            storageSummaryError = nil
        } catch {
            guard isCurrentMachine(target.machineID),
                  requestID == storageSummaryRequestID else { return }
            storageSummaryError = appLanguage.userMessage(for: error)
        }
        if isCurrentMachine(target.machineID), requestID == storageSummaryRequestID {
            storageSummaryLoading = false
        }
    }

    func cleanupStorage(for entry: RunStorageEntry) async {
        let target = client.currentTarget()
        let machineID = MachineContextKey.normalizeMachineID(target.machineID)
        guard storageCleaningRunIDsByMachine[machineID] == nil else { return }
        storageCleaningRunIDsByMachine[machineID] = entry.runId
        storageSummaryError = nil
        defer { storageCleaningRunIDsByMachine.removeValue(forKey: machineID) }
        do {
            let _: RunSummary = try await client.call(
                target: target,
                "storage.cleanup",
                body: StorageCleanupBody(
                    runId: entry.runId,
                    discardUnselected: entry.cleanupAction == "discard",
                    waitSeconds: 30
                ),
                timeoutSeconds: 15 * 60
            )
            if isCurrentMachine(target.machineID) {
                await loadStorageSummary()
            }
        } catch {
            if isCurrentMachine(target.machineID) {
                storageSummaryError = appLanguage.userMessage(for: error)
            }
        }
    }

    func updateSynthesisModel(_ model: String) async {
        let workspaceContext = ExecutionEditContext(self)
        let changed = settings.synthesisModel != model
        var next = settings
        next.synthesisModel = model
        if changed {
            next.synthesisFast = .automatic
            let context = CodexModelOptionsContext(machineID: currentMachineContextID, model: model)
            if let options = modelOptionsCache.options(for: context),
               !options.efforts.contains(where: { $0.value == next.synthesisEffort }) {
                next.synthesisEffort = ""
            }
        }
        settings = next
        let context = synthesisModelOptionsContext
        rememberCurrentSettings()
        await loadSynthesisModelOptions()
        guard synthesisModelOptionsContext == context, ExecutionEditContext(self) == workspaceContext else { return }
        if changed, let options = synthesisModelOptions,
           !settings.synthesisEffort.isEmpty,
           !options.efforts.contains(where: { $0.value == settings.synthesisEffort }) {
            settings.synthesisEffort = ""
            rememberCurrentSettings()
        }
        scheduleSynthesisSettingsUpdate()
    }

    func updateResumeSession(_ sessionID: String) async {
        let selection = usableResumeSessionID(sessionID) ?? "NO"
        guard settings.resumeSessionID != selection else { return }
        let mustResolveVisibleRun = activeRun?.isWorking == true
            || activeRun?.isReviewable == true
        let machineID = currentMachineContextID
        let workspaceID = selectedWorkspaceID
        let generation = workspaceContextGeneration
        guard await prepareVisibleResultForContextTransition(
            destination: .resumeSession,
            dispatchQueuedFollowUp: false
        ) else { return }
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspaceID,
              workspaceContextGeneration == generation else { return }
        if mustResolveVisibleRun {
            guard activeRun?.finalizedAgent != nil else {
                errorMessage = appLanguage.text(
                    "请开启同步回原工作区，或先放弃当前轮次。",
                    "Turn on Sync Back or discard the current turn before changing conversations."
                )
                return
            }
        }
        let previousContext = currentPromptHistoryContext()
        settings.resumeSessionID = selection
        if previousContext != currentPromptHistoryContext() {
            clearFollowUpQueue()
        }
        rememberCurrentSettings()
        preparePromptHistoryContext()
        await loadResumeHistory(selection, reportErrors: true)
        await loadPromptHistory()
    }

    func dismissRunSettings() {
        // Register local edits before dismissing permits a health refresh to
        // merge the workspace's last acknowledged settings into the form.
        rememberCurrentSettings()
        settingsPresented = false
        Task {
            await updateActiveSynthesisSettingsIfNeeded()
            await loadResumeSessions(reportErrors: true)
        }
    }

    func scheduleSynthesisSettingsUpdate() {
        rememberCurrentSettings()
        guard let context = currentRunOperationContext(),
              activeRun?.isWorking == true,
              activeRun?.mayUpdateSynthesis == true else { return }
        let desiredSettings = settings
        let key = context.key
        let token = UUID()
        synthesisSettingsTasks[key]?.cancel()
        synthesisSettingsTaskTokens[key] = token
        synthesisSettingsTasks[key] = Task { [weak self] in
            defer {
                if let self, self.synthesisSettingsTaskTokens[key] == token {
                    self.synthesisSettingsTasks.removeValue(forKey: key)
                    self.synthesisSettingsTaskTokens.removeValue(forKey: key)
                }
            }
            do {
                try await Task.sleep(for: .milliseconds(150))
            } catch {
                return
            }
            guard let self, !Task.isCancelled else { return }
            await self.updateActiveSynthesisSettings(
                context: context,
                desiredSettings: desiredSettings
            )
        }
    }

    private func updateActiveSynthesisSettingsIfNeeded() async {
        guard let context = currentRunOperationContext(),
              activeRun?.isWorking == true,
              activeRun?.mayUpdateSynthesis == true else { return }
        await updateActiveSynthesisSettings(context: context, desiredSettings: settings)
    }

    private func updateActiveSynthesisSettings(
        context: RunOperationContext,
        desiredSettings: RunSettings
    ) async {
        let runKey = WorkspaceRunViewKey(
            machineID: context.machineID,
            workspaceID: context.workspaceID,
            runID: context.runID
        )
        let desiredAgents = max(0, desiredSettings.synthesisAgents)
        let desiredModel = desiredSettings.synthesisModel.isEmpty
            ? nil : desiredSettings.synthesisModel
        let desiredEffort = desiredSettings.synthesisEffort.isEmpty
            ? nil : desiredSettings.synthesisEffort
        let desiredFast = desiredSettings.synthesisFast.requestValue
        var retryDelay = 0.2
        for attempt in 1...5 {
            guard !Task.isCancelled else { return }
            let run = isCurrentRunOperation(context)
                ? activeRun
                : runSummariesByContext[runKey]
            guard let run, run.isWorking, run.mayUpdateSynthesis else { return }
            guard run.config.synthesisAgents != desiredAgents
                || run.config.synthesisModel != desiredModel
                || run.config.synthesisEffort != desiredEffort
                || run.config.synthesisFast != desiredFast else { return }
            do {
                let updated: RunSummary = try await client.call(
                    target: context.target,
                    "runs.update_synthesis",
                    body: UpdateSynthesisBody(
                        runId: context.runID,
                        synthesisAgents: desiredAgents,
                        synthesisModel: desiredModel,
                        synthesisEffort: desiredEffort,
                        synthesisFast: desiredFast
                    )
                )
                guard !Task.isCancelled else { return }
                runSummariesByContext[runKey] = updated
                if isCurrentRunOperation(context) { activeRun = updated }
                return
            } catch is CancellationError {
                return
            } catch {
                if attempt == 1, isCurrentRunOperation(context) {
                    let detail = appLanguage.userMessage(for: error)
                    errorMessage = appLanguage.text(
                        "综合设置尚未生效，将自动重试。\(detail)",
                        "Synthesis settings are not applied yet and will retry automatically. \(detail)"
                    )
                } else if attempt == 5 {
                    if isCurrentRunOperation(context) {
                        let detail = appLanguage.userMessage(for: error)
                        errorMessage = appLanguage.text(
                            "无法应用综合设置，请调整后重试。\(detail)",
                            "Synthesis settings could not be applied. Adjust them and try again. \(detail)"
                        )
                    }
                    return
                }
                do {
                    try await Task.sleep(for: .seconds(retryDelay))
                } catch {
                    return
                }
                retryDelay = min(3.2, retryDelay * 2)
            }
        }
    }

    func rememberCurrentSettings() {
        guard let workspaceID = selectedWorkspaceID else { return }
        let workspaceKey = machineWorkspaceContextKey(workspaceID: workspaceID)
        let previous = settingsByWorkspace[workspaceKey]
        settings.agents = max(1, settings.agents)
        settings.synthesisAgents = max(0, settings.synthesisAgents)
        settings.followUpDelay = max(0, settings.followUpDelay)
        settings.maxParallel = max(1, settings.maxParallel)
        settings.subagentsLimit = max(1, settings.subagentsLimit)
        settings.codexExecutable = settings.codexExecutable
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if settings.codexExecutable.isEmpty { settings.codexExecutable = "codex" }
        settings.runsDirectory = settings.runsDirectory
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let changedFields = previous.map { settings.changedFields(from: $0) }
            ?? WorkspaceSettingField.all
        settingsByWorkspace[workspaceKey] = settings
        self.desktopStateStore.saveSettings(settingsByWorkspace)
        if !changedFields.isEmpty {
            scheduleRemoteSettingsSave(
                workspaceID: workspaceID,
                settings: settings,
                fields: changedFields
            )
        }
        if changedFields.contains(.followUpDelay),
           followUpQueueOwnershipIsKnown(), !daemonOwnsFollowUpQueue(),
           !followUpQueue.isEmpty {
            for index in followUpQueue.indices where followUpQueue[index].dispatch == nil {
                var queuedSettings = followUpQueue[index].settings ?? settings
                queuedSettings.followUpDelay = settings.followUpDelay
                followUpQueue[index].settings = queuedSettings
            }
            persistFollowUpQueue()
            if let run = activeRun, !run.isWorking {
                Task { await self.reconcileFollowUpQueue(for: run, resetCountdown: true) }
            }
        }
    }
}
