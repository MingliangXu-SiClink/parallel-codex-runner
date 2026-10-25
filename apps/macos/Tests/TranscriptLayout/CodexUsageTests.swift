import AppKit
import Combine
import SwiftUI
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct CodexUsageTests {
    private func snapshot(used: Double = 88, status: String = "available") -> CodexUsageSnapshot {
        CodexUsageSnapshot(status: status, planType: "pro", fetchedAt: 1_000,
            limits: [CodexUsageLimit(id: "codex", name: "Codex", windows: [
                CodexUsageWindow(id: "primary", usedPercent: used, windowMinutes: 10_080, resetsAt: 2_000)
            ])])
    }

    @Test func workspaceNavigationKeepsTheQuotaInEveryRenderedFrame() async throws {
        _ = NSApplication.shared
        let client = PcrdClient(socketURL: URL(fileURLWithPath: "/missing-usage-socket"),
            tokenURL: URL(fileURLWithPath: "/missing-usage-token"))
        let model = AppModel(client: client, windowID: UUID())
        defer { model.stopWindowObservation() }
        model.appLanguage = .english
        model.selectedWorkspaceID = "one"
        model.daemonOnline = false
        let store = CodexUsageStore()
        let host = NSHostingView(rootView: SidebarCodexUsage(usage: store).environmentObject(model)
            .frame(width: 280, height: 48).background(Color.white).environment(\.colorScheme, .light))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 280, height: 48),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.close() }
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(80))
        let value = CodexUsageSnapshot(status: "available", planType: "pro", fetchedAt: Date().timeIntervalSince1970,
            limits: [CodexUsageLimit(id: "codex", name: "Codex", windows: [
                CodexUsageWindow(id: "primary", usedPercent: 27, windowMinutes: 10_080, resetsAt: nil)
            ])], sourceId: "same-account")
        await store.refresh(context: store.context) { value }
        try await Task.sleep(for: .milliseconds(40))
        let context = store.context
        let pollingID = store.pollingID(for: context)
        var contexts: [String] = []
        let observation = store.$context.dropFirst().sink { contexts.append($0) }
        defer { observation.cancel() }
        func render() throws -> Data {
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let bytes = try #require(bitmap.bitmapData)
            #expect(Set(UnsafeBufferPointer(start: bytes, count: bitmap.bytesPerRow * bitmap.pixelsHigh)).count > 10)
            return try #require(bitmap.representation(using: .png, properties: [:]))
        }
        host.layoutSubtreeIfNeeded()
        let baseline = try render()
        var frames = 0
        for index in 0..<12 {
            model.selectedWorkspaceID = index.isMultiple(of: 2) ? "two" : "one"
            for _ in 0..<4 {
                host.layoutSubtreeIfNeeded()
                #expect(try render() == baseline, "Frame \(frames) changed the rendered quota")
                #expect(store.context == context)
                #expect(store.pollingID(for: context) == pollingID)
                #expect(!window.isVisible && !window.isKeyWindow)
                frames += 1
                try await Task.sleep(for: .milliseconds(8))
            }
        }
        #expect(contexts.isEmpty, "Navigation restarted account polling: \(contexts)")
        let png = try render()
        if let path = ProcessInfo.processInfo.environment["PCR_USAGE_EVIDENCE"] {
            let directory = URL(fileURLWithPath: path, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try png.write(to: directory.appendingPathComponent("quota-after-navigation.png"))
        }
        print("Quota frame check: \(frames) frames across 12 workspace switches, without a visible QA window")
    }

    @Test func anAvailableAccountChangePublishesTheNewQuotaWithoutAnEmptyIntermediate() async {
        let store = CodexUsageStore()
        let first = CodexUsageSnapshot(status: "available", planType: "pro", fetchedAt: 1000,
            limits: snapshot(used: 88).limits, sourceId: "first")
        let next = CodexUsageSnapshot(status: "available", planType: "pro", fetchedAt: 1500,
            limits: snapshot(used: 20).limits, sourceId: "next")
        await store.refresh(context: "mac") { first }
        var published: [String?] = []
        let observation = store.$snapshot.dropFirst().sink { published.append($0?.sourceId) }
        defer { observation.cancel() }
        await store.refresh(context: "mac") { next }
        #expect(published == ["next"])
        #expect(store.snapshot?.weeklyWindow?.remainingPercent == 80)
    }

    @Test func weeklyQuotaCanBePrimaryAndRemainsDistinctFromTokens() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let value = try decoder.decode(CodexUsageSnapshot.self, from: Data("""
        {"status":"available","plan_type":"pro","fetched_at":1000,
         "limits":[{"id":"codex","name":"Codex","windows":[
            {"id":"primary","used_percent":88,"window_minutes":10080,"resets_at":2000}]}]}
        """.utf8))
        #expect(value.weeklyWindow?.remainingPercent == 12)
        #expect(!value.isStale(at: Date(timeIntervalSince1970: 1100)))
        #expect(value.isStale(at: Date(timeIntervalSince1970: 1121)))
        #expect(value.isStale(at: Date(timeIntervalSince1970: 2000)))
        #expect(value.weeklyWindow?.title(language: .chinese) == "每周额度")
        #expect(CodexUsageSnapshot(status: "available", planType: nil, fetchedAt: 1000,
            limits: [CodexUsageLimit(id: "another-model", name: "Other", windows: value.limits[0].windows)]).weeklyWindow == nil)
    }

    @Test func missingAndInvalidUsageAreNeverDisplayedAsUnusedQuota() {
        #expect(CodexUsageWindow(id: "primary", usedPercent: nil, windowMinutes: nil, resetsAt: nil).remainingPercent == nil)
        #expect(CodexUsageWindow(id: "primary", usedPercent: .nan, windowMinutes: 300, resetsAt: nil).remainingPercent == nil)
        #expect(snapshot(used: 120).weeklyWindow?.remainingPercent == 0)
        #expect(snapshot(used: -5).weeklyWindow?.remainingPercent == 100)
        #expect(snapshot(used: .nan).sidebarSummary(at: Date(timeIntervalSince1970: 1100), language: .chinese) == "使用情况")
        #expect(CodexUsageSnapshot(status: "available", planType: nil, fetchedAt: 1000, limits: [])
            .sidebarSummary(at: Date(timeIntervalSince1970: 1100), language: .english) == "Usage")
    }

    @Test func executionBindingsAreSeparateFromTheDisplayedAccountQuota() async throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let value = try decoder.decode(CodexUsageSnapshot.self, from: Data("""
        {"status":"available","plan_type":"pro","fetched_at":1000,"limits":[],
         "execution_accounts":{"current":2,"waiting":3,"legacy":6,"unverified":1,"observed_at":1100}}
        """.utf8))
        let accounts = try #require(value.executionAccounts)
        #expect(accounts.summary(language: .chinese) == "2 个 Agent 已连接当前账号\n3 个 Agent 等待安全切换\n6 个旧执行器的账号尚未核实\n1 个 Agent 的执行账号待核实")
        let store = CodexUsageStore()
        await store.refresh(context: "mac") { value }
        #expect(store.executionAccounts?.legacy == 6)
        #expect(store.snapshot?.limits.isEmpty == true)
        await store.refresh(context: "mac") { CodexUsageSnapshot(status: "unavailable", planType: nil,
            fetchedAt: 1100, limits: [], sourceId: "new-account") }
        #expect(store.executionAccounts == nil)
        #expect(store.snapshot == nil)
    }

    @Test func failedRefreshCannotKeepAnOldExecutionBindingAsCurrent() async {
        let store = CodexUsageStore()
        let accounts = CodexExecutionAccounts(current: 8, waiting: 0, legacy: 0, unverified: 0, observedAt: 1000)
        let value = CodexUsageSnapshot(status: "available", planType: "pro", fetchedAt: 1000,
            limits: snapshot().limits, executionAccounts: accounts)
        await store.refresh(context: "mac") { value }
        await store.refresh(context: "mac") { throw URLError(.timedOut) }
        #expect(store.executionAccounts == nil)
        #expect(store.snapshot?.weeklyWindow?.remainingPercent == 12)
    }

    @Test func staleUsageKeepsTheLastKnownNumberInsteadOfRefreshNeeded() {
        let value = snapshot()
        for time in [1100.0, 1121.0, 1999.0] {
            let now = Date(timeIntervalSince1970: time)
            #expect(value.sidebarSummary(at: now, language: .chinese) == "剩余 12%")
            #expect(value.sidebarSummary(at: now, language: .english) == "12% left")
        }
        #expect(value.isStale(at: Date(timeIntervalSince1970: 1999)))
        #expect(snapshot(used: 100).sidebarSummary(at: Date(timeIntervalSince1970: 1999), language: .chinese) == "剩余 0%")
        #expect(snapshot(used: 1.24).sidebarSummary(at: Date(timeIntervalSince1970: 1999), language: .english) == "98.76% left")
    }

    @Test func resetBoundaryKeepsTheNumberWithoutClaimingANewCycleBalance() {
        let value = snapshot()
        for time in [2000.0, 3000.0] {
            let now = Date(timeIntervalSince1970: time)
            #expect(value.sidebarSummary(at: now, language: .chinese) == "上次剩余 12%")
            #expect(value.sidebarSummary(at: now, language: .english) == "Last 12% left")
            #expect(value.weeklyWindow?.remainingPercent == 12)
        }
        let noReset = CodexUsageWindow(id: "primary", usedPercent: 88, windowMinutes: 10080, resetsAt: nil)
        #expect(noReset.remainingSummary(at: Date(timeIntervalSince1970: 3000), language: .chinese) == "剩余 12%")
    }

    @Test func pendingAndFailedRefreshesDoNotReplaceTheCachedQuota() async {
        let store = CodexUsageStore()
        let value = snapshot()
        let now = Date(timeIntervalSince1970: 1999)
        await store.refresh(context: "mac|workspace") { value }
        #expect(!store.refreshFailed)
        await store.refresh(context: "mac|workspace") { throw PcrdClientError.connection("offline") }
        #expect(store.refreshFailed)
        #expect(store.snapshot?.sidebarSummary(at: now, language: .chinese) == "剩余 12%")
        let pending = Task {
            await store.refresh(context: "mac|workspace") {
                try await Task.sleep(for: .milliseconds(30))
                return value
            }
        }
        while !store.refreshing { await Task.yield() }
        #expect(!store.refreshFailed)
        #expect(store.snapshot?.sidebarSummary(at: now, language: .chinese) == "剩余 12%")
        await pending.value
        #expect(!store.refreshFailed)
        #expect(!store.refreshing)
        #expect(store.snapshot?.fetchedAt == 1000)
    }

    @Test func unavailableResponseRetainsUsageButMachineAndAccountChangesDoNot() async {
        let store = CodexUsageStore()
        let value = snapshot()
        await store.refresh(context: "first-machine") { value }
        let unavailable = CodexUsageSnapshot(status: "unavailable", planType: nil, fetchedAt: 1500, limits: [])
        await store.refresh(context: "first-machine") { unavailable }
        #expect(store.refreshFailed)
        #expect(store.snapshot?.fetchedAt == 1000)
        #expect(store.snapshot?.sidebarSummary(at: Date(timeIntervalSince1970: 1999), language: .chinese) == "剩余 12%")
        await store.refresh(context: "other-machine") { unavailable }
        #expect(store.snapshot == nil)
        await store.refresh(context: "other-machine") { value }
        let unsupported = CodexUsageSnapshot(status: "unsupported_account", planType: nil, fetchedAt: 1600, limits: [])
        await store.refresh(context: "other-machine") { unsupported }
        #expect(store.snapshot == nil)
    }

    @Test func accountUsageRequestDoesNotRouteThroughAWorkspace() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        for refresh in [false, true] {
            let body = try #require(JSONSerialization.jsonObject(with: encoder.encode(
                CodexUsageRequest(refresh: refresh))) as? [String: Bool])
            #expect(body == ["refresh": refresh])
        }
    }

    @Test func switchingMachinesRejectsThePreviousResponse() async {
        let store = CodexUsageStore()
        let previous = snapshot(used: 88)
        let current = snapshot(used: 20)
        let pending = Task {
            await store.refresh(context: "old-machine") {
                try await Task.sleep(for: .milliseconds(30))
                return previous
            }
        }
        while store.context != "old-machine" { await Task.yield() }
        await store.refresh(context: "new-machine") { current }
        await pending.value
        #expect(store.context == "new-machine")
        #expect(store.snapshot?.weeklyWindow?.remainingPercent == 80)
        #expect(!store.refreshing)
    }

    @Test func failuresMarkCachedDataAndSignOutClearsIt() async {
        let store = CodexUsageStore()
        let value = snapshot()
        await store.refresh(context: "mac") { value }
        await store.refresh(context: "mac") { throw PcrdClientError.connection("offline") }
        #expect(store.status == "unavailable")
        #expect(store.snapshot?.fetchedAt == 1000)
        let signedOut = snapshot(status: "not_logged_in")
        await store.refresh(context: "mac") { signedOut }
        #expect(store.snapshot == nil)
        await store.refresh(context: "older-server") { throw PcrdClientError.remote("unknown_action", "unknown") }
        #expect(store.status == "server_update_required")
        #expect(store.snapshot == nil)
    }

    @Test func restoredServerCacheShowsAQuotaWhileBackgroundRefreshContinues() async throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let cached = try decoder.decode(CodexUsageSnapshot.self, from: Data("""
        {"status":"available","plan_type":"pro","fetched_at":1000,"source_id":"first-account-source",
         "refresh_status":"refreshing","limits":[{"id":"codex","name":"Codex","windows":[
          {"id":"primary","used_percent":7,"window_minutes":10080,"resets_at":2000}]}]}
        """.utf8))
        let store = CodexUsageStore()
        #expect(store.pollingState == "settled")
        #expect(store.pollingID(for: "mac|workspace") == "mac|workspace|settled")
        await store.refresh(context: "mac|workspace") { cached }
        #expect(store.snapshot?.weeklyWindow?.remainingPercent == 93)
        #expect(store.refreshInProgress)
        #expect(store.refreshInterval == .seconds(3))
        #expect(store.pollingState == "pending")
        #expect(store.pollingID(for: "mac|workspace") == "mac|workspace|pending")
        let failed = CodexUsageSnapshot(status: "available", planType: "pro", fetchedAt: 1000,
            limits: cached.limits, sourceId: cached.sourceId, refreshStatus: "failed")
        await store.refresh(context: "mac|workspace") { failed }
        #expect(store.refreshFailed)
        #expect(store.pollingState == "retry")
        #expect(store.pollingID(for: "other-machine") == "other-machine|settled")
        #expect(store.snapshot?.fetchedAt == 1000)
        #expect(store.snapshot?.weeklyWindow?.remainingPercent == 93)
        let otherAccount = CodexUsageSnapshot(status: "unavailable", planType: nil, fetchedAt: 1500,
            limits: [], sourceId: "another-account-source")
        await store.refresh(context: "mac|workspace") { otherAccount }
        #expect(store.snapshot == nil)
        #expect(store.refreshInterval == .seconds(10))
        let signedOut = CodexUsageSnapshot(status: "not_logged_in", planType: nil, fetchedAt: 1600,
            limits: [], sourceId: "another-account-source")
        await store.refresh(context: "mac|workspace") { signedOut }
        #expect(store.snapshot == nil)
        #expect(!store.serverRefreshing)
        #expect(store.pollingState == "settled")
        #expect(store.refreshInterval == .seconds(60))
    }
}
