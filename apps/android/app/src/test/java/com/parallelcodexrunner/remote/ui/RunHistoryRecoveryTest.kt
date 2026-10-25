package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.ConversationDetailResponse
import com.parallelcodexrunner.remote.data.PersistedMobileState
import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.RemoteRunSettings
import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import com.parallelcodexrunner.remote.data.ResumeHistoryPage
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import com.parallelcodexrunner.remote.data.generated.ConversationSnapshot
import com.parallelcodexrunner.remote.data.generated.TurnPhase
import com.parallelcodexrunner.remote.data.generated.TurnRecord
import java.io.IOException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class RunHistoryRecoveryTest {
    private val key = WorkspaceRunKey("mac", "workspace", "old-run")
    private val conversation = ConversationRecord(
        conversationId = "conversation", workspaceId = key.workspaceId,
        activeTurnId = "turn", resumeSessionId = "adopted", turnIds = listOf("turn"),
    )
    private val turn = TurnRecord(
        turnId = "turn", runId = key.runId, conversationId = conversation.conversationId,
        phase = TurnPhase.FINALIZED,
    )
    private fun detail() = ConversationDetailResponse(
        conversation, listOf(ConversationSnapshot(conversation = conversation, turn = turn)),
    )

    @Test fun onlyAnExactMissingExecutionCanTriggerRecovery() {
        assertTrue(isMissingRun(RemoteCommandException("command_failed", "Unknown PCR run: old-run"), key.runId))
        assertTrue(isMissingRun(RemoteCommandException("run_not_found", "missing"), key.runId))
        for (error in listOf(
            IOException("Unknown PCR run: old-run"),
            RemoteCommandException("command_failed", "Unknown PCR run: another-run"),
            RemoteCommandException("agent_not_found", "missing agent"),
            RemoteCommandException("permission_denied", "not permitted"),
            RemoteCommandException("command_failed", "Run artifacts are not available"),
        )) assertFalse(isMissingRun(error, key.runId))
    }

    @Test fun adoptedSessionMustBelongToTheExactFinalizedTurnAndWorkspace() {
        assertEquals("adopted", adoptedHistorySession(key, detail()))
        assertNull(adoptedHistorySession(key.copy(workspaceId = "other"), detail()))
        assertNull(adoptedHistorySession(key.copy(runId = "other"), detail()))
        assertNull(adoptedHistorySession(key, detail().copy(
            conversation = conversation.copy(activeTurnId = "new-active-turn"),
        )))
        for (phase in listOf(TurnPhase.REVIEW, TurnPhase.FINALIZING, TurnPhase.PRIMARY_RUNNING)) {
            assertNull(adoptedHistorySession(key, detail().copy(turns = listOf(
                ConversationSnapshot(turn = turn.copy(phase = phase)),
            ))))
        }
        assertNull(adoptedHistorySession(key, detail().copy(
            conversation = conversation.copy(resumeSessionId = "NO"),
        )))
    }

    @Test fun everyHistoryPageIsReadBeforeItCanReplaceCachedContent() = runBlocking {
        val cursors = mutableListOf<Int>()
        val history = readCompleteResumeHistory { cursor ->
            cursors += cursor
            if (cursor == 0) ResumeHistoryPage(0, 100, true, listOf(
                ResumeHistoryEntry("user", "question", "user-1"),
            )) else ResumeHistoryPage(100, 101, false, listOf(
                ResumeHistoryEntry("assistant", "answer", "answer-1"),
            ))
        }
        assertEquals(listOf(0, 100), cursors)
        assertEquals(listOf("question", "answer"), history.map { it.text })
        val failure = runCatching {
            readCompleteResumeHistory { cursor ->
                if (cursor == 0) ResumeHistoryPage(0, 1, true)
                else throw IOException("second page failed")
            }
        }.exceptionOrNull()
        assertEquals("second page failed", failure?.message)
        assertTrue(runCatching {
            readCompleteResumeHistory { ResumeHistoryPage(it, it, true) }
        }.exceptionOrNull() is IllegalStateException)
        assertTrue(runCatching {
            readCompleteResumeHistory { ResumeHistoryPage(10, 20, false) }
        }.exceptionOrNull() is IllegalStateException)
    }

    @Test fun recoveryPreservesIdentityDraftAndSettingsWithoutKeepingRunActions() {
        val original = MainUiState(
            selectedMacId = key.macId, selectedWorkspaceId = key.workspaceId,
            visibleRunId = key.runId, visibleConversationId = "conversation",
            visibleConversationTitle = "Existing title", prompt = "Unsent draft",
            runStatus = "finalized", selectedAgent = 2, finalizedAgent = 2,
            canAddAgents = true, runSettings = RemoteRunSettings(model = "model", resumeSessionId = "parent"),
        )
        val history = listOf(MobileTranscriptBlock("question", MobileTranscriptKind.USER, "question"))
        val recovered = original.resumeHistoryProjection(key, "adopted", history)
        assertNull(recovered.visibleRunId)
        assertNull(recovered.selectedAgent)
        assertFalse(recovered.canAddAgents)
        assertEquals("conversation", recovered.visibleConversationId)
        assertEquals("Existing title", recovered.visibleConversationTitle)
        assertEquals("Unsent draft", recovered.prompt)
        assertEquals("model", recovered.runSettings.model)
        assertEquals("adopted", recovered.runSettings.resumeSessionId)
        assertEquals(history, recovered.conversationHistory)
        assertFalse(recovered.isVisibleConversationReadBlocked())
        assertEquals(original, original.resumeHistoryProjection(key.copy(macId = "other"), "adopted", history))
        assertEquals(original, original.resumeHistoryProjection(key.copy(workspaceId = "other"), "adopted", history))
        val opened = original.copy(visibleRunId = null).resumeHistoryProjection(
            key, "adopted", history, "target", "Target title", "Target draft")
        assertEquals("target", opened.visibleConversationId)
        assertEquals("Target draft", opened.prompt)
    }

    @Test fun recoveredNavigationSurvivesRestartAndRemoteDefaultChanges() {
        val workspaceKey = workspaceSettingsKey(key.macId, key.workspaceId)
        val repository = ConversationStateRepository(PersistedMobileState(
            viewedRunIdsByWorkspace = mapOf(workspaceKey to key.runId),
        ))
        repository.detach(workspaceKey, "adopted")
        val restored = ConversationStateRepository(PersistedMobileState(
            viewedRunIdsByWorkspace = repository.viewedRunIdsByWorkspace,
            detachedConversationContexts = repository.detachedContexts,
            detachedResumeSessions = repository.detachedResumeSessions,
        ))
        assertNull(restored.displayedRunId(workspaceKey, key.runId))
        assertEquals("adopted", restored.settingsForDraft(workspaceKey,
            RemoteRunSettings(resumeSessionId = "unrelated-new-default"),
        ).resumeSessionId)
    }

    @Test fun recoveryBindingsSurviveCacheCaptureAndOldCachesStillDecode() {
        val old = Json.decodeFromString<WorkspaceRunSnapshot>("""{"status":"finalized"}""")
        assertEquals("", old.adoptedSessionId)
        val store = WorkspaceRunStateStore()
        store.put(key, old.copy(adoptedSessionId = "adopted", recoveredSessionId = "adopted"))
        store.capture(key, MainUiState(runStatus = "finalized"))
        assertEquals("adopted", store[key]?.adoptedSessionId)
        assertEquals("adopted", store[key]?.recoveredSessionId)
        val restored = Json.decodeFromString<WorkspaceRunSnapshot>(Json.encodeToString(store[key]!!))
        assertEquals(store[key], restored)
    }

    @Test fun switchingNativeHistoryRejectsLateResultsEvenWithinTheSameWorkspace() {
        val state = MainUiState(selectedMacId = "mac", selectedWorkspaceId = "workspace",
            runSettings = RemoteRunSettings(resumeSessionId = "second-session"))
        assertFalse(state.isViewingResumeHistory("mac", "workspace", "first-session"))
        assertTrue(state.isViewingResumeHistory("mac", "workspace", "second-session"))
        assertFalse(state.copy(visibleRunId = "new-run")
            .isViewingResumeHistory("mac", "workspace", "second-session"))
    }

    @Test fun missingRunUsesAdoptedHistoryAndNeverMergesItWithTheOldParent() {
        val oldSettings = RemoteRunSettings(resumeSessionId = "parent")
        assertEquals("adopted", oldSettings.forConversationHistory(null, "adopted").resumeSessionId)
        assertEquals(oldSettings, oldSettings.forConversationHistory("running", "adopted"))
        assertEquals(oldSettings, oldSettings.forConversationHistory(null, ""))
        val oldState = MainUiState(runSettings = oldSettings)
        assertTrue(oldState.conversationSourceChanged(null, "adopted"))
        assertFalse(oldState.conversationSourceChanged(null, "parent"))
        assertTrue(oldState.copy(visibleRunId = "expired").conversationSourceChanged(null, "parent"))
        assertFalse(oldState.copy(visibleRunId = "running").conversationSourceChanged("running", "adopted"))
    }

    @Test fun nativeHistoryMovesDraftToOneConversationIdentityWithoutLeavingAStaleAlias() {
        val state = MainUiState(selectedMacId = key.macId, selectedWorkspaceId = key.workspaceId,
            runSettings = RemoteRunSettings(resumeSessionId = "adopted"))
        val snapshot = WorkspaceRunSnapshot(conversationId = "conversation")
        val alias = promptDraftContextKey(key.macId, key.workspaceId, null, "adopted")
        val canonical = promptDraftContextKey(key.macId, key.workspaceId, "conversation", "adopted")
        val drafts = mutableMapOf(canonical to "Saved draft")
        assertEquals("Saved draft", state.restoreHydratedConversationDraft(snapshot, drafts))
        assertEquals("Saved draft", state.copy(visibleRunId = key.runId)
            .restoreHydratedConversationDraft(snapshot, drafts))
        drafts[alias] = "Edited while loading"
        assertEquals("Edited while loading", state.restoreHydratedConversationDraft(snapshot, drafts))
        assertEquals(mapOf(canonical to "Edited while loading"), drafts)
        drafts[alias] = ""
        assertEquals("", state.restoreHydratedConversationDraft(snapshot, drafts))
        assertTrue(drafts.isEmpty())
        assertEquals("", state.restoreHydratedConversationDraft(snapshot, drafts))
    }
}
