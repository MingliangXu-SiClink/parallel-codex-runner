package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.unit.Density
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ConversationAgentControlsAccessibilityTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun compactTwoDigitAgentLabelCanGrowAtLargeFontScale() {
        val agents = (1..14).map { number ->
            AgentSummary(
                number = number,
                status = "success",
                reasoningTokens = null,
                seconds = null,
                recommended = false,
                rejected = false,
                workspaceAvailable = true,
                canAccept = true,
                canContinue = true,
            )
        }

        compose.setContent {
            CompositionLocalProvider(
                LocalDensity provides Density(density = 1f, fontScale = 2f),
            ) {
                MaterialTheme {
                    ConversationAgentSwitchBar(
                        state = MobileConversationAgentSwitchState(
                            runId = "run",
                            agents = agents,
                            selectedAgent = 10,
                            recommendedAgent = null,
                            finalizedAgent = null,
                            selectionEnabled = true,
                        ),
                        listState = rememberLazyListState(),
                        onSelectAgent = {},
                        onOpenAgentDetails = {},
                    )
                }
            }
        }

        compose.waitForIdle()
        val width = compose
            .onNodeWithContentDescription("Agent 10 已完成")
            .fetchSemanticsNode()
            .boundsInRoot
            .width
        assertTrue("Two-digit label must grow beyond the compact minimum", width > 60f)
    }
}
