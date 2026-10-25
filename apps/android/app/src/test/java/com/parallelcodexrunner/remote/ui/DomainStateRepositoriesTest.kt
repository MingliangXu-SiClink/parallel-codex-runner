package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.PersistedMobileState
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.RemoteWorkspace
import com.parallelcodexrunner.remote.data.normalized
import com.parallelcodexrunner.remote.data.withoutMac
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import com.parallelcodexrunner.remote.data.generated.AgentCandidateRecord
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot
import com.parallelcodexrunner.remote.data.generated.MachineRecord
import com.parallelcodexrunner.remote.data.generated.TurnRecord
import com.parallelcodexrunner.remote.data.generated.TurnPhase
import com.parallelcodexrunner.remote.data.generated.WorkspaceRecord
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DomainStateRepositoriesTest {
    @Test
    fun onlyVerifiedTurnsOfTheViewedConversationCanAdvanceItsReadingContext() {
        assertTrue(isNextViewedConversationTurn("old", "new", "conversation", "conversation"))
        assertEquals(false, isNextViewedConversationTurn("old", "new", "conversation", "unrelated"))
        assertEquals(false, isNextViewedConversationTurn("old", "new", null, null))
        assertEquals(false, isNextViewedConversationTurn("old", "new", "", ""))
        assertEquals(false, isNextViewedConversationTurn("same", "same", "conversation", "conversation"))
        assertEquals(false, isNextViewedConversationTurn(null, "new", "conversation", "conversation"))
    }

    @Test
    fun viewedHistorySurvivesRestartAndWorkspaceRefreshIndependentlyOfLatestRun() {
        val key = workspaceSettingsKey("machine", "workspace")
        val otherWorkspace = workspaceSettingsKey("machine", "other-workspace")
        val otherMachine = workspaceSettingsKey("other-machine", "workspace")
        val repository = ConversationStateRepository(PersistedMobileState())
        repository.selectRun(key, "viewed-history")
        repository.selectRun(otherWorkspace, "other-history")
        repository.selectRun(otherMachine, "remote-history")
        val encoded = Json.encodeToString(PersistedMobileState(
            viewedRunIdsByWorkspace = repository.viewedRunIdsByWorkspace,
        ).normalized())
        val decoded = Json.decodeFromString<PersistedMobileState>(encoded)
        val restored = ConversationStateRepository(decoded)
        assertEquals("viewed-history", restored.displayedRunId(key, "newest-unrelated-run"))
        assertEquals("other-history", restored.displayedRunId(otherWorkspace, "other-newest"))
        assertEquals("remote-history", restored.displayedRunId(otherMachine, "remote-newest"))
        assertEquals("latest", restored.displayedRunId("unseen", "latest"))
        assertEquals(mapOf(otherMachine to "remote-history"),
            decoded.withoutMac("machine").viewedRunIdsByWorkspace)
    }

    @Test
    fun newConversationAndAcceptedSubmissionClearThePreviousReadingSelection() {
        val key = workspaceSettingsKey("machine", "workspace")
        val repository = ConversationStateRepository(PersistedMobileState())
        repository.selectRun(key, "history")
        repository.detach(key, "NO")
        assertEquals(null, repository.displayedRunId(key, "latest"))
        assertTrue(repository.viewedRunIdsByWorkspace.isEmpty())
        repository.selectRun(key, "history")
        assertTrue(repository.detachedContexts.isEmpty())
        repository.attach(key)
        assertEquals("accepted-run", repository.displayedRunId(key, "accepted-run"))
    }

    @Test
    fun queuedContinuationAdvancesOnlyItsOwnViewedSource() {
        val key = workspaceSettingsKey("machine", "workspace")
        val repository = ConversationStateRepository(PersistedMobileState())
        repository.selectRun(key, "source")
        repository.advanceViewedRun(key, "other-source", "unrelated-run")
        assertEquals("source", repository.displayedRunId(key, "unrelated-run"))
        repository.advanceViewedRun(key, "source", "next-turn")
        assertEquals("next-turn", repository.displayedRunId(key, "latest"))
        repository.detach(key, "NO")
        repository.advanceViewedRun(key, "next-turn", "another-turn")
        assertEquals(null, repository.displayedRunId(key, "another-turn"))
    }

    @Test
    fun malformedAndDetachedReadingSelectionsAreNotRestored() {
        val key = workspaceSettingsKey("machine", "workspace")
        val normalized = PersistedMobileState(
            viewedRunIdsByWorkspace = mapOf(
                key to "history", "invalid" to "run", "machine\u001f" to "run",
                "machine\u001fother" to "  valid-run  ", "machine\u001fempty" to " ",
            ),
            detachedConversationContexts = setOf(key),
        ).normalized()
        assertEquals(mapOf("machine\u001fother" to "valid-run"), normalized.viewedRunIdsByWorkspace)
        assertTrue(Json.decodeFromString<PersistedMobileState>("{}").viewedRunIdsByWorkspace.isEmpty())
    }

    @Test
    fun workspaceRefreshCannotChangeADetachedConversationIdentity() {
        val key = workspaceSettingsKey("machine", "workspace")
        for (session in listOf("NO", "explicit-resume-session")) {
            val repository = ConversationStateRepository(PersistedMobileState())
            repository.detach(key, session)
            val refreshed = RemoteRunSettings(agents = 7, resumeSessionId = "latest-promoted-session")
            val persisted = PersistedMobileState(
                detachedConversationContexts = repository.detachedContexts,
                detachedResumeSessions = repository.detachedResumeSessions,
            ).normalized()
            val decoded = Json.decodeFromString<PersistedMobileState>(Json.encodeToString(persisted))
            val restored = ConversationStateRepository(decoded)
            val draftSettings = restored.settingsForDraft(key, refreshed)
            assertEquals(session, draftSettings.resumeSessionId)
            assertEquals(7, draftSettings.agents)
            assertEquals(refreshed, restored.settingsForDraft("other-workspace", refreshed))
            assertTrue(decoded.withoutMac("machine").detachedResumeSessions.isEmpty())
            restored.attach(key)
            assertEquals(refreshed, restored.settingsForDraft(key, refreshed))
            assertTrue(restored.detachedResumeSessions.isEmpty())
        }
    }

    @Test
    fun acknowledgedNewConversationSettingsPreserveTheDraftAcrossColdStart() {
        val oldWorkspace = RemoteWorkspace(
            workspaceId = "workspace", name = "Workspace", path = "/workspace",
            lastRunId = "old-run",
            settings = RemoteRunSettings(resumeSessionId = "old-session"),
        )
        val workspaceKey = workspaceSettingsKey("machine", "workspace")
        val draftKey = promptDraftContextKey("machine", "workspace", null, "NO")
        val saved = RemoteRunSettings(configured = true, agents = 3, resumeSessionId = "NO")
        val repository = WorkspaceStateRepository(PersistedMobileState(
            cachedWorkspacesByMac = mapOf(
                "machine" to listOf(oldWorkspace),
                "other-machine" to listOf(oldWorkspace),
            ),
        ))
        repository.cacheSavedSettings("machine", "workspace", saved)

        // The pending-settings outbox has been cleared after the server acknowledgement.
        val persisted = PersistedMobileState(
            cachedWorkspacesByMac = repository.cachedByMachine,
            detachedConversationContexts = setOf(workspaceKey),
            promptDrafts = mapOf(draftKey to "Keep my new conversation draft"),
        )
        val restored = WorkspaceStateRepository(persisted)
        val workspace = restored.cachedByMachine.getValue("machine").single()
        assertEquals(saved, workspace.settings)
        assertEquals(null, latestWorkspaceRunId(workspace, conversationDetached = true))
        assertEquals("Keep my new conversation draft", restorePromptDraft(
            persisted.promptDrafts.toMutableMap(), "machine", "workspace", null,
            checkNotNull(workspace.settings).resumeSessionId,
        ))
        assertEquals(oldWorkspace, restored.cachedByMachine.getValue("other-machine").single())
    }

    @Test
    fun generatedSnapshotPopulatesAndRemovesTheFiveLevelStateGraph() {
        val directory = Files.createTempDirectory("pcr-domain-state").toFile()
        val repositories = ClientDomainRepositories(
            PersistedMobileState(),
            WorkspaceRunDiskCache(directory, ByteArray(32) { 7 }),
        )
        val snapshot = ConversationSnapshot(
            schemaVersion = 1,
            machine = MachineRecord(machineId = "machine-1"),
            workspace = WorkspaceRecord(
                workspaceId = "workspace-1",
                machineId = "machine-1",
            ),
            conversation = ConversationRecord(
                conversationId = "conversation-1",
                workspaceId = "workspace-1",
                turnIds = listOf("turn-1"),
            ),
            turn = TurnRecord(
                turnId = "turn-1",
                conversationId = "conversation-1",
                runId = "run-1",
                candidateIds = listOf("candidate-1"),
            ),
            candidates = listOf(
                AgentCandidateRecord(
                    candidateId = "candidate-1",
                    turnId = "turn-1",
                    agentIndex = 1,
                )
            ),
        )

        repositories.ingest(snapshot)

        assertEquals(snapshot.machine, repositories.machines.records["machine-1"])
        assertEquals(snapshot.workspace, repositories.workspaces.records["workspace-1"])
        assertEquals(
            snapshot.conversation,
            repositories.conversations.records["conversation-1"],
        )
        assertEquals(snapshot.turn, repositories.turns.records["turn-1"])
        assertEquals(snapshot.candidates.first(), repositories.candidates.records["candidate-1"])

        repositories.removeWorkspace("workspace-1")

        assertTrue(repositories.workspaces.records.isEmpty())
        assertTrue(repositories.conversations.records.isEmpty())
        assertTrue(repositories.turns.records.isEmpty())
        assertTrue(repositories.candidates.records.isEmpty())
        assertTrue(repositories.machines.records.isNotEmpty())
        directory.deleteRecursively()
    }

    @Test
    fun conversationsAreSortedByMostRecentActivity() {
        val directory = Files.createTempDirectory("pcr-domain-sort").toFile()
        val repositories = ClientDomainRepositories(
            PersistedMobileState(),
            WorkspaceRunDiskCache(directory, ByteArray(32) { 8 }),
        )
        repositories.conversations.replaceWorkspaceRecords(
            "workspace-1",
            listOf(
                ConversationRecord(
                    conversationId = "older",
                    workspaceId = "workspace-1",
                    updatedAt = "2026-08-30T10:00:00Z",
                ),
                ConversationRecord(
                    conversationId = "newer",
                    workspaceId = "workspace-1",
                    updatedAt = "2026-09-01T10:00:00Z",
                ),
                ConversationRecord(
                    conversationId = "elsewhere",
                    workspaceId = "workspace-2",
                    updatedAt = "2026-09-02T10:00:00Z",
                ),
            ),
        )

        assertEquals(
            listOf("newer", "older"),
            repositories.conversations.recordsForWorkspace("workspace-1")
                .map(ConversationRecord::conversationId),
        )
        directory.deleteRecursively()
    }

    @Test
    fun conversationPagesMergeWithoutDroppingCachedOlderRecords() {
        val directory = Files.createTempDirectory("pcr-domain-conversation-pages").toFile()
        val repositories = ClientDomainRepositories(
            PersistedMobileState(),
            WorkspaceRunDiskCache(directory, ByteArray(32) { 9 }),
        )
        repositories.conversations.replaceWorkspaceRecords(
            "workspace-1",
            listOf(
                ConversationRecord(
                    conversationId = "cached-old",
                    workspaceId = "workspace-1",
                    updatedAt = "2026-08-01T10:00:00Z",
                )
            ),
        )

        repositories.conversations.mergeWorkspaceRecords(
            "workspace-1",
            listOf(
                ConversationRecord(
                    conversationId = "fresh-new",
                    workspaceId = "workspace-1",
                    updatedAt = "2026-09-01T10:00:00Z",
                ),
                ConversationRecord(
                    conversationId = "wrong-workspace",
                    workspaceId = "workspace-2",
                    updatedAt = "2026-09-02T10:00:00Z",
                ),
            ),
        )

        assertEquals(
            listOf("fresh-new", "cached-old"),
            repositories.conversations.recordsForWorkspace("workspace-1")
                .map(ConversationRecord::conversationId),
        )
        assertTrue("wrong-workspace" !in repositories.conversations.records)
        directory.deleteRecursively()
    }

    @Test
    fun conversationNavigationPrefersTheActiveTurnThenTheLatestTurn() {
        fun snapshot(turnId: String, index: Int, updatedAt: String) = ConversationSnapshot(
            conversation = ConversationRecord(
                conversationId = "conversation-1",
                workspaceId = "workspace-1",
            ),
            turn = TurnRecord(
                turnId = turnId,
                conversationId = "conversation-1",
                runId = "run-$turnId",
                turnIndex = index,
                phase = TurnPhase.FINALIZED,
                updatedAt = updatedAt,
            ),
        )
        val turns = listOf(
            snapshot("turn-1", 1, "2026-08-30T10:00:00Z"),
            snapshot("turn-2", 2, "2026-09-01T10:00:00Z"),
        )

        assertEquals(
            "turn-1",
            latestConversationSnapshot(
                ConversationRecord(
                    conversationId = "conversation-1",
                    activeTurnId = "turn-1",
                ),
                turns,
            )?.turn?.turnId,
        )
        assertEquals(
            "turn-2",
            latestConversationSnapshot(
                ConversationRecord(conversationId = "conversation-1"),
                turns,
            )?.turn?.turnId,
        )
    }
}
