package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ConversationReadPresentationTest {
    @get:Rule val compose = createComposeRule()

    @Test
    fun readFailureKeepsCachedReplyVisibleAndRetryWorksInBothAppearances() {
        var retries = 0
        var historyOpens = 0
        val dark = mutableStateOf(false)
        compose.setContent {
            val context = LocalContext.current.applicationContext
            val viewport = remember { ConversationViewportStore(context) }
            CompositionLocalProvider(LocalAppLanguage provides AppLanguage.CHINESE) {
                MaterialTheme(colorScheme = if (dark.value) darkColorScheme() else lightColorScheme()) {
                    Column(Modifier.fillMaxSize()) {
                        ConversationReadNotice(
                            issue = ConversationReadIssue("unknown", unavailable = true),
                            hasContent = true,
                            onRetry = { retries++ },
                            onOpenHistory = { historyOpens++ },
                        )
                        AgentConversation(
                            state = MainUiState(conversationHistory = listOf(
                                MobileTranscriptBlock("read-test-user", MobileTranscriptKind.USER, "My question"),
                                MobileTranscriptBlock("read-test-answer", MobileTranscriptKind.OUTPUT, "My cached answer"),
                            )),
                            modifier = Modifier.fillMaxSize(), viewportStore = viewport,
                            onLoadWorkOperation = { _, _ -> error("This fixture has no work operations") },
                        )
                    }
                }
            }
        }
        repeat(2) {
            compose.runOnIdle { dark.value = it == 1 }
            compose.onNodeWithText("My question").assertIsDisplayed()
            compose.onNodeWithText("My cached answer").assertIsDisplayed()
            compose.onNodeWithText("重新加载").assertIsDisplayed().performClick()
        }
        compose.runOnIdle { assertEquals(2, retries); assertEquals(0, historyOpens) }
    }

    @Test
    fun runtimeQuestionReplyRendersAsQuestionAndAnswerInsteadOfJson() {
        val raw = """<send_user_message_question_reply>
            [{"questionItemId":"read-ui-1","question":"Which device should I use?","answer":"Use the Android emulator"}]
            </send_user_message_question_reply>""".trimIndent()
        compose.setContent {
            val context = LocalContext.current.applicationContext
            val viewport = remember { ConversationViewportStore(context) }
            MaterialTheme {
                AgentConversation(
                    state = MainUiState(conversationHistory = listOf(
                        MobileTranscriptBlock("read-ui-reply", MobileTranscriptKind.USER, raw, isHistorical = true),
                    )),
                    modifier = Modifier.fillMaxSize(), viewportStore = viewport,
                    onLoadWorkOperation = { _, _ -> error("This fixture has no work operations") },
                )
            }
        }
        compose.onNodeWithText("Which device should I use?").assertIsDisplayed()
        compose.onNodeWithText("Use the Android emulator").assertIsDisplayed()
        compose.onAllNodesWithText("questionItemId", substring = true).assertCountEquals(0)
        compose.onAllNodesWithText("send_user_message_question_reply", substring = true).assertCountEquals(0)
    }
}
