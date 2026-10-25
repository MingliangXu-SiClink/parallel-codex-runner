import Foundation

struct AgentAnswerReadRetry {
    let failures: Int
    let after: Date
}

extension AppModel {
    func agentLoadingKey(
        machineID: String,
        workspaceID: String,
        runID: String,
        agent: Int
    ) -> String {
        MachineContextKey.run(
            machineID: machineID,
            workspaceID: workspaceID,
            runID: runID
        ) + "\u{1f}\(agent)"
    }

    func cacheVisibleRunViewState() {
        guard let workspace = selectedWorkspace,
              let run = activeRun,
              sameConversationWorkspace(run.workspace, workspace.path) else { return }
        let workspaceID = workspace.workspaceId
        let conversationTitle = applyVisibleConversationHistoryTitle(
            forRunID: run.runId
        )
        let key = WorkspaceRunViewKey(
            machineID: currentMachineContextID,
            workspaceID: workspaceID,
            runID: run.runId
        )
        let summary = currentReadRun(key, fallback: run)
        runSummariesByContext[key] = summary
        // An indexed destination is sufficient to bind a command, not to
        // replace a disk-cached answer with an unhydrated empty projection.
        guard !transcripts.isEmpty || !resumeHistory.isEmpty || run.agents.isEmpty else { return }
        let visible = WorkspaceRunViewSnapshot(
                selectedAgent: selectedAgent,
                transcripts: transcripts,
                conversationHistory: resumeHistory,
                conversationTitle: conversationTitle,
                historyLoaded: runViewStateStore.snapshot(for: key)?.historyLoaded
            )
        runViewStateStore.put(
            runViewStateStore.snapshot(for: key)?.mergingVisible(visible, for: key) ?? visible,
            runSummary: summary,
            for: key
        )
    }

    func restoreCachedRunViewState(
        machineID: String,
        workspaceID: String,
        run: RunSummary
    ) -> Int? {
        let key = WorkspaceRunViewKey(
            machineID: machineID,
            workspaceID: workspaceID,
            runID: run.runId
        )
        guard runViewStateStore.snapshot(for: key) != nil else { return nil }
        let cached = currentReadSnapshot(key)
        transcripts = cached.transcripts
        resumeHistory = cached.conversationHistory
        return cached.selectedAgent.flatMap { selected in
            run.agents.contains { $0.agent == selected } ? selected : nil
        }
    }

    func forgetCachedRunViews(machineID: String, workspaceID: String) {
        runViewStateStore.removeWorkspace(
            machineID: machineID,
            workspaceID: workspaceID
        )
        let expandedPrefix = MachineContextKey.workspace(
            machineID: machineID,
            workspaceID: workspaceID
        ) + "\u{1f}"
        expandedAgentWorkContexts = Set(
            expandedAgentWorkContexts.filter { !$0.hasPrefix(expandedPrefix) }
        )
        collapsedAgentWorkContexts = Set(
            collapsedAgentWorkContexts.filter { !$0.hasPrefix(expandedPrefix) }
        )
        agentDiffSummaries = agentDiffSummaries.filter {
            !$0.key.hasPrefix(expandedPrefix)
        }
        agentDiffSummaryLoadingKeys = Set(
            agentDiffSummaryLoadingKeys.filter { !$0.hasPrefix(expandedPrefix) }
        )
        let allKeys = Set(runSummariesByContext.keys)
            .union(runPollingTasks.keys)
            .union(runPollingTokens.keys)
            .union(conversationReadTasks.keys)
            .union(conversationHistoryTasks.keys)
        let keys = allKeys.filter {
            $0.machineID == MachineContextKey.normalizeMachineID(machineID)
                && $0.workspaceID == workspaceID
        }
        preloadedConversationKeys = Set(preloadedConversationKeys.filter {
            $0.machineID != MachineContextKey.normalizeMachineID(machineID)
                || $0.workspaceID != workspaceID
        })
        warmedWorkingConversationKeys = Set(warmedWorkingConversationKeys.filter {
            $0.machineID != MachineContextKey.normalizeMachineID(machineID)
                || $0.workspaceID != workspaceID
        })
        conversationPreloadRetryAfter = conversationPreloadRetryAfter.filter {
            $0.key.machineID != MachineContextKey.normalizeMachineID(machineID)
                || $0.key.workspaceID != workspaceID
        }
        for key in keys {
            cancelConversationRead(key)
            runSummariesByContext.removeValue(forKey: key)
            runPollingTasks[key]?.cancel()
            runPollingTasks.removeValue(forKey: key)
            runPollingTokens.removeValue(forKey: key)
        }
    }

    func loadRun(
        _ runID: String,
        loadDetailPages: Int,
        allowUnbound: Bool = false,
        expectedWorkspaceID: String? = nil,
        expectedNavigationRequestID: Int? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) async throws {
        let target = explicitTarget ?? client.currentTarget()
        let generation = workspaceContextGeneration
        let workspaceID = expectedWorkspaceID ?? selectedWorkspaceID
        var previousRun = activeRun
        // Disk cache is a first-paint bootstrap, not a polling source. Once the
        // requested Run already owns the visible surface, replaying its cached
        // snapshot can briefly replace newer transcript state and move the
        // reader before the network response arrives.
        if runViewCacheNeedsHydration(
            activeRunID: activeRun?.runId,
            requestedRunID: runID
        ) || (transcripts.isEmpty && resumeHistory.isEmpty), let workspaceID {
            let key = WorkspaceRunViewKey(
                machineID: target.machineID,
                workspaceID: workspaceID,
                runID: runID
            )
            if let cached = await runViewStateStore.hydrate(for: key),
               let storedRun = cached.runSummary,
               storedRun.runId == runID,
               selectedWorkspace.map({ sameConversationWorkspace(storedRun.workspace, $0.path) }) == true,
               isCurrentMachine(target.machineID),
               generation == workspaceContextGeneration,
               workspaceID == selectedWorkspaceID,
               navigationAcceptsRun(runID, requestID: expectedNavigationRequestID),
               activeRun?.runId == runID || allowUnbound {
                let sameVisibleRun = activeRun?.runId == runID
                var cachedRun = sameVisibleRun ? activeRun! : storedRun
                cachedRun.reconcileTerminalAgentStates()
                let snapshot = sameVisibleRun ? cached.snapshot.mergingVisible(
                    .init(selectedAgent: selectedAgent, transcripts: transcripts,
                        conversationHistory: resumeHistory), for: key) : cached.snapshot
                activeRun = cachedRun
                runSummariesByContext[key] = cachedRun
                transcripts = snapshot.transcripts
                resumeHistory = snapshot.conversationHistory
                let selectionKey = machineRunContextKey(
                    workspaceID: workspaceID,
                    runID: runID,
                    machineID: target.machineID
                )
                selectedAgent = selectedAgentForRestoredRun(
                    explicitSelection: rememberedSelectedAgent(
                        for: cachedRun,
                        contextKey: selectionKey
                    ),
                    cachedSelection: snapshot.selectedAgent,
                    run: cachedRun
                )
                previousRun = cachedRun
                workspaceContextLoadingID = nil
                refreshVisibleRunOperationState()
                restoreCachedFollowUpQueue(workspaceID: workspaceID, runID: runID)
            }
        }
        var run: RunSummary
        do {
            run = try await client.call(
                target: target,
                "turns.get",
                body: GetRunBody(runId: runID, includeEvents: false, cursor: 0, eventLimit: 1)
            )
        } catch {
            guard isCurrentMachine(target.machineID),
                  generation == workspaceContextGeneration,
                  workspaceID == selectedWorkspaceID,
                  navigationAcceptsRun(runID, requestID: expectedNavigationRequestID)
            else { return }
            if (error as? PcrdClientError)?.isMissingRun(runID) == true,
               await recoverUnavailableRunHistory(runID, target: target) { return }
            throw error
        }
        if let snapshot = run.conversationSnapshot {
            domainRepositories.ingest(snapshot)
        }
        guard isCurrentMachine(target.machineID),
              generation == workspaceContextGeneration,
              workspaceID == selectedWorkspaceID,
              navigationAcceptsRun(runID, requestID: expectedNavigationRequestID),
              (activeRun?.runId == runID || allowUnbound) else {
            return
        }
        if let workspaceID {
            let key = WorkspaceRunViewKey(machineID: target.machineID,
                workspaceID: workspaceID, runID: runID)
            let current = currentReadRun(key, fallback: activeRun?.runId == runID ? activeRun! : run)
            if run.isOlderProjection(than: current) { return }
        }
        // Readers can advance while turns.get is in flight. Compare and select
        // against the current projection, not the snapshot from before await.
        if activeRun?.runId == runID { previousRun = activeRun }
        if let previousRun {
            run.preserveTerminalAgentStates(from: previousRun)
        }
        run.reconcileTerminalAgentStates()
        if let workspaceID {
            let key = WorkspaceRunViewKey(
                machineID: target.machineID,
                workspaceID: workspaceID,
                runID: runID
            )
            runSummariesByContext[key] = run
            updateWorkspaceRunSummary(key, run: run)
            reconcileRunOperationIssue(run, key: key)
        }
        if let visibleRunID = activeRun?.runId, visibleRunID != runID {
            let daemonQueueAlreadyMovedToRun = daemonOwnsFollowUpQueue(
                machineID: target.machineID
            ) && followUpRunID == runID
            if !daemonQueueAlreadyMovedToRun {
                clearFollowUpQueue(persist: false)
            }
            selectedAgent = nil
            pendingAgentSelection = nil
            agentSelectionRequestID += 1
            transcripts = [:]
            resumeHistory = []
            agentDetailLoadTokens.removeAll()
            agentAnswerLoadTokens.removeAll()
            agentAnswerReadRetries.removeAll()
            loadingAgents.removeAll()
            loadingAgentKeys.removeAll()
        }
        if activeRun != run {
            activeRun = run
        }
        recordRunInConversationIndex(run, select: false)
        if let workspaceID {
            restoreCachedFollowUpQueue(workspaceID: workspaceID, runID: run.runId)
        }
        refreshVisibleRunOperationState()
        let workspaceRevisionChanged = run.workspaceRevision.map {
            $0 != workspaceRevision
        } == true
        if let revision = run.workspaceRevision,
           workspaceRevision != revision {
            workspaceRevision = revision
        }
        if let sessionID = run.sessionPromotion?.sessionId,
           !sessionID.isEmpty,
           settings.resumeSessionID != sessionID {
            settings.resumeSessionID = sessionID
            rememberCurrentSettings()
        }

        let allAgentsFinishedNow = previousRun?.runId == runID
            && previousRun?.isWorking == true
            && !run.isWorking
            && allAgentsAreTerminal(run)
        let explicitSelection = selectedAgentContextKey(workspaceID: workspaceID, runID: runID)
            .flatMap { rememberedSelectedAgent(for: run, contextKey: $0) }
        let nextSelectedAgent = selectedAgentAfterRunUpdate(
            previousRun: previousRun,
            currentSelection: selectedAgent,
            updatedRun: run,
            explicitSelection: explicitSelection
        )
        if selectedAgent != nextSelectedAgent {
            selectedAgent = nextSelectedAgent
        }

        if let selectedAgent,
           selectedAgentAnswerNeedsRefresh(run: run, selectedAgent: selectedAgent, transcripts: transcripts) {
            Task { [weak self] in
                await self?.loadAgentAnswer(selectedAgent, maxPages: loadDetailPages, target: target)
            }
        }
        if let selectedAgent,
           selectedAgentDetailNeedsRefresh(
               run: run,
               selectedAgent: selectedAgent,
               transcripts: transcripts
           ) {
            // Summary polling must not wait for transcript pagination. A long
            // answer can otherwise freeze progress and every Agent status
            // behind the currently visible branch.
            Task { [weak self] in
                await self?.loadAgentDetail(
                    selectedAgent,
                    maxPages: loadDetailPages,
                    target: target
                )
            }
        }
        if !followUpQueue.isEmpty {
            await reconcileFollowUpQueue(for: run,
                resetCountdown: allAgentsFinishedNow && !daemonOwnsFollowUpQueue(machineID: target.machineID))
        }
        if workspaceRevisionChanged {
            await refresh(reportErrors: false)
        }
        cacheVisibleRunViewState()
        scheduleVisibleAgentPreload(target: target)
    }

    @discardableResult
    func loadAgentAnswer(
        _ agent: Int,
        maxPages: Int,
        target explicitTarget: PcrdRequestTarget? = nil,
        retryImmediately: Bool = false
    ) async -> Bool {
        guard let runID = activeRun?.runId, let workspaceID = selectedWorkspaceID,
              selectedAgent == agent else { return false }
        guard retryImmediately || selectedAgentAnswerNeedsRefresh(run: activeRun,
            selectedAgent: agent, transcripts: transcripts) else { return agentTranscriptReadyForDisplay(agent) }
        let target = explicitTarget ?? client.currentTarget()
        let generation = workspaceContextGeneration
        let selectionID = agentSelectionRequestID
        let loadingKey = agentLoadingKey(machineID: target.machineID, workspaceID: workspaceID,
            runID: runID, agent: agent) + "#\(generation)#\(selectionID)"
        guard agentAnswerLoadTokens[loadingKey] == nil else { return agentTranscriptReadyForDisplay(agent) }
        guard retryImmediately || agentAnswerReadRetries[loadingKey].map({ $0.after <= Date() }) ?? true
        else { return agentTranscriptReadyForDisplay(agent) }
        let loadToken = UUID()
        agentAnswerLoadTokens[loadingKey] = loadToken
        defer {
            if agentAnswerLoadTokens[loadingKey] == loadToken {
                agentAnswerLoadTokens.removeValue(forKey: loadingKey)
            }
        }
        var transcript = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
        do {
            for _ in 0..<max(1, maxPages) {
                let previousCursor = transcript.finalMessageCursor
                var restart = false
                repeat {
                    let requested = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
                    let requestedStatus = activeRun?.agents.first { $0.agent == agent }?.normalizedStatus
                    let detail: RunAgentDetail = try await client.call(target: target, "runs.agent",
                        body: GetAgentBody(runId: runID, agent: agent, includeDiff: false,
                            includeEvents: false, cursor: 0, eventLimit: 1,
                            transcriptItemsOnly: true, conversationItemsOnly: true,
                            finalMessageCursor: transcript.finalMessageCursor))
                    guard !Task.isCancelled, agentAnswerLoadTokens[loadingKey] == loadToken,
                          isCurrentMachine(target.machineID),
                          generation == workspaceContextGeneration,
                          workspaceID == selectedWorkspaceID, activeRun?.runId == runID,
                          selectedAgent == agent, selectionID == agentSelectionRequestID else { return false }
                    transcript = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
                    let needsRestart = transcript.mergeFinalMessage(detail, requested: requested,
                        requestedStatus: requestedStatus)
                    if needsRestart {
                        guard !restart else { return agentTranscriptReadyForDisplay(agent) }
                        restart = true
                        continue
                    }
                    transcript.error = detail.error ?? ""
                    agentAnswerReadRetries.removeValue(forKey: loadingKey)
                    if case .agentDetail(let failedRunID, let failedAgent, _) = errorNotice,
                       failedRunID == runID, failedAgent == agent {
                        errorNotice = nil
                    }
                    if transcript.finalMessagePresentationID == nil,
                       requested.finalMessage.isEmpty, !transcript.finalMessage.isEmpty {
                        transcript.finalMessagePresentationID = "final-message"
                    }
                    if transcripts[agent] != transcript {
                        transcripts[agent] = transcript
                        cacheVisibleRunViewState()
                    }
                    break
                } while true
                // Answer reads deliberately leave the work cursor/hasMore alone.
                if !transcript.finalMessageHasMore || transcript.finalMessageCursor <= previousCursor { break }
            }
            return agentTranscriptReadyForDisplay(agent)
        } catch {
            // Reading is retried by normal polling; transport failures are not
            // durable Agent output and must never replace a cached reply.
            if agentAnswerLoadTokens[loadingKey] == loadToken,
               isCurrentMachine(target.machineID), generation == workspaceContextGeneration,
               selectedWorkspaceID == workspaceID, activeRun?.runId == runID,
               selectedAgent == agent, selectionID == agentSelectionRequestID {
                let failures = (agentAnswerReadRetries[loadingKey]?.failures ?? 0) + 1
                agentAnswerReadRetries[loadingKey] = AgentAnswerReadRetry(failures: failures,
                    after: Date().addingTimeInterval(min(5, 0.8 * pow(2, Double(min(failures - 1, 3))))))
                if !PromptSubmissionRetryPolicy.shouldRetry(error) {
                    errorNotice = .agentDetail(runID: runID, agent: agent, diagnostic: error.localizedDescription)
                }
            }
            return agentTranscriptReadyForDisplay(agent)
        }
    }

    @discardableResult
    func loadAgentDetail(
        _ agent: Int,
        maxPages: Int,
        target explicitTarget: PcrdRequestTarget? = nil,
        initialEventLimit: Int = 1_000,
        allowInactiveSelection: Bool = false,
        refreshFromStart: Bool = false
    ) async -> Bool {
        guard let runID = activeRun?.runId else { return false }
        let target = explicitTarget ?? client.currentTarget()
        let generation = workspaceContextGeneration
        let workspaceID = selectedWorkspaceID
        let loadingKey = agentLoadingKey(
            machineID: target.machineID,
            workspaceID: workspaceID ?? "",
            runID: runID,
            agent: agent
        )
        if !refreshFromStart, conversationAgentReadTokens[loadingKey] != nil {
            guard allowInactiveSelection else { return agentTranscriptReadyForDisplay(agent) }
            while conversationAgentReadTokens[loadingKey] != nil {
                if agentTranscriptReadyForDisplay(agent) { return true }
                do { try await Task.sleep(for: .milliseconds(20)) } catch { return false }
                guard isCurrentMachine(target.machineID), generation == workspaceContextGeneration,
                      workspaceID == selectedWorkspaceID, activeRun?.runId == runID else { return false }
            }
            if agentTranscriptReadyForDisplay(agent) { return true }
        }
        if agentDetailLoadTokens[loadingKey] != nil {
            guard allowInactiveSelection || refreshFromStart else {
                return agentTranscriptReadyForDisplay(agent)
            }
            while agentDetailLoadTokens[loadingKey] != nil {
                if !refreshFromStart, agentTranscriptReadyForDisplay(agent) { return true }
                do {
                    try await Task.sleep(for: .milliseconds(20))
                } catch {
                    return false
                }
                guard isCurrentMachine(target.machineID),
                      generation == workspaceContextGeneration,
                      workspaceID == selectedWorkspaceID,
                      activeRun?.runId == runID else { return false }
            }
            if !refreshFromStart, agentTranscriptReadyForDisplay(agent) { return true }
            return await loadAgentDetail(
                agent,
                maxPages: maxPages,
                target: target,
                initialEventLimit: initialEventLimit,
                allowInactiveSelection: allowInactiveSelection,
                refreshFromStart: refreshFromStart
            )
        }
        let loadToken = UUID()
        agentDetailLoadTokens[loadingKey] = loadToken
        loadingAgents.insert(loadingKey)
        let publishesLoadingState = selectedAgent == agent
            && !agentTranscriptReadyForDisplay(agent)
        if publishesLoadingState {
            loadingAgentKeys.insert(loadingKey)
        }
        defer {
            if agentDetailLoadTokens[loadingKey] == loadToken {
                agentDetailLoadTokens.removeValue(forKey: loadingKey)
                loadingAgents.remove(loadingKey)
                if publishesLoadingState {
                    loadingAgentKeys.remove(loadingKey)
                }
            }
        }

        var transcript = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
        if refreshFromStart {
            transcript.eventIDs = []
            transcript.blocks = []
            transcript.cursor = 0
            transcript.hasMore = true
        }
        var eventLimit = max(1, initialEventLimit)
        var receivedDetail = false
        do {
            for _ in 0..<max(1, maxPages) {
                guard !Task.isCancelled,
                      agentDetailLoadTokens[loadingKey] == loadToken,
                      (allowInactiveSelection || selectedAgent == agent) else {
                    return false
                }
                let previousEventCursor = transcript.cursor
                let previousFinalMessageCursor = transcript.finalMessageCursor
                var loadedDetail: RunAgentDetail?
                while loadedDetail == nil {
                    do {
                        let requested = refreshFromStart ? transcript : agentTranscriptBoundToBranch(
                            transcripts[agent], runID: runID, agent: agent
                        )
                        let requestedStatus = activeRun?.agents.first { $0.agent == agent }?.normalizedStatus
                        loadedDetail = try await client.call(
                            target: target,
                            "runs.agent",
                            body: GetAgentBody(
                                runId: runID,
                                agent: agent,
                                includeDiff: false,
                                includeEvents: true,
                                cursor: transcript.cursor,
                                eventLimit: eventLimit,
                                transcriptItemsOnly: true,
                                conversationItemsOnly: false,
                                finalMessageCursor: transcript.finalMessageCursor
                            )
                        )
                        guard !Task.isCancelled, isCurrentMachine(target.machineID),
                              generation == workspaceContextGeneration,
                              workspaceID == selectedWorkspaceID,
                              activeRun?.runId == runID else { return false }
                        if !refreshFromStart {
                            transcript = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
                        }
                        if refreshFromStart, let incoming = loadedDetail?.inputRequests {
                            transcript.mergeInputRequests(incoming)
                        }
                        if !refreshFromStart, let detail = loadedDetail,
                           transcript.mergeFinalMessage(detail, requested: requested,
                            requestedStatus: requestedStatus) {
                            loadedDetail = nil
                        }
                        guard !Task.isCancelled else { return false }
                    } catch let PcrdClientError.remote(code, message) {
                        guard code == "response_too_large", eventLimit > 1 else {
                            throw PcrdClientError.remote(code, message)
                        }
                        eventLimit = max(1, eventLimit / 2)
                    }
                }
                guard let detail = loadedDetail else { break }
                guard !Task.isCancelled,
                      agentDetailLoadTokens[loadingKey] == loadToken,
                      isCurrentMachine(target.machineID),
                      generation == workspaceContextGeneration,
                      workspaceID == selectedWorkspaceID,
                      (allowInactiveSelection || selectedAgent == agent),
                      activeRun?.runId == runID else { return false }
                receivedDetail = true
                if case .agentDetail(let failedRunID, let failedAgent, _) = errorNotice,
                   failedRunID == runID, failedAgent == agent {
                    errorNotice = nil
                }
                if let page = detail.transcriptPage, page.cursor == transcript.cursor,
                   page.nextCursor >= page.cursor, !page.hasMore || page.nextCursor > page.cursor {
                    let newItems = page.items
                        .filter { !transcript.eventIDs.contains($0.eventId) }
                        .sorted { $0.eventId < $1.eventId }
                    transcript.eventIDs.formUnion(newItems.map(\.eventId))
                    TranscriptFormatter.append(items: newItems, to: &transcript.blocks)
                    transcript.cursor = page.nextCursor
                    transcript.hasMore = page.hasMore
                } else if let page = detail.eventPage, page.cursor == transcript.cursor,
                          page.nextCursor >= page.cursor, !page.hasMore || page.nextCursor > page.cursor {
                    let newEvents = page.events
                        .filter { !transcript.eventIDs.contains($0.eventId) }
                        .sorted { $0.eventId < $1.eventId }
                    transcript.eventIDs.formUnion(newEvents.map(\.eventId))
                    TranscriptFormatter.append(events: newEvents, to: &transcript.blocks)
                    transcript.cursor = page.nextCursor
                    transcript.hasMore = page.hasMore
                } else if detail.transcriptPage == nil && detail.eventPage == nil {
                    transcript.hasMore = false
                }
                transcript.error = detail.error ?? ""
                if !refreshFromStart, transcripts[agent] != transcript {
                    transcripts[agent] = transcript
                    cacheVisibleRunViewState()
                }
                if !transcript.hasMore && !transcript.finalMessageHasMore { break }
                if transcript.cursor <= previousEventCursor,
                   transcript.finalMessageCursor <= previousFinalMessageCursor {
                    break
                }
            }
            if refreshFromStart, receivedDetail {
                // Manual refresh replaces the cached projection only after all
                // requested pages arrive. The visible answer never goes blank.
                // Rebuilding work must not replace an independently loaded
                // answer. Manual refresh gives the answer reader its own RPC.
                let visible = agentTranscriptBoundToBranch(transcripts[agent], runID: runID, agent: agent)
                transcript.copyFinalMessage(from: visible)
                transcript.recentWork = visible.recentWork
                transcript.preserveRecentWorkProgress(from: visible.blocks)
                transcript.mergeInputRequests(visible.inputRequests ?? [])
                transcripts[agent] = transcript
                cacheVisibleRunViewState()
            }
            return receivedDetail || agentTranscriptReadyForDisplay(agent)
        } catch {
            if PromptSubmissionRetryPolicy.shouldRetry(error) {
                return agentTranscriptReadyForDisplay(agent)
            }
            if let clientError = error as? PcrdClientError {
                switch clientError {
                case .connection, .missingToken:
                    // A detail page is a retryable view request, not Agent
                    // output. Keep the existing conversation intact while the
                    // run poller reconnects instead of inserting transport
                    // failures into the transcript.
                    return false
                default:
                    break
                }
            }
            if !allowInactiveSelection,
               agentDetailLoadTokens[loadingKey] == loadToken,
               isCurrentMachine(target.machineID),
               generation == workspaceContextGeneration,
               workspaceID == selectedWorkspaceID,
               selectedAgent == agent,
               activeRun?.runId == runID {
                if (error as? PcrdClientError)?.isMissingRun(runID) == true,
                   await recoverUnavailableRunHistory(runID, target: target) { return false }
                // Request failures belong to this view, not to the Agent's
                // durable output. Never persist a failed refresh as a reply.
                let notice = AppErrorNotice.agentDetail(
                    runID: runID, agent: agent, diagnostic: error.localizedDescription
                )
                if errorNotice != notice { errorNotice = notice }
            }
            return false
        }
    }

    func agentTranscriptReadyForDisplay(_ agent: Int) -> Bool {
        guard let transcript = transcripts[agent],
              transcript.sourceRunID == activeRun?.runId,
              transcript.sourceAgent == agent else { return false }
        return !transcript.blocks.isEmpty
            || transcript.conversation?.items.isEmpty == false
            || transcript.conversation?.hasMore == false
            || !transcript.finalMessage.isEmpty
            || !transcript.error.isEmpty
            || !transcript.artifactError.isEmpty
            || transcript.cursor > 0
            || transcript.finalMessageCursor > 0
            || (!transcript.hasMore && !transcript.finalMessageHasMore)
    }

    func loadSelectedAgentDetail(maxPages: Int) async {
        guard let selectedAgent else { return }
        await loadAgentDetail(selectedAgent, maxPages: maxPages)
    }

    func runPollingIsComplete(_ key: WorkspaceRunViewKey) -> Bool {
        let operationKey = MachineContextKey.run(machineID: key.machineID,
            workspaceID: key.workspaceID, runID: key.runID)
        guard runOperationIssues[operationKey]?.needsReadback != true else { return false }
        guard let run = runSummariesByContext[key], !run.isWorking,
              allAgentsAreTerminal(run) else { return false }
        return !isVisibleRunContext(key) || !selectedAgentDetailNeedsRefresh(
            run: run, selectedAgent: selectedAgent, transcripts: transcripts
        )
    }

    func beginPolling(
        _ runID: String,
        workspaceID explicitWorkspaceID: String? = nil,
        target explicitTarget: PcrdRequestTarget? = nil
    ) {
        guard let workspaceID = explicitWorkspaceID ?? selectedWorkspaceID else { return }
        let target = explicitTarget ?? client.currentTarget()
        let key = WorkspaceRunViewKey(
            machineID: target.machineID,
            workspaceID: workspaceID,
            runID: runID
        )
        guard !unavailableRunContexts.contains(key) else { return }
        if runPollingTasks[key]?.isCancelled == false { return }
        runPollingTasks[key]?.cancel()
        let token = UUID()
        runPollingTokens[key] = token
        runPollingTasks[key] = Task { [weak self] in
            var consecutiveFailures = 0
            var displayedWarning: String?
            defer {
                if let self, self.runPollingTokens[key] == token {
                    self.runPollingTasks.removeValue(forKey: key)
                    self.runPollingTokens.removeValue(forKey: key)
                }
            }
            while !Task.isCancelled {
                guard let self else { return }
                let visible = self.isVisibleRunContext(key)
                do {
                    if visible {
                        try await self.loadRun(
                            runID,
                            loadDetailPages: 4,
                            expectedWorkspaceID: workspaceID,
                            target: target
                        )
                    } else {
                        try await self.loadBackgroundRun(key, target: target)
                    }
                    consecutiveFailures = 0
                    if self.isVisibleRunContext(key), displayedWarning != nil {
                        self.liveConversationWarning = nil
                    }
                    displayedWarning = nil
                } catch is CancellationError {
                    return
                } catch {
                    guard !Task.isCancelled else { return }
                    if (error as? PcrdClientError)?.isMissingRun(runID) == true {
                        self.markRunUnavailable(key)
                        return
                    }
                    // A failed metadata refresh cannot turn a complete,
                    // cached answer into an endless reconnect operation.
                    if self.runPollingIsComplete(key) {
                        if self.isVisibleRunContext(key) { self.liveConversationWarning = nil }
                        return
                    }
                    consecutiveFailures += 1
                    if self.isVisibleRunContext(key), consecutiveFailures >= 3 {
                        self.liveConversationNeedsAction = !PromptSubmissionRetryPolicy.shouldRetry(error)
                        let warning = self.liveConversationNeedsAction
                            ? self.appLanguage.text("设备需要重新连接", "Reconnect the device")
                            : self.appLanguage.text("正在恢复连接", "Reconnecting")
                        if displayedWarning != warning {
                            displayedWarning = warning
                            self.liveConversationWarning = warning
                        }
                    }
                    let delaySeconds = min(
                        5.0,
                        0.8 * pow(2.0, Double(min(consecutiveFailures - 1, 3)))
                    )
                    try? await Task.sleep(for: .seconds(delaySeconds))
                    continue
                }
                if self.unavailableRunContexts.contains(key) { return }
                let currentlyVisible = self.isVisibleRunContext(key)
                if self.runPollingIsComplete(key) { return }
                try? await Task.sleep(
                    for: currentlyVisible ? .milliseconds(800) : .seconds(4)
                )
            }
        }
    }

    func reconcileRunPollingContexts(
        machineID: String,
        target: PcrdRequestTarget,
        workspaces: [RemoteWorkspace]
    ) {
        let resolvedMachineID = MachineContextKey.normalizeMachineID(machineID)
        let latestRuns = Dictionary(
            uniqueKeysWithValues: workspaces.compactMap { workspace in
                workspace.lastRunId.isEmpty
                    ? nil
                    : (workspace.workspaceId, workspace.lastRunId)
            }
        )
        let obsolete = runPollingTasks.keys.filter { key in
            key.machineID == resolvedMachineID
                && latestRuns[key.workspaceID] != key.runID
                && (latestRuns[key.workspaceID] == nil || runSummariesByContext[key]?.isWorking != true)
                && (latestRuns[key.workspaceID] == nil
                    || (conversationReadTasks[key] == nil && conversationHistoryTasks[key] == nil))
        }
        for key in obsolete {
            cancelConversationRead(key)
            runPollingTasks[key]?.cancel()
            runPollingTasks.removeValue(forKey: key)
            runPollingTokens.removeValue(forKey: key)
            // Stopping a reader does not invalidate known continuation lineage.
            if latestRuns[key.workspaceID] == nil { runSummariesByContext.removeValue(forKey: key) }
        }
        for workspace in workspaces
            where workspace.removalLooksActive && !workspace.lastRunId.isEmpty {
            beginPolling(
                workspace.lastRunId,
                workspaceID: workspace.workspaceId,
                target: target
            )
        }
    }

    private func isVisibleRunContext(_ key: WorkspaceRunViewKey) -> Bool {
        key.machineID == currentMachineContextID
            && key.workspaceID == selectedWorkspaceID
            && key.runID == activeRun?.runId
    }

    private func loadBackgroundRun(
        _ key: WorkspaceRunViewKey,
        target: PcrdRequestTarget
    ) async throws {
        let run: RunSummary = try await client.call(
            target: target,
            "turns.get",
            body: GetRunBody(
                runId: key.runID,
                includeEvents: false,
                cursor: 0,
                eventLimit: 1
            ),
            priority: .utility
        )
        _ = try recordRunObservation(run, for: key)
        scheduleConversationPreload(machineID: target.machineID, target: target, workspaces: workspaces)
    }

    @discardableResult
    func recordRunObservation(_ incoming: RunSummary, for key: WorkspaceRunViewKey) throws -> RunSummary {
        guard incoming.runId == key.runID else { throw PcrdClientError.invalidResponse }
        let previous = runSummariesByContext[key]
        guard previous.map({ sameConversationWorkspace($0.workspace, incoming.workspace) }) != false else {
            throw PcrdClientError.invalidResponse
        }
        var run = previous.map { incoming.isOlderProjection(than: $0) ? $0 : incoming } ?? incoming
        if let previous { run.preserveTerminalAgentStates(from: previous) }
        run.reconcileTerminalAgentStates()
        if let snapshot = run.conversationSnapshot { domainRepositories.ingest(snapshot) }
        runSummariesByContext[key] = run
        // Business receipts belong to their original run, not the open page.
        // Keep its answers/history while persisting the new save state.
        runViewStateStore.put(currentReadSnapshot(key), runSummary: run, for: key)
        updateWorkspaceRunSummary(key, run: run)
        reconcileRunOperationIssue(run, key: key)
        // Navigation may return to this run while a background read is in flight.
        // Settle the current matching view before polling can declare completion.
        if isVisibleRunContext(key) {
            activeRun = run
            if let revision = run.workspaceRevision { workspaceRevision = revision }
            if let sessionID = run.sessionPromotion?.sessionId, !sessionID.isEmpty,
               settings.resumeSessionID != sessionID {
                settings.resumeSessionID = sessionID
                rememberCurrentSettings()
                preparePromptHistoryContext()
            }
            cacheVisibleRunViewState()
        }
        return run
    }

    private func updateWorkspaceRunSummary(
        _ key: WorkspaceRunViewKey,
        run: RunSummary
    ) {
        guard key.machineID == currentMachineContextID else { return }
        let nextWorkspaces = workspaces.map { workspace in
            guard workspace.workspaceId == key.workspaceID else { return workspace }
            return workspace.replacingRunSummary(with: run)
        }
        if workspaces != nextWorkspaces {
            workspaces = nextWorkspaces
        }
    }

    func pruneRunPollingContexts(availableMachineIDs: Set<String>) {
        let normalized = Set(availableMachineIDs.map(MachineContextKey.normalizeMachineID))
        let obsolete = Set(runPollingTasks.keys)
            .union(runPollingTokens.keys)
            .union(runSummariesByContext.keys)
            .filter { !normalized.contains($0.machineID) }
        for key in obsolete {
            cancelConversationRead(key)
            runPollingTasks[key]?.cancel()
            runPollingTasks.removeValue(forKey: key)
            runPollingTokens.removeValue(forKey: key)
            runSummariesByContext.removeValue(forKey: key)
        }
        preloadedConversationKeys = Set(preloadedConversationKeys.filter {
            normalized.contains($0.machineID)
        })
        warmedWorkingConversationKeys = Set(warmedWorkingConversationKeys.filter {
            normalized.contains($0.machineID)
        })
        conversationPreloadRetryAfter = conversationPreloadRetryAfter.filter {
            normalized.contains($0.key.machineID)
        }
    }

    func resumeEventPollingIfNeeded() {
        guard let runID = activeRun?.runId else { return }
        if activeRun?.isWorking == true || selectedAgentDetailNeedsRefresh(
            run: activeRun,
            selectedAgent: selectedAgent,
            transcripts: transcripts
        ) {
            beginPolling(runID)
        }
    }
}
