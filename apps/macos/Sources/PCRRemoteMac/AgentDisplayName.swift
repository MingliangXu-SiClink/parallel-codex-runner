import Foundation

func conversationAgentDisplayName(
    agentID: Int,
    agents: [RunAgentSummary],
    language: AppLanguage
) -> String {
    guard let agent = agents.first(where: { $0.agent == agentID }) else {
        return language.text("Agent \(agentID)", "Agent \(agentID)")
    }
    let group = agents.filter { $0.isSynthesis == agent.isSynthesis }
    let position = group.firstIndex(where: { $0.agent == agentID }).map { $0 + 1 } ?? 1
    return agent.isSynthesis
        ? language.text("综合 \(position)", "Synthesis \(position)")
        : language.text("Agent \(position)", "Agent \(position)")
}

extension RunSummary {
    func displayName(forAgent agentID: Int, language: AppLanguage) -> String {
        conversationAgentDisplayName(
            agentID: agentID,
            agents: agents,
            language: language
        )
    }
}
