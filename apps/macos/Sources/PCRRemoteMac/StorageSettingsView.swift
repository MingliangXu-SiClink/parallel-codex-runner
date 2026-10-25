import SwiftUI

struct StorageManagementView: View {
    @EnvironmentObject private var model: AppModel
    @State private var pendingCleanup: RunStorageEntry?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "internaldrive")
                    .appScaledSystemFont(size: 22, weight: .medium)
                    .foregroundStyle(model.appAccentColor.color)
                    .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.appLanguage.localized("Storage"))
                        .appScaledTextStyle(.title2, weight: .semibold)
                    Text(
                        model.appLanguage.text(
                            "查看并清理 Parallel Codex 在 \(model.selectedMachine?.name ?? "当前设备") 上创建的文件",
                            "Review and release files created by Parallel Codex on "
                                + (model.selectedMachine?.name ?? "the selected computer")
                        )
                    )
                        .appScaledTextStyle(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Task { await model.loadStorageSummary() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help(model.appLanguage.localized("Recalculate storage"))
                .disabled(model.storageSummaryLoading || model.storageCleaningRunID != nil)
            }
            .padding(20)

            Divider()

            if model.storageSummaryLoading, model.storageSummary == nil {
                Spacer()
                ProgressView(model.appLanguage.localized("Calculating storage…"))
                Spacer()
            } else {
                if let summary = model.storageSummary {
                    HStack(spacing: 36) {
                        StorageMetricView(
                            title: model.appLanguage.text("运行文件", "Run files"),
                            value: summary.managed
                        )
                        StorageMetricView(
                            title: model.appLanguage.text("可清理", "Reclaimable"),
                            value: summary.reclaimable
                        )
                        StorageMetricView(
                            title: (summary.volumeCount ?? 1) > 1
                                ? model.appLanguage.text("最少可用空间", "Minimum available")
                                : model.appLanguage.text("磁盘可用空间", "Disk available"),
                            value: summary.free
                        )
                        Spacer()
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 16)

                    if (summary.volumeCount ?? 1) > 1 {
                        Text(model.appLanguage.text(
                            "运行文件分布在 \(summary.volumeCount ?? 1) 个磁盘，以上显示可用空间最少的磁盘。",
                            "Run files span \(summary.volumeCount ?? 1) disks. "
                                + "The smallest available space is shown."
                        ))
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 10)
                    }

                    if summary.unmanagedCount > 0 {
                        Text(model.appLanguage.text(
                            "\(summary.unmanagedCount) 个命令行运行约占用 \(summary.unmanaged)。为避免误删，应用不会处理不归它管理的运行。",
                            "\(summary.unmanagedCount) command-line run(s) use about "
                                + "\(summary.unmanaged). The app leaves them untouched to avoid "
                                + "deleting runs it does not own."
                        ))
                        .appScaledTextStyle(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 10)
                    }
                }

                List {
                    Section(model.appLanguage.localized("Runs that can be cleaned")) {
                        let cleanable = model.storageSummary?.runs.filter {
                            $0.cleanupAction != nil
                        } ?? []
                        if cleanable.isEmpty {
                            Text(model.appLanguage.localized(
                                "No app-managed runs currently have removable files."
                            ))
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(cleanable) { entry in
                                StorageRunRow(
                                    language: model.appLanguage,
                                    entry: entry,
                                    cleaning: model.storageCleaningRunID == entry.runId,
                                    enabled: model.storageCleaningRunID == nil
                                ) {
                                    pendingCleanup = entry
                                }
                            }
                        }
                    }
                }
                .listStyle(.inset)
            }

            Divider()
            HStack(spacing: 8) {
                if let error = model.storageSummaryError, !error.isEmpty {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(error)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                } else if model.storageSummaryLoading {
                    ProgressView()
                        .controlSize(.small)
                    Text(model.appLanguage.localized("Calculating storage…"))
                        .foregroundStyle(.secondary)
                } else {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text(model.appLanguage.localized(
                        "Only verified app-managed run files can be cleaned here"
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
            await model.loadStorageSummary()
        }
        .alert(item: $pendingCleanup) { entry in
            let discarding = entry.cleanupAction == "discard"
            return Alert(
                title: Text(discarding
                    ? model.appLanguage.text("放弃并清理此运行？", "Discard and clean this run?")
                    : model.appLanguage.text("清理保留副本？", "Clean retained copies?")),
                message: Text(
                    discarding
                        ? model.appLanguage.text(
                            "\(entry.workspaceName) 尚未选择 Agent 结果。清理会删除所有候选且无法撤销。",
                            "No Agent result has been selected for \(entry.workspaceName). "
                                + "Cleaning removes every candidate and cannot be undone."
                        )
                        : model.appLanguage.text(
                            "这会删除 \(entry.workspaceName) 保留的 Agent 工作区和临时 Codex 数据，不会改变已采用的工作区结果。",
                            "This removes retained Agent workspaces and temporary Codex data for "
                                + "\(entry.workspaceName). The accepted workspace result is unchanged."
                        )
                ),
                primaryButton: .destructive(
                    Text(discarding
                        ? model.appLanguage.text("放弃并清理", "Discard and Clean")
                        : model.appLanguage.text("清理", "Clean"))
                ) {
                    Task { await model.cleanupStorage(for: entry) }
                },
                secondaryButton: .cancel()
            )
        }
    }
}

private struct StorageMetricView: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .appScaledTextStyle(.title3, weight: .semibold)
                .lineLimit(1)
            Text(title)
                .appScaledTextStyle(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct StorageRunRow: View {
    let language: AppLanguage
    let entry: RunStorageEntry
    let cleaning: Bool
    let enabled: Bool
    let onCleanup: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.workspaceName.isEmpty
                    ? language.text("未命名工作区", "Unnamed Workspace")
                    : entry.workspaceName)
                    .fontWeight(.medium)
                    .lineLimit(1)
                Text(detail)
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer()
            if cleaning {
                ProgressView()
                    .controlSize(.small)
            } else {
                Button(entry.cleanupAction == "discard"
                    ? language.text("放弃并清理", "Discard and Clean")
                    : language.text("清理", "Clean")) {
                    onCleanup()
                }
                .disabled(!enabled)
            }
        }
        .padding(.vertical, 4)
    }

    private var detail: String {
        [
            storageStatus(entry.status),
            formattedDate(entry.updatedAt),
            language.text("约可清理 \(entry.reclaimable)", "Reclaim about \(entry.reclaimable)"),
        ]
            .compactMap { $0 }
            .joined(separator: " · ")
    }

    private func storageStatus(_ value: String) -> String {
        switch value.lowercased() {
        case "finalized": return language.text("已采用", "Accepted")
        case "discarded": return language.text("已放弃", "Discarded")
        case "expired": return language.text("已过期", "Expired")
        case "completed": return language.text("等待选择", "Awaiting selection")
        case "failed": return language.text("失败", "Failed")
        case "interrupted": return language.text("已中断", "Interrupted")
        default: return language.text("已结束", "Finished")
        }
    }

    private func formattedDate(_ value: String) -> String? {
        guard let date = parseInternetDate(value) else { return nil }
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
}
