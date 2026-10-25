import Foundation

struct AgentInputOption: Codable, Hashable, Sendable {
    let label: String
    let description: String

    init(label: String, description: String) {
        self.label = label
        self.description = description
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        label = try values.decode(String.self, forKey: .label)
        description = try values.decodeIfPresent(String.self, forKey: .description) ?? ""
    }
}

struct AgentInputQuestion: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let header: String
    let question: String
    let options: [AgentInputOption]?
    let isOther: Bool?
    let isSecret: Bool?
}

struct AgentInputAnswer: Codable, Hashable, Sendable {
    let answers: [String]
}

struct AgentInputRequest: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let connectionId: String
    let threadId: String
    let turnId: String
    let itemId: String
    let isBlocking: Bool
    let questions: [AgentInputQuestion]
    let state: String
    let delivery: String
    let answerId: String?
    let answers: [String: AgentInputAnswer]?
    let createdAt: Double
    let updatedAt: Double
    var kind: String? = nil

    var canAnswer: Bool { state == "open" && delivery == "none" && answers == nil }

    static func merge(_ incoming: [Self], into existing: [Self]) -> [Self] {
        var records = Dictionary(existing.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
        for request in incoming {
            if let old = records[request.id] {
                guard request.connectionId == old.connectionId,
                      request.threadId == old.threadId, request.turnId == old.turnId,
                      request.itemId == old.itemId, request.questions == old.questions,
                      request.updatedAt >= old.updatedAt else { continue }
                if request.updatedAt == old.updatedAt && old.state != "open" && request.state == "open" { continue }
                if old.answers != nil && request.answers == nil { continue }
                if old.delivery == "sent" && ["none", "queued", "sending"].contains(request.delivery) { continue }
            }
            records[request.id] = request
        }
        return records.values.sorted { $0.createdAt < $1.createdAt }
    }
}

enum AgentAnswerDeliveryState: String, Codable, Hashable, Sendable {
    case unsaved, localPending, queued, sending, received, sent, unconfirmed, closed, checking

    init(request: AgentInputRequest, command: PersistedAgentAnswer?, undurable: Bool) {
        if undurable { self = .unsaved }
        else if let command, request.answers != nil, request.answerId != command.body.answerId { self = .closed }
        else if request.delivery == "unknown" { self = .unconfirmed }
        else if request.delivery == "sent" { self = request.kind == "async" ? .received : .sent }
        else if request.state == "invalidated" || command?.status == "closed" { self = .closed }
        else if let command, ["pending", "accepted"].contains(command.status), !command.error.isEmpty { self = .unconfirmed }
        else if request.delivery == "queued" { self = .queued }
        else if request.delivery == "sending" { self = .sending }
        else if command?.status == "pending" { self = .localPending }
        else { self = .checking }
    }
}

struct AgentQuestionPresentation: Codable, Hashable, Sendable {
    let runID: String
    let agent: Int
    let request: AgentInputRequest
    let values: [String: String]
    let delivery: AgentAnswerDeliveryState
    var editable: Bool
    let expanded: Bool
    var error: String?
    var anchorAfterID: String? = nil

    init(runID: String, agent: Int, request: AgentInputRequest, command: PersistedAgentAnswer?,
         undurable: Bool = false, expanded: Bool = false, historical: Bool = false) {
        self.runID = runID
        self.agent = agent
        self.request = request
        values = request.questions.reduce(into: [:]) { result, question in
            if let value = request.answers?[question.id]?.answers.first
                ?? command?.body.answers[question.id]?.answers.first {
                result[question.id] = value
            }
        }
        delivery = AgentAnswerDeliveryState(request: request, command: command, undurable: undurable)
        editable = !historical && request.canAnswer && (command == nil || command?.status == "draft")
        self.expanded = expanded
        error = command.flatMap { !$0.error.isEmpty && $0.needsUserAction == true ? $0.error : nil }
    }

    var id: String { "question:\(runID):\(agent):\(request.id)" }

    var canSubmit: Bool {
        editable && request.questions.allSatisfy {
            !(values[$0.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    var searchableText: String {
        request.questions.map { question in
            let answer = values[question.id].map { question.isSecret == true ? "********" : $0 } ?? ""
            return ([question.question] + (question.options ?? []).map(\.label) + [answer]).joined(separator: "\n")
        }.joined(separator: "\n\n")
    }

    func receiptLabel(language: AppLanguage) -> String {
        switch delivery {
        case .unsaved: return language.text("回答未能保存，Agent \(agent) 尚未收到", "Answer not saved; Agent \(agent) has not received it")
        case .localPending: return language.text("正在发送回答，等待 Agent \(agent) 的回执", "Sending answer; awaiting Agent \(agent)'s receipt")
        case .queued: return language.text("PCR 已收到，Agent \(agent) 尚未接收", "PCR received the answer; Agent \(agent) has not received it yet")
        case .sending, .checking: return language.text("正在核对 Agent \(agent) 的接收回执", "Checking Agent \(agent)'s receipt")
        case .received: return language.text("Agent \(agent) 已接收回答", "Agent \(agent) received the answer")
        case .sent: return language.text("回答已发送至 Agent \(agent)", "Answer sent to Agent \(agent)")
        case .unconfirmed: return language.text("无法确认 Agent \(agent) 是否收到，回答已保留", "Agent \(agent)'s receipt is unconfirmed; your answer is preserved")
        case .closed: return language.text("Agent \(agent) 的问题已结束，已有回答保留", "Agent \(agent)'s question ended; your answer is preserved")
        }
    }
}

struct AgentQuestionBody: Encodable, Sendable {
    let runId: String
    let agent: Int
    let requestId: String
}

struct AgentQuestionResponse: Decodable, Sendable {
    let runId: String
    let agent: Int
    let request: AgentInputRequest
}

struct AgentQuestionAnswerBody: Codable, Equatable, Sendable {
    let runId: String
    let agent: Int
    let requestId: String
    let connectionId: String
    let answerId: String
    var answers: [String: AgentInputAnswer]
}

struct PersistedAgentAnswer: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let machineID: String
    let workspaceID: String
    let relayMachine: RelayMachine?
    var body: AgentQuestionAnswerBody
    var status: String
    var error: String
    var errorCode: String? = nil
    let createdAt: Date

    var needsUserAction: Bool {
        ["unauthorized", "invalid_token", "relay_credentials_rejected", "relay_pairing_required",
         "controller_unpaired", "device_revoked", "missing_token", "unknown_action", "unsupported_action"].contains(errorCode ?? "")
    }

    func matches(machineID: String, workspaceID: String, runID: String, agent: Int, requestID: String) -> Bool {
        self.machineID == machineID && self.workspaceID == workspaceID
            && body.runId == runID && body.agent == agent && body.requestId == requestID
    }
}

struct AgentAnswerFileSnapshot: Codable, Sendable {
    var revision: UInt64
    var entries: [PersistedAgentAnswer]
}

final class AgentAnswerFile: @unchecked Sendable {
    let url: URL
    private let lock = NSLock()
    private var writtenRevision: UInt64 = 0

    init(url: URL) { self.url = url }

    func load() throws -> AgentAnswerFileSnapshot {
        lock.lock()
        defer { lock.unlock() }
        guard FileManager.default.fileExists(atPath: url.path) else {
            return AgentAnswerFileSnapshot(revision: 0, entries: [])
        }
        let snapshot = try JSONDecoder().decode(AgentAnswerFileSnapshot.self, from: Data(contentsOf: url))
        writtenRevision = max(writtenRevision, snapshot.revision)
        return snapshot
    }

    func save(_ snapshot: AgentAnswerFileSnapshot) throws {
        lock.lock()
        defer { lock.unlock() }
        guard snapshot.revision >= writtenRevision else { return }
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(snapshot)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        let file = try FileHandle(forWritingTo: url)
        try file.synchronize()
        try file.close()
        writtenRevision = snapshot.revision
    }
}
