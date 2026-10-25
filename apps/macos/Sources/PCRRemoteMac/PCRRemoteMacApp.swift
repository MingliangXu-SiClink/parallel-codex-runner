import AppKit
import Darwin
import SwiftUI

enum AppLaunchMode {
    static let isUISmokeTest = CommandLine.arguments.contains("--pcr-ui-smoke-test")

    @MainActor
    static func signalUIReady() {
        guard isUISmokeTest,
              let marker = ProcessInfo.processInfo.environment["PCR_MAC_UI_SMOKE_MARKER"],
              !marker.isEmpty else { return }
        try? Data("ready\n".utf8).write(to: URL(fileURLWithPath: marker), options: .atomic)
        DispatchQueue.main.async {
            NSApplication.shared.terminate(nil)
        }
    }
}

@MainActor
final class ParallelCodexAppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    var questionAnswers: AgentAnswerOutbox = .shared
    let windowModels = NSMapTable<NSWindow, AppModel>(keyOptions: .weakMemory, valueOptions: .weakMemory)
    var isQuitting = false
    var updateRestartInProgress = false

    func applicationShouldSaveSecureApplicationState(_ app: NSApplication) -> Bool {
        // AppKit's restoration snapshot can briefly paint an old transcript
        // before the cache-backed conversation model is ready. Navigation,
        // drafts, scroll positions, and the window frame already have explicit
        // stores, so preserving AppKit's duplicate window state only creates a
        // stale first frame.
        false
    }

    func applicationShouldRestoreSecureApplicationState(_ app: NSApplication) -> Bool {
        false
    }

    func applicationShouldTerminate(
        _ sender: NSApplication
    ) -> NSApplication.TerminateReply {
        if !AppLaunchMode.isUISmokeTest {
            let answers = model?.questionAnswers ?? questionAnswers
            if !answers.flushForTermination(), !answers.undurableAnswers.isEmpty {
                return .terminateCancel
            }
        }
        // Closing the client persists its view; only explicit result actions may adopt an Agent.
        flushPersistentState()
        isQuitting = true
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        flushPersistentState()
    }

    private func flushPersistentState() {
        NativeMultilineEditorCommitRegistry.shared.commitPendingText()
        var models = windowModels.objectEnumerator()?.allObjects as? [AppModel] ?? []
        if let model, !models.contains(where: { $0 === model }) { models.append(model) }
        for model in models {
        _ = model.questionAnswers.flushForTermination()
        model.flushPromptDraftPersistence()
        model.cacheVisibleRunViewState()
        // AppKit can emit bounds changes while the window hierarchy is being
        // dismantled. Freeze the last live snapshot before teardown so those
        // temporary positions cannot replace it.
        model.transcriptScrollMemory.freezeForTermination()
        model.runViewStateStore.flushPendingWrites()
        model.desktopStateStore.flushPendingWrites()
        }
        TranscriptLayoutMetricsStore.shared.flush()
    }
}

@main
@MainActor
enum ParallelCodexEntryPoint {
    static func main() async {
        if AppLaunchMode.isUISmokeTest,
           ProcessInfo.processInfo.environment["PCR_INTERACTIVE_QA_AUTHORIZED"] != "1" {
            fputs("Interactive QA is disabled without fresh user approval.\n", stderr)
            exit(78)
        }
        if CommandLine.arguments.dropFirst().first == "--pcr-prepare-update" {
            guard CommandLine.arguments.count == 5 else { exit(64) }
            NSApplication.shared.setActivationPolicy(.prohibited)
            do {
                let update = try JSONDecoder().decode(MacOSUpdateInfo.self,
                    from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])))
                let prepared = try await MacOSUpdateVerifier.prepare(
                    archiveURL: URL(fileURLWithPath: CommandLine.arguments[3]),
                    update: update,
                    currentAppURL: URL(fileURLWithPath: CommandLine.arguments[4], isDirectory: true)
                )
                print(String(decoding: try JSONEncoder().encode(prepared), as: UTF8.self))
                exit(EXIT_SUCCESS)
            } catch {
                fputs("\(error.localizedDescription)\n", stderr)
                exit(EXIT_FAILURE)
            }
        }
        if CommandLine.arguments.dropFirst().first == "--pcr-install-update" {
            guard CommandLine.arguments.count == 3 else { exit(64) }
            NSApplication.shared.setActivationPolicy(.prohibited)
            let code = await MacOSUpdateInstaller.run(requestURL: URL(fileURLWithPath: CommandLine.arguments[2]))
            exit(code)
        }
        if CommandLine.arguments.contains("--pcr-resource-smoke-test") {
            NSApplication.shared.setActivationPolicy(.prohibited)
            let failures = await RenderingResourceCheck.failures()
            for failure in failures { print(failure) }
            if failures.isEmpty { print("Packaged math and syntax rendering passed.") }
            exit(failures.isEmpty ? EXIT_SUCCESS : EXIT_FAILURE)
        }
        if CommandLine.arguments.dropFirst().first == "--pcr-notify-run-completed" {
            guard CommandLine.arguments.count == 4 else { exit(64) }
            // Use the signed app identity without constructing any windows,
            // AppModel, daemon connection, or conversation restoration state.
            NSApplication.shared.setActivationPolicy(.prohibited)
            let outcome = await DesktopNotifier.deliver(
                workspaceName: CommandLine.arguments[3], runID: CommandLine.arguments[2]
            )
            print(outcome.rawValue)
            exit(outcome.consumed ? EXIT_SUCCESS : EXIT_FAILURE)
        }
        ParallelCodexMacApp.main()
    }
}

struct ParallelCodexMacApp: App {
    @NSApplicationDelegateAdaptor(ParallelCodexAppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @Environment(\.openWindow) private var openWindow
    @State private var restoredWindows = false
    private let initialWindowSize = MainWindowFrameStore.initialSize

    init() {
        if CommandLine.arguments.contains("--pcr-smoke-test") {
            exit(EXIT_SUCCESS)
        }
        if AppLaunchMode.isUISmokeTest {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                AppLaunchMode.signalUIReady()
            }
        }
    }

    var body: some Scene {
        Window(AppIdentity.productName, id: "main") {
            ContentView()
                .environmentObject(model)
                .environment(\.locale, model.appLanguage.locale)
                .environment(\.appLanguage, model.appLanguage)
                .environment(\.appAccentColor, model.appAccentColor)
                .environment(\.appTextSize, model.appTextSize)
                .environment(\.dynamicTypeSize, model.appTextSize.dynamicTypeSize)
                .preferredColorScheme(model.appColorMode.colorScheme)
                .tint(model.appAccentColor.color)
                .focusedSceneObject(model)
                .background(ClientWindowRegistration(model: model, delegate: appDelegate, windowID: nil)
                    .allowsHitTesting(false).accessibilityHidden(true))
                .onAppear {
                    guard !restoredWindows, !AppLaunchMode.isUISmokeTest else { return }
                    restoredWindows = true
                    for id in ClientWindowCatalog.openWindowIDs { openWindow(id: "conversation-window", value: id) }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(
            width: initialWindowSize.width,
            height: initialWindowSize.height
        )
        .commands {
            CommandGroup(after: .newItem) {
                Button(model.appLanguage.text("新窗口", "New Window")) {
                    openWindow(id: "conversation-window", value: UUID())
                }.keyboardShortcut("n", modifiers: [.command, .shift])
            }
            ConversationFindCommands(language: model.appLanguage)
            ConversationRunCommands(language: model.appLanguage)
            ClientWindowDisplayCommands()
        }

        WindowGroup(AppIdentity.productName, id: "conversation-window", for: UUID.self) { $id in
            if let id { ClientWindowContent(windowID: id, delegate: appDelegate) }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: initialWindowSize.width, height: initialWindowSize.height)

        Settings {
            SettingsRootView()
            .environmentObject(model)
            .environment(\.locale, model.appLanguage.locale)
            .environment(\.appLanguage, model.appLanguage)
            .environment(\.appAccentColor, model.appAccentColor)
            .environment(\.appTextSize, model.appTextSize)
            .environment(\.dynamicTypeSize, model.appTextSize.dynamicTypeSize)
            .preferredColorScheme(model.appColorMode.colorScheme)
            .tint(model.appAccentColor.color)
        }

        MenuBarExtra(AppIdentity.productName, systemImage: "command.square") {
            Text(AppIdentity.macRole)
                .foregroundStyle(.secondary)
            Label(
                model.daemonOnline
                    ? model.appLanguage.text("服务端已连接", "Server connected")
                    : model.appLanguage.text("服务端不可用", "Server unavailable"),
                systemImage: model.daemonOnline ? "checkmark.circle.fill" : "exclamationmark.circle.fill"
            )
            Divider()
            Button(model.appLanguage.text("刷新", "Refresh")) {
                Task {
                    await model.refresh(forceMachineDirectoryRefresh: true)
                }
            }
            Button(model.appLanguage.text(
                "退出 \(AppIdentity.productName)",
                "Quit \(AppIdentity.productName)"
            )) {
                NSApplication.shared.terminate(nil)
            }
        }
    }
}
