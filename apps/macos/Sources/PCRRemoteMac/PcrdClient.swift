import Darwin
import Foundation

enum PcrdClientError: LocalizedError {
    case missingToken
    case socketPathTooLong
    case connection(String)
    case invalidResponse
    case remote(String, String)

    var errorDescription: String? {
        switch self {
        case .missingToken:
            return "The computer server is not ready. Start Parallel Codex Server on this computer and try again."
        case .socketPathTooLong:
            return "The local server could not start because its data path is too long."
        case .connection(let message):
            return message
        case .invalidResponse:
            return "The local server returned an unreadable response."
        case .remote(let code, let message):
            if code == "server_not_configured" {
                return "This computer's server setup is incomplete. Repair Parallel Codex Server on this computer, then try again."
            }
            if code == "relay_credentials_rejected" {
                return "The server no longer accepts this device's credentials. Re-pair with the server to continue."
            }
            if code == "relay_unreachable" {
                return "Could not reach the relay server. Check your network connection."
            }
            if code == "relay_state_error" {
                return "The relay state is corrupted or unreadable."
            }
            if code == "relay_error" {
                return message
            }
            if code == "unknown_action" {
                return "The connected computer server needs to be updated before this action is available."
            }
            if code == "incompatible_protocol" || code == "unsupported_action" {
                return "This desktop app and the connected computer use incompatible versions. Update Parallel Codex and try again."
            }
            if code == "response_too_large" {
                return "This result is too large to display in one page. Narrow the requested range and try again."
            }
            if code == "workspace_busy" {
                if message.localizedCaseInsensitiveContains("interrupted finalization") {
                    return "Confirm the interrupted workspace sync and finish cleanup before changing workspaces."
                }
                return "Finish or discard the current task before changing workspaces."
            }
            return message
        }
    }

    func isMissingRun(_ runID: String) -> Bool {
        guard case .remote(let code, let message) = self else { return false }
        if ["run_not_found", "unknown_run"].contains(code) { return true }
        // Older pcrd versions use the generic code for this exact lookup failure.
        return code == "pcr_error"
            && message.trimmingCharacters(in: .whitespacesAndNewlines) == "Unknown PCR run: \(runID)"
    }

    var relayHTTPStatus: Int? {
        guard case .remote(let code, _) = self,
              code.hasPrefix("relay_http_") else { return nil }
        return Int(code.dropFirst("relay_http_".count))
    }
}

private struct RequestEnvelope<Body: Encodable & Sendable>: Encodable, Sendable {
    let protocolVersion = 1
    let kind = "request"
    let requestId: String
    let commandId: String
    let clientId = PcrdClient.clientID
    let clientCapabilities = ["agent_questions"]
    let sentAt: String
    let action: String
    let body: Body
    let authToken: String
    let expectedRevision: Int?
}

private struct ControllerStatusResponse: Decodable, Sendable {
    let paired: Bool
    let selectedMachineId: String?
}

private struct ControllerMachineList: Decodable, Sendable {
    let devices: [RelayMachine]
}

private struct ControllerPairBody: Encodable, Sendable {
    let pairingCode: String
    let deviceName: String
}

private struct ControllerSelectBody: Encodable, Sendable {
    let machineId: String
}

private struct ControllerCallBody<Body: Encodable & Sendable>: Encodable, Sendable {
    let machineId: String
    let machinePublicKey: String
    let targetAction: String
    let targetBody: Body
    let targetExpectedRevision: Int?
    let targetCommandId: String
    let timeoutSeconds: Int
}

private struct ControllerHTTPBody<Body: Encodable & Sendable>: Encodable, Sendable {
    let method: String
    let path: String
    let payload: Body
}

private struct ControllerHTTPReadBody: Encodable, Sendable {
    let method: String
    let path: String
}

private final class PcrdTargetStore: @unchecked Sendable {
    private let lock = NSLock()
    private var selectedTarget: PcrdRequestTarget = .local

    func get() -> PcrdRequestTarget {
        lock.lock()
        defer { lock.unlock() }
        return selectedTarget
    }

    func set(_ target: PcrdRequestTarget) {
        lock.lock()
        selectedTarget = target
        lock.unlock()
    }
}

enum PcrdRequestTarget: Hashable, Sendable {
    case local
    case localMachine(String)
    case relay(RelayMachine)

    var machineID: String {
        switch self {
        case .local:
            return MachineContextKey.localMachineID
        case .localMachine(let machineID):
            return machineID
        case .relay(let machine):
            return machine.deviceId
        }
    }
}

struct PcrdClient: Sendable {
    static let clientID = "mac-app"
    let socketURL: URL
    let tokenURL: URL
    private let targetStore: PcrdTargetStore

    init(
        socketURL: URL,
        tokenURL: URL
    ) {
        self.socketURL = socketURL
        self.tokenURL = tokenURL
        targetStore = PcrdTargetStore()
    }

    static var standard: PcrdClient {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        let directory = applicationSupport
            .appendingPathComponent("parallel-codex-runner", isDirectory: true)
            .appendingPathComponent("remote", isDirectory: true)
        return PcrdClient(
            socketURL: directory.appendingPathComponent("pcrd.sock"),
            tokenURL: directory.appendingPathComponent("pcrd.token")
        )
    }

    func call<Body, Response>(
        _ action: String,
        body: Body,
        expectedRevision: Int? = nil,
        timeoutSeconds: Int = 30,
        commandID: String? = nil,
        priority: TaskPriority? = nil,
        response: Response.Type = Response.self
    ) async throws -> Response where Body: Encodable & Sendable, Response: Decodable & Sendable {
        try await call(
            target: currentTarget(),
            action,
            body: body,
            expectedRevision: expectedRevision,
            timeoutSeconds: timeoutSeconds,
            commandID: commandID,
            priority: priority,
            response: response
        )
    }

    func call<Body, Response>(
        target: PcrdRequestTarget,
        _ action: String,
        body: Body,
        expectedRevision: Int? = nil,
        timeoutSeconds: Int = 30,
        commandID: String? = nil,
        priority: TaskPriority? = nil,
        response: Response.Type = Response.self
    ) async throws -> Response where Body: Encodable & Sendable, Response: Decodable & Sendable {
        if case .relay(let machine) = target {
            let effectiveTimeout = max(timeoutSeconds, minimumTimeout(for: action))
            let trimmedCommandID = commandID?.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            let stableCommandID = trimmedCommandID?.isEmpty == false
                ? trimmedCommandID!
                : UUID().uuidString.lowercased()
            return try await callLocal(
                "controller.call",
                body: ControllerCallBody(
                    machineId: machine.deviceId,
                    machinePublicKey: machine.publicKey,
                    targetAction: action,
                    targetBody: body,
                    targetExpectedRevision: expectedRevision,
                    targetCommandId: stableCommandID,
                    timeoutSeconds: effectiveTimeout
                ),
                timeoutSeconds: min(15 * 60, effectiveTimeout + 30),
                commandID: stableCommandID,
                priority: priority,
                observationAction: action,
                response: response
            )
        }
        return try await callLocal(
            action,
            body: body,
            expectedRevision: expectedRevision,
            timeoutSeconds: timeoutSeconds,
            commandID: commandID,
            priority: priority,
            response: response
        )
    }

    func currentTarget() -> PcrdRequestTarget {
        targetStore.get()
    }

    func callLocal<Body, Response>(
        _ action: String,
        body: Body,
        expectedRevision: Int? = nil,
        timeoutSeconds: Int = 30,
        commandID: String? = nil,
        priority: TaskPriority? = nil,
        observationAction: String? = nil,
        response: Response.Type = Response.self
    ) async throws -> Response where Body: Encodable & Sendable, Response: Decodable & Sendable {
        let socketURL = socketURL
        let tokenURL = tokenURL
        let effectiveTimeout = max(timeoutSeconds, minimumTimeout(for: action))
        return try await ClientBlockingIO.transport(
            priority: priority ?? Task.currentPriority,
            isObservation: Self.observationActions.contains(observationAction ?? action),
            isLiveContent: ["runs.agent", "runs.events"].contains(observationAction ?? action)
        ) {
            guard let tokenData = try? Data(contentsOf: tokenURL),
                  let rawToken = String(data: tokenData, encoding: .utf8) else {
                throw PcrdClientError.missingToken
            }
            let token = rawToken.trimmingCharacters(in: .whitespacesAndNewlines)
            let identifier = UUID().uuidString.lowercased()
            let stableCommandID = commandID
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .flatMap { $0.isEmpty ? nil : $0 }
                ?? identifier
            let envelope = RequestEnvelope(
                requestId: identifier,
                commandId: stableCommandID,
                sentAt: ISO8601DateFormatter().string(from: Date()),
                action: action,
                body: body,
                authToken: token,
                expectedRevision: expectedRevision
            )
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            var requestData = try encoder.encode(envelope)
            requestData.append(0x0A)
            var lastTransportError: Error?
            // Answer delivery owns reconciliation. A missing reply must first
            // read the original question's receipt, not immediately resend it.
            let attempts = (observationAction ?? action) == "runs.answer_question" ? 1 : 2
            for attempt in 0..<attempts {
                do {
                    let responseData = try sendUnixRequest(
                        socketPath: socketURL.path,
                        request: requestData,
                        timeoutSeconds: effectiveTimeout
                    )
                    let decoder = JSONDecoder()
                    decoder.keyDecodingStrategy = .convertFromSnakeCase
                    let decoded = try decoder.decode(
                        ResponseEnvelope<Response>.self,
                        from: responseData
                    )
                    guard decoded.ok, let value = decoded.body else {
                        if let error = decoded.error {
                            throw PcrdClientError.remote(error.code, error.message)
                        }
                        throw PcrdClientError.invalidResponse
                    }
                    return value
                } catch let error as PcrdClientError {
                    if case .remote = error { throw error }
                    lastTransportError = error
                } catch {
                    lastTransportError = error
                }
                if attempt == 0 { continue }
            }
            throw lastTransportError ?? PcrdClientError.invalidResponse
        }
    }

    func relayIsPaired() async -> Bool {
        let status: ControllerStatusResponse? = try? await callLocal(
            "controller.status",
            body: EmptyBody()
        )
        return status?.paired == true
    }

    func relayMachines() async throws -> [RelayMachine] {
        let response: ControllerMachineList = try await callLocal(
            "controller.machines",
            body: EmptyBody()
        )
        return response.devices
    }

    func connectRelayController(pairingCode: String, deviceName: String) async throws {
        let _: ControllerStatusResponse = try await callLocal(
            "controller.pair",
            body: ControllerPairBody(
                pairingCode: pairingCode,
                deviceName: deviceName
            ),
            timeoutSeconds: 60
        )
    }

    func selectedRelayMachineID() async -> String? {
        let status: ControllerStatusResponse? = try? await callLocal(
            "controller.status",
            body: EmptyBody()
        )
        return status?.selectedMachineId
    }

    func relayControllerIdentity() async throws -> RelayControllerIdentity {
        try await callLocal(
            "controller.identity",
            body: EmptyBody()
        )
    }

    func selectRelayMachine(
        _ machine: RelayMachine?,
        localMachineID: String? = nil
    ) async {
        setRelayMachineTarget(machine, localMachineID: localMachineID)
        await persistSelectedRelayMachine(machine)
    }

    func setRelayMachineTarget(
        _ machine: RelayMachine?,
        localMachineID: String? = nil
    ) {
        guard let machine else {
            targetStore.set(.local)
            return
        }
        let resolvedLocalID = localMachineID?.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        if resolvedLocalID?.isEmpty == false,
           machine.deviceId == resolvedLocalID {
            targetStore.set(.localMachine(machine.deviceId))
        } else {
            targetStore.set(.relay(machine))
        }
    }

    func persistSelectedRelayMachine(_ machine: RelayMachine?) async {
        let _: ControllerStatusResponse? = try? await callLocal(
            "controller.select",
            body: ControllerSelectBody(machineId: machine?.deviceId ?? "")
        )
    }

    func relayRequest<Body, Response>(
        method: String,
        path: String,
        body: Body,
        response: Response.Type = Response.self
    ) async throws -> Response where Body: Encodable & Sendable, Response: Decodable & Sendable {
        try await callLocal(
            "controller.http",
            body: ControllerHTTPBody(
                method: method,
                path: path,
                payload: body
            ),
            timeoutSeconds: 60,
            response: response
        )
    }

    func relayRequest<Response>(
        method: String,
        path: String,
        response: Response.Type = Response.self
    ) async throws -> Response where Response: Decodable & Sendable {
        try await callLocal(
            "controller.http",
            body: ControllerHTTPReadBody(method: method, path: path),
            timeoutSeconds: 60,
            response: response
        )
    }

    private func minimumTimeout(for action: String) -> Int {
        switch action {
        case "runs.start",
             "runs.continue",
             "runs.accept_agent",
             "turns.start",
             "turns.continue",
             "turns.finalize",
             "runs.recover_finalization",
             "runs.discard",
             "storage.cleanup",
             "runs.add_agents",
             "runs.retry_agent",
             "runs.stop",
             "controller.call":
            return 15 * 60
        case "runs.estimate", "filesystem.inspect", "workspaces.add", "storage.summary":
            return 3 * 60
        default:
            return 30
        }
    }

    // Only known reads share the observation budget. Automatic user commands
    // can inherit utility priority; relay wrappers must use their inner action.
    private static let observationActions: Set<String> = [
        "system.health", "system.capabilities", "commands.get",
        "filesystem.roots", "filesystem.list", "filesystem.inspect",
        "workspaces.list", "workspaces.settings.get", "workspace.file.read",
        "workspace.item.resolve", "settings.defaults.get", "devices.list",
        "runs.estimate", "runs.list", "runs.get", "runs.history", "runs.events",
        "runs.agent", "runs.question", "runs.operation", "runs.diff", "runs.file.preview",
        "conversations.list", "conversations.search", "conversations.get", "turns.get",
        "storage.summary", "codex.resume_sessions", "codex.resume_history",
        "codex.model_options", "codex.account_usage", "prompt_history.list",
        "feedback.list", "feedback.get", "feedback.attachment", "relay.status",
        "controller.status", "controller.identity", "controller.machines",
        "controller.completion_watches", "followups.get", "followups.list",
    ]
}

private func sendUnixRequest(
    socketPath: String,
    request: Data,
    timeoutSeconds: Int
) throws -> Data {
    let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else {
        throw PcrdClientError.connection("The local server could not open a connection.")
    }
    defer { Darwin.close(descriptor) }

    var noSignal: Int32 = 1
    guard withUnsafePointer(to: &noSignal, {
        Darwin.setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size))
    }) == 0 else {
        throw PcrdClientError.connection("The local server could not open a connection.")
    }

    var timeout = timeval(tv_sec: max(1, timeoutSeconds), tv_usec: 0)
    withUnsafePointer(to: &timeout) { pointer in
        _ = setsockopt(
            descriptor,
            SOL_SOCKET,
            SO_RCVTIMEO,
            pointer,
            socklen_t(MemoryLayout<timeval>.size)
        )
        _ = setsockopt(
            descriptor,
            SOL_SOCKET,
            SO_SNDTIMEO,
            pointer,
            socklen_t(MemoryLayout<timeval>.size)
        )
    }

    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let pathBytes = Array(socketPath.utf8CString)
    let capacity = MemoryLayout.size(ofValue: address.sun_path)
    guard pathBytes.count <= capacity else {
        throw PcrdClientError.socketPathTooLong
    }
    withUnsafeMutableBytes(of: &address.sun_path) { destination in
        destination.initializeMemory(as: UInt8.self, repeating: 0)
        pathBytes.withUnsafeBytes { source in
            destination.copyBytes(from: source)
        }
    }
    var connected: Int32 = -1
    for delay in [0.0, 0.1, 0.3] {
        if delay > 0 { Thread.sleep(forTimeInterval: delay) }
        connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(
                    descriptor,
                    $0,
                    socklen_t(MemoryLayout<sockaddr_un>.size)
                )
            }
        }
        if connected == 0 { break }
        // Darwin also returns ECONNREFUSED when a healthy local server's
        // accept queue is full. Retry before sending any command bytes.
        if errno != ECONNREFUSED && errno != EAGAIN && errno != EINTR { break }
    }
    guard connected == 0 else {
        throw PcrdClientError.connection(
            "Cannot connect to the computer server. Check that Parallel Codex Server is running and try again."
        )
    }

    try request.withUnsafeBytes { bytes in
        guard let base = bytes.baseAddress else { return }
        var sent = 0
        while sent < bytes.count {
            let count = Darwin.write(
                descriptor,
                base.advanced(by: sent),
                bytes.count - sent
            )
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else {
                throw PcrdClientError.connection("The local server connection was interrupted.")
            }
            sent += count
        }
    }

    var response = Data()
    var buffer = [UInt8](repeating: 0, count: 16_384)
    let maximumResponseBytes = 1_024 * 1_024 + 1
    while true {
        let count = buffer.withUnsafeMutableBytes { bytes in
            Darwin.read(descriptor, bytes.baseAddress, bytes.count)
        }
        if count < 0 && errno == EINTR { continue }
        guard count >= 0 else {
            throw PcrdClientError.connection("The local server connection was interrupted.")
        }
        if count == 0 { break }
        response.append(buffer, count: count)
        guard response.count <= maximumResponseBytes else {
            throw PcrdClientError.invalidResponse
        }
        if response.contains(0x0A) { break }
    }
    guard let newline = response.firstIndex(of: 0x0A) else {
        throw PcrdClientError.invalidResponse
    }
    return response.prefix(upTo: newline)
}
