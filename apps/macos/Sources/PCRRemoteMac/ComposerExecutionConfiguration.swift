import AppKit
import SwiftUI

enum ExecutionStage: String, CaseIterable {
    case candidates, synthesis

    var count: WritableKeyPath<RunSettings, Int> { self == .candidates ? \.agents : \.synthesisAgents }
    var model: WritableKeyPath<RunSettings, String> { self == .candidates ? \.model : \.synthesisModel }
    var effort: WritableKeyPath<RunSettings, String> { self == .candidates ? \.effort : \.synthesisEffort }
    var fast: WritableKeyPath<RunSettings, RunFastMode> { self == .candidates ? \.fast : \.synthesisFast }
    var tint: Color { self == .candidates ? .orange : .teal }

    func title(_ language: AppLanguage) -> String {
        self == .candidates ? language.text("候选", "Candidates") : language.text("综合", "Synthesis")
    }

    @MainActor func isLocked(in model: AppModel) -> Bool {
        self == .synthesis && model.activeRun?.isWorking == true && model.activeRun?.mayUpdateSynthesis != true
    }

    @MainActor func displayedSettings(in model: AppModel) -> RunSettings {
        var settings = model.settings
        if isLocked(in: model), let run = model.activeRun {
            settings.synthesisAgents = run.config.synthesisAgents ?? run.agents.filter { $0.role == "synthesis" }.count
            settings.synthesisModel = run.config.synthesisModel ?? ""
            settings.synthesisEffort = run.config.synthesisEffort ?? ""
            settings.synthesisFast = RunFastMode(run.config.synthesisFast)
        }
        return settings
    }

    @MainActor func options(in model: AppModel) -> CodexModelOptions? {
        if isLocked(in: model), displayedSettings(in: model)[keyPath: self.model].isEmpty { return nil }
        return model.modelOptionsCache.options(for: CodexModelOptionsContext(
            machineID: model.currentMachineContextID, model: displayedSettings(in: model)[keyPath: self.model]))
    }

    @MainActor func modelTitle(in model: AppModel) -> String {
        let selection = displayedSettings(in: model)[keyPath: self.model]
        let options = options(in: model)
        let value = selection.isEmpty ? options?.effectiveModel ?? options?.configuredModel ?? "" : selection
        let label = model.codexModelCatalog?.first(where: { $0.value == value })?.label ?? value
        return label.isEmpty ? model.appLanguage.text("Codex 默认", "Codex default") : codexModelDisplayName(label)
    }
}

struct ExecutionEditContext: Hashable {
    let machineID: String
    let workspaceID: String?
    let generation: Int
    let runID: String?

    @MainActor init(_ model: AppModel) {
        machineID = model.currentMachineContextID
        workspaceID = model.selectedWorkspaceID
        generation = model.workspaceContextGeneration
        runID = model.activeRun?.runId
    }
}

enum ExecutionConfigurationEdit {
    case count(Int), model(String), effort(String), fast(RunFastMode)
}

private struct ExecutionCatalogContext: Hashable { let machine: String; let online: Bool }

func executionEffortTitle(selection: String, effective: String?, language: AppLanguage) -> String {
    guard selection.isEmpty else { return localizedCodexEffortLabel(selection, language: language) }
    let title = localizedAutomaticCodexLabel(effective, language: language)
    return language == .english ? title.replacingOccurrences(of: "Automatic", with: "Auto", options: .anchored) : title
}

@MainActor
extension AppModel {
    @discardableResult
    func editExecutionConfiguration(_ edit: ExecutionConfigurationEdit, stage: ExecutionStage,
                                    context: ExecutionEditContext) async -> Bool {
        guard ExecutionEditContext(self) == context, !isRunOperationInProgress, !stage.isLocked(in: self) else { return false }
        switch edit {
        case .count(let value):
            guard (stage == .candidates ? 1...effectiveMaximumAgentCount : 0...effectiveMaximumAgentCount).contains(value) else { return false }
            settings[keyPath: stage.count] = value
        case .model(let value):
            if stage == .candidates { await updatePrimaryModel(value) }
            else { await updateSynthesisModel(value) }
            return true
        case .effort(let value):
            guard value.isEmpty || stage.options(in: self)?.efforts.contains(where: { $0.value == value }) == true
                || value == settings[keyPath: stage.effort] else { return false }
            settings[keyPath: stage.effort] = value
        case .fast(let value): settings[keyPath: stage.fast] = value
        }
        if stage == .synthesis { scheduleSynthesisSettingsUpdate() }
        else { rememberCurrentSettings() }
        return true
    }
}

struct ComposerExecutionConfigurationSummary: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var textSize
    @Binding var isEditing: Bool
    @State private var isPresented = false

    init(isEditing: Binding<Bool> = .constant(false)) { _isEditing = isEditing }

    @MainActor static func description(in model: AppModel) -> String {
        ExecutionStage.allCases.map { stage in
            let settings = stage.displayedSettings(in: model)
            let effort = executionEffortTitle(selection: settings[keyPath: stage.effort],
                effective: stage.options(in: model)?.effectiveEffort, language: model.appLanguage)
            return "\(stage.title(model.appLanguage)) × \(settings[keyPath: stage.count]) · \(stage.modelTitle(in: model)) · \(effort) · Fast \(settings[keyPath: stage.fast].title(language: model.appLanguage))"
        }.joined(separator: "\n")
    }

    var body: some View {
        entry
        .frame(height: 28 * textSize.scale, alignment: .leading)
        .disabled(model.isRunOperationInProgress)
        .background(ExecutionConfigurationAnchor(isPresented: $isPresented, preferredWidth: 368 * textSize.scale) { width in
            ExecutionConfigurationPopover(width: width)
                .environmentObject(model)
                .environment(\.appTextSize, textSize)
                .environment(\.appLanguage, model.appLanguage)
                .onExitCommand(perform: dismiss)
        })
        .onChange(of: isPresented) { isEditing = $0 }
        .onChange(of: ExecutionEditContext(model)) { _ in dismiss() }
        .onChange(of: model.isRunOperationInProgress) { if $0 { dismiss() } }
        .onDisappear(perform: dismiss)
        .task(id: ExecutionCatalogContext(machine: model.currentMachineContextID, online: model.daemonOnline)) {
            guard model.daemonOnline else { return }
            await model.reloadModelOptions()
        }
    }

    private func dismiss() {
        isPresented = false
        isEditing = false
    }

    private var entry: some View {
        Button {
            isPresented.toggle()
            isEditing = isPresented
        } label: {
            HStack(spacing: 6 * textSize.scale) {
                ViewThatFits(in: .horizontal) {
                    stages.fixedSize()
                    ScrollView(.horizontal, showsIndicators: false) {
                        stages.fixedSize()
                    }
                    .frame(height: 28 * textSize.scale)
                }
                Image(systemName: "chevron.down")
                    .appScaledSystemFont(size: 9, weight: .medium)
                    .rotationEffect(.degrees(isPresented ? 180 : 0))
            }
            .appScaledSystemFont(size: 13)
            .foregroundStyle(ParallelCodexVisualStyle.secondaryText)
            .padding(.horizontal, 4 * textSize.scale)
            .frame(height: 28 * textSize.scale)
            .contentShape(Rectangle())
        }
        .buttonStyle(ExecutionSummaryButtonStyle(selected: isPresented))
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(model.appLanguage.text("执行配置", "Execution configuration"))
        .accessibilityValue(Self.description(in: model))
        .accessibilityIdentifier("composer.execution-summary")
        .help(Self.description(in: model))
    }

    private var stages: some View {
        HStack(spacing: 12 * textSize.scale) {
            ForEach(ExecutionStage.allCases, id: \.self) { stage in
                let settings = stage.displayedSettings(in: model)
                HStack(spacing: 5 * textSize.scale) {
                    Text(stage.title(model.appLanguage))
                    Text("\(settings[keyPath: stage.count])").monospacedDigit()
                        .foregroundStyle(ParallelCodexVisualStyle.primaryText)
                    Text(stage.modelTitle(in: model))
                        .foregroundStyle(ParallelCodexVisualStyle.primaryText)
                        .accessibilityIdentifier("configuration.summary.\(stage.rawValue).model")
                }
            }
        }
    }
}

struct ExecutionSummaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused
    @State private var hovered = false
    let selected: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(!enabled ? 0.45 : configuration.isPressed ? 0.65 : hovered || selected ? 1 : 0.94)
            .overlay(RoundedRectangle(cornerRadius: 4)
                .stroke(Color.accentColor.opacity(enabled && focused ? 0.6 : 0), lineWidth: 1))
            .onHover { hovered = $0 }
    }
}

struct ExecutionConfigurationPopover: View {
    @Environment(\.appTextSize) private var textSize
    var width: CGFloat? = nil

    var body: some View {
        ComposerExecutionConfiguration()
        .padding(12 * textSize.scale)
        .frame(width: width ?? 368 * textSize.scale)
        .background(ParallelCodexVisualStyle.surface)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("configuration.popover")
    }
}

struct ComposerExecutionConfiguration: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var textSize

    var body: some View {
        ExecutionPipelineLayout(scale: textSize.scale) {
            ExecutionStageView(stage: .candidates)
            ExecutionStageView(stage: .synthesis)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(model.appLanguage.text("执行配置", "Execution configuration"))
        .accessibilityIdentifier("composer.execution-configuration")
        .task(id: ExecutionCatalogContext(machine: model.currentMachineContextID, online: model.daemonOnline)) {
            guard model.daemonOnline else { return }
            await model.reloadModelOptions()
        }
    }

}

private struct ExecutionStageLayout: Layout {
    let scale: CGFloat

    private func metrics(_ width: CGFloat, _ subviews: Subviews, minimumHeight: Bool) -> (inline: Bool, role: CGSize, model: CGSize, meta: CGSize, scope: CGSize) {
        let role = subviews[0].sizeThatFits(.unspecified)
        let name = subviews[1].sizeThatFits(.unspecified)
        let scope = subviews[3].sizeThatFits(.unspecified)
        let attributes = subviews[2].sizeThatFits(.unspecified)
        let inline = minimumHeight && role.width + name.width + 12 * scale <= width
            && attributes.width + scope.width + 12 * scale <= width
        let model = subviews[1].sizeThatFits(.init(width: inline ? width - role.width - 12 * scale : width, height: nil))
        let meta = subviews[2].sizeThatFits(.init(width: inline ? width - scope.width - 12 * scale : width, height: nil))
        return (inline, role, model, meta, scope)
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 300 * scale
        let m = metrics(width, subviews, minimumHeight: proposal.height == 0)
        let top = m.inline ? max(m.role.height, m.model.height) : m.role.height + m.model.height + 2 * scale
        return CGSize(width: width, height: top + m.meta.height + 2 * scale)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let m = metrics(bounds.width, subviews, minimumHeight: proposal.height == 0)
        subviews[0].place(at: bounds.origin, proposal: .init(width: m.role.width, height: m.role.height))
        subviews[1].place(at: CGPoint(x: bounds.minX + (m.inline ? m.role.width + 12 * scale : 0),
            y: bounds.minY + (m.inline ? 0 : m.role.height + 2 * scale)), proposal: .init(width: m.model.width, height: m.model.height))
        subviews[2].place(at: CGPoint(x: bounds.minX, y: bounds.maxY - m.meta.height), proposal: .init(width: m.meta.width, height: m.meta.height))
        subviews[3].place(at: CGPoint(x: bounds.maxX - m.scope.width,
            y: m.inline ? bounds.maxY - m.meta.height : bounds.minY + 6 * scale),
            proposal: .init(width: m.scope.width, height: m.scope.height))
    }
}

private struct ExecutionAttributesLayout: Layout {
    let scale: CGFloat
    private func sizes(_ width: CGFloat, _ subviews: Subviews) -> (CGSize, CGSize, Bool) {
        let first = subviews[0].sizeThatFits(.unspecified), second = subviews[1].sizeThatFits(.unspecified)
        return (subviews[0].sizeThatFits(.init(width: min(first.width, width), height: nil)),
                subviews[1].sizeThatFits(.init(width: min(second.width, width), height: nil)),
                first.width + second.width + 12 * scale <= width)
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let idealWidth = subviews[0].sizeThatFits(.unspecified).width + subviews[1].sizeThatFits(.unspecified).width + 12 * scale
        let width = proposal.width ?? idealWidth
        let (a, b, inline) = sizes(width, subviews)
        return CGSize(width: width, height: inline ? max(a.height, b.height) : a.height + b.height + 2 * scale)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let (a, b, inline) = sizes(bounds.width, subviews)
        subviews[0].place(at: bounds.origin, proposal: .init(width: min(a.width, bounds.width), height: a.height))
        subviews[1].place(at: CGPoint(x: bounds.minX + (inline ? a.width + 12 * scale : 0),
            y: bounds.minY + (inline ? 0 : a.height + 2 * scale)),
            proposal: .init(width: min(b.width, bounds.width), height: b.height))
    }
}

// A layout, rather than alternate view trees, keeps open editors intact when the window changes width.
struct ExecutionPipelineLayout: Layout {
    let scale: CGFloat
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 344 * scale
        let wide = width >= 336 * scale
        let stageWidth = wide ? (width - 16 * scale) / 2 : width
        // A zero height proposal asks each narrow stage for its minimum fitting height.
        let heights = subviews.map { $0.sizeThatFits(.init(width: stageWidth, height: wide ? nil : 0)).height }
        return CGSize(width: width, height: wide ? heights.max() ?? 0 : heights.reduce(0, +) + 20 * scale)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let wide = bounds.width >= 336 * scale
        let width = wide ? (bounds.width - 16 * scale) / 2 : bounds.width
        let stageProposal = ProposedViewSize(width: width, height: wide ? nil : 0)
        let firstHeight = subviews[0].sizeThatFits(stageProposal).height
        subviews[0].place(at: bounds.origin, proposal: stageProposal)
        if wide {
            subviews[1].place(at: CGPoint(x: bounds.minX + width + 16 * scale, y: bounds.minY),
                              proposal: stageProposal)
        } else {
            subviews[1].place(at: CGPoint(x: bounds.minX, y: bounds.minY + firstHeight + 20 * scale), proposal: stageProposal)
        }
    }
}

private enum ExecutionField: String { case model, effort, fast }

private struct ExecutionStageView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var textSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let stage: ExecutionStage
    @State private var editingCustomCount = false

    private var language: AppLanguage { model.appLanguage }
    private var settings: RunSettings { stage.displayedSettings(in: model) }
    private var options: CodexModelOptions? { stage.options(in: model) }
    private var locked: Bool { stage.isLocked(in: model) }
    private var modelSelection: String { settings[keyPath: stage.model] }
    private var modelTitle: String {
        stage.modelTitle(in: model)
    }
    private var effortTitle: String {
        executionEffortTitle(selection: settings[keyPath: stage.effort], effective: options?.effectiveEffort, language: language)
    }
    private var scope: String {
        guard model.activeRun?.isWorking == true else { return "" }
        return stage == .candidates ? language.text("下一轮", "Next turn")
            : locked ? language.text("本轮已开始", "Started") : language.text("本轮可改", "This turn")
    }

    var body: some View {
        ExecutionStageLayout(scale: textSize.scale) {
            HStack(spacing: 5 * textSize.scale) {
                Image(systemName: stage == .candidates ? "square.stack.3d.up" : "arrow.triangle.merge")
                    .foregroundStyle(locked ? Color.secondary : stage.tint)
                Text(stage.title(language)).foregroundStyle(.secondary)
                Text("×").foregroundStyle(.secondary)
                countPicker
            }
            .appScaledSystemFont(size: 11, weight: .medium)
            .padding(.horizontal, 5)
            .frame(minHeight: 24 * textSize.scale)
            field(.model, label: language.localized("Model")) {
                HStack(spacing: 6 * textSize.scale) {
                    Text(modelTitle).appScaledSystemFont(size: 15, weight: .semibold)
                        .foregroundStyle(locked ? Color.secondary : .primary)
                        .fixedSize(horizontal: false, vertical: true)
                    if modelSelection.isEmpty, options?.effectiveModel?.isEmpty == false {
                        Text(language.text("默认", "Default")).appScaledSystemFont(size: 10).foregroundStyle(.secondary)
                    }
                    Image(systemName: "chevron.down").appScaledSystemFont(size: 9, weight: .medium).foregroundStyle(.tertiary)
                        .opacity(locked ? 0 : 1)
                }
            }
            ExecutionAttributesLayout(scale: textSize.scale) {
                field(.effort, label: language.text("推理强度", "Reasoning effort")) {
                    Label(effortTitle, systemImage: "slider.horizontal.3")
                        .fixedSize(horizontal: false, vertical: true)
                }
                field(.fast, label: "Fast") {
                    let fast = settings[keyPath: stage.fast]
                    Label("Fast \(fast == .automatic ? language.text("自动", "Auto") : fast.title(language: language))",
                          systemImage: fast == .off ? "bolt.slash" : fast == .on ? "bolt.fill" : "bolt.badge.clock")
                        .foregroundStyle(fast == .on && !locked ? stage.tint : .secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .appScaledSystemFont(size: 11)
            .foregroundStyle(.secondary)
            Image(systemName: "lock.fill")
            .appScaledSystemFont(size: 10, weight: .medium)
            .foregroundStyle(.secondary)
            .fixedSize()
            .opacity(locked ? 1 : 0)
            .accessibilityHidden(!locked)
            .accessibilityLabel(language.text("综合配置已锁定", "Synthesis configuration locked"))
            .help(locked ? language.text("综合已开始，显示本轮实际配置", "Synthesis has started. Showing the configuration in use.") : scope)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(stage.title(language))
        .onChange(of: ExecutionEditContext(model)) { _ in editingCustomCount = false }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: locked)
    }

    private var countPicker: some View {
        let context = ExecutionEditContext(model)
        let value = settings[keyPath: stage.count]
        let range = (stage == .candidates ? 1 : 0)...model.effectiveMaximumAgentCount
        let choices = Array(Set(Array(range.lowerBound...min(8, range.upperBound)) + [value])).sorted()
        return Group {
            if editingCustomCount {
                ExecutionCountEditor(title: stage.title(language), value: value, range: range,
                    onFinish: { editingCustomCount = false }, autoFocus: true) { count in
                        Task { await model.editExecutionConfiguration(.count(count), stage: stage, context: context) }
                    }
            } else {
                ExecutionCountPopUp(title: "\(stage.title(language)) · \(language.text("数量", "Count"))",
                    value: value, choices: choices, select: { count in
                        Task { await model.editExecutionConfiguration(.count(count), stage: stage, context: context) }
                    }, custom: { editingCustomCount = true })
                    .frame(width: max(32, CGFloat(String(value).count) * 7 + 20) * textSize.scale)
            }
        }
        .id(context)
        .frame(width: 56 * textSize.scale, height: 24 * textSize.scale, alignment: .leading)
        .disabled(locked || model.isRunOperationInProgress)
        .accessibilityIdentifier("configuration.\(stage.rawValue).count")
        .help(scope)
    }

    private func field<Content: View>(_ field: ExecutionField, label: String, @ViewBuilder content: @escaping () -> Content) -> some View {
        let context = ExecutionEditContext(model)
        return ExecutionOptionMenu(title: "\(stage.title(language)) · \(label)",
            identifier: "configuration.\(stage.rawValue).\(field.rawValue)",
            choices: choices(for: field), selection: selection(for: field), select: { value in
            let edit: ExecutionConfigurationEdit
            switch field {
            case .model: edit = .model(value)
            case .effort: edit = .effort(value)
            case .fast:
                guard let mode = RunFastMode(rawValue: value) else { return }
                edit = .fast(mode)
            }
            Task { await model.editExecutionConfiguration(edit, stage: stage, context: context) }
        }) {
            content().padding(.horizontal, 5).frame(minHeight: 24 * textSize.scale).contentShape(Rectangle())
        }
        .id(context)
        .disabled(locked || model.isRunOperationInProgress)
        .accessibilityLabel("\(stage.title(language)) · \(label)")
        .accessibilityValue(value(for: field))
        .accessibilityHint(scope)
        .accessibilityIdentifier("configuration.\(stage.rawValue).\(field.rawValue)")
        .help("\(label): \(value(for: field))\(scope.isEmpty ? "" : "\n\(scope)")")
    }

    private func value(for field: ExecutionField) -> String {
        switch field {
        case .model: modelSelection.isEmpty ? "\(language.text("Codex 默认", "Codex default")) · \(modelTitle)" : modelTitle
        case .effort: effortTitle
        case .fast: settings[keyPath: stage.fast].title(language: language)
        }
    }

    private func selection(for field: ExecutionField) -> String {
        switch field {
        case .model: modelSelection
        case .effort: settings[keyPath: stage.effort]
        case .fast: settings[keyPath: stage.fast].rawValue
        }
    }

    private func choices(for field: ExecutionField) -> [CodexOption] {
        switch field {
        case .model:
            return codexOptionChoices(dynamic: model.codexModelCatalog, current: modelSelection,
                fallbackLabel: language.text("Codex 默认", "Codex default")).map {
                CodexOption(label: $0.value.isEmpty ? $0.label : codexModelDisplayName($0.label), value: $0.value, selected: $0.selected)
            }
        case .effort:
            return codexEffortChoices(dynamic: options?.efforts, current: settings[keyPath: stage.effort],
                                      effective: options?.effectiveEffort, language: language)
        case .fast:
            return RunFastMode.allCases.map { CodexOption(label: $0.title(language: language), value: $0.rawValue, selected: false) }
        }
    }
}

struct ExecutionOptionMenu<Content: View>: View {
    let title: String
    var identifier = ""
    let choices: [CodexOption]
    let selection: String
    let select: (String) -> Void
    @ViewBuilder let label: () -> Content

    var body: some View {
        // Keep SwiftUI's complete, wrapping label; native Menu truncates custom labels.
        label().accessibilityHidden(true)
            .overlay(ExecutionOptionPopUp(title: title, identifier: identifier,
                choices: choices, selection: selection, select: select))
    }
}

private struct ExecutionOptionPopUp: NSViewRepresentable {
    @Environment(\.appTextSize) private var textSize
    @Environment(\.isEnabled) private var enabled
    let title: String
    let identifier: String
    let choices: [CodexOption]
    let selection: String
    let select: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSPopUpButton {
        let button = NSPopUpButton(frame: .zero, pullsDown: false)
        button.isBordered = false
        button.isTransparent = true
        button.autoenablesItems = false
        button.target = context.coordinator
        button.action = #selector(Coordinator.selected(_:))
        button.setAccessibilityElement(true)
        (button.cell as? NSPopUpButtonCell)?.arrowPosition = .noArrow
        return button
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSPopUpButton, context: Context) -> CGSize? {
        guard let width = proposal.width, let height = proposal.height else { return nil }
        return CGSize(width: width, height: height)
    }

    func updateNSView(_ button: NSPopUpButton, context: Context) {
        context.coordinator.parent = self
        if button.itemArray.map(\.title) != choices.map(\.label)
            || button.itemArray.compactMap({ $0.representedObject as? String }) != choices.map(\.value) {
            button.removeAllItems()
            for option in choices {
                let item = NSMenuItem(title: option.label, action: nil, keyEquivalent: "")
                item.representedObject = option.value
                button.menu?.addItem(item)
            }
        }
        button.selectItem(at: choices.firstIndex { $0.value == selection } ?? -1)
        button.font = .systemFont(ofSize: 13 * textSize.scale)
        button.isEnabled = enabled
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.setAccessibilityIdentifier(identifier)
        button.setAccessibilityLabel(title)
    }

    static func dismantleNSView(_ button: NSPopUpButton, coordinator: Coordinator) {
        button.menu?.cancelTracking()
        button.target = nil
        button.action = nil
    }

    @MainActor final class Coordinator: NSObject {
        var parent: ExecutionOptionPopUp
        init(_ parent: ExecutionOptionPopUp) { self.parent = parent }

        @objc func selected(_ button: NSPopUpButton) {
            guard parent.enabled, let value = button.selectedItem?.representedObject as? String,
                  parent.choices.contains(where: { $0.value == value }) else { return }
            parent.select(value)
        }
    }
}

private struct ExecutionCountPopUp: NSViewRepresentable {
    @Environment(\.appTextSize) private var textSize
    @Environment(\.appLanguage) private var language
    @Environment(\.isEnabled) private var enabled
    let title: String
    let value: Int
    let choices: [Int]
    let select: (Int) -> Void
    let custom: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSPopUpButton {
        let button = NSPopUpButton(frame: .zero, pullsDown: false)
        button.isBordered = false
        button.controlSize = .small
        button.autoenablesItems = false
        button.target = context.coordinator
        button.action = #selector(Coordinator.selected(_:))
        (button.cell as? NSPopUpButtonCell)?.altersStateOfSelectedItem = false
        return button
    }

    func updateNSView(_ button: NSPopUpButton, context: Context) {
        context.coordinator.parent = self
        let customTitle = language.text("自定", "Custom")
        if button.itemArray.filter({ !$0.isSeparatorItem }).map(\.tag) != choices + [-1]
            || button.lastItem?.title != customTitle {
            button.removeAllItems()
            for value in choices {
                button.addItem(withTitle: String(value))
                button.lastItem?.tag = value
            }
            button.menu?.addItem(.separator())
            button.addItem(withTitle: customTitle)
            button.lastItem?.tag = -1
        }
        button.font = .monospacedDigitSystemFont(ofSize: 11 * textSize.scale, weight: .medium)
        button.isEnabled = enabled
        (button.cell as? NSPopUpButtonCell)?.arrowPosition = enabled ? .arrowAtCenter : .noArrow
        _ = button.selectItem(withTag: value)
        button.setAccessibilityLabel(title)
    }

    static func dismantleNSView(_ button: NSPopUpButton, coordinator: Coordinator) {
        button.target = nil
        button.action = nil
    }

    @MainActor final class Coordinator: NSObject {
        var parent: ExecutionCountPopUp
        init(_ parent: ExecutionCountPopUp) { self.parent = parent }

        @objc func selected(_ button: NSPopUpButton) {
            guard parent.enabled, let item = button.selectedItem, !item.isSeparatorItem else { return }
            if item.tag == -1 { parent.custom() }
            else { parent.select(item.tag) }
        }
    }
}

struct ExecutionCountEditor: View {
    @Environment(\.appTextSize) private var textSize
    @Environment(\.appLanguage) private var language
    @Environment(\.isEnabled) private var enabled
    let title: String
    let value: Int
    let range: ClosedRange<Int>
    let commit: (Int) -> Void
    let onFinish: () -> Void
    let autoFocus: Bool
    @State private var text: String
    @State private var lastApplied: Int
    @State private var hovered = false
    @FocusState private var focused: Bool

    init(title: String, value: Int, range: ClosedRange<Int>, onFinish: @escaping () -> Void = {},
         autoFocus: Bool = false, commit: @escaping (Int) -> Void) {
        self.title = title; self.value = value; self.range = range; self.commit = commit
        self.onFinish = onFinish; self.autoFocus = autoFocus
        _text = State(initialValue: String(value))
        _lastApplied = State(initialValue: value)
    }

    static func validated(_ text: String, range: ClosedRange<Int>) -> Int? {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty, raw.utf8.allSatisfy({ (48...57).contains($0) }),
              let value = Int(raw), range.contains(value) else { return nil }
        return value
    }

    private var number: Int? { Self.validated(text, range: range) }
    private func update(_ text: String) {
        guard enabled, let number = Self.validated(text, range: range), number != lastApplied else { return }
        lastApplied = number
        commit(number)
    }

    var body: some View {
        TextField("", text: $text).textFieldStyle(.plain)
            .appScaledSystemFont(size: 11, weight: .medium).monospacedDigit()
            .foregroundStyle(enabled ? Color.primary : .secondary)
            .padding(.horizontal, 4 * textSize.scale)
            .frame(width: 36 * textSize.scale, height: 24 * textSize.scale)
            .background(Color.primary.opacity(enabled && (hovered || focused) ? 0.055 : 0),
                        in: RoundedRectangle(cornerRadius: 4))
            .overlay(RoundedRectangle(cornerRadius: 4)
                .stroke(number == nil ? Color.red : Color.primary.opacity(focused ? 0.25 : 0), lineWidth: 1))
            .focused($focused)
            .onAppear { if autoFocus { focused = true } }
            .onHover { hovered = $0 }
            .onChange(of: text, perform: update)
            .onChange(of: value) { value in
                lastApplied = value
                if !focused { text = String(value) }
            }
            .onChange(of: focused) { if !$0 { finish() } }
            .onChange(of: enabled) { if !$0 { lastApplied = value; finish() } }
            .onSubmit(finish)
            .onExitCommand(perform: finish)
            .accessibilityLabel("\(title) · \(language.text("数量", "Count"))")
            .accessibilityIdentifier("configuration.count-input")
    }

    private func finish() {
        text = String(lastApplied)
        focused = false
        onFinish()
    }
}
