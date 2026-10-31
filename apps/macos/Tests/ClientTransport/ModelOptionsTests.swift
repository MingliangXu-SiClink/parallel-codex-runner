import Darwin
import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ModelOptionsTests {
    private static let options = CodexModelOptions(
        configuredModel: "gpt-6.1-sol", configuredEffort: "high", configuredServiceTier: nil,
        effectiveModel: "gpt-6.1-sol", effectiveEffort: "high", effectiveFast: nil,
        models: [
            CodexOption(label: "Default", value: "", selected: true),
            CodexOption(label: "gpt-6.1-sol", value: "gpt-6.1-sol", selected: false),
            CodexOption(label: "future-model", value: "future-model", selected: false),
        ],
        efforts: [
            CodexOption(label: "Automatic", value: "", selected: true),
            CodexOption(label: "high", value: "high", selected: false),
            CodexOption(label: "future-effort", value: "future-effort", selected: false),
        ]
    )

    @Test func catalogIsIndependentOfTheWorkspaceAndEffort() async throws {
        let fixture = try Fixture(delay: .milliseconds(80))
        defer { fixture.close() }
        #expect(fixture.model.selectedWorkspaceID == nil)
        fixture.model.settings.model = "gpt-6.1-sol"
        fixture.model.settings.effort = "high"
        let loading = Task { await fixture.model.loadPrimaryModelOptions() }
        try await Task.sleep(for: .milliseconds(20))
        fixture.model.selectedWorkspaceID = "new-workspace"
        fixture.model.workspaceContextGeneration &+= 1
        fixture.model.settings.effort = "future-effort"
        await loading.value
        let body = try #require(await fixture.server.value)
        #expect(body.model == "gpt-6.1-sol")
        #expect(body.effort == nil)
        #expect(fixture.model.primaryModelOptions?.models.count == 3)
        #expect(fixture.model.primaryModelOptions?.efforts.count == 3)
        #expect(fixture.model.settings.effort == "future-effort")
        #expect(!fixture.model.primaryModelOptionsLoading)
        fixture.model.resetRunView()
        #expect(fixture.model.primaryModelOptions?.models.count == 3)
    }

    @Test func staleModelResponseDoesNotPublishAnotherModelsEfforts() async throws {
        let fixture = try Fixture(delay: .milliseconds(80))
        defer { fixture.close() }
        fixture.model.settings.model = "old-model"
        let loading = Task { await fixture.model.loadPrimaryModelOptions() }
        try await Task.sleep(for: .milliseconds(20))
        fixture.model.settings.model = "new-model"
        await loading.value
        #expect(await fixture.server.value != nil)
        #expect(fixture.model.primaryModelOptions == nil)
        #expect(fixture.model.codexModelCatalog?.count == 3)
        fixture.model.settings.model = "old-model"
        #expect(fixture.model.primaryModelOptions?.efforts.count == 3)
    }

    @Test func delayedPrimarySelectionCannotResetAnotherWorkspacesEffort() async throws {
        let fixture = try Fixture(delay: .milliseconds(80))
        defer { fixture.close() }
        fixture.model.selectedWorkspaceID = "first-workspace"
        let selecting = Task { await fixture.model.updatePrimaryModel("gpt-6.1-sol") }
        try await Task.sleep(for: .milliseconds(20))
        fixture.model.selectedWorkspaceID = "second-workspace"
        fixture.model.workspaceContextGeneration += 1
        fixture.model.settings.effort = "other-workspace-effort"
        await selecting.value
        #expect(fixture.model.settings.effort == "other-workspace-effort")
        #expect(await fixture.server.value != nil)
    }

    @Test func delayedSynthesisSelectionCannotValidateOrScheduleUpdatesInAnotherWorkspace() async throws {
        let fixture = try Fixture(delay: .milliseconds(80))
        defer { fixture.close() }
        fixture.model.selectedWorkspaceID = "first-workspace"
        let selecting = Task { await fixture.model.updateSynthesisModel("gpt-6.1-sol") }
        try await Task.sleep(for: .milliseconds(20))
        fixture.model.selectedWorkspaceID = "second-workspace"
        fixture.model.workspaceContextGeneration += 1
        fixture.model.settings.synthesisEffort = "other-workspace-effort"
        await selecting.value
        #expect(fixture.model.settings.synthesisEffort == "other-workspace-effort")
        #expect(fixture.model.synthesisSettingsTasks.isEmpty)
        #expect(await fixture.server.value != nil)
    }

    @Test func directoryRefreshNeverChangesAnExplicitSelection() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.settings.model = "unlisted-custom-model"
        fixture.model.settings.effort = "custom-effort"
        await fixture.model.loadPrimaryModelOptions()
        #expect(await fixture.server.value != nil)
        #expect(fixture.model.settings.model == "unlisted-custom-model")
        #expect(fixture.model.settings.effort == "custom-effort")
    }

    @Test(arguments: ["future-effort", "unsupported-effort"])
    func explicitModelChangeKeepsCompatibleEffortOnly(effort: String) async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        fixture.model.settings.model = "previous-model"
        fixture.model.settings.effort = effort
        await fixture.model.updatePrimaryModel("gpt-6.1-sol")
        #expect(await fixture.server.value != nil)
        #expect(fixture.model.settings.model == "gpt-6.1-sol")
        #expect(fixture.model.settings.effort == (effort == "future-effort" ? effort : ""))
    }

    @Test func cachedCapabilitiesSurviveFailuresAndDoNotCrossMachinesOrModels() async throws {
        let cache = CodexModelOptionsCache()
        let context = CodexModelOptionsContext(machineID: "mac", model: "gpt-6.1-sol")
        let options = Self.options
        _ = try await cache.refresh(context: context) { options }
        do {
            _ = try await cache.refresh(context: context) { throw PcrdClientError.connection("offline") }
            Issue.record("Expected a failed refresh")
        } catch {}
        #expect(cache.options(for: context)?.models.count == 3)
        #expect(cache.options(for: CodexModelOptionsContext(machineID: "other-mac", model: context.model)) == nil)
        #expect(cache.models(for: "other-mac") == nil)
        #expect(cache.options(for: CodexModelOptionsContext(machineID: "mac", model: "future-model")) == nil)
        #expect(cache.models(for: "mac")?.count == 3)
    }

    @Test func committedCapabilitiesRestoreBeforeFetchingAndFailuresNeverReplaceThem() async throws {
        let name = "PCR.ModelOptionsTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let context = CodexModelOptionsContext(machineID: "mac", model: "gpt-6.1-sol")
        var value = Self.options
        value.catalogContext = "account-a"
        value.catalogRevision = 1
        value.catalogRefreshing = true
        let saved = value
        _ = try await CodexModelOptionsCache(defaults: defaults).refresh(context: context) { saved }
        let restored = CodexModelOptionsCache(defaults: defaults)
        #expect(restored.models(for: "mac")?.count == 3)
        #expect(restored.options(for: context)?.efforts.count == 3)
        #expect(restored.options(for: context)?.catalogRefreshing != true)
        let before = defaults.data(forKey: "parallel-codex.model-options.v1")
        do { _ = try await restored.refresh(context: context) { throw PcrdClientError.connection("offline") } }
        catch {}
        var empty = saved
        empty.models = []
        let emptyResult = empty
        do {
            _ = try await restored.refresh(context: context) { emptyResult }
            Issue.record("An empty discovery must not erase committed capabilities")
        } catch {}
        #expect(defaults.data(forKey: "parallel-codex.model-options.v1") == before)
        #expect(CodexModelOptionsCache(defaults: defaults).models(for: "mac")?.count == 3)
    }

    @Test func aLateOlderCatalogCannotReplaceANewerSuccessfulCatalog() async throws {
        let cache = CodexModelOptionsCache()
        let first = CodexModelOptionsContext(machineID: "mac", model: "first")
        let second = CodexModelOptionsContext(machineID: "mac", model: "second")
        cache.selectScope("account-a", machineID: "mac")
        var old = Self.options
        old.catalogContext = "account-a"
        old.catalogRevision = 1
        var new = old
        new.catalogRevision = 2
        new.models = [CodexOption(label: "new-model", value: "new-model", selected: false)]
        let oldValue = old, newValue = new
        async let delayed = cache.refresh(context: first) {
            try await Task.sleep(for: .milliseconds(60))
            return oldValue
        }
        try await Task.sleep(for: .milliseconds(10))
        _ = try await cache.refresh(context: second) { newValue }
        _ = try await delayed
        #expect(cache.models(for: "mac")?.map(\.value) == ["new-model"])
        #expect(cache.options(for: first)?.models.map(\.value) == ["new-model"])
        #expect(cache.options(for: first)?.efforts.count == 3)
    }

    @Test func changingAccountsDoesNotJoinOrPublishAnOldAccountsRequest() async throws {
        let cache = CodexModelOptionsCache()
        let context = CodexModelOptionsContext(machineID: "mac", model: "gpt-6.1-sol")
        cache.selectScope("account-a", machineID: "mac")
        var old = Self.options
        old.catalogContext = "account-a"
        let oldValue = old
        let pending = Task {
            try await cache.refresh(context: context) {
                try await Task.sleep(for: .milliseconds(60))
                return oldValue
            }
        }
        try await Task.sleep(for: .milliseconds(10))
        cache.selectScope("account-b", machineID: "mac")
        #expect(cache.models(for: "mac") == nil)
        var new = Self.options
        new.catalogContext = "account-b"
        new.models = [CodexOption(label: "account-b-model", value: "account-b-model", selected: true)]
        let newValue = new
        _ = try await cache.refresh(context: context) { newValue }
        do { _ = try await pending.value; Issue.record("Old account response must be fenced") }
        catch is CancellationError {} catch { Issue.record("Unexpected error: \(error)") }
        #expect(cache.models(for: "mac")?.map(\.value) == ["account-b-model"])
    }

    @Test func separateWindowsPreserveEachOthersCommittedMachineCatalogs() async throws {
        let name = "PCR.ModelOptionsWindows.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let first = CodexModelOptionsCache(defaults: defaults)
        let second = CodexModelOptionsCache(defaults: defaults)
        var value = Self.options
        value.catalogContext = "account-a"
        let saved = value
        _ = try await first.refresh(context: .init(machineID: "mac-a", model: "first")) { saved }
        _ = try await second.refresh(context: .init(machineID: "mac-b", model: "second")) { saved }
        let restored = CodexModelOptionsCache(defaults: defaults)
        #expect(restored.models(for: "mac-a")?.count == 3)
        #expect(restored.models(for: "mac-b")?.count == 3)
    }

    @Test func simultaneousPrimaryAndSynthesisRequestsShareDiscovery() async throws {
        let cache = CodexModelOptionsCache()
        let context = CodexModelOptionsContext(machineID: "mac", model: "")
        let counter = RequestCounter()
        let options = Self.options
        let fetch: @Sendable () async throws -> CodexModelOptions = {
            await counter.increment()
            try await Task.sleep(for: .milliseconds(30))
            return options
        }
        async let first = cache.refresh(context: context, fetch: fetch)
        async let second = cache.refresh(context: context, fetch: fetch)
        _ = try await (first, second)
        #expect(await counter.value == 1)
        #expect(cache.options(for: context)?.efforts.count == 3)
    }

    @Test func lateResponseRemainsScopedToItsMachine() async throws {
        let cache = CodexModelOptionsCache()
        let first = CodexModelOptionsContext(machineID: "A", model: "")
        let second = CodexModelOptionsContext(machineID: "B", model: "")
        let old = Self.options
        let current = CodexModelOptions(configuredModel: "another-model", configuredEffort: nil,
            configuredServiceTier: nil, effectiveModel: "another-model", effectiveEffort: nil,
            effectiveFast: nil, models: [CodexOption(label: "Another", value: "another-model", selected: true)], efforts: [])
        async let pending = cache.refresh(context: first) {
            try await Task.sleep(for: .milliseconds(50))
            return old
        }
        _ = try await cache.refresh(context: second) { current }
        _ = try await pending
        #expect(cache.models(for: "B")?.first?.value == "another-model")
        #expect(cache.options(for: second)?.efforts.isEmpty == true)
        #expect(cache.models(for: "A")?.count == 3)
    }

    @Test func displayNamesDoNotChangeProtocolIdentifiers() {
        #expect(codexModelDisplayName("gpt-6.1-sol") == "GPT-6.1 Sol")
        #expect(codexModelDisplayName("gpt-6-astra") == "GPT-6 Astra")
        #expect(codexModelDisplayName("provider/custom-model") == "provider/custom-model")
        #expect(codexOptionChoices(dynamic: Self.options.models, current: "future-model", fallbackLabel: "Default")
            .contains { $0.value == "future-model" })
        #expect(codexEffortChoices(dynamic: Self.options.efforts, current: "future-effort", effective: "high", language: .chinese)
            .contains { $0.value == "future-effort" && $0.label == "future-effort" })
    }

    @Test func modelMenusContainOnlyConcreteIDsAndSelectTheEffectiveLegacyModel() {
        let choices = codexModelChoices(dynamic: Self.options.models + [Self.options.models[1]],
                                       current: "", effective: "gpt-6.1-sol")
        #expect(choices.map(\.value) == ["gpt-6.1-sol", "future-model"])
        #expect(choices.first?.label == "GPT-6.1 Sol")
        #expect(choices.filter(\.selected).map(\.value) == ["gpt-6.1-sol"])
        #expect(resolvedCodexModel(current: "", options: Self.options) == "gpt-6.1-sol")
        #expect(resolvedCodexModel(current: "custom", options: Self.options) == "custom")
        #expect(resolvedCodexModel(current: "", options: nil).isEmpty)
    }

    @Test func catalogFailurePreservesExplicitModelWithoutInventingDefaultOrOtherModels() {
        let choices = codexModelChoices(dynamic: nil, current: "private-model", effective: "")
        #expect(choices.map(\.value) == ["private-model"])
        #expect(choices.first?.selected == true)
        #expect(codexModelChoices(dynamic: nil, current: "", effective: "").isEmpty)
        #expect(codexModelChoices(dynamic: [CodexOption(label: "Default", value: " ", selected: true)],
                                 current: "", effective: "").isEmpty)
        // Effort still has a meaningful automatic mode; model selection does not.
        #expect(codexEffortChoices(dynamic: Self.options.efforts, current: "", effective: "high", language: .english)
            .filter { $0.value.isEmpty }.count == 1)
    }

    @Test func emptyModelEditsCannotRestoreAnImplicitDefault() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let before = fixture.model.settings
        let defaults = fixture.model.defaultSettings
        for value in ["", " \n "] {
            await fixture.model.updatePrimaryModel(value)
            await fixture.model.updateSynthesisModel(value)
            await fixture.model.updateDefaultPrimaryModel(value)
            await fixture.model.updateDefaultSynthesisModel(value)
            for stage in ExecutionStage.allCases {
                #expect(!(await fixture.model.editExecutionConfiguration(.model(value), stage: stage,
                    context: ExecutionEditContext(fixture.model))))
            }
        }
        #expect(fixture.model.settings == before)
        #expect(fixture.model.defaultSettings == defaults)
    }

    @Test func newRequestsPinResolvedModelsWithoutRewritingOldSettingsOrAcceptedMessages() async throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        var legacy = RunSettings()
        legacy.synthesisAgents = 2
        let context = CodexModelOptionsContext(machineID: fixture.model.currentMachineContextID, model: "")
        let options = Self.options
        _ = try await fixture.model.modelOptionsCache.refresh(context: context) { options }
        let snapshot = try #require(fixture.model.settingsWithConcreteModelsForNewPrompt(legacy))
        let body = fixture.model.makeRunStartBody(workspaceID: "qa", prompt: "new prompt", settings: snapshot, confirmLargeRun: false)
        #expect(body.model == "gpt-6.1-sol" && body.synthesisModel == "gpt-6.1-sol")
        #expect(legacy.model.isEmpty && legacy.synthesisModel.isEmpty)
        let changed = CodexModelOptions(configuredModel: "new-default", configuredEffort: nil,
            configuredServiceTier: nil, effectiveModel: "new-default", effectiveEffort: nil,
            effectiveFast: nil,
            models: [CodexOption(label: "new-default", value: "new-default", selected: true)], efforts: [])
        _ = try await fixture.model.modelOptionsCache.refresh(context: context) { changed }
        #expect(snapshot.model == "gpt-6.1-sol" && snapshot.synthesisModel == "gpt-6.1-sol")
        let continued = fixture.model.makeRunContinueBody(runID: "qa", agent: 1, prompt: "new prompt", settings: snapshot, confirmLargeRun: false)
        #expect(continued.model == body.model && continued.synthesisModel == body.synthesisModel)
        #expect(fixture.model.promptOutboxByWorkspace.isEmpty)
    }

    @Test func unresolvedNewModelsDoNotFallBackOrReplaceAnExplicitChoice() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        var settings = RunSettings()
        #expect(fixture.model.settingsWithConcreteModelsForNewPrompt(settings) == nil)
        settings.model = "unlisted-explicit-candidate"
        settings.synthesisAgents = 0
        #expect(fixture.model.settingsWithConcreteModelsForNewPrompt(settings)?.model == settings.model)
        settings.synthesisAgents = 1
        #expect(fixture.model.settingsWithConcreteModelsForNewPrompt(settings) == nil)
        settings.synthesisModel = "unlisted-explicit-synthesis"
        #expect(fixture.model.settingsWithConcreteModelsForNewPrompt(settings) == settings)
    }

    private actor RequestCounter {
        var value = 0
        func increment() { value += 1 }
    }

    private struct ModelRequest: Decodable, Sendable {
        let model: String?
        let effort: String?
    }

    @MainActor
    private final class Fixture {
        let directory: URL
        let listener: Int32
        let model: AppModel
        let server: Task<ModelRequest?, Never>

        init(delay: Duration = .zero) throws {
            directory = FileManager.default.temporaryDirectory.appendingPathComponent("pcr-model-\(UUID().uuidString.prefix(8))")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let socketURL = directory.appendingPathComponent("s")
            let tokenURL = directory.appendingPathComponent("t")
            try Data("test-token".utf8).write(to: tokenURL)
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { target in
                Array(socketURL.path.utf8CString).withUnsafeBytes { target.copyBytes(from: $0) }
            }
            let descriptor = listener
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(bound == 0 && Darwin.listen(listener, 4) == 0)
            let serve: @Sendable () -> ModelRequest? = {
                var pollDescriptor = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
                guard Darwin.poll(&pollDescriptor, 1, 3_000) > 0 else { return nil }
                let socket = Darwin.accept(descriptor, nil, nil)
                guard socket >= 0 else { return nil }
                defer { Darwin.close(socket) }
                var bytes = [UInt8](repeating: 0, count: 8192)
                let count = Darwin.read(socket, &bytes, bytes.count)
                guard count > 0,
                      let request = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as? [String: Any],
                      request["action"] as? String == "codex.model_options",
                      let bodyObject = request["body"] as? [String: Any],
                      let bodyData = try? JSONSerialization.data(withJSONObject: bodyObject),
                      let body = try? JSONDecoder().decode(ModelRequest.self, from: bodyData) else { return nil }
                let replyJSON = Data("""
                {"ok":true,"body":{"configured_model":"gpt-6.1-sol","configured_effort":"high",
                "effective_model":"gpt-6.1-sol","effective_effort":"high","models":[
                {"label":"Default","value":"","selected":false},
                {"label":"gpt-6.1-sol","value":"gpt-6.1-sol","selected":true},
                {"label":"future-model","value":"future-model","selected":false}],"efforts":[
                {"label":"Automatic","value":"","selected":false},
                {"label":"high","value":"high","selected":true},
                {"label":"future-effort","value":"future-effort","selected":false}]}}

                """.utf8)
                guard let object = try? JSONSerialization.jsonObject(with: replyJSON),
                      let compactReply = try? JSONSerialization.data(withJSONObject: object) else { return nil }
                let reply = compactReply + Data([10])
                let duration = delay.components
                Thread.sleep(forTimeInterval: Double(duration.seconds) + Double(duration.attoseconds) / 1e18)
                _ = reply.withUnsafeBytes { Darwin.write(socket, $0.baseAddress, $0.count) }
                return body
            }
            // The fixture is blocking socket I/O, not cooperative executor work.
            server = Task {
                await withCheckedContinuation { continuation in
                    DispatchQueue(label: "pcr-model-options-fixture").async {
                        continuation.resume(returning: serve())
                    }
                }
            }
            model = AppModel(client: PcrdClient(socketURL: socketURL, tokenURL: tokenURL), windowID: UUID())
        }

        func close() {
            model.stopWindowObservation()
            model.settingsPersistenceTasks.values.forEach { $0.cancel() }
            model.synthesisSettingsTasks.values.forEach { $0.cancel() }
            Darwin.close(listener)
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
