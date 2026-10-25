import SwiftUI

struct ResultPhaseTimings: View {
    @Environment(\.appLanguage) private var language
    @Environment(\.appTextSize) private var textSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var isExpanded: Bool
    let runTimings: [String: Double]
    let agentTimings: [String: Double]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                isExpanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .appScaledSystemFont(size: 10, weight: .semibold)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: isExpanded)
                        .frame(width: 12 * textSize.scale)
                        .foregroundStyle(.secondary)
                    Text(language.text("阶段耗时", "Phase timings"))
                        .fontWeight(.medium)
                    Spacer(minLength: 8)
                }
                .frame(maxWidth: .infinity, minHeight: 24 * textSize.scale, alignment: .leading)
                .contentShape(Rectangle())
            }
            .accessibilityLabel(language.text("阶段耗时", "Phase timings"))
            .accessibilityValue(isExpanded ? language.text("已展开", "Expanded") : language.text("已收起", "Collapsed"))
            .accessibilityIdentifier("result.phase-timings")

            if isExpanded {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        phaseRows(["discovery", "baseline", "result_preservation", "workspace_sync", "session_promotion", "finalization_cleanup"], timings: runTimings)
                        phaseRows(["workspace_copy", "history_preparation", "configuration", "launching", "initializing", "resume_slot", "resuming", "creating_thread", "submitting", "waiting_for_output", "working", "cleanup"], timings: agentTimings)
                    }
                    .padding(.top, 6)
                }
                .frame(maxHeight: 160 * textSize.scale)
                .accessibilityIdentifier("result.phase-timings-content")
            }
        }
        .appScaledTextStyle(.caption)
    }

    private func phaseRows(_ order: [String], timings: [String: Double]) -> some View {
        ForEach(order, id: \.self) { phase in
            if let seconds = timings[phase], seconds.isFinite, seconds >= 0 {
                HStack(alignment: .top) {
                    Text(phaseLabel(phase)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 12)
                    Text(ResultDetailMetrics.duration(seconds: seconds) ?? "").monospacedDigit()
                }
            }
        }
    }

    private func phaseLabel(_ phase: String) -> String {
        switch phase {
        case "discovery": return language.text("来源与配置发现", "Source and configuration discovery")
        case "baseline": return language.text("审阅基线准备", "Review baseline preparation")
        case "result_preservation": return language.text("结果保存", "Result preservation")
        case "workspace_sync": return language.text("文件回写", "Workspace synchronization")
        case "session_promotion": return language.text("对话保存", "Conversation preservation")
        case "finalization_cleanup": return language.text("清理副本", "Copy cleanup")
        case "workspace_copy": return language.text("工作区准备", "Workspace preparation")
        case "history_preparation": return language.text("历史准备", "History preparation")
        case "configuration": return language.text("配置加载", "Configuration")
        case "launching": return language.text("进程启动", "Process launch")
        case "initializing": return language.text("连接初始化", "Connection initialization")
        case "resume_slot": return language.text("恢复资源等待", "Resume resource wait")
        case "resuming": return language.text("恢复对话", "Conversation restoration")
        case "creating_thread": return language.text("创建对话", "Conversation creation")
        case "submitting": return language.text("提交接收", "Submission acceptance")
        case "waiting_for_output": return language.text("首个活动等待", "First activity wait")
        case "working": return language.text("执行", "Execution")
        default: return language.text("执行收尾", "Execution cleanup")
        }
    }
}
