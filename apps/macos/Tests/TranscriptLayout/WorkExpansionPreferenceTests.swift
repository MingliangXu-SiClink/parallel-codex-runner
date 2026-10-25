import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkExpansionPreferenceTests {
    @Test
    func changingDefaultReplacesRememberedAndManualExpansionImmediately() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let context = try #require(model.selectedAgentWorkContextKey)
        model.workExpandedByDefault = false
        model.collapsedAgentWorkContexts.insert(context)
        model.workProcess.sections[context + "|default"] = false
        model.workProcess.sections[context + "|work-summary-run-1-thought"] = false
        model.workProcess.expanded.insert(context + "|operation")
        model.workProcess.save()
        let collapsed = model.selectedTimelineBlocks
        #expect(!collapsed.contains { $0.kind == .thought })

        model.setWorkExpandedByDefault(true)
        #expect(model.showsSelectedAgentWork)
        #expect(model.selectedTimelineBlocks.contains { $0.kind == .thought })
        #expect(model.workProcess.sections.isEmpty)
        #expect(model.workProcess.expanded.contains(context + "|operation"))
        #expect(AppAppearanceStore.loadWorkExpandedByDefault())

        model.setWorkExpandedByDefault(false)
        #expect(!model.showsSelectedAgentWork)
        #expect(model.selectedTimelineBlocks == collapsed)
        #expect(!AppAppearanceStore.loadWorkExpandedByDefault())
    }

    @Test
    func runningAgentDoesNotForceWorkOpenAgainstPreference() throws {
        let fixture = try Fixture(running: true)
        defer { fixture.close() }
        let model = fixture.model
        model.workExpandedByDefault = false
        #expect(model.selectedAgentSummary?.isTerminal == false)
        #expect(!model.showsSelectedAgentWork)
        #expect(!model.selectedTimelineBlocks.contains { $0.kind == .thought })
        model.setWorkExpandedByDefault(true)
        #expect(model.selectedTimelineBlocks.contains { $0.kind == .thought })
        model.setWorkExpandedByDefault(false)
        model.transcripts[1]?.blocks.insert(TranscriptBlock(id: "new", kind: .thought, text: "New work"), at: 1)
        #expect(!model.selectedTimelineBlocks.contains { $0.kind == .thought })
    }

    @Test
    func oldInitialSnapshotsDoNotOverrideTheCurrentDefault() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        let context = try #require(model.selectedAgentWorkContextKey)
        model.workProcess.sections[context + "|default"] = true
        model.workExpandedByDefault = false
        #expect(!model.showsSelectedAgentWork)
        model.workProcess.sections[context + "|default"] = false
        model.workExpandedByDefault = true
        #expect(model.showsSelectedAgentWork)
    }

    @Test
    func manualSectionsSurviveStreamingUntilDefaultIsChangedAgain() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.workExpandedByDefault = false
        model.setWorkExpandedByDefault(true)
        let section = try #require(model.selectedTimelineBlocks.first { $0.activityType == "work_summary_expanded" })
        model.activateWorkBlock(section)
        #expect(!model.selectedTimelineBlocks.contains { $0.kind == .thought })
        model.transcripts[1]?.blocks.insert(TranscriptBlock(id: "new", kind: .thought, text: "New work"), at: 1)
        #expect(!model.selectedTimelineBlocks.contains { $0.kind == .thought })
        model.setWorkExpandedByDefault(false)
        model.setWorkExpandedByDefault(true)
        #expect(model.selectedTimelineBlocks.contains { $0.kind == .thought })
    }

    @Test
    func preferenceAlsoAppliesWhenReturningToAnotherAgent() throws {
        let fixture = try Fixture()
        defer { fixture.close() }
        let model = fixture.model
        model.workExpandedByDefault = false
        model.selectedAgent = 2
        model.toggleSelectedAgentWork()
        #expect(model.showsSelectedAgentWork)
        model.selectedAgent = 1
        model.setWorkExpandedByDefault(true)
        model.setWorkExpandedByDefault(false)
        model.selectedAgent = 2
        #expect(!model.showsSelectedAgentWork)
        #expect(!model.selectedTimelineBlocks.contains { $0.kind == .thought })
    }

    @Test
    func settingUpdatesExistingWindowsAndPersistsForReopenedWindows() throws {
        let main = try Fixture()
        defer { main.close() }
        main.model.workExpandedByDefault = true
        main.model.setWorkExpandedByDefault(false)
        let secondary = try Fixture()
        defer { secondary.close() }
        let context = try #require(secondary.model.selectedAgentWorkContextKey)
        secondary.model.collapsedAgentWorkContexts.insert(context)
        secondary.model.workProcess.sections[context + "|work-summary-run-1-thought"] = false
        secondary.model.workProcess.expanded.insert(context + "|operation")
        let collapsed = secondary.model.selectedTimelineBlocks

        main.model.setWorkExpandedByDefault(true)
        #expect(secondary.model.workExpandedByDefault)
        #expect(secondary.model.selectedTimelineBlocks.contains { $0.kind == .thought })
        #expect(secondary.model.workProcess.expanded.contains(context + "|operation"))
        let reopened = AppModel(windowID: UUID())
        defer { reopened.stopWindowObservation() }
        #expect(reopened.workExpandedByDefault)
        #expect(reopened.workProcess.sections.isEmpty)

        main.model.setWorkExpandedByDefault(false)
        #expect(!secondary.model.workExpandedByDefault)
        #expect(secondary.model.selectedTimelineBlocks == collapsed)
        #expect(!reopened.workExpandedByDefault)
    }

    @MainActor
    private final class Fixture {
        let model: AppModel
        let saved: [String: Any]
        let keys = ["ParallelCodex.WorkExpandedByDefault", "ParallelCodex.WorkSections", "ParallelCodex.WorkOperations"]

        init(running: Bool = false) throws {
            saved = Dictionary(uniqueKeysWithValues: keys.compactMap { key in
                UserDefaults.standard.object(forKey: key).map { (key, $0) }
            })
            model = AppModel(windowID: UUID())
            model.workProcess.sections = [:]
            model.workProcess.expanded = []
            model.selectedWorkspaceID = "work-expansion-test"
            model.activeRun = try JSONDecoder().decode(RunSummary.self, from: JSONSerialization.data(withJSONObject: [
                "runId": "run", "status": running ? "running" : "completed", "workspace": "/qa", "prompt": "Question",
                "createdAt": "2026-09-18T00:00:00Z", "updatedAt": "2026-09-18T00:00:00Z", "config": [:],
                "syncedBack": false, "workspacesDeleted": false, "codexHomesDeleted": false,
                "workerActive": running, "workerAvailable": false, "eventCount": 1,
                "agents": (1...2).map { ["agent": $0, "name": "Agent \($0)", "status": running ? "running" : "success",
                    "reasoningTokenCounts": [:], "rejected": false, "recommended": false, "finalized": false] },
            ]))
            model.selectedAgent = 1
            for agent in 1...2 {
                model.transcripts[agent] = AgentTranscript(sourceRunID: "run", sourceAgent: agent,
                    blocks: [
                        TranscriptBlock(id: "thought", kind: .thought, text: "Planning work"),
                        TranscriptBlock(id: "answer", kind: .output, text: "Answer"),
                    ],
                    hasMore: false, finalMessageHasMore: false)
            }
        }

        func close() {
            model.stopWindowObservation()
            for key in keys { UserDefaults.standard.set(saved[key], forKey: key) }
        }
    }
}
