package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.generated.*
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import org.junit.Assert.*
import org.junit.Test

class WorkProcessTest {
    @Test fun operationUpdatesAreScopedAndMonotonic() {
        fun item(execution: String, event: Int, status: String): JsonArray = Json.parseToJsonElement("""
            [{"id":"event-$event","kind":"command","text":"cat a.tex","operation":{
            "id":"$execution:item0","event_id":$event,"kind":"file_read","status":"$status"}}]
        """) as JsonArray
        var blocks = MobileTranscriptFormatter.appendItems(emptyList(), item("first", 1, "running"))
        blocks = MobileTranscriptFormatter.appendItems(blocks, item("first", 3, "completed"))
        blocks = MobileTranscriptFormatter.appendItems(blocks, item("first", 2, "running"))
        blocks = MobileTranscriptFormatter.appendItems(blocks, item("retry", 4, "running"))
        assertEquals(listOf("event-1", "event-4"), blocks.map { it.id })
        assertEquals(WorkOperationStatus.COMPLETED, blocks[0].operation!!.status)
    }

    @Test fun missingStatisticsAndUnknownMetadataFallBackHonestly() {
        val operation = WorkOperationRecord(id = "op", kind = WorkOperationKind.FILE_CHANGE,
            status = WorkOperationStatus.COMPLETED, targets = listOf(WorkOperationTarget(path = "a.tex", change = WorkFileChangeKind.CREATE)))
        assertEquals("已创建 a.tex", WorkOperationPresentation.summary(operation, AppLanguage.CHINESE))
        assertEquals("Created a.tex", WorkOperationPresentation.summary(operation, AppLanguage.ENGLISH))
        val unknown = Json.parseToJsonElement("""[{"id":"e","kind":"command","text":"custom command","operation":{"kind":"future_kind"}}]""") as JsonArray
        val block = MobileTranscriptFormatter.appendItems(emptyList(), unknown).single()
        assertEquals("custom command", block.text)
        assertNull(block.operation)
    }

    @Test fun eachSegmentCanBeCollapsedWithoutMovingNaturalReply() {
        val blocks = listOf(MobileTranscriptBlock("a", MobileTranscriptKind.COMMAND, "cat a"),
            MobileTranscriptBlock("reply", MobileTranscriptKind.OUTPUT, "Found the cause"),
            MobileTranscriptBlock("b", MobileTranscriptKind.COMMAND, "cat b"))
        val visible = MobileConversationPresentation.visibleBlocks(blocks, "run", 1, true,
            sectionOverrides = mapOf("work-summary-run-1-a" to false))
        assertEquals(listOf("work-summary-run-1-a", "reply", "work-summary-run-1-b", "b"), visible.map { it.id })
    }
}
