import AppKit
import SwiftUI

private enum SettingsSection: String, CaseIterable, Identifiable {
    case computers
    case app
    case conversation
    case storage
    case feedback

    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .computers: return "desktopcomputer"
        case .app: return "gearshape"
        case .conversation: return "bubble.left.and.bubble.right"
        case .storage: return "internaldrive"
        case .feedback: return "quote.bubble"
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .computers: return language.text("设备", "Computers")
        case .app: return language.text("应用", "App")
        case .conversation: return language.text("对话", "Conversation")
        case .storage: return language.text("存储空间", "Storage")
        case .feedback: return language.text("反馈", "Feedback")
        }
    }
}

struct SettingsRootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selection = SettingsSection.computers

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                ForEach(SettingsSection.allCases) { section in
                    Button {
                        selection = section
                    } label: {
                        VStack(spacing: 4) {
                            Image(systemName: section.systemImage)
                                .appScaledSystemFont(size: 17, weight: .medium)
                            Text(section.title(language: model.appLanguage))
                                .appScaledTextStyle(.caption)
                                .lineLimit(1)
                        }
                        .foregroundStyle(
                            selection == section
                                ? model.appAccentColor.color
                                : Color.secondary
                        )
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .contentShape(Rectangle())
                        .background(
                            selection == section
                                ? model.appAccentColor.color.opacity(0.12)
                                : Color.clear,
                            in: RoundedRectangle(cornerRadius: 6)
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(
                        selection == section ? .isSelected : []
                    )
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)

            Divider()

            settingsContent
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .appScaledTextStyle(.body)
        .frame(width: 700, height: 720)
        .background(
            SettingsWindowTitle(
                title: model.appLanguage.text(
                    "Parallel Codex 设置",
                    "Parallel Codex Settings"
                )
            )
            .frame(width: 0, height: 0)
        )
    }

    @ViewBuilder
    private var settingsContent: some View {
        switch selection {
        case .computers:
            ComputerSettingsView()
        case .app:
            AppPreferencesView()
        case .conversation:
            GeneralConversationSettingsView()
        case .storage:
            StorageManagementView()
        case .feedback:
            FeedbackSettingsView()
        }
    }
}

private struct SettingsWindowTitle: NSViewRepresentable {
    let title: String

    func makeNSView(context: Context) -> NSView {
        NSView(frame: .zero)
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async {
            view.window?.title = title
        }
    }
}
