package com.parallelcodexrunner.remote.ui

import android.content.Context
import android.text.SpannableString
import android.text.TextPaint
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.text.style.URLSpan
import android.util.TypedValue
import android.view.View
import android.widget.TextView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.isSpecified
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import io.noties.markwon.Markwon
import io.noties.markwon.SoftBreakAddsNewLinePlugin
import io.noties.markwon.ext.latex.JLatexMathPlugin
import io.noties.markwon.ext.tables.TablePlugin
import io.noties.markwon.inlineparser.MarkwonInlineParserPlugin
import java.util.concurrent.ConcurrentHashMap

private val latexRenderers = ConcurrentHashMap<Pair<Int, Boolean>, Markwon>()

internal data class LatexMarkdownRenderSignature(
    val content: String,
    val textSizePx: Float,
    val textColor: Int,
    val preserveLineBreaks: Boolean = false,
)

internal fun latexMarkdownNeedsRender(
    previous: LatexMarkdownRenderSignature?,
    next: LatexMarkdownRenderSignature,
): Boolean = previous != next

private data class LatexLayoutPublishSignature(
    val layoutKey: String?,
    val textSizePx: Float,
)

private class LatexMarkdownTextView(context: Context) : TextView(context) {
    var renderSignature: LatexMarkdownRenderSignature? = null
    var layoutPublishSignature: LatexLayoutPublishSignature? = null
}

internal fun containsLatex(content: String): Boolean {
    if (normalizeLatexMarkdown(content) != content) return true
    var fenced = false
    var fenceMarker = ""
    for (line in content.lineSequence()) {
        val trimmed = line.trimStart()
        val marker = when {
            trimmed.startsWith("```") -> "```"
            trimmed.startsWith("~~~") -> "~~~"
            else -> ""
        }
        if (marker.isNotEmpty() && (!fenced || marker == fenceMarker)) {
            fenced = !fenced
            fenceMarker = if (fenced) marker else ""
            continue
        }
        if (!fenced && lineHasDoubleDollarOutsideCode(line)) return true
    }
    return false
}

internal fun normalizeLatexMarkdown(content: String): String {
    val result = StringBuilder(content.length + 16)
    var fenced = false
    var fenceMarker = ""
    content.lineSequence().forEachIndexed { lineIndex, line ->
        val trimmed = line.trimStart()
        val marker = when {
            trimmed.startsWith("```") -> "```"
            trimmed.startsWith("~~~") -> "~~~"
            else -> ""
        }
        if (marker.isNotEmpty() && (!fenced || marker == fenceMarker)) {
            fenced = !fenced
            fenceMarker = if (fenced) marker else ""
            if (lineIndex > 0) result.append('\n')
            result.append(line)
            return@forEachIndexed
        }
        if (lineIndex > 0) result.append('\n')
        result.append(if (fenced) line else normalizeLatexLine(line))
    }
    return result.toString()
}

private fun normalizeLatexLine(line: String): String {
    val result = StringBuilder(line.length + 8)
    var index = 0
    var inlineCodeTicks = 0
    while (index < line.length) {
        val char = line[index]
        if (char == '`') {
            var count = 1
            while (index + count < line.length && line[index + count] == '`') count += 1
            if (inlineCodeTicks == 0) inlineCodeTicks = count
            else if (inlineCodeTicks == count) inlineCodeTicks = 0
            result.append(line, index, index + count)
            index += count
            continue
        }
        if (inlineCodeTicks == 0 && char == '\\' && index + 1 < line.length) {
            when (line[index + 1]) {
                '[', ']', '(', ')' -> {
                    result.append("$$")
                    index += 2
                    continue
                }
            }
        }
        if (inlineCodeTicks == 0 && char == '$') {
            if (index + 1 < line.length && line[index + 1] == '$') {
                result.append("$$")
                index += 2
                continue
            }
            val closing = findSingleDollarClose(line, index + 1)
            if (closing > index + 1) {
                result.append("$$")
                result.append(line, index + 1, closing)
                result.append("$$")
                index = closing + 1
                continue
            }
        }
        result.append(char)
        index += 1
    }
    return result.toString()
}

private fun findSingleDollarClose(line: String, start: Int): Int {
    var index = start
    while (index < line.length) {
        if (line[index] == '\\') {
            index += 2
            continue
        }
        if (line[index] == '$') {
            if (index + 1 < line.length && line[index + 1] == '$') return -1
            val value = line.substring(start, index).trim()
            return index.takeIf {
                value.isNotEmpty() && value.any { char -> char.isLetter() || char == '\\' }
            } ?: -1
        }
        index += 1
    }
    return -1
}

private fun lineHasDoubleDollarOutsideCode(line: String): Boolean {
    var index = 0
    var inlineCodeTicks = 0
    while (index < line.length) {
        if (line[index] == '`') {
            var count = 1
            while (index + count < line.length && line[index + count] == '`') count += 1
            if (inlineCodeTicks == 0) inlineCodeTicks = count
            else if (inlineCodeTicks == count) inlineCodeTicks = 0
            index += count
            continue
        }
        if (inlineCodeTicks == 0 && line.startsWith("$$", index)) return true
        index += 1
    }
    return false
}

@Composable
internal fun LatexMarkdownTranscript(
    content: String,
    textColor: Color,
    baseStyle: TextStyle,
    modifier: Modifier = Modifier,
    onOpenLink: (String) -> Unit = {},
    layoutKey: String? = null,
    onTextLayout: (String, ConversationTextLayout) -> Unit = { _, _ -> },
    preserveLineBreaks: Boolean = false,
) {
    val context = LocalContext.current
    val density = LocalDensity.current
    val textSizePx = with(density) {
        if (baseStyle.fontSize.isSpecified) baseStyle.fontSize.toPx() else 16.sp.toPx()
    }
    val markdown = remember(context.applicationContext, textSizePx, preserveLineBreaks) {
        latexRenderer(context.applicationContext, textSizePx, preserveLineBreaks)
    }
    val renderedContent = remember(content) { normalizeLatexMarkdown(content) }
    val color = textColor.toArgb()
    val currentLayoutKey = rememberUpdatedState(layoutKey)
    val currentTextSizePx = rememberUpdatedState(textSizePx)
    val currentOnTextLayout = rememberUpdatedState(onTextLayout)
    val currentOnOpenLink = rememberUpdatedState(onOpenLink)
    val openLink = remember {
        { uri: String -> currentOnOpenLink.value(uri) }
    }
    val renderSignature = remember(renderedContent, textSizePx, color, preserveLineBreaks) {
        LatexMarkdownRenderSignature(
            content = renderedContent,
            textSizePx = textSizePx,
            textColor = color,
            preserveLineBreaks = preserveLineBreaks,
        )
    }

    AndroidView(
        modifier = modifier,
        factory = { context ->
            LatexMarkdownTextView(context).apply {
                includeFontPadding = false
                setTextIsSelectable(true)
                linksClickable = true
                movementMethod = LinkMovementMethod.getInstance()
                addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
                    publishConversationTextLayout(
                        textView = view as TextView,
                        layoutKey = currentLayoutKey.value,
                        textSizePx = currentTextSizePx.value,
                        onTextLayout = currentOnTextLayout.value,
                    )
                }
            }
        },
        update = { textView ->
            val needsRender = latexMarkdownNeedsRender(
                previous = textView.renderSignature,
                next = renderSignature,
            )
            if (needsRender) {
                textView.setTextColor(color)
                textView.setTextSize(TypedValue.COMPLEX_UNIT_PX, textSizePx)
                markdown.setMarkdown(textView, renderedContent)
                installWorkspaceLinkHandlers(textView, openLink)
                textView.renderSignature = renderSignature
            }

            val layoutSignature = LatexLayoutPublishSignature(
                layoutKey = currentLayoutKey.value,
                textSizePx = textSizePx,
            )
            if (needsRender || textView.layoutPublishSignature != layoutSignature) {
                textView.layoutPublishSignature = layoutSignature
                textView.post {
                    publishConversationTextLayout(
                        textView = textView,
                        layoutKey = currentLayoutKey.value,
                        textSizePx = currentTextSizePx.value,
                        onTextLayout = currentOnTextLayout.value,
                    )
                }
            }
        },
    )
}

private fun publishConversationTextLayout(
    textView: TextView,
    layoutKey: String?,
    textSizePx: Float,
    onTextLayout: (String, ConversationTextLayout) -> Unit,
) {
    val key = layoutKey ?: return
    val layout = textView.layout ?: return
    if (layout.lineCount <= 0) return
    onTextLayout(
        key,
        ConversationTextLayout(
            textSizePx = textSizePx,
            lineStarts = IntArray(layout.lineCount, layout::getLineStart),
            lineTops = IntArray(layout.lineCount, layout::getLineTop),
            lineBottoms = IntArray(layout.lineCount, layout::getLineBottom),
        ),
    )
}

private fun installWorkspaceLinkHandlers(
    textView: TextView,
    onOpenLink: (String) -> Unit,
) {
    val text = SpannableString(textView.text)
    val links = text.getSpans(0, text.length, URLSpan::class.java)
    links.forEach { link ->
        val start = text.getSpanStart(link)
        val end = text.getSpanEnd(link)
        val flags = text.getSpanFlags(link)
        text.removeSpan(link)
        text.setSpan(
            object : ClickableSpan() {
                override fun onClick(widget: View) = onOpenLink(link.url)

                override fun updateDrawState(drawState: TextPaint) {
                    link.updateDrawState(drawState)
                }
            },
            start,
            end,
            flags,
        )
    }
    if (links.isNotEmpty()) {
        textView.setText(text, TextView.BufferType.SPANNABLE)
        textView.movementMethod = LinkMovementMethod.getInstance()
    }
}

private fun latexRenderer(context: Context, textSizePx: Float, preserveLineBreaks: Boolean): Markwon {
    val sizeKey = (textSizePx * 10f).toInt() to preserveLineBreaks
    return latexRenderers.getOrPut(sizeKey) {
        Markwon.builder(context)
            .apply {
                if (preserveLineBreaks) usePlugin(SoftBreakAddsNewLinePlugin.create())
            }
            .usePlugin(MarkwonInlineParserPlugin.create())
            // Responses that contain any formula use Markwon for the whole
            // message. Keep GFM tables enabled on that path as well so a formula
            // elsewhere in the answer cannot degrade tables into literal pipes.
            .usePlugin(TablePlugin.create(context))
            .usePlugin(
                JLatexMathPlugin.create(textSizePx) { builder ->
                    builder.inlinesEnabled(true)
                    builder.blocksEnabled(true)
                }
            )
            .build()
    }
}
