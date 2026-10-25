import Foundation

private struct ConversationReadRevision: Equatable {
    let eventCount: Int
    let status: String
}

private struct ConversationBranchRead: Hashable, Sendable {
    let agent: Int
    let mode: ConversationReadMode
}

private enum ConversationReadEvent: Sendable {
    case finished(ConversationBranchRead, Bool)
    case checkMetadata
    case prepared([Int: PreparedConversationProjection])
}

@MainActor
extension AppModel {
    func scheduleConversationPreload(machineID: String, target: PcrdRequestTarget,
                                     workspaces: [RemoteWorkspace]) {
        guard isCurrentMachine(machineID),
              MachineContextKey.normalizeMachineID(machineID) == target.machineID else { return }
        var started = false
        let workspaceIDs = Set(workspaces.map(\.workspaceId))
        let draining = Set(conversationReadTasks.keys).union(conversationHistoryTasks.keys).filter {
            $0.machineID == target.machineID && workspaceIDs.contains($0.workspaceID)
        }
        let readyRuns = Set(runSummariesByContext.compactMap { key, run in
            key.machineID == target.machineID && workspaceIDs.contains(key.workspaceID) && run.isWorking ? key : nil
        }).union(workspaces.filter(\.removalLooksActive).map {
            WorkspaceRunViewKey(machineID: machineID, workspaceID: $0.workspaceId, runID: $0.lastRunId)
        }).union(draining)
        transcriptPresentationCache.retainReadyRuns(readyRuns)
        runViewStateStore.retainReadyRuns(readyRuns)
        let primingActiveBodies = workspaces.filter(\.removalLooksActive).contains { workspace in
            let key = WorkspaceRunViewKey(machineID: machineID, workspaceID: workspace.workspaceId, runID: workspace.lastRunId)
            guard let run = runSummariesByContext[key],
                  let snapshot = runViewStateStore.snapshot(for: key) else { return true }
            return run.agents.contains { agent in
                guard let transcript = snapshot.transcripts[agent.agent] else { return true }
                let bodyReady = transcript.conversation.map { !$0.items.isEmpty || !$0.hasMore } ?? false
                return !bodyReady && transcript.finalMessage.isEmpty
                    && (transcript.hasMore || transcript.cursor < run.eventCount)
            }
        }
        for workspace in workspaces where !workspace.lastRunId.isEmpty {
            // Cold archives must not queue ahead of the first live body in another workspace.
            guard workspace.removalLooksActive || !primingActiveBodies
                || workspace.workspaceId == selectedWorkspaceID else { continue }
            let key = WorkspaceRunViewKey(machineID: machineID,
                workspaceID: workspace.workspaceId, runID: workspace.lastRunId)
            started = scheduleConversationRead(workspace: workspace, key: key, target: target) || started
            for live in readyRuns where live.workspaceID == workspace.workspaceId && live != key {
                started = scheduleConversationRead(workspace: workspace, key: live, target: target) || started
            }
        }
        guard started else { return }
        let token = UUID()
        conversationPreloadToken = token
        let tasks = Array(conversationReadTasks.values)
        conversationPreloadTask = Task { [weak self] in
            for task in tasks { await task.value }
            if self?.conversationPreloadToken == token {
                self?.conversationPreloadTask = nil
                self?.conversationPreloadToken = nil
            }
        }
    }

    @discardableResult
    func scheduleConversationRead(workspace: RemoteWorkspace, key: WorkspaceRunViewKey,
                                  target: PcrdRequestTarget) -> Bool {
        guard isCurrentMachine(key.machineID), key.machineID == target.machineID,
              conversationPreloadRetryAfter[key, default: .distantPast] <= Date() else { return false }
        let record = runViewStateStore.cachedRecords(machineID: key.machineID,
            workspaceID: workspace.workspaceId).first { $0.key == key }
        let run = runSummariesByContext[key] ?? record?.runSummary
        // History has its own task and retry state. A slow Agent reader must
        // not prevent the earlier conversation from being filled back in.
        if let run { scheduleConversationHistoryRead(key: key, run: run, target: target) }
        if conversationReadTasks[key] != nil {
            if workspace.lastRunId == key.runID,
               let status = workspace.runStatus, let known = runSummariesByContext[key],
               status != known.status {
                beginPolling(key.runID, workspaceID: key.workspaceID, target: target)
            }
            return false
        }
        if let run, let snapshot = record?.snapshot {
            let statusChanged = workspace.runStatus.map { $0 != run.status } ?? false
            if !run.isWorking && !statusChanged && run.agents.allSatisfy({
                !selectedAgentDetailNeedsRefresh(run: run, selectedAgent: $0.agent,
                    transcripts: snapshot.transcripts)
            }) { return false }
        }
        let token = UUID()
        conversationReadTokens[key] = token
        conversationReadTasks[key] = Task(priority: .utility) { [weak self] in
            guard let self else { return }
            defer {
                if self.conversationReadTokens[key] == token {
                    self.conversationReadTasks.removeValue(forKey: key)
                    self.conversationReadTokens.removeValue(forKey: key)
                    if !Task.isCancelled, self.conversationReadIsValid(key),
                       let after = self.conversationPreloadRetryAfter[key] {
                        self.scheduleConversationReadRetry(workspace: workspace,
                            key: key, target: target, after: after)
                    } else if !Task.isCancelled, self.conversationReadIsValid(key),
                       let latest = self.workspaces.first(where: { $0.workspaceId == key.workspaceID }),
                       latest.runStatus != workspace.runStatus {
                        self.scheduleConversationPreload(machineID: key.machineID,
                            target: target, workspaces: [latest])
                    }
                }
            }
            await self.preloadConversation(workspace: workspace, key: key,
                target: target, token: token)
        }
        return true
    }

    private func scheduleConversationReadRetry(workspace: RemoteWorkspace, key: WorkspaceRunViewKey,
                                               target: PcrdRequestTarget, after: Date) {
        let token = UUID()
        conversationReadTokens[key] = token
        conversationReadTasks[key] = Task(priority: .utility) { [weak self] in
            do { try await Task.sleep(for: .seconds(max(0, after.timeIntervalSinceNow))) }
            catch { return }
            guard let self, self.conversationReadTokens[key] == token else { return }
            self.conversationReadTasks.removeValue(forKey: key)
            self.conversationReadTokens.removeValue(forKey: key)
            guard self.conversationReadIsValid(key) else { return }
            self.scheduleConversationRead(workspace: workspace, key: key, target: target)
        }
    }

    func cancelConversationPreload() {
        conversationPreloadTask?.cancel()
        conversationPreloadTask = nil
        conversationPreloadToken = nil
        for key in Set(conversationReadTasks.keys).union(conversationHistoryTasks.keys) {
            cancelConversationRead(key)
        }
    }

    func cancelConversationRead(_ key: WorkspaceRunViewKey) {
        conversationReadTasks.removeValue(forKey: key)?.cancel()
        conversationReadTokens.removeValue(forKey: key)
        conversationHistoryTasks.removeValue(forKey: key)?.cancel()
        conversationHistoryTokens.removeValue(forKey: key)
    }

    private func conversationReadIsValid(_ key: WorkspaceRunViewKey) -> Bool {
        !Task.isCancelled && isCurrentMachine(key.machineID)
            && !unavailableRunContexts.contains(key)
            && workspaces.contains { $0.workspaceId == key.workspaceID
                && ($0.lastRunId == key.runID || runSummariesByContext[key] != nil) }
    }

    func currentReadRun(_ key: WorkspaceRunViewKey, fallback: RunSummary) -> RunSummary {
        let cached = runSummariesByContext[key] ?? fallback
        let known = cached.isOlderProjection(than: fallback) ? fallback : cached
        if isCurrentMachine(key.machineID), selectedWorkspaceID == key.workspaceID, let visible = activeRun,
           visible.runId == key.runID,
           !visible.isOlderProjection(than: known),
           known.isOlderProjection(than: visible) || visible.agents.count >= known.agents.count {
            var merged = visible
            merged.preserveTerminalAgentStates(from: known)
            merged.reconcileTerminalAgentStates()
            return merged
        }
        return known
    }

    func currentReadSnapshot(_ key: WorkspaceRunViewKey,
                             fallback: WorkspaceRunViewSnapshot? = nil) -> WorkspaceRunViewSnapshot {
        let cached = runViewStateStore.snapshot(for: key)
        var snapshot = cached ?? fallback ?? WorkspaceRunViewSnapshot(
            selectedAgent: nil, transcripts: [:], conversationHistory: [])
        if let cached, let fallback, cached != fallback {
            snapshot = fallback.mergingVisible(cached, for: key)
        }
        if selectedWorkspaceID == key.workspaceID, activeRun?.runId == key.runID,
           isCurrentMachine(key.machineID) {
            // Capture concurrent foreground reads without persisting an older
            // visible Run summary over newly received background metadata.
            if snapshot.selectedAgent != selectedAgent || snapshot.transcripts != transcripts
                || snapshot.conversationHistory != resumeHistory {
                snapshot = snapshot.mergingVisible(WorkspaceRunViewSnapshot(selectedAgent: selectedAgent,
                    transcripts: transcripts, conversationHistory: resumeHistory), for: key)
            }
        }
        return snapshot
    }

    private func preloadConversation(workspace: RemoteWorkspace, key: WorkspaceRunViewKey,
                                     target: PcrdRequestTarget, token: UUID) async {
        do {
            let cached = await runViewStateStore.hydrate(for: key)
            guard conversationReadIsValid(key), conversationReadTokens[key] == token else { return }
            let known = runSummariesByContext[key] ?? cached?.runSummary
            var run: RunSummary
            if let known, workspace.runStatus.map({ $0 == known.status }) ?? true {
                run = known
            } else {
                run = try await client.call(target: target, "turns.get",
                    body: GetRunBody(runId: key.runID, includeEvents: false, cursor: 0, eventLimit: 1),
                    priority: .utility)
            }
            guard conversationReadIsValid(key), conversationReadTokens[key] == token,
                  run.runId == key.runID, sameConversationWorkspace(run.workspace, workspace.path) else { return }
            if let previous = runSummariesByContext[key] {
                run.preserveTerminalAgentStates(from: previous)
                if previous.eventCount > run.eventCount { run = previous }
            }
            run.reconcileTerminalAgentStates()
            runSummariesByContext[key] = run
            var snapshot = currentReadSnapshot(key)
            if snapshot.selectedAgent == nil {
                let selectionKey = machineRunContextKey(workspaceID: key.workspaceID,
                    runID: key.runID, machineID: key.machineID)
                snapshot.selectedAgent = selectedAgentForRestoredRun(
                    explicitSelection: selectedAgentsByContext[selectionKey], cachedSelection: nil, run: run)
            }
            runViewStateStore.put(snapshot, runSummary: run, for: key)
            if let destination = selectedWorkspace,
               destination.workspaceId == key.workspaceID, destination.lastRunId == key.runID,
               workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID,
               selectedConversationID == nil, activeRun == nil {
                installCachedWorkspaceConversation(
                    runViewStateStore.cachedRecords(machineID: key.machineID, workspaceID: key.workspaceID),
                    workspace: destination, machineID: key.machineID)
            }
            scheduleConversationHistoryRead(key: key, run: run, target: target)

            let readRun = run
            let failed = await withTaskGroup(of: ConversationReadEvent.self, returning: Bool.self) { group in
                if let cachedRun = cached?.runSummary, let snapshot = cached?.snapshot {
                    group.addTask { .prepared(await ConversationReadProjection.prepare(run: cachedRun, snapshot: snapshot)) }
                }
                var active = Set<ConversationBranchRead>()
                var attempted: [ConversationBranchRead: ConversationReadRevision] = [:]
                var retryAfter: [ConversationBranchRead: Date] = [:]
                var nextLiveRead: [ConversationBranchRead: Date] = [:]
                var checking = false
                var failed = false
                while self.conversationReadIsValid(key), self.conversationReadTokens[key] == token {
                    let latest = self.currentReadRun(key, fallback: readRun)
                    let current = self.currentReadSnapshot(key)
                    for candidate in latest.agents {
                        for mode in [ConversationReadMode.conversation, .recentWork, .work] {
                            let branch = ConversationBranchRead(agent: candidate.agent,
                                mode: mode)
                            guard !active.contains(branch) else { continue }
                            if mode == .recentWork && !self.needsRecentWorkRange(
                                agent: candidate.agent, run: latest, snapshot: current) { continue }
                            let probeLive = !candidate.isTerminal
                                && (mode != .conversation || selectedAgentDetailNeedsRefresh(run: latest,
                                    selectedAgent: branch.agent, transcripts: current.transcripts))
                                && nextLiveRead[branch, default: .distantPast] <= Date()
                            guard probeLive || self.conversationBranchNeedsRefresh(branch, run: latest,
                                snapshot: current) else {
                                retryAfter.removeValue(forKey: branch)
                                continue
                            }
                            let revision = ConversationReadRevision(eventCount: latest.eventCount,
                                status: candidate.normalizedStatus)
                            guard retryAfter[branch, default: .distantPast] <= Date(),
                                  attempted[branch] != revision || probeLive else { continue }
                            attempted[branch] = revision
                            active.insert(branch)
                            group.addTask {
                                .finished(branch, await self.preloadConversationAgent(branch, key: key,
                                    run: readRun, target: target, token: token, probeLive: probeLive))
                            }
                        }
                    }
                    if active.isEmpty && !latest.isWorking && allAgentsAreTerminal(latest) { break }
                    if !checking {
                        checking = true
                        group.addTask {
                            try? await Task.sleep(for: .milliseconds(100))
                            return .checkMetadata
                        }
                    }
                    guard let event = await group.next() else { break }
                    switch event {
                    case .finished(let branch, let succeeded):
                        active.remove(branch)
                        failed = !succeeded || failed
                        if !succeeded {
                            attempted.removeValue(forKey: branch)
                            retryAfter[branch] = Date().addingTimeInterval(1)
                        } else {
                            retryAfter.removeValue(forKey: branch)
                            nextLiveRead[branch] = Date().addingTimeInterval(3)
                        }
                    case .checkMetadata:
                        checking = false
                    case .prepared(let projections):
                        let current = self.currentReadSnapshot(key)
                        let latest = self.currentReadRun(key, fallback: readRun)
                        for (agent, projection) in projections {
                            let input = TranscriptProjectionInput(runID: latest.runId, runStatus: latest.status,
                                prompt: latest.prompt, history: current.conversationHistory,
                                transcript: agentTranscriptBoundToBranch(current.transcripts[agent], runID: key.runID, agent: agent),
                                runError: latest.error, workerError: latest.workerError, pendingSubmission: nil)
                            if input == projection.input {
                                self.cachePreparedConversation(projection, key: key, run: latest, agent: agent)
                            }
                        }
                    }
                }
                group.cancelAll()
                return failed
            }
            guard conversationReadIsValid(key), conversationReadTokens[key] == token else { return }
            let finalRun = runSummariesByContext[key] ?? run
            let complete = finalRun.agents.allSatisfy {
                !selectedAgentDetailNeedsRefresh(run: finalRun, selectedAgent: $0.agent,
                    transcripts: runViewStateStore.snapshot(for: key)?.transcripts ?? [:])
            }
            if !finalRun.isWorking && allAgentsAreTerminal(finalRun) && complete {
                preloadedConversationKeys.insert(key)
                warmedWorkingConversationKeys.remove(key)
            } else {
                warmedWorkingConversationKeys.insert(key)
                preloadedConversationKeys.remove(key)
            }
            if !failed || complete { conversationPreloadRetryAfter.removeValue(forKey: key) }
            else { conversationPreloadRetryAfter[key] = Date().addingTimeInterval(1) }
        } catch {
            if conversationReadIsValid(key) {
                conversationPreloadRetryAfter[key] = Date().addingTimeInterval(1)
            }
        }
    }

    private func conversationBranchNeedsRefresh(_ branch: ConversationBranchRead,
                                                 run: RunSummary, snapshot: WorkspaceRunViewSnapshot) -> Bool {
        if branch.mode == .recentWork {
            guard needsRecentWorkRange(agent: branch.agent, run: run, snapshot: snapshot) else { return false }
            guard let recent = snapshot.transcripts[branch.agent]?.recentWork else { return true }
            return recent.hasMore || recent.cursor < run.eventCount
        }
        guard branch.mode == .conversation else {
            return selectedAgentDetailNeedsRefresh(run: run, selectedAgent: branch.agent,
                transcripts: snapshot.transcripts)
        }
        if let transcript = snapshot.transcripts[branch.agent],
           transcript.sourceRunID == run.runId, transcript.sourceAgent == branch.agent,
           !transcript.hasMore, transcript.cursor >= run.eventCount {
            return false
        }
        guard let transcript = snapshot.transcripts[branch.agent],
              transcript.sourceRunID == run.runId, transcript.sourceAgent == branch.agent,
              let conversation = transcript.conversation else { return true }
        return conversation.hasMore || conversation.cursor < run.eventCount
            || selectedAgentAnswerNeedsRefresh(run: run, selectedAgent: branch.agent,
                transcripts: snapshot.transcripts)
    }

    private func needsRecentWorkRange(agent: Int, run: RunSummary,
                                      snapshot: WorkspaceRunViewSnapshot) -> Bool {
        guard let transcript = snapshot.transcripts[agent],
              transcript.sourceRunID == run.runId, transcript.sourceAgent == agent,
              let conversation = transcript.conversation,
              conversation.cursor > 0 || !conversation.hasMore else { return false }
        return run.eventCount - transcript.cursor > AgentRecentWork.initialEventWindow
    }

    private func preloadConversationAgent(_ branch: ConversationBranchRead, key: WorkspaceRunViewKey,
                                           run: RunSummary, target: PcrdRequestTarget,
                                           token: UUID, probeLive: Bool) async -> Bool {
        let agent = branch.agent
        let loadingKey = agentLoadingKey(machineID: key.machineID,
            workspaceID: key.workspaceID, runID: key.runID, agent: agent)
            + (branch.mode == .conversation ? "#conversation" : branch.mode == .recentWork ? "#recent-work" : "")
        guard branch.mode != .work || agentDetailLoadTokens[loadingKey] == nil else { return false }
        let readToken = UUID()
        conversationAgentReadTokens[loadingKey] = readToken
        defer {
            if conversationAgentReadTokens[loadingKey] == readToken {
                conversationAgentReadTokens.removeValue(forKey: loadingKey)
            }
        }
        var firstPage = true
        var pagesRead = 0
        let recentHead = currentReadRun(key, fallback: run).eventCount
        while conversationReadIsValid(key), conversationReadTokens[key] == token {
            let current = currentReadSnapshot(key)
            let currentRun = currentReadRun(key, fallback: run)
            guard (firstPage && probeLive) || conversationBranchNeedsRefresh(branch, run: currentRun,
                snapshot: current) else { return true }
            firstPage = false
            let transcript = agentTranscriptBoundToBranch(current.transcripts[agent],
                runID: key.runID, agent: agent)
            let page = await ConversationReadProjection.agentPage(client: client, target: target,
                run: currentRun, agent: agent, transcript: transcript,
                mode: branch.mode)
            guard conversationReadIsValid(key), conversationReadTokens[key] == token else { return true }
            guard page.detail != nil else { return false }
            guard await commitConversationPage(page, key: key, run: currentRun) else { return true }
            pagesRead += 1
            if branch.mode == .recentWork,
               pagesRead >= 2 || (page.detail?.transcriptPage?.nextCursor ?? 0) >= recentHead { return true }
            // Each branch yields after a page. Transport slots stay bounded,
            // but a slow branch never holds back another branch's next page.
            await Task.yield()
        }
        return true
    }

    private func commitConversationPage(_ page: ConversationAgentRead,
                                        key: WorkspaceRunViewKey, run: RunSummary) async -> Bool {
        while conversationReadIsValid(key) {
            let base = currentReadSnapshot(key)
            let transcript = agentTranscriptBoundToBranch(base.transcripts[page.agent],
                runID: key.runID, agent: page.agent)
            let renderRun = runSummariesByContext[key] ?? run
            let input = TranscriptProjectionInput(runID: renderRun.runId, runStatus: renderRun.status,
                prompt: renderRun.prompt, history: base.conversationHistory, transcript: transcript,
                runError: renderRun.error, workerError: renderRun.workerError, pendingSubmission: nil)
            let cached = transcriptPresentationCache.value(
                for: conversationPresentationKey(key: key, run: renderRun, agent: page.agent), matching: input)
            let previous = cached.map { PreparedConversationProjection(input: $0.input, blocks: $0.blocks) }
            guard let merged = await ConversationReadProjection.mergeTranscript(page, into: transcript,
                run: renderRun), merged != transcript else { return false }
            guard conversationReadIsValid(key) else { return false }
            var current = currentReadSnapshot(key)
            guard current.transcripts[page.agent] == base.transcripts[page.agent],
                  current.conversationHistory == base.conversationHistory else { continue }
            current.transcripts[page.agent] = merged
            runViewStateStore.put(current, runSummary: runSummariesByContext[key] ?? run, for: key)
            if selectedWorkspaceID == key.workspaceID, activeRun?.runId == key.runID,
               transcripts[page.agent] != merged {
                transcripts[page.agent] = merged
            }
            // Commit the page before expensive presentation work. Concurrent work
            // progress may supersede a projection, but cannot starve its raw body.
            let prepared = await ConversationReadProjection.prepare(run: renderRun,
                history: current.conversationHistory, transcript: merged, previous: previous)
            guard conversationReadIsValid(key) else { return true }
            let latest = currentReadSnapshot(key)
            let latestRun = currentReadRun(key, fallback: renderRun)
            let latestInput = TranscriptProjectionInput(runID: latestRun.runId, runStatus: latestRun.status,
                prompt: latestRun.prompt, history: latest.conversationHistory,
                transcript: agentTranscriptBoundToBranch(latest.transcripts[page.agent], runID: key.runID, agent: page.agent),
                runError: latestRun.error, workerError: latestRun.workerError, pendingSubmission: nil)
            if latestInput == prepared.input {
                cachePreparedConversation(prepared, key: key, run: latestRun, agent: page.agent)
            }
            return true
        }
        return false
    }

    func scheduleConversationHistoryRead(key: WorkspaceRunViewKey, run: RunSummary,
                                         target: PcrdRequestTarget) {
        guard conversationHistoryTasks[key] == nil,
              runViewStateStore.snapshot(for: key)?.historyLoaded != true else { return }
        let token = UUID()
        conversationHistoryTokens[key] = token
        conversationHistoryTasks[key] = Task(priority: .utility) { [weak self] in
            guard let self else { return nil }
            defer {
                if self.conversationHistoryTokens[key] == token {
                    self.conversationHistoryTokens.removeValue(forKey: key)
                    self.conversationHistoryTasks.removeValue(forKey: key)
                }
            }
            do {
                // History can finish before the parallel transcript reader has
                // hydrated. Never let that first page replace a complete disk cache.
                _ = await self.runViewStateStore.hydrate(for: key)
                guard self.conversationReadIsValid(key), self.conversationHistoryTokens[key] == token,
                      self.runViewStateStore.snapshot(for: key)?.historyLoaded != true else { return nil }
                try await ConversationReadProjection.history(client: self.client,
                    target: target, runID: key.runID) { [weak self] entries, complete in
                    await self?.commitConversationHistoryPage(entries, complete: complete,
                        key: key, run: run, token: token)
                }
                guard self.conversationReadIsValid(key), self.conversationHistoryTokens[key] == token else { return nil }
                let snapshot = self.currentReadSnapshot(key)
                let currentRun = self.currentReadRun(key, fallback: run)
                let prepared = await ConversationReadProjection.prepare(run: currentRun, snapshot: snapshot)
                guard self.conversationReadIsValid(key), self.conversationHistoryTokens[key] == token else { return nil }
                let latest = self.currentReadSnapshot(key), latestRun = self.currentReadRun(key, fallback: run)
                for (agent, projection) in prepared {
                    let input = TranscriptProjectionInput(runID: latestRun.runId, runStatus: latestRun.status,
                        prompt: latestRun.prompt, history: latest.conversationHistory,
                        transcript: agentTranscriptBoundToBranch(latest.transcripts[agent], runID: key.runID, agent: agent),
                        runError: latestRun.error, workerError: latestRun.workerError, pendingSubmission: nil)
                    if projection.input == input {
                        self.cachePreparedConversation(projection, key: key, run: latestRun, agent: agent)
                    }
                }
                return nil
            } catch is CancellationError {
                return nil
            } catch {
                if self.conversationReadIsValid(key) {
                    self.conversationPreloadRetryAfter[key] = Date().addingTimeInterval(1)
                }
                return error.localizedDescription
            }
        }
    }

    private func commitConversationHistoryPage(_ entries: [ResumeHistoryEntry], complete: Bool,
                                              key: WorkspaceRunViewKey, run: RunSummary, token: UUID) {
        guard conversationReadIsValid(key), conversationHistoryTokens[key] == token else { return }
        // Persist history before formatting any branch; live work must not invalidate a successful history read.
        var snapshot = currentReadSnapshot(key)
        snapshot.conversationHistory = mergeConversationHistory(current: snapshot.conversationHistory,
            authoritative: entries)
        snapshot.historyLoaded = complete
        runViewStateStore.put(snapshot, runSummary: currentReadRun(key, fallback: run), for: key)
        if selectedWorkspaceID == key.workspaceID, activeRun?.runId == key.runID,
           resumeHistory != snapshot.conversationHistory {
            resumeHistory = snapshot.conversationHistory
        }
    }

    private func conversationPresentationKey(key: WorkspaceRunViewKey, run: RunSummary,
                                             agent: Int) -> TranscriptPresentationKey {
        let conversationID = selectedWorkspaceID == key.workspaceID && activeRun?.runId == key.runID
            ? selectedConversationID ?? run.conversationKey : run.conversationKey
        return TranscriptPresentationKey(machineID: key.machineID,
            workspaceID: key.workspaceID, conversationID: conversationID, runID: key.runID, agent: agent)
    }

    private func cachePreparedConversation(_ prepared: PreparedConversationProjection,
                                          key: WorkspaceRunViewKey, run: RunSummary, agent: Int) {
        let presentationKey = conversationPresentationKey(key: key, run: run, agent: agent)
        guard transcriptPresentationCache.value(for: presentationKey, matching: prepared.input) == nil else { return }
        presentationProjectionSequence &+= 1
        transcriptPresentationCache.put(TranscriptPresentationSnapshot(input: prepared.input,
            blocks: prepared.blocks, generation: presentationProjectionSequence,
            timelineInput: nil, timelineBlocks: [], timelineGeneration: 0,
            itemsInput: nil, items: [], itemsGeneration: 0), for: presentationKey)
    }
}
