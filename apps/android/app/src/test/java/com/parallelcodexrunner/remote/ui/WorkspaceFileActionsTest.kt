package com.parallelcodexrunner.remote.ui

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WorkspaceFileActionsTest {
    @Test
    fun localWorkspaceReferencesAreNotSentToAnExternalApp() {
        listOf(
            "/Users/example/workspace/report.md",
            "file:///Users/example/workspace/report.md",
            "docs/report.docx",
            "../shared/report.pdf",
            "%2FUsers%2Fexample%2Fworkspace%2Freport.md",
        ).forEach { reference ->
            assertEquals(
                reference,
                TranscriptLinkKind.WORKSPACE_FILE,
                transcriptLinkKind(reference),
            )
        }
    }

    @Test
    fun webAndContactLinksRemainExternal() {
        listOf(
            "https://openai.com",
            "http://example.com",
            "realman-robotics.cn/cn/products/rm75.html",
            "github.com/openai/codex",
            "mailto:person@example.com",
            "tel:10086",
        ).forEach { reference ->
            assertEquals(
                reference,
                TranscriptLinkKind.EXTERNAL,
                transcriptLinkKind(reference),
            )
        }
        assertEquals(
            "https://github.com/openai/codex",
            externalTranscriptUri("github.com/openai/codex"),
        )
    }

    @Test
    fun normalWorkspaceFilenamesAreNotMistakenForBareWebAddresses() {
        listOf(
            "README.md", "analysis.py", "report.pdf", "config.toml", "preview.png", "figure.JPG",
            "image.webp", "report.docx", "slides.pptx", "data.xlsx", "audio.wav", "video.mov", "archive.zip",
        ).forEach { reference ->
            assertEquals(
                reference,
                TranscriptLinkKind.WORKSPACE_FILE,
                transcriptLinkKind(reference),
            )
            assertNull(externalTranscriptUri(reference))
        }
    }

    @Test
    fun unsafeOrContextOnlySchemesAreRejected() {
        listOf("", "#heading", "javascript:alert(1)", "data:text/plain,secret", "pcr://pair")
            .forEach { reference ->
                assertEquals(
                    reference,
                    TranscriptLinkKind.UNSUPPORTED,
                    transcriptLinkKind(reference),
                )
            }
    }

    @Test
    fun systemBackReturnsToTheParentMarkdownBeforeClosingTheReader() {
        val parent = WorkspaceDocument(
            name = "README.md",
            content = "parent",
            source = File("README.md"),
            relativePath = "README.md",
        )
        val child = WorkspaceDocument(
            name = "details.md",
            content = "child",
            source = File("details.md"),
            relativePath = "docs/details.md",
        )

        val parentState = MainUiState(
            workspaceDocument = child,
            workspaceDocumentHistory = listOf(parent),
        ).popWorkspaceDocument()
        assertEquals(parent, parentState.workspaceDocument)
        assertEquals(emptyList<WorkspaceDocument>(), parentState.workspaceDocumentHistory)

        val closedState = parentState.popWorkspaceDocument()
        assertNull(closedState.workspaceDocument)
        assertEquals(emptyList<WorkspaceDocument>(), closedState.workspaceDocumentHistory)
    }
}
