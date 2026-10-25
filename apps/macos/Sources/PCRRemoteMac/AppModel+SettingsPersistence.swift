import Foundation

@MainActor
extension AppModel {
    func scheduleRemoteSettingsSave(
        workspaceID: String,
        settings: RunSettings,
        fields: Set<WorkspaceSettingField>,
        retry: Bool = false,
        machineID explicitMachineID: String? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) {
        guard !fields.isEmpty else { return }
        let target = explicitTarget ?? client.currentTarget()
        let machineID = explicitMachineID ?? target.machineID
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        var pendingFields = dirtySettingsFieldsByWorkspace[workspaceKey] ?? []
        pendingFields.formUnion(fields)
        pendingFields = WorkspaceSettingField.normalized(pendingFields)
        dirtySettingsFieldsByWorkspace[workspaceKey] = pendingFields
        self.desktopStateStore.saveDirtySettingFields(dirtySettingsFieldsByWorkspace)
        if !retry { reportedSettingsFailures.remove(workspaceKey) }
        let requestID = (settingsSaveRequestIDs[workspaceKey] ?? 0) + 1
        settingsSaveRequestIDs[workspaceKey] = requestID
        settingsPersistenceTasks[workspaceKey]?.cancel()
        let body = settings.remoteUpdateBody(
            workspaceID: workspaceID,
            fields: pendingFields
        )
        settingsPersistenceTasks[workspaceKey] = Task { [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(250))
                guard !Task.isCancelled, let self else { return }
                let _: WorkspaceSettingsResponse = try await self.client.call(
                    target: target,
                    "workspaces.settings.update",
                    body: body
                )
                guard !Task.isCancelled else { return }
                guard self.settingsSaveRequestIDs[workspaceKey] == requestID else { return }
                self.settingsPersistenceTasks[workspaceKey] = nil
                self.dirtySettingsFieldsByWorkspace.removeValue(forKey: workspaceKey)
                self.desktopStateStore.saveDirtySettingFields(
                    self.dirtySettingsFieldsByWorkspace
                )
                self.reportedSettingsFailures.remove(workspaceKey)
                if pendingFields.contains(.followUpDelay) {
                    _ = await self.refreshDaemonFollowUpQueue(
                        workspaceID: workspaceID,
                        runID: nil,
                        machineID: machineID,
                        migrateLocalQueue: false
                    )
                }
            } catch is CancellationError {
                return
            } catch {
                guard let self, !Task.isCancelled else { return }
                guard self.settingsSaveRequestIDs[workspaceKey] == requestID else { return }
                self.settingsPersistenceTasks[workspaceKey] = nil
                self.dirtySettingsFieldsByWorkspace[workspaceKey] = pendingFields
                self.desktopStateStore.saveDirtySettingFields(
                    self.dirtySettingsFieldsByWorkspace
                )
                if self.isCurrentMachine(machineID),
                   self.selectedWorkspaceID == workspaceID,
                   self.reportedSettingsFailures.insert(workspaceKey).inserted {
                    self.connectionErrorMessage = self.appLanguage.text(
                        "设置已保存在本机，设备重新连接后会自动同步。",
                        "Settings are saved locally and will sync when the computer reconnects."
                    )
                }
            }
        }
    }

    func retryDirtySettingsIfNeeded() {
        guard let workspaceID = selectedWorkspaceID,
              loadedWorkspaceID == workspaceID else { return }
        let workspaceKey = machineWorkspaceContextKey(workspaceID: workspaceID)
        guard let fields = dirtySettingsFieldsByWorkspace[workspaceKey],
              !fields.isEmpty,
              settingsPersistenceTasks[workspaceKey] == nil,
              let local = settingsByWorkspace[workspaceKey] else { return }
        scheduleRemoteSettingsSave(
            workspaceID: workspaceID,
            settings: local,
            fields: fields,
            retry: true
        )
    }

    func applyRemoteWorkspaceSettingsIfIdle() {
        guard !settingsPresented,
              let workspace = selectedWorkspace,
              workspace.workspaceId == loadedWorkspaceID,
              workspace.settings?.configured == true,
              let remote = workspace.settings else { return }
        // Resume is the visible conversation's identity, not a shared machine
        // preference. A health refresh must not turn a new draft into a reply
        // to whichever session the workspace most recently used.
        let preservedFields = (dirtySettingsFieldsByWorkspace[
            machineWorkspaceContextKey(workspaceID: workspace.workspaceId)
        ] ?? []).union([.resumeSessionID])
        let updated = settings.merging(
            remote: remote,
            preserving: preservedFields
        )
        guard updated != settings else { return }
        settings = updated
        settingsByWorkspace[
            machineWorkspaceContextKey(workspaceID: workspace.workspaceId)
        ] = updated
        self.desktopStateStore.saveSettings(settingsByWorkspace)
    }

    func migrateLegacyDesktopState(to machineID: String) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        var changed = false

        for (legacyKey, value) in Array(settingsByWorkspace)
            where !MachineContextKey.isMachineScoped(legacyKey) {
            let nextKey = machineWorkspaceContextKey(
                workspaceID: legacyKey,
                machineID: resolvedMachineID
            )
            if settingsByWorkspace[nextKey] == nil { settingsByWorkspace[nextKey] = value }
            settingsByWorkspace.removeValue(forKey: legacyKey)
            changed = true
        }

        for (legacyKey, value) in Array(dirtySettingsFieldsByWorkspace)
            where !MachineContextKey.isMachineScoped(legacyKey) {
            let nextKey = machineWorkspaceContextKey(
                workspaceID: legacyKey,
                machineID: resolvedMachineID
            )
            dirtySettingsFieldsByWorkspace[nextKey, default: []].formUnion(value)
            dirtySettingsFieldsByWorkspace.removeValue(forKey: legacyKey)
            changed = true
        }

        for (legacyKey, value) in Array(promptDraftsByContext)
            where !MachineContextKey.isMachineScoped(legacyKey) {
            let parts = legacyKey.split(
                separator: "\u{1f}",
                maxSplits: 1,
                omittingEmptySubsequences: false
            )
            guard let workspaceID = parts.first.map(String.init), !workspaceID.isEmpty else {
                continue
            }
            let sessionID = parts.count > 1 ? String(parts[1]) : ""
            let nextKey = machineSessionContextKey(
                workspaceID: workspaceID,
                sessionID: sessionID,
                machineID: resolvedMachineID
            )
            if promptDraftsByContext[nextKey] == nil { promptDraftsByContext[nextKey] = value }
            promptDraftsByContext.removeValue(forKey: legacyKey)
            changed = true
        }

        for (legacyKey, legacyQueue) in Array(persistedFollowUpQueues)
            where legacyQueue.machineID == nil || !MachineContextKey.isMachineScoped(legacyKey) {
            var queue = legacyQueue
            queue.machineID = resolvedMachineID
            let existing = persistedFollowUpQueues[queue.contextKey]
            if let existing, existing.contextKey != legacyKey {
                let existingIDs = Set(existing.items.map(\.id))
                let mergedItems = existing.items + queue.items.filter { !existingIDs.contains($0.id) }
                persistedFollowUpQueues[queue.contextKey] = PersistedFollowUpQueue(
                    machineID: resolvedMachineID,
                    workspaceID: queue.workspaceID,
                    runID: queue.runID,
                    items: mergedItems,
                    deadline: existing.deadline ?? queue.deadline
                )
            } else {
                persistedFollowUpQueues[queue.contextKey] = queue
            }
            if legacyKey != queue.contextKey {
                persistedFollowUpQueues.removeValue(forKey: legacyKey)
            }
            changed = true
        }

        for (legacyKey, legacySubmission) in Array(promptOutboxByWorkspace)
            where legacySubmission.machineID == nil || !MachineContextKey.isMachineScoped(legacyKey) {
            var submission = legacySubmission
            submission.machineID = resolvedMachineID
            if promptOutboxByWorkspace[submission.contextKey] == nil {
                promptOutboxByWorkspace[submission.contextKey] = submission
            }
            if legacyKey != submission.contextKey {
                promptOutboxByWorkspace.removeValue(forKey: legacyKey)
            }
            changed = true
        }

        for (legacyKey, legacyResult) in Array(acceptedPromptResultsByWorkspace)
            where legacyResult.submission.machineID == nil
                || !MachineContextKey.isMachineScoped(legacyKey) {
            var result = legacyResult
            result.submission.machineID = resolvedMachineID
            if acceptedPromptResultsByWorkspace[result.contextKey] == nil {
                acceptedPromptResultsByWorkspace[result.contextKey] = result
            }
            if legacyKey != result.contextKey {
                acceptedPromptResultsByWorkspace.removeValue(forKey: legacyKey)
            }
            changed = true
        }

        for (legacyKey, agent) in Array(selectedAgentsByContext)
            where !MachineContextKey.isMachineScoped(legacyKey) {
            let parts = legacyKey.split(
                separator: "\u{1f}",
                maxSplits: 1,
                omittingEmptySubsequences: false
            )
            guard parts.count == 2 else { continue }
            let nextKey = machineRunContextKey(
                workspaceID: String(parts[0]),
                runID: String(parts[1]),
                machineID: resolvedMachineID
            )
            if selectedAgentsByContext[nextKey] == nil { selectedAgentsByContext[nextKey] = agent }
            if selectedAgentContextsWhileWorking.remove(legacyKey) != nil {
                selectedAgentContextsWhileWorking.insert(nextKey)
            }
            selectedAgentsByContext.removeValue(forKey: legacyKey)
            changed = true
        }

        guard changed else { return }
        self.desktopStateStore.saveSettings(settingsByWorkspace)
        self.desktopStateStore.saveDirtySettingFields(dirtySettingsFieldsByWorkspace)
        self.desktopStateStore.savePromptDrafts(promptDraftsByContext)
        self.desktopStateStore.saveFollowUpQueues(persistedFollowUpQueues)
        self.desktopStateStore.savePromptOutbox(promptOutboxByWorkspace)
        self.desktopStateStore.saveAcceptedPromptResults(acceptedPromptResultsByWorkspace)
        self.desktopStateStore.saveAgentSelections(
            selectedAgentsByContext,
            selectedWhileWorking: selectedAgentContextsWhileWorking
        )
    }
}
