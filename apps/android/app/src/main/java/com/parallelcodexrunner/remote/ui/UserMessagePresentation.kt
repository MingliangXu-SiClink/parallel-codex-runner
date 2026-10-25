package com.parallelcodexrunner.remote.ui

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

internal data class UserQuestionReply(val question: String, val answer: String)

internal object UserMessagePresentation {
    private const val OPEN = "<send_user_message_question_reply>"
    private const val CLOSE = "</send_user_message_question_reply>"
    private val fields = setOf("questionItemId", "question", "answer")

    fun questionReplies(text: String): List<UserQuestionReply>? {
        val envelope = text.trim()
        if (!envelope.startsWith(OPEN) || !envelope.endsWith(CLOSE)) return null
        val payload = envelope.substring(OPEN.length, envelope.length - CLOSE.length)
        val entries = runCatching { Json.parseToJsonElement(payload) }.getOrNull() as? JsonArray
            ?: return null
        if (entries.isEmpty()) return null
        // Only a complete, recognized runtime envelope is metadata. Malformed,
        // quoted, or extended payloads stay literal, so no user text is discarded.
        return entries.map { element ->
            val entry = element as? JsonObject ?: return null
            if (entry.keys != fields) return null
            fun string(name: String): String? = (entry[name] as? JsonPrimitive)
                ?.takeIf { it.isString }?.content?.takeIf(String::isNotBlank)
            string("questionItemId") ?: return null
            UserQuestionReply(string("question") ?: return null, string("answer") ?: return null)
        }
    }

    fun text(raw: String): String = questionReplies(raw)?.joinToString("\n\n") { reply ->
        reply.question.lineSequence().joinToString("\n") { "> $it" } + "\n\n" + reply.answer
    } ?: raw
}
