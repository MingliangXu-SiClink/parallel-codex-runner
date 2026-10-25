import Foundation

extension AppModel {
    var currentMachineContextID: String {
        client.currentTarget().machineID
    }

    func machineWorkspaceContextKey(
        workspaceID: String,
        machineID: String? = nil
    ) -> String {
        MachineContextKey.workspace(
            machineID: machineID ?? currentMachineContextID,
            workspaceID: workspaceID
        )
    }

    func machineRunContextKey(
        workspaceID: String,
        runID: String,
        machineID: String? = nil
    ) -> String {
        MachineContextKey.run(
            machineID: machineID ?? currentMachineContextID,
            workspaceID: workspaceID,
            runID: runID
        )
    }

    func machineSessionContextKey(
        workspaceID: String,
        sessionID: String,
        machineID: String? = nil
    ) -> String {
        MachineContextKey.session(
            machineID: machineID ?? currentMachineContextID,
            workspaceID: workspaceID,
            sessionID: sessionID
        )
    }

    func machineConversationContextKey(
        workspaceID: String,
        conversationID: String,
        machineID: String? = nil
    ) -> String {
        MachineContextKey.conversation(
            machineID: machineID ?? currentMachineContextID,
            workspaceID: workspaceID,
            conversationID: conversationID
        )
    }

    func requestTarget(for machineID: String?) -> PcrdRequestTarget? {
        let resolved = MachineContextKey.normalizeMachineID(machineID)
        if resolved == MachineContextKey.localMachineID { return .local }
        if resolved == localDaemonMachineID { return .localMachine(resolved) }
        if let machine = machines.first(where: { $0.deviceId == resolved }) {
            return .relay(machine)
        }
        let current = client.currentTarget()
        return current.machineID == resolved ? current : nil
    }

    func isCurrentMachine(_ machineID: String?) -> Bool {
        currentMachineContextID == MachineContextKey.normalizeMachineID(machineID)
    }
}
