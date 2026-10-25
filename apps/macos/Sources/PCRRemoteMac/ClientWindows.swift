import AppKit
import SwiftUI

@MainActor
enum ClientWindowCatalog {
    private static let key = "parallel-codex.open-window-ids.v1"
    static var openWindowIDs: [UUID] {
        (UserDefaults.standard.stringArray(forKey: key) ?? []).compactMap(UUID.init(uuidString:))
    }
    static func opened(_ id: UUID) {
        let ids = Array(Set(openWindowIDs + [id])).sorted { $0.uuidString < $1.uuidString }
        UserDefaults.standard.set(ids.map(\.uuidString), forKey: key)
    }
    static func closed(_ id: UUID) {
        UserDefaults.standard.set(openWindowIDs.filter { $0 != id }.map(\.uuidString), forKey: key)
    }
}

struct ClientWindowContent: View {
    @StateObject private var model: AppModel
    let windowID: UUID
    let delegate: ParallelCodexAppDelegate

    init(windowID: UUID, delegate: ParallelCodexAppDelegate) {
        self.windowID = windowID
        self.delegate = delegate
        _model = StateObject(wrappedValue: AppModel(windowID: windowID))
    }

    var body: some View {
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
            .background(ClientWindowRegistration(model: model, delegate: delegate, windowID: windowID)
                .allowsHitTesting(false).accessibilityHidden(true))
    }
}

struct ClientWindowRegistration: NSViewRepresentable {
    let model: AppModel
    let delegate: ParallelCodexAppDelegate
    let windowID: UUID?

    func makeNSView(context: Context) -> ClientWindowRegistrationView {
        let view = ClientWindowRegistrationView()
        view.model = model
        view.delegate = delegate
        view.windowID = windowID
        return view
    }
    func updateNSView(_ view: ClientWindowRegistrationView, context: Context) {}
}

final class ClientWindowRegistrationView: NSView {
    weak var model: AppModel?
    weak var delegate: ParallelCodexAppDelegate?
    var windowID: UUID?
    private weak var registeredWindow: NSWindow?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window, registeredWindow !== window, let model, let delegate else { return }
        NotificationCenter.default.removeObserver(self)
        registeredWindow = window
        model.applicationDelegate = delegate
        delegate.windowModels.setObject(model, forKey: window)
        if window.isKeyWindow || delegate.model == nil { delegate.model = model }
        if !AppLaunchMode.isUISmokeTest { model.questionAnswers.start(client: model.client) }
        if let windowID { ClientWindowCatalog.opened(windowID) }
        NotificationCenter.default.addObserver(self, selector: #selector(becameKey),
            name: NSWindow.didBecomeKeyNotification, object: window)
        NotificationCenter.default.addObserver(self, selector: #selector(willClose),
            name: NSWindow.willCloseNotification, object: window)
    }

    @objc private func becameKey() { delegate?.model = model }
    @objc private func willClose() {
        if let answers = model?.questionAnswers { Task { await answers.flush() } }
        model?.flushPromptDraftPersistence()
        model?.cacheVisibleRunViewState()
        model?.runViewStateStore.flushPendingWrites()
        model?.desktopStateStore.flushPendingWrites()
        model?.stopWindowObservation()
        if let windowID, delegate?.isQuitting != true,
           model?.promptOutboxByWorkspace.isEmpty != false, model?.feedback.pending.isEmpty != false {
            ClientWindowCatalog.closed(windowID)
        }
        if let registeredWindow { delegate?.windowModels.removeObject(forKey: registeredWindow) }
    }
    deinit { NotificationCenter.default.removeObserver(self) }
}

struct ClientWindowDisplayCommands: Commands {
    @FocusedObject private var model: AppModel?
    @FocusedBinding(\.sidebarVisibility) private var sidebarVisible: Bool?
    var body: some Commands {
        CommandGroup(replacing: .sidebar) {
            Button(model?.appLanguage.text(
                sidebarVisible == true ? "隐藏边栏" : "显示边栏",
                sidebarVisible == true ? "Hide Sidebar" : "Show Sidebar"
            ) ?? "Show Sidebar") {
                sidebarVisible?.toggle()
            }
            .keyboardShortcut("s", modifiers: [.control, .command])
            .disabled(sidebarVisible == nil)
            Divider()
            Button(model?.appLanguage.text("放大显示", "Zoom In") ?? "Zoom In") {
                if let model, let size = model.appTextSize.larger { model.setAppTextSize(size) }
            }.keyboardShortcut("+", modifiers: .command).disabled(model?.appTextSize.larger == nil)
            Button(model?.appLanguage.text("缩小显示", "Zoom Out") ?? "Zoom Out") {
                if let model, let size = model.appTextSize.smaller { model.setAppTextSize(size) }
            }.keyboardShortcut("-", modifiers: .command).disabled(model?.appTextSize.smaller == nil)
            Button(model?.appLanguage.text("实际大小", "Actual Size") ?? "Actual Size") {
                model?.setAppTextSize(.standard)
            }.keyboardShortcut("0", modifiers: .command).disabled(model == nil || model?.appTextSize == .standard)
        }
    }
}
