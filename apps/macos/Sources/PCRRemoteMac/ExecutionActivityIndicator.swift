import AppKit
import SwiftUI

enum ExecutionActivityState {
    static func isWorking(run: RunSummary?, isLatestTurn: Bool,
                          hasPendingSubmission: Bool, connectionConfirmed: Bool) -> Bool {
        guard connectionConfirmed, isLatestTurn, !hasPendingSubmission,
              let run, run.workerActive, run.normalizedStatus == "running" else { return false }
        return run.agents.contains { $0.hasObservedActiveExecution }
    }

    static func isProcessing(workspace: RemoteWorkspace, hasPendingSubmission: Bool,
                             connectionConfirmed: Bool) -> Bool {
        guard connectionConfirmed, workspace.available, !hasPendingSubmission,
              !workspace.lastRunId.isEmpty, workspace.runWorkerActive == true else { return false }
        return ["preparing", "starting", "running", "stopping", "finalizing"]
            .contains((workspace.runStatus ?? "").lowercased())
    }
}

enum AgentActivityPresentation {
    static func clock(_ value: String?) -> String? {
        guard let value, let date = parseInternetDate(value) else { return nil }
        return clock(date: date)
    }

    static func clock(date: Date?) -> String? {
        date?.formatted(date: .omitted, time: .shortened)
    }

    static func detail(_ agent: RunAgentSummary?, language: AppLanguage, live: Bool, now: Date = Date()) -> String {
        guard let agent, agent.normalizedStatus == "running", let activity = agent.activity else { return "" }
        var parts: [String] = []
        if !live { parts.append(language.text("上次记录", "Last observed")) }
        if agent.isObservedWaiting {
            if let until = clock(date: activity.waitUntilDate) {
                if let deadline = activity.waitUntilDate, deadline <= now, live {
                    parts.append(language.text("计划时刻已到，等待结束待确认", "Planned wait elapsed; end unconfirmed"))
                } else {
                    parts.append(language.text("计划等待至 \(until)", "Planned wait until \(until)"))
                }
            } else {
                parts.append(language.text("等待截止时间未知", "Wait deadline unknown"))
            }
        } else if activity.activeOperationCount > 0 {
            parts.append(language.text("\(activity.activeOperationCount) 个工具运行中", "\(activity.activeOperationCount) tools running"))
        }
        if let output = clock(date: activity.lastOutputAtDate) {
            parts.append(language.text("最近输出 \(output)", "Last output \(output)"))
        } else if let observed = clock(date: activity.observedAtDate) {
            parts.append(language.text("最近活动 \(observed)", "Last activity \(observed)"))
        }
        if agent.isObservedWaiting {
            parts.append(language.text("后台进度未知", "External progress unknown"))
        }
        return parts.joined(separator: " · ")
    }
}

// The header, Agent tabs and sidebar share a compositor-only pulse. Animation
// never mutates SwiftUI state or drives transcript layout.
struct ExecutionActivityIndicator: NSViewRepresentable {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let isWorking: Bool
    var color: Color = .blue
    var diameterFraction: CGFloat = 2 / 3

    func makeNSView(context: Context) -> NativeExecutionActivityView {
        NativeExecutionActivityView(frame: .zero)
    }

    func updateNSView(_ view: NativeExecutionActivityView, context: Context) {
        view.configure(isWorking: isWorking, reduceMotion: reduceMotion,
                       color: NSColor(color), diameterFraction: diameterFraction)
    }

    static func dismantleNSView(_ view: NativeExecutionActivityView, coordinator: ()) {
        view.stop()
    }
}

final class NativeExecutionActivityView: NSView {
    static let animationKey = "execution-activity"
    private static let animationEpoch = CACurrentMediaTime()
    let indicatorLayer = CALayer()
    private var isWorking = false
    private var reduceMotion = false
    private var isVisible = false
    private var indicatorColor = NSColor.systemBlue
    private var diameterFraction: CGFloat = 2 / 3

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.addSublayer(indicatorLayer)
        setAccessibilityElement(false)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    deinit { NotificationCenter.default.removeObserver(self) }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func configure(isWorking: Bool, reduceMotion: Bool, color: NSColor = .systemBlue,
                   diameterFraction: CGFloat = 2 / 3) {
        self.isWorking = isWorking
        self.reduceMotion = reduceMotion
        if !indicatorColor.isEqual(color) {
            indicatorColor = color
            updateColor()
        }
        let fraction = min(1, max(0, diameterFraction))
        if self.diameterFraction != fraction {
            self.diameterFraction = fraction
            needsLayout = true
        }
        updateAnimation()
    }

    override func layout() {
        super.layout()
        let diameter = min(bounds.width, bounds.height) * diameterFraction
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        indicatorLayer.frame = CGRect(x: (bounds.width - diameter) / 2,
                                      y: (bounds.height - diameter) / 2,
                                      width: diameter, height: diameter)
        indicatorLayer.cornerRadius = diameter / 2
        CATransaction.commit()
        updateColor()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColor()
    }

    private func updateColor() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            indicatorLayer.backgroundColor = indicatorColor.cgColor
            CATransaction.commit()
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        NotificationCenter.default.removeObserver(self)
        if let window {
            NotificationCenter.default.addObserver(self, selector: #selector(refreshVisibility),
                name: NSWindow.didChangeOcclusionStateNotification, object: window)
        }
        refreshVisibility()
    }

    override func viewDidHide() {
        super.viewDidHide()
        refreshVisibility()
    }

    override func viewDidUnhide() {
        super.viewDidUnhide()
        refreshVisibility()
    }

    @objc private func refreshVisibility() {
        updateVisibility(window?.occlusionState.contains(.visible) == true && !isHiddenOrHasHiddenAncestor)
    }

    func updateVisibility(_ visible: Bool) {
        isVisible = visible
        updateAnimation()
    }

    func stop() {
        NotificationCenter.default.removeObserver(self)
        isVisible = false
        updateAnimation()
    }

    private func updateAnimation() {
        guard isWorking, isVisible, !reduceMotion else {
            indicatorLayer.removeAnimation(forKey: Self.animationKey)
            return
        }
        guard indicatorLayer.animation(forKey: Self.animationKey) == nil else { return }
        let pulse = CABasicAnimation(keyPath: "opacity")
        pulse.fromValue = 0.65
        pulse.toValue = 1
        pulse.duration = 1.5
        pulse.autoreverses = true
        pulse.repeatCount = .infinity
        pulse.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        pulse.beginTime = indicatorLayer.convertTime(Self.animationEpoch, from: nil)
        indicatorLayer.add(pulse, forKey: Self.animationKey)
    }
}
