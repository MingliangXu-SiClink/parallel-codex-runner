package com.parallelcodexrunner.remote.ui

internal fun agentDisplayName(
    agent: AgentSummary,
    agents: List<AgentSummary>,
    language: AppLanguage,
    compact: Boolean = false,
): String {
    val synthesis = agent.role.equals("synthesis", ignoreCase = true)
    val group = agents
        .filter { it.role.equals("synthesis", ignoreCase = true) == synthesis }
        .sortedBy(AgentSummary::number)
    val position = group.indexOfFirst { it.number == agent.number }
        .takeIf { it >= 0 }
        ?.plus(1)
        ?: 1
    if (compact) {
        return if (synthesis) language.text("综$position", "S$position")
        else position.toString()
    }
    return if (synthesis) language.text("综合 $position", "Synthesis $position")
    else "Agent $position"
}

internal fun agentDisplayName(
    agentNumber: Int,
    agents: List<AgentSummary>,
    language: AppLanguage,
): String = agents.firstOrNull { it.number == agentNumber }
    ?.let { agentDisplayName(it, agents, language) }
    ?: "Agent $agentNumber"
