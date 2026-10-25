import Foundation

@MainActor
extension AppModel {
    func stashDefaultSettingsState(machineID: String) {
        defaultSettingsStates[machineID] = DefaultSettingsContextState(
            settings: defaultSettings,
            baseline: defaultSettingsBaseline,
            dirtyFields: dirtyDefaultSettingFields,
            loaded: defaultSettingsLoaded,
            needsSave: defaultSettingsNeedsSave,
            error: defaultSettingsError
        )
        self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
    }

    func restoreDefaultSettingsState(machineID: String) {
        let state = defaultSettingsStates[machineID] ?? DefaultSettingsContextState()
        defaultSettings = state.settings
        defaultSettingsBaseline = state.baseline
        dirtyDefaultSettingFields = state.dirtyFields
        defaultSettingsLoaded = state.loaded
        defaultSettingsNeedsSave = state.needsSave
        defaultSettingsError = state.error.map(appLanguage.localizedClientMessage)
        defaultSettingsLoading = false
        defaultSettingsSaving = defaultSettingsSaveTasks[machineID] != nil
        defaultPrimaryModelOptions = nil
        defaultSynthesisModelOptions = nil
        defaultSettingsOptionsError = nil
        defaultModelOptionErrors.removeAll()
        defaultPrimaryOptionsRequestID += 1
        defaultSynthesisOptionsRequestID += 1
    }

    func loadDefaultConversationSettings(force: Bool = false) async {
        let target = client.currentTarget()
        let machineID = target.machineID
        if defaultSettingsLoaded,
           defaultSettingsSaveTasks[machineID] != nil || defaultSettingsNeedsSave {
            if defaultSettingsSaveTasks[machineID] == nil {
                scheduleDefaultConversationSettingsSave()
            }
            return
        }
        if !daemonOnline {
            await refresh(reportErrors: false)
        }
        guard daemonOnline else {
            if !defaultSettingsLoaded {
                defaultSettingsBaseline = defaultSettings
                defaultSettingsLoaded = true
                stashDefaultSettingsState(machineID: machineID)
            }
            defaultSettingsError = appLanguage.text("服务端不可用", "Server unavailable")
            return
        }
        guard force || !defaultSettingsLoaded else { return }
        if !defaultSettingsLoaded {
            defaultSettingsBaseline = defaultSettings
            defaultSettingsLoaded = true
            stashDefaultSettingsState(machineID: machineID)
        }
        defaultSettingsLoading = true
        defaultSettingsError = nil
        defaultSettingsOptionsError = nil
        do {
            let response: WorkspaceSettingsResponse = try await client.call(
                target: target,
                "settings.defaults.get",
                body: EmptyBody()
            )
            var loaded = RunSettings()
            loaded.apply(response.settings)
            loaded.resumeSessionID = ""
            loaded.resumeIncludeNonInteractive = false
            loaded.runsDirectory = ""
            loaded.codexExecutable = "codex"
            guard isCurrentMachine(machineID) else {
                var state = defaultSettingsStates[machineID]
                    ?? DefaultSettingsContextState()
                let pendingFields = WorkspaceSettingField.normalized(
                    state.dirtyFields
                ).intersection(WorkspaceSettingField.defaultConversation)
                state.settings = state.settings.merging(
                    remote: response.settings,
                    preserving: pendingFields
                )
                state.baseline = loaded
                state.dirtyFields = pendingFields
                state.loaded = true
                state.needsSave = !pendingFields.isEmpty
                state.error = nil
                defaultSettingsStates[machineID] = state
                self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
                return
            }
            let pendingFields = WorkspaceSettingField.normalized(
                dirtyDefaultSettingFields
            ).intersection(WorkspaceSettingField.defaultConversation)
            defaultSettings = defaultSettings.merging(
                remote: response.settings,
                preserving: pendingFields
            )
            defaultSettingsBaseline = loaded
            dirtyDefaultSettingFields = pendingFields
            defaultSettingsLoaded = true
            defaultSettingsNeedsSave = !pendingFields.isEmpty
            stashDefaultSettingsState(machineID: machineID)
            async let primary: Void = loadDefaultPrimaryModelOptions()
            async let synthesis: Void = loadDefaultSynthesisModelOptions()
            _ = await (primary, synthesis)
        } catch {
            if isCurrentMachine(machineID) {
                defaultSettingsError = appLanguage.userMessage(for: error)
            }
        }
        guard isCurrentMachine(machineID) else { return }
        defaultSettingsLoading = false
        if defaultSettingsNeedsSave {
            scheduleDefaultConversationSettingsSave()
        }
    }

    func scheduleDefaultConversationSettingsSave(machineID requestedMachineID: String? = nil) {
        let machineID = MachineContextKey.normalizeMachineID(
            requestedMachineID ?? currentMachineContextID
        )
        let current = isCurrentMachine(machineID)
        var state: DefaultSettingsContextState
        if current {
            guard defaultSettingsLoaded else { return }
            state = DefaultSettingsContextState(
                settings: defaultSettings,
                baseline: defaultSettingsBaseline,
                dirtyFields: dirtyDefaultSettingFields,
                loaded: true,
                needsSave: defaultSettingsNeedsSave,
                error: defaultSettingsError
            )
        } else {
            guard let stored = defaultSettingsStates[machineID], stored.loaded else { return }
            state = stored
        }

        let baseline = state.baseline ?? state.settings
        state.dirtyFields.formUnion(
            state.settings.changedFields(from: baseline)
                .intersection(WorkspaceSettingField.defaultConversation)
        )
        state.dirtyFields = WorkspaceSettingField.normalized(
            state.dirtyFields
        ).intersection(WorkspaceSettingField.defaultConversation)
        guard !state.dirtyFields.isEmpty else {
            state.needsSave = false
            defaultSettingsStates[machineID] = state
            self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
            if current {
                dirtyDefaultSettingFields.removeAll()
                defaultSettingsNeedsSave = false
                defaultSettingsSaving = false
            }
            return
        }
        if current, defaultSettingsLoading {
            state.needsSave = true
            defaultSettingsStates[machineID] = state
            self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
            defaultSettingsNeedsSave = true
            return
        }
        guard let target = requestTarget(for: machineID) else {
            state.needsSave = true
            state.error = "Settings are waiting for this computer to reconnect."
            defaultSettingsStates[machineID] = state
            self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
            if current {
                dirtyDefaultSettingFields = state.dirtyFields
                defaultSettingsNeedsSave = true
                defaultSettingsSaving = false
                defaultSettingsError = state.error.map(appLanguage.localizedClientMessage)
            }
            return
        }
        let requestID = (defaultSettingsSaveRequestIDs[machineID] ?? 0) + 1
        defaultSettingsSaveRequestIDs[machineID] = requestID
        let snapshot = state.settings
        let fields = state.dirtyFields
        defaultSettingsSaveTasks[machineID]?.cancel()
        state.needsSave = true
        state.error = nil
        defaultSettingsStates[machineID] = state
        self.desktopStateStore.saveDefaultSettings(defaultSettingsStates)
        if current {
            dirtyDefaultSettingFields = fields
            defaultSettingsNeedsSave = true
            defaultSettingsSaving = true
            defaultSettingsError = nil
        }
        defaultSettingsSaveTasks[machineID] = Task { [weak self] in
            var retryDelay = 0.3
            for attempt in 1...5 {
                do {
                    try await Task.sleep(for: .seconds(retryDelay))
                    guard !Task.isCancelled, let self else { return }
                    let response: WorkspaceSettingsResponse = try await self.client.call(
                        target: target,
                        "settings.defaults.update",
                        body: snapshot.defaultConversationUpdateBody(fields: fields)
                    )
                    guard !Task.isCancelled,
                          self.defaultSettingsSaveRequestIDs[machineID] == requestID else {
                        return
                    }
                    var saved = RunSettings()
                    saved.apply(response.settings)
                    saved.resumeSessionID = ""
                    saved.resumeIncludeNonInteractive = false
                    saved.runsDirectory = ""
                    saved.codexExecutable = "codex"
                    self.defaultSettingsStates[machineID] = DefaultSettingsContextState(
                        settings: saved,
                        baseline: saved,
                        loaded: true
                    )
                    self.desktopStateStore.saveDefaultSettings(self.defaultSettingsStates)
                    self.defaultSettingsAutoRetryAfter.removeValue(forKey: machineID)
                    self.defaultSettingsSaveTasks[machineID] = nil
                    if self.isCurrentMachine(machineID) {
                        self.defaultSettingsSaving = false
                        self.defaultSettingsError = nil
                        self.defaultSettingsNeedsSave = false
                        self.defaultSettings = saved
                        self.defaultSettingsBaseline = saved
                        self.dirtyDefaultSettingFields.removeAll()
                    }
                    return
                } catch is CancellationError {
                    return
                } catch {
                    guard let self,
                          !Task.isCancelled,
                          self.defaultSettingsSaveRequestIDs[machineID] == requestID else {
                        return
                    }
                    let detail = self.appLanguage.userMessage(for: error)
                    let message = attempt < 5
                        ? self.appLanguage.text(
                            "设置尚未保存，将自动重试。\(detail)",
                            "Settings are not saved yet and will retry automatically. \(detail)"
                        )
                        : self.appLanguage.text(
                            "设置仍在等待同步，服务恢复后会自动重试。\(detail)",
                            "Settings are still waiting to sync and will retry when the server is available. \(detail)"
                        )
                    var state = self.defaultSettingsStates[machineID]
                        ?? DefaultSettingsContextState()
                    state.settings = snapshot
                    state.baseline = baseline
                    state.dirtyFields = fields
                    state.loaded = true
                    state.needsSave = true
                    state.error = message
                    self.defaultSettingsStates[machineID] = state
                    self.desktopStateStore.saveDefaultSettings(self.defaultSettingsStates)
                    if self.isCurrentMachine(machineID) {
                        self.defaultSettingsError = message
                    }
                    if attempt == 5 {
                        self.defaultSettingsAutoRetryAfter[machineID] =
                            Date().addingTimeInterval(60)
                        self.defaultSettingsSaveTasks[machineID] = nil
                        if self.isCurrentMachine(machineID) {
                            self.defaultSettingsSaving = false
                        }
                        return
                    }
                    retryDelay = min(3.0, retryDelay * 2)
                }
            }
        }
    }

    func retryDefaultSettingsIfNeeded() {
        let currentMachineID = currentMachineContextID
        if defaultSettingsLoaded || defaultSettingsNeedsSave {
            stashDefaultSettingsState(machineID: currentMachineID)
        }
        let now = Date()
        let pendingMachineIDs: [String] = defaultSettingsStates.compactMap { entry in
            let (machineID, state) = entry
            guard state.loaded,
                  state.needsSave,
                  !state.dirtyFields.isEmpty,
                  defaultSettingsSaveTasks[machineID] == nil,
                  now >= (defaultSettingsAutoRetryAfter[machineID] ?? .distantPast),
                  requestTarget(for: machineID) != nil else {
                return nil
            }
            if let machine = machines.first(where: { $0.deviceId == machineID }),
               machine.online == false {
                return nil
            }
            if machineID == currentMachineID, defaultSettingsLoading {
                return nil
            }
            return machineID
        }
        for machineID in pendingMachineIDs {
            defaultSettingsAutoRetryAfter[machineID] = now.addingTimeInterval(60)
            scheduleDefaultConversationSettingsSave(machineID: machineID)
        }
    }

    func updateDefaultPrimaryModel(_ model: String) async {
        let changed = defaultSettings.model != model
        defaultSettings.model = model
        if changed {
            defaultSettings.effort = ""
            defaultSettings.fast = .automatic
            defaultPrimaryModelOptions = nil
        }
        await loadDefaultPrimaryModelOptions()
        scheduleDefaultConversationSettingsSave()
    }

    func updateDefaultSynthesisModel(_ model: String) async {
        let changed = defaultSettings.synthesisModel != model
        defaultSettings.synthesisModel = model
        if changed {
            defaultSettings.synthesisEffort = ""
            defaultSettings.synthesisFast = .automatic
            defaultSynthesisModelOptions = nil
        }
        await loadDefaultSynthesisModelOptions()
        scheduleDefaultConversationSettingsSave()
    }

    func reloadDefaultModelOptions() async {
        defaultModelOptionErrors.removeAll()
        defaultSettingsOptionsError = nil
        async let primary: Void = loadDefaultPrimaryModelOptions()
        async let synthesis: Void = loadDefaultSynthesisModelOptions()
        _ = await (primary, synthesis)
    }

    private func setDefaultModelOptionsError(_ stage: String, error: Error?) {
        if let error {
            defaultModelOptionErrors[stage] = appLanguage.userMessage(for: error)
        } else {
            defaultModelOptionErrors.removeValue(forKey: stage)
        }
        let message = defaultModelOptionErrors
            .sorted { $0.key < $1.key }
            .map {
                let label = appLanguage.text(
                    $0.key == "Primary" ? "主 Agent" : "Synthesis",
                    $0.key
                )
                return "\(label): \($0.value)"
            }
            .joined(separator: "  ")
        defaultSettingsOptionsError = message.isEmpty ? nil : message
    }

    private func loadDefaultPrimaryModelOptions() async {
        let target = client.currentTarget()
        let machineID = target.machineID
        defaultPrimaryOptionsRequestID += 1
        let requestID = defaultPrimaryOptionsRequestID
        let requestedModel = defaultSettings.model
        let requestedEffort = defaultSettings.effort
        do {
            let options: CodexModelOptions = try await client.call(
                target: target,
                "codex.model_options",
                body: ModelOptionsBody(
                    model: requestedModel.isEmpty ? nil : requestedModel,
                    effort: requestedEffort.isEmpty ? nil : requestedEffort
                )
            )
            guard isCurrentMachine(machineID),
                  requestID == defaultPrimaryOptionsRequestID,
                  defaultSettings.model == requestedModel,
                  defaultSettings.effort == requestedEffort else { return }
            defaultPrimaryModelOptions = options
            setDefaultModelOptionsError("Primary", error: nil)
            if !defaultSettings.effort.isEmpty,
               !options.efforts.contains(where: { $0.value == defaultSettings.effort }) {
                defaultSettings.effort = ""
                defaultSettingsNeedsSave = true
                scheduleDefaultConversationSettingsSave()
            }
        } catch {
            guard isCurrentMachine(machineID),
                  requestID == defaultPrimaryOptionsRequestID else { return }
            defaultPrimaryModelOptions = nil
            setDefaultModelOptionsError("Primary", error: error)
        }
    }

    private func loadDefaultSynthesisModelOptions() async {
        let target = client.currentTarget()
        let machineID = target.machineID
        defaultSynthesisOptionsRequestID += 1
        let requestID = defaultSynthesisOptionsRequestID
        let requestedModel = defaultSettings.synthesisModel
        let requestedEffort = defaultSettings.synthesisEffort
        do {
            let options: CodexModelOptions = try await client.call(
                target: target,
                "codex.model_options",
                body: ModelOptionsBody(
                    model: requestedModel.isEmpty ? nil : requestedModel,
                    effort: requestedEffort.isEmpty ? nil : requestedEffort
                )
            )
            guard isCurrentMachine(machineID),
                  requestID == defaultSynthesisOptionsRequestID,
                  defaultSettings.synthesisModel == requestedModel,
                  defaultSettings.synthesisEffort == requestedEffort else { return }
            defaultSynthesisModelOptions = options
            setDefaultModelOptionsError("Synthesis", error: nil)
            if !defaultSettings.synthesisEffort.isEmpty,
               !options.efforts.contains(where: {
                   $0.value == defaultSettings.synthesisEffort
               }) {
                defaultSettings.synthesisEffort = ""
                defaultSettingsNeedsSave = true
                scheduleDefaultConversationSettingsSave()
            }
        } catch {
            guard isCurrentMachine(machineID),
                  requestID == defaultSynthesisOptionsRequestID else { return }
            defaultSynthesisModelOptions = nil
            setDefaultModelOptionsError("Synthesis", error: error)
        }
    }
}
