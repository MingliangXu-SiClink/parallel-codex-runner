import SwiftUI

struct AppPreferencesView: View {
    @EnvironmentObject private var model: AppModel
    @State private var trashPresented = false

    var body: some View {
        Form {
            Section(model.appLanguage.text("语言", "Language")) {
                Picker(model.appLanguage.text("应用语言", "App language"), selection: languageBinding) {
                    ForEach(AppLanguage.allCases) { language in
                        Text(language.title).tag(language)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section(model.appLanguage.text("显示与界面", "Appearance")) {
                Picker(model.appLanguage.text("外观", "Theme"), selection: colorModeBinding) {
                    ForEach(AppColorMode.allCases) { mode in
                        Text(mode.title(language: model.appLanguage)).tag(mode)
                    }
                }
                .pickerStyle(.segmented)

                Picker(model.appLanguage.text("主题色", "Accent color"), selection: accentColorBinding) {
                    ForEach(AppAccentColor.allCases) { accent in
                        Label {
                            Text(accent.title(language: model.appLanguage))
                        } icon: {
                            Circle()
                                .fill(accent.color)
                                .frame(width: 10, height: 10)
                        }
                        .tag(accent)
                    }
                }
                .pickerStyle(.menu)

                Picker(model.appLanguage.text("文字大小", "Text size"), selection: textSizeBinding) {
                    ForEach(AppTextSize.allCases) { size in
                        Text(size.title(language: model.appLanguage)).tag(size)
                    }
                }
                .pickerStyle(.segmented)

                Toggle(
                    model.appLanguage.text("默认展开工作过程", "Expand work by default"),
                    isOn: workExpandedBinding
                )
            }

            Section(model.appLanguage.text("通知", "Notifications")) {
                Toggle(
                    model.appLanguage.text("任务完成通知", "Completion notifications"),
                    isOn: completionNotificationsBinding
                )
                Text(model.appLanguage.text(
                    "所有 Agent 都结束后，在应用不位于前台时通知。",
                    "Notify when all Agents finish while the app is in the background."
                ))
                .appScaledTextStyle(.caption)
                .foregroundStyle(.secondary)

                Button(model.appLanguage.text("打开系统通知设置", "Open Notification Settings")) {
                    guard let url = URL(
                        string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
                    ) else { return }
                    NSWorkspace.shared.open(url)
                }
            }

            Section(model.appLanguage.text("对话", "Conversations")) {
                Button { trashPresented = true } label: {
                    Label(model.appLanguage.text("最近删除", "Recently Deleted"), systemImage: "trash")
                }
                .accessibilityIdentifier("preferences.conversation-trash")
            }

            Section(model.appLanguage.text("软件更新", "Software Update")) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(model.appLanguage.text("当前版本", "Current version"))
                            .foregroundStyle(.secondary)
                        Text(model.currentMacOSVersionLabel)
                            .appScaledTextStyle(.body, design: .monospaced)
                            .textSelection(.enabled)
                    }
                    Spacer()
                    Button {
                        Task { await model.checkForMacOSUpdate() }
                    } label: {
                        if model.macOSUpdateChecking {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Text(model.appLanguage.text("检查更新", "Check for Updates"))
                        }
                    }
                    .disabled(
                        model.macOSUpdateChecking || model.macOSUpdateDownloading || model.macOSUpdateRestarting
                    )
                }

                if let update = model.macOSUpdateAvailable {
                    VStack(alignment: .leading, spacing: 7) {
                        Label(
                            model.appLanguage.text(
                                model.macOSPreparedUpdate == nil ? "发现版本 \(update.versionName)" : "版本 \(update.versionName) 已准备好",
                                model.macOSPreparedUpdate == nil ? "Version \(update.versionName) is available" : "Version \(update.versionName) is ready"
                            ),
                            systemImage: "arrow.down.circle.fill"
                        )
                        .appScaledTextStyle(.headline)
                        .foregroundStyle(model.appAccentColor.color)

                        if !update.releaseNotes.isEmpty {
                            Text(update.releaseNotes)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }

                        HStack(spacing: 10) {
                            if model.macOSPreparedUpdate != nil {
                                Button {
                                    Task { await model.restartToInstallMacOSUpdate() }
                                } label: {
                                    Label(model.appLanguage.text("重启以更新", "Restart to Update"), systemImage: "arrow.clockwise")
                                }
                                .buttonStyle(.borderedProminent)
                                .controlSize(.small)
                                .disabled(model.macOSUpdateRestarting)
                                .accessibilityIdentifier("update.restart")
                            } else {
                                Button {
                                    Task { await model.downloadMacOSUpdate() }
                                } label: {
                                    if model.macOSUpdateDownloading {
                                        HStack(spacing: 6) {
                                            ProgressView().controlSize(.small)
                                            Text(model.appLanguage.text("正在准备更新", "Preparing Update"))
                                        }
                                    } else {
                                        Label(model.appLanguage.text("下载更新", "Download Update"), systemImage: "arrow.down.to.line")
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .controlSize(.small)
                                .disabled(model.macOSUpdateDownloading)
                                .accessibilityIdentifier("update.download")
                            }
                        }

                        if model.macOSPreparedUpdate != nil {
                            Text(model.appLanguage.text(
                                "重启后即可使用新版。后台 Agent 会继续运行。",
                                "The new version will open after restarting. Background Agents will keep running."
                            ))
                            .appScaledTextStyle(.caption)
                            .foregroundStyle(.secondary)
                        }
                    }
                } else if model.macOSUpdateChecked,
                          model.macOSUpdateError == nil,
                          !model.macOSUpdateChecking {
                    Label(
                        model.appLanguage.text("已是最新版本", "Parallel Codex is up to date"),
                        systemImage: "checkmark.circle.fill"
                    )
                    .foregroundStyle(.green)
                }

                if let error = model.macOSUpdateError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }

            }
        }
        .formStyle(.grouped)
        .padding(14)
        .sheet(isPresented: $trashPresented) {
            ConversationTrashView(store: model.conversationTrash).environmentObject(model)
        }
        .task {
            if !model.macOSUpdateChecked {
                await model.checkForMacOSUpdate(silent: true)
            }
        }
    }

    private var languageBinding: Binding<AppLanguage> {
        Binding(
            get: { model.appLanguage },
            set: { value in
                DispatchQueue.main.async { model.setAppLanguage(value) }
            }
        )
    }

    private var colorModeBinding: Binding<AppColorMode> {
        Binding(
            get: { model.appColorMode },
            set: { value in
                DispatchQueue.main.async { model.setAppColorMode(value) }
            }
        )
    }

    private var accentColorBinding: Binding<AppAccentColor> {
        Binding(
            get: { model.appAccentColor },
            set: { value in
                DispatchQueue.main.async { model.setAppAccentColor(value) }
            }
        )
    }

    private var textSizeBinding: Binding<AppTextSize> {
        Binding(
            get: { model.appTextSize },
            set: { value in
                DispatchQueue.main.async { model.setAppTextSize(value) }
            }
        )
    }

    private var workExpandedBinding: Binding<Bool> {
        Binding(
            get: { model.workExpandedByDefault },
            set: { value in
                DispatchQueue.main.async { model.setWorkExpandedByDefault(value) }
            }
        )
    }

    private var completionNotificationsBinding: Binding<Bool> {
        Binding(
            get: { model.completionNotificationsEnabled },
            set: { value in
                DispatchQueue.main.async {
                    model.setCompletionNotificationsEnabled(value)
                }
            }
        )
    }
}
