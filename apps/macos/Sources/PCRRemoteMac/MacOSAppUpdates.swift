import AppKit
import CryptoKit
import Foundation
import SwiftUI

struct MacOSUpdateInfo: Codable, Equatable, Sendable {
    let releaseId: String
    let bundleId: String
    let buildNumber: Int
    let versionName: String
    let releaseNotes: String
    let sha256: String
    let sizeBytes: Int
    let designatedRequirement: String
    let signingAuthority: String
    let publishedAt: String
}

struct MacOSUpdateReadyBanner: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.down.circle").foregroundStyle(.secondary)
            Text(model.macOSUpdateError ?? model.appLanguage.text(
                "新版 \(model.macOSPreparedUpdate?.update.versionName ?? "") 已准备好",
                "Version \(model.macOSPreparedUpdate?.update.versionName ?? "") is ready"
            ))
            .lineLimit(2)
            Spacer(minLength: 8)
            Button {
                Task { await model.restartToInstallMacOSUpdate() }
            } label: {
                Label(model.appLanguage.text("重启以更新", "Restart to Update"), systemImage: "arrow.clockwise")
            }
            .disabled(model.macOSUpdateRestarting)
            .accessibilityIdentifier("update.restart-banner")
            Button { model.macOSUpdateReminderDismissed = true } label: { Image(systemName: "xmark") }
                .buttonStyle(.plain)
                .help(model.appLanguage.text("稍后", "Later"))
                .accessibilityLabel(model.appLanguage.text("稍后更新", "Update Later"))
        }
        .appScaledTextStyle(.caption)
        .controlSize(.small)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(ParallelCodexVisualStyle.sidebar)
        .overlay(alignment: .bottom) { Divider() }
        .accessibilityIdentifier("update.ready-banner")
    }
}

private struct MacOSUpdateCheckBody: Encodable, Sendable {
    let macDeviceId: String
    let bundleId: String
    let currentBuildNumber: Int
}

private struct MacOSUpdateCheckResponse: Decodable, Sendable {
    let available: Bool
    let update: MacOSUpdateInfo?
}

private struct MacOSUpdateTicketBody: Encodable, Sendable {
    let macDeviceId: String
    let releaseId: String
}

private struct MacOSUpdateTicketResponse: Decodable, Sendable {
    let release: MacOSUpdateInfo
    let ticket: String
    let expiresAt: String
    let downloadPath: String
}

private struct MacOSUpdateDownloadBody: Encodable, Sendable {
    let ticket: String
    let releaseId: String
    let expectedSha256: String
    let expectedSize: Int
}

private struct MacOSUpdateDownloadResponse: Decodable, Sendable {
    let path: String
    let sha256: String
    let sizeBytes: Int
}

enum MacOSUpdateVerificationError: LocalizedError {
    case invalidArchive
    case integrityFailure
    case invalidBundle
    case incompatibleSignature
    case installationUnavailable
    case verificationCommand(String)

    var errorDescription: String? {
        switch self {
        case .invalidArchive:
            return "The downloaded update archive is invalid."
        case .integrityFailure:
            return "The downloaded update failed integrity verification."
        case .invalidBundle:
            return "The downloaded app does not match this Parallel Codex release."
        case .incompatibleSignature:
            return "The downloaded app was signed by a different publisher."
        case .installationUnavailable:
            return "The app cannot be updated in its current location. Move Parallel Codex to a writable Applications folder, then try again."
        case .verificationCommand(let message):
            return message
        }
    }
}

enum MacOSUpdateVerifier {
    static func prepare(
        archiveURL: URL,
        update: MacOSUpdateInfo,
        currentAppURL: URL,
        updatesRoot: URL = MacOSUpdateInstaller.updatesRoot
    ) async throws -> MacOSPreparedUpdate {
        try await Task.detached(priority: .userInitiated) {
            try validateDestination(currentAppURL)
            try verifyArchiveIntegrity(archiveURL, update: update)
            try FileManager.default.createDirectory(at: updatesRoot, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let temporaryRoot = updatesRoot
                .appendingPathComponent(
                    "parallel-codex-update-\(UUID().uuidString.lowercased())",
                    isDirectory: true
                )
            try FileManager.default.createDirectory(
                at: temporaryRoot,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            var prepared = false
            defer { if !prepared { try? FileManager.default.removeItem(at: temporaryRoot) } }

            try validateArchiveEntries(
                names: run("/usr/bin/zipinfo", ["-1", archiveURL.path]).stdout,
                listing: run("/usr/bin/zipinfo", ["-l", archiveURL.path]).stdout
            )

            try run(
                "/usr/bin/ditto",
                ["-x", "-k", archiveURL.path, temporaryRoot.path]
            )
            let appURL = temporaryRoot.appendingPathComponent(
                "Parallel Codex.app",
                isDirectory: true
            )
            try verifyBundle(appURL, update: update, currentAppURL: currentAppURL)
            let result = MacOSPreparedUpdate(update: update, targetPath: currentAppURL.standardizedFileURL.path,
                                             directoryPath: temporaryRoot.path)
            try result.save(to: updatesRoot)
            prepared = true
            return result
        }.value
    }

    static func validateArchiveEntries(names: String, listing: String) throws {
        let entries = names.split(separator: "\n", omittingEmptySubsequences: true)
        guard !entries.isEmpty, entries.count <= 20_000,
              entries.allSatisfy({ raw in
                  let entry = String(raw)
                  let parts = entry.split(separator: "/", omittingEmptySubsequences: true)
                  return entry.hasPrefix("Parallel Codex.app/") && !entry.contains("\\")
                      && !parts.contains("..") && !parts.contains(".") && !entry.contains("//")
                      && !entry.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
              }),
              !listing.split(separator: "\n").contains(where: {
                  $0.range(of: "^[lbcps][rwxStTs-]{9}\\s", options: .regularExpression) != nil
              }),
              let total = listing.split(separator: "\n").last,
              let range = total.range(of: "[0-9]+ bytes uncompressed", options: .regularExpression),
              let bytes = Int(total[range].split(separator: " ")[0]), bytes > 0, bytes <= 1_073_741_824 else {
            throw MacOSUpdateVerificationError.invalidArchive
        }
    }

    static func validateDestination(_ currentAppURL: URL) throws {
        let parent = currentAppURL.deletingLastPathComponent()
        let swapKey = URLResourceKey(rawValue: kCFURLVolumeSupportsSwapRenamingKey as String)
        let volume = try? parent.resourceValues(forKeys: [swapKey])
        guard currentAppURL.pathExtension == "app",
              !currentAppURL.path.contains("/AppTranslocation/"),
              !currentAppURL.path.hasPrefix("/Volumes/"),
              currentAppURL.resolvingSymlinksInPath().path == currentAppURL.standardizedFileURL.path,
              FileManager.default.isWritableFile(atPath: parent.path),
              volume?.allValues[swapKey] as? Bool == true else {
            throw MacOSUpdateVerificationError.installationUnavailable
        }
    }

    static func verifyBundle(_ appURL: URL, update: MacOSUpdateInfo, currentAppURL: URL) throws {
        let values = try appURL.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else {
            throw MacOSUpdateVerificationError.invalidBundle
        }
        let plist = try bundleMetadata(appURL)
        guard plist["CFBundleIdentifier"] as? String == update.bundleId,
              Int(plist["CFBundleVersion"] as? String ?? "") == update.buildNumber,
              plist["CFBundleShortVersionString"] as? String == update.versionName else {
            throw MacOSUpdateVerificationError.invalidBundle
        }
        let currentPlist = try bundleMetadata(currentAppURL)
        guard currentPlist["CFBundleIdentifier"] as? String == update.bundleId,
              let build = Int(currentPlist["CFBundleVersion"] as? String ?? ""),
              update.buildNumber > build else { throw MacOSUpdateVerificationError.invalidBundle }
        for bundle in [currentAppURL, appURL] {
            try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", "--verbose=2", bundle.path])
        }
        let requirement = try signingRequirement(appURL)
        let authority = try signingAuthority(appURL)
        let currentRequirement = try signingRequirement(currentAppURL)
        let currentAuthority = try signingAuthority(currentAppURL)
        guard requirement == update.designatedRequirement,
              requirement == currentRequirement,
              authority == update.signingAuthority,
              authority == currentAuthority else {
            throw MacOSUpdateVerificationError.incompatibleSignature
        }
    }

    static func bundleMetadata(_ appURL: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: appURL.appendingPathComponent("Contents/Info.plist"))
        guard let value = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any]
        else { throw MacOSUpdateVerificationError.invalidBundle }
        return value
    }

    static func verifyArchiveIntegrity(
        _ archiveURL: URL,
        update: MacOSUpdateInfo
    ) throws {
        let values = try archiveURL.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
        )
        guard values.isRegularFile == true,
              values.isSymbolicLink != true,
              values.fileSize == update.sizeBytes else {
            throw MacOSUpdateVerificationError.integrityFailure
        }
        let handle = try FileHandle(forReadingFrom: archiveURL)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty {
            hasher.update(data: data)
        }
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == update.sha256.lowercased() else {
            throw MacOSUpdateVerificationError.integrityFailure
        }
    }

    @discardableResult
    static func run(
        _ executable: String,
        _ arguments: [String]
    ) throws -> (stdout: String, stderr: String) {
        let process = Process()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let stdoutURL = directory.appendingPathComponent("stdout")
        let stderrURL = directory.appendingPathComponent("stderr")
        FileManager.default.createFile(atPath: stdoutURL.path, contents: nil)
        FileManager.default.createFile(atPath: stderrURL.path, contents: nil)
        let output = try FileHandle(forWritingTo: stdoutURL)
        let error = try FileHandle(forWritingTo: stderrURL)
        defer { try? output.close(); try? error.close() }
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        let stdout = String(
            data: try Data(contentsOf: stdoutURL),
            encoding: .utf8
        ) ?? ""
        let stderr = String(
            data: try Data(contentsOf: stderrURL),
            encoding: .utf8
        ) ?? ""
        guard process.terminationStatus == 0 else {
            FileHandle.standardError.write(Data(stderr.utf8))
            throw MacOSUpdateVerificationError.verificationCommand("The downloaded update could not be verified. The current app has not been changed.")
        }
        return (stdout, stderr)
    }

    static func signingRequirement(_ appURL: URL) throws -> String {
        let result = try run("/usr/bin/codesign", ["-d", "-r-", appURL.path])
        let output = result.stdout + "\n" + result.stderr
        guard let line = output.split(separator: "\n").first(where: {
            $0.hasPrefix("designated => ")
        }) else {
            throw MacOSUpdateVerificationError.invalidBundle
        }
        return String(line.dropFirst("designated => ".count))
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func signingAuthority(_ appURL: URL) throws -> String {
        let result = try run(
            "/usr/bin/codesign",
            ["-dv", "--verbose=4", appURL.path]
        )
        let output = result.stdout + "\n" + result.stderr
        guard let line = output.split(separator: "\n").first(where: {
            $0.hasPrefix("Authority=")
        }) else {
            throw MacOSUpdateVerificationError.invalidBundle
        }
        return String(line.dropFirst("Authority=".count))
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

@MainActor
extension AppModel {
    var currentMacOSVersionLabel: String {
        let version = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "-"
        let build = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleVersion"
        ) as? String ?? "-"
        return "\(version) (\(build))"
    }

    func checkForMacOSUpdate(silent: Bool = false) async {
        guard !macOSUpdateChecking, !macOSUpdateDownloading else { return }
        macOSUpdateChecking = true
        if !silent { macOSUpdateError = nil }
        defer { macOSUpdateChecking = false }
        do {
            if selectedMachineID == nil {
                await refresh(
                    reportErrors: !silent,
                    forceMachineDirectoryRefresh: true
                )
            }
            guard let machineID = selectedMachineID, !machineID.isEmpty else {
                throw PcrdClientError.connection(
                    "Connect this app to a computer before checking for updates."
                )
            }
            guard let bundleID = Bundle.main.bundleIdentifier,
                  let buildText = Bundle.main.object(
                    forInfoDictionaryKey: "CFBundleVersion"
                  ) as? String,
                  let buildNumber = Int(buildText) else {
                throw MacOSUpdateVerificationError.invalidBundle
            }
            let response: MacOSUpdateCheckResponse = try await client.relayRequest(
                method: "POST",
                path: "/v1/app-updates/macos/check",
                body: MacOSUpdateCheckBody(
                    macDeviceId: machineID,
                    bundleId: bundleID,
                    currentBuildNumber: buildNumber
                )
            )
            let nextUpdate = response.available ? response.update : nil
            if let prepared = macOSPreparedUpdate {
                macOSUpdateAvailable = prepared.update
            } else {
                macOSUpdateAvailable = nextUpdate
            }
            macOSUpdateChecked = true
            macOSUpdateError = nil
        } catch {
            if !silent {
                macOSUpdateError = appLanguage.userMessage(for: error)
                macOSUpdateChecked = true
            }
        }
        if macOSUpdateAvailable != nil, macOSPreparedUpdate == nil, macOSUpdateError == nil {
            macOSUpdateChecking = false
            await downloadMacOSUpdate()
        }
    }

    func downloadMacOSUpdate() async {
        guard let update = macOSUpdateAvailable,
              !macOSUpdateDownloading,
              !macOSUpdateChecking else { return }
        guard let machineID = selectedMachineID, !machineID.isEmpty else {
            macOSUpdateError = appLanguage.text(
                "请先连接一台设备。",
                "Connect a computer first."
            )
            return
        }
        macOSUpdateDownloading = true
        macOSUpdateError = nil
        defer { macOSUpdateDownloading = false }
        do {
            let ticket: MacOSUpdateTicketResponse = try await client.relayRequest(
                method: "POST",
                path: "/v1/app-updates/macos/tickets",
                body: MacOSUpdateTicketBody(
                    macDeviceId: machineID,
                    releaseId: update.releaseId
                )
            )
            guard ticket.downloadPath == "/v1/app-updates/macos/download",
                  ticket.release == update else {
                throw MacOSUpdateVerificationError.invalidArchive
            }
            let downloaded: MacOSUpdateDownloadResponse = try await client.callLocal(
                "controller.download_macos_update",
                body: MacOSUpdateDownloadBody(
                    ticket: ticket.ticket,
                    releaseId: update.releaseId,
                    expectedSha256: update.sha256,
                    expectedSize: update.sizeBytes
                ),
                timeoutSeconds: 5 * 60
            )
            guard downloaded.sha256.lowercased() == update.sha256.lowercased(),
                  downloaded.sizeBytes == update.sizeBytes else {
                throw MacOSUpdateVerificationError.integrityFailure
            }
            let prepared = try await MacOSUpdateVerifier.prepare(
                archiveURL: URL(fileURLWithPath: downloaded.path),
                update: update,
                currentAppURL: Bundle.main.bundleURL
            )
            macOSPreparedUpdate = prepared
            macOSUpdateReminderDismissed = false
        } catch {
            macOSUpdateError = appLanguage.userMessage(for: error)
        }
    }

    func restartToInstallMacOSUpdate() async {
        guard let prepared = macOSPreparedUpdate, !macOSUpdateRestarting else { return }
        macOSUpdateRestarting = true
        macOSUpdateError = nil
        do {
            guard let delegate = applicationDelegate else {
                throw MacOSUpdateVerificationError.invalidBundle
            }
            guard !delegate.updateRestartInProgress else {
                macOSUpdateRestarting = false
                return
            }
            delegate.updateRestartInProgress = true
            defer { delegate.updateRestartInProgress = false }
            let requestURL = try await MacOSUpdateInstaller.start(prepared)
            NSApp.terminate(nil)
            if !delegate.isQuitting {
                MacOSUpdateInstaller.cancel(requestURL)
                throw MacOSUpdateVerificationError.verificationCommand("The update restart was cancelled. The current app has not been changed.")
            }
        } catch {
            macOSUpdateError = appLanguage.userMessage(for: error)
            macOSUpdateRestarting = false
        }
    }
}
