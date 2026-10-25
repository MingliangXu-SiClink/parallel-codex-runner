package com.parallelcodexrunner.remote.ui

import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTranscriptTest {
    @Test
    fun compactTablesFitBeforeTheyBecomeScrollable() {
        assertEquals(
            360.dp,
            markdownTableLayoutWidth(
                columnCount = 3,
                availableWidth = 360.dp,
                preferredCellWidth = 112.dp,
            ),
        )
        assertEquals(
            448.dp,
            markdownTableLayoutWidth(
                columnCount = 4,
                availableWidth = 360.dp,
                preferredCellWidth = 112.dp,
            ),
        )
        assertEquals(
            420.dp,
            markdownTableLayoutWidth(
                columnCount = 4,
                availableWidth = 420.dp,
                preferredCellWidth = 112.dp,
            ),
        )
        assertEquals(
            560.dp,
            markdownTableLayoutWidth(
                columnCount = 5,
                availableWidth = 360.dp,
                preferredCellWidth = 112.dp,
            ),
        )
    }

    @Test
    fun scrollIndicatorsTrackTheVisibleTableEdges() {
        assertEquals(
            MarkdownTableScrollIndicators(backward = false, forward = true),
            markdownTableScrollIndicators(value = 0, maximum = 120),
        )
        assertEquals(
            MarkdownTableScrollIndicators(backward = true, forward = true),
            markdownTableScrollIndicators(value = 60, maximum = 120),
        )
        assertEquals(
            MarkdownTableScrollIndicators(backward = true, forward = false),
            markdownTableScrollIndicators(value = 120, maximum = 120),
        )
        assertEquals(
            MarkdownTableScrollIndicators(backward = false, forward = false),
            markdownTableScrollIndicators(value = 0, maximum = 0),
        )
    }

    @Test
    fun visibleMarkdownContentsExcludesWorkProcessCommands() {
        val snapshot = WorkspaceRunSnapshot(
            prompt = "current question",
            selectedAgent = 2,
            conversationHistory = listOf(
                MobileTranscriptBlock("history", MobileTranscriptKind.USER, "earlier question"),
            ),
            agentTranscripts = mapOf(
                2 to MobileAgentTranscript(
                    blocks = listOf(
                        MobileTranscriptBlock("thought", MobileTranscriptKind.THOUGHT, "considering"),
                        MobileTranscriptBlock("command", MobileTranscriptKind.COMMAND, "git status"),
                        MobileTranscriptBlock("answer", MobileTranscriptKind.OUTPUT, "current answer"),
                    ),
                ),
            ),
        )

        val contents = visibleMarkdownContents(snapshot)

        assertTrue("earlier question" in contents)
        assertTrue("current question" in contents)
        assertTrue("considering" in contents)
        assertTrue("current answer" in contents)
        assertFalse("git status" in contents)
    }

}
