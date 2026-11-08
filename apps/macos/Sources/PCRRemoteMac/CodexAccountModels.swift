import Foundation

// MARK: - Account profile (matches AccountProfile.to_dict() from pcrd)

struct PCRAccount: Identifiable, Codable, Sendable, Equatable {
    let id: String
    let displayName: String
    let email: String?
    let accountID: String?
    let isApiKey: Bool
    let createdAt: String?
    var isActive: Bool

    static func == (lhs: PCRAccount, rhs: PCRAccount) -> Bool { lhs.id == rhs.id }
}

// MARK: - codex.accounts.list

struct AccountsListResponse: Decodable, Sendable {
    let accounts: [PCRAccount]
    let activeID: String?
}

// MARK: - codex.accounts.switch / delete / sync

struct AccountIDBody: Encodable, Sendable {
    let id: String
}

struct AccountActionResponse: Decodable, Sendable {
    let success: Bool
    let account: PCRAccount?
}

struct AccountSyncResponse: Decodable, Sendable {
    let synced: Bool
    let account: PCRAccount?
}

// MARK: - codex.accounts.add_apikey

struct AddApiKeyBody: Encodable, Sendable {
    let api_key: String
    let activate: Bool
}

// MARK: - codex.accounts.login_start / poll / cancel

struct LoginStartResponse: Decodable, Sendable {
    let login_id: String
    let auth_url: String
}

struct LoginPollBody: Encodable, Sendable {
    let login_id: String
}

struct LoginCancelBody: Encodable, Sendable {
    let login_id: String
}

struct LoginPollResponse: Decodable, Sendable {
    let status: String   // pending | success | error | timeout | not_found
    let account: PCRAccount?
    let error: String?
}
