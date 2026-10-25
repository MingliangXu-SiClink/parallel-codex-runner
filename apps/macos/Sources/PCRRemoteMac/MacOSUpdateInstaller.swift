import AppKit
import Darwin
import Foundation

struct MacOSPreparedUpdate: Codable, Equatable, Sendable {
    let update: MacOSUpdateInfo
    let targetPath: String
    let directoryPath: String

    var directoryURL: URL { URL(fileURLWithPath: directoryPath, isDirectory: true) }
    var stagedAppURL: URL { directoryURL.appendingPathComponent("Parallel Codex.app", isDirectory: true) }

    func save(to root: URL) throws {
        let data = try JSONEncoder().encode(self)
        try data.write(to: root.appendingPathComponent("prepared.json"), options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                             ofItemAtPath: root.appendingPathComponent("prepared.json").path)
    }

    static func restore(root: URL = MacOSUpdateInstaller.updatesRoot,
                        currentAppURL: URL = Bundle.main.bundleURL) -> MacOSPreparedUpdate? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("prepared.json")),
              let value = try? JSONDecoder().decode(Self.self, from: data),
              value.targetPath == currentAppURL.standardizedFileURL.path,
              value.directoryURL.deletingLastPathComponent().standardizedFileURL == root.standardizedFileURL,
              let current = try? MacOSUpdateVerifier.bundleMetadata(currentAppURL),
              current["CFBundleIdentifier"] as? String == value.update.bundleId,
              let build = Int(current["CFBundleVersion"] as? String ?? ""),
              value.update.buildNumber > build,
              FileManager.default.fileExists(atPath: value.stagedAppURL.path) else { return nil }
        return value
    }
}

struct MacOSInstallRequest: Codable, Sendable {
    let prepared: MacOSPreparedUpdate
    let parentProcessID: Int32
    let id: UUID
    var activateOnRelaunch: Bool? = nil

    var activatesOnRelaunch: Bool { activateOnRelaunch ?? true }
}

private struct MacOSInstallResult: Codable {
    let ready: Bool
    let installed: Bool
    let rolledBack: Bool
    let message: String?
}

private struct MacOSInstallJournal: Codable {
    let targetPath: String
    let backupPath: String
    let newBuild: Int
    var state: String

    func save(to url: URL) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

enum MacOSUpdateInstallationError: LocalizedError {
    case restored(String)

    var errorDescription: String? {
        switch self {
        case .restored(let message): return "The update failed and the previous app was restored. " + message
        }
    }
}

enum MacOSUpdateInstaller {
    static var updatesRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Parallel Codex/Updates", isDirectory: true)
            .resolvingSymlinksInPath()
    }

    @MainActor
    static func start(_ prepared: MacOSPreparedUpdate) async throws -> URL {
        let currentAppURL = Bundle.main.bundleURL
        let executableURL = Bundle.main.executableURL
        let pid = ProcessInfo.processInfo.processIdentifier
        let requestURL = prepared.directoryURL.appendingPathComponent("install-\(UUID().uuidString).json")
        let helperAppURL = prepared.directoryURL.appendingPathComponent("update-helper-\(UUID().uuidString).app", isDirectory: true)
        let readyURL = requestURL.appendingPathExtension("ready")
        let cancelURL = requestURL.appendingPathExtension("cancel")
        let request = MacOSInstallRequest(prepared: prepared, parentProcessID: pid, id: UUID())
        try await Task.detached(priority: .userInitiated) {
            guard prepared.targetPath == currentAppURL.standardizedFileURL.path, let executableURL else {
                throw MacOSUpdateVerificationError.invalidBundle
            }
            try MacOSUpdateVerifier.validateDestination(currentAppURL)
            try MacOSUpdateVerifier.verifyBundle(prepared.stagedAppURL, update: prepared.update, currentAppURL: currentAppURL)
            // A bundle's executable signature includes its Info.plist slot.
            // Preserve the signed bundle instead of stripping out its Mach-O.
            try FileManager.default.copyItem(at: currentAppURL, to: helperAppURL)
            try MacOSUpdateVerifier.run("/usr/bin/codesign", ["--verify", "--deep", "--strict", helperAppURL.path])
            let helperURL = helperAppURL.appendingPathComponent("Contents/MacOS")
                .appendingPathComponent(executableURL.lastPathComponent)
            try JSONEncoder().encode(request).write(to: requestURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: requestURL.path)
            let logURL = prepared.directoryURL.appendingPathComponent("installer.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            let log = try FileHandle(forWritingTo: logURL)
            defer { try? log.close() }
            let process = Process()
            process.executableURL = helperURL
            process.arguments = ["--pcr-install-update", requestURL.path]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = log
            process.standardError = log
            try process.run()
        }.value
        do {
            for _ in 0..<250 {
                try Task.checkCancellation()
                if let data = try? Data(contentsOf: readyURL),
                   let result = try? JSONDecoder().decode(MacOSInstallResult.self, from: data) {
                    guard result.ready else {
                        throw MacOSUpdateVerificationError.verificationCommand(result.message ?? "The update could not be prepared for restart.")
                    }
                    return requestURL
                }
                try await Task.sleep(for: .milliseconds(20))
            }
            throw MacOSUpdateVerificationError.verificationCommand("The update could not be prepared for restart. The current app has not been changed.")
        } catch {
            try? Data().write(to: cancelURL, options: .atomic)
            throw error
        }
    }

    static func cancel(_ requestURL: URL) {
        try? Data().write(to: requestURL.appendingPathExtension("cancel"), options: .atomic)
    }

    // The helper owns replacement only after the GUI exits. It never sends an
    // IPC command to the scheduler, accepts a result, or terminates an Agent.
    @MainActor
    static func run(requestURL: URL) async -> Int32 {
        let readyURL = requestURL.appendingPathExtension("ready")
        let resultURL = updatesRoot.appendingPathComponent("last-install-result.json")
        var backupURL: URL?
        var targetURL: URL?
        var swapped = false
        var parentExited = false
        var activatesOnRelaunch = true
        var lockDescriptor: Int32 = -1
        var journal: MacOSInstallJournal?
        let journalURL = updatesRoot.appendingPathComponent("install-journal.json")
        defer {
            if lockDescriptor >= 0 {
                flock(lockDescriptor, LOCK_UN)
                close(lockDescriptor)
            }
        }
        do {
            let request = try JSONDecoder().decode(MacOSInstallRequest.self, from: Data(contentsOf: requestURL))
            activatesOnRelaunch = request.activatesOnRelaunch
            let prepared = request.prepared
            let target = URL(fileURLWithPath: prepared.targetPath, isDirectory: true)
            targetURL = target
            guard prepared.directoryURL.deletingLastPathComponent().standardizedFileURL == updatesRoot.standardizedFileURL,
                  requestURL.deletingLastPathComponent().standardizedFileURL == prepared.directoryURL.standardizedFileURL,
                  prepared.directoryURL.resolvingSymlinksInPath() == prepared.directoryURL.standardizedFileURL,
                  let parent = NSRunningApplication(processIdentifier: request.parentProcessID),
                  parent.bundleURL?.standardizedFileURL == target.standardizedFileURL else {
                throw MacOSUpdateVerificationError.invalidBundle
            }
            lockDescriptor = open(updatesRoot.appendingPathComponent("installer.lock").path,
                                  O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
            guard lockDescriptor >= 0, flock(lockDescriptor, LOCK_EX | LOCK_NB) == 0 else {
                throw MacOSUpdateVerificationError.verificationCommand("Another update is being installed. The current app has not been changed.")
            }
            try MacOSUpdateVerifier.validateDestination(target)
            try MacOSUpdateVerifier.verifyBundle(prepared.stagedAppURL, update: prepared.update, currentAppURL: target)
            let otherInstances = NSWorkspace.shared.runningApplications.filter {
                $0.processIdentifier != request.parentProcessID && $0.processIdentifier != ProcessInfo.processInfo.processIdentifier
                    && $0.bundleURL?.standardizedFileURL == target.standardizedFileURL
            }
            guard otherInstances.isEmpty else {
                throw MacOSUpdateVerificationError.verificationCommand("Another copy of Parallel Codex is running. Quit that copy, then retry the update.")
            }
            let neighbor = target.deletingLastPathComponent()
                .appendingPathComponent(".ParallelCodex-update-\(request.id.uuidString).app", isDirectory: true)
            journal = MacOSInstallJournal(targetPath: target.path, backupPath: neighbor.path,
                                           newBuild: prepared.update.buildNumber, state: "prepared")
            try journal?.save(to: journalURL)
            try writeResult(MacOSInstallResult(ready: true, installed: false, rolledBack: false, message: nil), to: readyURL)
            for _ in 0..<600 {
                if FileManager.default.fileExists(atPath: requestURL.appendingPathExtension("cancel").path) {
                    throw MacOSUpdateVerificationError.verificationCommand("The update restart was cancelled. The current app has not been changed.")
                }
                if parent.isTerminated { break }
                try await Task.sleep(for: .milliseconds(200))
            }
            guard parent.isTerminated else {
                throw MacOSUpdateVerificationError.verificationCommand("Parallel Codex did not quit. The current app has not been changed.")
            }
            parentExited = true
            guard NSWorkspace.shared.runningApplications.allSatisfy({
                $0.bundleURL?.standardizedFileURL != target.standardizedFileURL
            }) else {
                throw MacOSUpdateVerificationError.verificationCommand("Another copy of Parallel Codex is running. Quit that copy, then retry the update.")
            }
            try MacOSUpdateVerifier.run("/usr/bin/ditto", [prepared.stagedAppURL.path, neighbor.path])
            backupURL = neighbor
            try MacOSUpdateVerifier.verifyBundle(neighbor, update: prepared.update, currentAppURL: target)
            guard NSWorkspace.shared.runningApplications.allSatisfy({
                $0.bundleURL?.standardizedFileURL != target.standardizedFileURL
            }) else {
                throw MacOSUpdateVerificationError.verificationCommand("Another copy of Parallel Codex is running. Quit that copy, then retry the update.")
            }
            try replaceBundle(target: target, staged: neighbor) { installed in
                // After the swap the previous app is at neighbor, so it remains
                // the independent trust anchor for installed-bundle validation.
                try MacOSUpdateVerifier.verifyBundle(installed, update: prepared.update, currentAppURL: neighbor)
            }
            swapped = true
            journal?.state = "swapped"
            try journal?.save(to: journalURL)
            _ = try await reopen(target, activates: activatesOnRelaunch)
            swapped = false
            journal?.state = "launched"
            try? journal?.save(to: journalURL)
            let backup = prepared.directoryURL.appendingPathComponent("previous.app", isDirectory: true)
            try? FileManager.default.moveItem(at: neighbor, to: backup)
            try? writeResult(MacOSInstallResult(ready: true, installed: true, rolledBack: false, message: nil), to: resultURL)
            // Do not auto-roll back later runtime crashes: application data may
            // have legitimately changed after successful launch.
            return EXIT_SUCCESS
        } catch {
            var rolledBack = false
            if case MacOSUpdateInstallationError.restored = error { rolledBack = true }
            if swapped, let targetURL, let backupURL {
                rolledBack = swap(targetURL, backupURL) == 0
            }
            if parentExited, (!swapped || rolledBack), let targetURL {
                _ = try? await reopen(targetURL, activates: activatesOnRelaunch)
            }
            journal?.state = rolledBack ? "rolledBack" : "failed"
            try? journal?.save(to: journalURL)
            let result = MacOSInstallResult(ready: false, installed: false, rolledBack: rolledBack,
                                           message: error.localizedDescription)
            try? writeResult(result, to: readyURL)
            try? writeResult(result, to: resultURL)
            return EXIT_FAILURE
        }
    }

    @MainActor
    static func relaunchConfiguration(activates: Bool) -> NSWorkspace.OpenConfiguration {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = activates
        configuration.createsNewApplicationInstance = true
        if let privateHome = ProcessInfo.processInfo.environment["CFFIXED_USER_HOME"] {
            configuration.environment = ["HOME": privateHome, "CFFIXED_USER_HOME": privateHome]
        }
        return configuration
    }

    @MainActor
    private static func reopen(_ target: URL, activates: Bool) async throws {
        let configuration = relaunchConfiguration(activates: activates)
        do {
            _ = try await NSWorkspace.shared.openApplication(at: target, configuration: configuration)
        } catch {
            // LaunchServices can retain the old bundle's executable after an
            // atomic exchange. Launch the verified target directly, preserving
            // the helper's environment, rather than losing a valid update.
            if NSWorkspace.shared.runningApplications.contains(where: {
                !$0.isTerminated && $0.bundleURL?.standardizedFileURL == target.standardizedFileURL
            }) { return }
            guard let executable = Bundle(url: target)?.executableURL,
                  executable.deletingLastPathComponent().standardizedFileURL
                    == target.appendingPathComponent("Contents/MacOS").standardizedFileURL else {
                throw MacOSUpdateVerificationError.invalidBundle
            }
            let process = Process()
            process.executableURL = executable
            process.environment = ProcessInfo.processInfo.environment
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
        }
    }

    static func replaceBundle(target: URL, staged: URL, verify: (URL) throws -> Void) throws {
        guard target.deletingLastPathComponent().standardizedFileURL == staged.deletingLastPathComponent().standardizedFileURL,
              target.standardizedFileURL != staged.standardizedFileURL,
              target.resolvingSymlinksInPath() == target.standardizedFileURL,
              staged.resolvingSymlinksInPath() == staged.standardizedFileURL else {
            throw MacOSUpdateVerificationError.invalidBundle
        }
        guard swap(target, staged) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        do { try verify(target) }
        catch {
            guard swap(target, staged) == 0 else {
                throw MacOSUpdateVerificationError.verificationCommand("The update failed and the previous app could not be restored. The backup has been retained.")
            }
            throw MacOSUpdateInstallationError.restored(error.localizedDescription)
        }
    }

    private static func swap(_ first: URL, _ second: URL) -> Int32 {
        first.withUnsafeFileSystemRepresentation { firstPath in
            second.withUnsafeFileSystemRepresentation { secondPath in
                renamex_np(firstPath!, secondPath!, UInt32(RENAME_SWAP))
            }
        }
    }

    private static func writeResult(_ result: MacOSInstallResult, to url: URL) throws {
        try JSONEncoder().encode(result).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}
