import Foundation

enum MachineContextKey {
    static let localMachineID = "local"

    private static let prefix = "machine-v2"
    private static let separator = "\u{1f}"

    static func normalizeMachineID(_ machineID: String?) -> String {
        let value = machineID?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? localMachineID : value
    }

    static func workspace(machineID: String?, workspaceID: String) -> String {
        join([prefix, normalizeMachineID(machineID), workspaceID])
    }

    static func run(machineID: String?, workspaceID: String, runID: String) -> String {
        join([prefix, normalizeMachineID(machineID), workspaceID, runID])
    }

    static func session(
        machineID: String?,
        workspaceID: String,
        sessionID: String
    ) -> String {
        join([prefix, normalizeMachineID(machineID), workspaceID, sessionID])
    }

    static func conversation(
        machineID: String?,
        workspaceID: String,
        conversationID: String
    ) -> String {
        join([
            prefix,
            normalizeMachineID(machineID),
            workspaceID,
            "conversation",
            conversationID,
        ])
    }

    static func task(machineID: String?, runID: String) -> String {
        join([prefix, normalizeMachineID(machineID), "run", runID])
    }

    static func belongsToWorkspace(
        _ key: String,
        machineID: String?,
        workspaceID: String
    ) -> Bool {
        key == workspace(machineID: machineID, workspaceID: workspaceID)
            || key.hasPrefix(
                workspace(machineID: machineID, workspaceID: workspaceID) + separator
            )
    }

    static func isMachineScoped(_ key: String) -> Bool {
        key.hasPrefix(prefix + separator)
    }

    static func machineID(from key: String) -> String? {
        let parts = key.components(separatedBy: separator)
        guard parts.count >= 3, parts[0] == prefix, !parts[1].isEmpty else { return nil }
        return parts[1]
    }

    static func taskRunID(from key: String) -> String? {
        let parts = key.components(separatedBy: separator)
        guard parts.count == 4,
              parts[0] == prefix,
              parts[2] == "run",
              !parts[3].isEmpty else { return nil }
        return parts[3]
    }

    static func workspaceID(from key: String, machineID: String) -> String? {
        let parts = key.components(separatedBy: separator)
        guard parts.count == 3, parts[0] == prefix,
              parts[1] == normalizeMachineID(machineID), !parts[2].isEmpty else { return nil }
        return parts[2]
    }

    private static func join(_ parts: [String]) -> String {
        parts.joined(separator: separator)
    }
}
