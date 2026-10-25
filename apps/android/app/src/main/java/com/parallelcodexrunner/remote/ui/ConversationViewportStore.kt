package com.parallelcodexrunner.remote.ui

import android.content.Context
import android.util.Base64
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.snapshots.SnapshotStateMap
import java.security.MessageDigest
import kotlin.math.roundToInt

private const val VIEWPORT_FOCUS = 0.5

internal fun conversationViewportTextScale(
    appTextScale: Float,
    systemFontScale: Float,
): Float = (appTextScale.takeIf { it > 0f } ?: 1f) *
    (systemFontScale.takeIf { it > 0f } ?: 1f)

internal fun conversationViewportWidthChanged(
    savedWidthDp: Int?,
    currentWidthDp: Int,
): Boolean = savedWidthDp?.takeIf { it > 0 }?.let { saved ->
    currentWidthDp > 0 && saved != currentWidthDp
} ?: false

internal data class ConversationViewportRecord(
    val anchorBlockId: String?,
    val anchorSignature: String?,
    val anchorIndex: Int,
    val anchorOffset: Int,
    val followLatest: Boolean,
    val anchorItemSize: Int? = null,
    val anchorViewportSize: Int? = null,
    val anchorTextOffset: Int? = null,
    val anchorTextSizePx: Float? = null,
    val anchorTextViewportFraction: Float? = null,
    val textScale: Float = 1f,
    val layoutWidthDp: Int? = null,
)

internal data class ConversationViewportPosition(
    val index: Int,
    val offset: Int,
)

internal data class ConversationTextLayout(
    val textSizePx: Float,
    val lineStarts: IntArray,
    val lineTops: IntArray,
    val lineBottoms: IntArray,
) {
    fun characterOffsetAtY(y: Int): Int? {
        if (lineStarts.isEmpty()) return null
        val line = lineBottoms.indexOfFirst { y < it }
            .takeIf { it >= 0 } ?: lineStarts.lastIndex
        return lineStarts[line]
    }

    fun yForCharacterOffset(offset: Int): Int? {
        if (lineStarts.isEmpty()) return null
        val search = lineStarts.binarySearch(offset.coerceAtLeast(0))
        val line = if (search >= 0) search else (-search - 2).coerceAtLeast(0)
        return lineTops[line.coerceAtMost(lineTops.lastIndex)]
    }

    fun hasSameGeometry(other: ConversationTextLayout): Boolean =
        textSizePx.approximatelyEquals(other.textSizePx) &&
            lineStarts.contentEquals(other.lineStarts) &&
            lineTops.contentEquals(other.lineTops) &&
            lineBottoms.contentEquals(other.lineBottoms)
}

internal fun restoredConversationTextOffset(
    record: ConversationViewportRecord,
    layout: ConversationTextLayout,
    currentItemSize: Int? = null,
    currentViewportSize: Int? = null,
): Int? {
    val textOffset = record.anchorTextOffset ?: return null
    val textY = layout.yForCharacterOffset(textOffset) ?: return null
    val viewportFraction = record.anchorTextViewportFraction
        ?.coerceIn(0f, 1f) ?: 0f
    val viewportSize = currentViewportSize?.takeIf { it > 0 }
        ?: record.anchorViewportSize?.takeIf { it > 0 }
    val visibleOffset = textY - (
        (viewportSize ?: 0) * viewportFraction
        ).roundToInt()
    return currentItemSize?.takeIf { it > 0 }?.let { itemSize ->
        visibleOffset.coerceIn(0, (itemSize - 1).coerceAtLeast(0))
    } ?: visibleOffset.coerceAtLeast(0)
}

internal fun conversationViewportKey(
    macId: String?,
    workspaceId: String?,
    runId: String?,
    agent: Int?,
): String = listOf(
    macId.orEmpty(),
    workspaceId.orEmpty(),
    runId.orEmpty(),
    agent?.toString().orEmpty(),
).joinToString("\u001f")

internal fun conversationViewportRecord(
    firstVisibleItemIndex: Int,
    firstVisibleItemScrollOffset: Int,
    followLatest: Boolean,
    blocks: List<MobileTranscriptBlock>,
    firstVisibleItemSize: Int? = null,
    viewportSize: Int? = null,
    anchorTextOffset: Int? = null,
    anchorTextSizePx: Float? = null,
    anchorTextViewportFraction: Float? = null,
    textScale: Float = 1f,
    layoutWidthDp: Int? = null,
): ConversationViewportRecord {
    val anchor = blocks.getOrNull(firstVisibleItemIndex)
    return ConversationViewportRecord(
        anchorBlockId = anchor?.id,
        anchorSignature = anchor?.let(::conversationBlockSignature),
        anchorIndex = firstVisibleItemIndex,
        anchorOffset = firstVisibleItemScrollOffset.coerceAtLeast(0),
        followLatest = followLatest,
        anchorItemSize = firstVisibleItemSize?.takeIf { it > 0 },
        anchorViewportSize = viewportSize?.takeIf { it > 0 },
        anchorTextOffset = anchorTextOffset?.takeIf { it >= 0 },
        anchorTextSizePx = anchorTextSizePx?.takeIf { it > 0f },
        anchorTextViewportFraction = anchorTextViewportFraction
            ?.takeIf { it in 0f..1f },
        textScale = textScale.takeIf { it > 0f } ?: 1f,
        layoutWidthDp = layoutWidthDp?.takeIf { it > 0 },
    )
}

internal fun rescaledConversationOffset(
    record: ConversationViewportRecord,
    currentTextScale: Float,
    currentItemSize: Int? = null,
    currentViewportSize: Int? = null,
): Int {
    val oldItemSize = record.anchorItemSize
    val newItemSize = currentItemSize?.takeIf { it > 0 }
    val ratio = if (oldItemSize != null && oldItemSize > 0 && newItemSize != null) {
        newItemSize.toDouble() / oldItemSize.toDouble()
    } else {
        val oldScale = record.textScale.takeIf { it > 0f } ?: 1f
        val newScale = currentTextScale.takeIf { it > 0f } ?: 1f
        newScale.toDouble() / oldScale.toDouble()
    }
    val oldViewportSize = record.anchorViewportSize
    val newViewportSize = currentViewportSize?.takeIf { it > 0 }
    val offset = if (
        oldViewportSize != null && oldViewportSize > 0 && newViewportSize != null
    ) {
        // Preserve the content at the middle of the viewport, not merely the raw
        // distance from the top of one very tall Markdown item. This remains stable
        // when larger text both increases line height and changes line wrapping.
        (
            (record.anchorOffset + oldViewportSize * VIEWPORT_FOCUS) * ratio -
                newViewportSize * VIEWPORT_FOCUS
            ).roundToInt().coerceAtLeast(0)
    } else {
        (record.anchorOffset * ratio).roundToInt().coerceAtLeast(0)
    }
    return if (newItemSize == null) {
        offset
    } else {
        offset.coerceAtMost((newItemSize - 1).coerceAtLeast(0))
    }
}

internal fun restoredConversationIndex(
    record: ConversationViewportRecord,
    blocks: List<MobileTranscriptBlock>,
): Int? {
    record.anchorBlockId?.let { anchorId ->
        blocks.indexOfFirst { it.id == anchorId }
            .takeIf { it >= 0 }
            ?.let { return it }
    }
    val signature = record.anchorSignature ?: return null
    return blocks.indices
        .filter { conversationBlockSignature(blocks[it]) == signature }
        .minByOrNull { kotlin.math.abs(it - record.anchorIndex) }
}

internal fun initialConversationViewportPosition(
    record: ConversationViewportRecord?,
    blocks: List<MobileTranscriptBlock>,
    textScale: Float = 1f,
): ConversationViewportPosition {
    if (record == null || record.followLatest) {
        // AgentConversation appends one tail item after these blocks. Starting
        // there lets LazyColumn lay out from the bottom on its first frame.
        return ConversationViewportPosition(blocks.size, 0)
    }
    val restoredIndex = restoredConversationIndex(record, blocks)
    return ConversationViewportPosition(
        index = restoredIndex ?: record.anchorIndex.coerceIn(0, blocks.size),
        offset = rescaledConversationOffset(record, textScale),
    )
}

private fun conversationBlockSignature(block: MobileTranscriptBlock): String {
    val stablePrefix = buildString {
        append(block.kind.name)
        append('\u0000')
        append(block.operationId.orEmpty())
        append('\u0000')
        append(block.text.take(512))
    }
    return MessageDigest.getInstance("SHA-256")
        .digest(stablePrefix.toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
}

internal data class ConversationViewport(
    val listState: LazyListState,
    val followLatest: MutableState<Boolean>,
    val pendingRestore: MutableState<ConversationViewportRecord?>,
    val programmaticScroll: MutableState<Boolean>,
    val textLayouts: SnapshotStateMap<String, ConversationTextLayout>,
    var blocks: List<MobileTranscriptBlock>,
    var latestRecord: ConversationViewportRecord?,
    var textScale: Float,
    var layoutWidthDp: Int,
)

internal class ConversationViewportStore(context: Context) {
    private val preferences = context.getSharedPreferences(
        "pcr_conversation_viewports",
        Context.MODE_PRIVATE,
    )
    private val active = LinkedHashMap<String, ConversationViewport>(
        MAX_ACTIVE_VIEWPORTS,
        0.75f,
        true,
    )

    fun get(
        key: String,
        blocks: List<MobileTranscriptBlock>,
        textScale: Float = 1f,
        layoutWidthDp: Int = 0,
    ): ConversationViewport {
        active[key]?.let { viewport ->
            viewport.blocks = blocks
            // LazyListState remembers an item index, not the conversation item that
            // occupied that index. History may be prepended while this conversation is
            // off screen, so re-anchor every returning viewport by its stable block id.
            viewport.latestRecord?.let { record ->
                viewport.followLatest.value = record.followLatest
                if (record.followLatest) {
                    viewport.pendingRestore.value = null
                    viewport.listState.requestScrollToItem(blocks.size)
                } else {
                    val restoredIndex = restoredConversationIndex(record, blocks)
                    val scaleChanged = !record.textScale.approximatelyEquals(textScale)
                    val widthChanged = conversationViewportWidthChanged(
                        savedWidthDp = record.layoutWidthDp,
                        currentWidthDp = layoutWidthDp,
                    )
                    if (scaleChanged || widthChanged) {
                        viewport.textLayouts.clear()
                    }
                    viewport.pendingRestore.value = record.takeIf {
                        restoredIndex == null || scaleChanged || widthChanged
                    }
                    restoredIndex?.let { index ->
                        viewport.listState.requestScrollToItem(
                            index,
                            rescaledConversationOffset(record, textScale),
                        )
                    }
                }
            }
            viewport.textScale = textScale
            viewport.layoutWidthDp = layoutWidthDp
            return viewport
        }
        val saved = load(key)
        val restoredIndex = saved?.takeUnless { it.followLatest }?.let { record ->
            restoredConversationIndex(record, blocks)
        }
        val pendingRestore = saved?.takeIf { record ->
            !record.followLatest && (
                !record.textScale.approximatelyEquals(textScale) ||
                    conversationViewportWidthChanged(
                        savedWidthDp = record.layoutWidthDp,
                        currentWidthDp = layoutWidthDp,
                    ) ||
                    (record.anchorBlockId != null && restoredIndex == null)
                )
        }
        val initialPosition = initialConversationViewportPosition(saved, blocks, textScale)
        val viewport = ConversationViewport(
            listState = LazyListState(initialPosition.index, initialPosition.offset),
            followLatest = mutableStateOf(saved?.followLatest ?: true),
            pendingRestore = mutableStateOf(pendingRestore),
            programmaticScroll = mutableStateOf(false),
            textLayouts = mutableStateMapOf(),
            blocks = blocks,
            latestRecord = saved,
            textScale = textScale,
            layoutWidthDp = layoutWidthDp,
        )
        active[key] = viewport
        trimActiveViewports()
        return viewport
    }

    fun save(
        key: String,
        viewport: ConversationViewport,
        blocks: List<MobileTranscriptBlock> = viewport.blocks,
    ) {
        viewport.blocks = blocks
        if (viewport.pendingRestore.value != null) return
        val layout = viewport.listState.layoutInfo
        val firstVisibleItemIndex = viewport.listState.firstVisibleItemIndex
        val firstVisibleItemSize = layout.visibleItemsInfo
            .firstOrNull { it.index == firstVisibleItemIndex }
            ?.size
        val viewportSize = (layout.viewportEndOffset - layout.viewportStartOffset)
            .takeIf { it > 0 }
        val textLayout = blocks.getOrNull(firstVisibleItemIndex)
            ?.id
            ?.let(viewport.textLayouts::get)
        val textViewportFraction = VIEWPORT_FOCUS.toFloat()
        val anchorTextOffset = textLayout?.characterOffsetAtY(
            viewport.listState.firstVisibleItemScrollOffset +
                ((viewportSize ?: 0) * textViewportFraction).roundToInt(),
        )
        val record = if (layout.totalItemsCount == 0) {
            viewport.latestRecord ?: conversationViewportRecord(
                firstVisibleItemIndex = firstVisibleItemIndex,
                firstVisibleItemScrollOffset = viewport.listState.firstVisibleItemScrollOffset,
                followLatest = viewport.followLatest.value,
                blocks = blocks,
                firstVisibleItemSize = firstVisibleItemSize,
                viewportSize = viewportSize,
                anchorTextOffset = anchorTextOffset,
                anchorTextSizePx = textLayout?.textSizePx,
                anchorTextViewportFraction = textViewportFraction,
                textScale = viewport.textScale,
                layoutWidthDp = viewport.layoutWidthDp,
            )
        } else {
            val lastVisible = layout.visibleItemsInfo.lastOrNull()
            val atEnd = conversationIsAtEnd(
                totalItemsCount = layout.totalItemsCount,
                lastVisibleIndex = lastVisible?.index,
                lastVisibleEnd = lastVisible?.let { it.offset + it.size },
                viewportEndOffset = layout.viewportEndOffset,
            )
            conversationViewportRecord(
                firstVisibleItemIndex = firstVisibleItemIndex,
                firstVisibleItemScrollOffset = viewport.listState.firstVisibleItemScrollOffset,
                followLatest = atEnd,
                blocks = blocks,
                firstVisibleItemSize = firstVisibleItemSize,
                viewportSize = viewportSize,
                anchorTextOffset = anchorTextOffset,
                anchorTextSizePx = textLayout?.textSizePx,
                anchorTextViewportFraction = textViewportFraction,
                textScale = viewport.textScale,
                layoutWidthDp = viewport.layoutWidthDp,
            )
        }
        viewport.latestRecord = record
        val prefix = preferencePrefix(key)
        preferences.edit()
            .putBoolean("$prefix.present", true)
            .putInt("$prefix.index", record.anchorIndex)
            .putInt("$prefix.offset", record.anchorOffset)
            .putBoolean("$prefix.follow", record.followLatest)
            .putInt("$prefix.itemSize", record.anchorItemSize ?: -1)
            .putInt("$prefix.viewportSize", record.anchorViewportSize ?: -1)
            .putInt("$prefix.textOffset", record.anchorTextOffset ?: -1)
            .putFloat("$prefix.textSizePx", record.anchorTextSizePx ?: -1f)
            .putFloat(
                "$prefix.textViewportFraction",
                record.anchorTextViewportFraction ?: -1f,
            )
            .putFloat("$prefix.textScale", record.textScale)
            .putInt("$prefix.layoutWidthDp", record.layoutWidthDp ?: -1)
            .apply {
                if (record.anchorBlockId == null) {
                    remove("$prefix.anchor")
                } else {
                    putString("$prefix.anchor", record.anchorBlockId)
                }
                if (record.anchorSignature == null) {
                    remove("$prefix.signature")
                } else {
                    putString("$prefix.signature", record.anchorSignature)
                }
            }
            .apply()
    }

    fun saveAll() {
        active.toList().forEach { (key, viewport) ->
            save(key, viewport)
        }
    }

    private fun trimActiveViewports() {
        while (active.size > MAX_ACTIVE_VIEWPORTS) {
            val iterator = active.entries.iterator()
            if (!iterator.hasNext()) return
            val (key, viewport) = iterator.next()
            save(key, viewport)
            iterator.remove()
        }
    }

    private fun load(key: String): ConversationViewportRecord? {
        val prefix = preferencePrefix(key)
        if (!preferences.getBoolean("$prefix.present", false)) return null
        return ConversationViewportRecord(
            anchorBlockId = preferences.getString("$prefix.anchor", null),
            anchorSignature = preferences.getString("$prefix.signature", null),
            anchorIndex = preferences.getInt("$prefix.index", 0).coerceAtLeast(0),
            anchorOffset = preferences.getInt("$prefix.offset", 0).coerceAtLeast(0),
            followLatest = preferences.getBoolean("$prefix.follow", true),
            anchorItemSize = preferences.getInt("$prefix.itemSize", -1).takeIf { it > 0 },
            anchorViewportSize = preferences.getInt("$prefix.viewportSize", -1)
                .takeIf { it > 0 },
            anchorTextOffset = preferences.getInt("$prefix.textOffset", -1)
                .takeIf { it >= 0 },
            anchorTextSizePx = preferences.getFloat("$prefix.textSizePx", -1f)
                .takeIf { it > 0f },
            anchorTextViewportFraction = preferences.getFloat(
                "$prefix.textViewportFraction",
                -1f,
            ).takeIf { it in 0f..1f },
            textScale = preferences.getFloat("$prefix.textScale", 1f)
                .takeIf { it > 0f } ?: 1f,
            layoutWidthDp = preferences.getInt("$prefix.layoutWidthDp", -1)
                .takeIf { it > 0 },
        )
    }

    private fun preferencePrefix(key: String): String = Base64.encodeToString(
        key.toByteArray(Charsets.UTF_8),
        Base64.NO_PADDING or Base64.NO_WRAP or Base64.URL_SAFE,
    )

    private companion object {
        const val MAX_ACTIVE_VIEWPORTS = 8
    }
}

private fun Float.approximatelyEquals(other: Float): Boolean =
    kotlin.math.abs(this - other) < 0.0001f
