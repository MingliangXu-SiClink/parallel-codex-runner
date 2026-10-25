package com.parallelcodexrunner.remote.ui

import android.graphics.Bitmap
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.test.platform.app.InstrumentationRegistry
import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import java.io.File
import org.junit.Rule
import org.junit.Test

class ConversationInterruptionTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun interruptedHistoryKeepsThePromptAndShowsLocalizedStatus() {
        val notice = "<turn_aborted>\nThe user interrupted the previous turn on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.\n</turn_aborted>"
        val language = mutableStateOf(AppLanguage.CHINESE)
        val state = MainUiState(
            selectedMacId = "interruption-test-mac",
            selectedWorkspaceId = "interruption-test-workspace",
            conversationHistory = MobileTranscriptFormatter.historyBlocks(listOf(
                ResumeHistoryEntry("user", notice, id = "cancel-one"),
                ResumeHistoryEntry("user", "Keep my prompt\n$notice", id = "prompt-two"),
            )),
        )
        compose.setContent {
            val context = LocalContext.current.applicationContext
            val store = remember { ConversationViewportStore(context) }
            CompositionLocalProvider(LocalAppLanguage provides language.value) {
                MaterialTheme {
                    AgentConversation(state, Modifier.fillMaxSize(), viewportStore = store,
                        onLoadWorkOperation = { _, _ -> error("This fixture has no work operations") })
                }
            }
        }
        for ((selectedLanguage, label) in listOf(
            AppLanguage.CHINESE to "回复已中断",
            AppLanguage.ENGLISH to "Response interrupted",
        )) {
            compose.runOnIdle { language.value = selectedLanguage }
            compose.onNodeWithText("Keep my prompt").assertIsDisplayed()
            compose.onAllNodesWithText(label).assertCountEquals(2)
            compose.onAllNodesWithText("<turn_aborted>", substring = true).assertCountEquals(0)
            val context = InstrumentationRegistry.getInstrumentation().targetContext
            val output = File(context.externalCacheDir, "interruption-${selectedLanguage.name}.png")
            output.outputStream().use {
                compose.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it)
            }
        }
    }
}
