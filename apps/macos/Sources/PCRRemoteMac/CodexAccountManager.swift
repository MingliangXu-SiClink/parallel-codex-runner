import Foundation
import SwiftUI

/// Manages the list of stored PCR Codex accounts and drives add/switch/delete/login
/// operations through the pcrd backend.
@MainActor
final class CodexAccountManager: ObservableObject {

    @Published private(set) var accounts: [PCRAccount] = []
    @Published private(set) var loading = false
    @Published private(set) var loadError: String?
    @Published private(set) var switching = false
    @Published private(set) var adding = false
    @Published private(set) var deleting = false

    /// Active OAuth login state.
    @Published private(set) var loginInProgress = false
    @Published private(set) var loginError: String?
    private var loginID: String?
    private var pollTask: Task<Void, Never>?

    private let model: AppModel

    init(model: AppModel) { self.model = model }

    // MARK: - List / refresh

    func refresh() async {
        guard model.daemonOnline else {
            loadError = model.appLanguage.text("未连接到 pcrd", "Not connected to pcrd")
            return
        }
        loading = true
        loadError = nil
        defer { loading = false }
        do {
            let client = model.client
            // Account management is always local to the machine the user is managing.
            let response: AccountsListResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.list",
                body: EmptyBody()
            )
            accounts = response.accounts
        } catch {
            loadError = error.localizedDescription
        }
    }

    // MARK: - Switch

    func switchAccount(id: String) async {
        guard !switching else { return }
        switching = true
        defer { switching = false }
        do {
            let client = model.client
            let response: AccountActionResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.switch",
                body: AccountIDBody(id: id)
            )
            if let updated = response.account {
                accounts = accounts.map { acct in
                    var a = acct
                    a.isActive = a.id == updated.id
                    return a
                }
                // Let the UI react immediately; the next status refresh confirms.
                await model.refresh(reportErrors: false)
            } else {
                await refresh()
            }
        } catch {
            loadError = error.localizedDescription
            await refresh()
        }
    }

    // MARK: - Delete

    func deleteAccount(id: String) async {
        guard !deleting else { return }
        deleting = true
        defer { deleting = false }
        do {
            let client = model.client
            let _: AccountActionResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.delete",
                body: AccountIDBody(id: id)
            )
            accounts.removeAll { $0.id == id }
        } catch {
            loadError = error.localizedDescription
            await refresh()
        }
    }

    // MARK: - Add API Key

    func addAPIKey(_ apiKey: String, activate: Bool = true) async -> Bool {
        let trimmed = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        adding = true
        defer { adding = false }
        do {
            let client = model.client
            let response: AccountActionResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.add_apikey",
                body: AddApiKeyBody(api_key: trimmed, activate: activate)
            )
            if let acct = response.account {
                if let idx = accounts.firstIndex(where: { $0.id == acct.id }) {
                    accounts[idx] = acct
                } else {
                    accounts.append(acct)
                }
                accounts = accounts.map { a in
                    var copy = a
                    copy.isActive = a.id == acct.id && activate
                    return copy
                }
                if activate { await model.refresh(reportErrors: false) }
            }
            return true
        } catch {
            loadError = error.localizedDescription
            return false
        }
    }

    // MARK: - OAuth login

    /// Starts the OAuth login flow.  Returns the auth URL the caller should open
    /// in a browser; the manager polls for completion.
    func startLogin() async {
        guard !loginInProgress else { return }
        loginInProgress = true
        loginError = nil
        loginID = nil
        do {
            let client = model.client
            let start: LoginStartResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.login_start",
                body: EmptyBody(),
                timeoutSeconds: 45
            )
            loginID = start.login_id
            // Open the auth URL in the default browser.
            if let url = URL(string: start.auth_url) {
                NSWorkspace.shared.open(url)
            }
            startPolling()
        } catch {
            loginError = error.localizedDescription
            loginInProgress = false
        }
    }

    func cancelLogin() {
        pollTask?.cancel()
        pollTask = nil
        if let id = loginID {
            Task {
                let client = self.model.client
                _ = try? await client.call(
                    target: client.currentTarget(),
                    "codex.accounts.login_cancel",
                    body: LoginCancelBody(login_id: id)
                ) as AccountActionResponse
            }
        }
        loginID = nil
        loginInProgress = false
    }

    private func startPolling() {
        pollTask?.cancel()
        guard let id = loginID else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)  // 2 s
                if Task.isCancelled { break }
                guard let self else { break }
                await self.pollOnce(loginID: id)
                if !self.loginInProgress { break }
            }
        }
    }

    private func pollOnce(loginID: String) async {
        do {
            let client = model.client
            let response: LoginPollResponse = try await client.call(
                target: client.currentTarget(),
                "codex.accounts.login_poll",
                body: LoginPollBody(login_id: loginID)
            )
            switch response.status {
            case "success":
                if let acct = response.account {
                    if let idx = accounts.firstIndex(where: { $0.id == acct.id }) {
                        accounts[idx] = acct
                    } else {
                        accounts.append(acct)
                    }
                    accounts = accounts.map { a in
                        var copy = a
                        copy.isActive = a.id == acct.id
                        return copy
                    }
                } else {
                    await refresh()
                }
                loginInProgress = false
                self.loginID = nil
                pollTask?.cancel()
                await model.refresh(reportErrors: false)
            case "error", "timeout", "not_found":
                loginError = response.error
                loginInProgress = false
                self.loginID = nil
                pollTask?.cancel()
                await refresh()
            default:
                break  // pending — keep polling
            }
        } catch {
            // Network blips during polling are non-fatal; keep going.
        }
    }
}
