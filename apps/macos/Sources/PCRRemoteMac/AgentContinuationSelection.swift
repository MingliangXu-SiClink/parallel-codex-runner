import Foundation

struct AgentContinuationSelection: Equatable {
    enum Status: Equatable { case pending, confirmed, unavailable }
    let id: UUID
    let agent: Int
    var status: Status
}

@MainActor
extension AppModel {
    var continuationSelectionNeedsConfirmation: Bool {
        guard let key = selectedAgentContextKey(), let choice = continuationSelections[key],
              let run = activeRun, !run.isWorking, run.finalizedAgent == nil,
              run.agents.contains(where: { $0.agent == choice.agent && $0.mayContinue }) else { return false }
        return choice.status != .confirmed
    }

    var continuationSelectionContextText: String? {
        guard continuationSelectionNeedsConfirmation, let key = selectedAgentContextKey() else { return nil }
        if continuationSelections[key]?.status == .unavailable {
            return appLanguage.text("仅查看此回复；续问来源尚未确认", "Viewing this reply; continuation source is not confirmed")
        }
        return appLanguage.text("正在确认续问来源", "Confirming the continuation source")
    }

    func requestAgentContinuationSelection(_ agent: Int, run: RunSummary) {
        guard !run.isWorking, run.finalizedAgent == nil,
              run.agents.contains(where: { $0.agent == agent && $0.mayContinue }),
              selectedWorkspace?.lastRunId == run.runId,
              let workspaceID = selectedWorkspaceID,
              let key = selectedAgentContextKey(), let target = requestTarget(for: currentMachineContextID),
              systemCapabilities?.supports("followups.select_agent") == true
                || systemCapabilitiesByMachine[currentMachineContextID]?.supports("followups.select_agent") == true
        else { return }
        continuationSelections[key] = .init(id: UUID(), agent: agent, status: .pending)
        pauseFollowUpForAgentSelection()
        guard continuationSelectionTasks[key] == nil else { return }
        let machineID = currentMachineContextID
        let generation = workspaceContextGeneration
        let taskID = UUID()
        continuationSelectionTaskIDs[key] = taskID
        continuationSelectionTasks[key] = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.continuationSelectionTaskIDs[key] == taskID {
                    self.continuationSelectionTasks[key] = nil
                    self.continuationSelectionTaskIDs[key] = nil
                }
            }
            // One in-flight write per run. Coalesce only unsent choices; an
            // uncertain receipt is retried with the SAME id before the next
            // choice is sent. Cancellation is not a server-side rollback.
            while !Task.isCancelled, let intent = self.continuationSelections[key], intent.status == .pending {
                guard self.isCurrentMachine(machineID), self.selectedWorkspaceID == workspaceID,
                      self.workspaceContextGeneration == generation,
                      self.activeRun?.runId == run.runId, self.activeRun?.isWorking == false,
                      self.activeRun?.finalizedAgent == nil else {
                    self.continuationSelections.removeValue(forKey: key)
                    return
                }
                var attempt = 0
                while !Task.isCancelled {
                    do {
                        let response: RemoteFollowUpQueueResponse = try await self.client.call(
                            target: target, "followups.select_agent",
                            body: FollowUpAgentSelectionBody(workspaceId: workspaceID,
                                runId: run.runId, agent: intent.agent),
                            commandID: "follow-up-select-\(intent.id.uuidString.lowercased())"
                        )
                        guard !Task.isCancelled else { return }
                        self.applyDaemonFollowUpQueue(response.queue, workspaceID: workspaceID,
                            machineID: machineID, revision: response.revision)
                        if self.continuationSelections[key]?.id == intent.id {
                            let workspaceKey = MachineContextKey.workspace(machineID: machineID,
                                workspaceID: workspaceID)
                            let currentQueue = self.daemonFollowUpSnapshotsByWorkspace[workspaceKey]
                            let confirmed = response.queue?.runId == run.runId
                                && response.queue?.selectedAgent == intent.agent
                                && currentQueue?.runId == run.runId
                                && currentQueue?.selectedAgent == intent.agent
                            self.continuationSelections[key]?.status = confirmed ? .confirmed : .unavailable
                            if confirmed, self.isCurrentMachine(machineID),
                               self.selectedWorkspaceID == workspaceID,
                               self.workspaceContextGeneration == generation,
                               self.activeRun?.runId == run.runId {
                                if let currentRun = self.activeRun {
                                    await self.reconcileFollowUpQueue(for: currentRun, resetCountdown: true)
                                }
                            }
                        }
                        break
                    } catch {
                        if self.selectionReceiptIsDefinitive(error) {
                            if self.continuationSelections[key]?.id == intent.id {
                                self.continuationSelections[key]?.status = .unavailable
                            }
                            break
                        }
                        guard self.isCurrentMachine(machineID), self.selectedWorkspaceID == workspaceID,
                              self.workspaceContextGeneration == generation,
                              self.activeRun?.runId == run.runId else { return }
                        attempt += 1
                        do { try await Task.sleep(for: .seconds(min(8, Double(attempt)))) }
                        catch { return }
                    }
                }
            }
        }
    }

    private func selectionReceiptIsDefinitive(_ error: Error) -> Bool {
        guard let error = error as? PcrdClientError, case .remote(let code, _) = error else { return false }
        // A relay transport error is not an acknowledgement from the target
        // scheduler. Keep its command identity until a real receipt arrives.
        return !code.hasPrefix("relay_") && code != "command_timeout"
    }
}
