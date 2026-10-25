import Foundation

@MainActor
extension AppModel {
    /// A registry/cache is a projection, not permission to rewind a known
    /// continuation. Resolve its head using scoped lineage, never cache LRU order.
    func reconciledWorkspaceHead(_ workspace: RemoteWorkspace, machineID: String?) -> RemoteWorkspace {
        let known = runSummariesByContext.compactMap { key, run in
            key.machineID == MachineContextKey.normalizeMachineID(machineID)
                && key.workspaceID == workspace.workspaceId
                && sameConversationWorkspace(run.workspace, workspace.path)
                && !unavailableRunContexts.contains(key) ? run : nil
        }
        guard !workspace.lastRunId.isEmpty,
              let conversation = workspaceConversationSummaries(from: known).first(where: {
                  $0.runs.contains { $0.runId == workspace.lastRunId || $0.continuedFromRunId == workspace.lastRunId }
              }), let latest = conversation.latestRun else { return workspace }
        return workspace.replacingRunSummary(with: latest, startingNewTurn: true)
    }

    func reconciledWorkspaceRegistry(_ incoming: [RemoteWorkspace], revision: Int,
                                    machineID: String) -> [RemoteWorkspace] {
        if isCurrentMachine(machineID), revision < workspaceRevision {
            // Stale membership is no more authoritative than a stale head.
            return workspaces.map { reconciledWorkspaceHead($0, machineID: machineID) }
        }
        return incoming.map { reconciledWorkspaceHead($0, machineID: machineID) }
    }
    static let newConversationSelectionID = "local:new-conversation"

    var selectedConversation: WorkspaceConversationSummary? {
        workspaceConversations.first { $0.id == selectedConversationID }
    }

    var isComposingNewConversation: Bool {
        selectedConversationID == Self.newConversationSelectionID
    }

    var isViewingLatestWorkspaceRun: Bool {
        guard let workspace = selectedWorkspace else { return false }
        if isComposingNewConversation { return true }
        return activeRun?.runId == workspace.lastRunId
    }

    var isViewingLatestConversationTurn: Bool {
        if isComposingNewConversation { return true }
        guard let runID = activeRun?.runId,
              let conversation = selectedConversation else { return false }
        return runID == conversation.latestRunID
    }

    var isViewingEarlierConversationTurn: Bool {
        guard let runID = activeRun?.runId, let conversation = selectedConversation,
              let workspaceID = selectedWorkspaceID,
              historicalConversationTurn == WorkspaceRunViewKey(machineID: currentMachineContextID,
                  workspaceID: workspaceID, runID: runID),
              !conversation.opensResumeHistory,
              conversation.runs.contains(where: { $0.runId == runID }) else { return false }
        return runID != conversation.latestRunID
    }

    func loadWorkspaceConversationIndex(
        workspace: RemoteWorkspace,
        target: PcrdRequestTarget,
        generation: Int
    ) async {
        conversationIndexTask?.cancel()
        conversationTitlesTask?.cancel()
        conversationTitlesTask = Task { [weak self] in
            await self?.refreshConversationTitles(workspaceID: workspace.workspaceId, target: target)
        }
        conversationIndexRequestID += 1
        let requestID = conversationIndexRequestID
        let requestedAt = Date()
        conversationIndexLoading = true
        do {
            async let resumePage: ResumeSessionList? = try? await client.call(
                target: target,
                "codex.resume_sessions",
                body: ResumeSessionsBody(
                    workspaceId: workspace.workspaceId,
                    includeNonInteractive: true,
                    limit: 100
                )
            )
            let page: RunListResponse = try await client.call(
                target: target,
                "runs.list",
                body: ListRunsBody(
                    limit: 100,
                    cursor: nil,
                    workspace: workspace.path
                )
            )
            let discoveredSessions = await resumePage?.sessions ?? []
            guard isCurrentMachine(target.machineID),
                  requestID == conversationIndexRequestID,
                  isCurrentWorkspaceContext(
                    workspace.workspaceId,
                    generation: generation
                  ) else { return }
            let runs = page.runs.filter { $0.workspace == workspace.path }
            if workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID {
                selectedConversationID = nil
            }
            applyWorkspaceConversationIndex(
                runs,
                resumeSessions: discoveredSessions,
                workspace: workspace,
                machineID: target.machineID,
                completeAsOf: page.hasMore == false ? requestedAt : nil
            )
            workspaceEntryDefaultNavigationRequestID = nil
            cacheVisibleRunViewState()
            resumeSessions = discoveredSessions
            guard page.hasMore == true,
                  let cursor = page.nextCursor,
                  !cursor.isEmpty else {
                conversationIndexLoading = false
                conversationIndexTask = nil
                return
            }

            // The newest page is enough to open the current conversation.
            // Older pages continue in the background so a large workspace
            // history never blocks the live Run and composer.
            conversationIndexTask = Task { [weak self] in
                await self?.loadRemainingWorkspaceConversationIndex(
                    workspace: workspace,
                    target: target,
                    requestID: requestID,
                    initialRuns: runs,
                    resumeSessions: discoveredSessions,
                    initialCursor: cursor,
                    requestedAt: requestedAt
                )
            }
        } catch {
            guard requestID == conversationIndexRequestID,
                  isCurrentWorkspaceContext(
                    workspace.workspaceId,
                    generation: generation
                  ) else { return }
            conversationIndexLoading = false
            conversationIndexTask = nil
            workspaceEntryDefaultNavigationRequestID = nil
            if workspaceConversations.isEmpty {
                selectedConversationID = workspace.lastRunId.isEmpty
                    ? Self.newConversationSelectionID
                    : nil
            }
        }
    }

    /// Restores the locally cached conversation index and selected transcript
    /// before the Relay request starts. The server response still replaces or
    /// extends this projection as the source of truth.
    func restoreCachedWorkspaceConversation(
        workspace: RemoteWorkspace,
        machineID: String,
        generation: Int
    ) async {
        guard isCurrentMachine(machineID),
              isCurrentWorkspaceContext(workspace.workspaceId, generation: generation) else { return }
        let navigationRequestID = conversationNavigationRequestID
        let warm = runViewStateStore.cachedRecords(machineID: machineID, workspaceID: workspace.workspaceId)
        if warm.isEmpty {
            // Read the workspace cache together on cold entry. Hydrating only
            // the registry's old parent hides an already committed child.
            let records = await runViewStateStore.hydrateWorkspace(machineID: machineID,
                workspaceID: workspace.workspaceId)
            guard isCurrentMachine(machineID),
                  isCurrentWorkspaceContext(workspace.workspaceId, generation: generation),
                  conversationNavigationRequestID == navigationRequestID else { return }
            if !records.isEmpty {
                installCachedWorkspaceConversation(records, workspace: workspace, machineID: machineID)
                return
            }
        }
        if !warm.isEmpty {
            installCachedWorkspaceConversation(warm, workspace: workspace, machineID: machineID)
            if selectedConversation?.opensResumeHistory == true
                || warm.contains(where: { $0.key.runID == activeRun?.runId && $0.snapshot.historyLoaded == true }) { return }
        }
        let head = reconciledWorkspaceHead(workspace, machineID: machineID)
        let requestedRunID = workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID
            && !head.lastRunId.isEmpty ? head.lastRunId
            : selectedConversation?.latestRunID ?? head.lastRunId
        if !requestedRunID.isEmpty,
           let record = await runViewStateStore.hydrate(for: WorkspaceRunViewKey(machineID: machineID,
                workspaceID: workspace.workspaceId, runID: requestedRunID)) {
            guard isCurrentMachine(machineID),
                  isCurrentWorkspaceContext(workspace.workspaceId, generation: generation),
                  conversationNavigationRequestID == navigationRequestID else { return }
            installCachedWorkspaceConversation(warm + [record], workspace: workspace, machineID: machineID)
            return
        }
        guard isCurrentMachine(machineID),
              isCurrentWorkspaceContext(workspace.workspaceId, generation: generation),
              conversationNavigationRequestID == navigationRequestID else { return }
        if !warm.isEmpty { return }
        let records = await runViewStateStore.hydrateWorkspace(
            machineID: machineID,
            workspaceID: workspace.workspaceId
        )
        guard isCurrentMachine(machineID),
              isCurrentWorkspaceContext(workspace.workspaceId, generation: generation),
              conversationNavigationRequestID == navigationRequestID,
              !records.isEmpty else { return }
        installCachedWorkspaceConversation(
            records,
            workspace: workspace,
            machineID: machineID
        )
    }

    /// Applies a fully hydrated workspace projection without suspension. The
    /// caller may clear the previous workspace immediately before this method;
    /// SwiftUI then observes one completed context swap rather than a blank
    /// intermediate frame.
    @discardableResult
    func installCachedWorkspaceConversation(
        _ records: [WorkspaceRunViewCacheRecord],
        workspace: RemoteWorkspace,
        machineID: String
    ) -> Bool {
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspace.workspaceId else {
            return false
        }
        defer { restoreVisiblePromptSubmission() }
        var records = records.filter { record in
            record.key.machineID == MachineContextKey.normalizeMachineID(machineID)
                && record.key.workspaceID == workspace.workspaceId
                && (record.runSummary.map { run in
                    run.runId == record.key.runID && sameConversationWorkspace(run.workspace, workspace.path)
                } ?? true)
        }
        for (key, run) in runSummariesByContext where key.machineID == MachineContextKey.normalizeMachineID(machineID)
            && key.workspaceID == workspace.workspaceId && sameConversationWorkspace(run.workspace, workspace.path)
            && !records.contains(where: { $0.key == key }) {
            records.append(.init(key: key, runSummary: run, snapshot: currentReadSnapshot(key)))
        }
        // Cache installation is a merge, including when the request is current.
        // A newly initiated hydrate can itself contain older content.
        records = Array(Dictionary(records.map { ($0.key, $0) }, uniquingKeysWith: { current, incoming in
            let run = incoming.runSummary.flatMap { candidate in
                current.runSummary.map { candidate.isOlderProjection(than: $0) ? $0 : candidate } ?? candidate
            } ?? current.runSummary
            return WorkspaceRunViewCacheRecord(key: current.key, runSummary: run,
                snapshot: current.snapshot.mergingVisible(incoming.snapshot, for: current.key))
        }).values).map { record in
            .init(key: record.key,
                runSummary: record.runSummary.map { currentReadRun(record.key, fallback: $0) },
                snapshot: currentReadSnapshot(record.key, fallback: record.snapshot))
        }
        for record in records {
            if let run = record.runSummary { runSummariesByContext[record.key] = run }
        }
        let workspace = reconciledWorkspaceHead(
            workspaces.first(where: { $0.workspaceId == workspace.workspaceId }) ?? workspace, machineID: machineID)
        workspaces = workspaces.map { $0.workspaceId == workspace.workspaceId ? workspace : $0 }
        let runs = records.compactMap(\.runSummary)
        let workspaceKey = machineWorkspaceContextKey(
            workspaceID: workspace.workspaceId,
            machineID: machineID
        )
        let cachedResume = cachedResumeConversation(
            records: records,
            navigationID: selectedConversationByWorkspace[workspaceKey],
            workspace: workspace
        )
        // A warm older page is not this workspace's current destination. Hydrate
        // the exact latest key rather than briefly opening a blocked old task.
        if workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID,
           cachedResume == nil, !workspace.lastRunId.isEmpty,
           !runs.contains(where: { $0.runId == workspace.lastRunId }) {
            return false
        }
        guard !runs.isEmpty || cachedResume != nil else { return false }
        // Window restoration keeps an explicit saved destination. A fresh
        // workspace entry instead uses its most recent cached conversation.
        if selectedConversationID == nil, workspaceEntryDefaultNavigationRequestID == nil {
            selectedConversationID = selectedConversationByWorkspace[workspaceKey]
        }
        for run in runs {
            runSummariesByContext[
                WorkspaceRunViewKey(
                    machineID: machineID,
                    workspaceID: workspace.workspaceId,
                    runID: run.runId
                )
            ] = run
        }
        applyWorkspaceConversationIndex(
            runs,
            resumeSessions: cachedResume.map { [$0.session] } ?? [],
            workspace: workspace,
            machineID: machineID
        )
        let persistedTitlesByRunID = Dictionary(
            records.compactMap { record -> (String, String)? in
                let title = record.snapshot.conversationTitle
                guard let title, !title.isEmpty else { return nil }
                return (record.key.runID, title)
            },
            uniquingKeysWith: { existing, _ in existing }
        )
        let fallbackTitlesByRunID = Dictionary(
            records.compactMap { record -> (String, String)? in
                guard let title = cachedConversationTitle(
                    from: record.snapshot.conversationHistory
                ) else { return nil }
                return (record.key.runID, title)
            },
            uniquingKeysWith: { existing, _ in existing }
        )
        if !persistedTitlesByRunID.isEmpty || !fallbackTitlesByRunID.isEmpty {
            workspaceConversations = workspaceConversations.map { conversation in
                if let title = customConversationTitle(conversation, workspaceID: workspace.workspaceId, machineID: machineID) {
                    var updated = conversation
                    updated.title = title
                    return updated
                }
                guard let title = preferredCachedConversationTitle(
                    runIDs: conversation.runs.map(\.runId),
                    persisted: persistedTitlesByRunID,
                    fallback: fallbackTitlesByRunID
                ) else { return conversation }
                return WorkspaceConversationSummary(
                    id: conversation.id,
                    title: title,
                    runs: conversation.runs,
                    resumeSession: conversation.resumeSession,
                    unavailableRunIDs: conversation.unavailableRunIDs
                )
            }
        }
        if let cachedResume,
           selectedConversation?.opensResumeHistory == true,
           selectedConversation?.resumeSession?.sessionId == cachedResume.session.sessionId {
            settings.resumeSessionID = cachedResume.session.sessionId
            activeRun = nil
            selectedAgent = nil
            transcripts = [:]
            resumeHistory = cachedResume.record.snapshot.conversationHistory
            preparePromptHistoryContext()
            refreshVisibleRunOperationState()
            return true
        }
        let explicitRunID = conversationNavigationTarget.flatMap { destination in
            destination.belongsTo(machineID: machineID, workspaceID: workspace.workspaceId,
                requestID: conversationNavigationRequestID) && !destination.followsLatest
                ? destination.runID : nil
        }
        let visibleRunID = selectedConversation?.opensResumeHistory == true
            ? ""
            : (explicitRunID ?? selectedConversation?.latestRunID
                ?? (isComposingNewConversation ? "" : workspace.lastRunId))
        guard !visibleRunID.isEmpty,
              let record = records.first(where: { $0.key.runID == visibleRunID }),
              var run = record.runSummary else { return true }
        run.reconcileTerminalAgentStates()
        activeRun = run
        transcripts = record.snapshot.transcripts
        resumeHistory = record.snapshot.conversationHistory
        let selectionKey = machineRunContextKey(
            workspaceID: workspace.workspaceId,
            runID: visibleRunID,
            machineID: machineID
        )
        selectedAgent = selectedAgentForRestoredRun(
            explicitSelection: rememberedSelectedAgent(for: run, contextKey: selectionKey),
            cachedSelection: record.snapshot.selectedAgent,
            run: run
        )
        refreshVisibleRunOperationState()
        restoreCachedFollowUpQueue(workspaceID: workspace.workspaceId, runID: run.runId)
        return true
    }

    private func cachedResumeConversation(
        records: [WorkspaceRunViewCacheRecord],
        navigationID: String?,
        workspace: RemoteWorkspace
    ) -> (session: ResumeSession, record: WorkspaceRunViewCacheRecord)? {
        guard let record = cachedResumeConversationRecord(
            from: records,
            navigationID: navigationID
        ) else { return nil }
        let sessionID = String(record.key.runID.dropFirst("resume-session:".count))
        let cachedTitle = record.snapshot.conversationTitle
            ?? cachedConversationTitle(from: record.snapshot.conversationHistory)
            ?? "Previous conversation"
        return (
            ResumeSession(
                sessionId: sessionID,
                title: cachedTitle,
                cwd: workspace.path,
                updatedAt: record.snapshot.conversationUpdatedAt,
                createdAt: nil,
                source: "cache",
                model: "",
                rolloutPath: "",
                preview: cachedTitle,
                tokensUsed: nil
            ),
            record
        )
    }

    private func loadRemainingWorkspaceConversationIndex(
        workspace: RemoteWorkspace,
        target: PcrdRequestTarget,
        requestID: Int,
        initialRuns: [RunSummary],
        resumeSessions: [ResumeSession],
        initialCursor: String,
        requestedAt: Date
    ) async {
        var runs = initialRuns
        var cursor = initialCursor
        defer {
            if requestID == conversationIndexRequestID,
               isCurrentMachine(target.machineID),
               selectedWorkspaceID == workspace.workspaceId {
                conversationIndexLoading = false
                conversationIndexTask = nil
            }
        }
        do {
            while !Task.isCancelled {
                let page: RunListResponse = try await client.call(
                    target: target,
                    "runs.list",
                    body: ListRunsBody(
                        limit: 100,
                        cursor: cursor,
                        workspace: workspace.path
                    )
                )
                guard requestID == conversationIndexRequestID,
                      isCurrentMachine(target.machineID),
                      selectedWorkspaceID == workspace.workspaceId else { return }
                runs.append(contentsOf: page.runs.filter { $0.workspace == workspace.path })
                applyWorkspaceConversationIndex(
                    runs,
                    resumeSessions: resumeSessions,
                    workspace: workspace,
                    machineID: target.machineID,
                    completeAsOf: page.hasMore == false ? requestedAt : nil
                )
                cacheVisibleRunViewState()
                guard page.hasMore == true,
                      let next = page.nextCursor,
                      !next.isEmpty,
                      next != cursor else { return }
                cursor = next
            }
        } catch is CancellationError {
            return
        } catch {
            // The newest page is already usable. Keep it visible and let a
            // later workspace refresh retry loading the older index pages.
        }
    }

    func applyWorkspaceConversationIndex(
        _ fetchedRuns: [RunSummary],
        resumeSessions fetchedSessions: [ResumeSession],
        workspace: RemoteWorkspace,
        machineID: String,
        completeAsOf: Date? = nil
    ) {
        guard isCurrentMachine(machineID), selectedWorkspaceID == workspace.workspaceId else { return }
        // Filter before joining resume lineage: a late refresh or an old cache
        // must never merge another workspace's runs into the visible index.
        let existingRuns = workspaceConversations.flatMap(\.runs)
            .filter { sameConversationWorkspace($0.workspace, workspace.path) }
        let existingSessions = workspaceConversations.compactMap(\.resumeSession)
            .filter { sameConversationWorkspace($0.cwd, workspace.path) }
        let fetchedRuns = fetchedRuns.filter { sameConversationWorkspace($0.workspace, workspace.path) }
        let fetchedSessions = fetchedSessions.filter { sameConversationWorkspace($0.cwd, workspace.path) }
        let sessions = Dictionary(
            (existingSessions + fetchedSessions).map { ($0.sessionId, $0) },
            uniquingKeysWith: { _, newer in newer }
        ).values
        let uniqueRuns = Dictionary(
            (existingRuns + fetchedRuns).map { ($0.runId, $0) },
            uniquingKeysWith: { current, incoming in
                incoming.isOlderProjection(than: current) ? current : incoming
            }
        ).values
        reconcileRunAvailability(
            cachedRuns: existingRuns,
            fetchedRuns: fetchedRuns,
            workspaceID: workspace.workspaceId,
            machineID: machineID,
            completeAsOf: completeAsOf
        )
        let nextConversations = applyingCachedHistoryTitles(
            to: workspaceConversationSummaries(
                from: Array(uniqueRuns),
                resumeSessions: Array(sessions),
                unavailableRunIDs: unavailableRunIDs(
                    workspaceID: workspace.workspaceId, machineID: machineID
                )
            ),
            workspaceID: workspace.workspaceId,
            machineID: machineID
        )
        if workspaceConversations != nextConversations {
            workspaceConversations = nextConversations
        }

        let latestConversation = workspaceEntryDefaultNavigationRequestID == conversationNavigationRequestID
            ? workspaceConversations.first(where: { $0.runs.contains { $0.runId == workspace.lastRunId } })
                ?? workspaceConversations.first
            : workspaceConversations.first
        let visibleConversation = activeRun.flatMap { run in
            workspaceConversations.first { conversation in
                conversation.runs.contains { $0.runId == run.runId }
            }
        }
        let currentSelection = selectedConversationID
        let currentConversation = currentSelection.flatMap {
            conversationMatchingNavigationID($0, in: workspaceConversations)
        }
        let nextSelection: String
        if currentSelection == Self.newConversationSelectionID {
            nextSelection = Self.newConversationSelectionID
        } else if let currentSelection, visibleDeletedConversation != nil,
                  activeRun != nil || !resumeHistory.isEmpty {
            // A later lineage merge may identify the page being read as deleted.
            // Keep the reading position until the user restores or navigates away.
            nextSelection = currentSelection
        } else if visibleDeletedConversation != nil {
            // A saved destination is not a live reading position. Do not reopen
            // deleted history during window or cache restoration.
            nextSelection = Self.newConversationSelectionID
        } else if let currentConversation {
            nextSelection = currentConversation.id
        } else if currentSelection != nil, let visibleConversation {
            // Conversation identities can be repaired when older pages reveal
            // a shared Codex resume lineage. Keep the Run the user is reading
            // selected instead of jumping to another conversation.
            nextSelection = visibleConversation.id
        } else if let currentSelection {
            // A partial page cannot invalidate an explicit destination.
            nextSelection = currentSelection
        } else if let latestConversation {
            nextSelection = latestConversation.id
        } else if let first = workspaceConversations.first {
            nextSelection = first.id
        } else {
            nextSelection = Self.newConversationSelectionID
        }
        let destinationConversation = conversationMatchingNavigationID(nextSelection, in: workspaceConversations)
        let advancesVisibleLatest = pendingPromptSubmission == nil
            && !isViewingEarlierConversationTurn
            && activeRun != nil
            && selectedConversationID == nextSelection
            && destinationConversation.map { conversation in
                activeRun?.runId != conversation.latestRunID
                    && !conversation.latestRunID.isEmpty
                    && (conversationNavigationTarget?.followsLatest == true
                        || (conversationNavigationTarget?.runID.map { $0 == conversation.latestRunID } ?? true))
                    && !conversation.opensResumeHistory
            } == true
        if selectedConversationID != nextSelection || advancesVisibleLatest {
            cacheVisibleRunViewState()
            selectedConversationID = nextSelection
            if let conversation = selectedConversation {
                if let destination = conversationNavigationTarget,
                   destination.belongsTo(machineID: machineID, workspaceID: workspace.workspaceId,
                       requestID: conversationNavigationRequestID),
                   destination.conversationID == currentSelection,
                   currentConversation?.id == nextSelection,
                   destination.runID.map({ id in conversation.runs.contains { $0.runId == id } }) ?? true {
                    conversationNavigationTarget = ConversationNavigationTarget(requestID: destination.requestID,
                        machineID: destination.machineID, workspaceID: destination.workspaceID,
                        conversationID: nextSelection, runID: destination.runID,
                        followsLatest: destination.followsLatest)
                }
                if advancesVisibleLatest || activeRun.map({ visible in
                    !conversation.runs.contains(where: { $0.runId == visible.runId })
                }) == true {
                    // An index may select a newer conversation, but must never
                    // expose its identity with the previous conversation's Run.
                    clearVisibleConversationProjection(invalidateRequests: false)
                    let records = runViewStateStore.cachedRecords(machineID: machineID, workspaceID: workspace.workspaceId)
                    if conversation.opensResumeHistory, let session = conversation.resumeSession {
                        settings.resumeSessionID = session.sessionId
                        let key = resumeConversationViewKey(machineID: machineID,
                            workspaceID: workspace.workspaceId, sessionID: session.sessionId)
                        resumeHistory = records.first { $0.key == key }?.snapshot.conversationHistory ?? []
                    } else if !installCachedConversationProjection(
                        records.first { $0.key.runID == conversation.latestRunID },
                        conversation: conversation, workspace: workspace) {
                        _ = installIndexedConversationProjection(conversation, workspace: workspace)
                    }
                }
            }
            // Restore the destination draft in the same MainActor update that
            // selects its conversation. Cached transcript content is installed
            // immediately afterward, so the first visible frame is coherent.
            preparePromptHistoryContext()
        }
    }

    func selectConversation(_ conversationID: String) async {
        workspaceEntryDefaultNavigationRequestID = nil
        guard let workspace = selectedWorkspace,
              conversationID == Self.newConversationSelectionID
                || workspaceConversations.contains(where: { $0.id == conversationID }) else {
            return
        }

        if selectedConversationID == conversationID {
            defer {
                restoreVisiblePromptSubmission()
                resumePendingPromptSubmissions()
            }
            guard conversationID != Self.newConversationSelectionID,
                  let conversation = workspaceConversations.first(where: {
                      $0.id == conversationID
                  }) else { return }
            if conversation.opensResumeHistory,
               let session = conversation.resumeSession {
                if resumeHistory.isEmpty {
                    let navigationRequestID = beginConversationNavigationRequest(
                        workspace: workspace, conversationID: conversationID, runID: nil, followsLatest: true
                    )
                    defer { finishConversationNavigationRequest(navigationRequestID) }
                    await openResumeConversation(
                        session,
                        workspace: workspace,
                        conversationID: conversationID,
                        navigationRequestID: navigationRequestID
                    )
                }
                return
            }
            guard !conversation.latestRunID.isEmpty,
                  activeRun?.runId != conversation.latestRunID else { return }
            await selectConversationTurn(conversation.latestRunID)
            return
        }

        let navigationRequestID = beginConversationNavigationRequest(
            workspace: workspace, conversationID: conversationID,
            runID: workspaceConversations.first { $0.id == conversationID }.flatMap {
                $0.opensResumeHistory || $0.latestRunID.isEmpty ? nil : $0.latestRunID
            }, followsLatest: true
        )
        defer { finishConversationNavigationRequest(navigationRequestID) }
        let projection = await cachedConversationProjection(
            conversationID: conversationID,
            workspace: workspace
        )
        guard selectedWorkspaceID == workspace.workspaceId,
              navigationRequestID == conversationNavigationRequestID,
              openingConversationID == conversationID else { return }

        cacheVisibleRunViewState()
        clearVisibleConversationProjection()
        errorMessage = nil
        selectedConversationID = conversationID
        rememberConversationNavigation()
        preparePromptHistoryContext()

        if conversationID == Self.newConversationSelectionID {
            restoreVisiblePromptSubmission()
            // Focus as soon as the empty conversation becomes visible. Prompt
            // history may still be loading and must not delay the first keystroke.
            promptFocusRequestSequence &+= 1
            settings.resumeSessionID = "NO"
            rememberCurrentSettings()
            preparePromptHistoryContext()
            await loadPromptHistory()
            if selectedConversationID == conversationID { resumePendingPromptSubmissions() }
            return
        }

        guard let conversation = workspaceConversations.first(where: {
            $0.id == conversationID
        }) else { return }
        let installedCachedProjection = installCachedConversationProjection(
            projection,
            conversation: conversation,
            workspace: workspace
        )
        if !installedCachedProjection {
            // runs.list already gave us the destination Turn. Use that
            // lightweight projection immediately while its full transcript is
            // fetched, rather than replacing the previous conversation with a
            // blank loading surface.
            _ = installIndexedConversationProjection(
                conversation,
                workspace: workspace
            )
            workspaceContextLoadingID = workspace.workspaceId
        }
        restoreCachedFollowUpQueue(workspaceID: workspace.workspaceId, runID: activeRun?.runId)
        restoreVisiblePromptSubmission()
        defer {
            if navigationRequestID == conversationNavigationRequestID,
               selectedWorkspaceID == workspace.workspaceId,
               selectedConversationID == conversationID,
               workspaceContextLoadingID == workspace.workspaceId {
                workspaceContextLoadingID = nil
            }
            if selectedConversationID == conversationID { resumePendingPromptSubmissions() }
        }
        if conversation.opensResumeHistory,
           let session = conversation.resumeSession {
            await openResumeConversation(
                session,
                workspace: workspace,
                conversationID: conversationID,
                navigationRequestID: navigationRequestID
            )
            return
        }
        guard !conversation.latestRunID.isEmpty else { return }
        await openConversationRun(
            conversation.latestRunID,
            workspace: workspace,
            conversationID: conversationID,
            navigationRequestID: navigationRequestID
        )
    }

    private func cachedConversationProjection(
        conversationID: String,
        workspace: RemoteWorkspace
    ) async -> WorkspaceRunViewCacheRecord? {
        guard conversationID != Self.newConversationSelectionID,
              let conversation = workspaceConversations.first(where: {
                  $0.id == conversationID
              }) else { return nil }
        let key: WorkspaceRunViewKey
        if conversation.opensResumeHistory,
           let session = conversation.resumeSession {
            key = resumeConversationViewKey(
                machineID: currentMachineContextID,
                workspaceID: workspace.workspaceId,
                sessionID: session.sessionId
            )
        } else {
            guard !conversation.latestRunID.isEmpty else { return nil }
            key = WorkspaceRunViewKey(
                machineID: currentMachineContextID,
                workspaceID: workspace.workspaceId,
                runID: conversation.latestRunID
            )
        }
        let record = await runViewStateStore.hydrate(for: key)
        // The index may discover a continuation while disk I/O is suspended.
        // A cached member of the lineage is not necessarily its latest turn.
        guard let current = workspaceConversations.first(where: { $0.id == conversationID }),
              current.opensResumeHistory == conversation.opensResumeHistory,
              current.opensResumeHistory || current.latestRunID == key.runID else { return nil }
        return record
    }

    @discardableResult
    private func installCachedConversationProjection(
        _ record: WorkspaceRunViewCacheRecord?,
        conversation: WorkspaceConversationSummary,
        workspace: RemoteWorkspace,
        runID: String? = nil
    ) -> Bool {
        guard let record, record.key.machineID == currentMachineContextID,
              record.key.workspaceID == workspace.workspaceId else { return false }
        if conversation.opensResumeHistory {
            guard let session = conversation.resumeSession,
                  record.key == resumeConversationViewKey(machineID: currentMachineContextID,
                    workspaceID: workspace.workspaceId, sessionID: session.sessionId) else { return false }
            settings.resumeSessionID = session.sessionId
            resumeHistory = record.snapshot.conversationHistory
            return !resumeHistory.isEmpty
        }
        guard var run = record.runSummary, run.runId == record.key.runID,
              run.runId == (runID ?? conversation.latestRunID),
              sameConversationWorkspace(run.workspace, workspace.path),
              conversation.runs.contains(where: { $0.runId == run.runId }) else { return false }
        run = currentReadRun(record.key, fallback: run)
        let snapshot = currentReadSnapshot(record.key, fallback: record.snapshot)
        resumeHistory = snapshot.conversationHistory
        run.reconcileTerminalAgentStates()
        activeRun = run
        runSummariesByContext[
            WorkspaceRunViewKey(
                machineID: currentMachineContextID,
                workspaceID: workspace.workspaceId,
                runID: run.runId
            )
        ] = run
        transcripts = snapshot.transcripts
        let selectionKey = machineRunContextKey(
            workspaceID: workspace.workspaceId,
            runID: run.runId,
            machineID: currentMachineContextID
        )
        selectedAgent = selectedAgentForRestoredRun(
            explicitSelection: rememberedSelectedAgent(
                for: run,
                contextKey: selectionKey
            ),
            cachedSelection: snapshot.selectedAgent,
            run: run
        )
        refreshVisibleRunOperationState()
        return true
    }

    @discardableResult
    func installIndexedConversationProjection(
        _ conversation: WorkspaceConversationSummary,
        workspace: RemoteWorkspace
    ) -> Bool {
        guard !conversation.opensResumeHistory,
              var run = conversation.latestRun else { return false }
        run.reconcileTerminalAgentStates()
        activeRun = run
        transcripts = [:]
        resumeHistory = []
        let key = WorkspaceRunViewKey(
            machineID: currentMachineContextID,
            workspaceID: workspace.workspaceId,
            runID: run.runId
        )
        runSummariesByContext[key] = run
        let selectionKey = machineRunContextKey(
            workspaceID: workspace.workspaceId,
            runID: run.runId,
            machineID: currentMachineContextID
        )
        selectedAgent = selectedAgentForRestoredRun(
            explicitSelection: rememberedSelectedAgent(
                for: run,
                contextKey: selectionKey
            ),
            cachedSelection: nil,
            run: run
        )
        refreshVisibleRunOperationState()
        return true
    }

    private func openResumeConversation(
        _ session: ResumeSession,
        workspace: RemoteWorkspace,
        conversationID: String,
        navigationRequestID: Int
    ) async {
        guard navigationRequestID == conversationNavigationRequestID else { return }
        settings.resumeSessionID = session.sessionId
        rememberCurrentSettings()
        preparePromptHistoryContext()
        await loadResumeHistory(session.sessionId, reportErrors: true)
        guard selectedWorkspaceID == workspace.workspaceId,
              navigationRequestID == conversationNavigationRequestID,
              selectedConversationID == conversationID else { return }
        await loadPromptHistory()
    }

    func selectConversationTurn(_ runID: String) async {
        guard let workspace = selectedWorkspace,
              let conversation = selectedConversation,
              conversation.runs.contains(where: { $0.runId == runID }),
              activeRun?.runId != runID else { return }

        let conversationID = conversation.id
        let navigationRequestID = beginConversationNavigationRequest(
            workspace: workspace, conversationID: conversationID, runID: runID,
            followsLatest: runID == conversation.latestRunID
        )
        defer { finishConversationNavigationRequest(navigationRequestID) }
        let cacheKey = WorkspaceRunViewKey(
            machineID: currentMachineContextID,
            workspaceID: workspace.workspaceId,
            runID: runID
        )
        let cached = await runViewStateStore.hydrate(for: cacheKey)
        guard selectedWorkspaceID == workspace.workspaceId,
              navigationRequestID == conversationNavigationRequestID,
              selectedConversationID == conversationID,
              selectedConversation?.runs.contains(where: { $0.runId == runID }) == true else {
            return
        }
        if !navigationAcceptsRun(runID, requestID: navigationRequestID) {
            await openConversationRun(runID, workspace: workspace,
                conversationID: conversationID, navigationRequestID: navigationRequestID)
            return
        }

        cacheVisibleRunViewState()
        errorMessage = nil
        selectedConversationID = conversationID
        rememberConversationNavigation()
        if cached != nil {
            clearVisibleConversationProjection()
            _ = installCachedConversationProjection(
                cached,
                conversation: conversation,
                workspace: workspace,
                runID: runID
            )
            restoreCachedFollowUpQueue(workspaceID: workspace.workspaceId, runID: runID)
        } else {
            // Keep the current Turn visible until the target summary arrives.
            // loadRun replaces the Run and its transcript in one MainActor
            // update, avoiding an empty frame for uncached history.
            workspaceContextLoadingID = workspace.workspaceId
        }
        defer {
            if navigationRequestID == conversationNavigationRequestID,
               selectedWorkspaceID == workspace.workspaceId,
               selectedConversationID == conversationID,
               workspaceContextLoadingID == workspace.workspaceId {
                workspaceContextLoadingID = nil
            }
        }
        await openConversationRun(
            runID,
            workspace: workspace,
            conversationID: conversationID,
            navigationRequestID: navigationRequestID
        )
    }

    private func beginConversationNavigationRequest(
        workspace: RemoteWorkspace, conversationID: String, runID: String?, followsLatest: Bool = false
    ) -> Int {
        workspaceEntryDefaultNavigationRequestID = nil
        conversationNavigationRequestID += 1
        historicalConversationTurn = !followsLatest ? runID.map {
            WorkspaceRunViewKey(machineID: currentMachineContextID,
                workspaceID: workspace.workspaceId, runID: $0)
        } : nil
        conversationNavigationTarget = ConversationNavigationTarget(
            requestID: conversationNavigationRequestID, machineID: currentMachineContextID,
            workspaceID: workspace.workspaceId, conversationID: conversationID, runID: runID,
            followsLatest: followsLatest
        )
        // History requests otherwise remain valid across a switch to a blank
        // conversation and can repopulate it with the previous transcript.
        resumeHistoryRequestID += 1
        return conversationNavigationRequestID
    }

    private func finishConversationNavigationRequest(_ requestID: Int) {
        guard conversationNavigationTarget?.requestID == requestID else { return }
        conversationNavigationTarget = nil
    }

    func navigationAcceptsRun(_ runID: String, requestID: Int?) -> Bool {
        guard let requestID else { return true }
        guard requestID == conversationNavigationRequestID else { return false }
        guard let destination = conversationNavigationTarget else {
            // Background refreshes have no explicit navigation target, but
            // their response must still belong to the current latest turn.
            return selectedConversation.map {
                !$0.opensResumeHistory && $0.latestRunID == runID
            } ?? false
        }
        guard destination.belongsTo(machineID: currentMachineContextID,
            workspaceID: selectedWorkspaceID, requestID: requestID) else { return false }
        let expected = destination.followsLatest
            ? conversationMatchingNavigationID(destination.conversationID,
                in: workspaceConversations)?.latestRunID ?? destination.runID
            : destination.runID
        return expected == nil || expected == runID
    }

    private func openConversationRun(
        _ runID: String,
        workspace: RemoteWorkspace,
        conversationID: String,
        navigationRequestID: Int
    ) async {
        guard navigationRequestID == conversationNavigationRequestID else { return }
        var requestedRunID = runID
        while navigationRequestID == conversationNavigationRequestID && !Task.isCancelled {
            if conversationNavigationTarget?.followsLatest == true,
               let latest = conversationMatchingNavigationID(conversationID,
                    in: workspaceConversations)?.latestRunID, !latest.isEmpty {
                requestedRunID = latest
            }
            await openConversationRunPage(requestedRunID, workspace: workspace,
                conversationID: conversationID, navigationRequestID: navigationRequestID)
            guard conversationNavigationTarget?.followsLatest == true,
                  !navigationAcceptsRun(requestedRunID, requestID: navigationRequestID) else { return }
        }
    }

    private func openConversationRunPage(
        _ runID: String, workspace: RemoteWorkspace, conversationID: String,
        navigationRequestID: Int
    ) async {
        do {
            try await loadRun(
                runID,
                loadDetailPages: 20,
                allowUnbound: true,
                expectedWorkspaceID: workspace.workspaceId,
                expectedNavigationRequestID: navigationRequestID
            )
            guard activeRun?.runId == runID,
                  navigationAcceptsRun(runID, requestID: navigationRequestID),
                  navigationRequestID == conversationNavigationRequestID,
                  selectedConversationID == conversationID else { return }
            restoreFollowUpQueue(
                workspaceID: workspace.workspaceId,
                runID: runID
            )
            // Live state must not wait for a long historical transcript to
            // finish paging. History now appears progressively underneath it.
            resumeEventPollingIfNeeded()
            await loadRunHistory(runID, reportErrors: true, waitForCompletion: false)
            guard navigationAcceptsRun(runID, requestID: navigationRequestID),
                  activeRun?.runId == runID else { return }
            preparePromptHistoryContext()
            await loadPromptHistory()
        } catch {
            guard selectedWorkspaceID == workspace.workspaceId,
                  navigationAcceptsRun(runID, requestID: navigationRequestID),
                  navigationRequestID == conversationNavigationRequestID,
                  selectedConversationID == conversationID else { return }
            let detail = appLanguage.userMessage(for: error)
            errorMessage = appLanguage.text(
                "无法打开此对话。\(detail)",
                "This conversation could not be opened. \(detail)"
            )
        }
    }

    func beginNewConversation() async {
        if isComposingNewConversation {
            promptFocusRequestSequence &+= 1
            return
        }
        await selectConversation(Self.newConversationSelectionID)
    }

    var newConversationBlockReason: String? {
        // Opening a draft never mutates the workspace or accepts a result.
        // Concurrent writes are checked when the message is actually submitted.
        return nil
    }

    func openLatestWorkspaceConversation() async {
        guard let workspace = selectedWorkspace else {
            await beginNewConversation()
            return
        }
        let conversation = workspace.hasUnresolvedResultSelection
            ? workspaceConversations.first(where: {
                $0.runs.contains { $0.runId == workspace.lastRunId }
            })
            : workspaceConversations.first
        guard let conversation else {
            await beginNewConversation()
            return
        }
        await selectConversation(conversation.id)
    }

    func openLatestSelectedConversationTurn() async {
        guard let conversation = selectedConversation else {
            await openLatestWorkspaceConversation()
            return
        }
        if conversation.opensResumeHistory {
            await selectConversation(conversation.id)
        } else if !conversation.latestRunID.isEmpty {
            await selectConversationTurn(conversation.latestRunID)
        }
    }

    func recordRunInConversationIndex(_ run: RunSummary, select: Bool = true) {
        guard let workspace = selectedWorkspace,
              sameConversationWorkspace(run.workspace, workspace.path) else { return }
        if let current = workspaceConversations.lazy.flatMap(\.runs).first(where: { $0.runId == run.runId }),
           run.isOlderProjection(than: current) { return }
        if !select, run.isWorking,
           let previous = workspaceConversations.lazy.flatMap(\.runs).first(where: { $0.runId == run.runId }),
           previous.readerState == run.readerState {
            return
        }
        if let record = run.conversationSnapshot?.conversation, record.workspaceId == workspace.workspaceId {
            ingestConversationTitles([record], machineID: currentMachineContextID)
        }
        let existing = workspaceConversations.flatMap(\.runs)
            .filter { $0.runId != run.runId && sameConversationWorkspace($0.workspace, workspace.path) }
        let summaries = workspaceConversationSummaries(
            from: existing + [run],
            resumeSessions: workspaceConversations.compactMap(\.resumeSession)
                .filter { sameConversationWorkspace($0.cwd, workspace.path) },
            unavailableRunIDs: unavailableRunIDs(
                workspaceID: selectedWorkspaceID ?? "", machineID: currentMachineContextID
            )
        )
        let nextConversations = applyingCachedHistoryTitles(
            to: summaries,
            workspaceID: workspace.workspaceId,
            machineID: currentMachineContextID
        )
        if workspaceConversations != nextConversations {
            workspaceConversations = nextConversations
        }
        if select {
            guard let nextConversationID = workspaceConversations.first(where: {
                $0.runs.contains { $0.runId == run.runId }
            })?.id else { return }
            if selectedConversationID != nextConversationID {
                selectedConversationID = nextConversationID
                rememberConversationNavigation()
            }
        }
    }

    func applyingCachedHistoryTitles(
        to conversations: [WorkspaceConversationSummary],
        workspaceID: String,
        machineID: String
    ) -> [WorkspaceConversationSummary] {
        conversationTrash.visible(conversations, machineID: machineID, workspaceID: workspaceID).map { conversation in
            if let title = customConversationTitle(conversation, workspaceID: workspaceID, machineID: machineID) {
                var updated = conversation
                updated.title = title
                return updated
            }
            let cachedTitle = conversation.runs.lazy.compactMap { run in
                self.runViewStateStore.snapshot(
                    for: WorkspaceRunViewKey(
                        machineID: machineID,
                        workspaceID: workspaceID,
                        runID: run.runId
                    )
                ).flatMap { cachedConversationTitle(from: $0.conversationHistory) }
            }.first
            guard let cachedTitle else { return conversation }
            return WorkspaceConversationSummary(
                id: conversation.id,
                title: cachedTitle,
                runs: conversation.runs,
                resumeSession: conversation.resumeSession,
                unavailableRunIDs: conversation.unavailableRunIDs
            )
        }
    }

    func applyVisibleConversationHistoryTitle(forRunID runID: String) -> String? {
        guard let owningConversation = conversationMatchingNavigationID(
            runID,
            in: workspaceConversations
        ) else { return nil }
        if let workspaceID = selectedWorkspaceID,
           let title = customConversationTitle(owningConversation, workspaceID: workspaceID, machineID: currentMachineContextID) {
            return title
        }
        guard let title = cachedConversationTitle(from: resumeHistory) else {
            return owningConversation.title
        }
        guard owningConversation.title != title else { return title }
        let nextConversations = workspaceConversations.map { conversation in
            guard conversation.id == owningConversation.id,
                  conversation.title != title else { return conversation }
            return WorkspaceConversationSummary(
                id: conversation.id,
                title: title,
                runs: conversation.runs,
                resumeSession: conversation.resumeSession,
                unavailableRunIDs: conversation.unavailableRunIDs
            )
        }
        if workspaceConversations != nextConversations {
            workspaceConversations = nextConversations
        }
        return title
    }

    func refreshLatestWorkspaceConversation(
        followRemoteLatest: Bool = false
    ) async {
        guard followRemoteLatest || !isViewingEarlierConversationTurn else { return }
        guard let workspace = selectedWorkspace,
              !workspace.lastRunId.isEmpty else { return }
        let runID = workspace.lastRunId
        let target = client.currentTarget()
        guard !unavailableRunIDs(
            workspaceID: workspace.workspaceId, machineID: target.machineID
        ).contains(runID) else { return }
        let generation = workspaceContextGeneration
        let navigationRequestID = conversationNavigationRequestID
        let previousSelectedConversationLatestRunID = selectedConversation?.latestRunID
        let known = workspaceConversations
            .flatMap(\.runs)
            .first { $0.runId == runID }
        let run: RunSummary
        do {
            run = try await client.call(
                target: target,
                "turns.get",
                body: GetRunBody(
                    runId: runID,
                    includeEvents: false,
                    cursor: 0,
                    eventLimit: 1
                )
            )
        } catch {
            guard navigationRequestID == conversationNavigationRequestID,
                  isCurrentMachine(target.machineID),
                  isCurrentWorkspaceContext(workspace.workspaceId, generation: generation) else { return }
            if (error as? PcrdClientError)?.isMissingRun(runID) == true {
                _ = await recoverUnavailableRunHistory(runID, target: target)
                return
            }
            guard let known else { return }
            run = known
        }
        guard isCurrentMachine(target.machineID),
              navigationRequestID == conversationNavigationRequestID,
              isCurrentWorkspaceContext(
                  workspace.workspaceId,
                  generation: generation
              ) else { return }
        recordRunInConversationIndex(run, select: false)
        // The workspace pointer can lag behind its conversation's actual turns.
        // Refresh facts about that older run, but never navigate backwards to it.
        guard let latestConversation = workspaceConversations.first(where: {
            $0.runs.contains { $0.runId == run.runId }
        }), latestConversation.latestRunID == run.runId else { return }
        if followRemoteLatest {
            if selectedConversationID != latestConversation.id {
                selectedConversationID = latestConversation.id
                rememberConversationNavigation()
            }
        } else {
            guard conversationSelectionContainsRun(
                workspaceConversations,
                selectedConversationID: selectedConversationID,
                runID: run.runId
            ) else { return }
        }
        let repairsImplicitLatest = !isViewingEarlierConversationTurn
            && selectedConversation?.opensResumeHistory != true
            && activeRun.map { visible in latestConversation.runs.contains { $0.runId == visible.runId } } == true
        guard repairsImplicitLatest || conversationRefreshMayOpenLatestTurn(
            visibleRunID: activeRun?.runId,
            previousLatestRunID: previousSelectedConversationLatestRunID,
            latestRunID: run.runId,
            opensResumeHistory: selectedConversation?.opensResumeHistory == true
        ) else {
            // Navigation owns both explicitly opened historical Turns and a
            // newer direct Codex continuation. Polling the last PCR Turn must
            // not replace either surface with older content.
            return
        }
        if activeRun?.runId == run.runId {
            if visibleRunRefreshNeedsReload(
                current: activeRun,
                remote: run,
                selectedAgent: selectedAgent,
                transcripts: transcripts
            ) {
                try? await loadRun(
                    run.runId,
                    loadDetailPages: 4,
                    allowUnbound: false,
                    expectedWorkspaceID: workspace.workspaceId,
                    expectedNavigationRequestID: navigationRequestID,
                    target: target
                )
            }
            // A stable summary does not need transcript pagination, but an
            // interrupted live poller must still be recreated for active work.
            resumeEventPollingIfNeeded()
            return
        }
        cacheVisibleRunViewState()
        do {
            try await loadRun(
                run.runId,
                loadDetailPages: 20,
                allowUnbound: true,
                expectedWorkspaceID: workspace.workspaceId,
                expectedNavigationRequestID: navigationRequestID,
                target: target
            )
            guard navigationRequestID == conversationNavigationRequestID,
                  activeRun?.runId == run.runId else { return }
            resumeEventPollingIfNeeded()
            await loadRunHistory(run.runId)
            guard navigationRequestID == conversationNavigationRequestID,
                  activeRun?.runId == run.runId else { return }
            restoreFollowUpQueue(
                workspaceID: workspace.workspaceId,
                runID: run.runId
            )
        } catch {
            guard isCurrentMachine(target.machineID),
                  navigationAcceptsRun(run.runId, requestID: navigationRequestID),
                  isCurrentWorkspaceContext(
                      workspace.workspaceId,
                      generation: generation
                  ),
                  conversationSelectionContainsRun(
                      workspaceConversations,
                      selectedConversationID: selectedConversationID,
                      runID: run.runId
                  ) else { return }
            let detail = appLanguage.userMessage(for: error)
            errorMessage = appLanguage.text(
                "无法打开最新一轮。\(detail)",
                "The latest turn could not be opened. \(detail)"
            )
        }
    }

    func rememberWorkspaceSelection() {
        guard let workspaceID = selectedWorkspaceID else { return }
        selectedWorkspaceByMachine[currentMachineContextID] = workspaceID
        // Workspace bootstrap resets selectedConversationID before the local
        // cache and server index are hydrated. Persisting that temporary nil
        // used to delete the user's saved conversation on every app launch or
        // workspace switch.
        self.desktopStateStore.saveConversationNavigation(
            selectedWorkspaceByMachine: selectedWorkspaceByMachine,
            selectedConversationByWorkspace: selectedConversationByWorkspace
        )
    }

    func rememberedWorkspaceID(
        machineID: String,
        available workspaces: [RemoteWorkspace]
    ) -> String? {
        let remembered = selectedWorkspaceByMachine[
            MachineContextKey.normalizeMachineID(machineID)
        ]
        return remembered.flatMap { value in
            workspaces.contains { $0.workspaceId == value } ? value : nil
        }
    }

    func rememberConversationNavigation() {
        if let workspaceID = selectedWorkspaceID {
            let workspaceKey = machineWorkspaceContextKey(workspaceID: workspaceID)
            if let selectedConversationID {
                selectedConversationByWorkspace[workspaceKey] = selectedConversationID
            } else {
                selectedConversationByWorkspace.removeValue(forKey: workspaceKey)
            }
        }
        self.desktopStateStore.saveConversationNavigation(
            selectedWorkspaceByMachine: selectedWorkspaceByMachine,
            selectedConversationByWorkspace: selectedConversationByWorkspace
        )
    }

    func clearVisibleConversationProjection(invalidateRequests: Bool = true) {
        if invalidateRequests { workspaceContextGeneration += 1 }
        remoteTurnTransition = nil
        liveConversationWarning = nil
        pendingPromptSubmission = nil
        storageConfirmationMessage = nil
        clearFollowUpQueue(persist: false)
        diffPresented = false
        diffLoading = false
        diffText = ""
        activeRun = nil
        selectedAgent = nil
        pendingAgentSelection = nil
        agentSelectionRequestID += 1
        transcripts = [:]
        agentDetailLoadTokens.removeAll()
        agentAnswerLoadTokens.removeAll()
        agentAnswerReadRetries.removeAll()
        loadingAgents.removeAll()
        loadingAgentKeys.removeAll()
        resumeHistory = []
        resumeHistoryRequestID += 1
        refreshVisibleRunOperationState()
    }
}
