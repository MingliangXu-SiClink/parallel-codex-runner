import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct ExecutionActivityTests {
    @Test
    func motionUsesOpacityWithoutLayoutTimersOrLoadingIndicators() throws {
        let view = NativeExecutionActivityView(frame: NSRect(x: 0, y: 0, width: 12, height: 12))
        view.layout()
        let frame = view.indicatorLayer.frame
        view.configure(isWorking: true, reduceMotion: false)
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
        view.updateVisibility(true)
        let pulse = try #require(view.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey)
            as? CABasicAnimation)
        #expect(pulse.keyPath == "opacity")
        #expect(pulse.duration == 1.5 && pulse.autoreverses)
        #expect(pulse.fromValue as? Double == 0.65)
        #expect(pulse.toValue as? Int == 1)
        #expect(view.indicatorLayer.animationKeys()?.count == 1)
        view.configure(isWorking: true, reduceMotion: false)
        #expect(view.indicatorLayer.animationKeys()?.count == 1)
        #expect(view.indicatorLayer.frame == frame)
        #expect(view.subviews.isEmpty)
        #expect(view.hitTest(.zero) == nil, "Decorative dots must not intercept Agent-tab clicks.")
        view.stop()
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
    }

    @Test
    func hiddenCompletedReducedMotionAndDetachedIndicatorsDoNotAnimate() {
        let view = NativeExecutionActivityView(frame: NSRect(x: 0, y: 0, width: 12, height: 12))
        view.configure(isWorking: true, reduceMotion: false)
        view.updateVisibility(true)
        #expect(view.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey) != nil)
        view.updateVisibility(false)
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
        view.updateVisibility(true)
        view.configure(isWorking: true, reduceMotion: true)
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
        view.configure(isWorking: false, reduceMotion: false)
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
        view.configure(isWorking: true, reduceMotion: false)
        view.stop()
        #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
    }

    @Test
    func eightAgentDotsAndSidebarDotShareOnePhaseWithoutResizing() throws {
        let dots = (0..<9).map { index -> NativeExecutionActivityView in
            let size: CGFloat = index == 8 ? 5 : 12
            let view = NativeExecutionActivityView(frame: NSRect(x: 0, y: 0, width: size, height: size))
            view.configure(isWorking: true, reduceMotion: false,
                           color: index == 8 ? .systemOrange : .systemBlue,
                           diameterFraction: index == 8 ? 1 : 2 / 3)
            view.layout()
            view.updateVisibility(true)
            return view
        }
        defer { dots.forEach { $0.stop() } }
        let frames = dots.map { $0.indicatorLayer.frame }
        let pulses = try dots.map {
            try #require($0.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey)
                as? CABasicAnimation)
        }
        #expect(Set(pulses.map(\.beginTime)).count == 1)
        #expect(pulses.allSatisfy { $0.keyPath == "opacity" && $0.duration == 1.5 && $0.autoreverses })
        #expect(frames.dropLast().allSatisfy { $0.size == CGSize(width: 8, height: 8) })
        #expect(frames.last?.size == CGSize(width: 5, height: 5))
        for (index, view) in dots.enumerated() {
            view.configure(isWorking: true, reduceMotion: false,
                           color: index == 8 ? .systemOrange : .systemBlue,
                           diameterFraction: index == 8 ? 1 : 2 / 3)
            #expect(view.indicatorLayer.frame == frames[index])
            #expect(view.indicatorLayer.animationKeys()?.count == 1)
            #expect((view.indicatorLayer.animation(forKey: NativeExecutionActivityView.animationKey)
                as? CABasicAnimation)?.beginTime == pulses[index].beginTime)
        }
    }

    @Test
    func nativeDotColorsFollowAppearanceAndStaticDotsNeverFade() throws {
        let view = NativeExecutionActivityView(frame: NSRect(x: 0, y: 0, width: 5, height: 5))
        defer { view.stop() }
        let color = NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? .systemOrange : .systemBlue
        }
        for appearance in [NSAppearance.Name.aqua, .darkAqua] {
            view.appearance = NSAppearance(named: appearance)
            view.configure(isWorking: false, reduceMotion: false, color: color, diameterFraction: 1)
            view.layout()
            view.updateVisibility(true)
            var expected: CGColor?
            view.effectiveAppearance.performAsCurrentDrawingAppearance { expected = color.cgColor }
            #expect(view.indicatorLayer.backgroundColor == expected)
            #expect(view.indicatorLayer.frame.size == CGSize(width: 5, height: 5))
            #expect(view.indicatorLayer.animationKeys()?.isEmpty != false)
            #expect(view.indicatorLayer.opacity == 1)
        }
    }

    @Test
    func workspaceMotionRequiresConfirmedLiveProcessingNotWarningsOrSavedMessages() throws {
        func workspace(status: String = "running", workerActive: Bool = true,
                       available: Bool = true) throws -> RemoteWorkspace {
            try JSONDecoder().decode(RemoteWorkspace.self, from: JSONSerialization.data(withJSONObject: [
                "workspaceId": "motion-qa", "path": "/tmp/qa", "name": "QA",
                "lastRunId": "motion-run", "lastResumeSessionId": "", "runStatus": status,
                "runWorkerActive": workerActive, "selected": false, "available": available,
                "readable": true, "writable": true,
            ]))
        }
        func active(_ workspace: RemoteWorkspace, pending: Bool = false, connected: Bool = true) -> Bool {
            ExecutionActivityState.isProcessing(workspace: workspace,
                hasPendingSubmission: pending, connectionConfirmed: connected)
        }
        for status in ["preparing", "starting", "running", "stopping", "finalizing"] {
            #expect(active(try workspace(status: status)))
        }
        #expect(!active(try workspace(), connected: false))
        #expect(!active(try workspace(), pending: true))
        #expect(!active(try workspace(workerActive: false)))
        #expect(!active(try workspace(available: false)))
        for status in ["queued", "completed", "finalized", "failed", "error", "sync_ambiguous", "cleanup_failed"] {
            #expect(!active(try workspace(status: status)))
        }
    }

    @Test
    func connectionConfirmationChangesAgentPresentationButNotItsSelection() {
        var state = ConversationAgentSwitchState(runID: "motion-qa", agents: [], selectedAgent: 2,
            recommendedAgent: nil, finalizedAgent: nil, selectionEnabled: true)
        let offline = state
        state.executionConfirmed = true
        #expect(state != offline)
        #expect(state.highlightedAgent == offline.highlightedAgent)
        #expect(state.pendingAgent == offline.pendingAgent)
        state.executionConfirmed = false
        #expect(state == offline)
    }

    @Test
    func cachedOfflineHistoricalQueuedSendingAndFinishingStatesNeverClaimLiveExecution() throws {
        func run(status: String = "running", workerActive: Bool = true,
                 agentStatus: String = "running") throws -> RunSummary {
            try JSONDecoder().decode(RunSummary.self, from: Data("""
            {"runId":"activity-qa","status":"\(status)","workspace":"/tmp/qa","prompt":"QA",
             "createdAt":"2026-10-04","updatedAt":"2026-10-04","config":{},
             "syncedBack":false,"workspacesDeleted":false,"codexHomesDeleted":false,
             "workerActive":\(workerActive),"workerAvailable":true,"eventCount":0,
             "agents":[{"agent":1,"name":"Agent 1","status":"\(agentStatus)","reasoningTokenCounts":{},"recommended":false,
                        "finalized":false,"rejected":false}]}
            """.utf8))
        }
        func active(_ run: RunSummary?, latest: Bool = true, pending: Bool = false,
                    connected: Bool = true) -> Bool {
            ExecutionActivityState.isWorking(run: run, isLatestTurn: latest,
                hasPendingSubmission: pending, connectionConfirmed: connected)
        }
        let running = try run()
        #expect(active(running))
        #expect(!active(nil))
        #expect(!active(running, connected: false))
        #expect(!active(running, latest: false))
        #expect(!active(running, pending: true))
        #expect(!active(try run(workerActive: false)))
        for status in ["preparing", "stopping", "finalizing", "finalized", "completed", "failed"] {
            #expect(!active(try run(status: status)))
        }
        for status in ["success", "queued", "starting", "waiting_for_models", "stopping"] {
            #expect(!active(try run(agentStatus: status)))
        }
    }
}
