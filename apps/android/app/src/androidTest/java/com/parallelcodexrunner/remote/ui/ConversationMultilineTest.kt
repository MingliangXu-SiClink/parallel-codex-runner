package com.parallelcodexrunner.remote.ui

import android.graphics.Bitmap
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.test.platform.app.InstrumentationRegistry
import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import java.io.File
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.assertTrue

class ConversationMultilineTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun reusingCurrentPromptDoesNotRewriteCachedHistory() {
        val current = mutableStateOf("Original cached prompt QA")
        val showHistory = mutableStateOf(false)
        compose.setContent {
            MaterialTheme {
                Column {
                    MarkdownTranscript(
                        content = current.value,
                        textColor = MaterialTheme.colorScheme.onSurface,
                        baseStyle = MaterialTheme.typography.bodyLarge,
                    )
                    if (showHistory.value) {
                        MarkdownTranscript(
                            content = "Original cached prompt QA",
                            textColor = MaterialTheme.colorScheme.onSurface,
                            baseStyle = MaterialTheme.typography.bodyLarge,
                        )
                    }
                }
            }
        }
        compose.onNodeWithText("Original cached prompt QA").assertIsDisplayed()
        compose.runOnIdle { current.value = "Next streamed prompt QA" }
        compose.onNodeWithText("Next streamed prompt QA").assertIsDisplayed()
        compose.runOnIdle { showHistory.value = true }
        compose.onNodeWithText("Original cached prompt QA").assertIsDisplayed()
        compose.onAllNodesWithText("Next streamed prompt QA").assertCountEquals(1)
        compose.runOnIdle { current.value = "Final streamed prompt QA" }
        compose.onNodeWithText("Original cached prompt QA").assertIsDisplayed()
        compose.onNodeWithText("Final streamed prompt QA").assertIsDisplayed()
    }

    @Test
    fun multilineUserParametersKeepTheirLineBreaks() {
        val source = "vx: -0.12\nvy: 0\nomega: 0\nduration: 5\ncommand\\_duration: 0.15"
        val state = MainUiState(
            selectedMacId = "multiline-test-mac",
            selectedWorkspaceId = "multiline-test-workspace",
            conversationHistory = MobileTranscriptFormatter.historyBlocks(listOf(
                ResumeHistoryEntry("assistant", source, id = "explanation"),
                ResumeHistoryEntry("user", source, id = "parameters"),
            )),
        )
        compose.setContent {
            val context = LocalContext.current.applicationContext
            val store = remember { ConversationViewportStore(context) }
            MaterialTheme {
                AgentConversation(state, Modifier.fillMaxSize(), viewportStore = store,
                    onLoadWorkOperation = { _, _ -> error("This fixture has no work operations") })
            }
        }
        compose.onNodeWithText("vx: -0.12\nvy: 0\nomega: 0\nduration: 5\ncommand_duration: 0.15")
            .assertIsDisplayed()
        compose.onNodeWithText("vx: -0.12 vy: 0 omega: 0 duration: 5 command_duration: 0.15")
            .assertIsDisplayed()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        File(context.externalCacheDir, "multiline-user.png").outputStream().use {
            compose.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    @Test
    fun formulaMessagesKeepUserLineBreaksWhenTheRendererIsReused() {
        val preserveLineBreaks = mutableStateOf(true)
        var rootView: View? = null
        compose.setContent {
            rootView = LocalView.current
            MaterialTheme {
                MarkdownTranscript(
                    content = "First line\nSecond line\n\n${'$'}${'$'}x^2${'$'}${'$'}",
                    textColor = MaterialTheme.colorScheme.onSurface,
                    baseStyle = MaterialTheme.typography.bodyLarge,
                    preserveLineBreaks = preserveLineBreaks.value,
                )
            }
        }
        fun assertNativeText(expected: String) {
            compose.runOnIdle {
                val text = descendants(checkNotNull(rootView).rootView)
                    .filterIsInstance<TextView>()
                    .first { it.text.contains("First line") }.text.toString()
                assertTrue("Expected $expected in $text", text.contains(expected))
            }
        }
        assertNativeText("First line\nSecond line")
        compose.runOnIdle { preserveLineBreaks.value = false }
        assertNativeText("First line Second line")
        compose.runOnIdle { preserveLineBreaks.value = true }
        assertNativeText("First line\nSecond line")
    }

    private fun descendants(view: View): List<View> = listOf(view) + if (view is ViewGroup) {
        (0 until view.childCount).flatMap { descendants(view.getChildAt(it)) }
    } else emptyList()
}
