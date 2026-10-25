package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import org.junit.Assert.*
import org.junit.Test

class UserMessagePresentationTest {
    private val envelope = """<send_user_message_question_reply>
        [{"questionItemId":"request-1","question":"Which device?","answer":"Android emulator"},
         {"questionItemId":"request-2","question":"What happened?\nAny error?","answer":"The page stayed empty."}]
        </send_user_message_question_reply>""".trimIndent()
    private val expected = "> Which device?\n\nAndroid emulator\n\n> What happened?\n> Any error?\n\nThe page stayed empty."

    @Test
    fun rendersCompleteQuestionAndAnswerPairsWithoutProtocolFields() {
        assertEquals(expected, UserMessagePresentation.text(envelope))
    }

    @Test
    fun currentHistoryAndCachedUserMessagesUseTheSameProjection() {
        val current = MobileTranscriptFormatter.visibleBlocks(envelope, MobileAgentTranscript())
        assertEquals(expected, current.single().text)
        val history = MobileTranscriptFormatter.historyBlocks(listOf(
            ResumeHistoryEntry(category = "user", text = envelope, id = "original-id"),
        ))
        assertEquals(expected, history.single().text)
        assertEquals("original-id", history.single().id)
        val cached = listOf(MobileTranscriptBlock("cache-id", MobileTranscriptKind.USER, envelope, isHistorical = true))
        assertEquals(expected, MobileTranscriptFormatter.visibleBlocks(cached, "", MobileAgentTranscript()).single().text)
    }

    @Test
    fun literalQuotedMalformedAndUnknownPayloadsAreNeverDiscarded() {
        listOf(
            "```xml\n$envelope\n```",
            "Please explain:\n$envelope",
            "$envelope\nThis is literal user text.",
            envelope.replace("\"Android emulator\"", "42"),
            envelope.replace("\"request-1\"", "\"request-1\",\"extra\":\"keep me\""),
            envelope.replace("</send_user_message_question_reply>", ""),
            "<send_user_message_question_reply>not JSON</send_user_message_question_reply>",
        ).forEach { assertEquals(it, UserMessagePresentation.text(it)) }
    }

    @Test
    fun assistantTextIsNotReinterpretedAsUserProtocol() {
        val result = MobileTranscriptFormatter.visibleBlocks("", MobileAgentTranscript(finalMessage = envelope))
        assertEquals(envelope, result.single().text)
        assertEquals(expected, UserMessagePresentation.text(expected))
    }
}
