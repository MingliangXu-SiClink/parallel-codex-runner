import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum FeedbackKind: String, CaseIterable, Identifiable, Sendable {
    case bug
    case feature
    case experience

    var id: String { rawValue }

    var title: String {
        switch self {
        case .bug: return "Problem"
        case .feature: return "Feature"
        case .experience: return "Experience"
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .bug: return language.text("问题", "Problem")
        case .feature: return language.text("功能建议", "Feature")
        case .experience: return language.text("体验建议", "Experience")
        }
    }
}

struct FeedbackAttachmentRecord: Codable, Identifiable, Sendable {
    let attachmentId: String
    let contentType: String
    let sizeBytes: Int
    let sha256: String

    var id: String { attachmentId }
}

struct FeedbackNoteRecord: Codable, Identifiable, Sendable {
    let noteId: String
    let author: String
    let text: String
    let createdAt: String

    var id: String { noteId }
}

struct FeedbackActivityRecord: Codable, Identifiable, Sendable {
    let activityId: String
    let eventType: String
    let actor: String
    let status: String
    let text: String
    let version: String
    let createdAt: String

    var id: String { activityId }
}

struct FeedbackRecord: Codable, Identifiable, Sendable {
    let feedbackId: String
    let kind: String
    let description: String
    let status: String
    let attachments: [FeedbackAttachmentRecord]
    let notes: [FeedbackNoteRecord]
    let activity: [FeedbackActivityRecord]
    let developerReply: String
    let fixedVersion: String
    let createdAt: String
    let updatedAt: String
    let withdrawnAt: String

    var id: String { feedbackId }
}

private struct FeedbackRecordResponse: Decodable, Sendable {
    let feedback: FeedbackRecord
}

private struct FeedbackListResponse: Decodable, Sendable {
    let items: [FeedbackRecord]
    let nextCursor: String
    let hasMore: Bool
}

private struct FeedbackSubmitBody: Encodable, Sendable {
    let clientFeedbackId: String
    let kind: String
    let description: String
    let diagnostics: [String: String]
    let relatedDeviceId: String?
}

private struct FeedbackNoteBody: Encodable, Sendable {
    let clientNoteId: String
    let text: String
}

private struct FeedbackAttachmentBody: Encodable, Sendable {
    let contentType: String
    let data: String
}

@MainActor
final class FeedbackModel: ObservableObject {
    @Published private(set) var items: [FeedbackRecord] = []
    @Published private(set) var pending: [PersistedFeedbackSubmission] = []
    @Published private(set) var loading = false
    @Published private(set) var submitting = false
    @Published private(set) var actioningFeedbackID: String?
    @Published private(set) var hasMore = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var lastQueuedSubmissionID: String?

    private let client: PcrdClient
    private let desktopStateStore: DesktopStateStore
    private var outbox: [String: PersistedFeedbackSubmission]
    private var nextCursor = ""
    private var retryTask: Task<Void, Never>?
    private var sendingIDs: Set<String> = []

    init(
        client: PcrdClient,
        desktopStateStore: DesktopStateStore = .shared,
        initialOutbox: [String: PersistedFeedbackSubmission] = [:]
    ) {
        self.client = client
        self.desktopStateStore = desktopStateStore
        outbox = initialOutbox
        pending = Self.sortedPending(outbox)
        startRetryLoop()
    }

    func mergePersistedOutbox(
        _ persisted: [String: PersistedFeedbackSubmission]
    ) {
        outbox = persisted.merging(
            outbox,
            uniquingKeysWith: { _, current in current }
        )
        pending = Self.sortedPending(outbox)
    }

    func refresh(reset: Bool = true) async {
        if reset {
            loading = true
            errorMessage = nil
        }
        do {
            let cursor = reset ? "" : nextCursor
            let suffix = cursor.isEmpty ? "" : "&cursor=\(cursor)"
            let response: FeedbackListResponse = try await client.relayRequest(
                method: "GET",
                path: "/v1/feedback?limit=30\(suffix)",
                response: FeedbackListResponse.self
            )
            items = reset ? response.items : items + response.items
            nextCursor = response.nextCursor
            hasMore = response.hasMore
            errorMessage = nil
        } catch {
            errorMessage = userMessage(error)
        }
        loading = false
    }

    func submit(
        kind: FeedbackKind,
        description: String,
        screenshotURLs: [URL],
        diagnostics: [String: String]
    ) async {
        let description = description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !description.isEmpty, !submitting else { return }
        submitting = true
        errorMessage = nil
        do {
            let id = UUID().uuidString.lowercased()
            let attachments = try await Task.detached(priority: .userInitiated) {
                try Self.prepareScreenshots(id: id, urls: Array(screenshotURLs.prefix(3)))
            }.value
            let submission = PersistedFeedbackSubmission(
                id: id,
                kind: kind.rawValue,
                description: String(description.prefix(8_000)),
                diagnostics: diagnostics,
                attachments: attachments,
                remoteFeedbackID: "",
                status: .pending,
                error: ""
            )
            outbox[id] = submission
            persistOutbox()
            lastQueuedSubmissionID = id
            submitting = false
            Task { [weak self] in
                await self?.send(id)
                self?.startRetryLoop()
            }
        } catch {
            submitting = false
            errorMessage = userMessage(error)
        }
    }

    func retry(_ id: String) {
        guard var submission = outbox[id] else { return }
        submission.status = .pending
        submission.error = ""
        outbox[id] = submission
        persistOutbox()
        startRetryLoop()
    }

    func removePending(_ id: String) {
        guard let submission = outbox.removeValue(forKey: id) else { return }
        persistOutbox()
        Self.deleteSubmissionFiles(id: id)
        if !submission.remoteFeedbackID.isEmpty {
            Task { [weak self] in
                await self?.withdrawSubmittedDuringCancellation(
                    submission.remoteFeedbackID
                )
            }
        }
    }

    func addNote(feedbackID: String, text: String) async {
        let note = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !note.isEmpty, actioningFeedbackID == nil else { return }
        actioningFeedbackID = feedbackID
        do {
            let clientNoteID = "feedback-note-\(feedbackID)-\(UUID().uuidString.lowercased())"
            let response: FeedbackRecordResponse = try await client.relayRequest(
                method: "POST",
                path: "/v1/feedback/\(feedbackID)/notes",
                body: FeedbackNoteBody(
                    clientNoteId: clientNoteID,
                    text: String(note.prefix(4_000))
                ),
                response: FeedbackRecordResponse.self
            )
            replace(response.feedback)
            errorMessage = nil
        } catch {
            errorMessage = userMessage(error)
        }
        actioningFeedbackID = nil
    }

    func withdraw(feedbackID: String) async {
        guard actioningFeedbackID == nil else { return }
        actioningFeedbackID = feedbackID
        do {
            let response: FeedbackRecordResponse = try await client.relayRequest(
                method: "POST",
                path: "/v1/feedback/\(feedbackID)/withdraw",
                response: FeedbackRecordResponse.self
            )
            replace(response.feedback)
            errorMessage = nil
        } catch {
            errorMessage = userMessage(error)
        }
        actioningFeedbackID = nil
    }

    private func startRetryLoop() {
        guard retryTask == nil,
              outbox.values.contains(where: { $0.status != .failed }) else { return }
        retryTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.drainOutboxOnce()
                guard self.outbox.values.contains(where: { $0.status != .failed }) else {
                    break
                }
                try? await Task.sleep(for: .seconds(30))
            }
            self.retryTask = nil
        }
    }

    private func drainOutboxOnce() async {
        let ids = outbox.values
            .filter { $0.status != .failed }
            .map(\.id)
            .sorted()
        for id in ids where !Task.isCancelled {
            await send(id)
        }
    }

    private func send(_ id: String) async {
        guard var submission = outbox[id], sendingIDs.insert(id).inserted else { return }
        defer { sendingIDs.remove(id) }
        submission.status = .retrying
        submission.error = ""
        outbox[id] = submission
        persistOutbox()
        do {
            var remote: FeedbackRecord
            if submission.remoteFeedbackID.isEmpty {
                let relatedDeviceID = submission.diagnostics["mac_device_id"]
                do {
                    remote = try await submitFeedback(
                        submission,
                        relatedDeviceID: relatedDeviceID
                    )
                } catch let error as PcrdClientError
                    where error.relayHTTPStatus == 403
                        && relatedDeviceID?.isEmpty == false
                        && error.localizedDescription.localizedCaseInsensitiveContains(
                            "related device is not available"
                        ) {
                    // Feedback belongs to the product, not to the machine that
                    // happened to be selected when it was written. Preserve the
                    // report if that optional diagnostic context was revoked.
                    remote = try await submitFeedback(
                        submission,
                        relatedDeviceID: nil
                    )
                }
                guard var current = outbox[id] else {
                    await withdrawSubmittedDuringCancellation(remote.feedbackId)
                    return
                }
                current.remoteFeedbackID = remote.feedbackId
                current.status = .retrying
                current.error = ""
                submission = current
                outbox[id] = current
                persistOutbox()
            } else {
                remote = items.first { $0.feedbackId == submission.remoteFeedbackID }
                    ?? FeedbackRecord(
                        feedbackId: submission.remoteFeedbackID,
                        kind: submission.kind,
                        description: submission.description,
                        status: "submitted",
                        attachments: [],
                        notes: [],
                        activity: [],
                        developerReply: "",
                        fixedVersion: "",
                        createdAt: "",
                        updatedAt: "",
                        withdrawnAt: ""
                    )
            }
            var uploaded = Set(remote.attachments.map(\.sha256))
            for attachment in submission.attachments where !uploaded.contains(attachment.sha256) {
                guard outbox[id] != nil else {
                    await withdrawSubmittedDuringCancellation(remote.feedbackId)
                    return
                }
                let content = try Self.attachmentData(submissionID: id, attachment: attachment)
                let response: FeedbackRecordResponse = try await client.relayRequest(
                    method: "POST",
                    path: "/v1/feedback/\(submission.remoteFeedbackID)/attachments",
                    body: FeedbackAttachmentBody(
                        contentType: attachment.contentType,
                        data: content.base64EncodedString()
                    ),
                    response: FeedbackRecordResponse.self
                )
                remote = response.feedback
                uploaded.insert(attachment.sha256)
            }
            guard outbox[id] != nil else {
                await withdrawSubmittedDuringCancellation(remote.feedbackId)
                return
            }
            outbox.removeValue(forKey: id)
            persistOutbox()
            Self.deleteSubmissionFiles(id: id)
            replace(remote)
            errorMessage = nil
        } catch {
            guard var current = outbox[id] else { return }
            current.status = isPermanent(error) ? .failed : .retrying
            current.error = String(userMessage(error).prefix(500))
            outbox[id] = current
            persistOutbox()
        }
    }

    private func submitFeedback(
        _ submission: PersistedFeedbackSubmission,
        relatedDeviceID: String?
    ) async throws -> FeedbackRecord {
        let response: FeedbackRecordResponse = try await client.relayRequest(
            method: "POST",
            path: "/v1/feedback",
            body: FeedbackSubmitBody(
                clientFeedbackId: submission.id,
                kind: submission.kind,
                description: submission.description,
                diagnostics: submission.diagnostics,
                relatedDeviceId: relatedDeviceID
            ),
            response: FeedbackRecordResponse.self
        )
        return response.feedback
    }

    private func withdrawSubmittedDuringCancellation(_ feedbackID: String) async {
        guard !feedbackID.isEmpty else { return }
        let response: FeedbackRecordResponse? = try? await client.relayRequest(
            method: "POST",
            path: "/v1/feedback/\(feedbackID)/withdraw",
            response: FeedbackRecordResponse.self
        )
        if let response {
            replace(response.feedback)
        }
    }

    private func replace(_ feedback: FeedbackRecord) {
        if let index = items.firstIndex(where: { $0.feedbackId == feedback.feedbackId }) {
            items[index] = feedback
        } else {
            items.insert(feedback, at: 0)
        }
    }

    private func persistOutbox() {
        self.desktopStateStore.saveFeedbackOutbox(outbox)
        pending = Self.sortedPending(outbox)
    }

    private func isPermanent(_ error: Error) -> Bool {
        if let code = (error as? PcrdClientError)?.relayHTTPStatus {
            return [400, 401, 403, 404, 413, 422].contains(code)
        }
        guard case PcrdClientError.remote(let code, _) = error else { return false }
        return [
            "feedback_error", "invalid_argument", "unsupported_action",
            "incompatible_protocol", "not_allowed",
        ].contains(code)
    }

    private func userMessage(_ error: Error) -> String {
        if let code = (error as? PcrdClientError)?.relayHTTPStatus {
            switch code {
            case 401, 403:
                return "Reconnect this app to the server before sending feedback."
            case 429:
                return "Several reports were sent recently. Try again shortly."
            case 400..<500:
                return "This feedback could not be sent. Review it and try again."
            default:
                return "Feedback is temporarily unavailable. Saved feedback will retry automatically."
            }
        }
        if case PcrdClientError.remote(let code, _) = error,
           code == "unsupported_action" || code == "unknown_action" {
            return "Update Parallel Codex on the connected computer to use feedback."
        }
        let message = error.localizedDescription
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return message.isEmpty ? "Feedback is unavailable. Try again shortly." : message
    }

    private static func sortedPending(
        _ values: [String: PersistedFeedbackSubmission]
    ) -> [PersistedFeedbackSubmission] {
        values.values.sorted { $0.id > $1.id }
    }

    private nonisolated static func prepareScreenshots(
        id: String,
        urls: [URL]
    ) throws -> [PersistedFeedbackAttachment] {
        guard !urls.isEmpty else { return [] }
        let directory = feedbackOutboxDirectory.appendingPathComponent(id, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        do {
            return try urls.enumerated().map { index, url in
                guard let data = compressedJPEG(url: url) else {
                    throw FeedbackFileError("Screenshot \(index + 1) could not be read.")
                }
                guard data.count <= 512 * 1024 else {
                    throw FeedbackFileError("Screenshot \(index + 1) is too large. Crop it and try again.")
                }
                let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                let fileName = "\(digest).jpg"
                let target = directory.appendingPathComponent(fileName)
                try data.write(to: target, options: [.atomic, .completeFileProtection])
                try FileManager.default.setAttributes(
                    [.posixPermissions: 0o600],
                    ofItemAtPath: target.path
                )
                return PersistedFeedbackAttachment(
                    fileName: fileName,
                    contentType: "image/jpeg",
                    sha256: digest,
                    sizeBytes: data.count
                )
            }
        } catch {
            deleteSubmissionFiles(id: id)
            throw error
        }
    }

    private nonisolated static func compressedJPEG(url: URL) -> Data? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        for edge in [1_600, 1_200, 900] {
            let options = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: edge,
            ] as CFDictionary
            guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options) else {
                continue
            }
            for quality in stride(from: 0.86, through: 0.46, by: -0.08) {
                let output = NSMutableData()
                guard let destination = CGImageDestinationCreateWithData(
                    output,
                    UTType.jpeg.identifier as CFString,
                    1,
                    nil
                ) else { continue }
                CGImageDestinationAddImage(
                    destination,
                    image,
                    [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
                )
                guard CGImageDestinationFinalize(destination) else { continue }
                let data = output as Data
                if data.count <= 512 * 1024 { return data }
            }
        }
        return nil
    }

    private nonisolated static func attachmentData(
        submissionID: String,
        attachment: PersistedFeedbackAttachment
    ) throws -> Data {
        let directory = feedbackOutboxDirectory
            .appendingPathComponent(submissionID, isDirectory: true)
            .standardizedFileURL
        let file = directory.appendingPathComponent(attachment.fileName).standardizedFileURL
        guard file.deletingLastPathComponent() == directory,
              !attachment.fileName.contains("/"),
              !attachment.fileName.contains("\\") else {
            throw FeedbackFileError("A saved screenshot path is invalid.")
        }
        let data = try Data(contentsOf: file)
        guard data.count == attachment.sizeBytes else {
            throw FeedbackFileError("A saved screenshot is incomplete.")
        }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard digest == attachment.sha256 else {
            throw FeedbackFileError("A saved screenshot failed its integrity check.")
        }
        return data
    }

    private nonisolated static func deleteSubmissionFiles(id: String) {
        let directory = feedbackOutboxDirectory.appendingPathComponent(id, isDirectory: true)
        try? FileManager.default.removeItem(at: directory)
    }

    private nonisolated static var feedbackOutboxDirectory: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        return base
            .appendingPathComponent("Parallel Codex", isDirectory: true)
            .appendingPathComponent("feedback-outbox", isDirectory: true)
    }
}

private struct FeedbackFileError: LocalizedError, Sendable {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}
