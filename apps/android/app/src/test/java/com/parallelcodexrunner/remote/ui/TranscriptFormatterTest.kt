package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.ResumeHistoryEntry
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TranscriptFormatterTest {
    @Test
    fun formatsPromptReasoningCommandAndAnswerInOrder() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 1)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"type":"command_execution","command":"/bin/zsh -lc 'rg TODO'","exit_code":0,"aggregated_output":"ignored"}}""",
                    )
                }
            )
            add(
                buildJsonObject {
                    put("event_id", 2)
                    put("type", "agent_line")
                    put("text", """{"type":"agent_reasoning","text":"Inspecting files"}""")
                }
            )
            add(
                buildJsonObject {
                    put("event_id", 3)
                    put("type", "agent_line")
                    put("text", """{"type":"agent_message","message":"Done"}""")
                }
            )
        }

        val transcript = MobileAgentTranscript(
            blocks = MobileTranscriptFormatter.appendEvents(emptyList(), events),
            finalMessage = "Done",
        )
        val blocks = MobileTranscriptFormatter.visibleBlocks("Fix the project", transcript)

        assertEquals(
            listOf(
                MobileTranscriptKind.USER,
                MobileTranscriptKind.COMMAND,
                MobileTranscriptKind.THOUGHT,
                MobileTranscriptKind.OUTPUT,
            ),
            blocks.map(MobileTranscriptBlock::kind),
        )
        assertEquals("rg TODO", blocks[1].text)
        assertEquals("已完成", blocks[1].status)
        assertFalse(blocks.any { "ignored" in it.text })
        assertEquals(1, blocks.count { it.kind == MobileTranscriptKind.OUTPUT })
    }

    @Test
    fun keepsAdjacentReasoningAsSeparateStepsWithoutDuplicatingEvents() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 9)
                    put("type", "agent_line")
                    put("text", "agent_reasoning: first")
                }
            )
            add(
                buildJsonObject {
                    put("event_id", 10)
                    put("type", "agent_line")
                    put("text", "agent_reasoning: second")
                }
            )
        }

        val first = MobileTranscriptFormatter.appendEvents(emptyList(), events)
        val second = MobileTranscriptFormatter.appendEvents(first, events)
        val visible = MobileTranscriptFormatter.visibleBlocks(
            "",
            MobileAgentTranscript(blocks = second),
        )

        assertEquals(2, second.size)
        assertEquals(2, visible.size)
        assertEquals(listOf("first", "second"), visible.map { it.text })
    }

    @Test
    fun replacesRunningCommandWithItsCompletion() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 11)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.started","item":{"id":"item_2","type":"command_execution","command":"pwd","status":"in_progress"}}""",
                    )
                }
            )
            add(
                buildJsonObject {
                    put("event_id", 12)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"pwd","exit_code":0,"status":"completed"}}""",
                    )
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendEvents(emptyList(), events)

        assertEquals(1, blocks.size)
        assertEquals("pwd", blocks.single().text)
        assertEquals("已完成", blocks.single().status)
    }

    @Test
    fun keepsACompletedCommandWhenAnItemIdIsReused() {
        val firstTurn = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 21)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"id":"item_0","type":"command_execution","command":"pwd","exit_code":0}}""",
                    )
                }
            )
        }
        val secondTurn = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 22)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.started","item":{"id":"item_0","type":"command_execution","command":"git status","status":"in_progress"}}""",
                    )
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendEvents(
            MobileTranscriptFormatter.appendEvents(emptyList(), firstTurn),
            secondTurn,
        )

        assertEquals(listOf("pwd", "git status"), blocks.map { it.text })
    }

    @Test
    fun removesDisplayOnlyBoldMarkersFromReasoning() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 31)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"id":"item_1","type":"reasoning","text":"**Inspecting files**\n**Planning the fix**"}}""",
                    )
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendEvents(emptyList(), events)

        assertEquals("Inspecting files\nPlanning the fix", blocks.single().text)
    }

    @Test
    fun rendersResumeModelMismatchAsWarningInsteadOfError() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 32)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"id":"item_0","type":"error","message":"This session was recorded with model `luna` but is resuming with `sol`. Consider switching back to `luna` as it may affect Codex performance."}}""",
                    )
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendEvents(emptyList(), events)

        assertEquals(MobileTranscriptKind.WARNING, blocks.single().kind)
    }

    @Test
    fun rendersTransportFallbackAsWarningInsteadOfError() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 33)
                    put("type", "agent_line")
                    put(
                        "text",
                        """{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Falling back from WebSockets to HTTPS transport. stream disconnected before completion"}}""",
                    )
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendEvents(emptyList(), events)

        assertEquals(MobileTranscriptKind.WARNING, blocks.single().kind)
    }

    @Test
    fun formatsPlainRuntimeLogsInsteadOfShowingRawLoggerNoise() {
        val events = buildJsonArray {
            add(
                buildJsonObject {
                    put("event_id", 34)
                    put("type", "agent_line")
                    put(
                        "text",
                        "2026-09-02T04:54:41.381240Z ERROR " +
                            "codex_core::tools::router: error=exec_command failed: " +
                            "CreateProcess { message: No such file or directory (os error 2) }",
                    )
                }
            )
        }

        val block = MobileTranscriptFormatter.appendEvents(emptyList(), events).single()

        assertEquals(MobileTranscriptKind.WARNING, block.kind)
        assertEquals("missing_execution_path", block.activityType)
        assertFalse("codex_core" in block.text)
        assertFalse("2026-09-02" in block.text)
    }

    @Test
    fun consumesCanonicalTranscriptItemsWithoutReparsingCodexJson() {
        val items = buildJsonArray {
            add(
                buildJsonObject {
                    put("id", "event-41")
                    put("event_id", 41)
                    put("kind", "command")
                    put("text", "rg TODO")
                    put("status", "running")
                    put("operation_id", "item_7")
                }
            )
            add(
                buildJsonObject {
                    put("id", "event-42")
                    put("event_id", 42)
                    put("kind", "command")
                    put("text", "rg TODO")
                    put("status", "completed")
                    put("operation_id", "item_7")
                }
            )
            add(
                buildJsonObject {
                    put("id", "event-43")
                    put("event_id", 43)
                    put("kind", "output")
                    put("text", "检查完成。")
                }
            )
            add(
                buildJsonObject {
                    put("id", "event-44")
                    put("event_id", 44)
                    put("kind", "warning")
                    put("text", "Model mismatch")
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendItems(emptyList(), items)

        assertEquals(3, blocks.size)
        assertEquals("已完成", blocks[0].status)
        assertEquals("检查完成。", blocks[1].text)
        assertEquals(MobileTranscriptKind.WARNING, blocks[2].kind)
    }

    @Test
    fun mergesLiveToolActivityAndKeepsItsPresentationType() {
        val items = buildJsonArray {
            add(
                buildJsonObject {
                    put("id", "event-51")
                    put("event_id", 51)
                    put("kind", "activity")
                    put("text", "Preparing search")
                    put("status", "running")
                    put("operation_id", "search-1")
                    put("activity_type", "web_search")
                }
            )
            add(
                buildJsonObject {
                    put("id", "event-52")
                    put("event_id", 52)
                    put("kind", "activity")
                    put("text", "first query\nsecond query")
                    put("status", "completed")
                    put("operation_id", "search-1")
                    put("activity_type", "web_search")
                }
            )
        }

        val blocks = MobileTranscriptFormatter.appendItems(emptyList(), items)

        assertEquals(1, blocks.size)
        assertEquals("first query\nsecond query", blocks.single().text)
        assertEquals("已完成", blocks.single().status)
        assertEquals("web_search", blocks.single().activityType)
        assertEquals("event-51", blocks.single().id)
    }

    @Test
    fun keepsResumedConversationBeforeTheCurrentTurn() {
        val history = MobileTranscriptFormatter.historyBlocks(
            listOf(
                ResumeHistoryEntry("user", "先检查测试"),
                ResumeHistoryEntry("assistant", "发现一个失败用例"),
            )
        )

        val blocks = MobileTranscriptFormatter.visibleBlocks(
            history = history,
            prompt = "继续修复",
            transcript = MobileAgentTranscript(
                blocks = listOf(
                    MobileTranscriptBlock(
                        id = "event-1",
                        kind = MobileTranscriptKind.OUTPUT,
                        text = "已经修复",
                    )
                )
            ),
        )

        assertEquals(
            listOf(
                "先检查测试",
                "发现一个失败用例",
                "继续修复",
                "已经修复",
            ),
            blocks.map { it.text },
        )
        assertTrue(blocks[0].isHistorical)
        assertTrue(blocks[1].isHistorical)
        assertFalse(blocks[2].isHistorical)
    }

    @Test
    fun showsRuntimeInterruptionAsStatusWithoutLosingTheUserPrompt() {
        val notice = "<turn_aborted>\nThe user interrupted the previous turn on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.\n</turn_aborted>"
        val blocks = MobileTranscriptFormatter.historyBlocks(listOf(
            ResumeHistoryEntry("user", notice, id = "cancel-one"),
            ResumeHistoryEntry("user", "Keep my prompt\n$notice", id = "prompt-two"),
        ))
        assertEquals(listOf("Response interrupted", "Keep my prompt", "Response interrupted"), blocks.map { it.text })
        assertEquals(listOf("cancel-one:interrupted", "prompt-two", "prompt-two:interrupted"), blocks.map { it.id })
        assertEquals(MobileTranscriptKind.ACTIVITY, blocks.first().kind)
        assertEquals(MobileTranscriptKind.USER, blocks[1].kind)
        assertEquals(blocks, MobileConversationPresentation.visibleBlocks(blocks, "run", 1, false))
        listOf(
            "```xml\n$notice\n```", "```xml\n$notice",
            "<turn_aborted>my own markup</turn_aborted>", "Explain this inline: $notice",
        ).forEach { literal ->
            assertEquals(literal, MobileTranscriptFormatter.historyBlocks(
                listOf(ResumeHistoryEntry("user", literal)),
            ).single().text)
        }
        assertEquals(notice, MobileTranscriptFormatter.historyBlocks(
            listOf(ResumeHistoryEntry("assistant", notice)),
        ).single().text)
    }

    @Test
    fun hidesAssistantMetadataAndRendersFileCitationsAsLinks() {
        val answer = """
            Created :codex-file-citation{path="/Users/test/Project Name/result [final].docx" purpose="output"} successfully.

            <oai-mem-citation>
            <citation_entries>
            MEMORY.md:1-2|note=[internal]
            </citation_entries>
            </oai-mem-citation>
        """.trimIndent()

        val rendered = MobileTranscriptFormatter.assistantPresentationText(answer)

        assertEquals(
            "Created [result \\[final\\].docx]" +
                "(<file:///Users/test/Project%20Name/result%20%5Bfinal%5D.docx>) successfully.",
            rendered,
        )
    }

    @Test
    fun deduplicatesLegacyCitationEnrichedAssistantMirror() {
        val answer = """
            Useful answer.

            Useful answer.

            <oai-mem-citation>
            <citation_entries>
            MEMORY.md:1-2|note=[internal]
            </citation_entries>
            </oai-mem-citation>
        """.trimIndent()

        assertEquals(
            "Useful answer.",
            MobileTranscriptFormatter.assistantPresentationText(answer),
        )
    }

    @Test
    fun deduplicatesLegacyAssistantMirrorWithoutMetadata() {
        val answer = "Useful answer.\nUseful answer."

        assertEquals(
            "Useful answer.",
            MobileTranscriptFormatter.assistantPresentationText(answer),
        )
    }

    @Test
    fun repeatedPromptAndAnswerRemainVisibleAsANewTurn() {
        val history = listOf(
            MobileTranscriptBlock("history-0", MobileTranscriptKind.USER, "继续"),
            MobileTranscriptBlock("history-1", MobileTranscriptKind.OUTPUT, "已经完成"),
        )

        val blocks = MobileTranscriptFormatter.visibleBlocks(
            history = history,
            prompt = "继续",
            transcript = MobileAgentTranscript(finalMessage = "已经完成"),
        )

        assertEquals(
            listOf("继续", "已经完成", "继续", "已经完成"),
            blocks.map(MobileTranscriptBlock::text),
        )
    }

    @Test
    fun canonicalFinalLinkReplacesTemporaryAgentLinkWithoutDuplicatingAnswer() {
        val streamed = """
            已按实际方案重绘。

            [查看高清 PNG](</Users/test/.codex_parallel_runs/run/workspaces/agent_001/assets/plan.png>)
        """.trimIndent()
        val final = """
            已按实际方案重绘。

            [查看高清 PNG](</Users/test/Desktop/project/assets/plan.png>)
        """.trimIndent()

        val blocks = MobileTranscriptFormatter.visibleBlocks(
            prompt = "修改图片",
            transcript = MobileAgentTranscript(
                blocks = listOf(
                    MobileTranscriptBlock("event-8", MobileTranscriptKind.OUTPUT, streamed)
                ),
                finalMessage = final,
            ),
        )
        val outputs = blocks.filter { it.kind == MobileTranscriptKind.OUTPUT }

        assertEquals(
            listOf("修改图片"),
            blocks.filter { it.kind == MobileTranscriptKind.USER }.map(MobileTranscriptBlock::text),
        )
        assertEquals(1, outputs.size)
        assertEquals("event-8", outputs.single().id)
        assertEquals(final, outputs.single().text)
        assertTrue(outputs.single().isFinalAnswer)
    }

    @Test
    fun assignsUniqueKeysWhenHistoryAndLiveEventsReuseAnId() {
        val blocks = MobileTranscriptFormatter.visibleBlocks(
            history = listOf(
                MobileTranscriptBlock("event-1", MobileTranscriptKind.USER, "Earlier prompt")
            ),
            prompt = "",
            transcript = MobileAgentTranscript(
                blocks = listOf(
                    MobileTranscriptBlock("event-1", MobileTranscriptKind.COMMAND, "pwd")
                )
            ),
        )

        assertEquals(blocks.size, blocks.map(MobileTranscriptBlock::id).distinct().size)
    }
}
