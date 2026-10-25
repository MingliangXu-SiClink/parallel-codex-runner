import AppKit
import Foundation
import SwiftUI

struct CodexUsageWindow: Decodable, Identifiable, Sendable {
    let id: String
    let usedPercent: Double?
    let windowMinutes: Int?
    let resetsAt: Double?

    var remainingPercent: Double? {
        guard let usedPercent, usedPercent.isFinite else { return nil }
        return max(0, min(100, 100 - usedPercent))
    }

    var remainingLabel: String? {
        guard let remaining = remainingPercent, let usedPercent else { return nil }
        // Integer-only provider data must not imply hundredth-percent accuracy.
        let precision = usedPercent == usedPercent.rounded() ? 0 : 2
        return remaining.formatted(.number.precision(.fractionLength(precision))) + "%"
    }

    func remainingSummary(at now: Date, language: AppLanguage) -> String? {
        guard let remaining = remainingLabel else { return nil }
        if resetsAt.map({ $0 <= now.timeIntervalSince1970 }) == true {
            return language.text("上次剩余 \(remaining)", "Last \(remaining) left")
        }
        return language.text("剩余 \(remaining)", "\(remaining) left")
    }

    func title(language: AppLanguage) -> String {
        switch windowMinutes {
        case 10_080: return language.text("每周额度", "Weekly limit")
        case 300: return language.text("5 小时额度", "5-hour limit")
        case let minutes? where minutes % 60 == 0:
            return language.text("\(minutes / 60) 小时额度", "\(minutes / 60)-hour limit")
        case let minutes?: return language.text("\(minutes) 分钟额度", "\(minutes)-minute limit")
        default: return language.text("使用额度", "Usage limit")
        }
    }
}

struct CodexUsageLimit: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let windows: [CodexUsageWindow]
}

struct CodexExecutionAccounts: Decodable, Sendable {
    let current: Int
    let waiting: Int
    let legacy: Int
    let unverified: Int
    let observedAt: Double

    func summary(language: AppLanguage) -> String? {
        var lines: [String] = []
        if current > 0 { lines.append(language.text("\(current) 个 Agent 已连接当前账号", "\(current) Agents connected to this account")) }
        if waiting > 0 { lines.append(language.text("\(waiting) 个 Agent 等待安全切换", "\(waiting) Agents awaiting a safe switch")) }
        if legacy > 0 { lines.append(language.text("\(legacy) 个旧执行器的账号尚未核实", "\(legacy) legacy executors have unverified accounts")) }
        if unverified > 0 { lines.append(language.text("\(unverified) 个 Agent 的执行账号待核实", "\(unverified) Agent accounts need verification")) }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }
}

struct CodexUsageSnapshot: Decodable, Sendable {
    let status: String
    let planType: String?
    let fetchedAt: Double
    let limits: [CodexUsageLimit]
    let sourceId: String?
    let refreshStatus: String?
    let executionAccounts: CodexExecutionAccounts?

    init(status: String, planType: String?, fetchedAt: Double, limits: [CodexUsageLimit],
         sourceId: String? = nil, refreshStatus: String? = nil, executionAccounts: CodexExecutionAccounts? = nil) {
        self.status = status
        self.planType = planType
        self.fetchedAt = fetchedAt
        self.limits = limits
        self.sourceId = sourceId
        self.refreshStatus = refreshStatus
        self.executionAccounts = executionAccounts
    }

    var weeklyWindow: CodexUsageWindow? {
        limits.first { $0.id == "codex" }?.windows.first { $0.windowMinutes == 10_080 }
    }

    func sidebarSummary(at now: Date, language: AppLanguage) -> String {
        weeklyWindow?.remainingSummary(at: now, language: language)
            ?? language.text("使用情况", "Usage")
    }

    func isStale(at now: Date) -> Bool {
        now.timeIntervalSince1970 - fetchedAt > 120
            || weeklyWindow?.resetsAt.map { $0 <= now.timeIntervalSince1970 } == true
    }
}

@MainActor
final class CodexUsageStore: ObservableObject {
    @Published private(set) var context = ""
    @Published private(set) var snapshot: CodexUsageSnapshot?
    @Published private(set) var status = "loading"
    @Published private(set) var refreshing = false
    @Published private(set) var serverRefreshing = false
    @Published private(set) var executionAccounts: CodexExecutionAccounts?
    private var sourceId: String?
    private var generation = 0

    var refreshInProgress: Bool { refreshing || serverRefreshing }
    var refreshFailed: Bool { status == "unavailable" && !refreshInProgress }
    var refreshInterval: Duration {
        serverRefreshing ? .seconds(3) : status == "unavailable" ? .seconds(10) : .seconds(60)
    }

    var pollingState: String {
        serverRefreshing ? "pending" : status == "unavailable" ? "retry" : "settled"
    }

    func pollingID(for newContext: String) -> String {
        newContext + "|" + (context == newContext ? pollingState : "settled")
    }

    func refresh(context newContext: String, fetch: @Sendable () async throws -> CodexUsageSnapshot) async {
        if context != newContext {
            context = newContext
            snapshot = nil
            status = "loading"
            refreshing = false
            serverRefreshing = false
            sourceId = nil
            executionAccounts = nil
        }
        guard !refreshing else { return }
        generation += 1
        let request = generation
        refreshing = true
        defer { if request == generation { refreshing = false } }
        do {
            let value = try await fetch()
            guard request == generation, !Task.isCancelled else { return }
            let sourceChanged = value.sourceId.map { sourceId != $0 } ?? false
            if let nextSource = value.sourceId, sourceChanged {
                sourceId = nextSource
            }
            serverRefreshing = value.refreshStatus == "refreshing"
            status = value.refreshStatus == "failed" ? "unavailable" : value.status
            executionAccounts = value.executionAccounts
            if value.status == "available" { snapshot = value }
            else if sourceChanged || value.status != "unavailable" { snapshot = nil }
        } catch {
            guard request == generation, !Task.isCancelled else { return }
            serverRefreshing = false
            executionAccounts = nil
            if case PcrdClientError.remote(let code, _) = error,
                ["unknown_action", "unsupported_action", "not_allowed", "remote_action_forbidden"].contains(code) {
                status = "server_update_required"
                snapshot = nil
            } else if case PcrdClientError.remote("relay_credentials_rejected", _) = error {
                status = "pairing_required"
                snapshot = nil
            } else {
                status = "unavailable"
            }
        }
    }
}

struct CodexUsageRequest: Encodable, Sendable {
    let refresh: Bool
}

struct SidebarCodexUsage: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var usage = CodexUsageStore()
    @State private var presented = false

    init(usage: CodexUsageStore = CodexUsageStore()) {
        _usage = StateObject(wrappedValue: usage)
    }

    private var context: String {
        model.currentMachineContextID
    }

    private var currentSnapshot: CodexUsageSnapshot? {
        usage.context == context ? usage.snapshot : nil
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            Button { presented.toggle() } label: {
                HStack(spacing: 6) {
                    Image(systemName: "gauge.with.dots.needle.33percent")
                        .appScaledSystemFont(size: 14)
                    Text(summary(at: timeline.date))
                        .appScaledSystemFont(size: 12)
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                .padding(.horizontal, 8)
                .frame(maxWidth: .infinity, minHeight: 38, alignment: .trailing)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(model.appLanguage.text("Codex 每周额度与重置时间", "Codex weekly limits and reset times"))
            .accessibilityLabel(model.appLanguage.text("Codex 使用情况", "Codex usage") + " · " + summary(at: timeline.date))
            .accessibilityIdentifier("sidebar.codex-usage")
            .popover(isPresented: $presented, arrowEdge: .top) {
                usageDetails(at: timeline.date)
            }
        }
        // A manual refresh must wake a previous 60-second sleep immediately.
        .task(id: usage.pollingID(for: context)) {
            await refresh()
            while !Task.isCancelled {
                do { try await Task.sleep(for: usage.refreshInterval) }
                catch { return }
                if NSApp.isActive || usage.serverRefreshing { await refresh() }
            }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active { Task { await refresh() } }
        }
        .onChange(of: model.daemonOnline) { online in
            if online { Task { await refresh() } }
        }
        .onChange(of: presented) { open in
            if open { Task { await refresh() } }
        }
    }

    private func refresh(force: Bool = false) async {
        let client = model.client
        let target = client.currentTarget()
        let online = model.daemonOnline && model.selectedMachine?.online != false
        // Quota belongs to the computer's account, not the selected workspace.
        await usage.refresh(context: target.machineID) {
            guard online else { throw PcrdClientError.connection("offline") }
            return try await client.call(
                target: target, "codex.account_usage",
                body: CodexUsageRequest(refresh: force)
            )
        }
    }

    private func summary(at now: Date) -> String {
        currentSnapshot?.sidebarSummary(at: now, language: model.appLanguage)
            ?? model.appLanguage.text("使用情况", "Usage")
    }

    private func usageDetails(at now: Date) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(model.appLanguage.text("Codex 使用情况", "Codex usage"))
                    .appScaledSystemFont(size: 15, weight: .semibold)
                Spacer()
                Button { Task { await refresh(force: true) } } label: {
                    if usage.refreshInProgress {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                .frame(width: 24, height: 24)
                .buttonStyle(.plain)
                .disabled(usage.refreshInProgress)
                .help(model.appLanguage.text("刷新使用情况", "Refresh usage"))
                .accessibilityLabel(model.appLanguage.text("刷新使用情况", "Refresh usage"))
                .accessibilityIdentifier("usage.refresh")
            }
            Text([model.selectedMachine?.name ?? model.appLanguage.text("此 Mac", "This Mac"),
                  currentSnapshot?.planType?.capitalized].compactMap { $0 }.joined(separator: " · "))
                .appScaledSystemFont(size: 12)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Text(model.appLanguage.text("当前登录账号的额度", "Limits for the signed-in account"))
                .appScaledSystemFont(size: 11)
                .foregroundStyle(.secondary)
            if usage.context == context,
               let accounts = usage.executionAccounts,
               now.timeIntervalSince1970 - accounts.observedAt < 120,
               let summary = accounts.summary(language: model.appLanguage) {
                Text(summary)
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("usage.execution-accounts")
            }
            if let snapshot = currentSnapshot {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        ForEach(snapshot.limits) { limit in
                            VStack(alignment: .leading, spacing: 12) {
                                if snapshot.limits.count > 1 {
                                    Text(limit.name).appScaledSystemFont(size: 12, weight: .semibold)
                                }
                                ForEach(limit.windows) { window in
                                    windowRow(window, at: now)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(minHeight: min(310, CGFloat(snapshot.limits.reduce(0) { $0 + $1.windows.count })
                    * 80 * model.appTextSize.scale), maxHeight: 310)
                if usage.refreshFailed {
                    Text(model.appLanguage.text("当前无法更新，显示上次获取的额度。", "Unable to refresh. Showing the last fetched limits."))
                        .appScaledSystemFont(size: 12)
                        .foregroundStyle(.secondary)
                }
                Text(model.appLanguage.text("上次更新 ", "Last updated ") +
                     Date(timeIntervalSince1970: snapshot.fetchedAt).formatted(date: .abbreviated, time: .shortened))
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.secondary)
            } else {
                Text(statusMessage)
                    .appScaledSystemFont(size: 13)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(18)
        .frame(width: 320)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("usage.details")
    }

    private func windowRow(_ window: CodexUsageWindow, at now: Date) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(window.title(language: model.appLanguage))
                Spacer(minLength: 8)
                if let remaining = window.remainingSummary(at: now, language: model.appLanguage) {
                    Text(remaining)
                        .monospacedDigit()
                } else {
                    Text(model.appLanguage.text("暂无数据", "Unavailable"))
                }
            }
            .appScaledSystemFont(size: 13)
            if let remaining = window.remainingPercent {
                ProgressView(value: remaining, total: 100)
                    .tint(remaining <= 10 ? Color.orange : model.appAccentColor.color)
                    .accessibilityLabel(window.title(language: model.appLanguage))
                    .accessibilityValue(window.remainingSummary(at: now, language: model.appLanguage) ?? "")
            }
            if let reset = window.resetsAt {
                Text(reset > now.timeIntervalSince1970
                     ? model.appLanguage.text("重置于 ", "Resets ") + Date(timeIntervalSince1970: reset).formatted(date: .abbreviated, time: .shortened)
                     : model.appLanguage.text("已到重置时间，显示上次获取的额度。", "Reset time reached. Showing the last fetched limits."))
                    .appScaledSystemFont(size: 11)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var statusMessage: String {
        switch usage.context == context ? usage.status : "loading" {
        case "loading": return model.appLanguage.text("正在获取使用情况…", "Fetching usage…")
        case "not_logged_in": return model.appLanguage.text("此设备的 Codex 尚未登录。登录后请刷新。", "Codex is not signed in on this computer. Sign in, then refresh.")
        case "unsupported_account": return model.appLanguage.text("此设备使用的账号类型不提供 ChatGPT 周额度。", "This account type does not provide ChatGPT weekly limits.")
        case "codex_unavailable": return model.appLanguage.text("此设备未找到 Codex。安装后请刷新。", "Codex was not found on this computer. Install it, then refresh.")
        case "update_required": return model.appLanguage.text("请更新此设备的 Codex 后查看额度。", "Update Codex on this computer to view usage.")
        case "server_update_required": return model.appLanguage.text("请更新此设备的 Parallel Codex 服务后查看额度。", "Update Parallel Codex Server on this computer to view usage.")
        case "pairing_required": return model.appLanguage.text("此设备的配对已失效，请在设置中重新连接。", "This device's pairing is no longer valid. Reconnect in Settings.")
        default: return model.appLanguage.text("暂时无法获取额度，将自动重试。", "Usage is currently unavailable. It will retry automatically.")
        }
    }
}
