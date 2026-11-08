import SwiftUI

struct CodexAccountSection: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var manager: CodexAccountManager
    @State private var deleteTarget: PCRAccount?
    @State private var showAddAPIKey = false
    @State private var apiKeyInput = ""
    @State private var loginErrorPresented = false

    init(model: AppModel) {
        _manager = StateObject(wrappedValue: CodexAccountManager(model: model))
    }

    var body: some View {
        Section(model.appLanguage.text("Codex 账号", "Codex Account")) {
            if manager.loading {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(model.appLanguage.text("加载账号…", "Loading accounts…"))
                        .foregroundStyle(.secondary)
                }
            } else if let err = manager.loadError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .appScaledTextStyle(.caption)
                    .textSelection(.enabled)
            } else if manager.accounts.isEmpty {
                Text(model.appLanguage.text("暂无账号，请点击下方添加。", "No accounts yet. Add one below."))
                    .foregroundStyle(.secondary)
                    .appScaledTextStyle(.callout)
            } else {
                ForEach(manager.accounts) { account in
                    accountRow(account)
                }
            }

            // Login / add buttons
            if manager.loginInProgress {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(model.appLanguage.text(
                            "已在浏览器中打开登录页，请完成授权…",
                            "Sign-in page opened in browser. Complete authorization there…"
                        ))
                        .appScaledTextStyle(.callout)
                        .foregroundStyle(.secondary)
                    }
                    Button(model.appLanguage.text("取消登录", "Cancel Sign-in"), role: .destructive) {
                        manager.cancelLogin()
                    }
                    .controlSize(.small)
                }
            } else {
                HStack(spacing: 10) {
                    Button {
                        Task { await manager.startLogin() }
                    } label: {
                        Label(
                            model.appLanguage.text("ChatGPT 登录", "Sign in with ChatGPT"),
                            systemImage: "person.badge.key.fill"
                        )
                    }
                    .controlSize(.small)
                    .disabled(!model.daemonOnline || manager.switching || manager.deleting)

                    Button {
                        apiKeyInput = ""
                        showAddAPIKey = true
                    } label: {
                        Label(
                            model.appLanguage.text("添加 API Key", "Add API Key"),
                            systemImage: "key.fill"
                        )
                    }
                    .controlSize(.small)
                    .disabled(!model.daemonOnline || manager.switching || manager.deleting)
                }
            }
        }
        .task { await manager.refresh() }
        .alert(
            model.appLanguage.text("登录失败", "Sign-in failed"),
            isPresented: Binding(
                get: { manager.loginError != nil },
                set: { if !$0 { }  }
            )
        ) {
            Button(model.appLanguage.text("好", "OK"), role: .cancel) { }
        } message: {
            Text(manager.loginError ?? "")
        }
        .confirmationDialog(
            deleteTarget.map { account in
                model.appLanguage.text(
                    "删除「\(account.displayName)」？此操作无法撤销。",
                    "Delete \"\(account.displayName)\"? This cannot be undone."
                )
            } ?? "",
            isPresented: Binding(get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } }),
            titleVisibility: .visible
        ) {
            if let target = deleteTarget {
                Button(model.appLanguage.text("删除账号", "Delete Account"), role: .destructive) {
                    Task {
                        await manager.deleteAccount(id: target.id)
                        deleteTarget = nil
                    }
                }
                Button(model.appLanguage.text("取消", "Cancel"), role: .cancel) {
                    deleteTarget = nil
                }
            }
        }
        .sheet(isPresented: $showAddAPIKey) {
            AddAPIKeySheet(
                apiKey: $apiKeyInput,
                language: model.appLanguage
            ) { key in
                showAddAPIKey = false
                if let key {
                    Task { await manager.addAPIKey(key) }
                }
            }
        }
    }

    @ViewBuilder
    private func accountRow(_ account: PCRAccount) -> some View {
        HStack(spacing: 10) {
            // Active indicator
            Image(systemName: account.isActive ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(account.isActive ? model.appAccentColor.color : Color.secondary.opacity(0.5))
                .frame(width: 18)

            VStack(alignment: .leading, spacing: 2) {
                Text(account.displayName)
                    .appScaledTextStyle(.body)
                    .lineLimit(1)
                if let email = account.email, email != account.displayName {
                    Text(email)
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if account.isApiKey {
                    Text(model.appLanguage.text("API Key", "API Key"))
                        .appScaledTextStyle(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 3))
                }
            }

            Spacer()

            // Switch button
            if !account.isActive {
                Button(model.appLanguage.text("切换", "Switch")) {
                    Task { await manager.switchAccount(id: account.id) }
                }
                .controlSize(.small)
                .buttonStyle(.bordered)
                .disabled(manager.switching || manager.deleting)
            } else {
                Text(model.appLanguage.text("当前", "Active"))
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(model.appAccentColor.color)
            }

            // Delete button (only for non-active accounts)
            if !account.isActive {
                Button(role: .destructive) {
                    deleteTarget = account
                } label: {
                    Image(systemName: "trash")
                        .foregroundStyle(.red.opacity(0.8))
                }
                .buttonStyle(.borderless)
                .controlSize(.small)
                .disabled(manager.deleting)
            }
        }
        .padding(.vertical, 2)
    }
}

// MARK: - Add API Key sheet

private struct AddAPIKeySheet: View {
    @Binding var apiKey: String
    let language: AppLanguage
    let completion: (String?) -> Void

    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(language.text("添加 OpenAI API Key", "Add OpenAI API Key"))
                .font(.headline)

            Text(language.text(
                "API Key 将安全保存到 PCR 的账号存储中，不会写入系统的 Codex 配置。",
                "The API Key will be saved securely to PCR's own account store and will not be written to the system Codex configuration."
            ))
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            SecureField(language.text("sk-…", "sk-…"), text: $apiKey)
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .onSubmit { if !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { completion(apiKey) } }

            HStack {
                Spacer()
                Button(language.text("取消", "Cancel"), role: .cancel) { completion(nil) }
                    .keyboardShortcut(.escape, modifiers: [])
                Button(language.text("添加", "Add")) {
                    completion(apiKey.trimmingCharacters(in: .whitespacesAndNewlines))
                }
                .keyboardShortcut(.return, modifiers: [])
                .buttonStyle(.borderedProminent)
                .disabled(apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24)
        .frame(minWidth: 360, maxWidth: 480)
        .onAppear { focused = true }
    }
}
