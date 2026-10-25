import SwiftUI

struct ConversationDeleteView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let request: ConversationDeleteRequest
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(model.appLanguage.text("删除对话？", "Delete conversation?"))
                .appScaledSystemFont(size: 17, weight: .semibold)
            Text(localizedConversationTitle(request.conversation.title, language: model.appLanguage))
                .appScaledTextStyle(.body).lineLimit(3)
            Text(model.appLanguage.text(
                "对话会移入这台 Mac 的“最近删除”，其他设备上的历史不受影响。项目文件和工作副本会保留。",
                "The conversation will move to Recently Deleted on this Mac. History on other devices, project files and working copies will be kept."))
                .appScaledTextStyle(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let message = error ?? blockReason {
                Text(message).appScaledTextStyle(.callout).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button(model.appLanguage.text("取消", "Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(model.appLanguage.text("删除", "Delete"), role: .destructive) {
                    do { try model.deleteConversation(request); dismiss() }
                    catch { self.error = model.appLanguage.userMessage(for: error) }
                }
                .disabled(blockReason != nil)
                .foregroundStyle(.red)
                .accessibilityIdentifier("conversation.delete.confirm")
            }
        }
        .padding(24).frame(width: 420)
    }

    private var blockReason: String? {
        let current = conversationMatchingNavigationID(request.conversation.id, in: model.workspaceConversations)
            ?? request.conversation
        return model.conversationDeleteBlockReason(current)
    }
}

struct ConversationTrashView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: ConversationTrashStore
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(model.appLanguage.text("最近删除", "Recently Deleted"))
                    .appScaledSystemFont(size: 17, weight: .semibold)
                Spacer()
                Text(model.appLanguage.text("仅此 Mac", "On this Mac"))
                    .appScaledTextStyle(.caption).foregroundStyle(.secondary)
            }
            if store.loadError != nil {
                Text(model.appLanguage.text("无法读取本地对话状态。", "Local conversation state could not be read."))
                    .foregroundStyle(.red)
            } else if store.records.isEmpty {
                Text(model.appLanguage.text("没有已删除的对话", "No deleted conversations"))
                    .foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(store.records.sorted { $0.deletedAt > $1.deletedAt }) { record in
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(localizedConversationTitle(record.title, language: model.appLanguage))
                                        .appScaledTextStyle(.body).lineLimit(2)
                                    Text([record.workspaceName, record.machineName].filter { !$0.isEmpty }.joined(separator: " · "))
                                        .appScaledTextStyle(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    Text(record.deletedAt.formatted(date: .abbreviated, time: .shortened))
                                        .appScaledTextStyle(.caption).foregroundStyle(.tertiary)
                                }
                                Spacer(minLength: 8)
                                Button {
                                    do { try model.restoreDeletedConversation(record); error = nil }
                                    catch { self.error = model.appLanguage.userMessage(for: error) }
                                } label: {
                                    Label(model.appLanguage.text("恢复", "Restore"), systemImage: "arrow.uturn.backward")
                                }
                                .buttonStyle(.borderless)
                                .help(model.appLanguage.text("恢复对话", "Restore Conversation"))
                                .accessibilityIdentifier("conversation.trash.restore")
                            }
                            .padding(.vertical, 12)
                            .accessibilityElement(children: .contain)
                            Divider()
                        }
                    }
                }
                .frame(maxHeight: .infinity)
                .accessibilityIdentifier("conversation.trash.list")
            }
            if let error { Text(error).foregroundStyle(.red).appScaledTextStyle(.callout) }
            HStack {
                Spacer()
                Button(model.appLanguage.text("完成", "Done")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(24).frame(width: 600, height: 420)
    }
}
