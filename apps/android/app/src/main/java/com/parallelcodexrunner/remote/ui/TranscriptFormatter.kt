package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import com.parallelcodexrunner.remote.data.generated.WorkOperationRecord
import com.parallelcodexrunner.remote.data.generated.WorkOperationStatus
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.Serializable
import java.net.URI

@Serializable
enum class MobileTranscriptKind {
    USER,
    THOUGHT,
    OUTPUT,
    COMMAND,
    ACTIVITY,
    WARNING,
    ERROR,
}

@Serializable
data class MobileTranscriptBlock(
    val id: String,
    val kind: MobileTranscriptKind,
    val text: String,
    val status: String? = null,
    val operationId: String? = null,
    val activityType: String? = null,
    val isHistorical: Boolean = false,
    val isFinalAnswer: Boolean = false,
    val operation: WorkOperationRecord? = null,
)

@Serializable
data class MobileAgentTranscript(
    val blocks: List<MobileTranscriptBlock> = emptyList(),
    val cursor: Int = 0,
    val hasMore: Boolean = true,
    val finalMessage: String = "",
    val finalMessageCursor: Int = 0,
    val finalMessageHasMore: Boolean = true,
    val finalMessageId: String = "",
    val error: String = "",
    val errorFromAgentResponse: Boolean = false,
)

object MobileTranscriptFormatter {
    private val json = Json { ignoreUnknownKeys = true }
    private val memoryCitationBlock = Regex(
        "(?is)<oai-mem-citation\\b[^>]*>.*?</oai-mem-citation\\s*>"
    )
    private val unfinishedMemoryCitation = Regex("(?is)<oai-mem-citation\\b[^>]*>")
    private val memoryCitationTag = Regex("(?is)</?oai-mem-citation\\s*>")
    private val codexFileCitation = Regex(
        """:codex-file-citation\{(?:(?:[^"{}\\]|\\.)|"(?:[^"\\]|\\.)*")*\}"""
    )
    private val codexFileCitationPath = Regex(
        """(?:^|[\{\s])path\s*=\s*"(?<path>(?:[^"\\]|\\.)*)""""
    )
    private val markdownLink = Regex(
        """(!?\[[^\]\n]*\])\((?:<[^>\n]*>|(?:\\.|[^)\n])*)\)"""
    )
    private val runtimeLog = Regex(
        """^(?:\S+\s+)?(ERROR|WARN(?:ING)?)\s+([A-Za-z0-9_.:-]+):\s*(.+)$""",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )

    fun visibleBlocks(
        prompt: String,
        transcript: MobileAgentTranscript,
    ): List<MobileTranscriptBlock> = visibleBlocks(emptyList(), prompt, transcript)

    fun visibleBlocks(
        history: List<MobileTranscriptBlock>,
        prompt: String,
        transcript: MobileAgentTranscript,
    ): List<MobileTranscriptBlock> {
        val values = mutableListOf<MobileTranscriptBlock>()
        history.forEach { append(it, values) }
        val currentTurnStart = values.size
        cleaned(prompt).takeIf(String::isNotEmpty)?.let {
            // runs.history already ends at the current run's rollout boundary.
            // Repeating a legitimate prompt such as "继续" must still render as
            // a new turn even when identical text exists earlier in history.
            values += MobileTranscriptBlock("current-prompt", MobileTranscriptKind.USER, UserMessagePresentation.text(it))
        }
        transcript.blocks.forEach { append(it, values) }
        val finalMessage = assistantPresentationText(cleaned(transcript.finalMessage))
        if (finalMessage.isNotEmpty()) {
            val existing = values.indices.drop(currentTurnStart).lastOrNull { index ->
                values[index].kind == MobileTranscriptKind.OUTPUT &&
                    equivalentAssistantPresentation(values[index].text, finalMessage)
            }
            if (existing != null) {
                // The streamed event can still reference the temporary Agent
                // workspace. Keep its stable identity, but use the canonical
                // sync-back-aware links from the final artifact.
                values[existing] = values[existing].copy(
                    text = finalMessage,
                    isFinalAnswer = true,
                )
            } else {
                append(
                    MobileTranscriptBlock(
                        "final-message",
                        MobileTranscriptKind.OUTPUT,
                        finalMessage,
                        isFinalAnswer = true,
                    ),
                    values,
                )
            }
        }
        cleaned(transcript.error).takeIf(String::isNotEmpty)?.let {
            values += MobileTranscriptBlock("agent-error", MobileTranscriptKind.ERROR, it)
        }
        val seen = mutableSetOf<String>()
        return values.mapIndexed { index, block ->
            if (seen.add(block.id)) {
                block
            } else {
                block.copy(id = "${block.id}-$index")
            }
        }
    }

    fun historyBlocks(
        entries: List<ResumeHistoryEntry>,
        startingIndex: Int = 0,
    ): List<MobileTranscriptBlock> =
        entries.flatMapIndexed { index, entry ->
            val text = cleaned(entry.text)
            if (text.isEmpty()) return@flatMapIndexed emptyList()
            val kind = historyKind(entry.category)
            val id = entry.id.ifBlank { "history-${startingIndex + index}" }
            val prompt = if (kind == MobileTranscriptKind.USER) userTextBeforeCodexInterruption(text) else null
            if (prompt != null) {
                buildList {
                    if (prompt.isNotEmpty()) add(MobileTranscriptBlock(id, kind, prompt, isHistorical = true))
                    add(MobileTranscriptBlock(
                        id = "$id:interrupted", kind = MobileTranscriptKind.ACTIVITY,
                        text = "Response interrupted", activityType = "turn_interrupted", isHistorical = true,
                    ))
                }
            } else {
                listOf(MobileTranscriptBlock(
                    id = id,
                    kind = kind,
                    text = presentationText(kind, text),
                    isHistorical = true,
                    isFinalAnswer = kind == MobileTranscriptKind.OUTPUT,
                ))
            }
        }

    private fun userTextBeforeCodexInterruption(text: String): String? {
        val closing = "</turn_aborted>"
        val marker = "<turn_aborted>"
        val start = text.lastIndexOf(marker)
        if (!text.endsWith(closing) || start < 0 || (start > 0 && text[start - 1] != '\n')) return null
        val prefix = text.substring(0, start).trim()
        // Quoted code and unknown payloads are user content, not runtime status.
        if ("```" in prefix || "~~~" in prefix) return null
        val body = text.substring(start + marker.length, text.length - closing.length)
            .trim().split(Regex("\\s+")).joinToString(" ")
        if (body != "The user interrupted the previous turn on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.") return null
        return prefix
    }

    fun appendEvents(
        current: List<MobileTranscriptBlock>,
        events: JsonArray,
    ): List<MobileTranscriptBlock> {
        val values = current.toMutableList()
        events.forEach { event ->
            val objectValue = event as? JsonObject ?: return@forEach
            if (objectValue.text("type") != "agent_line") return@forEach
            val eventId = objectValue.int("event_id") ?: return@forEach
            val raw = cleaned(objectValue.text("text") ?: objectValue.text("message").orEmpty())
            block(eventId, raw)?.let { appendIncoming(it, values) }
        }
        return values
    }

    fun appendItems(
        current: List<MobileTranscriptBlock>,
        items: JsonArray,
    ): List<MobileTranscriptBlock> {
        val values = current.toMutableList()
        items.forEach { value ->
            val item = value as? JsonObject ?: return@forEach
            val id = item.text("id") ?: return@forEach
            val kind = runCatching {
                MobileTranscriptKind.valueOf(item.text("kind").orEmpty().uppercase())
            }.getOrNull() ?: return@forEach
            val text = cleaned(item.text("text").orEmpty())
            if (text.isEmpty()) return@forEach
            appendIncoming(
                MobileTranscriptBlock(
                    id = id,
                    kind = kind,
                    text = text,
                    status = canonicalStatus(item.text("status")),
                    operationId = item.text("operation_id")?.takeIf(String::isNotBlank),
                    activityType = item.text("activity_type")?.takeIf(String::isNotBlank),
                    operation = item["operation"]?.let {
                        runCatching { json.decodeFromString<WorkOperationRecord>(it.toString()) }.getOrNull()
                    },
                ),
                values,
            )
        }
        return values
    }

    private fun appendIncoming(
        block: MobileTranscriptBlock,
        values: MutableList<MobileTranscriptBlock>,
    ) {
        val presented = block.copy(text = presentationText(block.kind, block.text))
        if (presented.text.isEmpty()) return
        presented.operation?.let { operation ->
            val index = values.indexOfFirst { it.operation?.id == operation.id }
            if (index < 0) {
                values += presented
            } else {
                val previous = values[index].operation!!
                if (operation.eventId > previous.eventId &&
                    !(previous.status != WorkOperationStatus.RUNNING && operation.status == WorkOperationStatus.RUNNING)
                ) values[index] = presented.copy(id = values[index].id)
            }
            return
        }
        val existing = values.indexOfFirst { it.id == presented.id }
        if (existing >= 0) {
            values[existing] = presented
            return
        }
        val runningOperation = if (
            presented.kind in setOf(MobileTranscriptKind.COMMAND, MobileTranscriptKind.ACTIVITY)
        ) {
            values.indexOfLast {
                it.kind == presented.kind &&
                    it.status == "运行中" &&
                    (
                        presented.operationId != null && it.operationId == presented.operationId
                    )
            }
        } else {
            -1
        }
        if (runningOperation >= 0) {
            values[runningOperation] = presented.copy(id = values[runningOperation].id)
        } else {
            values += presented
        }
    }

    private fun block(eventId: Int, raw: String): MobileTranscriptBlock? {
        if (raw.isEmpty() || isHiddenRuntimeDiagnostic(raw)) return null
        val payload = runCatching { json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
        if (payload != null) {
            commandItem(payload)?.let { item ->
                val command = commandText(item["command"])
                if (command.isEmpty()) return null
                return MobileTranscriptBlock(
                    id = "event-$eventId",
                    kind = MobileTranscriptKind.COMMAND,
                    text = stripShellWrapper(command),
                    status = commandStatus(item),
                    operationId = item.text("id")?.takeIf(String::isNotBlank),
                )
            }
            eventItem(payload)?.takeIf { it.text("type") == "error" }?.let { item ->
                val message = cleaned(item.text("message").orEmpty())
                if (message.isNotEmpty()) {
                    return MobileTranscriptBlock(
                        id = "event-$eventId",
                        kind = if (isRecoverableWarning(message)) {
                            MobileTranscriptKind.WARNING
                        } else {
                            MobileTranscriptKind.ERROR
                        },
                        text = message,
                    )
                }
            }
            val text = cleaned(firstText(payload))
            if (text.isEmpty()) return null
            val kind = payloadKind(payload)
            return MobileTranscriptBlock(
                id = "event-$eventId",
                kind = kind,
                text = displayText(kind, stripAgentPrefix(text)),
            )
        }

        runtimeDiagnostic(eventId, raw)?.let { return it }

        val kind = when {
            raw.startsWith("agent_reasoning:", ignoreCase = true) -> MobileTranscriptKind.THOUGHT
            raw.startsWith("agent_message:", ignoreCase = true) -> MobileTranscriptKind.OUTPUT
            else -> MobileTranscriptKind.ACTIVITY
        }
        return MobileTranscriptBlock(
            id = "event-$eventId",
            kind = kind,
            text = displayText(kind, stripAgentPrefix(raw)),
        )
    }

    private fun isHiddenRuntimeDiagnostic(value: String): Boolean {
        val normalized = value.lowercase()
        return "codex_models_manager" in normalized &&
            "failed to refresh available models" in normalized
    }

    private fun runtimeDiagnostic(eventId: Int, value: String): MobileTranscriptBlock? {
        val match = runtimeLog.matchEntire(value) ?: return null
        val level = match.groupValues[1].uppercase()
        val message = match.groupValues[3]
            .trim()
            .removePrefix("error=")
            .trimStart()
        val normalized = message.lowercase()
        if ("createprocess" in normalized && "no such file or directory" in normalized) {
            return MobileTranscriptBlock(
                id = "event-$eventId",
                kind = MobileTranscriptKind.WARNING,
                text = "A command could not start because a required path no longer exists.",
                activityType = "missing_execution_path",
            )
        }
        return MobileTranscriptBlock(
            id = "event-$eventId",
            kind = if (level.startsWith("WARN")) {
                MobileTranscriptKind.WARNING
            } else {
                MobileTranscriptKind.ERROR
            },
            text = message,
            activityType = "runtime_diagnostic",
        )
    }

    private fun isRecoverableWarning(value: String): Boolean {
        val normalized = value.lowercase().split(Regex("\\s+")).joinToString(" ")
        val modelResume = normalized.startsWith("this session was recorded with model") &&
            "but is resuming with" in normalized &&
            "consider switching back to" in normalized
        val transportRecovery =
            normalized.startsWith("falling back from websockets to https transport") ||
                normalized.startsWith("reconnecting...")
        return modelResume || transportRecovery
    }

    private fun append(
        block: MobileTranscriptBlock,
        values: MutableList<MobileTranscriptBlock>,
    ) {
        val presented = block.copy(text = presentationText(block.kind, block.text))
        if (presented.text.isEmpty()) return
        val deduplicated = presented.kind !in setOf(
            MobileTranscriptKind.COMMAND,
            MobileTranscriptKind.USER,
            MobileTranscriptKind.ERROR,
        )
        val last = values.lastOrNull()
        if (deduplicated &&
            last?.kind == presented.kind &&
            last.status == presented.status &&
            equivalent(last.text, presented.text)
        ) {
            return
        }
        values += presented
    }

    private fun payloadKind(payload: JsonObject): MobileTranscriptKind {
        val signals = mutableListOf<String>()
        collectSignals(payload, signals)
        val value = signals.joinToString(" ").lowercase()
        return when {
            "reasoning" in value || "thinking" in value -> MobileTranscriptKind.THOUGHT
            "agent_message" in value || "assistant" in value -> MobileTranscriptKind.OUTPUT
            else -> MobileTranscriptKind.ACTIVITY
        }
    }

    private fun historyKind(category: String): MobileTranscriptKind {
        val value = category.lowercase()
        return when {
            "user" in value || "input" in value -> MobileTranscriptKind.USER
            "reason" in value || "thought" in value -> MobileTranscriptKind.THOUGHT
            "command" in value || "tool" in value -> MobileTranscriptKind.ACTIVITY
            else -> MobileTranscriptKind.OUTPUT
        }
    }

    private fun collectSignals(value: JsonObject, signals: MutableList<String>) {
        listOf("type", "event", "role").forEach { key -> value.text(key)?.let(signals::add) }
        listOf("payload", "item").forEach { key ->
            (value[key] as? JsonObject)?.let { collectSignals(it, signals) }
        }
    }

    private fun commandItem(value: JsonObject): JsonObject? {
        (value["item"] as? JsonObject)?.let {
            if (it.text("type") == "command_execution") return it
        }
        return (value["payload"] as? JsonObject)?.let(::commandItem)
    }

    private fun eventItem(value: JsonObject): JsonObject? {
        (value["item"] as? JsonObject)?.let { return it }
        return (value["payload"] as? JsonObject)?.let(::eventItem)
    }

    private fun firstText(value: JsonElement?): String = when (value) {
        null -> ""
        is JsonArray -> value.map(::firstText).filter(String::isNotEmpty).joinToString("\n")
        is JsonObject -> {
            listOf("message", "text", "content", "delta", "summary", "reasoning")
                .firstNotNullOfOrNull { key -> firstText(value[key]).takeIf(String::isNotEmpty) }
                ?: listOf("item", "payload")
                    .firstNotNullOfOrNull { key -> firstText(value[key]).takeIf(String::isNotEmpty) }
                .orEmpty()
        }
        else -> value.jsonPrimitive.contentOrNull.orEmpty()
    }

    private fun commandText(value: JsonElement?): String = when (value) {
        is JsonArray -> value.mapNotNull { it.jsonPrimitive.contentOrNull }.joinToString(" ")
        null -> ""
        else -> value.jsonPrimitive.contentOrNull.orEmpty().trim()
    }

    private fun commandStatus(item: JsonObject): String {
        item["exit_code"]?.jsonPrimitive?.intOrNull?.let {
            return if (it == 0) "已完成" else "失败（退出码 $it）"
        }
        return when (item.text("status")?.lowercase()) {
            "completed", "complete", "success", "succeeded" -> "已完成"
            "failed", "failure", "error" -> "失败"
            "cancelled", "canceled" -> "已取消"
            else -> "运行中"
        }
    }

    private fun canonicalStatus(value: String?): String? = when (value?.lowercase()) {
        "running" -> "运行中"
        "completed" -> "已完成"
        "failed" -> "失败"
        "cancelled", "canceled" -> "已取消"
        null -> null
        else -> value
    }

    private fun stripAgentPrefix(value: String): String {
        listOf("agent_reasoning:", "agent_message:").forEach { prefix ->
            if (value.startsWith(prefix, ignoreCase = true)) {
                return cleaned(value.drop(prefix.length))
            }
        }
        return value
    }

    private fun displayText(kind: MobileTranscriptKind, value: String): String {
        if (kind != MobileTranscriptKind.THOUGHT) return value
        return value.lineSequence().joinToString("\n") { line ->
            val trimmed = line.trim()
            when {
                trimmed.length > 4 && trimmed.startsWith("**") && trimmed.endsWith("**") ->
                    trimmed.substring(2, trimmed.length - 2)
                trimmed.length > 4 && trimmed.startsWith("__") && trimmed.endsWith("__") ->
                    trimmed.substring(2, trimmed.length - 2)
                else -> line
            }
        }
    }

    internal fun assistantPresentationText(text: String): String {
        var value = memoryCitationBlock.replace(text, "")
        unfinishedMemoryCitation.find(value)?.let { match ->
            value = value.substring(0, match.range.first)
        }
        value = memoryCitationTag.replace(value, "")
            .replace(Regex("\\n[\\t ]*\\n(?:[\\t ]*\\n)+"), "\n\n")
            .trim()
        value = deduplicateMirroredFinalAnswer(value)
        return codexFileCitationsToMarkdown(value)
    }

    private fun presentationText(kind: MobileTranscriptKind, text: String): String = when (kind) {
        MobileTranscriptKind.OUTPUT -> assistantPresentationText(text)
        MobileTranscriptKind.USER -> UserMessagePresentation.text(text)
        else -> text
    }

    private fun deduplicateMirroredFinalAnswer(text: String): String {
        listOf("\n", "\n\n", "\r\n").forEach { separator ->
            val remaining = text.length - separator.length
            if (remaining <= 0 || remaining % 2 != 0) return@forEach
            val half = remaining / 2
            if (text.regionMatches(half, separator, 0, separator.length) &&
                text.regionMatches(0, text, half + separator.length, half)
            ) {
                return text.substring(0, half)
            }
        }
        return text
    }

    private fun codexFileCitationsToMarkdown(text: String): String =
        codexFileCitation.replace(text) { match ->
            val pathMatch = codexFileCitationPath.find(match.value) ?: return@replace match.value
            val rawPath = pathMatch.groups["path"]?.value ?: return@replace match.value
            val path = runCatching { json.decodeFromString<String>("\"$rawPath\"") }
                .getOrNull()
                ?.takeIf(String::isNotEmpty)
                ?: return@replace match.value
            val uri = runCatching {
                when {
                    path.startsWith("file:", ignoreCase = true) -> URI(path).takeIf {
                        it.scheme.equals("file", ignoreCase = true) &&
                            (it.host.isNullOrEmpty() || it.host == "localhost") &&
                            it.path.isNotEmpty()
                    }
                    path.startsWith('/') -> URI("file", "", path, null)
                    else -> null
                }
            }.getOrNull() ?: return@replace match.value
            val label = path.trimEnd('/').substringAfterLast('/')
                .replace("\\", "\\\\")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .takeIf(String::isNotEmpty)
                ?: return@replace match.value
            "[$label](<${uri.toASCIIString()}>)"
        }

    internal fun stripShellWrapper(command: String): String {
        listOf("/bin/zsh -lc ", "zsh -lc ", "/bin/bash -lc ", "bash -lc ").forEach { prefix ->
            if (!command.startsWith(prefix)) return@forEach
            val value = command.drop(prefix.length)
            if (value.length >= 2 && value.first() == value.last() && value.first() in "\"'") {
                return value.substring(1, value.lastIndex)
            }
            return value
        }
        return command
    }

    private fun cleaned(value: String): String = value.trim()

    private fun equivalent(left: String, right: String): Boolean =
        left.split(Regex("\\s+")).joinToString(" ") == right.split(Regex("\\s+")).joinToString(" ")

    internal fun equivalentAssistantPresentation(left: String, right: String): Boolean =
        equivalent(
            markdownVisibleFingerprint(assistantPresentationText(left)),
            markdownVisibleFingerprint(assistantPresentationText(right)),
        )

    private fun markdownVisibleFingerprint(text: String): String =
        markdownLink.replace(text) { match -> match.groupValues[1] }

    private fun JsonObject.text(key: String): String? =
        this[key]?.jsonPrimitive?.contentOrNull

    private fun JsonObject.int(key: String): Int? =
        this[key]?.jsonPrimitive?.intOrNull
}
