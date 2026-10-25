import AppKit
import SwiftUI

/// A turn owns several alternative Agent branches. This control is embedded
/// directly below that turn's prompt so branch selection cannot be mistaken
/// for a conversation-wide or workspace-wide setting.
struct ConversationAgentSwitchBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let language: AppLanguage
    let textSize: AppTextSize
    let accentColor: Color
    let state: ConversationAgentSwitchState
    let resultsInspectorVisible: Bool
    let accessibilityHidden: Bool
    let selectAgent: (Int) -> Void
    let toggleResults: () -> Void

    static func minimumHeight(for textSize: AppTextSize) -> CGFloat {
        60 * textSize.scale
    }

    static func stickyHeight(for textSize: AppTextSize) -> CGFloat {
        ceil(minimumHeight(for: textSize) + 16)
    }

    private var primaryAgents: [RunAgentSummary] {
        state.agents.filter { !$0.isSynthesis }.sorted { $0.agent < $1.agent }
    }

    private var synthesisAgents: [RunAgentSummary] {
        state.agents.filter(\.isSynthesis).sorted { $0.agent < $1.agent }
    }

    private var orderedAgents: [RunAgentSummary] {
        primaryAgents + synthesisAgents
    }

    private var selectedAgentIndex: Int? {
        guard let selected = state.highlightedAgent else { return nil }
        return orderedAgents.firstIndex { $0.agent == selected }
    }

    private func revealRequestID(viewportWidth: CGFloat) -> String {
        let agents = orderedAgents.map { String($0.agent) }.joined(separator: ",")
        return "\(state.runID)|\(state.highlightedAgent ?? -1)|\(textSize.rawValue)|\(Int(viewportWidth.rounded()))|\(agents)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4 * textSize.scale) {
            agentTabs
            AgentActivityObservation(agent: state.agents.first { $0.agent == state.selectedAgent },
                language: language, textSize: textSize, live: state.executionConfirmed)
        }
        .padding(.horizontal, textSize.scale)
        .frame(
            maxWidth: .infinity,
            minHeight: Self.minimumHeight(for: textSize),
            alignment: .leading
        )
        .buttonStyle(.plain)
        .environment(\.dynamicTypeSize, textSize.dynamicTypeSize)
        .tint(accentColor)
        .accessibilityHidden(accessibilityHidden)
    }

    private var agentTabs: some View {
        HStack(spacing: 8 * textSize.scale) {
            GeometryReader { geometry in
                scrollableAgentTabs(viewportWidth: geometry.size.width)
                    .frame(
                        width: geometry.size.width,
                        height: geometry.size.height,
                        alignment: .leading
                    )
            }
            .frame(
                maxWidth: .infinity,
                minHeight: 36 * textSize.scale,
                maxHeight: 36 * textSize.scale
            )
            .layoutPriority(1)

        }
    }

    private func scrollableAgentTabs(viewportWidth: CGFloat) -> some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 2) {
                    ForEach(orderedAgents) { agent in
                        agentTab(agent)
                            .frame(width: tabWidth(agent))
                            .id(scrollTargetID(for: agent.agent))
                    }
                }
                .padding(3 * textSize.scale)
                .background(
                    ParallelCodexVisualStyle.subtleSurface,
                    in: RoundedRectangle(
                        cornerRadius: ParallelCodexVisualStyle.Radius.selection,
                        style: .continuous
                    )
                )
                .padding(.trailing, trailingRevealSpace(viewportWidth: viewportWidth))
            }
            .id("agent-tabs-scroll-\(state.runID)-\(textSize.rawValue)")
            .onAppear {
                revealSelectedAgent(
                    using: proxy,
                    viewportWidth: viewportWidth,
                    animated: false
                )
            }
            .onChange(of: state.highlightedAgent) { _ in
                revealSelectedAgent(
                    using: proxy,
                    viewportWidth: viewportWidth,
                    animated: true
                )
            }
            .onChange(of: viewportWidth) { _ in
                // Window resizing changes how many candidates fit without
                // changing the selection. Keep the selected branch visible
                // throughout the live resize instead of waiting for another
                // selection or text-size update.
                revealSelectedAgent(
                    using: proxy,
                    viewportWidth: viewportWidth,
                    animated: false
                )
            }
            .task(id: revealRequestID(viewportWidth: viewportWidth)) {
                // Dynamic Type and a changing candidate list both update tab
                // widths. Let that layout finish, then align to a whole tab so
                // a clipped digit never appears at the leading edge.
                try? await Task.sleep(for: .milliseconds(80))
                guard !Task.isCancelled else { return }
                revealSelectedAgent(
                    using: proxy,
                    viewportWidth: viewportWidth,
                    animated: false
                )
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(language.text("Agent 候选", "Agent candidates"))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func trailingRevealSpace(viewportWidth: CGFloat) -> CGFloat {
        guard let start = leadingAgentIndex(viewportWidth: viewportWidth) else {
            return 8 * textSize.scale
        }
        let suffixWidth = tabRangeWidth(start..<orderedAgents.endIndex)
        let usableWidth = max(0, viewportWidth - (6 * textSize.scale))
        return max(8 * textSize.scale, usableWidth - suffixWidth)
    }

    private func scrollTargetID(for agent: Int) -> String {
        "\(textSize.rawValue):\(agent)"
    }

    private func revealSelectedAgent(
        using proxy: ScrollViewProxy,
        viewportWidth: CGFloat,
        animated: Bool
    ) {
        guard let start = leadingAgentIndex(viewportWidth: viewportWidth) else { return }
        let target = scrollTargetID(for: orderedAgents[start].agent)
        DispatchQueue.main.async {
            if animated {
                withAnimation(
                    reduceMotion
                        ? nil
                        : .easeOut(duration: ParallelCodexVisualStyle.Motion.quick)
                ) {
                    proxy.scrollTo(target, anchor: .leading)
                }
            } else {
                proxy.scrollTo(target, anchor: .leading)
            }
        }
    }

    private func leadingAgentIndex(viewportWidth: CGFloat) -> Int? {
        guard let selected = selectedAgentIndex else { return nil }
        let usableWidth = max(1, viewportWidth - (6 * textSize.scale))
        var start = selected
        var width = tabWidth(orderedAgents[selected])
        while start > orderedAgents.startIndex {
            let previous = orderedAgents.index(before: start)
            let candidate = width + 2 + tabWidth(orderedAgents[previous])
            guard candidate <= usableWidth else { break }
            start = previous
            width = candidate
        }
        return start
    }

    private func tabRangeWidth(_ range: Range<Int>) -> CGFloat {
        guard !range.isEmpty else { return 0 }
        let tabs = range.reduce(CGFloat.zero) { partial, index in
            partial + tabWidth(orderedAgents[index])
        }
        return tabs + CGFloat(max(0, range.count - 1)) * 2
    }

    private func tabWidth(_ agent: RunAgentSummary) -> CGFloat {
        let selected = agent.agent == state.selectedAgent
        let font = NSFont.systemFont(
            ofSize: ParallelCodexVisualStyle.Typography.caption * textSize.scale,
            weight: selected ? .medium : .regular
        )
        let labelWidth = ceil(
            (displayName(agent) as NSString).size(withAttributes: [.font: font]).width
        )
        let fixedWidth = (39 * textSize.scale) + 15
        return fixedWidth + labelWidth + statusLabelWidth
    }

    private var statusLabelWidth: CGFloat {
        let font = NSFont.systemFont(ofSize: 11 * textSize.scale, weight: .regular)
        // Reserve the longest phase label so state changes do not move neighboring tabs.
        return [language.text("等待模型", "Waiting for model"), language.text("等待下一步", "Awaiting activity"),
                language.text("定时等待", "Waiting"), language.text("不可用", "Unavailable")]
            .map { ceil(($0 as NSString).size(withAttributes: [.font: font]).width) }
            .max() ?? 0
    }

    private func agentTab(_ agent: RunAgentSummary) -> some View {
        let selected = agent.agent == state.highlightedAgent
        let pending = agent.agent == state.pendingAgent
        return Button {
            guard state.selectionEnabled, !pending else { return }
            if !selected {
                selectAgent(agent.agent)
            }
        } label: {
            HStack(spacing: 5) {
                ResultStatusGlyph(status: glyphStatus(agent),
                                  animatesExecution: state.executionConfirmed && agent.hasObservedActiveExecution)
                .frame(width: 12 * textSize.scale, height: 12 * textSize.scale)
                .accessibilityHidden(true)
                Text(displayName(agent))
                    .appScaledSystemFont(
                        size: ParallelCodexVisualStyle.Typography.caption,
                        weight: selected ? .medium : .regular
                    )
                    .lineLimit(1)
                Text(navigatorStatus(agent))
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .frame(width: statusLabelWidth, alignment: .leading)
                Image(systemName: agentMarkerSymbol(agent) ?? "checkmark")
                    .appScaledSystemFont(size: 9, weight: .semibold)
                    .foregroundStyle(agentMarkerColor(agent))
                    .opacity(agentMarkerSymbol(agent) == nil ? 0 : 1)
                    .frame(width: 11 * textSize.scale)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 8 * textSize.scale)
            .frame(height: 30 * textSize.scale)
            .foregroundStyle(
                selected ? Color.primary : (pending ? accentColor : Color.secondary)
            )
            .background(
                selected
                    ? ParallelCodexVisualStyle.surface
                    : (pending ? accentColor.opacity(0.10) : Color.clear),
                in: RoundedRectangle(cornerRadius: ParallelCodexVisualStyle.Radius.selection)
            )
            .shadow(color: selected ? .black.opacity(0.06) : .clear, radius: 2, y: 1)
            .contentShape(Rectangle())
        }
        .allowsHitTesting(state.selectionEnabled && !pending)
        .help(agentHelp(agent))
        .accessibilityLabel(agentAccessibilityLabel(agent))
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func branchPosition(_ agent: RunAgentSummary) -> String {
        let group = agent.isSynthesis ? synthesisAgents : primaryAgents
        let index = group.firstIndex { $0.agent == agent.agent }.map { $0 + 1 } ?? 1
        let label = agent.isSynthesis
            ? language.text("综合", "Synthesis")
            : language.text("候选", "Candidate")
        return "\(label) \(index)/\(max(1, group.count))"
    }

    private func displayName(_ agent: RunAgentSummary) -> String {
        let group = agent.isSynthesis ? synthesisAgents : primaryAgents
        let index = group.firstIndex { $0.agent == agent.agent }.map { $0 + 1 } ?? 1
        return agent.isSynthesis
            ? language.text("综合 \(index)", "Synthesis \(index)")
            : language.text("Agent \(index)", "Agent \(index)")
    }

    private func agentMarkerSymbol(_ agent: RunAgentSummary) -> String? {
        if (agent.pendingQuestionCount ?? 0) > 0 { return "questionmark.bubble.fill" }
        if state.finalizedAgent == agent.agent { return "checkmark.seal.fill" }
        if state.recommendedAgent == agent.agent || agent.recommended { return "star.fill" }
        if agent.rejected { return "nosign" }
        return nil
    }

    private func agentMarkerColor(_ agent: RunAgentSummary) -> Color {
        if (agent.pendingQuestionCount ?? 0) > 0 { return accentColor }
        if state.finalizedAgent == agent.agent { return .green }
        if state.recommendedAgent == agent.agent || agent.recommended { return .orange }
        return .secondary
    }

    private func glyphStatus(_ agent: RunAgentSummary) -> String {
        if (agent.blockingQuestionCount ?? 0) > 0 { return "waiting_for_output" }
        if state.finalizedAgent == agent.agent || agent.isSuccessful { return "success" }
        if agent.isObservedWaiting || (agent.normalizedStatus == "running" && agent.activity?.state == "awaiting_event") {
            return "waiting_for_output"
        }
        if agent.isRunning { return agent.status }
        return agent.workspaceAvailable == false ? "unavailable" : agent.status
    }

    private func agentHelp(_ agent: RunAgentSummary) -> String {
        var parts = [agent.name, navigatorStatus(agent)]
        if (agent.pendingQuestionCount ?? 0) > 0 { parts.append(language.text("有问题待回答", "Has unanswered questions")) }
        let activity = AgentActivityPresentation.detail(agent, language: language, live: state.executionConfirmed)
        if !activity.isEmpty { parts.append(activity) }
        if agent.isSuccessful,
           agent.workspaceAvailable == false,
           state.finalizedAgent != agent.agent {
            parts.append(language.text("只读", "Read only"))
        }
        if state.recommendedAgent == agent.agent || agent.recommended {
            parts.append(language.text("推荐", "Recommended"))
        }
        if agent.isSynthesis { parts.append(language.text("综合 Agent", "Synthesis")) }
        if let seconds = agent.seconds, agent.isTerminal {
            parts.append(Self.duration(seconds))
        }
        if let reasoning = agent.reasoningDisplay(language: language) {
            parts.append(reasoning)
        }
        return parts.joined(separator: " · ")
    }

    private func agentAccessibilityLabel(_ agent: RunAgentSummary) -> String {
        var parts = [displayName(agent), navigatorStatus(agent)]
        if (agent.pendingQuestionCount ?? 0) > 0 { parts.append(language.text("有问题待回答", "Has unanswered questions")) }
        if agent.isSuccessful,
           agent.workspaceAvailable == false,
           state.finalizedAgent != agent.agent {
            parts.append(language.text("只读", "Read only"))
        }
        if state.recommendedAgent == agent.agent || agent.recommended {
            parts.append(language.text("推荐", "Recommended"))
        }
        if agent.rejected {
            parts.append(language.text("不参与推荐", "Excluded from recommendation"))
        }
        parts.append(branchPosition(agent))
        return parts.joined(separator: ", ")
    }

    private func navigatorStatus(_ agent: RunAgentSummary) -> String {
        if (agent.blockingQuestionCount ?? 0) > 0 { return language.text("待回答", "Your input") }
        if state.finalizedAgent == agent.agent {
            return language.text("已采用", "Used")
        }
        if agent.isSuccessful {
            return language.text("已完成", "Completed")
        }
        return agent.compactStatusLabel(language: language)
    }

    private static func duration(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded()))
        if total >= 3_600 { return "\(total / 3_600)h \((total % 3_600) / 60)m" }
        if total >= 60 { return "\(total / 60)m \(total % 60)s" }
        return "\(total)s"
    }
}

private struct AgentActivityObservation: View {
    let agent: RunAgentSummary?
    let language: AppLanguage
    let textSize: AppTextSize
    let live: Bool
    @State private var now = Date()

    private var deadline: Date? {
        live && agent?.isObservedWaiting == true ? agent?.activity?.waitUntilDate : nil
    }

    var body: some View {
        let detail = AgentActivityPresentation.detail(agent, language: language, live: live, now: now)
        Text(detail)
            .appScaledSystemFont(size: 11)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: .infinity, minHeight: 18 * textSize.scale,
                   maxHeight: 18 * textSize.scale, alignment: .leading)
            .help(detail)
            .accessibilityIdentifier("agent.activity.observation")
            .task(id: deadline) {
                now = Date()
                guard let until = deadline else { return }
                while now < until {
                    do { try await Task.sleep(for: .seconds(30)) }
                    catch { return }
                    now = Date()
                }
            }
    }
}
