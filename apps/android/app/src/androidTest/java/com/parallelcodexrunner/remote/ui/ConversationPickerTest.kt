package com.parallelcodexrunner.remote.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ConversationPickerTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun newConversationIsAvailableWithoutOpeningRunSettings() {
        val state = mutableStateOf(MainUiState())
        var newConversations = 0
        var historySelections = 0
        compose.setContent {
            CompositionLocalProvider(LocalAppLanguage provides AppLanguage.ENGLISH) {
                MaterialTheme {
                    ConversationPickerSheet(
                        state = state.value,
                        workspaceName = "A workspace with a long name",
                        onDismiss = {},
                        onRefresh = {},
                        onNewConversation = { newConversations += 1 },
                        onSelect = { historySelections += 1 },
                    )
                }
            }
        }
        compose.onNodeWithText("No conversations in this workspace yet").assertIsDisplayed()
        compose.onNodeWithContentDescription("New conversation").assertIsDisplayed().performClick()
        compose.runOnIdle {
            assertEquals(1, newConversations)
            assertEquals(0, historySelections)
            state.value = state.value.copy(runActionInProgress = true)
        }
        compose.onNodeWithContentDescription("New conversation").assertIsNotEnabled()
        compose.runOnIdle {
            // Loading history must not prevent starting a separate conversation.
            state.value = state.value.copy(runActionInProgress = false, conversationListLoading = true)
        }
        compose.onNodeWithContentDescription("New conversation").performClick()
        compose.runOnIdle { assertEquals(2, newConversations) }
    }
}
