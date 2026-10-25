import Combine
import Foundation

@MainActor
final class AgentAnswerOutbox: ObservableObject {
    static let shared = AgentAnswerOutbox(file: AgentAnswerFile(url:
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("parallel-codex-runner/agent-answers.v1.json")))

    @Published private(set) var entries: [PersistedAgentAnswer] = []
    @Published private(set) var receipts: [String: AgentInputRequest] = [:]
    @Published private(set) var storageError: String?
    @Published private(set) var undurableAnswers: Set<String> = []
    private let file: AgentAnswerFile
    private var revision: UInt64 = 0
    private var committing: Set<String> = []
    private var deliveries: [String: Task<Void, Never>] = [:]
    private var retryAfter: [String: Date] = [:]
    private var failures: [String: Int] = [:]
    private var monitor: Task<Void, Never>?
    private var draftSave: Task<Void, Never>?
    private var loadFailed = false
    private var needsFlush = false
    private var storageRetryAfter = Date.distantPast

    init(file: AgentAnswerFile) {
        self.file = file
        do {
            let saved = try file.load()
            revision = saved.revision
            entries = saved.entries
        } catch { storageError = error.localizedDescription; loadFailed = true }
    }

    func entry(machineID: String, workspaceID: String, runID: String,
               agent: Int, requestID: String) -> PersistedAgentAnswer? {
        entries.first { $0.matches(machineID: machineID, workspaceID: workspaceID,
                                  runID: runID, agent: agent, requestID: requestID) }
    }

    func presentation(for snapshot: AgentQuestionPresentation, target: PcrdRequestTarget,
                      workspaceID: String) -> AgentQuestionPresentation {
        guard let command = entry(machineID: target.machineID, workspaceID: workspaceID,
            runID: snapshot.runID, agent: snapshot.agent, requestID: snapshot.request.id),
              command.body.connectionId == snapshot.request.connectionId else { return snapshot }
        let request = receipts[command.id].flatMap {
            AgentInputRequest.merge([$0], into: [snapshot.request]).first
        } ?? snapshot.request
        var value = AgentQuestionPresentation(runID: snapshot.runID, agent: snapshot.agent, request: request,
            command: command, undurable: undurableAnswers.contains(command.id), expanded: snapshot.expanded)
        value.editable = snapshot.editable && value.editable
        value.anchorAfterID = snapshot.anchorAfterID
        if let storageError { value.error = storageError }
        return value
    }

    func setDraft(request: AgentInputRequest, target: PcrdRequestTarget, workspaceID: String,
                  runID: String, agent: Int, questionID: String, value: String) {
        guard !loadFailed, request.canAnswer,
              request.questions.contains(where: { $0.id == questionID }) else { return }
        var command = entry(machineID: target.machineID, workspaceID: workspaceID,
                            runID: runID, agent: agent, requestID: request.id)
            ?? makeEntry(request: request, target: target, workspaceID: workspaceID, runID: runID, agent: agent)
        guard command.status == "draft", !committing.contains(command.id) else { return }
        command.body.answers[questionID] = AgentInputAnswer(answers: [String(value.prefix(8192))])
        replace(command)
        draftSave?.cancel()
        draftSave = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            guard let self else { return }
            do { try await self.persist() } catch { self.storageError = error.localizedDescription }
        }
    }

    func commit(request: AgentInputRequest, target: PcrdRequestTarget, workspaceID: String,
                runID: String, agent: Int) async throws {
        guard !loadFailed else { throw CocoaError(.fileWriteUnknown) }
        guard var command = entry(machineID: target.machineID, workspaceID: workspaceID,
                                  runID: runID, agent: agent, requestID: request.id), request.canAnswer else {
            throw PcrdClientError.remote("question_conflict", "The original question is no longer available.")
        }
        guard command.status == "draft", !committing.contains(command.id) else { return }
        for question in request.questions {
            guard let values = command.body.answers[question.id]?.answers,
                  values.count == 1, !values[0].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  values[0].count <= 8192 else { throw PcrdClientError.invalidResponse }
            if let options = question.options, !options.isEmpty, question.isOther != true,
               !options.contains(where: { $0.label == values[0] }) { throw PcrdClientError.invalidResponse }
        }
        command.status = "pending"
        command.error = ""
        draftSave?.cancel()
        committing.insert(command.id)
        undurableAnswers.insert(command.id)
        defer { committing.remove(command.id) }
        replace(command)
        do { try await persist() }
        catch {
            command.error = error.localizedDescription
            replace(command)
            storageError = error.localizedDescription
            throw error
        }
    }

    func flush() async {
        draftSave?.cancel()
        guard !loadFailed else { return }
        do { try await persist() } catch { storageError = error.localizedDescription }
    }

    func flushForTermination() -> Bool {
        guard !loadFailed else { return undurableAnswers.isEmpty }
        guard needsFlush || !undurableAnswers.isEmpty else { return true }
        draftSave?.cancel()
        revision &+= 1
        let snapshot = AgentAnswerFileSnapshot(revision: revision, entries: entries)
        do { try file.save(snapshot); persisted(snapshot); return true }
        catch { storageError = error.localizedDescription; return false }
    }

    func start(client: PcrdClient) {
        guard monitor == nil else { return }
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.storageError != nil, !self.loadFailed, self.storageRetryAfter <= Date() {
                    self.storageRetryAfter = Date().addingTimeInterval(5)
                    await self.flush()
                }
                let candidates = self.entries.filter { $0.status == "pending" }
                    + self.entries.filter { $0.status == "accepted" }
                for entry in candidates where (entry.status == "pending"
                    || entry.status == "accepted" && !["sent", "unknown"].contains(self.receipts[entry.id]?.delivery ?? "")
                        && self.receipts[entry.id]?.state != "invalidated")
                    && !self.committing.contains(entry.id) && !self.undurableAnswers.contains(entry.id)
                    && self.deliveries[entry.id] == nil
                    && (self.retryAfter[entry.id] ?? .distantPast) <= Date() {
                    guard self.deliveries.count < 4 else { break }
                    if entry.status == "accepted", !self.deliveries.isEmpty { continue }
                    self.deliveries[entry.id] = Task { [weak self] in
                        guard let self else { return }
                        defer { self.deliveries.removeValue(forKey: entry.id) }
                        await self.deliver(entry, client: client)
                    }
                }
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            }
        }
    }

    func stop() {
        monitor?.cancel()
        monitor = nil
        deliveries.values.forEach { $0.cancel() }
        deliveries.removeAll()
        draftSave?.cancel()
    }

    private func deliver(_ original: PersistedAgentAnswer, client: PcrdClient) async {
        var entry = original
        let target: PcrdRequestTarget = entry.relayMachine.map { .relay($0) } ?? .localMachine(entry.machineID)
        do {
            let snapshot: AgentQuestionResponse = try await client.call(target: target, "runs.question",
                body: AgentQuestionBody(runId: entry.body.runId, agent: entry.body.agent, requestId: entry.body.requestId),
                priority: original.status == "pending" ? .userInitiated : .utility)
            try Task.checkCancellation()
            guard snapshot.runId == entry.body.runId, snapshot.agent == entry.body.agent,
                  snapshot.request.id == entry.body.requestId,
                  snapshot.request.connectionId == entry.body.connectionId else { throw PcrdClientError.invalidResponse }
            if let answer = snapshot.request.answers {
                guard answer == entry.body.answers, snapshot.request.answerId == entry.body.answerId else {
                    throw PcrdClientError.remote("question_conflict", "This question already has a different answer.")
                }
                remember(snapshot.request, for: entry.id)
            } else {
                guard original.status == "pending" else { throw PcrdClientError.invalidResponse }
                guard snapshot.request.canAnswer else {
                    remember(snapshot.request, for: entry.id)
                    throw PcrdClientError.remote("question_conflict", "The original question has ended. Your selection is preserved.")
                }
                let receipt: AgentQuestionResponse = try await client.call(target: target, "runs.answer_question",
                    body: entry.body, commandID: "agent-answer-\(entry.id)")
                try Task.checkCancellation()
                guard receipt.runId == entry.body.runId, receipt.agent == entry.body.agent,
                      receipt.request.id == entry.body.requestId,
                      receipt.request.connectionId == entry.body.connectionId,
                      receipt.request.threadId == snapshot.request.threadId,
                      receipt.request.turnId == snapshot.request.turnId,
                      receipt.request.itemId == snapshot.request.itemId,
                      receipt.request.answerId == entry.body.answerId,
                      receipt.request.answers == entry.body.answers else { throw PcrdClientError.invalidResponse }
                remember(receipt.request, for: entry.id)
            }
            entry.status = "accepted"
            entry.error = ""
            entry.errorCode = nil
            failures.removeValue(forKey: entry.id)
            retryAfter[entry.id] = Date().addingTimeInterval(5)
        } catch is CancellationError { return }
        catch {
            guard !Task.isCancelled else { return }
            if case PcrdClientError.remote(let code, _) = error { entry.errorCode = code }
            else if case PcrdClientError.missingToken = error { entry.errorCode = "missing_token" }
            if case PcrdClientError.remote("question_conflict", _) = error {
                entry.status = "closed"
            }
            entry.error = error.localizedDescription
            failures[entry.id, default: 0] += 1
            retryAfter[entry.id] = Date().addingTimeInterval(min(30, pow(2, Double(min(failures[entry.id] ?? 1, 5)))))
        }
        guard !Task.isCancelled else { return }
        guard entry != original else { return }
        replace(entry)
        do { try await persist() } catch { storageError = error.localizedDescription }
    }

    private func remember(_ request: AgentInputRequest, for id: String) {
        let previous = receipts[id].map { [$0] } ?? []
        if let latest = AgentInputRequest.merge([request], into: previous).first, latest != receipts[id] {
            receipts[id] = latest
        }
    }

    private func makeEntry(request: AgentInputRequest, target: PcrdRequestTarget,
                           workspaceID: String, runID: String, agent: Int) -> PersistedAgentAnswer {
        let id = UUID().uuidString
        let relay: RelayMachine?
        if case .relay(let machine) = target { relay = machine } else { relay = nil }
        return PersistedAgentAnswer(id: id, machineID: target.machineID, workspaceID: workspaceID,
            relayMachine: relay, body: AgentQuestionAnswerBody(runId: runID, agent: agent,
                requestId: request.id, connectionId: request.connectionId, answerId: id, answers: [:]),
            status: "draft", error: "", createdAt: Date())
    }

    private func replace(_ entry: PersistedAgentAnswer) {
        needsFlush = true
        if let index = entries.firstIndex(where: { $0.id == entry.id }) { entries[index] = entry }
        else { entries.append(entry) }
    }

    private func persist() async throws {
        revision &+= 1
        let snapshot = AgentAnswerFileSnapshot(revision: revision, entries: entries)
        let file = file
        try await ClientBlockingIO.transport(priority: .userInitiated, isObservation: false) {
            try file.save(snapshot)
        }
        persisted(snapshot)
    }

    private func persisted(_ snapshot: AgentAnswerFileSnapshot) {
        for saved in snapshot.entries where saved.status == "pending" {
            if entries.first(where: { $0.id == saved.id })?.body == saved.body {
                undurableAnswers.remove(saved.id)
            }
        }
        if snapshot.entries == entries { needsFlush = false }
        if undurableAnswers.isEmpty { storageError = nil }
    }
}
