import AppKit
import UserNotifications

@MainActor
enum DesktopNotifier {
    enum Outcome: String {
        case delivered, foreground, disabled, notAuthorized, unavailable, failed
        var consumed: Bool { self != .unavailable && self != .failed }
    }

    static func prepare() {
        guard let center = notificationCenter else { return }
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    static func runCompleted(workspaceName: String, runID: String) {
        Task { await deliver(workspaceName: workspaceName, runID: runID) }
    }

    static func suppression(enabled: Bool, frontmostID: String?, appID: String) -> Outcome? {
        if !enabled { return .disabled }
        if frontmostID == appID { return .foreground }
        return nil
    }

    @discardableResult
    static func deliver(workspaceName: String, runID: String) async -> Outcome {
        guard let center = notificationCenter,
              let appID = Bundle.main.bundleIdentifier else { return .unavailable }
        func suppressionNow() -> Outcome? {
            suppression(enabled: AppAppearanceStore.loadCompletionNotificationsEnabled(),
                        frontmostID: NSWorkspace.shared.frontmostApplication?.bundleIdentifier,
                        appID: appID)
        }
        if let outcome = suppressionNow() { return outcome }
        let settings = await center.notificationSettings()
        // Only the interactive app requests permission, never a background worker.
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else { return .notAuthorized }
        if let outcome = suppressionNow() { return outcome }
        let language = AppLanguage.load()
        let content = UNMutableNotificationContent()
        content.title = language.text("所有 Agent 已完成", "All Agents finished")
        content.body = workspaceName
        content.sound = .default
        content.threadIdentifier = "pcr-completions"
        content.userInfo = ["run_id": runID]
        do {
            try await center.add(UNNotificationRequest(
                identifier: "pcr-run-completed-\(runID)", content: content, trigger: nil
            ))
            return .delivered
        } catch {
            return .failed
        }
    }

    private static var notificationCenter: UNUserNotificationCenter? {
        guard Bundle.main.bundleURL.pathExtension == "app",
              Bundle.main.bundleIdentifier?.isEmpty == false else { return nil }
        return .current()
    }
}
