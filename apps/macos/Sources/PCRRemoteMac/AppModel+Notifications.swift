import AppKit
import Foundation

extension AppModel {
    func refreshRunNotifications(includeBackgroundRuns: Bool = true) async {
        let currentTarget = client.currentTarget()
        let currentMachineID = currentTarget.machineID
        let daemonNotifiesThisMac = systemCapabilitiesByMachine[currentMachineID]?
            .supportsDaemonCompletionNotifications == true
            && currentMachineID == localDaemonMachineID
        if daemonNotifiesThisMac {
            observedWorkingRuns = observedWorkingRuns.filter {
                MachineContextKey.machineID(from: $0.key) != currentMachineID
            }
            runMonitoringInitializedMachines.insert(currentMachineID)
        }

        var currentWorking = Dictionary(
            uniqueKeysWithValues: workspaces.compactMap { workspace -> (String, String)? in
                guard workspace.removalLooksActive, !workspace.lastRunId.isEmpty else {
                    return nil
                }
                return (
                    MachineContextKey.task(
                        machineID: currentMachineID,
                        runID: workspace.lastRunId
                    ),
                    workspace.name
                )
            }
        )
        if !daemonNotifiesThisMac {
            let previouslyWorking = observedWorkingRuns.filter {
                MachineContextKey.machineID(from: $0.key) == currentMachineID
            }
            if runMonitoringInitializedMachines.insert(currentMachineID).inserted {
                observedWorkingRuns = observedWorkingRuns.filter {
                    MachineContextKey.machineID(from: $0.key) != currentMachineID
                }
                observedWorkingRuns.merge(currentWorking) { _, current in current }
            } else {
                for (runKey, workspaceName) in previouslyWorking
                    where currentWorking[runKey] == nil {
                    await refreshObservedRun(
                        runKey: runKey,
                        workspaceName: workspaceName,
                        target: currentTarget
                    )
                    if observedWorkingRuns[runKey] != nil {
                        currentWorking[runKey] = workspaceName
                    }
                }
                observedWorkingRuns = observedWorkingRuns.filter {
                    MachineContextKey.machineID(from: $0.key) != currentMachineID
                }
                observedWorkingRuns.merge(currentWorking) { _, current in current }
            }
        }

        let availableMachineIDs = Set(machines.map(\.deviceId)).union(
            [currentMachineID, localDaemonMachineID].compactMap { $0 }
        )
        observedWorkingRuns = observedWorkingRuns.filter { entry in
            MachineContextKey.machineID(from: entry.key).map(availableMachineIDs.contains)
                ?? false
        }
        let delegatableRuns = observedWorkingRuns.filter {
            MachineContextKey.machineID(from: $0.key) != localDaemonMachineID
        }
        for (runKey, workspaceName) in delegatableRuns {
            if await registerDaemonCompletionWatch(
                runKey: runKey,
                workspaceName: workspaceName
            ) {
                observedWorkingRuns.removeValue(forKey: runKey)
                notifiedRunIDs.insert(runKey)
            }
        }
        guard includeBackgroundRuns else { return }
        let backgroundRuns = observedWorkingRuns.filter {
            MachineContextKey.machineID(from: $0.key) != currentMachineID
        }
        for (runKey, workspaceName) in backgroundRuns {
            guard let machineID = MachineContextKey.machineID(from: runKey),
                  let target = notificationTarget(machineID: machineID) else { continue }
            if machineID == localDaemonMachineID,
               systemCapabilitiesByMachine[machineID]?
                   .supportsDaemonCompletionNotifications == true {
                observedWorkingRuns.removeValue(forKey: runKey)
                continue
            }
            await refreshObservedRun(
                runKey: runKey,
                workspaceName: workspaceName,
                target: target
            )
        }
    }

    private func registerDaemonCompletionWatch(
        runKey: String,
        workspaceName: String
    ) async -> Bool {
        if daemonMonitoredRemoteRuns.contains(runKey) { return true }
        guard let machineID = MachineContextKey.machineID(from: runKey),
              let runID = MachineContextKey.taskRunID(from: runKey),
              machineID != localDaemonMachineID,
              let machine = machines.first(where: { $0.deviceId == machineID }) else {
            return false
        }
        do {
            let response: ControllerCompletionWatchResponse = try await client.callLocal(
                "controller.completion_watch",
                body: ControllerCompletionWatchBody(
                    machineId: machine.deviceId,
                    machinePublicKey: machine.publicKey,
                    runId: runID,
                    workspaceName: workspaceName
                )
            )
            guard response.watching,
                  response.machineId == machine.deviceId,
                  response.runId == runID else { return false }
            daemonMonitoredRemoteRuns.insert(runKey)
            return true
        } catch {
            // Older local daemons and temporary local IPC failures keep using
            // the app-owned polling path until the next refresh succeeds.
            return false
        }
    }

    private func notificationTarget(machineID: String) -> PcrdRequestTarget? {
        if machineID == MachineContextKey.localMachineID {
            return .local
        }
        if machineID == localDaemonMachineID {
            return .localMachine(machineID)
        }
        return requestTarget(for: machineID)
    }

    private func refreshObservedRun(
        runKey: String,
        workspaceName: String,
        target: PcrdRequestTarget
    ) async {
        guard let runID = MachineContextKey.taskRunID(from: runKey) else {
            observedWorkingRuns.removeValue(forKey: runKey)
            return
        }
        do {
            let run: RunSummary = try await client.call(
                target: target,
                "turns.get",
                body: GetRunBody(
                    runId: runID,
                    includeEvents: false,
                    cursor: 0,
                    eventLimit: 1
                )
            )
            if run.isWorking || !allAgentsAreTerminal(run) {
                observedWorkingRuns[runKey] = workspaceName
            } else {
                observedWorkingRuns.removeValue(forKey: runKey)
                notifyRunCompletionIfNeeded(
                    run,
                    workspaceName: workspaceName,
                    machineID: target.machineID,
                    observedTransition: true
                )
            }
        } catch {
            // Keep monitoring this run across transient daemon failures.
            observedWorkingRuns[runKey] = workspaceName
        }
    }

    func workspaceDisplayName(for run: RunSummary) -> String {
        workspaces.first(where: {
            URL(fileURLWithPath: $0.path).standardizedFileURL.path
                == URL(fileURLWithPath: run.workspace).standardizedFileURL.path
        })?.name ?? URL(fileURLWithPath: run.workspace).lastPathComponent
    }

    func notifyRunCompletionIfNeeded(
        _ run: RunSummary,
        workspaceName explicitWorkspaceName: String? = nil,
        machineID explicitMachineID: String? = nil,
        observedTransition: Bool = false
    ) {
        let machineID = explicitMachineID ?? currentMachineContextID
        let runKey = MachineContextKey.task(machineID: machineID, runID: run.runId)
        guard run.isExecutionComplete,
              observedTransition || observedWorkingRuns[runKey] != nil,
              completionNotificationsEnabled,
              notifiedRunIDs.insert(runKey).inserted else { return }
        let daemonNotifiesThisMac = systemCapabilitiesByMachine[machineID]?
            .supportsDaemonCompletionNotifications == true
            && machineID == localDaemonMachineID
        guard !daemonNotifiesThisMac else {
            return
        }
        guard !NSApplication.shared.isActive else { return }
        let workspaceName = explicitWorkspaceName
            ?? selectedWorkspace?.name
            ?? URL(fileURLWithPath: run.workspace).lastPathComponent
        DesktopNotifier.runCompleted(workspaceName: workspaceName, runID: run.runId)
    }

    func allAgentsAreTerminal(_ run: RunSummary) -> Bool {
        !run.isWorking && !run.agents.isEmpty && run.agents.allSatisfy { $0.isTerminal }
    }
}
