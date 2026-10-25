import Foundation

@MainActor
extension AppModel {
    func loadPrimaryModelOptions() async {
        let target = client.currentTarget()
        let context = primaryModelOptionsContext
        loadingPrimaryModelOptionsContexts.insert(context)
        defer { loadingPrimaryModelOptionsContexts.remove(context) }
        do {
            _ = try await modelOptionsCache.refresh(context: context) { [client] in
                try await client.call(
                    target: target, "codex.model_options",
                    body: ModelOptionsBody(
                        model: context.model.isEmpty ? nil : context.model,
                        effort: nil
                    )
                )
            }
            guard primaryModelOptionsContext == context else { return }
            modelOptionsRevision &+= 1
            setModelOptionsError("Primary", error: nil)
        } catch {
            guard primaryModelOptionsContext == context else { return }
            setModelOptionsError("Primary", error: error)
        }
    }

    func loadSynthesisModelOptions() async {
        let target = client.currentTarget()
        let context = synthesisModelOptionsContext
        do {
            _ = try await modelOptionsCache.refresh(context: context) { [client] in
                try await client.call(
                    target: target, "codex.model_options",
                    body: ModelOptionsBody(
                        model: context.model.isEmpty ? nil : context.model,
                        effort: nil
                    )
                )
            }
            guard synthesisModelOptionsContext == context else { return }
            modelOptionsRevision &+= 1
            setModelOptionsError("Synthesis", error: nil)
        } catch {
            guard synthesisModelOptionsContext == context else { return }
            setModelOptionsError("Synthesis", error: error)
        }
    }

    func reloadModelOptions() async {
        modelOptionErrors.removeAll()
        modelOptionsError = nil
        async let primary: Void = loadPrimaryModelOptions()
        async let synthesis: Void = loadSynthesisModelOptions()
        _ = await (primary, synthesis)
    }

    private func setModelOptionsError(_ stage: String, error: Error?) {
        if let error {
            modelOptionErrors[stage] = appLanguage.userMessage(for: error)
        } else {
            modelOptionErrors.removeValue(forKey: stage)
        }
        let message = modelOptionErrors
            .sorted { $0.key < $1.key }
            .map {
                let label = appLanguage.text(
                    $0.key == "Primary" ? "主 Agent" : "综合 Agent",
                    $0.key
                )
                return "\(label): \($0.value)"
            }
            .joined(separator: "  ")
        modelOptionsError = message.isEmpty ? nil : message
    }

    func loadResumeSessions(reportErrors: Bool = false) async {
        guard let workspaceID = selectedWorkspaceID else { return }
        let target = client.currentTarget()
        let generation = workspaceContextGeneration
        let includeNonInteractive = settings.resumeIncludeNonInteractive
        do {
            let response: ResumeSessionList = try await client.call(
                target: target,
                "codex.resume_sessions",
                body: ResumeSessionsBody(
                    workspaceId: workspaceID,
                    includeNonInteractive: includeNonInteractive,
                    limit: 100
                )
            )
            guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                  settings.resumeIncludeNonInteractive == includeNonInteractive else { return }
            resumeSessions = response.sessions
        } catch {
            guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                  settings.resumeIncludeNonInteractive == includeNonInteractive else { return }
            resumeSessions = []
            if reportErrors {
                let detail = appLanguage.userMessage(for: error)
                errorMessage = appLanguage.text(
                    "无法加载历史对话。\(detail)",
                    "The previous conversations could not be loaded. \(detail)"
                )
            }
        }
    }

    func loadResumeHistory(
        _ sessionID: String,
        reportErrors: Bool = false
    ) async {
        resumeHistoryRequestID += 1
        let requestID = resumeHistoryRequestID
        guard let workspaceID = selectedWorkspaceID,
              let resolvedSessionID = usableResumeSessionID(sessionID) else {
            resumeHistory = []
            return
        }
        let generation = workspaceContextGeneration
        let target = client.currentTarget()
        let cacheKey = resumeConversationViewKey(
            machineID: target.machineID,
            workspaceID: workspaceID,
            sessionID: resolvedSessionID
        )
        var resolvedConversationTitle = selectedConversation.flatMap { conversation in
            conversation.resumeSession?.sessionId == resolvedSessionID
                ? conversation.title
                : nil
        }
        var resolvedConversationUpdatedAt = selectedConversation.flatMap { conversation in
            conversation.resumeSession?.sessionId == resolvedSessionID
                ? conversation.resumeSession?.updatedAt
                : nil
        }
        do {
            if let cached = await runViewStateStore.hydrate(for: cacheKey),
               requestID == resumeHistoryRequestID,
               isCurrentWorkspaceContext(workspaceID, generation: generation) {
                resumeHistory = mergeConversationHistory(
                    current: resumeHistory,
                    authoritative: cached.snapshot.conversationHistory
                )
            }
            var cursor = 0
            var entries: [ResumeHistoryEntry] = []
            while true {
                let page: ResumeHistoryPage = try await client.call(
                    target: target,
                    "codex.resume_history",
                    body: ResumeHistoryBody(
                        workspaceId: workspaceID,
                        sessionId: resolvedSessionID,
                        cursor: cursor,
                        limit: 100,
                        maxBytes: 512 * 1024,
                        conversationItemsOnly: true
                    )
                )
                if let session = page.session,
                   session.sessionId == resolvedSessionID {
                    resolvedConversationTitle = session.displayTitle
                    resolvedConversationUpdatedAt = session.updatedAt
                }
                entries.append(contentsOf: page.entries)
                guard requestID == resumeHistoryRequestID,
                      isCurrentWorkspaceContext(
                        workspaceID,
                        generation: generation
                      ) else { return }
                resumeHistory = mergeConversationHistory(
                    current: resumeHistory,
                    authoritative: entries
                )
                runViewStateStore.put(
                    WorkspaceRunViewSnapshot(
                        selectedAgent: nil,
                        transcripts: [:],
                        conversationHistory: resumeHistory,
                        conversationTitle: resolvedConversationTitle
                            ?? cachedConversationTitle(from: resumeHistory),
                        conversationUpdatedAt: resolvedConversationUpdatedAt
                    ),
                    for: cacheKey
                )
                guard page.hasMore, page.nextCursor > cursor else { break }
                cursor = page.nextCursor
            }
            guard requestID == resumeHistoryRequestID,
                  isCurrentWorkspaceContext(workspaceID, generation: generation) else { return }
            resumeHistory = mergeConversationHistory(
                current: resumeHistory,
                authoritative: entries
            )
            runViewStateStore.put(
                WorkspaceRunViewSnapshot(
                    selectedAgent: nil,
                    transcripts: [:],
                    conversationHistory: resumeHistory,
                    conversationTitle: resolvedConversationTitle
                        ?? cachedConversationTitle(from: resumeHistory),
                    conversationUpdatedAt: resolvedConversationUpdatedAt
                ),
                for: cacheKey
            )
        } catch {
            guard requestID == resumeHistoryRequestID,
                  isCurrentWorkspaceContext(workspaceID, generation: generation) else { return }
            if reportErrors {
                errorNotice = .selectedConversationHistory(error.localizedDescription)
            }
        }
    }

    func loadConversationHistory(
        runID: String?,
        sessionID: String
    ) async {
        if let runID, !runID.isEmpty {
            await loadRunHistory(runID)
        } else {
            await loadResumeHistory(sessionID)
        }
    }

    func loadRunHistory(
        _ runID: String,
        reportErrors: Bool = false,
        waitForCompletion: Bool = true
    ) async {
        resumeHistoryRequestID += 1
        let requestID = resumeHistoryRequestID
        guard let workspaceID = selectedWorkspaceID, !runID.isEmpty else {
            resumeHistory = []
            return
        }
        let generation = workspaceContextGeneration
        let target = client.currentTarget()
        let key = WorkspaceRunViewKey(machineID: target.machineID, workspaceID: workspaceID, runID: runID)
        let cached = await runViewStateStore.hydrate(for: key)
        guard requestID == resumeHistoryRequestID, isCurrentMachine(target.machineID),
              isCurrentWorkspaceContext(workspaceID, generation: generation), activeRun?.runId == runID,
              let run = activeRun else { return }
        if let cached {
            resumeHistory = mergeConversationHistory(current: resumeHistory,
                authoritative: cached.snapshot.conversationHistory)
        }
        scheduleConversationHistoryRead(key: key, run: run, target: target)
        guard waitForCompletion else { return }
        let error = await conversationHistoryTasks[key]?.value
        guard requestID == resumeHistoryRequestID, isCurrentMachine(target.machineID),
              isCurrentWorkspaceContext(workspaceID, generation: generation), activeRun?.runId == runID else { return }
        if reportErrors, let error {
            errorNotice = .previousConversationHistory(error)
        }
    }

    func ensureVisibleConversationHistory() async {
        guard resumeHistory.isEmpty,
              let runID = activeRun?.runId,
              !runID.isEmpty else { return }
        await loadRunHistory(runID)
    }

    func currentConversationHistory() -> [ResumeHistoryEntry] {
        conversationHistory(for: selectedAgent)
    }

    func conversationHistory(for agent: Int?) -> [ResumeHistoryEntry] {
        guard let activeRun else { return resumeHistory }
        let transcript = agent.flatMap { transcripts[$0] } ?? AgentTranscript()
        return branchConversationHistory(
            history: resumeHistory,
            prompt: activeRun.prompt,
            transcript: transcript
        )
    }
}
