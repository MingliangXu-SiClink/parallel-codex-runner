package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.times
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.foundation.shape.RoundedCornerShape
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownColor
import com.mikepenz.markdown.m3.markdownTypography
import com.mikepenz.markdown.compose.LocalMarkdownColors
import com.mikepenz.markdown.compose.LocalMarkdownDimens
import com.mikepenz.markdown.compose.components.markdownComponents
import com.mikepenz.markdown.compose.elements.MarkdownDivider
import com.mikepenz.markdown.compose.elements.MarkdownTableHeader
import com.mikepenz.markdown.compose.elements.MarkdownTableRow
import com.mikepenz.markdown.model.markdownAnimations
import com.mikepenz.markdown.model.markdownAnnotator
import com.mikepenz.markdown.model.markdownAnnotatorConfig
import com.mikepenz.markdown.model.markdownDimens
import com.mikepenz.markdown.model.markdownPadding
import com.mikepenz.markdown.model.MarkdownState
import com.mikepenz.markdown.model.ReferenceLinkHandler
import com.mikepenz.markdown.model.State
import com.mikepenz.markdown.model.parseMarkdownFlow
import com.mikepenz.markdown.model.rememberMarkdownState
import org.intellij.markdown.ast.ASTNode
import org.intellij.markdown.ast.findChildOfType
import org.intellij.markdown.flavours.gfm.GFMFlavourDescriptor
import org.intellij.markdown.flavours.gfm.GFMElementTypes.HEADER
import org.intellij.markdown.flavours.gfm.GFMElementTypes.ROW
import org.intellij.markdown.flavours.gfm.GFMTokenTypes.CELL
import org.intellij.markdown.flavours.gfm.GFMTokenTypes.TABLE_SEPARATOR
import org.intellij.markdown.parser.MarkdownParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap
import java.util.Locale

private object MarkdownTranscriptStateCache {
    private const val MaxEntries = 256
    private const val MaxCharacters = 4_000_000
    private const val MaxSingleEntryCharacters = 300_000

    private val states = LinkedHashMap<String, MarkdownState>(MaxEntries, 0.75f, true)
    private var characterCount = 0

    @Synchronized
    fun get(content: String): MarkdownState? = states[content]

    @Synchronized
    fun missing(contents: Collection<String>): List<String> = contents.filterNot(states::containsKey)

    @Synchronized
    fun put(content: String, state: MarkdownState) {
        if (content.isEmpty() || content.length > MaxSingleEntryCharacters) return
        val previous = states.put(content, state)
        if (previous == null) characterCount += content.length
        while (states.size > MaxEntries || characterCount > MaxCharacters) {
            val iterator = states.entries.iterator()
            if (!iterator.hasNext()) break
            val eldest = iterator.next()
            characterCount -= eldest.key.length
            iterator.remove()
        }
    }
}

private val markdownTranscriptKinds = setOf(
    MobileTranscriptKind.USER,
    MobileTranscriptKind.THOUGHT,
    MobileTranscriptKind.OUTPUT,
    MobileTranscriptKind.WARNING,
    MobileTranscriptKind.ERROR,
)

internal fun visibleMarkdownContents(snapshot: WorkspaceRunSnapshot): List<String> {
    val selected = snapshot.selectedAgent ?: return snapshot.conversationHistory
        .asSequence()
        .filter { it.kind in markdownTranscriptKinds }
        .map(MobileTranscriptBlock::text)
        .filter(String::isNotBlank)
        .distinct()
        .toList()
    val transcript = snapshot.agentTranscripts[selected] ?: MobileAgentTranscript()
    return MobileTranscriptFormatter.visibleBlocks(
        history = snapshot.conversationHistory,
        prompt = snapshot.prompt,
        transcript = transcript,
    )
        .asSequence()
        .filter { it.kind in markdownTranscriptKinds }
        .map(MobileTranscriptBlock::text)
        .filter(String::isNotBlank)
        .distinct()
        .toList()
}

private class PreparsedReferenceLinkHandler : ReferenceLinkHandler {
    private val links = ConcurrentHashMap<String, String>()

    override fun store(label: String, destination: String?) {
        val normalized = label.lowercase(Locale.ROOT)
        if (destination == null) {
            links.remove(normalized)
        } else {
            links[normalized] = destination
        }
    }

    override fun find(label: String): String = links[label.lowercase(Locale.ROOT)].orEmpty()

    fun snapshot(): Map<String, String> = links.toMap()
}

private class PreparsedMarkdownState(
    private val content: String,
    private val flavour: GFMFlavourDescriptor,
    private val parser: MarkdownParser,
    private val referenceLinkHandler: PreparsedReferenceLinkHandler,
) : MarkdownState {
    private val mutableState = MutableStateFlow<State>(State.Loading(referenceLinkHandler))
    override val state: StateFlow<State> = mutableState.asStateFlow()
    private val mutableLinks = MutableStateFlow<Map<String, String>>(emptyMap())
    override val links: StateFlow<Map<String, String>> = mutableLinks.asStateFlow()

    override suspend fun parse(): State {
        val current = mutableState.value
        if (current !is State.Loading) return current
        val parsed = try {
            parseMarkdownFlow(
                content = content,
                lookupLinks = true,
                flavour = flavour,
                parser = parser,
                referenceLinkHandler = referenceLinkHandler,
            ).first { it !is State.Loading }
        } catch (error: Throwable) {
            State.Error(error, referenceLinkHandler)
        }
        mutableLinks.value = referenceLinkHandler.snapshot()
        mutableState.value = parsed
        return parsed
    }
}

private suspend fun preparseMarkdown(
    content: String,
    flavour: GFMFlavourDescriptor,
    parser: MarkdownParser,
): MarkdownState {
    val state = PreparsedMarkdownState(
        content = content,
        flavour = flavour,
        parser = parser,
        referenceLinkHandler = PreparsedReferenceLinkHandler(),
    )
    state.parse()
    return state
}

/** Parses the Markdown that the next conversation frame will display off the UI thread. */
internal suspend fun prewarmVisibleMarkdown(snapshot: WorkspaceRunSnapshot) = withContext(Dispatchers.Default) {
    val contents = MarkdownTranscriptStateCache.missing(
        visibleMarkdownContents(snapshot).filterNot(::containsLatex),
    )
    if (contents.isEmpty()) return@withContext

    val flavour = GFMFlavourDescriptor()
    val parser = MarkdownParser(flavour)
    contents.forEach { content ->
        val state = preparseMarkdown(content, flavour, parser)
        MarkdownTranscriptStateCache.put(content, state)
    }
}

internal data class MarkdownTableScrollIndicators(
    val backward: Boolean,
    val forward: Boolean,
)

internal fun markdownTableScrollIndicators(
    value: Int,
    maximum: Int,
): MarkdownTableScrollIndicators {
    val safeMaximum = maximum.coerceAtLeast(0)
    val safeValue = value.coerceIn(0, safeMaximum)
    return MarkdownTableScrollIndicators(
        backward = safeValue > 0,
        forward = safeValue < safeMaximum,
    )
}

internal fun markdownTableLayoutWidth(
    columnCount: Int,
    availableWidth: Dp,
    preferredCellWidth: Dp,
    minimumCellWidth: Dp = 100.dp,
): Dp {
    if (columnCount <= 0 || availableWidth <= 0.dp) return availableWidth
    val naturalWidth = preferredCellWidth * columnCount
    val fittedCellWidth = availableWidth / columnCount.toFloat()
    return if (naturalWidth <= availableWidth || fittedCellWidth >= minimumCellWidth) {
        availableWidth
    } else {
        naturalWidth
    }
}

@Composable
private fun ScrollableMarkdownTable(
    content: String,
    node: ASTNode,
    style: TextStyle,
) {
    val dimens = LocalMarkdownDimens.current
    val background = LocalMarkdownColors.current.tableBackground
    val columnCount = remember(node) {
        node.findChildOfType(HEADER)?.children?.count { it.type == CELL } ?: 0
    }
    val scrollState = rememberScrollState()
    val shape = RoundedCornerShape(dimens.tableCornerSize)

    BoxWithConstraints(
        modifier = Modifier
            .background(background, shape)
            .border(
                width = 1.dp,
                color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.72f),
                shape = shape,
            )
            .widthIn(max = dimens.tableMaxWidth),
    ) {
        val tableWidth = markdownTableLayoutWidth(
            columnCount = columnCount,
            availableWidth = maxWidth,
            preferredCellWidth = dimens.tableCellWidth,
        )
        val scrollable = tableWidth > maxWidth
        Column(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = if (scrollable) {
                    Modifier
                        .horizontalScroll(scrollState)
                        .requiredWidth(tableWidth)
                } else {
                    Modifier.fillMaxWidth()
                },
            ) {
                var rowIndex = 1
                node.children.forEach { child ->
                    when (child.type) {
                        HEADER -> MarkdownTableHeader(
                            content = content,
                            header = child,
                            tableWidth = tableWidth,
                            style = style,
                            maxLines = 6,
                            overflow = TextOverflow.Ellipsis,
                        )
                        ROW -> {
                            MarkdownTableRow(
                                content = content,
                                header = child,
                                tableWidth = tableWidth,
                                style = style,
                                rowIndex = rowIndex,
                                maxLines = 12,
                                overflow = TextOverflow.Ellipsis,
                            )
                            rowIndex += 1
                        }
                        TABLE_SEPARATOR -> MarkdownDivider()
                    }
                }
            }

            if (scrollable) {
                MarkdownTableScrollBar(scrollState)
            }
        }
    }
}

@Composable
private fun MarkdownTableScrollBar(
    scrollState: androidx.compose.foundation.ScrollState,
) {
    val trackColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.12f)
    val thumbColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.38f)
    Canvas(
        modifier = Modifier
            .fillMaxWidth()
            .height(6.dp),
    ) {
        // Read rapidly changing scroll state during drawing so horizontal table
        // scrolling invalidates only the scrollbar canvas, not its composition.
        val maximum = scrollState.maxValue
        val position = scrollState.value
        val indicators = markdownTableScrollIndicators(position, maximum)
        val centerY = size.height / 2f
        val strokeWidth = 2.dp.toPx()
        drawLine(
            color = trackColor,
            start = Offset(0f, centerY),
            end = Offset(size.width, centerY),
            strokeWidth = strokeWidth,
            cap = StrokeCap.Round,
        )
        if (!indicators.backward && !indicators.forward && maximum != Int.MAX_VALUE) return@Canvas

        val contentWidth = size.width + maximum.coerceAtLeast(0).toFloat()
        val naturalThumbWidth = if (contentWidth > 0f) {
            size.width * (size.width / contentWidth)
        } else {
            size.width
        }
        val thumbWidth = naturalThumbWidth.coerceIn(24.dp.toPx(), size.width)
        val travel = (size.width - thumbWidth).coerceAtLeast(0f)
        val progress = if (maximum > 0 && maximum != Int.MAX_VALUE) {
            position.toFloat() / maximum.toFloat()
        } else {
            0f
        }
        val startX = travel * progress.coerceIn(0f, 1f)
        drawLine(
            color = thumbColor,
            start = Offset(startX, centerY),
            end = Offset(startX + thumbWidth, centerY),
            strokeWidth = strokeWidth,
            cap = StrokeCap.Round,
        )
    }
}

@Composable
internal fun MarkdownTranscript(
    content: String,
    textColor: Color,
    baseStyle: TextStyle,
    modifier: Modifier = Modifier,
    onOpenLink: (String) -> Unit = {},
    layoutKey: String? = null,
    onTextLayout: (String, ConversationTextLayout) -> Unit = { _, _ -> },
    preserveLineBreaks: Boolean = false,
) {
    if (containsLatex(content)) {
        LatexMarkdownTranscript(
            content = content,
            textColor = textColor,
            baseStyle = baseStyle,
            modifier = modifier,
            onOpenLink = onOpenLink,
            layoutKey = layoutKey,
            onTextLayout = onTextLayout,
            preserveLineBreaks = preserveLineBreaks,
        )
        return
    }
    // The list restores a pixel offset, so its first layout must use the final Markdown height.
    // Keep parsed states across conversation switches: revisiting cached history then has the
    // stable height without parsing the same Markdown again on the composition thread.
    val cachedState = remember(content) { MarkdownTranscriptStateCache.get(content) }
    // A retained parser is mutable. Reusing it for the next prompt would also
    // rewrite the old prompt through the content-keyed history cache.
    val markdownState = key(content) {
        cachedState ?: rememberMarkdownState(
            content = content,
            immediate = true,
            retainState = true,
        )
    }
    SideEffect {
        if (cachedState == null) MarkdownTranscriptStateCache.put(content, markdownState)
    }
    val linkStyle = TextLinkStyles(
        style = baseStyle.copy(
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.Medium,
            textDecoration = TextDecoration.Underline,
        ).toSpanStyle(),
    )

    val linkHandler = remember(onOpenLink) {
        object : UriHandler {
            override fun openUri(uri: String) = onOpenLink(uri)
        }
    }
    CompositionLocalProvider(LocalUriHandler provides linkHandler) {
        SelectionContainer {
            BoxWithConstraints(modifier = modifier.fillMaxWidth()) {
                val compactTable = maxWidth < 520.dp
                Markdown(
                    markdownState = markdownState,
                    annotator = markdownAnnotator(
                        config = markdownAnnotatorConfig(eolAsNewLine = preserveLineBreaks),
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    colors = markdownColor(
                        text = textColor,
                        codeBackground = MaterialTheme.colorScheme.surfaceContainerLow,
                        inlineCodeBackground = Color.Transparent,
                        dividerColor = MaterialTheme.colorScheme.outlineVariant,
                        tableBackground = MaterialTheme.colorScheme.surfaceContainerLowest,
                    ),
                    typography = markdownTypography(
                        h1 = MaterialTheme.typography.titleLarge.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        h2 = MaterialTheme.typography.titleMedium.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        h3 = MaterialTheme.typography.titleSmall.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        h4 = MaterialTheme.typography.bodyLarge.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        h5 = MaterialTheme.typography.bodyMedium.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        h6 = MaterialTheme.typography.bodySmall.copy(
                            color = textColor,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        text = baseStyle.copy(color = textColor),
                        paragraph = baseStyle.copy(color = textColor),
                        ordered = baseStyle.copy(color = textColor),
                        bullet = baseStyle.copy(color = textColor),
                        list = baseStyle.copy(color = textColor),
                        quote = baseStyle.copy(
                            color = textColor.copy(alpha = 0.82f),
                            fontStyle = FontStyle.Italic,
                        ),
                        code = baseStyle.copy(
                            color = textColor,
                            fontFamily = FontFamily.Monospace,
                        ),
                        inlineCode = baseStyle.copy(
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Medium,
                        ),
                        textLink = linkStyle,
                        table = baseStyle.copy(
                            color = textColor,
                            fontSize = baseStyle.fontSize * 0.9f,
                        ),
                    ),
                    components = markdownComponents(
                        table = { model ->
                            ScrollableMarkdownTable(
                                content = model.content,
                                node = model.node,
                                style = model.typography.table,
                            )
                        },
                    ),
                    dimens = markdownDimens(
                        tableCellWidth = if (compactTable) 112.dp else 160.dp,
                        tableCellPadding = if (compactTable) 6.dp else 12.dp,
                    ),
                    padding = markdownPadding(
                        block = 3.dp,
                        list = 2.dp,
                        listItemTop = 1.dp,
                        listItemBottom = 1.dp,
                        listIndent = 12.dp,
                    ),
                    animations = markdownAnimations(animateTextSize = { this }),
                    error = { fallbackModifier ->
                        Text(
                            text = content,
                            modifier = fallbackModifier.fillMaxWidth(),
                            style = baseStyle,
                            color = textColor,
                        )
                    },
                )
            }
        }
    }
}
