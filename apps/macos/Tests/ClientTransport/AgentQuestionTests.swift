import AppKit
import Darwin
import Foundation
import SwiftUI
import Testing
@testable import PCRRemoteMac

private struct QuestionFixtureForm: View {
    let request: AgentInputRequest
    @ObservedObject var outbox: AgentAnswerOutbox
    var body: some View {
        let command = outbox.entry(machineID: "machine", workspaceID: "workspace", runID: "run", agent: 1, requestID: request.id)
        let current = command.flatMap { outbox.receipts[$0.id] } ?? request
        let value = AgentQuestionPresentation(runID: "run", agent: 1, request: current, command: command)
        AgentQuestionForm(presentation: value, language: .chinese, actions: .init(
            setAnswer: { value, id, answer in
                outbox.setDraft(request: value.request, target: .localMachine("machine"), workspaceID: "workspace",
                    runID: value.runID, agent: value.agent, questionID: id, value: answer)
            },
            submit: { value in
                try await outbox.commit(request: value.request, target: .localMachine("machine"),
                    workspaceID: "workspace", runID: value.runID, agent: value.agent)
            }))
    }
}

@Suite(.serialized)
@MainActor
struct AgentQuestionTests {
    @Test(arguments: [false, true], [false, true])
    func actualWindowRegistrationStartsSavedAnswers(extraWindow: Bool, alreadyReceived: Bool) async throws {
        let original = request()
        let value = "验证简单基线"
        let fixture = try Fixture(question: alreadyReceived ? request(delivery: "queued", answer: value) : original,
                                  loseFirstAnswerReceipt: !alreadyReceived)
        defer { fixture.close() }
        fixture.outbox.setDraft(request: original, target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: value)
        try await fixture.outbox.commit(request: original, target: .localMachine("machine"),
            workspaceID: "workspace", runID: "run", agent: 1)
        let saved = try #require(fixture.outbox.entries.first)
        if alreadyReceived {
            fixture.peer.setQuestion(request(delivery: "queued", answer: value, answerID: saved.id))
        }
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: fixture.file.url))
        let model = AppModel(client: fixture.client, windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: fixture.directory.appendingPathComponent("desktop")),
            questionAnswers: restored)
        defer { restored.stop(); model.stopWindowObservation() }
        let delegate = ParallelCodexAppDelegate()
        delegate.questionAnswers = restored
        let catalogKey = "parallel-codex.open-window-ids.v1"
        let catalog = UserDefaults.standard.object(forKey: catalogKey)
        defer { UserDefaults.standard.set(catalog, forKey: catalogKey) }
        let registration = ClientWindowRegistrationView()
        registration.model = model
        registration.delegate = delegate
        registration.windowID = extraWindow ? UUID() : nil
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 100, height: 100),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = registration
        defer { window.close() }
        try await until { restored.entries.first?.status == "accepted" }
        #expect(restored.entries.first?.id == saved.id && restored.entries.first?.body == saved.body)
        #expect(fixture.peer.answersReceived == (alreadyReceived ? 0 : 1))
        #expect(fixture.peer.commands == (alreadyReceived ? [] : ["agent-answer-\(saved.id)"]))
        #expect(fixture.peer.allAnswersWereDurable)
        #expect(model.activeRun == nil && model.selectedAgent == nil)
    }

    private func request(_ id: String = "question", state: String = "open", delivery: String = "none",
                         updated: Double = 1, answer: String? = nil, blocking: Bool = true,
                         answerID: String? = nil, kind: String? = nil) -> AgentInputRequest {
        AgentInputRequest(id: id, connectionId: "native-connection", threadId: "original-thread", turnId: "original-turn",
            itemId: "original-item", isBlocking: blocking, questions: [AgentInputQuestion(id: "direction", header: "方向",
                question: "你希望这个 Agent 优先验证哪种方向？", options: [
                    AgentInputOption(label: "验证简单基线", description: "保留现有成果，先进行最小验证。"),
                    AgentInputOption(label: "分析当前误差", description: "根据实际结果决定下一步，不重复已完成的工作。")],
                isOther: true, isSecret: false)], state: state, delivery: delivery, answerId: answerID,
            answers: answer.map { ["direction": AgentInputAnswer(answers: [$0])] }, createdAt: 1, updatedAt: updated,
            kind: kind)
    }

    private func directory() throws -> URL {
        let url = URL(fileURLWithPath: "/tmp").appendingPathComponent("pqa-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @Test func originalQuestionAndDraftSurviveClientRestartWithoutSubmitting() async throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = AgentAnswerFile(url: directory.appendingPathComponent("answers.json"))
        let outbox = AgentAnswerOutbox(file: file)
        outbox.setDraft(request: request(), target: .localMachine("machine-a"), workspaceID: "workspace-a",
            runID: "original-run", agent: 1, questionID: "direction", value: "验证简单基线")
        await outbox.flush()
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: file.url))
        let command = try #require(restored.entries.first)
        #expect(command.status == "draft")
        #expect(command.body.runId == "original-run" && command.body.agent == 1)
        #expect(command.body.connectionId == "native-connection")
        #expect(command.body.answers["direction"]?.answers == ["验证简单基线"])
        #expect(restored.entry(machineID: "machine-b", workspaceID: "workspace-a", runID: "original-run", agent: 1, requestID: "question") == nil)
        #expect(restored.entry(machineID: "machine-a", workspaceID: "workspace-b", runID: "original-run", agent: 1, requestID: "question") == nil)
        #expect(restored.entry(machineID: "machine-a", workspaceID: "workspace-a", runID: "original-run", agent: 2, requestID: "question") == nil)
    }

    @Test func duplicateWindowSubmissionsCommitOneImmutableAnswerBeforeDelivery() async throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = AgentAnswerFile(url: directory.appendingPathComponent("answers.json"))
        let outbox = AgentAnswerOutbox(file: file)
        outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        async let first: Void = outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        async let second: Void = outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        _ = try await (first, second)
        #expect(outbox.entries.count == 1)
        let saved = try file.load()
        #expect(saved.entries.first?.status == "pending")
        let original = try #require(outbox.entries.first)
        outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "分析当前误差")
        #expect(outbox.entries.first?.body == original.body)
    }

    @Test func delayedSnapshotsCannotResurrectResolvedOrWrongExecutionQuestions() {
        let open = request()
        let closed = request(state: "resolved", delivery: "sent", updated: 2, answer: "验证简单基线")
        #expect(AgentInputRequest.merge([open], into: [closed]) == [closed])
        let invalid = request(state: "invalidated", delivery: "unknown")
        #expect(AgentInputRequest.merge([open], into: [invalid]) == [invalid])
        let impostor = AgentInputRequest(id: open.id, connectionId: "different-owner", threadId: open.threadId,
            turnId: open.turnId, itemId: open.itemId, isBlocking: true, questions: open.questions,
            state: "open", delivery: "none", answerId: nil, answers: nil, createdAt: 1, updatedAt: 99)
        #expect(AgentInputRequest.merge([impostor], into: [closed]) == [closed])
    }

    @Test func transcriptQuestionCacheIsBoundToItsOriginalBranchAndSurvivesColdRead() throws {
        var transcript = AgentTranscript(sourceRunID: "run", sourceAgent: 1)
        transcript.inputRequests = [request()]
        let restored = try JSONDecoder().decode(AgentTranscript.self, from: JSONEncoder().encode(transcript))
        #expect(agentTranscriptBoundToBranch(restored, runID: "run", agent: 1).inputRequests == [request()])
        #expect(agentTranscriptBoundToBranch(restored, runID: "other-run", agent: 1).inputRequests == nil)
        #expect(agentTranscriptBoundToBranch(restored, runID: "run", agent: 2).inputRequests == nil)
    }

    @Test func lateDiskWritesDoNotReplaceNewerConfirmedChoices() throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = AgentAnswerFile(url: directory.appendingPathComponent("answers.json"))
        try file.save(AgentAnswerFileSnapshot(revision: 5, entries: []))
        try file.save(AgentAnswerFileSnapshot(revision: 2, entries: []))
        #expect(try file.load().revision == 5)
        let permissions = try FileManager.default.attributesOfItem(atPath: file.url.path)[.posixPermissions] as? Int
        #expect(permissions == 0o600)
    }

    @Test func corruptedOutboxIsNeverSilentlyOverwritten() async throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("answers.json")
        try Data("corrupted original evidence".utf8).write(to: url)
        let outbox = AgentAnswerOutbox(file: AgentAnswerFile(url: url))
        #expect(outbox.storageError != nil)
        outbox.setDraft(request: request(), target: .local, workspaceID: "workspace", runID: "run", agent: 1,
                        questionID: "direction", value: "验证简单基线")
        await outbox.flush()
        #expect(try String(contentsOf: url, encoding: .utf8) == "corrupted original evidence")
    }

    @Test func lostAnswerReceiptIsReconciledWithoutAnotherAnswerOrNewPrompt() async throws {
        let fixture = try Fixture(question: request(), loseFirstAnswerReceipt: true)
        defer { fixture.close() }
        let outbox = fixture.outbox
        outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        outbox.start(client: fixture.client)
        try await until { outbox.entries.first?.status == "accepted" }
        #expect(fixture.peer.answersReceived == 1)
        #expect(fixture.peer.readsReceived >= 2)
        #expect(fixture.peer.actions.allSatisfy { ["runs.question", "runs.answer_question"].contains($0) })
        #expect(fixture.peer.allAnswersWereDurable)
    }

    @Test func serviceAcceptanceIsPolledUntilNativeReceiptWithoutResubmittingOrRewriting() async throws {
        let original = request(blocking: false, kind: "async")
        let fixture = try Fixture(question: original)
        defer { fixture.close() }
        fixture.outbox.setDraft(request: original, target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await fixture.outbox.commit(request: original, target: .localMachine("machine"),
            workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        let saved = try #require(fixture.outbox.entries.first)
        let queued = try #require(fixture.outbox.receipts[saved.id])
        #expect(AgentAnswerDeliveryState(request: queued, command: saved, undurable: false) == .queued)
        try await until { (try? fixture.file.load().entries.first?.status) == "accepted" }
        let disk = try Data(contentsOf: fixture.file.url)
        let received = request(state: "resolved", delivery: "sent", updated: 3, answer: "验证简单基线",
            blocking: false, answerID: saved.id, kind: "async")
        fixture.peer.setQuestion(received)
        try await until { fixture.outbox.receipts[saved.id]?.delivery == "sent" }
        #expect(AgentAnswerDeliveryState(request: received, command: saved, undurable: false) == .received)
        #expect(fixture.peer.answersReceived == 1)
        #expect(try Data(contentsOf: fixture.file.url) == disk)
        #expect(fixture.outbox.entries.first == saved)
        fixture.outbox.stop()
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: fixture.file.url))
        defer { restored.stop() }
        restored.start(client: fixture.client)
        try await until { restored.receipts[saved.id]?.delivery == "sent" }
        #expect(fixture.peer.answersReceived == 1)
        #expect(try Data(contentsOf: fixture.file.url) == disk)
        #expect(AgentInputRequest.merge([queued], into: [received]) == [received])
        let replay = request(delivery: "none", updated: 9)
        #expect(AgentInputRequest.merge([replay], into: [received]) == [received])
    }

    @Test func aLateEmptyReceiptNeverResendsAnAlreadyAcceptedAnswer() async throws {
        let original = request()
        let fixture = try Fixture(question: original)
        defer { fixture.close() }
        fixture.outbox.setDraft(request: original, target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await fixture.outbox.commit(request: original, target: .localMachine("machine"),
            workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        let saved = try #require(fixture.outbox.entries.first)
        fixture.peer.setQuestion(request(updated: 3))
        try await until { fixture.outbox.entries.first?.error.isEmpty == false }
        #expect(fixture.outbox.entries.first?.status == "accepted")
        #expect(fixture.peer.answersReceived == 1)
        #expect(fixture.outbox.receipts[saved.id]?.delivery == "queued")
        fixture.peer.setQuestion(request(state: "resolved", delivery: "sent", updated: 4,
            answer: "验证简单基线", answerID: saved.id))
        try await until { fixture.outbox.receipts[saved.id]?.delivery == "sent" }
        #expect(fixture.peer.answersReceived == 1)
        #expect(fixture.outbox.entries.first?.body == saved.body)
    }

    @Test func anotherAnswerIDWithTheSameValuesIsNotMyNativeReceipt() async throws {
        let foreign = request(state: "resolved", delivery: "sent", updated: 2,
            answer: "验证简单基线", answerID: "another-answer", kind: "async")
        let fixture = try Fixture(question: foreign)
        defer { fixture.close() }
        fixture.outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await fixture.outbox.commit(request: request(), target: .localMachine("machine"),
            workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "closed" }
        let saved = try #require(fixture.outbox.entries.first)
        #expect(fixture.peer.answersReceived == 0)
        #expect(saved.body.answerId != foreign.answerId)
        #expect(AgentAnswerDeliveryState(request: foreign, command: saved, undurable: false) == .closed)
    }

    @Test func receiptPresentationSeparatesDurabilityDispatchAndNativeAcceptance() {
        #expect(AgentAnswerDeliveryState(request: request(), command: nil, undurable: true) == .unsaved)
        #expect(AgentAnswerDeliveryState(request: request(delivery: "queued"), command: nil, undurable: false) == .queued)
        #expect(AgentAnswerDeliveryState(request: request(delivery: "sending"), command: nil, undurable: false) == .sending)
        #expect(AgentAnswerDeliveryState(request: request(delivery: "unknown"), command: nil, undurable: false) == .unconfirmed)
        #expect(AgentAnswerDeliveryState(request: request(state: "invalidated"), command: nil, undurable: false) == .closed)
        #expect(AgentAnswerDeliveryState(request: request(delivery: "sent"), command: nil, undurable: false) == .sent)
        #expect(AgentAnswerDeliveryState(request: request(delivery: "sent", kind: "async"), command: nil, undurable: false) == .received)
        #expect(AgentAnswerDeliveryState(request: request(), command: nil, undurable: false) == .checking)
    }

    @Test func aReadFailureDoesNotTurnTheLastQueuedSnapshotIntoAClaimAboutCurrentNativeReceipt() {
        var saved = PersistedAgentAnswer(id: "original-answer", machineID: "machine", workspaceID: "workspace",
            relayMachine: nil, body: AgentQuestionAnswerBody(runId: "run", agent: 1, requestId: "question",
                connectionId: "native-connection", answerId: "original-answer", answers: [
                    "direction": AgentInputAnswer(answers: ["验证简单基线"])]),
            status: "accepted", error: "Read timed out", createdAt: Date())
        let queued = request(delivery: "queued", answer: "验证简单基线", answerID: saved.id, kind: "async")
        #expect(AgentAnswerDeliveryState(request: queued, command: saved, undurable: false) == .unconfirmed)
        saved.status = "pending"
        #expect(AgentAnswerDeliveryState(request: request(), command: saved, undurable: false) == .unconfirmed)
        let received = request(state: "resolved", delivery: "sent", answer: "验证简单基线", answerID: saved.id, kind: "async")
        #expect(AgentAnswerDeliveryState(request: received, command: saved, undurable: false) == .received)
    }

    @Test(arguments: [420.0, 980.0], [AppTextSize.standard, .extraLarge])
    func committedQuestionShowsItsQuestionAndAnswerWithoutAnEmptyPanel(width: Double, size: AppTextSize) async throws {
        let original = request(blocking: false, kind: "async")
        let fixture = try Fixture(question: original)
        defer { fixture.close() }
        let target = fixture.client.currentTarget()
        fixture.outbox.setDraft(request: original, target: target, workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await fixture.outbox.commit(request: original, target: target,
            workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        let model = try showQuestions([original], in: fixture)
        defer { model.stopWindowObservation() }
        let block = try #require(model.selectedTranscriptBlocks.first { $0.question != nil })
        let presentation = try #require(block.question)
        let view = AgentQuestionForm(presentation: presentation, language: .chinese, actions: .init())
            .font(.system(size: 14 * size.transcriptScale))
            .environment(\.appLanguage, .chinese).environment(\.appTextSize, size)
            .frame(width: width).background(ParallelCodexVisualStyle.canvas).preferredColorScheme(.dark)
        let host = NSHostingView(rootView: view)
        host.frame = NSRect(x: 0, y: 0, width: width, height: 300)
        let window = NSWindow(contentRect: host.bounds, styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = host
        defer { window.contentView = nil }
        for _ in 0..<4 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(50)) }
        let height = host.fittingSize.height
        #expect(height > 60 && height < 240 * size.scale)
        #expect(presentation.values["direction"] == "验证简单基线")
        #expect(presentation.delivery == .queued)
        host.frame.size.height = ceil(height)
        host.layoutSubtreeIfNeeded(); host.displayIfNeeded()
        let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        if let output = ProcessInfo.processInfo.environment["PCR_QUESTION_QA_OUTPUT"] {
            try #require(bitmap.representation(using: .png, properties: [:])).write(to:
                URL(fileURLWithPath: output).appendingPathComponent("receipt-queued-\(Int(width))-\(size.rawValue).png"))
        }
        #expect(!window.isVisible && !window.isKeyWindow)
        #expect(model.transcripts[1]?.inputRequests == [original], "Receipt projection must not rewrite navigation or the transcript.")
    }

    @Test func questionsKeepTheirChronologicalMessagePositionsRatherThanADockedViewport() async throws {
        let original = request()
        let fixture = try Fixture(question: original)
        defer { fixture.close() }
        let history = (1...12).map { request("answered-\($0)", state: "resolved", delivery: "sent", answer: "验证简单基线") }
        let model = try showQuestions(history + [original], in: fixture)
        defer { model.stopWindowObservation() }
        let projected = model.selectedTimelineItems.compactMap { item -> AgentQuestionPresentation? in
            guard case .message(let block) = item else { return nil }
            return block.question
        }
        #expect(projected.count == 13)
        #expect(projected.last?.request.id == original.id)
        let active = try #require(projected.first { $0.request.id == original.id })
        let host = NSHostingView(rootView: AgentQuestionForm(presentation: active, language: .chinese, actions: .init())
            .environment(\.appLanguage, .chinese).environment(\.appTextSize, .standard)
            .frame(width: 580).background(ParallelCodexVisualStyle.canvas).preferredColorScheme(.dark))
        host.frame = NSRect(x: 0, y: 0, width: 580, height: 400)
        let window = NSWindow(contentRect: host.bounds, styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = host
        defer { window.contentView = nil }
        for _ in 0..<4 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(50)) }
        host.frame.size.height = ceil(host.fittingSize.height)
        host.layoutSubtreeIfNeeded(); host.displayIfNeeded()
        let radios = descendants(host).compactMap { $0 as? NSButton }
        #expect(radios.count == 3)
        for radio in radios { #expect(host.bounds.contains(radio.convert(radio.bounds, to: host))) }
        #expect(host.bounds.height < 400)
        #expect(fixture.outbox.entries.isEmpty && fixture.peer.answersReceived == 0)
        #expect(!window.isVisible && !window.isKeyWindow)
        if let output = ProcessInfo.processInfo.environment["PCR_QUESTION_QA_OUTPUT"] {
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            try #require(bitmap.representation(using: .png, properties: [:])).write(to:
                URL(fileURLWithPath: output).appendingPathComponent("active-question-before-history.png"))
        }
    }

    private func showQuestions(_ requests: [AgentInputRequest], in fixture: Fixture) throws -> AppModel {
        let model = AppModel(client: fixture.client, windowID: UUID(),
            desktopStateStore: DesktopStateStore(directory: fixture.directory.appendingPathComponent("desktop")),
            questionAnswers: fixture.outbox)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        model.activeRun = try decoder.decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
            "run_id": "run", "status": "running", "workspace": "/tmp/qa", "prompt": "Fixture",
            "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z", "config": [:],
            "synced_back": false, "workspaces_deleted": false, "codex_homes_deleted": false,
            "worker_active": true, "worker_available": true, "event_count": 0, "agents": []]))
        model.selectedWorkspaceID = "workspace"; model.selectedAgent = 1
        model.transcripts[1] = AgentTranscript(sourceRunID: "run", sourceAgent: 1, inputRequests: requests)
        return model
    }

    @Test(arguments: [TranscriptAppearance.dark, .light], [420.0, 980.0])
    func nativeTypingSurvivesADelayedTimelineSnapshot(appearance: TranscriptAppearance, width: Double) async throws {
        let base = request()
        let original = AgentInputRequest(id: base.id, connectionId: base.connectionId, threadId: base.threadId,
            turnId: base.turnId, itemId: base.itemId, isBlocking: false,
            questions: [.init(id: "direction", header: "Question 1", question: "请补充你的看法。",
                options: nil, isOther: true, isSecret: false)], state: "open", delivery: "none",
            answerId: nil, answers: nil, createdAt: 1, updatedAt: 1, kind: "async")
        let fixture = try Fixture(question: original)
        defer { fixture.close() }
        let actions = AgentQuestionActions.answering(using: fixture.outbox, target: .localMachine("machine"),
            workspaceID: "workspace")
        let item = AgentQuestionCollectionItem()
        func configure(_ snapshot: AgentQuestionPresentation, width: CGFloat) {
            item.configure(question: snapshot, language: .chinese, textSize: .standard, accentColor: .orange,
                appearance: appearance, availableWidth: width, actions: actions, heightChanged: { _ in })
        }
        let stale = AgentQuestionPresentation(runID: "run", agent: 1, request: original, command: nil)
        configure(stale, width: width)
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: width, height: 170),
            styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = item.view
        defer { window.contentView = nil; window.close() }
        for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
        func screenshot(_ suffix: String) throws {
            guard let folder = ProcessInfo.processInfo.environment["PCR_QUESTION_QA_OUTPUT"] else { return }
            item.view.displayIfNeeded()
            let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
            item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            #expect(png.count > 2000)
            try png.write(to: URL(fileURLWithPath: folder)
                .appendingPathComponent("question-input-\(appearance.rawValue)-\(Int(width))-\(suffix).png"))
        }
        #expect(actions.answerContext?.current(stale).canSubmit == false)
        try screenshot("empty")
        let field = try #require(descendants(item.view).compactMap { $0 as? NSTextField }.first { $0.isEditable })
        #expect(window.makeFirstResponder(field))
        let editor = try #require(field.currentEditor() as? NSTextView)
        editor.insertText("我还没有输入完成", replacementRange: editor.selectedRange())
        try await until { fixture.outbox.entries.first?.body.answers["direction"]?.answers == ["我还没有输入完成"] }
        let identity = try #require(fixture.outbox.entries.first?.id)
        #expect(actions.answerContext?.current(stale).canSubmit == true)
        for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
        try screenshot("typing")
        configure(AgentQuestionPresentation(runID: "run", agent: 1, request: original,
            command: fixture.outbox.entries.first), width: width)
        for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
        // A captured collection update may arrive after the text setter has committed locally.
        configure(stale, width: width - 20)
        for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
        #expect(editor.string == "我还没有输入完成")
        #expect(field.stringValue == "我还没有输入完成")
        #expect(fixture.outbox.entries.first?.id == identity)
        #expect(fixture.outbox.entries.first?.status == "draft")
        #expect(fixture.peer.answersReceived == 0)
        #expect(!window.isVisible && !window.isKeyWindow)
        await fixture.outbox.flush()
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: fixture.file.url))
        #expect(restored.entries.first?.body.answers["direction"]?.answers == ["我还没有输入完成"])
        try await actions.submit(stale)
        try await until { fixture.outbox.entries.first?.status == "pending" && fixture.outbox.undurableAnswers.isEmpty }
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        #expect(fixture.peer.answersReceived == 1)
        #expect(fixture.outbox.entries.first?.id == identity)
        #expect(fixture.outbox.entries.first?.body.answers["direction"]?.answers == ["我还没有输入完成"])
    }

    @Test func editingMultipleAnswersRetainsSiblingsAndAnIntentionalEmptyDraftAcrossReuse() async throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = AgentAnswerOutbox(file: AgentAnswerFile(url: directory.appendingPathComponent("answers.json")))
        defer { outbox.stop() }
        let original = AgentInputRequest(id: "multi", connectionId: "owner", threadId: "thread", turnId: "turn",
            itemId: "item", isBlocking: false, questions: [
                .init(id: "first", header: "First", question: "First question", options: nil, isOther: true, isSecret: false),
                .init(id: "second", header: "Second", question: "Second question", options: nil, isOther: true, isSecret: false)],
            state: "open", delivery: "none", answerId: nil, answers: nil, createdAt: 1, updatedAt: 1, kind: "async")
        let stale = AgentQuestionPresentation(runID: "run", agent: 1, request: original, command: nil)
        let actions = AgentQuestionActions.answering(using: outbox, target: .localMachine("machine"), workspaceID: "workspace")
        actions.setAnswer(stale, "first", "第一项草稿")
        actions.setAnswer(stale, "second", "第二项草稿")
        let identity = try #require(outbox.entries.first?.id)
        #expect(actions.answerContext?.current(stale).values == ["first": "第一项草稿", "second": "第二项草稿"])
        #expect(actions.answerContext?.current(stale).canSubmit == true)
        actions.setAnswer(stale, "first", "")
        #expect(actions.answerContext?.current(stale).canSubmit == false)
        await outbox.flush()
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: directory.appendingPathComponent("answers.json")))
        let restoredActions = AgentQuestionActions.answering(using: restored, target: .localMachine("machine"), workspaceID: "workspace")
        let restoredPresentation = try #require(restoredActions.answerContext?.current(stale))
        #expect(restored.entries.first?.id == identity && restored.entries.first?.status == "draft")
        #expect(restoredPresentation.values == ["first": "", "second": "第二项草稿"])
        let item = AgentQuestionCollectionItem()
        func configure(_ value: AgentQuestionPresentation) {
            item.configure(question: value, language: .chinese, textSize: .standard, accentColor: .orange,
                appearance: .dark, availableWidth: 640, actions: restoredActions, heightChanged: { _ in })
        }
        configure(stale)
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 640, height: 400),
            styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false; window.contentView = item.view
        defer { window.contentView = nil; window.close() }
        for agent in [1, 2, 1] {
            item.prepareForReuse()
            configure(AgentQuestionPresentation(runID: "run", agent: agent, request: original, command: nil))
            for _ in 0..<4 { item.view.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(25)) }
            let fields = descendants(item.view).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
            #expect(fields.map(\.stringValue) == (agent == 1 ? ["", "第二项草稿"] : ["", ""]))
        }
        #expect(restored.entries.count == 1 && restored.entries.first?.status == "draft")
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func liveQuestionDraftUsesItsFullScopeAndNeverRevivesAHistoricalEditor() async throws {
        let directory = try directory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = AgentAnswerOutbox(file: AgentAnswerFile(url: directory.appendingPathComponent("answers.json")))
        defer { outbox.stop() }
        let original = request()
        let stale = AgentQuestionPresentation(runID: "run", agent: 1, request: original, command: nil)
        for workspace in ["workspace", "other"] {
            outbox.setDraft(request: original, target: .localMachine("machine"), workspaceID: workspace,
                runID: "run", agent: 1, questionID: "direction", value: workspace)
        }
        #expect(outbox.presentation(for: stale, target: .localMachine("machine"), workspaceID: "workspace").values["direction"] == "workspace")
        #expect(outbox.presentation(for: stale, target: .localMachine("machine"), workspaceID: "other").values["direction"] == "other")
        #expect(outbox.presentation(for: stale, target: .localMachine("another-machine"), workspaceID: "workspace").values.isEmpty)
        var historical = stale; historical.editable = false
        let history = outbox.presentation(for: historical, target: .localMachine("machine"), workspaceID: "workspace")
        #expect(!history.editable && history.values["direction"] == "workspace")
        outbox.setDraft(request: original, target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "")
        #expect(outbox.presentation(for: stale, target: .localMachine("machine"), workspaceID: "workspace").values["direction"] == "")
        await outbox.flush()
        #expect(outbox.entries.allSatisfy { $0.status == "draft" })
    }

    @Test func coldClientResumesOnlyCommittedAnswerAndRetainsOriginalCommandIdentity() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        fixture.outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "分析当前误差")
        try await fixture.outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        let identity = try #require(fixture.outbox.entries.first?.id)
        let restored = AgentAnswerOutbox(file: AgentAnswerFile(url: fixture.file.url))
        defer { restored.stop() }
        restored.start(client: fixture.client)
        try await until { restored.entries.first?.status == "accepted" }
        #expect(restored.entries.first?.id == identity)
        #expect(fixture.peer.commands == ["agent-answer-\(identity)"])
        #expect(fixture.peer.allAnswersWereDurable)
    }

    @Test func endedQuestionPreservesChoiceWithoutSendingItToAnotherTurn() async throws {
        let fixture = try Fixture(question: request(state: "invalidated", updated: 2))
        defer { fixture.close() }
        fixture.outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        try await fixture.outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "closed" }
        #expect(fixture.peer.answersReceived == 0)
        #expect(fixture.outbox.entries.first?.body.answers["direction"]?.answers == ["验证简单基线"])
    }

    @Test func temporaryStorageFailureRecoversCommittedChoiceWithoutAnotherConfirmation() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        try FileManager.default.createDirectory(at: fixture.file.url, withIntermediateDirectories: true)
        fixture.outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        do {
            try await fixture.outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
            Issue.record("A failed durable write must not claim success")
        } catch {}
        #expect(fixture.outbox.undurableAnswers.count == 1)
        #expect(fixture.outbox.entries.first?.status == "pending")
        #expect(!fixture.outbox.flushForTermination())
        fixture.outbox.start(client: fixture.client)
        try await Task.sleep(for: .milliseconds(100))
        #expect(fixture.peer.answersReceived == 0)
        try FileManager.default.removeItem(at: fixture.file.url)
        await fixture.outbox.flush()
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        #expect(fixture.peer.answersReceived == 1 && fixture.peer.allAnswersWereDurable)
        #expect(fixture.outbox.undurableAnswers.isEmpty && fixture.outbox.storageError == nil)
    }

    @Test func closingTheLastWindowDoesNotLoseAnUndurableConfirmedChoiceOnQuit() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        try FileManager.default.createDirectory(at: fixture.file.url, withIntermediateDirectories: true)
        fixture.outbox.setDraft(request: request(), target: .localMachine("machine"), workspaceID: "workspace",
            runID: "run", agent: 1, questionID: "direction", value: "验证简单基线")
        do {
            try await fixture.outbox.commit(request: request(), target: .localMachine("machine"), workspaceID: "workspace", runID: "run", agent: 1)
        } catch {}
        let delegate = ParallelCodexAppDelegate()
        delegate.questionAnswers = fixture.outbox
        #expect(delegate.model == nil && delegate.windowModels.count == 0)
        #expect(delegate.applicationShouldTerminate(NSApplication.shared) == .terminateCancel)
        #expect(!delegate.isQuitting && fixture.peer.answersReceived == 0)
        try FileManager.default.removeItem(at: fixture.file.url)
        #expect(fixture.outbox.flushForTermination())
        let saved = try AgentAnswerFile(url: fixture.file.url).load()
        #expect(saved.entries.first?.status == "pending")
        #expect(saved.entries.first?.body.answers["direction"]?.answers == ["验证简单基线"])
    }

    @Test func questionsRenderOffscreenAtNarrowAndWideWidthsWithoutChangingNavigation() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        let model = AppModel(client: fixture.client, windowID: UUID(), questionAnswers: fixture.outbox)
        defer { model.stopWindowObservation() }
        for width in [420.0, 980.0] {
            let view = QuestionFixtureForm(request: request(), outbox: fixture.outbox)
                .environmentObject(model).environment(\.appLanguage, AppLanguage.chinese)
                .environment(\.appTextSize, AppTextSize.standard).padding(20)
                .background(Color(nsColor: .windowBackgroundColor)).preferredColorScheme(.light)
            let host = NSHostingView(rootView: view)
            host.frame = NSRect(x: 0, y: 0, width: width, height: 460)
            let window = NSWindow(contentRect: host.bounds, styleMask: .borderless, backing: .buffered, defer: false)
            window.contentView = host
            host.appearance = NSAppearance(named: .aqua)
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            #expect(png.count > 4000)
            if let output = ProcessInfo.processInfo.environment["PCR_QUESTION_QA_OUTPUT"] {
                try png.write(to: URL(fileURLWithPath: output).appendingPathComponent("questions-\(Int(width)).png"))
            }
            #expect(model.activeRun == nil && model.selectedAgent == nil)
            window.contentView = nil
        }
    }

    @Test func optionalNativeOptionDescriptionsDoNotHideTheEntireQuestion() throws {
        var payload = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(request())) as? [String: Any])
        var questions = try #require(payload["questions"] as? [[String: Any]])
        var options = try #require(questions[0]["options"] as? [[String: Any]])
        options[0].removeValue(forKey: "description")
        questions[0]["options"] = options
        payload["questions"] = questions
        let decoded = try JSONDecoder().decode(AgentInputRequest.self, from: JSONSerialization.data(withJSONObject: payload))
        #expect(decoded.questions[0].options?[0].description == "")
        #expect(decoded.questions[0].options?[0].label == request().questions[0].options?[0].label)
        #expect(decoded.canAnswer)
    }

    @Test func actualRadioSelectionCanAnswerTheOriginalQuestionWithoutAnotherPrompt() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        let model = AppModel(client: fixture.client, windowID: UUID(), questionAnswers: fixture.outbox)
        defer { model.stopWindowObservation() }
        let host = NSHostingView(rootView: QuestionFixtureForm(request: request(), outbox: fixture.outbox)
            .environmentObject(model).environment(\.appLanguage, .chinese).padding(20))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: 380),
            styleMask: .borderless, backing: .buffered, defer: false)
        defer { window.contentView = nil }
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(50))
        host.displayIfNeeded()
        let radios = descendants(host).compactMap { $0 as? NSButton }
        #expect(radios.count == 3)
        #expect(fixture.outbox.entries.isEmpty, "Rendering must not preselect an answer.")
        let choice = try #require(radios.first)
        choice.performClick(nil)
        try await until { fixture.outbox.entries.first?.body.answers["direction"]?.answers == ["验证简单基线"] }
        host.layoutSubtreeIfNeeded()
        #expect(choice.state == .on)
        try await fixture.outbox.commit(request: request(), target: .localMachine("machine"),
            workspaceID: "workspace", runID: "run", agent: 1)
        fixture.outbox.start(client: fixture.client)
        try await until { fixture.outbox.entries.first?.status == "accepted" }
        #expect(fixture.peer.answersReceived == 1 && fixture.peer.allAnswersWereDurable)
        #expect(fixture.peer.actions.allSatisfy { ["runs.question", "runs.answer_question"].contains($0) })
        #expect(!window.isVisible && !window.isKeyWindow)
    }

    @Test func manualWorkRefreshMustPublishQuestionsWithoutWaitingForTheAnswerReader() async throws {
        let fixture = try Fixture(question: request())
        defer { fixture.close() }
        let model = AppModel(client: fixture.client, windowID: UUID(), questionAnswers: fixture.outbox)
        defer { model.stopWindowObservation() }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        model.activeRun = try decoder.decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
            "run_id": "run", "status": "running", "workspace": "/tmp/qa", "prompt": "Fixture",
            "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z", "config": [:],
            "synced_back": false, "workspaces_deleted": false, "codex_homes_deleted": false,
            "worker_active": true, "worker_available": true, "event_count": 0, "agents": [],
        ]))
        model.selectedWorkspaceID = "workspace"
        model.selectedAgent = 1
        model.transcripts[1] = AgentTranscript(sourceRunID: "run", sourceAgent: 1,
            finalMessage: "Existing output", finalMessageHasMore: false)
        #expect(await model.loadAgentDetail(1, maxPages: 1, refreshFromStart: true))
        #expect(model.transcripts[1]?.inputRequests == [request()])
        #expect(model.transcripts[1]?.finalMessage == "Existing output")
        #expect(fixture.peer.actions == ["runs.agent"])
    }

    private func descendants(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(descendants)
    }


    private func until(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(8)
        while !condition() && Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        #expect(condition())
    }

    @MainActor private final class Fixture {
        let directory: URL
        let file: AgentAnswerFile
        let outbox: AgentAnswerOutbox
        let client: PcrdClient
        let peer: Peer
        init(question: AgentInputRequest, loseFirstAnswerReceipt: Bool = false) throws {
            directory = URL(fileURLWithPath: "/tmp").appendingPathComponent("pqq-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            file = AgentAnswerFile(url: directory.appendingPathComponent("outbox.json"))
            outbox = AgentAnswerOutbox(file: file)
            let socket = directory.appendingPathComponent("s"), token = directory.appendingPathComponent("t")
            try Data("question-test-token".utf8).write(to: token)
            client = PcrdClient(socketURL: socket, tokenURL: token)
            peer = try Peer(socket: socket, file: file, question: question, loseFirstAnswerReceipt: loseFirstAnswerReceipt)
        }
        func close() { outbox.stop(); peer.close(); try? FileManager.default.removeItem(at: directory) }
    }

    private final class Peer: @unchecked Sendable {
        private let lock = NSLock()
        private let listener: Int32
        private var worker: Task<Void, Never>?
        private var question: AgentInputRequest
        private let file: AgentAnswerFile
        private var lost: Bool
        private var actionValues: [String] = []
        private var commandValues: [String] = []
        private var durable = true
        var actions: [String] { lock.withLock { actionValues } }
        var commands: [String] { lock.withLock { commandValues } }
        var answersReceived: Int { actions.filter { $0 == "runs.answer_question" }.count }
        var readsReceived: Int { actions.filter { $0 == "runs.question" }.count }
        var allAnswersWereDurable: Bool { lock.withLock { durable } }
        func setQuestion(_ value: AgentInputRequest) { lock.withLock { question = value } }

        init(socket: URL, file: AgentAnswerFile, question: AgentInputRequest, loseFirstAnswerReceipt: Bool) throws {
            self.file = file; self.question = question; lost = loseFirstAnswerReceipt
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let bytes = Array(socket.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { target in bytes.withUnsafeBytes { target.copyBytes(from: $0) } }
            let bound = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            } }
            guard bound == 0 && Darwin.listen(listener, 16) == 0 else { throw CocoaError(.fileReadUnknown) }
            worker = Task.detached { [weak self] in
                while !Task.isCancelled {
                    guard let self else { return }
                    _ = try? await ClientBlockingIO.transport(priority: .utility, isObservation: false) {
                        var polling = pollfd(fd: self.listener, events: Int16(POLLIN), revents: 0)
                        guard Darwin.poll(&polling, 1, 100) > 0 else { return }
                        let socket = Darwin.accept(self.listener, nil, nil)
                        guard socket >= 0 else { return }
                        self.handle(socket)
                        Darwin.close(socket)
                    }
                }
            }
        }
        func close() { worker?.cancel(); Darwin.shutdown(listener, SHUT_RDWR); Darwin.close(listener) }
        private func handle(_ socket: Int32) {
            var data = Data(), bytes = [UInt8](repeating: 0, count: 4096)
            while !data.contains(10) {
                let count = Darwin.read(socket, &bytes, bytes.count)
                guard count > 0 else { return }
                data.append(contentsOf: bytes.prefix(count))
            }
            guard let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let action = request["action"] as? String, let body = request["body"] as? [String: Any] else { return }
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
            let response: Data? = lock.withLock {
                actionValues.append(action)
                durable = durable && (request["client_capabilities"] as? [String]) == ["agent_questions"]
                if action == "runs.answer_question" {
                    commandValues.append(request["command_id"] as? String ?? "")
                    let saved = try? file.load()
                    durable = durable && saved?.entries.first?.status == "pending"
                    let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                    guard let encoded = try? JSONSerialization.data(withJSONObject: body),
                          let answer = try? decoder.decode(AgentQuestionAnswerBody.self, from: encoded) else { return nil }
                    question = AgentInputRequest(id: question.id, connectionId: question.connectionId,
                        threadId: question.threadId, turnId: question.turnId, itemId: question.itemId,
                        isBlocking: question.isBlocking, questions: question.questions, state: "open", delivery: "queued",
                        answerId: answer.answerId, answers: answer.answers, createdAt: 1, updatedAt: 2, kind: question.kind)
                    if lost { lost = false; return nil }
                }
                guard let encoded = try? encoder.encode(question),
                      let record = try? JSONSerialization.jsonObject(with: encoded) else { return nil }
                var result: [String: Any] = ["run_id": "run", "agent": 1, "request": record]
                if action == "runs.agent" {
                    result = ["run_id": "run", "agent": 1, "name": "AGENT-001", "status": "running",
                        "reasoning_token_counts": [:], "rejected": false, "recommended": false, "finalized": false,
                        "stdout_tail": "", "stderr_tail": "", "final_message": "", "input_requests": [record]]
                }
                guard let payload = try? JSONSerialization.data(withJSONObject: ["ok": true, "body": result]) else { return nil }
                return payload + Data([10])
            }
            guard let response else { return }
            var noPipe: Int32 = 1
            setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, &noPipe, socklen_t(MemoryLayout.size(ofValue: noPipe)))
            _ = response.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
        }
    }
}
