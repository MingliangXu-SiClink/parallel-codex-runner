import Foundation

@MainActor
extension AppModel {
    func loadSelectedWorkspaceContext() async {
        cacheVisibleRunViewState()
        guard let workspace = selectedWorkspace else {
            resetRunView()
            workspaceContextLoadingID = nil
            loadedWorkspaceID = nil
            return
        }
        let generation = workspaceContextGeneration
        let navigationRequestID = conversationNavigationRequestID
        let workspaceID = workspace.workspaceId
        let machineID = currentMachineContextID
        let target = client.currentTarget()
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspaceID,
            machineID: machineID
        )
        loadedWorkspaceID = workspace.workspaceId
        // Warm content is readable while authoritative context checks continue.
        // Only a cold surface needs the blocking hydration state.
        workspaceContextLoadingID = activeRun != nil || !resumeHistory.isEmpty ? nil : workspaceID
        let latestRunID = workspace.lastRunId
        let latestRunTask: Task<RunSummary?, Never>? = latestRunID.isEmpty
            ? nil
            : Task {
                try? await client.call(
                    target: target,
                    "turns.get",
                    body: GetRunBody(
                        runId: latestRunID,
                        includeEvents: false,
                        cursor: 0,
                        eventLimit: 1
                    )
                )
            }
        defer {
            latestRunTask?.cancel()
            if isCurrentMachine(machineID),
               isCurrentWorkspaceContext(workspaceID, generation: generation),
               workspaceContextLoadingID == workspaceID {
                workspaceContextLoadingID = nil
            }
        }
        let restoredSubmission = promptOutboxByWorkspace[workspaceKey]
        let restoredAcceptedResult = acceptedPromptResultsByWorkspace[workspaceKey]
        restoreVisiblePromptSubmission()
        let cachedSettings = settingsByWorkspace[workspaceKey]
        let pendingFields = dirtySettingsFieldsByWorkspace[workspaceKey] ?? []
        settings = resolvedWorkspaceSettings(
            cached: cachedSettings,
            remote: workspace.settings,
            preserving: pendingFields
        )
        await restoreCachedWorkspaceConversation(
            workspace: workspace,
            machineID: machineID,
            generation: generation
        )
        guard isCurrentWorkspaceContext(workspaceID, generation: generation),
              conversationNavigationRequestID == navigationRequestID else {
            return
        }
        await loadWorkspaceConversationIndex(
            workspace: workspace,
            target: target,
            generation: generation
        )
        guard isCurrentWorkspaceContext(workspaceID, generation: generation),
              conversationNavigationRequestID == navigationRequestID else {
            return
        }
        if isComposingNewConversation {
            settings.resumeSessionID = "NO"
        } else if let session = selectedConversation?.resumeSession,
                  selectedConversation?.opensResumeHistory == true {
            settings.resumeSessionID = session.sessionId
        }
        let visibleRunID = selectedConversation?.opensResumeHistory == true
            ? ""
            : (selectedConversation?.latestRunID
                ?? (isComposingNewConversation ? "" : workspace.lastRunId))
        func acceptsVisibleRun() -> Bool {
            guard conversationNavigationRequestID == navigationRequestID else { return false }
            if selectedConversationID == nil, conversationNavigationTarget == nil {
                // The exact workspace task can bootstrap even if its index is
                // temporarily unavailable. A later explicit selection wins.
                return selectedWorkspace?.lastRunId == visibleRunID
            }
            return navigationAcceptsRun(visibleRunID, requestID: navigationRequestID)
        }
        if runViewCacheNeedsHydration(
            activeRunID: activeRun?.runId,
            requestedRunID: visibleRunID
        ) {
            cacheVisibleRunViewState()
            clearVisibleConversationProjection(invalidateRequests: false)
        }
        let needsCachedBody = activeRun?.runId != visibleRunID
            || (transcripts.isEmpty && resumeHistory.isEmpty && activeRun?.agents.isEmpty == false)
        if !visibleRunID.isEmpty, needsCachedBody {
            workspaceContextLoadingID = workspaceID
        }

        if !visibleRunID.isEmpty, needsCachedBody {
            let cacheKey = WorkspaceRunViewKey(
                machineID: machineID,
                workspaceID: workspaceID,
                runID: visibleRunID
            )
            var installedCachedProjection = false
            if let cached = await runViewStateStore.hydrate(for: cacheKey),
               let storedRun = cached.runSummary {
                guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                      acceptsVisibleRun(),
                      selectedConversation?.latestRunID == visibleRunID
                        || (selectedConversation == nil && workspace.lastRunId == visibleRunID)
                else { return }
                var cachedRun = currentReadRun(cacheKey, fallback: storedRun)
                cachedRun.reconcileTerminalAgentStates()
                let snapshot = currentReadSnapshot(cacheKey, fallback: cached.snapshot)
                activeRun = cachedRun
                runSummariesByContext[cacheKey] = cachedRun
                transcripts = snapshot.transcripts
                resumeHistory = snapshot.conversationHistory
                let selectionKey = machineRunContextKey(
                    workspaceID: workspaceID,
                    runID: visibleRunID,
                    machineID: machineID
                )
                selectedAgent = selectedAgentForRestoredRun(
                    explicitSelection: rememberedSelectedAgent(
                        for: cachedRun,
                        contextKey: selectionKey
                    ),
                    cachedSelection: snapshot.selectedAgent,
                    run: cachedRun
                )
                refreshVisibleRunOperationState()
                restoreCachedFollowUpQueue(workspaceID: workspaceID, runID: visibleRunID)
                installedCachedProjection = true
            }
            if !installedCachedProjection,
               let conversation = selectedConversation,
               conversation.latestRunID == visibleRunID {
                // The conversation index is enough for a coherent first
                // frame. Keep its prompt and Agent selector visible while the
                // authoritative Turn and transcript pages arrive.
                _ = installIndexedConversationProjection(
                    conversation,
                    workspace: workspace
                )
            }
        }

        // Preserve local edits that have not reached pcrd yet, including an
        // explicit choice to start without a resumed conversation.
        let resumeDisabled = settings.resumeSessionID.uppercased() == "NO"

        if !visibleRunID.isEmpty {
            let runFailureKey = MachineContextKey.task(
                machineID: machineID,
                runID: visibleRunID
            )
            do {
                let prefetched = visibleRunID == latestRunID
                    ? await latestRunTask?.value
                    : nil
                var run: RunSummary
                if let prefetched {
                    run = prefetched
                } else {
                    run = try await client.call(
                        target: target,
                        "turns.get",
                        body: GetRunBody(
                            runId: visibleRunID,
                            includeEvents: false,
                            cursor: 0,
                            eventLimit: 1
                        )
                    )
                }
                guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                      acceptsVisibleRun(), run.runId == visibleRunID,
                      sameConversationWorkspace(run.workspace, workspace.path) else {
                    return
                }
                reportedRunLoadFailures.remove(runFailureKey)
                let key = WorkspaceRunViewKey(machineID: machineID, workspaceID: workspaceID, runID: run.runId)
                run = currentReadRun(key, fallback: run)
                if let cachedRun = activeRun, cachedRun.runId == run.runId {
                    run.preserveTerminalAgentStates(from: cachedRun)
                }
                run.reconcileTerminalAgentStates()
                activeRun = run
                runSummariesByContext[
                    WorkspaceRunViewKey(
                        machineID: machineID,
                        workspaceID: workspaceID,
                        runID: run.runId
                    )
                ] = run
                let cachedAgent = restoreCachedRunViewState(
                    machineID: machineID,
                    workspaceID: workspaceID,
                    run: run
                )
                refreshVisibleRunOperationState()
                if !run.isWorking {
                    notifiedRunIDs.insert(
                        MachineContextKey.task(machineID: machineID, runID: run.runId)
                    )
                }
                if cachedSettings == nil && workspace.settings?.configured != true {
                    settings.apply(run.config)
                }
                if !resumeDisabled,
                   !pendingFields.contains(.resumeSessionID),
                   let sessionID = run.sessionPromotion?.sessionId,
                   !sessionID.isEmpty {
                    settings.resumeSessionID = sessionID
                }
                let selectionKey = selectedAgentContextKey(
                    workspaceID: workspaceID,
                    runID: run.runId
                )
                let rememberedAgent = selectionKey.flatMap {
                    rememberedSelectedAgent(for: run, contextKey: $0)
                }
                pendingAgentSelection = nil
                agentSelectionRequestID += 1
                selectedAgent = selectedAgentForRestoredRun(
                    explicitSelection: rememberedAgent,
                    cachedSelection: cachedAgent,
                    run: run
                )
            } catch {
                guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                      acceptsVisibleRun() else {
                    return
                }
                let recovered = (error as? PcrdClientError)?.isMissingRun(visibleRunID) == true
                    ? await recoverUnavailableRunHistory(visibleRunID, target: target) : false
                guard isCurrentWorkspaceContext(workspaceID, generation: generation),
                      acceptsVisibleRun() else { return }
                if !recovered, activeRun?.runId != visibleRunID {
                    activeRun = nil
                }
                let retainsReadableContext = activeRun?.runId == visibleRunID
                    && PromptSubmissionRetryPolicy.shouldRetry(error)
                if !recovered, !retainsReadableContext,
                   reportedRunLoadFailures.insert(runFailureKey).inserted {
                    let detail = appLanguage.userMessage(for: error)
                    errorMessage = appLanguage.text(
                        "无法恢复上一个任务。\(detail)",
                        "The previous task could not be restored. \(detail)"
                    )
                }
            }
        }

        guard isCurrentWorkspaceContext(workspaceID, generation: generation),
              conversationNavigationRequestID == navigationRequestID else { return }
        restoreFollowUpQueue(workspaceID: workspaceID, runID: activeRun?.runId)
        let conversationSessionID = selectedConversation?.resumeSession?.sessionId ?? ""
        let promotedSessionID = activeRun?.sessionPromotion?.sessionId ?? ""
        let visibleSessionID = usableResumeSessionID(conversationSessionID)
            ?? usableResumeSessionID(promotedSessionID)
            ?? usableResumeSessionID(workspace.lastResumeSessionId)
        if !resumeDisabled,
           !pendingFields.contains(.resumeSessionID),
           let visibleSessionID {
            settings.resumeSessionID = visibleSessionID
        }
        settingsByWorkspace[workspaceKey] = settings
        self.desktopStateStore.saveSettings(settingsByWorkspace)
        if workspace.settings?.configured != true {
            scheduleRemoteSettingsSave(
                workspaceID: workspace.workspaceId,
                settings: settings,
                fields: WorkspaceSettingField.all
            )
        }

        preparePromptHistoryContext()
        restoreVisiblePromptSubmission()
        if let restoredAcceptedResult {
            _ = await adoptAcceptedPromptResult(restoredAcceptedResult)
        }
        if let run = activeRun {
            if !followUpQueue.isEmpty {
                await reconcileFollowUpQueue(for: run, resetCountdown: false)
            }
            resumeEventPollingIfNeeded()
        }
        if let restoredSubmission,
           [.pending, .retrying].contains(restoredSubmission.status) {
            submitPendingPrompt(restoredSubmission)
        }
        retryDirtySettingsIfNeeded()

        // Model, Resume, history, and transcript pages enrich the surface but
        // must not delay live Run monitoring or outbox recovery.
        let historyRunID = activeRun?.runId
        let historySessionID = usableResumeSessionID(settings.resumeSessionID) ?? ""
        async let primaryOptions: Void = loadPrimaryModelOptions()
        async let synthesisOptions: Void = loadSynthesisModelOptions()
        async let sessions: Void = loadResumeSessions()
        async let history: Void = loadConversationHistory(
            runID: historyRunID,
            sessionID: historySessionID
        )
        async let promptHistory: Void = loadPromptHistory()
        async let agentDetail: Void = loadSelectedAgentDetail(maxPages: 20)
        _ = await (
            primaryOptions,
            synthesisOptions,
            sessions,
            history,
            promptHistory,
            agentDetail
        )
    }
}
