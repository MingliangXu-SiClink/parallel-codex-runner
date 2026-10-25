package com.parallelcodexrunner.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationTimelineTest {
    @Test
    fun insertsAgentSwitchAfterCurrentPromptWithoutChangingHistory() {
        val blocks = listOf(
            block("history-user", MobileTranscriptKind.USER, "旧问题", historical = true),
            block("history-answer", MobileTranscriptKind.OUTPUT, "旧回答", historical = true),
            block("current-prompt", MobileTranscriptKind.USER, "新问题"),
            block("answer", MobileTranscriptKind.OUTPUT, "候选回答"),
        )
        val switch = MobileConversationAgentSwitchState(
            runId = "run-1",
            agents = listOf(agent(1), agent(2)),
            selectedAgent = 1,
            recommendedAgent = 2,
            finalizedAgent = null,
            selectionEnabled = true,
        )

        val items = MobileConversationTimelineProjection.make(blocks, switch)

        assertEquals(
            listOf(
                "message:history-user",
                "message:history-answer",
                "message:current-prompt",
                "agent-switch:run-1",
                "message:answer",
            ),
            items.map(MobileConversationTimelineItem::id),
        )
    }

    @Test
    fun completedAgentCollapsesOnlyCurrentTurnWork() {
        val blocks = listOf(
            block("old-command", MobileTranscriptKind.ACTIVITY, "旧命令", historical = true),
            block("current-prompt", MobileTranscriptKind.USER, "修复问题"),
            block("thought", MobileTranscriptKind.THOUGHT, "检查代码"),
            block("command", MobileTranscriptKind.COMMAND, "rg TODO"),
            block("answer", MobileTranscriptKind.OUTPUT, "已修复", final = true),
        )

        val collapsed = MobileConversationPresentation.visibleBlocks(
            blocks = blocks,
            runId = "run-1",
            agent = 1,
            workExpanded = false,
        )

        assertFalse(collapsed.any { it.id == "old-command" })
        assertFalse(collapsed.any { it.id == "thought" || it.id == "command" })
        assertEquals(
            listOf("current-prompt", "work-summary-run-1-1-thought", "answer"),
            collapsed.map(MobileTranscriptBlock::id),
        )
        assertEquals("1 条思考 · 1 个命令", collapsed[1].text)
    }

    @Test
    fun runningAgentCanCollapseWorkWithoutHidingNaturalOutput() {
        val blocks = listOf(
            block("current-prompt", MobileTranscriptKind.USER, "检查"),
            block("thought", MobileTranscriptKind.THOUGHT, "先思考"),
            block("update", MobileTranscriptKind.OUTPUT, "我正在检查"),
            block("command", MobileTranscriptKind.COMMAND, "rg TODO"),
        )

        val visible = MobileConversationPresentation.visibleBlocks(
            blocks = blocks,
            runId = "run-1",
            agent = 1,
            workExpanded = false,
        )

        assertEquals(
            listOf("current-prompt", "work-summary-run-1-1-thought", "update", "work-summary-run-1-1-command"),
            visible.map(MobileTranscriptBlock::id),
        )
        assertEquals("1 条思考", visible[1].text)
        assertEquals("1 个命令", visible[3].text)
    }

    @Test
    fun localizesWorkSummaryCountsForEnglish() {
        val visible = MobileConversationPresentation.visibleBlocks(
            blocks = listOf(
                block("thought", MobileTranscriptKind.THOUGHT, "Think"),
                block("command", MobileTranscriptKind.COMMAND, "pwd"),
            ),
            runId = "run-1",
            agent = 1,
            workExpanded = false,
            language = AppLanguage.ENGLISH,
        )

        val summary = visible.single { it.id == "work-summary-run-1-1-thought" }
        assertEquals("1 reasoning step · 1 command", summary.text)
        assertEquals("work_summary", summary.activityType)
    }

    @Test
    fun expandedWorkPreservesNaturalOutputOutsideDisclosure() {
        val blocks = listOf(
            block("current-prompt", MobileTranscriptKind.USER, "检查"),
            block("thought", MobileTranscriptKind.THOUGHT, "先思考"),
            block("update", MobileTranscriptKind.OUTPUT, "阶段更新"),
            block("command", MobileTranscriptKind.COMMAND, "rg TODO"),
            block("answer", MobileTranscriptKind.OUTPUT, "完成", final = true),
        )

        val visible = MobileConversationPresentation.visibleBlocks(
            blocks = blocks,
            runId = "run-1",
            agent = 1,
            workExpanded = true,
        )
        val collapsed = MobileConversationPresentation.visibleBlocks(
            blocks = blocks,
            runId = "run-1",
            agent = 1,
            workExpanded = false,
        )

        assertEquals(
            listOf(
                "current-prompt",
                "work-summary-run-1-1-thought",
                "thought",
                "update",
                "work-summary-run-1-1-command",
                "command",
                "answer",
            ),
            visible.map(MobileTranscriptBlock::id),
        )
        assertEquals(1, visible.indexOfFirst { it.id == "work-summary-run-1-1-thought" })
        assertEquals(1, collapsed.indexOfFirst { it.id == "work-summary-run-1-1-thought" })
        assertTrue(visible.last().isFinalAnswer)
    }

    @Test
    fun coalescesStreamedAndFinalAnswersThatOnlyDifferByLinkDestination() {
        val streamed = """
            已按实际方案重绘。

            [查看高清 PNG](</tmp/agent_001/assets/plan.png>)
        """.trimIndent()
        val final = """
            已按实际方案重绘。

            [查看高清 PNG](</Users/test/Desktop/project/assets/plan.png>)
        """.trimIndent()

        val visible = MobileConversationPresentation.visibleBlocks(
            blocks = listOf(
                block("streamed", MobileTranscriptKind.OUTPUT, streamed),
                block("final", MobileTranscriptKind.OUTPUT, final, final = true),
            ),
            runId = "run-1",
            agent = 1,
            workExpanded = false,
        )

        assertEquals(1, visible.size)
        assertEquals("streamed", visible.single().id)
        assertEquals(final, visible.single().text)
        assertTrue(visible.single().isFinalAnswer)
    }

    private fun block(
        id: String,
        kind: MobileTranscriptKind,
        text: String,
        historical: Boolean = false,
        final: Boolean = false,
    ) = MobileTranscriptBlock(
        id = id,
        kind = kind,
        text = text,
        isHistorical = historical,
        isFinalAnswer = final,
    )

    private fun agent(number: Int) = AgentSummary(
        number = number,
        status = "success",
        reasoningTokens = null,
        seconds = null,
        recommended = number == 2,
        rejected = false,
        workspaceAvailable = true,
        canAccept = true,
        canContinue = true,
    )
}
