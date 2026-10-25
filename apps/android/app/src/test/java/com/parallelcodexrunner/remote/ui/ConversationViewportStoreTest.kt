package com.parallelcodexrunner.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class ConversationViewportStoreTest {
    @Test
    fun viewportScaleIncludesTheAndroidSystemFontScale() {
        assertEquals(
            1.3225f,
            conversationViewportTextScale(
                appTextScale = 1.15f,
                systemFontScale = 1.15f,
            ),
            0.0001f,
        )
        assertEquals(
            1f,
            conversationViewportTextScale(
                appTextScale = 0f,
                systemFontScale = -1f,
            ),
            0f,
        )
    }

    @Test
    fun viewportWidthChangesOnlyWhenBothLayoutsAreKnownAndDifferent() {
        assertEquals(false, conversationViewportWidthChanged(null, 360))
        assertEquals(false, conversationViewportWidthChanged(0, 360))
        assertEquals(false, conversationViewportWidthChanged(360, 360))
        assertEquals(true, conversationViewportWidthChanged(360, 411))
    }

    @Test
    fun viewportIdentityIncludesSelectedAgent() {
        val first = conversationViewportKey("mac", "workspace", "run", 1)
        val second = conversationViewportKey("mac", "workspace", "run", 2)

        assertNotEquals(first, second)
        assertEquals(first, conversationViewportKey("mac", "workspace", "run", 1))
    }

    @Test
    fun aNewOrBottomFollowingAgentStartsAtTheTailBeforeItsFirstFrame() {
        val blocks = listOf(block("prompt"), block("agent-switch"), block("answer"))

        assertEquals(
            ConversationViewportPosition(index = blocks.size, offset = 0),
            initialConversationViewportPosition(null, blocks),
        )
        assertEquals(
            ConversationViewportPosition(index = blocks.size, offset = 0),
            initialConversationViewportPosition(
                ConversationViewportRecord(
                    anchorBlockId = "prompt",
                    anchorSignature = null,
                    anchorIndex = 0,
                    anchorOffset = 0,
                    followLatest = true,
                ),
                blocks,
            ),
        )
    }

    @Test
    fun aSavedAgentStartsAtItsResolvedAnchorWithoutASecondFrameJump() {
        val blocks = listOf(block("older"), block("answer"), block("newer"))
        val record = ConversationViewportRecord(
            anchorBlockId = "answer",
            anchorSignature = null,
            anchorIndex = 0,
            anchorOffset = 137,
            followLatest = false,
        )

        assertEquals(
            ConversationViewportPosition(index = 1, offset = 137),
            initialConversationViewportPosition(record, blocks),
        )
    }

    @Test
    fun rescalesTheSavedOffsetWhenTextSizeChanges() {
        val record = ConversationViewportRecord(
            anchorBlockId = "answer",
            anchorSignature = null,
            anchorIndex = 1,
            anchorOffset = 800,
            followLatest = false,
            anchorItemSize = 1_000,
            anchorViewportSize = 400,
            textScale = 1f,
        )

        assertEquals(920, rescaledConversationOffset(record, currentTextScale = 1.15f))
        assertEquals(
            1_200,
            rescaledConversationOffset(
                record,
                currentTextScale = 1.15f,
                currentItemSize = 1_500,
            ),
        )
        assertEquals(
            1_300,
            rescaledConversationOffset(
                record,
                currentTextScale = 1.15f,
                currentItemSize = 1_500,
                currentViewportSize = 400,
            ),
        )
    }

    @Test
    fun initialRestoreUsesTheCurrentTextScaleBeforeTheFirstFrame() {
        val blocks = listOf(block("older"), block("answer"), block("newer"))
        val record = ConversationViewportRecord(
            anchorBlockId = "answer",
            anchorSignature = null,
            anchorIndex = 1,
            anchorOffset = 200,
            followLatest = false,
            textScale = 1f,
        )

        assertEquals(
            ConversationViewportPosition(index = 1, offset = 230),
            initialConversationViewportPosition(record, blocks, textScale = 1.15f),
        )
    }

    @Test
    fun textLayoutResolvesTheLineContainingAViewportAnchor() {
        val layout = ConversationTextLayout(
            textSizePx = 42f,
            lineStarts = intArrayOf(0, 12, 28, 45),
            lineTops = intArrayOf(0, 40, 80, 120),
            lineBottoms = intArrayOf(40, 80, 120, 160),
        )

        assertEquals(12, layout.characterOffsetAtY(67))
        assertEquals(80, layout.yForCharacterOffset(31))
        assertEquals(
            true,
            layout.hasSameGeometry(
                ConversationTextLayout(
                    textSizePx = 42f,
                    lineStarts = intArrayOf(0, 12, 28, 45),
                    lineTops = intArrayOf(0, 40, 80, 120),
                    lineBottoms = intArrayOf(40, 80, 120, 160),
                )
            ),
        )
    }

    @Test
    fun semanticRestoreKeepsTheSameTextAtTheViewportFocus() {
        val record = ConversationViewportRecord(
            anchorBlockId = "answer",
            anchorSignature = null,
            anchorIndex = 1,
            anchorOffset = 600,
            followLatest = false,
            anchorViewportSize = 400,
            anchorTextOffset = 28,
            anchorTextSizePx = 42f,
            anchorTextViewportFraction = 0.5f,
        )
        val resizedLayout = ConversationTextLayout(
            textSizePx = 48.3f,
            lineStarts = intArrayOf(0, 12, 28, 45),
            lineTops = intArrayOf(0, 60, 140, 220),
            lineBottoms = intArrayOf(60, 140, 220, 300),
        )

        assertEquals(
            40,
            restoredConversationTextOffset(
                record = record,
                layout = resizedLayout,
                currentItemSize = 1_000,
                currentViewportSize = 200,
            ),
        )
    }

    @Test
    fun restoresTheSameBlockAfterOlderHistoryIsPrepended() {
        val record = ConversationViewportRecord(
            anchorBlockId = "answer-2",
            anchorSignature = null,
            anchorIndex = 1,
            anchorOffset = 184,
            followLatest = false,
        )
        val blocks = listOf(
            block("older-1"),
            block("older-2"),
            block("answer-1"),
            block("answer-2"),
        )

        assertEquals(3, restoredConversationIndex(record, blocks))
    }

    @Test
    fun capturesTheExactVisibleBlockAndPixelOffset() {
        val blocks = listOf(block("answer-1"), block("answer-2"))

        assertEquals(
            ConversationViewportRecord(
                anchorBlockId = "answer-2",
                anchorSignature = conversationViewportRecord(
                    firstVisibleItemIndex = 1,
                    firstVisibleItemScrollOffset = 0,
                    followLatest = false,
                    blocks = blocks,
                ).anchorSignature,
                anchorIndex = 1,
                anchorOffset = 237,
                followLatest = false,
            ),
            conversationViewportRecord(
                firstVisibleItemIndex = 1,
                firstVisibleItemScrollOffset = 237,
                followLatest = false,
                blocks = blocks,
            ),
        )
    }

    @Test
    fun restoresByContentWhenARebuiltHistoryChangesBlockIds() {
        val oldBlocks = listOf(block("history-4", "same answer"))
        val record = conversationViewportRecord(
            firstVisibleItemIndex = 0,
            firstVisibleItemScrollOffset = 91,
            followLatest = false,
            blocks = oldBlocks,
        )
        val rebuiltBlocks = listOf(
            block("history-0", "older answer"),
            block("history-5", "same answer"),
        )

        assertEquals(1, restoredConversationIndex(record, rebuiltBlocks))
    }

    private fun block(id: String, text: String = id) = MobileTranscriptBlock(
        id = id,
        kind = MobileTranscriptKind.OUTPUT,
        text = text,
    )
}
