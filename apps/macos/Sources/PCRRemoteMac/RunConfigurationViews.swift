import AppKit
import SwiftUI

struct RunSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize
    @State private var advancedExpanded = false

    var body: some View {
        VStack(spacing: 0) {
            SheetHeader(
                title: "Run Settings",
                subtitle: model.selectedWorkspace?.name ?? "Parallel Codex",
                systemImage: "slider.horizontal.3",
                close: dismissSettings
            )
            .padding(20)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    ComposerExecutionConfiguration()
                    Divider()
                    RunSettingsSection("Run") {
                        RunSettingsGrid {
                            RunSettingsRow("Execution") {
                                Picker(
                                    model.appLanguage.localized("Execution"),
                                    selection: $model.settings.execution
                                ) {
                                    ForEach(RunExecution.allCases) { option in
                                        Text(option.title(language: model.appLanguage)).tag(option)
                                    }
                                }
                                .labelsHidden()
                                .pickerStyle(.segmented)
                            }
                            RunSettingsRow("Max parallel") {
                                HStack(spacing: 12) {
                                    Toggle(
                                        model.appLanguage.localized("Automatic"),
                                        isOn: $model.settings.automaticMaxParallel
                                    )
                                    .toggleStyle(.switch)
                                    AgentCountField(
                                        value: Binding(
                                            get: { model.settings.displayedMaxParallel },
                                            set: { model.settings.maxParallel = $0 }
                                        ),
                                        range: 1...model.effectiveMaximumAgentCount
                                    )
                                    .disabled(
                                        model.settings.automaticMaxParallel
                                            || model.settings.execution == .serial
                                    )
                                }
                            }
                            RunSettingsRow("Recommend by") {
                                Picker(
                                    model.appLanguage.localized("Recommend by"),
                                    selection: $model.settings.recommendation
                                ) {
                                    ForEach(RunRecommendation.allCases) { option in
                                        Text(option.title(language: model.appLanguage)).tag(option)
                                    }
                                }
                                .labelsHidden()
                            }
                            RunSettingsRow("Follow-up delay") {
                                HStack(spacing: 8) {
                                    AgentCountField(
                                        value: $model.settings.followUpDelay,
                                        range: 0...3600
                                    )
                                    Text(model.appLanguage.localized("seconds"))
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }

                    RunSettingsSection("Subagents") {
                        RunSettingsGrid {
                            RunSettingsToggleRow(
                                "Allow Agents to create subagents",
                                isOn: $model.settings.subagents
                            )
                            if model.settings.subagents {
                                RunSettingsRow("Subagent limit") {
                                    AgentCountField(
                                        value: $model.settings.subagentsLimit,
                                        range: 1...model.effectiveMaximumAgentCount
                                    )
                                }
                            }
                        }
                    }

                    RunSettingsSection("Workspace") {
                        RunSettingsGrid {
                            RunSettingsRow("Conversation") {
                                Picker(
                                    model.appLanguage.localized("Conversation"),
                                    selection: resumeBinding
                                ) {
                                    Text(model.appLanguage.localized("New conversation")).tag("")
                                    if let currentSession = usableResumeSessionID(
                                        model.settings.resumeSessionID
                                    ), !model.resumeSessions.contains(where: {
                                        $0.sessionId == currentSession
                                    }) {
                                        Text(model.appLanguage.localized("Current conversation"))
                                            .tag(currentSession)
                                    }
                                    ForEach(model.resumeSessions) { session in
                                        Text(session.displayTitle == "Previous conversation"
                                            ? model.appLanguage.text("之前的对话", "Previous conversation")
                                            : session.displayTitle)
                                            .lineLimit(1)
                                            .tag(session.sessionId)
                                    }
                                }
                                .labelsHidden()
                                .disabled(model.activeRun?.isWorking == true)
                            }
                            RunSettingsToggleRow(
                                "Apply selected Agent",
                                isOn: $model.settings.syncBack
                            )
                            RunSettingsToggleRow(
                                "Keep candidate workspaces",
                                isOn: $model.settings.keepWorkspaces
                            )
                            RunSettingsToggleRow(
                                "Include non-interactive conversations",
                                isOn: $model.settings.resumeIncludeNonInteractive
                            )
                        }
                    }

                    RunSettingsSection("Advanced") {
                        DisclosureGroup(
                            model.appLanguage.localized("Paths"),
                            isExpanded: $advancedExpanded
                        ) {
                            RunSettingsGrid {
                                RunSettingsRow("Runs directory") {
                                    TextField(
                                        "",
                                        text: $model.settings.runsDirectory,
                                        prompt: Text(model.appLanguage.localized("Automatic"))
                                    )
                                    .labelsHidden()
                                    .textFieldStyle(.roundedBorder)
                                }
                                RunSettingsRow("Codex executable") {
                                    TextField(
                                        "",
                                        text: $model.settings.codexExecutable,
                                        prompt: Text("codex")
                                    )
                                    .labelsHidden()
                                    .textFieldStyle(.roundedBorder)
                                }
                            }
                            .padding(.top, 10)
                        }
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            }

            Divider()
            HStack {
                if let error = model.modelOptionsError, !error.isEmpty {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(model.appLanguage.localized(
                        "Some Codex options could not be loaded"
                    ))
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                        .help(error)
                    Button(model.appLanguage.localized("Reload")) {
                        Task { await model.reloadModelOptions() }
                    }
                }
                Spacer()
                Button(model.appLanguage.localized("Done")) {
                    dismissSettings()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
            .padding(14)
            .background(.bar)
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .onExitCommand(perform: dismissSettings)
    }

    private func dismissSettings() {
        NSApp.keyWindow?.makeFirstResponder(nil)
        model.dismissRunSettings()
    }

    private var resumeBinding: Binding<String> {
        Binding(
            get: { usableResumeSessionID(model.settings.resumeSessionID) ?? "" },
            set: { value in Task { await model.updateResumeSession(value) } }
        )
    }

}

struct RunSettingsSection<Content: View>: View {
    @Environment(\.appLanguage) private var appLanguage
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(appLanguage.localized(title))
                .appScaledTextStyle(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

enum RunSettingsGridDensity {
    case regular
    case compact

    var labelWidth: CGFloat {
        switch self {
        case .regular: 170
        case .compact: 0
        }
    }

    var controlWidth: CGFloat {
        switch self {
        case .regular: 340
        case .compact: 290
        }
    }

    var columnSpacing: CGFloat {
        switch self {
        case .regular: 22
        case .compact: 0
        }
    }
}

private struct RunSettingsGridDensityKey: EnvironmentKey {
    static let defaultValue = RunSettingsGridDensity.regular
}

private extension EnvironmentValues {
    var runSettingsGridDensity: RunSettingsGridDensity {
        get { self[RunSettingsGridDensityKey.self] }
        set { self[RunSettingsGridDensityKey.self] = newValue }
    }
}

struct RunSettingsGrid<Content: View>: View {
    @Environment(\.appTextSize) private var appTextSize
    let density: RunSettingsGridDensity
    let content: Content

    init(
        density: RunSettingsGridDensity = .regular,
        @ViewBuilder content: () -> Content
    ) {
        self.density = density
        self.content = content()
    }

    var body: some View {
        Grid(
            alignment: .leading,
            horizontalSpacing: density.columnSpacing,
            verticalSpacing: 10 * appTextSize.scale
        ) {
            content
        }
        .environment(\.runSettingsGridDensity, density)
    }
}

struct RunSettingsRow<Content: View>: View {
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.appTextSize) private var appTextSize
    @Environment(\.runSettingsGridDensity) private var density
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    @ViewBuilder
    var body: some View {
        if density == .compact {
            VStack(alignment: .leading, spacing: 5) {
                Text(appLanguage.localized(title))
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                HStack(spacing: 0) {
                    content
                    Spacer(minLength: 0)
                }
            }
            .frame(width: density.controlWidth, alignment: .leading)
            .frame(minHeight: 42 * appTextSize.scale, alignment: .topLeading)
            .gridCellColumns(2)
        } else {
            GridRow(alignment: .firstTextBaseline) {
                Text(appLanguage.localized(title))
                    .foregroundStyle(.secondary)
                    .frame(width: density.labelWidth, alignment: .trailing)
                HStack(spacing: 0) {
                    content
                    Spacer(minLength: 0)
                }
                .frame(width: density.controlWidth, alignment: .leading)
            }
            .frame(minHeight: 28 * appTextSize.scale)
        }
    }
}

struct RunSettingsToggleRow: View {
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.appTextSize) private var appTextSize
    @Environment(\.runSettingsGridDensity) private var density
    let title: String
    @Binding var isOn: Bool

    init(_ title: String, isOn: Binding<Bool>) {
        self.title = title
        _isOn = isOn
    }

    @ViewBuilder
    var body: some View {
        if density == .compact {
            HStack(alignment: .center, spacing: 12) {
                Text(appLanguage.localized(title))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Toggle("", isOn: $isOn)
                    .labelsHidden()
                    .toggleStyle(.switch)
            }
            .frame(width: density.controlWidth, alignment: .leading)
            .frame(minHeight: 30 * appTextSize.scale)
            .gridCellColumns(2)
        } else {
            GridRow(alignment: .center) {
                Text(appLanguage.localized(title))
                    .foregroundStyle(.secondary)
                    .frame(width: density.labelWidth, alignment: .trailing)
                HStack(spacing: 0) {
                    Toggle("", isOn: $isOn)
                        .labelsHidden()
                        .toggleStyle(.switch)
                    Spacer(minLength: 0)
                }
                .frame(width: density.controlWidth, alignment: .leading)
            }
            .frame(minHeight: 28 * appTextSize.scale)
        }
    }
}

private struct SettingsStateNote: View {
    @Environment(\.appLanguage) private var appLanguage
    let text: String
    let systemImage: String

    var body: some View {
        Label(appLanguage.localized(text), systemImage: systemImage)
            .appScaledTextStyle(.caption)
            .foregroundStyle(.secondary)
            .padding(
                .leading,
                RunSettingsGridDensity.regular.labelWidth
                    + RunSettingsGridDensity.regular.columnSpacing
            )
    }
}

struct AgentDiffView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(model.diffTitle).appScaledTextStyle(.headline).lineLimit(1)
                Spacer()
                Button { model.diffPresented = false } label: { Image(systemName: "xmark") }
                    .help(model.appLanguage.localized("Close"))
            }.padding(16)
            Divider()
            if let context = model.diffContext {
                AgentReviewPane(context: context, mode: .changes, initialFile: model.diffSelectedFile)
                    .id("\(context):\(model.diffSelectedFile ?? "")")
            }
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
    }
}

struct AddAgentsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            SheetHeader(
                title: "Add Candidates",
                subtitle: "Run more Agents for the current question.",
                systemImage: "person.badge.plus",
                close: { model.addAgentsPresented = false }
            )

            LabeledContent(model.appLanguage.localized("Additional Agents")) {
                AgentCountField(
                    value: $model.additionalAgentCount,
                    range: 1...model.effectiveMaximumAgentCount
                )
            }

            Spacer()
            HStack {
                Spacer()
                Button(model.appLanguage.localized("Cancel"), role: .cancel) {
                    model.addAgentsPresented = false
                }
                Button(model.appLanguage.localized("Add")) {
                    Task { await model.addMoreAgents() }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
    }
}

struct AgentCountField: View {
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.appTextSize) private var appTextSize
    @Binding var value: Int
    let range: ClosedRange<Int>

    var body: some View {
        HStack(spacing: 8) {
            TextField(
                appLanguage.text("数量", "Count"),
                value: boundedValue,
                formatter: integerFormatter
            )
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .frame(width: 72 * appTextSize.scale)
                .help(
                    appLanguage.text(
                        "请输入 \(range.lowerBound) 到 \(range.upperBound) 之间的数值",
                        "Enter a value from \(range.lowerBound) to \(range.upperBound)"
                    )
                )
            Stepper("", value: boundedValue, in: range)
                .labelsHidden()
        }
    }

    private var boundedValue: Binding<Int> {
        Binding(
            get: { value },
            set: { value = min(max($0, range.lowerBound), range.upperBound) }
        )
    }

    private var integerFormatter: NumberFormatter {
        let formatter = NumberFormatter()
        formatter.numberStyle = .none
        formatter.allowsFloats = false
        formatter.minimum = NSNumber(value: range.lowerBound)
        formatter.maximum = NSNumber(value: range.upperBound)
        return formatter
    }
}

struct FollowUpQueueView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize
    @State private var editingItem: QueuedFollowUp?
    @State private var updating = false

    var body: some View {
        let context = model.followUpQueueContext
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                QueueIcon().frame(width: 28 * appTextSize.scale, height: 28 * appTextSize.scale).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.appLanguage.text("排队消息", "Queued messages"))
                        .appScaledTextStyle(.title3, weight: .semibold)
                    Text(model.appLanguage.text("\(model.composerQueuedMessages.count) 条待发送", "\(model.composerQueuedMessages.count) queued"))
                        .appScaledTextStyle(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                Button { Task { await model.cancelFollowUpQueueEditing() } } label: {
                    Image(systemName: "xmark").frame(width: 28, height: 28)
                }
                .buttonStyle(.plain)
                .help(model.appLanguage.text("关闭", "Close"))
                .accessibilityLabel(model.appLanguage.text("关闭队列", "Close queue"))
                .keyboardShortcut(.cancelAction)
            }
            .padding(18)

            Divider()

            if model.composerQueuedMessages.isEmpty {
                EmptyState(
                    title: model.appLanguage.text("没有排队消息", "No Queued Follow-ups"),
                    systemImage: "text.badge.xmark"
                )
            } else {
                QueuedMessageList(items: model.composerQueuedMessages,
                    dispatching: model.daemonFollowUpSnapshot?.state == "dispatching", context: context,
                    updating: updating, expanded: true,
                    change: { await apply($0, change: $1, context: context) }, edit: { editingItem = $0 })
                    .id(context)
                    .padding(.horizontal, 8)
            }

            Divider()
            VStack(alignment: .leading, spacing: 8) {
                if let issue = model.queueIssue {
                    Text(issue).foregroundStyle(.orange).lineLimit(3)
                }
                HStack {
                    Text(model.visibleDeferredPrompts.isEmpty ? model.followUpStatusText
                        : model.appLanguage.text("排队消息", "Queued messages")).foregroundStyle(.secondary).lineLimit(2)
                    Spacer(minLength: 12)
                    if updating {
                        Text(model.appLanguage.text("正在保存", "Saving")).foregroundStyle(.secondary)
                    }
                }
            }
            .appScaledTextStyle(.caption)
            .padding(14)
            .background(.bar)
        }
        .appScaledTextStyle(.body)
        .controlSize(appTextSize.controlSize)
        .frame(height: 4 * 76 * appTextSize.scale + 156)
        .background(ParallelCodexVisualStyle.canvas)
        .sheet(item: $editingItem) { item in
            QueuedFollowUpEditor(item: item,
                save: { await apply(item.id, change: $0, context: context) }, close: { editingItem = nil })
                .environment(\.appLanguage, model.appLanguage)
        }
        .onDisappear {
            Task { await model.cancelFollowUpQueueEditing() }
        }
    }

    private func apply(_ id: UUID, change: QueuedFollowUpChange, context: String) async -> Bool {
        guard !updating else { return false }
        updating = true
        defer { updating = false }
        return await model.changeQueuedFollowUp(id, change: change, expectedContext: context)
    }
}

struct ConnectionStatusBar: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var appTextSize

    var body: some View {
        HStack(spacing: 16) {
            StatusLabel(
                title: serverStatusTitle,
                systemImage: model.daemonOnline ? "checkmark.circle.fill" : "exclamationmark.circle.fill",
                color: model.daemonOnline ? .green : .red
            )
            Spacer()
            StatusLabel(
                title: model.relay.hasConnectedPhone ? "Phone connected" : "Phone not connected",
                systemImage: model.relay.hasConnectedPhone ? "checkmark.shield.fill" : "shield",
                color: model.relay.hasConnectedPhone ? .green : .secondary
            )
        }
        .padding(.horizontal, 18)
        .frame(minHeight: 40 * appTextSize.scale)
        .background(.bar)
    }

    private var serverStatusTitle: String {
        if model.daemonOnline { return "Server connected" }
        switch model.relayLink?.state {
        case "credentials_rejected":
            return "Server credentials rejected"
        case "connecting":
            return "Server connecting"
        case "not_configured":
            return "Server not configured"
        default:
            return "Server unavailable"
        }
    }
}

struct StatusLabel: View {
    let title: String
    let systemImage: String
    let color: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .appScaledTextStyle(.caption, weight: .medium)
            .foregroundStyle(color)
    }
}
