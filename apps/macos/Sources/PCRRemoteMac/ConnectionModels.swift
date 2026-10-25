import Foundation

let maximumAgentCountLimit = 4_096

struct EmptyBody: Codable, Sendable {}

struct ErrorPayload: Decodable, Sendable {
    let code: String
    let message: String
}

struct ResponseEnvelope<Response: Decodable & Sendable>: Decodable, Sendable {
    let ok: Bool
    let body: Response?
    let error: ErrorPayload?
}

struct HealthResponse: Decodable, Sendable {
    let status: String
    let workspaceRegistry: WorkspaceList
    let devices: DeviceList
    let relay: RelayStatus
    let link: RelayLinkInfo?
}

struct RelayLinkInfo: Decodable, Equatable, Sendable {
    let state: String
    let since: String?
    let lastError: String?
    let lastErrorAt: String?
}

private let clientProtocolVersion = 1
private let clientTranscriptSchemaVersion = 1

struct SystemCapabilities: Decodable, Equatable, Sendable {
    let daemonName: String?
    let daemonVersion: String?
    let protocolVersion: Int?
    let minimumClientProtocolVersion: Int?
    let supportedProtocolVersions: [Int]?
    let transcriptSchemaVersion: Int?
    let conversationSchemaVersion: Int?
    let maximumAgentCount: Int?
    let transport: String?
    let platform: String?
    let supportsDaemonCompletionNotifications: Bool?
    let supportsDaemonRemoteCompletionNotifications: Bool?
    let actions: [String]
    var commandReceiptStoreId: String? = nil
    var supportsFollowUpEnqueueReconciliation: Bool? = nil

    var compatible: Bool {
        let minimum = minimumClientProtocolVersion ?? clientProtocolVersion
        let supported = supportedProtocolVersions ?? [clientProtocolVersion]
        let transcript = transcriptSchemaVersion ?? clientTranscriptSchemaVersion
        let conversation = conversationSchemaVersion ?? generatedConversationSchemaVersion
        return minimum <= clientProtocolVersion
            && supported.contains(clientProtocolVersion)
            && transcript <= clientTranscriptSchemaVersion
            && conversation <= generatedConversationSchemaVersion
    }

    func supports(_ action: String) -> Bool {
        actions.contains(action)
    }

    var clientMaximumAgentCount: Int {
        min(max(1, maximumAgentCount ?? maximumAgentCountLimit), maximumAgentCountLimit)
    }
}

struct RelayStatus: Decodable, Equatable, Sendable {
    let configured: Bool
    let paired: Bool
    let baseUrl: String?
    let macDeviceId: String?
    let deviceName: String?
    let pairingId: String?
    let pairingUri: String?
    let pairingExpiresAt: String?
    let userId: String?
    let trustedAndroidDevices: Int?
    let trustedControllers: [RelayTrustedController]?

    var hasConnectedPhone: Bool { (trustedAndroidDevices ?? 0) > 0 }

    func trusts(_ identity: RelayControllerIdentity) -> Bool {
        guard let deviceID = identity.deviceId else { return false }
        return trustedControllers?.contains {
            $0.deviceId == deviceID && $0.publicKey == identity.publicKey
        } == true
    }
}

struct RelayTrustedController: Decodable, Equatable, Sendable {
    let deviceId: String
    let publicKey: String
}

struct RelayMachine: Codable, Identifiable, Hashable, Sendable {
    let deviceId: String
    let kind: String
    let name: String
    let publicKey: String
    let status: String
    let online: Bool?
    let lastSeenAt: String?

    var id: String { deviceId }
}

struct RelayControllerIdentity: Decodable, Sendable {
    let deviceId: String?
    let publicKey: String
}

struct ControllerCompletionWatchBody: Encodable, Sendable {
    let machineId: String
    let machinePublicKey: String
    let runId: String
    let workspaceName: String
}

struct ControllerCompletionWatchResponse: Decodable, Sendable {
    let watching: Bool
    let machineId: String
    let runId: String
    let watchCount: Int
}

struct RelayPairingBody: Encodable, Sendable {
    let force: Bool
}

struct RelayPollResult: Decodable, Sendable {
    let configured: Bool
    let paired: Bool
    let pairingStatus: String
    let baseUrl: String?
    let macDeviceId: String?
    let pairingUri: String?
    let pairingExpiresAt: String?
    let trustedAndroidDevices: Int?
}
