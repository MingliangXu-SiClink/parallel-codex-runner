package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.junit4.v2.createComposeRule
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ConversationViewportNavigationTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun restoresTheExactAgentPositionAfterReturningFromWorkspaceList() {
        val blocks = buildList {
            repeat(29) { index ->
                add(
                    MobileTranscriptBlock(
                        id = "event-$index",
                        kind = if (index % 3 == 0) {
                            MobileTranscriptKind.COMMAND
                        } else {
                            MobileTranscriptKind.THOUGHT
                        },
                        text = "Block $index\n" + "content ".repeat(32),
                    ),
                )
            }
            add(
                MobileTranscriptBlock(
                    id = "event-final-thought",
                    kind = MobileTranscriptKind.THOUGHT,
                    text = "Final reasoning\n" + "reasoning ".repeat(48),
                ),
            )
            add(
                MobileTranscriptBlock(
                    id = "event-final-answer",
                    kind = MobileTranscriptKind.OUTPUT,
                    text = buildString {
                        appendLine("## Final answer")
                        repeat(48) { appendLine("- Result line $it with `inline code`") }
                    },
                ),
            )
        }
        val state = MainUiState(
            selectedMacId = "mac",
            selectedWorkspaceId = "workspace",
            visibleRunId = "run",
            runStatus = "completed",
            runPrompt = "Prompt",
            agentSummaries = listOf(
                AgentSummary(6, "success", null, null, true, false, true, true, true),
            ),
            selectedAgent = 6,
            agentTranscripts = mapOf(6 to MobileAgentTranscript(blocks = blocks)),
        )
        val formattedBlocks = MobileTranscriptFormatter.visibleBlocks(
            prompt = state.runPrompt,
            transcript = state.agentTranscripts.getValue(6),
        )
        val visibleBlocks = MobileConversationPresentation.visibleBlocks(
            blocks = formattedBlocks,
            runId = state.visibleRunId.orEmpty(),
            agent = state.selectedAgent ?: 6,
            workExpanded = false,
        )
        val viewportKey = listOf("mac", "workspace", "run", "6").joinToString("\u001f")
        lateinit var store: ConversationViewportStore
        var showConversation by mutableStateOf(true)
        var storeGeneration by mutableIntStateOf(0)

        compose.setContent {
            val context = LocalContext.current.applicationContext
            store = remember(context, storeGeneration) { ConversationViewportStore(context) }
            if (showConversation) {
                AgentConversation(
                    state = state,
                    modifier = Modifier.fillMaxSize(),
                    scrollable = true,
                    viewportStore = store,
                    onLoadWorkOperation = { _, _ -> error("This fixture has no work operations") },
                )
            }
        }
        compose.waitForIdle()

        val targetIndex = visibleBlocks.lastIndex
        val before = store.get(viewportKey, visibleBlocks)
        runBlocking { before.listState.scrollToItem(targetIndex, 240) }
        compose.waitUntil(5_000) { before.listState.firstVisibleItemIndex == targetIndex }
        val expectedIndex = before.listState.firstVisibleItemIndex
        val expectedOffset = before.listState.firstVisibleItemScrollOffset

        compose.runOnIdle {
            store.saveAll()
            showConversation = false
        }
        compose.waitForIdle()
        compose.runOnIdle {
            storeGeneration += 1
            showConversation = true
        }
        compose.waitForIdle()

        val restored = store.get(viewportKey, visibleBlocks)
        compose.waitUntil(5_000) {
            restored.listState.firstVisibleItemIndex == expectedIndex
        }
        assertEquals(expectedIndex, restored.listState.firstVisibleItemIndex)
        assertEquals(expectedOffset, restored.listState.firstVisibleItemScrollOffset)
    }
}
