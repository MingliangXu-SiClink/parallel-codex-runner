import Foundation

@MainActor
extension AppModel {
    func updatePrompt(_ value: String) {
        workspaceEntryDefaultNavigationRequestID = nil
        prompt = value
        promptHistoryDraft = value
        promptHistoryIndex = promptHistoryEntries.count
        rememberPromptDraft()
    }

    func navigatePromptHistory(_ direction: Int) {
        workspaceEntryDefaultNavigationRequestID = nil
        guard !promptHistoryEntries.isEmpty else { return }
        if promptHistoryIndex == promptHistoryEntries.count {
            promptHistoryDraft = prompt
        } else if prompt != promptHistoryEntries[promptHistoryIndex] {
            promptHistoryDraft = prompt
            promptHistoryIndex = promptHistoryEntries.count
        }

        if direction < 0 {
            promptHistoryIndex = max(0, promptHistoryIndex - 1)
        } else if promptHistoryIndex < promptHistoryEntries.count {
            promptHistoryIndex += 1
        }
        prompt = promptHistoryIndex == promptHistoryEntries.count
            ? promptHistoryDraft
            : promptHistoryEntries[promptHistoryIndex]
        rememberPromptDraft()
    }

    func rememberPromptDraft() {
        guard !promptDraftContext.isEmpty else { return }
        if prompt.isEmpty {
            promptDraftsByContext.removeValue(forKey: promptDraftContext)
        } else {
            promptDraftsByContext[promptDraftContext] = prompt
        }
        let snapshot = promptDraftsByContext
        promptDraftSaveTask?.cancel()
        promptDraftSaveTask = Task {
            do {
                try await Task.sleep(for: .milliseconds(250))
            } catch {
                return
            }
            guard !Task.isCancelled else { return }
            self.desktopStateStore.savePromptDrafts(snapshot)
            self.desktopStateStore.savePromptSelections(self.promptSelectionsByContext)
        }
    }

    /// Commits the visible draft before the app or composer context exits.
    /// The store write is queued; the application delegate drains that queue
    /// immediately afterward during termination.
    func flushPromptDraftPersistence() {
        promptDraftSaveTask?.cancel()
        promptDraftSaveTask = nil
        guard !promptDraftContext.isEmpty else { return }
        if prompt.isEmpty {
            promptDraftsByContext.removeValue(forKey: promptDraftContext)
        } else {
            promptDraftsByContext[promptDraftContext] = prompt
        }
        self.desktopStateStore.savePromptDrafts(promptDraftsByContext)
        self.desktopStateStore.savePromptSelections(promptSelectionsByContext)
    }

    /// Ends the current composer context before changing machine or workspace.
    /// The old draft is persisted synchronously, then removed from the visible
    /// composer so it can never flash inside the destination conversation.
    func suspendPromptHistoryContext() {
        NativeMultilineEditorCommitRegistry.shared.commitPendingText()
        flushPromptDraftPersistence()
        promptHistoryContext = ""
        promptDraftContext = ""
        promptHistoryEntries = []
        promptHistoryDraft = ""
        promptHistoryIndex = 0
        prompt = ""
    }

    func importPromptFile(_ url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }
        do {
            updatePrompt(try String(contentsOf: url, encoding: .utf8))
        } catch {
            errorMessage = appLanguage.text(
                "所选文件不是可读取的 UTF-8 文本。",
                "The selected prompt file is not readable UTF-8 text."
            )
        }
    }

    func currentPromptHistoryContext() -> String {
        guard let workspaceID = selectedWorkspaceID else { return "" }
        return machineSessionContextKey(
            workspaceID: workspaceID,
            sessionID: settings.resumeSessionID
        )
    }

    func currentPromptDraftContext() -> String {
        guard let workspaceID = selectedWorkspaceID,
              let conversationID = selectedConversationID,
              !conversationID.isEmpty else { return "" }
        return machineConversationContextKey(
            workspaceID: workspaceID,
            conversationID: conversationID
        )
    }

    @discardableResult
    func preparePromptHistoryContext() -> String {
        let nextContext = currentPromptHistoryContext()
        let nextDraftContext = currentPromptDraftContext()
        let historyChanged = nextContext != promptHistoryContext
        let draftChanged = nextDraftContext != promptDraftContext
        guard historyChanged || draftChanged else { return nextContext }

        NativeMultilineEditorCommitRegistry.shared.commitPendingText()
        queueIssue = nil
        queueEditRequest = nil

        if draftChanged, !promptDraftContext.isEmpty {
            if prompt.isEmpty {
                promptDraftsByContext.removeValue(forKey: promptDraftContext)
            } else {
                promptDraftsByContext[promptDraftContext] = prompt
            }
            self.desktopStateStore.savePromptDrafts(promptDraftsByContext)
            self.desktopStateStore.savePromptSelections(promptSelectionsByContext)
        }

        if historyChanged {
            promptHistoryContext = nextContext
            promptHistoryEntries = []
            promptHistoryIndex = 0
        }

        if draftChanged {
            promptDraftContext = nextDraftContext
            let legacyContext = nextContext
            let migratedDraft: String?
            if nextDraftContext.isEmpty {
                migratedDraft = nil
            } else if let draft = promptDraftsByContext[nextDraftContext] {
                migratedDraft = draft
            } else if !legacyContext.isEmpty,
                      let draft = promptDraftsByContext.removeValue(forKey: legacyContext) {
                promptDraftsByContext[nextDraftContext] = draft
                self.desktopStateStore.savePromptDrafts(promptDraftsByContext)
                migratedDraft = draft
            } else {
                migratedDraft = nil
            }
            promptHistoryDraft = migratedDraft ?? ""
            prompt = promptHistoryDraft
        }
        return nextContext
    }

    func loadPromptHistory() async {
        guard let workspaceID = selectedWorkspaceID else {
            promptHistoryEntries = []
            promptHistoryIndex = 0
            return
        }
        let target = client.currentTarget()
        let nextContext = preparePromptHistoryContext()
        do {
            let response: PromptHistoryResponse = try await client.call(
                target: target,
                "prompt_history.list",
                body: PromptHistoryListBody(
                    workspaceId: workspaceID,
                    sessionId: usableResumeSessionID(settings.resumeSessionID)
                )
            )
            guard nextContext == currentPromptHistoryContext(),
                  promptHistoryContext == nextContext else { return }
            promptHistoryEntries = response.entries
        } catch {
            guard nextContext == currentPromptHistoryContext(),
                  promptHistoryContext == nextContext else { return }
            promptHistoryEntries = []
        }
        promptHistoryIndex = promptHistoryEntries.count
        promptHistoryDraft = prompt
    }

    func recordPromptHistory(
        _ value: String,
        workspaceID explicitWorkspaceID: String? = nil,
        sessionID explicitSessionID: String? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async {
        guard let workspaceID = explicitWorkspaceID ?? selectedWorkspaceID else { return }
        let target = explicitTarget ?? client.currentTarget()
        let sessionID = explicitSessionID
            ?? (target.machineID == currentMachineContextID && workspaceID == selectedWorkspaceID
                ? settings.resumeSessionID
                : "")
        let context = machineSessionContextKey(
            workspaceID: workspaceID,
            sessionID: sessionID,
            machineID: target.machineID
        )
        do {
            let response: PromptHistoryResponse = try await client.call(
                target: target,
                "prompt_history.append",
                body: PromptHistoryAppendBody(
                    workspaceId: workspaceID,
                    sessionId: usableResumeSessionID(sessionID),
                    prompt: value
                )
            )
            guard target.machineID == currentMachineContextID,
                  context == currentPromptHistoryContext() else { return }
            promptHistoryEntries = response.entries
            promptHistoryIndex = response.entries.count
            promptHistoryDraft = prompt
        } catch {
            // A history write should never make an otherwise valid run fail.
        }
    }
}
