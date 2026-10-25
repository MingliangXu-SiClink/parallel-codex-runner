package com.parallelcodexrunner.remote.ui

internal data class MobileConversationAgentSwitchState(
    val runId: String,
    val agents: List<AgentSummary>,
    val selectedAgent: Int?,
    val pendingAgentSelection: Int? = null,
    val recommendedAgent: Int?,
    val finalizedAgent: Int?,
    val selectionEnabled: Boolean,
)

internal sealed interface MobileConversationTimelineItem {
    val id: String

    data class Message(val block: MobileTranscriptBlock) : MobileConversationTimelineItem {
        override val id: String = "message:${block.id}"
    }

    data class AgentSwitch(
        val state: MobileConversationAgentSwitchState,
    ) : MobileConversationTimelineItem {
        override val id: String = "agent-switch:${state.runId}"
    }

    fun viewportAnchor(): MobileTranscriptBlock = when (this) {
        is Message -> block
        is AgentSwitch -> MobileTranscriptBlock(
            id = id,
            kind = MobileTranscriptKind.ACTIVITY,
            text = state.runId,
            activityType = "agent_switch",
        )
    }
}

internal object MobileConversationTimelineProjection {
    fun make(
        blocks: List<MobileTranscriptBlock>,
        switchState: MobileConversationAgentSwitchState?,
    ): List<MobileConversationTimelineItem> {
        val items = blocks.mapTo(mutableListOf<MobileConversationTimelineItem>()) {
            MobileConversationTimelineItem.Message(it)
        }
        if (switchState == null || switchState.agents.isEmpty()) return items

        val promptIndex = blocks.indexOfFirst {
            it.id == "current-prompt" || it.id.startsWith("current-prompt-visible-")
        }
        val insertionIndex = when {
            promptIndex >= 0 -> promptIndex + 1
            else -> blocks.indexOfFirst { !it.isHistorical }.takeIf { it >= 0 }
                ?: blocks.size
        }
        items.add(
            insertionIndex,
            MobileConversationTimelineItem.AgentSwitch(switchState),
        )
        return items
    }
}

internal object MobileConversationPresentation {
    fun isAgentWorkBlock(block: MobileTranscriptBlock): Boolean = when (block.kind) {
        MobileTranscriptKind.THOUGHT,
        MobileTranscriptKind.COMMAND,
        MobileTranscriptKind.ACTIVITY,
        -> block.activityType != "agent_switch" && block.activityType != "turn_interrupted"
        else -> false
    }

    fun visibleBlocks(
        blocks: List<MobileTranscriptBlock>,
        runId: String,
        agent: Int,
        workExpanded: Boolean,
        language: AppLanguage = AppLanguage.CHINESE,
        sectionOverrides: Map<String, Boolean> = emptyMap(),
    ): List<MobileTranscriptBlock> {
        val withoutHistoricalWork = blocks.filterNot {
            it.isHistorical && isAgentWorkBlock(it)
        }
        val projected = blocksWithWorkDisclosure(
            blocks = withoutHistoricalWork,
            runId = runId,
            agent = agent,
            expanded = workExpanded,
            language = language,
            sectionOverrides = sectionOverrides,
        )
        return coalesced(projected)
    }

    private fun blocksWithWorkDisclosure(
        blocks: List<MobileTranscriptBlock>,
        runId: String,
        agent: Int,
        expanded: Boolean,
        language: AppLanguage,
        sectionOverrides: Map<String, Boolean>,
    ): List<MobileTranscriptBlock> {
        return buildList {
            var index = 0
            while (index < blocks.size) {
                if (!isAgentWorkBlock(blocks[index])) {
                    add(blocks[index++])
                    continue
                }
                val start = index
                while (index < blocks.size && isAgentWorkBlock(blocks[index])) index++
                val work = blocks.subList(start, index)
                val id = "work-summary-$runId-$agent-${work.first().id}"
                val isExpanded = sectionOverrides[id] ?: expanded
                add(MobileTranscriptBlock(
                    id = id, kind = MobileTranscriptKind.ACTIVITY,
                    text = workSummaryText(work, language),
                    activityType = if (isExpanded) "work_summary_expanded" else "work_summary",
                ))
                if (isExpanded) addAll(work)
            }
        }
    }

    private fun workSummaryText(
        blocks: List<MobileTranscriptBlock>,
        language: AppLanguage,
    ): String {
        val reasoning = blocks.count { it.kind == MobileTranscriptKind.THOUGHT }
        val commands = blocks.count { it.kind == MobileTranscriptKind.COMMAND }
        val activities = blocks.size - reasoning - commands
        return buildList {
            if (reasoning > 0) {
                add(
                    language.text(
                        "$reasoning 条思考",
                        "$reasoning reasoning ${if (reasoning == 1) "step" else "steps"}",
                    )
                )
            }
            if (commands > 0) {
                add(
                    language.text(
                        "$commands 个命令",
                        "$commands ${if (commands == 1) "command" else "commands"}",
                    )
                )
            }
            if (activities > 0) {
                add(
                    language.text(
                        "$activities 项操作",
                        "$activities ${if (activities == 1) "action" else "actions"}",
                    )
                )
            }
        }.joinToString(" · ")
    }

    private fun coalesced(blocks: List<MobileTranscriptBlock>): List<MobileTranscriptBlock> {
        val result = mutableListOf<MobileTranscriptBlock>()
        blocks.forEach { block ->
            val lastIndex = result.lastIndex
            val last = result.getOrNull(lastIndex)
            val equivalentOutput = block.kind == MobileTranscriptKind.OUTPUT &&
                block.status == null &&
                last?.kind == MobileTranscriptKind.OUTPUT &&
                last.status == null &&
                last.isHistorical == block.isHistorical &&
                MobileTranscriptFormatter.equivalentAssistantPresentation(last.text, block.text)
            if (equivalentOutput) {
                result[lastIndex] = last.copy(
                    text = if (block.isFinalAnswer) block.text else last.text,
                    isFinalAnswer = last.isFinalAnswer || block.isFinalAnswer,
                )
                return@forEach
            }
            val mergeThought = block.kind == MobileTranscriptKind.THOUGHT &&
                block.status == null &&
                last?.kind == MobileTranscriptKind.THOUGHT &&
                last.status == null &&
                last.isHistorical == block.isHistorical
            val mergeOutput = block.kind == MobileTranscriptKind.OUTPUT &&
                block.status == null &&
                last?.kind == MobileTranscriptKind.OUTPUT &&
                last.status == null &&
                last.isHistorical == block.isHistorical &&
                ((!block.isFinalAnswer && !last.isFinalAnswer) || block.isHistorical)
            if (mergeThought || mergeOutput) {
                result[lastIndex] = last.copy(
                    text = last.text + "\n\n" + block.text,
                    isFinalAnswer = last.isFinalAnswer || block.isFinalAnswer,
                )
            } else {
                result += block
            }
        }
        return result
    }
}
