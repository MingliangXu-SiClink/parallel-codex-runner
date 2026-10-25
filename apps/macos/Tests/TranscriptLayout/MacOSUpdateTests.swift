import AppKit
import CryptoKit
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct MacOSUpdateTests {
    @Test @MainActor func backgroundInstallationPreservesFocusIncludingItsRollbackRelaunch() throws {
        let prepared = MacOSPreparedUpdate(update: info(), targetPath: "/Applications/Parallel Codex.app",
                                           directoryPath: "/unused/prepared")
        let legacy = MacOSInstallRequest(prepared: prepared, parentProcessID: 1, id: UUID())
        var object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(legacy)) as? [String: Any])
        object.removeValue(forKey: "activateOnRelaunch")
        let decoded = try JSONDecoder().decode(MacOSInstallRequest.self,
                                               from: JSONSerialization.data(withJSONObject: object))
        #expect(decoded.activatesOnRelaunch)
        var background = decoded
        background.activateOnRelaunch = false
        let restored = try JSONDecoder().decode(MacOSInstallRequest.self, from: JSONEncoder().encode(background))
        #expect(!restored.activatesOnRelaunch)
        let configuration = MacOSUpdateInstaller.relaunchConfiguration(activates: restored.activatesOnRelaunch)
        #expect(!configuration.activates)
        #expect(configuration.createsNewApplicationInstance)
        #expect(MacOSUpdateInstaller.relaunchConfiguration(activates: decoded.activatesOnRelaunch).activates)
    }

    @Test @MainActor func updateUsesTheRegisteredDelegateRatherThanSwiftUIsApplicationWrapper() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let client = PcrdClient(socketURL: root.appendingPathComponent("absent.sock"),
                                tokenURL: root.appendingPathComponent("absent.token"))
        let answers = AgentAnswerOutbox(file: AgentAnswerFile(url: root.appendingPathComponent("answers.json")))
        defer { answers.stop() }
        let model = AppModel(client: client, windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: root), questionAnswers: answers)
        let delegate = ParallelCodexAppDelegate()
        let registration = ClientWindowRegistrationView()
        registration.model = model
        registration.delegate = delegate
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = registration
        #expect(model.applicationDelegate === delegate)
        #expect(delegate.applicationShouldTerminate(NSApplication.shared) == .terminateNow)
        window.contentView = nil
    }

    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
            .appendingPathComponent("pcr-update-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func fakeApp(at url: URL, build: String, bundleID: String = "com.parallelcodexrunner.mac") throws {
        try FileManager.default.createDirectory(at: url.appendingPathComponent("Contents"), withIntermediateDirectories: true)
        let data = try PropertyListSerialization.data(fromPropertyList: ["CFBundleIdentifier": bundleID,
            "CFBundleVersion": build, "CFBundleShortVersionString": "0.1.\(build)"], format: .xml, options: 0)
        try data.write(to: url.appendingPathComponent("Contents/Info.plist"))
    }

    private func info(data: Data = Data("archive".utf8), build: Int = 401) -> MacOSUpdateInfo {
        MacOSUpdateInfo(releaseId: "test-release", bundleId: "com.parallelcodexrunner.mac", buildNumber: build,
            versionName: "0.1.\(build)", releaseNotes: "", sha256: SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(),
            sizeBytes: data.count, designatedRequirement: "test-requirement", signingAuthority: "test-publisher", publishedAt: "")
    }

    @Test func archiveVerificationRejectsChangedBytesSizeAndSymlinks() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let archive = root.appendingPathComponent("update.zip")
        let data = Data("archive".utf8)
        try data.write(to: archive)
        try MacOSUpdateVerifier.verifyArchiveIntegrity(archive, update: info(data: data))
        try Data("changed".utf8).write(to: archive)
        #expect(throws: MacOSUpdateVerificationError.self) {
            try MacOSUpdateVerifier.verifyArchiveIntegrity(archive, update: info(data: data))
        }
        try Data("different-size".utf8).write(to: archive)
        #expect(throws: MacOSUpdateVerificationError.self) {
            try MacOSUpdateVerifier.verifyArchiveIntegrity(archive, update: info(data: data))
        }
        let link = root.appendingPathComponent("linked.zip")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: archive)
        #expect(throws: MacOSUpdateVerificationError.self) {
            try MacOSUpdateVerifier.verifyArchiveIntegrity(link, update: info(data: Data("different-size".utf8)))
        }
    }

    @Test func atomicReplacementPreservesTheOldBundleAsBackup() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let target = root.appendingPathComponent("Parallel Codex.app")
        let staged = root.appendingPathComponent("new.app")
        try fakeApp(at: target, build: "400")
        try fakeApp(at: staged, build: "401")
        try MacOSUpdateVerifier.validateDestination(target)
        try MacOSUpdateInstaller.replaceBundle(target: target, staged: staged) { installed in
            let metadata = try MacOSUpdateVerifier.bundleMetadata(installed)
            #expect(metadata["CFBundleVersion"] as? String == "401")
        }
        #expect(try MacOSUpdateVerifier.bundleMetadata(target)["CFBundleVersion"] as? String == "401")
        #expect(try MacOSUpdateVerifier.bundleMetadata(staged)["CFBundleVersion"] as? String == "400")
    }

    @Test func archiveEntriesRejectTraversalLinksAndExcessiveUncompressedSize() throws {
        let safe = "Parallel Codex.app/\nParallel Codex.app/Contents/Info.plist\n"
        let listing = "-rw-r--r--  2.0 unx 1000 bx defN file\n2 files, 1000 bytes uncompressed, 500 bytes compressed: 50.0%\n"
        try MacOSUpdateVerifier.validateArchiveEntries(names: safe, listing: listing)
        for names in ["/Parallel Codex.app/Contents/file\n", "Parallel Codex.app/../escaped\n",
                      "Parallel Codex.app/./file\n", "Parallel Codex.app/Contents\\file\n", "another.app/file\n"] {
            #expect(throws: MacOSUpdateVerificationError.self) {
                try MacOSUpdateVerifier.validateArchiveEntries(names: names, listing: listing)
            }
        }
        for listing in ["lrwxr-xr-x 2.0 unx 100 bx stor link\n2 files, 1000 bytes uncompressed\n",
                        "lrwsr-xr-x 2.0 unx 100 bx stor link\n2 files, 1000 bytes uncompressed\n",
                        "2 files, 1073741825 bytes uncompressed, 500 bytes compressed\n", "invalid"] {
            #expect(throws: MacOSUpdateVerificationError.self) {
                try MacOSUpdateVerifier.validateArchiveEntries(names: safe, listing: listing)
            }
        }
    }

    @Test func verificationFailureRestoresTheOriginalBundle() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let target = root.appendingPathComponent("Parallel Codex.app")
        let staged = root.appendingPathComponent("new.app")
        try fakeApp(at: target, build: "400")
        try fakeApp(at: staged, build: "401")
        #expect(throws: MacOSUpdateInstallationError.self) {
            try MacOSUpdateInstaller.replaceBundle(target: target, staged: staged) { _ in
                throw MacOSUpdateVerificationError.incompatibleSignature
            }
        }
        #expect(try MacOSUpdateVerifier.bundleMetadata(target)["CFBundleVersion"] as? String == "400")
        #expect(try MacOSUpdateVerifier.bundleMetadata(staged)["CFBundleVersion"] as? String == "401")
    }

    @Test func replacementRejectsSymlinksAndNonAdjacentStaging() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let target = root.appendingPathComponent("Parallel Codex.app")
        let staged = root.appendingPathComponent("nested/new.app")
        try fakeApp(at: target, build: "400")
        try fakeApp(at: staged, build: "401")
        #expect(throws: MacOSUpdateVerificationError.self) {
            try MacOSUpdateInstaller.replaceBundle(target: target, staged: staged) { _ in }
        }
        let link = root.appendingPathComponent("linked.app")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        #expect(throws: MacOSUpdateVerificationError.self) {
            try MacOSUpdateVerifier.validateDestination(link)
        }
        #expect(try MacOSUpdateVerifier.bundleMetadata(target)["CFBundleVersion"] as? String == "400")
    }

    @Test func stagedUpdatesSurviveRestartButNeverOfferDowngradesOrAnotherApp() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let target = root.appendingPathComponent("Parallel Codex.app")
        let stage = root.appendingPathComponent("prepared", isDirectory: true)
        try fakeApp(at: target, build: "400")
        try fakeApp(at: stage.appendingPathComponent("Parallel Codex.app"), build: "401")
        let prepared = MacOSPreparedUpdate(update: info(), targetPath: target.path, directoryPath: stage.path)
        try prepared.save(to: root)
        #expect(MacOSPreparedUpdate.restore(root: root, currentAppURL: target) == prepared)
        try fakeApp(at: target, build: "401")
        #expect(MacOSPreparedUpdate.restore(root: root, currentAppURL: target) == nil)
        try fakeApp(at: target, build: "400", bundleID: "another.application")
        #expect(MacOSPreparedUpdate.restore(root: root, currentAppURL: target) == nil)
        let outside = MacOSPreparedUpdate(update: info(), targetPath: target.path,
                                         directoryPath: root.deletingLastPathComponent().path)
        try outside.save(to: root)
        #expect(MacOSPreparedUpdate.restore(root: root, currentAppURL: target) == nil)
    }
}
