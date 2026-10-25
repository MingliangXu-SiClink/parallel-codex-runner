import AppKit
import QuartzCore

final class ConversationTimelineScrollView: TranscriptViewportScrollView {
    static let scrollIdleInterval: TimeInterval = 0.18
    let historyRail = ConversationHistoryRailView()
    var onUserScroll: (() -> Void)?
    var onScrollEvent: ((NSEvent) -> Void)?
    var onViewportHeightChange: (() -> Void)?
    var obscuredTopHeight: CGFloat = 0
    private(set) var ownsScrollGesture = false
    private var lastScrollTimestamp: TimeInterval?
    private var usesNestedScrollDisplacement = false
    private var lastViewportHeight: CGFloat = 0

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        addSubview(historyRail)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        historyRail.frame = bounds
        if abs(contentSize.height - lastViewportHeight) > 0.5 {
            lastViewportHeight = contentSize.height
            onViewportHeightChange?()
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil { resetScrollGestureOwnership() }
    }

    override func scrollWheel(with event: NSEvent) {
        beginScrollEvent(event)
        claimScrollGesture()
        defer { endScrollEvent(event) }
        onUserScroll?()
        onScrollEvent?(event)
        historyRail.dismissPreview()
        if usesNestedScrollDisplacement {
            applyVerticalScroll(event.scrollingDeltaY * (event.hasPreciseScrollingDeltas ? 1 : 12))
        } else {
            super.scrollWheel(with: event)
        }
    }

    func beginScrollEvent(_ event: NSEvent) {
        let timeout: TimeInterval = event.phase.isEmpty && event.momentumPhase.isEmpty
            ? Self.scrollIdleInterval : 1
        if event.phase.contains(.began) || event.phase.contains(.mayBegin)
            || lastScrollTimestamp.map({ event.timestamp < $0
                || event.timestamp - $0 > timeout }) == true {
            resetScrollGestureOwnership()
        }
        lastScrollTimestamp = event.timestamp
    }

    func endScrollEvent(_ event: NSEvent) {
        if event.phase.contains(.cancelled) || event.momentumPhase.contains(.ended)
            || event.momentumPhase.contains(.cancelled) {
            resetScrollGestureOwnership()
        }
    }

    func claimScrollGesture() { ownsScrollGesture = true }

    func resetScrollGestureOwnership() {
        ownsScrollGesture = false
        usesNestedScrollDisplacement = false
        lastScrollTimestamp = nil
    }

    func scrollFromNestedWork(verticalDelta: CGFloat) {
        // AppKit did not receive this gesture's begin. Keep using the same
        // displacement path if hit testing later sends its events here.
        usesNestedScrollDisplacement = true
        onUserScroll?()
        historyRail.dismissPreview()
        applyVerticalScroll(verticalDelta)
    }

    private func applyVerticalScroll(_ verticalDelta: CGFloat) {
        guard let documentView, verticalDelta != 0 else { return }
        var point = contentView.bounds.origin
        let minimum = documentView.bounds.minY
        let maximum = max(minimum, documentView.bounds.maxY - contentSize.height)
        point.y = min(maximum, max(minimum,
            point.y + (documentView.isFlipped ? -verticalDelta : verticalDelta)))
        // The outer AppKit scroll view has not seen the inner gesture's began
        // event, so apply its residual directly, including mid-stream momentum.
        contentView.scroll(to: point)
        reflectScrolledClipView(contentView)
    }
}

final class ConversationHistoryRailView: NSView {
    private(set) var entries: [ConversationHistoryEntry] = []
    private(set) var activeIndex: Int?
    private(set) var hoveredIndex: Int?
    var navigate: ((String) -> Void)?
    var reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    private var language: AppLanguage = .english
    private var textSize: AppTextSize = .standard
    private var transcriptAppearance: TranscriptAppearance = .light
    private var tickLayers: [CALayer] = []
    private var tracking: NSTrackingArea?
    private var accessibilityButtons: [HistoryQuestionAccessibilityElement] = []
    private let preview = NSView()
    private let previewTitle = NSTextField(labelWithString: "")
    private let previewText = NSTextField(wrappingLabelWithString: "")
    private var focusRestoration: ConversationSearchFocus?

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }

    override func cancelOperation(_ sender: Any?) {
        dismissPreview()
        let restored = focusRestoration?.restore() ?? false
        // Native focus transfer can precede mouseDown, leaving no previous
        // responder to capture. The window already registers its composer.
        if !restored, let window, let editor = window.initialFirstResponder,
           editor !== self, editor.window === window,
           !editor.isHiddenOrHasHiddenAncestor, editor.acceptsFirstResponder {
            window.makeFirstResponder(editor)
        }
        focusRestoration = nil
    }

    var geometry: ConversationHistoryRailGeometry {
        ConversationHistoryRailGeometry(count: entries.count, height: bounds.height)
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        preview.wantsLayer = true
        preview.layer?.cornerRadius = ParallelCodexVisualStyle.Radius.panel
        preview.layer?.borderWidth = 1
        preview.layer?.shadowColor = NSColor.black.cgColor
        preview.layer?.shadowRadius = 12
        preview.layer?.shadowOffset = CGSize(width: 0, height: -3)
        preview.isHidden = true
        preview.setAccessibilityElement(false)
        for label in [previewTitle, previewText] {
            label.setAccessibilityElement(false)
            label.isSelectable = false
            preview.addSubview(label)
        }
        previewText.maximumNumberOfLines = 6
        previewText.lineBreakMode = .byWordWrapping
        addSubview(preview)
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(accessibilityOptionsChanged),
            name: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil
        )
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func accessibilityOptionsChanged() {
        reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        renderTicks(animated: false)
        updatePreview(animated: false)
    }

    func configure(
        entries: [ConversationHistoryEntry], language: AppLanguage,
        textSize: AppTextSize, appearance: TranscriptAppearance, resetsInteraction: Bool
    ) {
        let changed = self.entries != entries
        let presentationChanged = self.language != language || self.textSize != textSize
            || transcriptAppearance != appearance
        guard changed || presentationChanged || resetsInteraction else { return }
        self.language = language
        self.textSize = textSize
        transcriptAppearance = appearance
        setAccessibilityLabel(language.text("历史提问", "Question history"))
        if changed || resetsInteraction {
            dismissPreview()
            activeIndex = nil
        }
        if resetsInteraction { focusRestoration = nil }
        self.entries = entries
        if changed || resetsInteraction || accessibilityButtons.isEmpty {
            accessibilityButtons = entries.enumerated().map { index, entry in
                let element = HistoryQuestionAccessibilityElement()
                element.setAccessibilityParent(self)
                element.setAccessibilityRole(.button)
                element.setAccessibilityEnabled(true)
                element.setAccessibilityLabel("\(index + 1). \(entry.text.prefix(1200))")
                element.activate = { [weak self] in self?.activateEntry(at: index) }
                return element
            }
        }
        needsLayout = true
        layoutSubtreeIfNeeded()
        renderTicks(animated: false)
    }

    func setActiveIndex(_ index: Int?) {
        guard activeIndex != index else { return }
        activeIndex = index
        renderTicks(animated: false)
    }

    override func layout() {
        super.layout()
        isHidden = entries.count < 2 || bounds.height < 150 || bounds.width < 180
        guard !isHidden else { dismissPreview(); return }
        let geometry = geometry
        while tickLayers.count > geometry.tickCount {
            tickLayers.removeLast().removeFromSuperlayer()
        }
        while tickLayers.count < geometry.tickCount {
            let tick = CALayer()
            tick.name = "history-tick"
            tick.anchorPoint = CGPoint(x: 0, y: 0.5)
            tick.cornerRadius = 1
            layer?.addSublayer(tick)
            tickLayers.append(tick)
        }
        renderTicks(animated: false)
        updatePreview(animated: false)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let next = NSTrackingArea(
            rect: .zero,
            options: [.activeInKeyWindow, .inVisibleRect, .mouseMoved, .mouseEnteredAndExited],
            owner: self, userInfo: nil
        )
        addTrackingArea(next)
        tracking = next
    }

    private func isOverRail(_ point: CGPoint) -> Bool {
        point.x >= 0 && point.x <= 44
            && point.y >= geometry.firstY - 14
            && point.y <= geometry.firstY + geometry.length + 14
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = convert(point, from: superview)
        guard !isHidden, bounds.contains(local) else { return nil }
        if isOverRail(local) || (!preview.isHidden && preview.frame.contains(local)) { return self }
        return nil
    }

    override func mouseEntered(with event: NSEvent) { mouseMoved(with: event) }

    override func mouseMoved(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        if isOverRail(point) {
            hover(at: geometry.index(at: point.y))
        } else if preview.isHidden || !preview.frame.insetBy(dx: -10, dy: 0).contains(point) {
            dismissPreview()
        }
    }

    override func mouseExited(with event: NSEvent) { dismissPreview() }

    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        if window?.firstResponder !== self {
            focusRestoration = ConversationSearchFocus(window: window)
            window?.makeFirstResponder(self)
        }
        if isOverRail(point), let index = geometry.index(at: point.y) {
            activateEntry(at: index)
        } else if !preview.isHidden, let hoveredIndex {
            activateEntry(at: hoveredIndex)
        }
    }

    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 125: hover(at: min(entries.count - 1, (hoveredIndex ?? activeIndex ?? -1) + 1))
        case 126: hover(at: max(0, (hoveredIndex ?? activeIndex ?? 1) - 1))
        case 36, 76:
            if let index = hoveredIndex ?? activeIndex { activateEntry(at: index) }
        case 53:
            cancelOperation(nil)
        default: super.keyDown(with: event)
        }
    }

    override func scrollWheel(with event: NSEvent) {
        dismissPreview()
        enclosingScrollView?.scrollWheel(with: event)
    }

    func hover(at index: Int?) {
        let index = index.flatMap { entries.indices.contains($0) ? $0 : nil }
        guard hoveredIndex != index else { return }
        hoveredIndex = index
        renderTicks(animated: true)
        updatePreview(animated: true)
    }

    func dismissPreview() { hover(at: nil) }

    func activateEntry(at index: Int) {
        guard entries.indices.contains(index) else { return }
        let id = entries[index].id
        dismissPreview()
        navigate?(id)
    }

    private func renderTicks(animated: Bool) {
        let geometry = geometry
        let foreground = ParallelCodexVisualStyle.resolvedNSColor(.labelColor, for: transcriptAppearance)
        let hoveredY = hoveredIndex.map { geometry.y(for: $0) }
        for (index, tick) in tickLayers.enumerated() {
            let y = geometry.y(for: geometry.entryIndex(forTick: index))
            let active = activeIndex.map { geometry.tickIndex(forEntry: $0) == index } ?? false
            let distance = hoveredY.map { abs($0 - y) }
            let width = ConversationHistoryRailGeometry.width(distance: distance, active: active)
            let focused = hoveredIndex.map { geometry.tickIndex(forEntry: $0) == index } ?? false
            let alpha: Float = focused ? 0.95 : active ? 0.65 : hoveredY == nil ? 0.22 : 0.35
            let previousWidth = tick.presentation()?.bounds.width ?? tick.bounds.width
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            tick.position = CGPoint(x: 8, y: y)
            tick.bounds = CGRect(x: 0, y: 0, width: width, height: focused || active ? 2.5 : 2)
            tick.backgroundColor = foreground.cgColor
            tick.opacity = alpha
            CATransaction.commit()
            if animated, !reduceMotion, abs(previousWidth - width) > 0.2 {
                let animation = CASpringAnimation(keyPath: "bounds.size.width")
                animation.fromValue = previousWidth
                animation.toValue = width
                animation.mass = 1
                animation.stiffness = 480
                animation.damping = 32
                animation.duration = animation.settlingDuration
                tick.add(animation, forKey: "history-hover")
            } else {
                tick.removeAnimation(forKey: "history-hover")
            }
        }
    }

    private func updatePreview(animated: Bool) {
        guard let index = hoveredIndex, entries.indices.contains(index), !isHidden else {
            preview.layer?.removeAllAnimations()
            preview.isHidden = true
            return
        }
        let wasHidden = preview.isHidden
        let oldPosition = preview.layer?.presentation()?.position ?? preview.layer?.position
        let scale = textSize.scale
        let width = min(360 * scale, bounds.width - 62)
        let textWidth = width - 28
        previewTitle.font = .systemFont(ofSize: 11 * scale, weight: .medium)
        previewTitle.stringValue = language.text("提问 \(index + 1)", "Question \(index + 1)")
        previewText.font = .systemFont(ofSize: 13 * scale)
        previewText.preferredMaxLayoutWidth = textWidth
        previewText.stringValue = String(entries[index].text.prefix(1000))
        let measured = previewText.attributedStringValue.boundingRect(
            with: NSSize(width: textWidth, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading]
        ).height
        let titleHeight = 15 * scale
        let textHeight = min(6 * 18 * scale, ceil(measured) + 4,
                             max(20 * scale, bounds.height - titleHeight - 56))
        let height = textHeight + titleHeight + 32
        let y = min(max(geometry.y(for: index) - height / 2, 12), max(12, bounds.height - height - 12))
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        preview.frame = NSRect(x: 46, y: y, width: width, height: height)
        previewTitle.frame = NSRect(x: 14, y: height - 14 - titleHeight, width: textWidth, height: titleHeight)
        previewText.frame = NSRect(x: 14, y: 14, width: textWidth, height: textHeight)
        previewTitle.textColor = ParallelCodexVisualStyle.resolvedNSColor(.secondaryLabelColor, for: transcriptAppearance)
        previewText.textColor = ParallelCodexVisualStyle.resolvedNSColor(.labelColor, for: transcriptAppearance)
        preview.layer?.backgroundColor = ParallelCodexVisualStyle.resolvedNSColor(
            ParallelCodexVisualStyle.userSurfaceNSColor, for: transcriptAppearance
        ).cgColor
        preview.layer?.borderColor = ParallelCodexVisualStyle.resolvedNSColor(
            ParallelCodexVisualStyle.separatorNSColor, for: transcriptAppearance
        ).cgColor
        preview.layer?.shadowOpacity = transcriptAppearance == .dark ? 0.28 : 0.12
        preview.isHidden = false
        CATransaction.commit()
        if animated, !reduceMotion, let previewLayer = preview.layer {
            if wasHidden {
                let fade = CABasicAnimation(keyPath: "opacity")
                fade.fromValue = 0
                fade.toValue = 1
                fade.duration = ParallelCodexVisualStyle.Motion.quick
                previewLayer.add(fade, forKey: "history-preview")
            } else if let oldPosition {
                let movement = CABasicAnimation(keyPath: "position")
                movement.fromValue = oldPosition
                movement.toValue = previewLayer.position
                movement.duration = ParallelCodexVisualStyle.Motion.quick
                movement.timingFunction = CAMediaTimingFunction(name: .easeOut)
                previewLayer.add(movement, forKey: "history-preview")
            }
        } else {
            preview.layer?.removeAllAnimations()
        }
    }

    override func accessibilityChildren() -> [Any]? {
        guard !isHidden else { return [] }
        for (index, element) in accessibilityButtons.enumerated() {
            element.setAccessibilityFrameInParentSpace(NSRect(
                x: 0, y: bounds.height - geometry.y(for: index) - 6, width: 44, height: 12
            ))
        }
        return accessibilityButtons
    }
}

private final class HistoryQuestionAccessibilityElement: NSAccessibilityElement {
    var activate: (() -> Void)?

    override func accessibilityPerformPress() -> Bool {
        guard let activate else { return false }
        activate()
        return true
    }
}
