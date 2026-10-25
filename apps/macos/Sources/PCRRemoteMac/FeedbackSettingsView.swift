import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct FeedbackSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        FeedbackSettingsContent(
            feedback: model.feedback,
            diagnostics: model.feedbackDiagnostics()
        )
    }
}

private struct FeedbackSettingsContent: View {
    @Environment(\.appLanguage) private var appLanguage
    @Environment(\.appAccentColor) private var appAccentColor
    @ObservedObject var feedback: FeedbackModel
    let diagnostics: [String: String]

    @State private var kind = FeedbackKind.bug
    @State private var description = ""
    @State private var descriptionEditorHeight: CGFloat = 92
    @State private var screenshotURLs: [URL] = []
    @State private var excludedDiagnostics: Set<String> = []
    @State private var handledSubmissionID: String?
    @State private var expandedFeedback: Set<String> = []
    @State private var noteDrafts: [String: String] = [:]

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Form {
                composeSection
                if !feedback.pending.isEmpty {
                    pendingSection
                }
                historySection
            }
            .formStyle(.grouped)
            .scrollContentBackground(.hidden)
            .padding(14)
        }
        .task {
            await feedback.refresh()
        }
        .onChange(of: feedback.lastQueuedSubmissionID) { value in
            guard let value, value != handledSubmissionID else { return }
            handledSubmissionID = value
            description = ""
            screenshotURLs = []
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "quote.bubble")
                .appScaledSystemFont(size: 22, weight: .medium)
                .foregroundStyle(appAccentColor.color)
                .frame(width: 34, height: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(appLanguage.text("反馈", "Feedback"))
                    .appScaledTextStyle(.title2, weight: .semibold)
                Text(appLanguage.text(
                    "报告问题或提出改进建议",
                    "Report a problem or suggest an improvement"
                ))
                    .appScaledTextStyle(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if feedback.loading {
                ProgressView()
                    .controlSize(.small)
            } else {
                Button {
                    Task { await feedback.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help(appLanguage.text("刷新反馈", "Refresh feedback"))
            }
        }
        .padding(20)
    }

    private var composeSection: some View {
        Section(appLanguage.text("新反馈", "New Feedback")) {
            Picker(appLanguage.text("类型", "Type"), selection: $kind) {
                ForEach(FeedbackKind.allCases) { option in
                    Text(option.title(language: appLanguage)).tag(option)
                }
            }
            .pickerStyle(.segmented)

            VStack(alignment: .leading, spacing: 7) {
                Text(appLanguage.text("说明", "Description"))
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                NativeMultilineEditor(
                    text: $description,
                    measuredHeight: $descriptionEditorHeight,
                    minimumHeight: 92,
                    maximumHeight: 150,
                    accessibilityLabel: appLanguage.text("反馈说明", "Feedback description"),
                    onSubmit: nil
                )
                    .frame(height: descriptionEditorHeight)
                    .padding(5)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay {
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(.quaternary, lineWidth: 1)
                    }
                HStack {
                    Text(appLanguage.text(
                        "请说明发生了什么，或希望如何改进。",
                        "Describe what happened or what you would like to change."
                    ))
                    Spacer()
                    Text("\(description.count)/8000")
                }
                .appScaledTextStyle(.caption2)
                .foregroundStyle(.tertiary)
            }

            if !screenshotURLs.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 10) {
                        ForEach(screenshotURLs, id: \.self) { url in
                            FeedbackScreenshotThumbnail(url: url) {
                                screenshotURLs.removeAll { $0 == url }
                            }
                        }
                    }
                }
                .scrollIndicators(.hidden)
            }

            HStack {
                Button {
                    chooseScreenshots()
                } label: {
                    Label(
                        screenshotURLs.isEmpty
                            ? appLanguage.text("添加截图", "Add Screenshots")
                            : appLanguage.text("继续添加", "Add Another"),
                        systemImage: "photo.badge.plus"
                    )
                }
                .disabled(screenshotURLs.count >= 3 || feedback.submitting)
                Spacer()
                Button {
                    let included = diagnostics.filter {
                        !excludedDiagnostics.contains($0.key)
                    }
                    Task {
                        await feedback.submit(
                            kind: kind,
                            description: String(description.prefix(8_000)),
                            screenshotURLs: screenshotURLs,
                            diagnostics: included
                        )
                    }
                } label: {
                    if feedback.submitting {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text(appLanguage.text("提交", "Submit"))
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || feedback.submitting
                )
            }

            DisclosureGroup(appLanguage.text("包含的信息", "Included Details")) {
                VStack(alignment: .leading, spacing: 7) {
                    Text(
                        appLanguage.text(
                            "不会包含提问、对话、源代码、密钥或完整本地路径。",
                            "Prompts, conversations, source code, secrets, and full local paths are never included."
                        )
                    )
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                    ForEach(diagnostics.keys.sorted(), id: \.self) { key in
                        Toggle(isOn: diagnosticBinding(key)) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(diagnosticLabel(key, language: appLanguage))
                                Text(diagnostics[key] ?? "")
                                    .appScaledTextStyle(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                    .textSelection(.enabled)
                            }
                        }
                        .toggleStyle(.checkbox)
                    }
                }
                .padding(.top, 6)
            }

            if let message = feedback.errorMessage, !message.isEmpty {
                Label(message, systemImage: "exclamationmark.circle.fill")
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
        }
    }

    private var pendingSection: some View {
        Section(appLanguage.text("等待发送", "Waiting to Send")) {
            ForEach(feedback.pending) { pending in
                VStack(alignment: .leading, spacing: 7) {
                    HStack {
                        Text(feedbackKindTitle(pending.kind, language: appLanguage))
                            .appScaledTextStyle(.headline)
                        Spacer()
                        Text(pendingStatusTitle(pending.status, language: appLanguage))
                            .appScaledTextStyle(.caption, weight: .medium)
                            .foregroundStyle(
                                pending.status == .failed ? Color.red : Color.secondary
                            )
                    }
                    Text(pending.description)
                        .lineLimit(3)
                    if !pending.attachments.isEmpty {
                        Text(appLanguage.text(
                            "已在此 Mac 私密保存 \(pending.attachments.count) 张截图",
                            "\(pending.attachments.count) screenshots saved privately on this Mac"
                        ))
                            .appScaledTextStyle(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if !pending.error.isEmpty {
                        Text(pending.error)
                            .appScaledTextStyle(.caption)
                            .foregroundStyle(.red)
                            .textSelection(.enabled)
                    }
                    HStack {
                        Spacer()
                        if pending.status == .failed {
                            Button(appLanguage.text("重试", "Retry")) {
                                feedback.retry(pending.id)
                            }
                        }
                        Button(appLanguage.text("删除", "Delete"), role: .destructive) {
                            feedback.removePending(pending.id)
                        }
                    }
                    .buttonStyle(.borderless)
                }
                .padding(.vertical, 4)
            }
        }
    }

    private var historySection: some View {
        Section(appLanguage.text("我的反馈", "My Feedback")) {
            if feedback.items.isEmpty, !feedback.loading {
                Text(appLanguage.text("还没有提交反馈。", "No feedback submitted yet."))
                    .foregroundStyle(.secondary)
            }
            ForEach(feedback.items) { item in
                feedbackRow(item)
            }
            if feedback.hasMore {
                HStack {
                    Spacer()
                    Button(appLanguage.text("加载更早的反馈", "Load Earlier Feedback")) {
                        Task { await feedback.refresh(reset: false) }
                    }
                    Spacer()
                }
            }
        }
    }

    @ViewBuilder
    private func feedbackRow(_ item: FeedbackRecord) -> some View {
        DisclosureGroup(
            isExpanded: Binding(
                get: { expandedFeedback.contains(item.feedbackId) },
                set: { expanded in
                    if expanded {
                        expandedFeedback.insert(item.feedbackId)
                    } else {
                        expandedFeedback.remove(item.feedbackId)
                    }
                }
            )
        ) {
            VStack(alignment: .leading, spacing: 9) {
                Text(item.description)
                    .textSelection(.enabled)
                Text(appLanguage.text(
                    "反馈 #\(item.feedbackId.replacingOccurrences(of: "feedback-", with: "").prefix(8).uppercased())",
                    "Feedback #\(item.feedbackId.replacingOccurrences(of: "feedback-", with: "").prefix(8).uppercased())"
                ))
                    .appScaledTextStyle(.caption2)
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
                if !item.attachments.isEmpty {
                    Label(
                        appLanguage.text(
                            "附有 \(item.attachments.count) 张截图",
                            "\(item.attachments.count) screenshots attached"
                        ),
                        systemImage: "photo.on.rectangle"
                    )
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                }
                if !item.developerReply.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(appLanguage.text("开发者回复", "Developer Reply"))
                            .appScaledTextStyle(.caption, weight: .semibold)
                            .foregroundStyle(appAccentColor.color)
                        Text(item.developerReply)
                            .textSelection(.enabled)
                        if !item.fixedVersion.isEmpty {
                            Text(appLanguage.text(
                                "已在 \(item.fixedVersion) 修复",
                                "Fixed in \(item.fixedVersion)"
                            ))
                                .appScaledTextStyle(.caption, weight: .semibold)
                                .foregroundStyle(appAccentColor.color)
                        }
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(appAccentColor.color.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 7))
                }
                ForEach(item.notes) { note in
                    Text(appLanguage.text("补充：\(note.text)", "Added: \(note.text)"))
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                let statusHistory = item.activity.filter { $0.eventType == "status" }
                if !statusHistory.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(appLanguage.text("状态记录", "Status History"))
                            .appScaledTextStyle(.caption, weight: .semibold)
                            .foregroundStyle(.secondary)
                        ForEach(statusHistory) { activity in
                            Text(
                                "\(feedbackStatusTitle(activity.status, language: appLanguage)) · " +
                                feedbackDate(activity.createdAt, language: appLanguage)
                            )
                            .appScaledTextStyle(.caption)
                            .foregroundStyle(.secondary)
                        }
                    }
                }
                if !["withdrawn", "declined", "fixed"].contains(item.status) {
                    TextField(
                        appLanguage.text("补充更多信息", "Add more information"),
                        text: noteBinding(item.feedbackId),
                        axis: .vertical
                    )
                    .lineLimit(2...5)
                    HStack {
                        if item.status == "submitted" {
                            Button(appLanguage.text("撤回", "Withdraw"), role: .destructive) {
                                Task { await feedback.withdraw(feedbackID: item.feedbackId) }
                            }
                        }
                        Spacer()
                        Button(appLanguage.text("添加补充", "Add Note")) {
                            let note = noteDrafts[item.feedbackId] ?? ""
                            Task {
                                await feedback.addNote(
                                    feedbackID: item.feedbackId,
                                    text: note
                                )
                                if feedback.errorMessage == nil {
                                    noteDrafts[item.feedbackId] = ""
                                }
                            }
                        }
                        .disabled(
                            (noteDrafts[item.feedbackId] ?? "")
                                .trimmingCharacters(in: .whitespacesAndNewlines)
                                .isEmpty
                                || feedback.actioningFeedbackID != nil
                        )
                    }
                    .buttonStyle(.borderless)
                }
            }
            .padding(.top, 7)
        } label: {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.description.components(separatedBy: .newlines).first
                        ?? appLanguage.text("反馈", "Feedback"))
                        .lineLimit(1)
                    Text(
                        "\(feedbackKindTitle(item.kind, language: appLanguage)) · "
                            + feedbackDate(item.updatedAt, language: appLanguage)
                    )
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(feedbackStatusTitle(item.status, language: appLanguage))
                    .appScaledTextStyle(.caption, weight: .medium)
                    .foregroundStyle(feedbackStatusColor(
                        item.status,
                        accentColor: appAccentColor.color
                    ))
            }
        }
    }

    private func chooseScreenshots() {
        let panel = NSOpenPanel()
        panel.title = appLanguage.text("最多选择三张截图", "Choose up to three screenshots")
        panel.allowedContentTypes = [.png, .jpeg, .webP]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.begin { response in
            guard response == .OK else { return }
            let remaining = max(0, 3 - screenshotURLs.count)
            screenshotURLs = Array(
                (screenshotURLs + Array(panel.urls.prefix(remaining)))
                    .uniqued()
                    .prefix(3)
            )
        }
    }

    private func diagnosticBinding(_ key: String) -> Binding<Bool> {
        Binding(
            get: { !excludedDiagnostics.contains(key) },
            set: { included in
                if included {
                    excludedDiagnostics.remove(key)
                } else {
                    excludedDiagnostics.insert(key)
                }
            }
        )
    }

    private func noteBinding(_ feedbackID: String) -> Binding<String> {
        Binding(
            get: { noteDrafts[feedbackID] ?? "" },
            set: { noteDrafts[feedbackID] = String($0.prefix(4_000)) }
        )
    }
}

private struct FeedbackScreenshotThumbnail: View {
    @Environment(\.appLanguage) private var appLanguage
    let url: URL
    let remove: () -> Void

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Group {
                if let image = NSImage(contentsOf: url) {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFill()
                } else {
                    Image(systemName: "photo")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(width: 82, height: 70)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 7))

            Button(action: remove) {
                Image(systemName: "xmark.circle.fill")
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, .black.opacity(0.68))
            }
            .buttonStyle(.plain)
            .padding(4)
            .help(appLanguage.text("移除截图", "Remove screenshot"))
        }
    }
}

private func feedbackKindTitle(_ value: String, language: AppLanguage) -> String {
    FeedbackKind(rawValue: value)?.title(language: language)
        ?? language.text("问题", "Problem")
}

private func pendingStatusTitle(
    _ value: FeedbackSubmissionStatus,
    language: AppLanguage
) -> String {
    switch value {
    case .pending: return language.text("等待发送", "Waiting")
    case .retrying: return language.text("正在重试", "Retrying")
    case .failed: return language.text("发送失败", "Failed")
    }
}

private func feedbackStatusTitle(_ value: String, language: AppLanguage) -> String {
    switch value {
    case "confirmed": return language.text("已确认", "Confirmed")
    case "in_progress": return language.text("处理中", "In Progress")
    case "fixed": return language.text("已修复", "Fixed")
    case "declined": return language.text("暂不处理", "Not Planned")
    case "withdrawn": return language.text("已撤回", "Withdrawn")
    default: return language.text("已提交", "Submitted")
    }
}

private func feedbackStatusColor(_ value: String, accentColor: Color) -> Color {
    switch value {
    case "fixed": return .green
    case "declined", "withdrawn": return .secondary
    default: return accentColor
    }
}

private func feedbackDate(_ value: String, language: AppLanguage) -> String {
    guard let date = parseInternetDate(value) else {
        return language.text("最近", "Recently")
    }
    return date.formatted(
        .dateTime
            .locale(language.locale)
            .year()
            .month(.abbreviated)
            .day()
            .hour()
            .minute()
    )
}

private func diagnosticLabel(_ key: String, language: AppLanguage) -> String {
    switch key {
    case "app_platform": return language.text("应用平台", "App platform")
    case "app_version": return language.text("应用版本", "App version")
    case "app_build": return language.text("构建版本", "App build")
    case "os_version": return "macOS"
    case "device_model": return language.text("Mac 型号", "Mac")
    case "mac_device_id": return language.text("设备标识", "Computer identifier")
    case "mac_name": return language.text("设备名称", "Computer name")
    case "daemon_version": return language.text("服务端版本", "Connected server version")
    case "protocol_version": return language.text("连接协议", "Connection protocol")
    case "workspace_id": return language.text("工作区标识", "Workspace identifier")
    case "workspace_name": return language.text("工作区名称", "Workspace name")
    case "run_id": return language.text("任务标识", "Task identifier")
    case "agent": return "Agent"
    default: return key.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
