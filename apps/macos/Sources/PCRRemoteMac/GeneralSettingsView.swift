import SwiftUI

struct GeneralConversationSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "bubble.left.and.bubble.right")
                    .appScaledSystemFont(size: 22, weight: .medium)
                    .foregroundStyle(model.appAccentColor.color)
                    .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.appLanguage.text("默认对话设置", "Default Conversation Settings"))
                        .appScaledTextStyle(.title2, weight: .semibold)
                    Text(model.appLanguage.text(
                        "适用于 \(targetName) 上新添加的工作区",
                        "Applies to new workspaces on \(targetName)"
                    ))
                        .appScaledTextStyle(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(20)

            Divider()

            ScrollView {
                settingsSections
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .disabled(!model.defaultSettingsLoaded)

            Divider()
            HStack(spacing: 8) {
                if let error = model.defaultSettingsError, !error.isEmpty {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(error)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                } else if let error = model.defaultSettingsOptionsError,
                          !error.isEmpty {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(error)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    Button(model.appLanguage.localized("Reload Options")) {
                        Task { await model.reloadDefaultModelOptions() }
                    }
                } else if model.defaultSettingsLoading {
                    ProgressView()
                        .controlSize(.small)
                    Text(model.appLanguage.localized("Refreshing settings…"))
                        .foregroundStyle(.secondary)
                } else if model.defaultSettingsSaving {
                    ProgressView()
                        .controlSize(.small)
                    Text(model.appLanguage.localized("Saving…"))
                        .foregroundStyle(.secondary)
                } else {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text(model.appLanguage.text(
                        "\(targetName) 上新添加的工作区将使用这些设置",
                        "New workspaces on \(targetName) will use these settings"
                    ))
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .appScaledTextStyle(.caption)
            .padding(.horizontal, 20)
            .frame(minHeight: 42)
            .background(.bar)
        }
        .task {
            await model.loadDefaultConversationSettings(force: true)
        }
        .task(id: model.defaultSettings) {
            await Task.yield()
            guard !Task.isCancelled else { return }
            model.scheduleDefaultConversationSettingsSave()
        }
    }

    @ViewBuilder
    private var settingsSections: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 24) {
                VStack(alignment: .leading, spacing: 22) {
                    runSection(density: .compact)
                    subagentsSection(density: .compact)
                    workspaceSection(density: .compact)
                }
                .frame(minWidth: 302, maxWidth: .infinity, alignment: .topLeading)
                .accessibilityElement(children: .contain)

                VStack(alignment: .leading, spacing: 22) {
                    codexSection(density: .compact)
                    synthesisSection(density: .compact)
                }
                .frame(minWidth: 302, maxWidth: .infinity, alignment: .topLeading)
                .accessibilityElement(children: .contain)
            }
            .frame(minWidth: 628, maxWidth: .infinity, alignment: .topLeading)

            VStack(alignment: .leading, spacing: 22) {
                runSection(density: .regular)
                codexSection(density: .regular)
                synthesisSection(density: .regular)
                subagentsSection(density: .regular)
                workspaceSection(density: .regular)
            }
        }
    }

    private func runSection(density: RunSettingsGridDensity) -> some View {
        RunSettingsSection("Run") {
            RunSettingsGrid(density: density) {
                RunSettingsRow("Candidates") {
                    AgentCountField(
                        value: $model.defaultSettings.agents,
                        range: 1...model.effectiveMaximumAgentCount
                    )
                }
                RunSettingsRow("Execution") {
                    Picker(
                        model.appLanguage.localized("Execution"),
                        selection: $model.defaultSettings.execution
                    ) {
                        ForEach(RunExecution.allCases) { option in
                            Text(option.title(language: model.appLanguage)).tag(option)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                }
                RunSettingsRow("Max parallel") {
                    HStack(spacing: 7) {
                        Toggle(
                            model.appLanguage.localized("Automatic"),
                            isOn: $model.defaultSettings.automaticMaxParallel
                        )
                        .labelsHidden()
                        .toggleStyle(.switch)
                        Text(model.appLanguage.text("自动", "Auto"))
                            .appScaledTextStyle(.caption)
                            .foregroundStyle(.secondary)
                        AgentCountField(
                            value: Binding(
                                get: { model.defaultSettings.displayedMaxParallel },
                                set: { model.defaultSettings.maxParallel = $0 }
                            ),
                            range: 1...model.effectiveMaximumAgentCount
                        )
                        .disabled(
                            model.defaultSettings.automaticMaxParallel
                                || model.defaultSettings.execution == .serial
                        )
                    }
                }
                RunSettingsRow("Recommend by") {
                    Picker(
                        model.appLanguage.localized("Recommend by"),
                        selection: $model.defaultSettings.recommendation
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
                            value: $model.defaultSettings.followUpDelay,
                            range: 0...3600
                        )
                        Text(model.appLanguage.text("秒", "sec"))
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func codexSection(density: RunSettingsGridDensity) -> some View {
        RunSettingsSection("Codex") {
            RunSettingsGrid(density: density) {
                RunSettingsRow("Model") {
                    Picker(model.appLanguage.localized("Model"), selection: primaryModelBinding) {
                        ForEach(primaryModels) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                }
                RunSettingsRow("Effort") {
                    Picker(
                        model.appLanguage.localized("Effort"),
                        selection: $model.defaultSettings.effort
                    ) {
                        ForEach(primaryEfforts) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                }
                RunSettingsRow("Fast mode") {
                    Picker(
                        model.appLanguage.localized("Fast mode"),
                        selection: $model.defaultSettings.fast
                    ) {
                        ForEach(RunFastMode.allCases) { option in
                            Text(fastModeTitle(
                                option,
                                options: model.defaultPrimaryModelOptions
                            ))
                            .tag(option)
                        }
                    }
                    .labelsHidden()
                }
            }
        }
    }

    private func synthesisSection(density: RunSettingsGridDensity) -> some View {
        RunSettingsSection("Synthesis") {
            RunSettingsGrid(density: density) {
                RunSettingsRow("Candidates") {
                    AgentCountField(
                        value: $model.defaultSettings.synthesisAgents,
                        range: 0...model.effectiveMaximumAgentCount
                    )
                }
                RunSettingsRow("Model") {
                    Picker(
                        model.appLanguage.localized("Synthesis model"),
                        selection: synthesisModelBinding
                    ) {
                        ForEach(synthesisModels) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                }
                RunSettingsRow("Effort") {
                    Picker(
                        model.appLanguage.localized("Synthesis effort"),
                        selection: $model.defaultSettings.synthesisEffort
                    ) {
                        ForEach(synthesisEfforts) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                }
                RunSettingsRow("Fast mode") {
                    Picker(
                        model.appLanguage.localized("Synthesis fast mode"),
                        selection: $model.defaultSettings.synthesisFast
                    ) {
                        ForEach(RunFastMode.allCases) { option in
                            Text(fastModeTitle(
                                option,
                                options: model.defaultSynthesisModelOptions
                            ))
                            .tag(option)
                        }
                    }
                    .labelsHidden()
                }
            }
        }
    }

    private func subagentsSection(density: RunSettingsGridDensity) -> some View {
        RunSettingsSection("Subagents") {
            RunSettingsGrid(density: density) {
                RunSettingsToggleRow(
                    "Allow Agents to create subagents",
                    isOn: $model.defaultSettings.subagents
                )
                if model.defaultSettings.subagents {
                    RunSettingsRow("Subagent limit") {
                        AgentCountField(
                            value: $model.defaultSettings.subagentsLimit,
                            range: 1...model.effectiveMaximumAgentCount
                        )
                    }
                }
            }
        }
    }

    private func workspaceSection(density: RunSettingsGridDensity) -> some View {
        RunSettingsSection("Workspace") {
            RunSettingsGrid(density: density) {
                RunSettingsToggleRow(
                    "Apply selected Agent",
                    isOn: $model.defaultSettings.syncBack
                )
                RunSettingsToggleRow(
                    "Keep candidate workspaces",
                    isOn: $model.defaultSettings.keepWorkspaces
                )
            }
        }
    }

    private var targetName: String {
        model.selectedMachine?.name
            ?? model.appLanguage.text("当前设备", "the selected computer")
    }

    private var primaryModels: [CodexOption] {
        codexOptionChoices(
            dynamic: primaryModelCatalog,
            current: model.defaultSettings.model,
            fallbackLabel: localizedDefaultModelLabel(
                model.defaultPrimaryModelOptions?.effectiveModel
                    ?? model.defaultSynthesisModelOptions?.effectiveModel,
                language: model.appLanguage
            )
        )
    }

    private var primaryEfforts: [CodexOption] {
        codexEffortChoices(
            dynamic: model.defaultPrimaryModelOptions?.efforts,
            current: model.defaultSettings.effort,
            effective: model.defaultPrimaryModelOptions?.effectiveEffort,
            language: model.appLanguage
        )
    }

    private var synthesisModels: [CodexOption] {
        codexOptionChoices(
            dynamic: synthesisModelCatalog,
            current: model.defaultSettings.synthesisModel,
            fallbackLabel: localizedDefaultModelLabel(
                model.defaultSynthesisModelOptions?.effectiveModel
                    ?? model.defaultPrimaryModelOptions?.effectiveModel,
                language: model.appLanguage
            )
        )
    }

    private var synthesisEfforts: [CodexOption] {
        codexEffortChoices(
            dynamic: model.defaultSynthesisModelOptions?.efforts,
            current: model.defaultSettings.synthesisEffort,
            effective: model.defaultSynthesisModelOptions?.effectiveEffort,
            language: model.appLanguage
        )
    }

    private var primaryModelCatalog: [CodexOption]? {
        let primary = model.defaultPrimaryModelOptions?.models ?? []
        return primary.isEmpty ? model.defaultSynthesisModelOptions?.models : primary
    }

    private var synthesisModelCatalog: [CodexOption]? {
        let synthesis = model.defaultSynthesisModelOptions?.models ?? []
        return synthesis.isEmpty ? model.defaultPrimaryModelOptions?.models : synthesis
    }

    private var primaryModelBinding: Binding<String> {
        Binding(
            get: { model.defaultSettings.model },
            set: { value in Task { await model.updateDefaultPrimaryModel(value) } }
        )
    }

    private var synthesisModelBinding: Binding<String> {
        Binding(
            get: { model.defaultSettings.synthesisModel },
            set: { value in Task { await model.updateDefaultSynthesisModel(value) } }
        )
    }

    private func fastModeTitle(
        _ mode: RunFastMode,
        options: CodexModelOptions?
    ) -> String {
        mode == .automatic
            ? localizedAutomaticCodexLabel(
                options?.effectiveFast,
                language: model.appLanguage
            )
            : mode.title(language: model.appLanguage)
    }
}
