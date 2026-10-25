import Foundation

@MainActor
extension AppModel {
    func refresh(
        reportErrors: Bool = true,
        forceMachineDirectoryRefresh: Bool = false
    ) async {
        refreshRequestID += 1
        let requestID = refreshRequestID
        defer {
            if remoteTurnTransition?.requestID == requestID {
                remoteTurnTransition = nil
            }
        }
        var requestMachineID = currentMachineContextID
        do {
            try await prepareMachineTarget(
                forceDirectoryRefresh: forceMachineDirectoryRefresh
            )
            let target = client.currentTarget()
            requestMachineID = target.machineID
            await restoreWorkspaceDirectoryFromCache(machineID: target.machineID, requestID: requestID)
            guard isCurrentMachine(target.machineID), requestID == refreshRequestID else { return }
            let capabilities = try await verifiedSystemCapabilities(target: target)
            guard capabilities.supports("system.health") else {
                throw PcrdClientError.remote(
                    "unsupported_action",
                    "The connected server does not support system.health."
                )
            }
            let health: HealthResponse = try await client.call(
                target: target,
                "system.health",
                body: EmptyBody()
            )
            guard isCurrentMachine(target.machineID),
                  requestID == refreshRequestID else { return }
            appliedRefreshRequestID = requestID
            let nextDaemonOnline = health.status == "ok"
            if daemonOnline != nextDaemonOnline { daemonOnline = nextDaemonOnline }
            if connectionLoading { connectionLoading = false }
            if connectionErrorMessage != nil { connectionErrorMessage = nil }
            let nextStatusText = nextDaemonOnline ? "Connected" : health.status
            if statusText != nextStatusText { statusText = nextStatusText }
            let previousWorkspaceID = selectedWorkspaceID
            let previousSelectedWorkspace = previousWorkspaceID.flatMap { workspaceID in
                workspaces.first { $0.workspaceId == workspaceID }
            }
            let previousVisibleRunID = activeRun?.runId
            let nextWorkspaces = reconciledWorkspaceRegistry(health.workspaceRegistry.workspaces,
                revision: health.workspaceRegistry.revision, machineID: target.machineID)
            let nextWorkspaceID = previousWorkspaceID.flatMap { previous in
                nextWorkspaces.contains { $0.workspaceId == previous } ? previous : nil
            } ?? rememberedWorkspaceID(
                machineID: target.machineID,
                available: nextWorkspaces
            ) ?? health.workspaceRegistry.selectedWorkspaceId.flatMap { remote in
                nextWorkspaces.contains { $0.workspaceId == remote } ? remote : nil
            } ?? nextWorkspaces.first?.workspaceId
            let nextSelectedWorkspace = nextWorkspaceID.flatMap { workspaceID in
                nextWorkspaces.first { $0.workspaceId == workspaceID }
            }
            let followsRemoteLatest = previousWorkspaceID == nextWorkspaceID
                && previousVisibleRunID != nil
                && previousVisibleRunID == previousSelectedWorkspace?.lastRunId
                && nextSelectedWorkspace?.lastRunId.isEmpty == false
                && nextSelectedWorkspace?.lastRunId != previousSelectedWorkspace?.lastRunId
            if followsRemoteLatest, let workspaceID = nextWorkspaceID {
                remoteTurnTransition = RemoteTurnTransition(
                    requestID: requestID,
                    machineID: target.machineID,
                    workspaceID: workspaceID,
                    conversationID: selectedConversationID
                )
            }
            if selectedWorkspaceID != nextWorkspaceID {
                cacheVisibleRunViewState()
            }
            if workspaces != nextWorkspaces { workspaces = nextWorkspaces }
            persistWorkspaceDirectory(nextWorkspaces, machineID: target.machineID)
            if workspaceRevision != health.workspaceRegistry.revision {
                workspaceRevision = max(workspaceRevision, health.workspaceRegistry.revision)
            }
            if devices != health.devices.devices { devices = health.devices.devices }
            if selectedWorkspaceID != nextWorkspaceID {
                suspendPromptHistoryContext()
                selectedWorkspaceID = nextWorkspaceID
                resetRunView()
                rememberWorkspaceSelection()
            }
            if relay != health.relay { relay = health.relay }
            relayBootstrapRetryAfter = .distantPast
            reconcileRunPollingContexts(
                machineID: target.machineID,
                target: target,
                workspaces: nextWorkspaces
            )
            scheduleConversationPreload(
                machineID: target.machineID,
                target: target,
                workspaces: nextWorkspaces
            )
            applyRemoteWorkspaceSettingsIfIdle()
            resumePendingPromptSubmissions()
            ensureFollowUpCoordinator()

            if loadedWorkspaceID != selectedWorkspaceID {
                await loadSelectedWorkspaceContext()
            } else {
                await refreshLatestWorkspaceConversation(
                    followRemoteLatest: followsRemoteLatest
                )
            }
            retryDirtySettingsIfNeeded()
            retryDefaultSettingsIfNeeded()
        } catch {
            guard requestID == refreshRequestID,
                  isCurrentMachine(requestMachineID) else { return }
            appliedRefreshRequestID = requestID
            if daemonOnline { daemonOnline = false }
            if connectionLoading { connectionLoading = false }
            if statusText != "Offline" { statusText = "Offline" }
            nextMachineDirectoryRefresh = .distantPast
            relayBootstrapRetryAfter = Date().addingTimeInterval(30)
            if let clientError = error as? PcrdClientError,
               case .connection = clientError {
                systemCapabilities = nil
            }
            let message = appLanguage.userMessage(for: error)
            if reportErrors, connectionErrorMessage != message {
                connectionErrorMessage = message
            }
        }
    }

    private func prepareMachineTarget(forceDirectoryRefresh: Bool) async throws {
        if let relayBootstrapTask {
            try await relayBootstrapTask.value
            return
        }
        let token = UUID()
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            try await self.performMachineTargetPreparation(
                forceDirectoryRefresh: forceDirectoryRefresh
            )
        }
        relayBootstrapTask = task
        relayBootstrapToken = token
        defer {
            if relayBootstrapToken == token {
                relayBootstrapTask = nil
                relayBootstrapToken = nil
            }
        }
        try await task.value
    }

    private func performMachineTargetPreparation(
        forceDirectoryRefresh: Bool
    ) async throws {
        if forceDirectoryRefresh {
            machineDirectoryRefreshTask?.cancel()
            machineDirectoryRefreshTask = nil
            machineDirectoryRefreshToken = nil
        }
        if !forceDirectoryRefresh,
           Date() < nextMachineDirectoryRefresh,
           let selectedMachineID,
           let selected = machines.first(where: { $0.deviceId == selectedMachineID }),
           client.currentTarget().machineID == selected.deviceId {
            return
        }
        let localStatus: RelayStatus? = try? await client.callLocal(
            "relay.status",
            body: EmptyBody()
        )
        if localDaemonMachineID == nil {
            localDaemonMachineID = localStatus?.macDeviceId
        }

        let remembered = await client.selectedRelayMachineID()
        let requestedMachineID = forceDirectoryRefresh
            ? (remembered ?? selectedMachineID)
            : (selectedMachineID ?? remembered)
        if let localStatus,
           shouldUseLocalMachineFirst(
               requestedMachineID: requestedMachineID,
               localStatus: localStatus
           ) {
            activateLocalMachine(localStatus)
            scheduleMachineDirectoryRefresh(localStatus: localStatus)
            return
        }
        if !forceDirectoryRefresh, Date() < relayBootstrapRetryAfter {
            throw PcrdClientError.connection(
                "The desktop app is waiting before reconnecting to the server."
            )
        }

        let controllerIdentity = try? await client.relayControllerIdentity()
        let controllerIsPaired = await client.relayIsPaired()
        let controllerIsTrusted: Bool
        if localStatus?.trustedControllers == nil {
            // pcrd versions before controller introspection can still use an
            // existing pairing; the next pcrd update enables active repair.
            controllerIsTrusted = controllerIsPaired && localStatus?.paired == true
        } else {
            controllerIsTrusted = controllerIdentity.map {
                localStatus?.trusts($0) == true
            } ?? false
        }
        if !controllerIsPaired || !controllerIsTrusted {
            let pairing: RelayStatus = try await client.callLocal(
                "relay.pairing_create",
                body: RelayPairingBody(force: false)
            )
            guard let code = pairing.pairingUri, !code.isEmpty else {
                throw PcrdClientError.remote(
                    "relay_error",
                    "The computer server did not create a connection code."
                )
            }
            localDaemonMachineID = pairing.macDeviceId ?? localDaemonMachineID
            try await client.connectRelayController(
                pairingCode: code,
                deviceName: "\(AppIdentity.productName) on \(Host.current().localizedName ?? "Mac")"
            )
            var claimed = false
            for _ in 0..<40 {
                let result: RelayPollResult = try await client.callLocal(
                    "relay.poll",
                    body: EmptyBody()
                )
                let identity = try await client.relayControllerIdentity()
                let status: RelayStatus = try await client.callLocal(
                    "relay.status",
                    body: EmptyBody()
                )
                if status.trusts(identity) {
                    claimed = true
                    break
                }
                if result.pairingStatus == "expired" {
                    break
                }
                try await Task.sleep(for: .milliseconds(250))
            }
            guard claimed else {
                throw PcrdClientError.remote(
                    "relay_error",
                    "The computer server did not finish connecting to the desktop app."
                )
            }
        }

        let available = try await client.relayMachines()
        guard !available.isEmpty else {
            throw PcrdClientError.connection(
                "No connected computers are available for this app."
            )
        }
        let candidate = available.first(where: { $0.deviceId == selectedMachineID })
            ?? available.first(where: { $0.deviceId == remembered })
            ?? available.first
        guard let selected = candidate else {
            throw PcrdClientError.connection(
                "No connected computers are available for this app."
            )
        }
        let previousMachineID = client.currentTarget().machineID
        if previousMachineID != selected.deviceId {
            cancelConversationPreload()
            cacheVisibleRunViewState()
            suspendPromptHistoryContext()
            if defaultSettingsLoaded || defaultSettingsNeedsSave {
                stashDefaultSettingsState(machineID: previousMachineID)
            }
            resetRunView()
            loadedWorkspaceID = nil
            selectedWorkspaceID = nil
            workspaces = []
            workspaceDirectoryIsCached = false
            workspaceDirectoryLoaded = false
            devices = []
            workspaceRevision = 0
            systemCapabilities = nil
            connectionLoading = true
        }
        machines = available
        nextMachineDirectoryRefresh = Date().addingTimeInterval(60)
        pruneRunPollingContexts(
            availableMachineIDs: Set(available.map(\.deviceId))
        )
        selectedMachineID = selected.deviceId
        await client.selectRelayMachine(
            selected,
            localMachineID: localDaemonMachineID
        )
        migrateLegacyDesktopState(to: selected.deviceId)
        systemCapabilities = systemCapabilitiesByMachine[selected.deviceId]
        if previousMachineID != selected.deviceId {
            restoreDefaultSettingsState(machineID: selected.deviceId)
        }
    }

    private func shouldUseLocalMachineFirst(
        requestedMachineID: String?,
        localStatus: RelayStatus
    ) -> Bool {
        let localID = localStatus.macDeviceId?.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        let requested = requestedMachineID?.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        return requested?.isEmpty != false
            || requested == MachineContextKey.localMachineID
            || (localID?.isEmpty == false && requested == localID)
    }

    private func activateLocalMachine(_ localStatus: RelayStatus) {
        let local = localMachineDescriptor(
            status: localStatus,
            remoteMachines: machines
        )
        let previousMachineID = client.currentTarget().machineID
        if previousMachineID != local.deviceId {
            cancelConversationPreload()
            cacheVisibleRunViewState()
            suspendPromptHistoryContext()
            if defaultSettingsLoaded || defaultSettingsNeedsSave {
                stashDefaultSettingsState(machineID: previousMachineID)
            }
            resetRunView()
            loadedWorkspaceID = nil
            selectedWorkspaceID = nil
            workspaces = []
            workspaceDirectoryIsCached = false
            workspaceDirectoryLoaded = false
            devices = []
            workspaceRevision = 0
            systemCapabilities = nil
            connectionLoading = true
        }
        machines = mergeMachineDirectory(
            remoteMachines: machines,
            localStatus: localStatus
        )
        selectedMachineID = local.deviceId
        client.setRelayMachineTarget(
            local,
            localMachineID: local.deviceId
        )
        migrateLegacyDesktopState(to: local.deviceId)
        systemCapabilities = systemCapabilitiesByMachine[local.deviceId]
        relayBootstrapRetryAfter = .distantPast
        nextMachineDirectoryRefresh = Date().addingTimeInterval(60)
        if previousMachineID != local.deviceId {
            restoreDefaultSettingsState(machineID: local.deviceId)
        }
    }

    private func scheduleMachineDirectoryRefresh(localStatus: RelayStatus) {
        guard localStatus.configured,
              localStatus.paired,
              machineDirectoryRefreshTask == nil else { return }
        let token = UUID()
        machineDirectoryRefreshToken = token
        machineDirectoryRefreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                if self.machineDirectoryRefreshToken == token {
                    self.machineDirectoryRefreshTask = nil
                    self.machineDirectoryRefreshToken = nil
                }
            }
            do {
                let available = try await self.client.relayMachines()
                guard !Task.isCancelled,
                      self.machineDirectoryRefreshToken == token else { return }
                self.machines = self.mergeMachineDirectory(
                    remoteMachines: available,
                    localStatus: localStatus
                )
                self.pruneRunPollingContexts(
                    availableMachineIDs: Set(self.machines.map(\.deviceId))
                )
            } catch {
                // The local app remains fully usable when the Relay is offline.
            }
        }
    }

    private func mergeMachineDirectory(
        remoteMachines: [RelayMachine],
        localStatus: RelayStatus
    ) -> [RelayMachine] {
        let local = localMachineDescriptor(
            status: localStatus,
            remoteMachines: remoteMachines
        )
        return [local] + remoteMachines
            .filter { $0.deviceId != local.deviceId }
            .sorted {
                let lhsOnline = $0.online == true ? 0 : 1
                let rhsOnline = $1.online == true ? 0 : 1
                if lhsOnline != rhsOnline { return lhsOnline < rhsOnline }
                let nameOrder = $0.name.localizedCaseInsensitiveCompare($1.name)
                if nameOrder != .orderedSame { return nameOrder == .orderedAscending }
                return $0.deviceId < $1.deviceId
            }
    }

    private func localMachineDescriptor(
        status: RelayStatus,
        remoteMachines: [RelayMachine]
    ) -> RelayMachine {
        let rawMachineID = status.macDeviceId?.trimmingCharacters(
            in: .whitespacesAndNewlines
        ) ?? ""
        let machineID = rawMachineID.isEmpty
            ? MachineContextKey.localMachineID
            : rawMachineID
        let remote = remoteMachines.first { $0.deviceId == machineID }
        let localName = status.deviceName?.trimmingCharacters(
            in: .whitespacesAndNewlines
        ) ?? ""
        return RelayMachine(
            deviceId: machineID,
            kind: remote?.kind ?? "mac",
            name: localName.isEmpty
                ? (remote?.name ?? localComputerDisplayName)
                : localName,
            publicKey: remote?.publicKey ?? "",
            status: "active",
            online: true,
            lastSeenAt: remote?.lastSeenAt
        )
    }

    func selectMachine(_ machineID: String) async {
        guard machineID != selectedMachineID,
              let machine = machines.first(where: { $0.deviceId == machineID }),
              !machineSelectionInProgress else { return }
        machineSelectionInProgress = true
        machineSelectionTargetID = machineID
        defer {
            machineSelectionInProgress = false
            machineSelectionTargetID = nil
        }

        let target = requestTarget(for: machineID) ?? .relay(machine)
        do {
            let capabilities = try await verifiedSystemCapabilities(target: target)
            guard capabilities.supports("system.health") else {
                throw PcrdClientError.remote(
                    "unsupported_action",
                    "The connected server does not support system.health."
                )
            }
            let health: HealthResponse = try await client.call(
                target: target,
                "system.health",
                body: EmptyBody()
            )
            guard machineSelectionTargetID == machineID,
                  machines.contains(where: { $0.deviceId == machineID }) else { return }

            let nextWorkspaces = health.workspaceRegistry.workspaces
            let nextWorkspaceID = rememberedWorkspaceID(
                machineID: machineID,
                available: nextWorkspaces
            ) ?? health.workspaceRegistry.selectedWorkspaceId.flatMap { remote in
                nextWorkspaces.contains { $0.workspaceId == remote } ? remote : nil
            } ?? nextWorkspaces.first?.workspaceId
            let nextWorkspace = nextWorkspaceID.flatMap { workspaceID in
                nextWorkspaces.first { $0.workspaceId == workspaceID }
            }
            let cachedRecords = if let nextWorkspaceID {
                await runViewStateStore.hydrateWorkspace(
                    machineID: machineID,
                    workspaceID: nextWorkspaceID
                )
            } else {
                [WorkspaceRunViewCacheRecord]()
            }
            guard machineSelectionTargetID == machineID,
                  machines.contains(where: { $0.deviceId == machineID }) else { return }

            suspendPromptHistoryContext()
            cacheVisibleRunViewState()
            stashDefaultSettingsState(machineID: currentMachineContextID)
            cancelConversationPreload()
            closeBrowser()
            pairingPresented = false
            pairingIsLoading = false
            pairingError = nil
            storageSummaryRequestID += 1
            storageSummary = nil
            storageSummaryLoading = false
            storageSummaryError = nil
            refreshRequestID += 1
            workspaceSelectionRequestID += 1

            client.setRelayMachineTarget(
                machine,
                localMachineID: localDaemonMachineID
            )
            selectedMachineID = machineID
            selectedWorkspaceID = nil
            resetRunView()
            loadedWorkspaceID = nil
            workspaces = nextWorkspaces
            persistWorkspaceDirectory(nextWorkspaces, machineID: machineID)
            devices = health.devices.devices
            workspaceRevision = health.workspaceRegistry.revision
            relay = health.relay
            relayLink = health.link
            daemonOnline = health.status == "ok"
            statusText = daemonOnline ? "Connected" : health.status
            connectionLoading = false
            connectionErrorMessage = nil
            relayBootstrapRetryAfter = .distantPast
            nextMachineDirectoryRefresh = Date().addingTimeInterval(60)
            migrateLegacyDesktopState(to: machineID)
            systemCapabilities = capabilities
            restoreDefaultSettingsState(machineID: machineID)
            selectedWorkspaceID = nextWorkspaceID
            rememberWorkspaceSelection()
            if let nextWorkspace {
                _ = installCachedWorkspaceConversation(
                    cachedRecords,
                    workspace: nextWorkspace,
                    machineID: machineID
                )
                workspaceContextLoadingID = nextWorkspace.workspaceId
            }
            applyRemoteWorkspaceSettingsIfIdle()
            reconcileRunPollingContexts(
                machineID: machineID,
                target: target,
                workspaces: nextWorkspaces
            )
            scheduleConversationPreload(
                machineID: machineID,
                target: target,
                workspaces: nextWorkspaces
            )
            resumePendingPromptSubmissions()
            ensureFollowUpCoordinator()

            await client.persistSelectedRelayMachine(machine)
            await loadSelectedWorkspaceContext()
            retryDirtySettingsIfNeeded()
            retryDefaultSettingsIfNeeded()
        } catch {
            guard machineSelectionTargetID == machineID else { return }
            connectionErrorMessage = appLanguage.userMessage(for: error)
        }
    }

    func connectMachine() async {
        let code = machinePairingCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty, !machinePairingInProgress else { return }
        machinePairingInProgress = true
        machinePairingError = nil
        defer { machinePairingInProgress = false }
        do {
            try await client.connectRelayController(
                pairingCode: code,
                deviceName: "\(AppIdentity.productName) on \(Host.current().localizedName ?? "Mac")"
            )
            machinePairingCode = ""
            systemCapabilities = nil
            await refresh(forceMachineDirectoryRefresh: true)
            machinePairingPresented = false
        } catch {
            machinePairingError = appLanguage.userMessage(for: error)
        }
    }

    func verifiedSystemCapabilities(
        forceRefresh: Bool = false,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async throws -> SystemCapabilities {
        let target = explicitTarget ?? client.currentTarget()
        if !forceRefresh, let cached = systemCapabilitiesByMachine[target.machineID] {
            if isCurrentMachine(target.machineID) { systemCapabilities = cached }
            return cached
        }
        let capabilities: SystemCapabilities = try await client.call(
            target: target,
            "system.capabilities",
            body: EmptyBody()
        )
        guard capabilities.compatible else {
            throw PcrdClientError.remote(
                "incompatible_protocol",
                "The app and connected server use incompatible protocol versions."
            )
        }
        systemCapabilitiesByMachine[target.machineID] = capabilities
        if isCurrentMachine(target.machineID) { systemCapabilities = capabilities }
        return capabilities
    }

    func requireSystemCapability(_ action: String) async throws {
        let target = client.currentTarget()
        var capabilities = try await verifiedSystemCapabilities(target: target)
        if !capabilities.supports(action) {
            capabilities = try await verifiedSystemCapabilities(
                forceRefresh: true,
                target: target
            )
        }
        guard capabilities.supports(action) else {
            throw PcrdClientError.remote(
                "unsupported_action",
                "The connected server does not support \(action)."
            )
        }
    }

}
