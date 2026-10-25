import Foundation

@MainActor
extension AppModel {
    func requestConversationRename(_ conversation: WorkspaceConversationSummary? = nil) {
        guard let conversation = conversation ?? selectedConversation,
              let workspaceID = selectedWorkspaceID else { return }
        conversationRenameRequest = ConversationRenameRequest(
            machineID: currentMachineContextID, workspaceID: workspaceID,
            conversation: conversation
        )
    }

    func renameConversation(_ request: ConversationRenameRequest, title: String) async throws {
        guard let target = requestTarget(for: request.machineID) else {
            throw NSError(domain: "PCR", code: 1, userInfo: [NSLocalizedDescriptionKey:
                appLanguage.text("设备未连接，请连接后重试。", "Connect to the computer and try again.")])
        }
        let capabilities: SystemCapabilities = try await client.call(target: target, "system.capabilities", body: EmptyBody())
        guard capabilities.supports("conversations.rename") else {
            throw NSError(domain: "PCR", code: 2, userInfo: [NSLocalizedDescriptionKey:
                appLanguage.text("请先更新这台设备的 pcrd，才能修改对话名称。", "Update pcrd on this computer to rename conversations.")])
        }
        let conversation = request.conversation
        let response: RenameConversationResponse = try await client.call(
            target: target, "conversations.rename",
            body: RenameConversationBody(
                workspaceId: request.workspaceID, conversationId: conversation.id,
                runId: conversation.latestRunID.isEmpty ? nil : conversation.latestRunID,
                resumeSessionId: conversation.resumeSession?.sessionId,
                title: ConversationTitles.normalized(title)
            ), commandID: UUID().uuidString
        )
        ingestConversationTitles([response.conversation], machineID: request.machineID)
        applyVisibleCustomConversationTitles(machineID: request.machineID, workspaceID: request.workspaceID)
    }

    func ingestConversationTitles(_ records: [ConversationRecord], machineID: String) {
        var changed = false
        for record in records where record.customTitle != nil {
            let key = ConversationTitles.key(machineID: machineID,
                workspaceID: record.workspaceId, conversationID: record.conversationId)
            let current = domainRepositories.conversations.titlesByContext[key]
            guard current == nil || (record.titleRevision ?? 0) > (current?.titleRevision ?? 0) else { continue }
            domainRepositories.conversations.titlesByContext[key] = record
            changed = true
        }
        if changed {
            desktopStateStore.saveConversationTitles(domainRepositories.conversations.titlesByContext)
        }
    }

    func customConversationTitle(
        _ conversation: WorkspaceConversationSummary, workspaceID: String, machineID: String
    ) -> String? {
        let prefix = MachineContextKey.workspace(machineID: machineID, workspaceID: workspaceID) + "\u{1f}"
        let records = domainRepositories.conversations.titlesByContext
            .filter { $0.key.hasPrefix(prefix) }.map(\.value)
        return ConversationTitles.matching(conversation, records: records)?.customTitle
    }

    func applyVisibleCustomConversationTitles(machineID: String, workspaceID: String) {
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspaceID else { return }
        let next = workspaceConversations.map { conversation in
            var updated = conversation
            if let title = customConversationTitle(conversation, workspaceID: workspaceID, machineID: machineID) {
                updated.title = title
            }
            return updated
        }
        if workspaceConversations != next { workspaceConversations = next }
    }

    func refreshConversationTitles(workspaceID: String, target: PcrdRequestTarget) async {
        var cursor: String?
        var seen = Set<String>()
        do {
            repeat {
                try Task.checkCancellation()
                let page: ConversationTitlesPage = try await client.call(target: target,
                    "conversations.list", body: ConversationTitlesBody(workspaceId: workspaceID, cursor: cursor))
                ingestConversationTitles(page.conversations, machineID: target.machineID)
                applyVisibleCustomConversationTitles(machineID: target.machineID, workspaceID: workspaceID)
                guard page.hasMore == true, let next = page.nextCursor, !next.isEmpty,
                      seen.insert(next).inserted else { return }
                cursor = next
            } while !Task.isCancelled
        } catch {
            // Existing cached titles remain readable while the connection recovers.
        }
    }
}
