import SwiftUI

struct ResultElapsedTime {
    private let start: Date?
    private let finish: Date?
    private let measuredSeconds: Double?
    let isLive: Bool
    let isWaiting: Bool

    init(agent: RunAgentSummary) {
        start = agent.startedAt.flatMap(parseInternetDate)
        finish = agent.finishedAt.flatMap(parseInternetDate)
        let phases = agent.phaseTimings ?? [:]
        measuredSeconds = (phases["waiting_for_output"] != nil || phases["working"] != nil)
            ? (phases["waiting_for_output"] ?? 0) + (phases["working"] ?? 0)
            : agent.seconds
        isWaiting = ["queued", "copying", "starting", "resuming", "submitting"].contains(agent.normalizedStatus)
        isLive = !isWaiting && agent.isRunning && start != nil && finish == nil
    }

    func seconds(at now: Date) -> Double? {
        guard !isWaiting else { return nil }
        if let start {
            if let finish {
                return measuredSeconds ?? max(0, finish.timeIntervalSince(start))
            }
            if isLive { return max(0, now.timeIntervalSince(start)) }
        }
        return measuredSeconds
    }
}

struct ResultDetailMetrics {
    let duration: String?
    let reasoningTokens: Int64?

    init(agent: RunAgentSummary, now: Date = .now) {
        duration = Self.duration(seconds: ResultElapsedTime(agent: agent).seconds(at: now))
        let steps = agent.reasoningSteps
        reasoningTokens = steps.isEmpty
            ? agent.reasoningTokens.map(Int64.init)
            : steps.reduce(Int64(0)) { $0 + $1.contribution }
    }

    static func duration(seconds: Double?) -> String? {
        if let seconds, seconds.isFinite, seconds < Double(Int.max) {
            let total = Int(max(0, seconds).rounded())
            if total >= 3_600 {
                return "\(total / 3_600)h \((total % 3_600) / 60)m \(total % 60)s"
            } else if total >= 60 {
                return "\(total / 60)m \(total % 60)s"
            } else {
                return "\(total)s"
            }
        }
        return nil
    }
}

struct ConversationResultDetails: View {
    @Environment(\.appLanguage) private var language
    @Environment(\.appAccentColor) private var accent
    @Environment(\.appTextSize) private var textSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AppStorage("parallel-codex.usage-breakdown-expanded") private var usageExpanded = false
    @State private var expandedFileCategories: Set<AgentFileCategory> = [.project]
    @State private var timingsExpanded = false
    @ObservedObject var liveMetrics: ActiveRunMetrics
    let run: RunSummary
    let agent: RunAgentSummary
    let diffSummary: AgentDiffSummary?
    let diffLoading: Bool
    let showDiff: () -> Void
    var showFileDiff: ((String) -> Void)? = nil

    private var metricsAgent: RunAgentSummary {
        liveMetrics.agent(runID: run.runId, index: agent.agent) ?? agent
    }

    private var canShowChanges: Bool {
        agent.workspaceAvailable == true || agent.isTerminal || diffSummary?.unavailable == false
    }

    private var collapsedUsageHeight: CGFloat { 32 * textSize.scale + 12 }

    var body: some View {
        VStack(spacing: 0) {
            summary
                .padding(20)
            Rectangle().fill(ParallelCodexVisualStyle.separator).frame(height: 1)
                .padding(.horizontal, 20)
            VSplitView {
                reasoningDetails
                    .padding(.horizontal, 20)
                    .padding(.vertical, 6)
                    .frame(minHeight: usageExpanded ? 100 * textSize.scale : collapsedUsageHeight,
                           idealHeight: usageExpanded ? 180 * textSize.scale : collapsedUsageHeight,
                           maxHeight: usageExpanded ? .infinity : collapsedUsageHeight)
                    .layoutPriority(-1)
                    .background(SidebarSplitPersistence(
                        name: ResultInspectorLayout.sectionsAutosaveName, isVertical: false,
                        initialPosition: 180 * textSize.scale, expanded: usageExpanded
                    ))
                fileChanges
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
                    .frame(minHeight: 90 * textSize.scale, maxHeight: .infinity)
            }
            .accessibilityIdentifier("result.sections")
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 16) {
            status
            let metrics = ResultDetailMetrics(agent: metricsAgent)
            HStack(alignment: .top, spacing: 24) {
                elapsedMetric
                metric(
                    language.text("推理 tokens", "Reasoning tokens"),
                    value: metrics.reasoningTokens?.formatted()
                )
            }
            let phases = metricsAgent.phaseTimings ?? [:]
            let runPhases = liveMetrics.runID == run.runId
                ? liveMetrics.runPhaseTimings : (run.phaseTimings ?? [:])
            if !phases.isEmpty || !runPhases.isEmpty {
                ResultPhaseTimings(isExpanded: $timingsExpanded, runTimings: runPhases, agentTimings: phases)
                    .buttonStyle(.plain)
            }
            if let error = agent.error, !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Label {
                    Text(error).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "exclamationmark.circle")
                }
                .foregroundStyle(.red)
                .appScaledTextStyle(.body)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var elapsedMetric: some View {
        let timing = ResultElapsedTime(agent: metricsAgent)
        let title = metricsAgent.phaseTimings == nil
            ? language.text("耗时", "Elapsed") : language.text("执行耗时", "Execution elapsed")
        return Group {
            if timing.isLive {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    metric(title, value:
                        ResultDetailMetrics.duration(seconds: timing.seconds(at: context.date)))
                }
            } else {
                metric(title, value: timing.isWaiting
                    ? language.text("等待开始", "Not started")
                    : ResultDetailMetrics.duration(seconds: timing.seconds(at: .now)))
            }
        }
    }

    private var status: some View {
        HStack(spacing: 10) {
            HStack(spacing: 6) {
                ResultStatusGlyph(status: conversationAgentGlyphStatus(run: run, agent: agent))
                    .accessibilityHidden(true)
                Text(conversationAgentDisplayStatus(run: run, agent: agent, language: language))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(conversationAgentDisplayStatus(run: run, agent: agent, language: language))
            .accessibilityIdentifier("result.execution-status")
            if agent.recommended {
                Label(language.text("推荐", "Recommended"), systemImage: "star.fill")
                    .foregroundStyle(.orange)
            }
        }
        .appScaledTextStyle(.body)
    }

    private func metric(_ title: String, value: String?) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .appScaledSystemFont(size: ParallelCodexVisualStyle.Typography.caption)
                .foregroundStyle(.secondary)
            Text(value ?? language.text("暂无", "Unavailable"))
                .appScaledTextStyle(.title2)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private var reasoningDetails: some View {
        let steps = metricsAgent.reasoningSteps
        return VStack(alignment: .leading, spacing: 8) {
            Button {
                usageExpanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .appScaledSystemFont(size: 10, weight: .semibold)
                        .rotationEffect(.degrees(usageExpanded ? 90 : 0))
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: usageExpanded)
                        .frame(width: 12 * textSize.scale)
                        .foregroundStyle(.secondary)
                    Text(language.text("用量明细", "Usage breakdown"))
                        .fontWeight(.medium)
                    Spacer(minLength: 8)
                    let count = steps.reduce(0) { $0 + $1.count }
                    if count > 0 {
                        Text(language.text("\(count) 次", "\(count) steps"))
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 32 * textSize.scale, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(language.text("用量明细", "Usage breakdown"))
            .accessibilityValue(usageExpanded ? language.text("已展开", "Expanded") : language.text("已收起", "Collapsed"))
            .accessibilityIdentifier("result.usage-breakdown")

            if usageExpanded {
                if steps.isEmpty {
                    Text(agent.isRunning
                        ? language.text("等待模型回报用量", "Waiting for model usage")
                        : language.text("暂无用量记录", "No usage recorded"))
                        .foregroundStyle(.secondary)
                        .frame(maxHeight: .infinity, alignment: .topLeading)
                } else {
                    usageRow(language.text("单次推理量", "Tokens / request"),
                             language.text("请求次数", "Requests"), language.text("累计 tokens", "Total tokens"))
                        .foregroundStyle(.secondary)
                    InspectorScrollView {
                        LazyVStack(spacing: 10) {
                            ForEach(steps) { step in
                                usageRow(step.interval.formatted(), step.count.formatted(),
                                         step.contribution.formatted())
                                    .accessibilityElement(children: .combine)
                            }
                        }
                        .monospacedDigit()
                        .textSelection(.enabled)
                        .padding(.vertical, 4)
                    }
                    .accessibilityLabel(language.text("用量记录", "Usage records"))
                    .accessibilityIdentifier("result.usage-scroll")
                }
            }
        }
        .appScaledTextStyle(.body)
    }

    private func usageRow(_ interval: String, _ count: String, _ total: String) -> some View {
        HStack(spacing: 12) {
            Text(interval).frame(maxWidth: .infinity, alignment: .leading)
            Text(count).frame(maxWidth: .infinity, alignment: .trailing)
            Text(total).frame(maxWidth: .infinity, alignment: .trailing)
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }

    private var fileChanges: some View {
        VStack(alignment: .leading, spacing: 12) {
            fileChangesHeader
            InspectorScrollView {
                fileChangesContent
                    .padding(.vertical, 4)
            }
            .accessibilityLabel(language.text("文件修改列表", "Changed files"))
            .accessibilityIdentifier("result.files-scroll")
        }
    }

    private var fileChangesHeader: some View {
        HStack(spacing: 8) {
            Text(language.text("文件修改", "File changes"))
                .appScaledTextStyle(.body, weight: .medium)
            if let summary = diffSummary, !summary.unavailable {
                Text(summary.totalFiles.formatted())
                    .appScaledTextStyle(.body)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if canShowChanges {
                Button(action: showDiff) {
                    Image(systemName: "doc.text.magnifyingglass")
                }
                .buttonStyle(ParallelCodexIconButtonStyle())
                .help(language.text("查看完整修改", "View full patch"))
                .accessibilityLabel(language.text("查看完整修改", "View full patch"))
            }
        }
    }

    private var fileChangesContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !canShowChanges {
                fileState(language.text("文件修改不可用", "File changes unavailable"), symbol: "doc")
            } else if let summary = diffSummary, !summary.unavailable {
                if summary.changes.isEmpty {
                    fileState(agent.isRunning ? language.text("尚无文件修改", "No changes yet")
                              : language.text("没有文件修改", "No file changes"), symbol: "doc")
                } else {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(AgentFileCategory.allCases) { category in
                            let changes = summary.changes.filter { $0.category == category }
                            if !changes.isEmpty {
                                Button {
                                    if expandedFileCategories.contains(category) {
                                        expandedFileCategories.remove(category)
                                    } else {
                                        expandedFileCategories.insert(category)
                                    }
                                } label: {
                                    HStack(spacing: 8) {
                                        Image(systemName: expandedFileCategories.contains(category) ? "chevron.down" : "chevron.right")
                                            .frame(width: 12)
                                        Text(category.title(language))
                                        Spacer(minLength: 4)
                                        Text(changes.count.formatted()).monospacedDigit()
                                    }
                                    .foregroundStyle(.secondary)
                                    .appScaledTextStyle(.caption, weight: .medium)
                                    .padding(.vertical, 9)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("result.files.\(category.rawValue)")
                                .accessibilityValue(expandedFileCategories.contains(category)
                                    ? language.text("已展开", "Expanded") : language.text("已收起", "Collapsed"))
                                if expandedFileCategories.contains(category) {
                                    ForEach(changes) { change in
                                        Button {
                                            if let showFileDiff { showFileDiff(change.path) }
                                            else { showDiff() }
                                        } label: {
                                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                                Image(systemName: fileSymbol(change.status))
                                                    .foregroundStyle(fileColor(change.status))
                                                    .frame(width: 16)
                                                Text(change.path)
                                                    .lineLimit(2)
                                                    .truncationMode(.middle)
                                                Spacer(minLength: 0)
                                                Image(systemName: "chevron.right")
                                                    .foregroundStyle(.tertiary)
                                            }
                                            .padding(.vertical, 7)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .contentShape(Rectangle())
                                        }
                                        .buttonStyle(.plain)
                                        .appScaledTextStyle(.body)
                                        .help(language.text("查看 \(change.path) 的修改", "View changes to \(change.path)"))
                                        .accessibilityIdentifier("result.file.\(change.path)")
                                    }
                                }
                            }
                        }
                    }
                    if summary.truncated {
                        Button(language.text("查看全部修改", "View all changes"), action: showDiff)
                            .buttonStyle(.borderless)
                    }
                }
            } else if diffSummary?.unavailable == true {
                fileState(language.text("无法读取文件修改", "Could not load file changes"), symbol: "exclamationmark.circle")
            } else {
                HStack(spacing: 8) {
                    if diffLoading { ProgressView().controlSize(.small) }
                    Text(language.text("正在读取文件修改", "Loading file changes"))
                }
                .appScaledTextStyle(.body)
                .foregroundStyle(.secondary)
            }
        }
    }

    private func fileState(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .appScaledTextStyle(.body)
            .foregroundStyle(.secondary)
    }

    private func fileSymbol(_ status: String) -> String {
        switch status {
        case "A", "C": return "plus.circle"
        case "D": return "minus.circle"
        case "R": return "arrow.right"
        default: return "pencil"
        }
    }

    private func fileColor(_ status: String) -> Color {
        switch status {
        case "A", "C": return .green
        case "D": return .red
        default: return accent.color
        }
    }
}
