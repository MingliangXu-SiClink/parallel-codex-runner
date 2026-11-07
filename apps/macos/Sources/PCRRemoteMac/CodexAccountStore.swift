import Foundation

struct CodexAccountEntry: Identifiable, Decodable, Equatable, Sendable {
    let id: String
    let displayName: String
    let email: String?
    let accountId: String?
    let hasCredential: Bool
    let lastUsedAt: String?

    private enum CodingKeys: String, CodingKey {
        case id, displayName = "display_name", email, accountId = "account_id"
        case hasCredential = "has_credential", lastUsedAt = "last_used_at"
    }

    var isApiKey: Bool { email == nil }

    var subtitle: String {
        if let email { return email }
        if let aid = accountId, aid.hasPrefix("apikey-") {
            return String(aid.dropFirst(7))
        }
        return "API Key"
    }
}

private struct CodexAccountsResponse: Decodable {
    let accounts: [CodexAccountEntry]
    let activeAccountId: String?
    let currentIdentity: String?
    private enum CodingKeys: String, CodingKey {
        case accounts, activeAccountId = "active_account_id", currentIdentity = "current_identity"
    }
}

private struct CodexAccountSwitchBody: Encodable {
    let accountId: String
    private enum CodingKeys: String, CodingKey { case accountId = "account_id" }
}

private struct CodexAccountSwitchResponse: Decodable {
    let switchedTo: String
    let displayName: String
    let isApiKey: Bool
    private enum CodingKeys: String, CodingKey {
        case switchedTo = "switched_to", displayName = "display_name", isApiKey = "is_api_key"
    }
}

extension AppModel {
    func loadCodexAccounts() async {
        guard !codexAccountsLoading else { return }
        codexAccountsLoading = true
        codexAccountsError = nil
        do {
            let target = client.currentTarget()
            let response: CodexAccountsResponse = try await client.call(
                target: target, "codex.accounts.list", body: EmptyBody()
            )
            codexAccounts = response.accounts
            codexAccountActiveID = response.activeAccountId
            codexAccountsLoading = false
        } catch {
            codexAccountsError = appLanguage.userMessage(for: error)
            codexAccountsLoading = false
        }
    }

    func switchCodexAccount(to accountId: String) async {
        guard !codexAccountSwitching else { return }
        codexAccountSwitching = true
        codexAccountsError = nil
        do {
            let target = client.currentTarget()
            let _: CodexAccountSwitchResponse = try await client.call(
                target: target, "codex.accounts.switch",
                body: CodexAccountSwitchBody(accountId: accountId)
            )
            codexAccountActiveID = accountId
            codexAccountSwitching = false
        } catch {
            codexAccountsError = appLanguage.userMessage(for: error)
            codexAccountSwitching = false
        }
    }
}
