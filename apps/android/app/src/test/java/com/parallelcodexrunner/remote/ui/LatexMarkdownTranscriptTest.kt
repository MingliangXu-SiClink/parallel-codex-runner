package com.parallelcodexrunner.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LatexMarkdownTranscriptTest {
    @Test
    fun normalizesCodexLatexDelimitersForMarkwon() {
        val source = """Inline \(x^2 + y^2\) text.

\[
I(z; a) = \sum_i p_i \log p_i
\]

Also ${'$'}z_t${'$'}."""

        assertEquals(
            """Inline ${'$'}${'$'}x^2 + y^2${'$'}${'$'} text.

${'$'}${'$'}
I(z; a) = \sum_i p_i \log p_i
${'$'}${'$'}

Also ${'$'}${'$'}z_t${'$'}${'$'}.""",
            normalizeLatexMarkdown(source),
        )
        assertTrue(containsLatex(source))
    }

    @Test
    fun leavesCodeAndOrdinaryCurrencyUntouched() {
        val source = """Price is ${'$'}5.

`\(not math\)`

```text
\[not math\]
```"""

        assertEquals(source, normalizeLatexMarkdown(source))
        assertFalse(containsLatex(source))
    }

    @Test
    fun recognizesAlreadyNormalizedLatexButNotCode() {
        assertTrue(containsLatex("Formula ${'$'}${'$'}x^2${'$'}${'$'} here"))
        assertFalse(containsLatex("`${'$'}${'$'}not math${'$'}${'$'}`"))
        assertFalse(
            containsLatex(
                """```text
${'$'}${'$'}not math${'$'}${'$'}
```"""
            )
        )
    }

    @Test
    fun rerendersOnlyWhenVisibleLatexInputsChange() {
        val original = LatexMarkdownRenderSignature(
            content = "${'$'}${'$'}x^2${'$'}${'$'}",
            textSizePx = 32f,
            textColor = 0x112233,
        )

        assertTrue(latexMarkdownNeedsRender(previous = null, next = original))
        assertFalse(latexMarkdownNeedsRender(previous = original, next = original.copy()))
        assertTrue(
            latexMarkdownNeedsRender(
                previous = original,
                next = original.copy(content = "${'$'}${'$'}x^3${'$'}${'$'}"),
            )
        )
        assertTrue(
            latexMarkdownNeedsRender(
                previous = original,
                next = original.copy(textSizePx = 36f),
            )
        )
        assertTrue(
            latexMarkdownNeedsRender(
                previous = original,
                next = original.copy(textColor = 0x334455),
            )
        )
        assertTrue(
            latexMarkdownNeedsRender(
                previous = original,
                next = original.copy(preserveLineBreaks = true),
            )
        )
    }
}
