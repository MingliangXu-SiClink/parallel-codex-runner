import AppKit
import CryptoKit
import SwiftUI

final class TranscriptFlowLayout: NSCollectionViewFlowLayout {
    override func shouldInvalidateLayout(forBoundsChange newBounds: NSRect) -> Bool {
        abs(newBounds.width - (collectionView?.bounds.width ?? 0)) > 0.5
            || super.shouldInvalidateLayout(forBoundsChange: newBounds)
    }

    override func invalidationContext(forBoundsChange newBounds: NSRect) -> NSCollectionViewLayoutInvalidationContext {
        let context = super.invalidationContext(forBoundsChange: newBounds)
        if abs(newBounds.width - (collectionView?.bounds.width ?? 0)) > 0.5,
           let flowContext = context as? NSCollectionViewFlowLayoutInvalidationContext {
            // Requery row widths, not only positions, when a review pane resizes the viewport.
            flowContext.invalidateFlowLayoutDelegateMetrics = true
        }
        return context
    }
}

final class WidthAwareCollectionView: NSCollectionView {
    var onLiveResizeBegan: (() -> Void)?
    var onWidthChange: ((Bool) -> Void)?
    var onLiveResizeEnded: (() -> Void)?
    private var previousWidth: CGFloat = 0

    override func viewWillStartLiveResize() {
        super.viewWillStartLiveResize()
        onLiveResizeBegan?()
    }

    override func layout() {
        if abs(bounds.width - previousWidth) > 0.5 {
            previousWidth = bounds.width
            // Update delegate widths before flow layout consults its cached
            // item sizes, including when a neighboring review column opens.
            onWidthChange?(inLiveResize)
            collectionViewLayout?.invalidateLayout()
        }
        super.layout()
    }

    override func viewDidEndLiveResize() {
        super.viewDidEndLiveResize()
        onLiveResizeEnded?()
    }
}

private struct AgentSwitchHostingConfiguration: Equatable {
    let language: AppLanguage
    let textSize: AppTextSize
    let accentColor: AppAccentColor
    let state: ConversationAgentSwitchState
    let resultsInspectorVisible: Bool
    let accessibilityHidden: Bool
}

private final class AgentSwitchHostingModel: ObservableObject {
    @Published private(set) var configuration: AgentSwitchHostingConfiguration
    private var selectAgentAction: (Int) -> Void
    private var toggleResultsAction: () -> Void

    init(
        configuration: AgentSwitchHostingConfiguration,
        selectAgent: @escaping (Int) -> Void,
        toggleResults: @escaping () -> Void
    ) {
        self.configuration = configuration
        self.selectAgentAction = selectAgent
        self.toggleResultsAction = toggleResults
    }

    func update(
        configuration: AgentSwitchHostingConfiguration,
        selectAgent: @escaping (Int) -> Void,
        toggleResults: @escaping () -> Void
    ) {
        selectAgentAction = selectAgent
        toggleResultsAction = toggleResults
        if self.configuration != configuration {
            self.configuration = configuration
        }
    }

    func selectAgent(_ agent: Int) {
        selectAgentAction(agent)
    }

    func toggleResults() {
        toggleResultsAction()
    }
}

private struct HostedAgentSwitchBar: View {
    @ObservedObject var model: AgentSwitchHostingModel

    var body: some View {
        let configuration = model.configuration
        ConversationAgentSwitchBar(
            language: configuration.language,
            textSize: configuration.textSize,
            accentColor: configuration.accentColor.color,
            state: configuration.state,
            resultsInspectorVisible: configuration.resultsInspectorVisible,
            accessibilityHidden: configuration.accessibilityHidden,
            selectAgent: model.selectAgent,
            toggleResults: model.toggleResults
        )
    }
}

final class AgentSwitchTrackingHostingView<Content: View>: NSHostingView<Content> {
    var interactionChanged: ((Bool) -> Void)?
    private var eventMonitor: Any?
    private var clickInProgress = false

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        removeEventMonitor()
        guard window != nil else {
            finishClick()
            return
        }
        eventMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .leftMouseUp]
        ) { [weak self] event in
            self?.observeMouseEvent(event)
            return event
        }
    }

    private func observeMouseEvent(_ event: NSEvent) {
        guard event.window === window else { return }
        switch event.type {
        case .leftMouseDown:
            let point = convert(event.locationInWindow, from: nil)
            guard bounds.contains(point), !clickInProgress else { return }
            clickInProgress = true
            interactionChanged?(true)
        case .leftMouseUp:
            guard clickInProgress else { return }
            clickInProgress = false
            // Local monitors run before SwiftUI dispatches the Button action.
            // Release the update gate on the next main-loop turn so the action
            // always sees the same control tree that received mouse-down.
            DispatchQueue.main.async { [weak self] in
                self?.interactionChanged?(false)
            }
        default:
            break
        }
    }

    private func finishClick() {
        guard clickInProgress else { return }
        clickInProgress = false
        interactionChanged?(false)
    }

    private func removeEventMonitor() {
        guard let eventMonitor else { return }
        NSEvent.removeMonitor(eventMonitor)
        self.eventMonitor = nil
    }
}

final class AgentSwitchCollectionItem: NSCollectionViewItem {
    static let identifier = NSUserInterfaceItemIdentifier("AgentSwitchCollectionItem")

    static func preferredHeight(for textSize: AppTextSize) -> CGFloat {
        ceil(max(
            42,
            ConversationAgentSwitchBar.minimumHeight(for: textSize) + 6 * textSize.scale
        ))
    }

    private var hostingModel: AgentSwitchHostingModel?
    private var hostingView: AgentSwitchTrackingHostingView<HostedAgentSwitchBar>?

    override func loadView() {
        view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = true
        view.wantsLayer = true
    }

    func configure(
        language: AppLanguage,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        state: ConversationAgentSwitchState,
        resultsInspectorVisible: Bool,
        accessibilityHidden: Bool,
        selectAgent: @escaping (Int) -> Void,
        toggleResults: @escaping () -> Void,
        interactionChanged: @escaping (Bool) -> Void
    ) {
        let configuration = AgentSwitchHostingConfiguration(
            language: language,
            textSize: textSize,
            accentColor: accentColor,
            state: state,
            resultsInspectorVisible: resultsInspectorVisible,
            accessibilityHidden: accessibilityHidden
        )
        if let hostingModel {
            // Keep one hosting root for the lifetime of the collection item.
            // Replacing NSHostingView.rootView on every run poll tears down the
            // SwiftUI button tree and can swallow a mouse-up while the user is
            // choosing another running Agent.
            hostingModel.update(
                configuration: configuration,
                selectAgent: selectAgent,
                toggleResults: toggleResults
            )
            hostingView?.interactionChanged = interactionChanged
            return
        }

        let hostingModel = AgentSwitchHostingModel(
            configuration: configuration,
            selectAgent: selectAgent,
            toggleResults: toggleResults
        )
        let hostingView = AgentSwitchTrackingHostingView(
            rootView: HostedAgentSwitchBar(model: hostingModel)
        )
        hostingView.interactionChanged = interactionChanged
        hostingView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(hostingView)
        NSLayoutConstraint.activate([
            hostingView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            hostingView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            hostingView.topAnchor.constraint(equalTo: view.topAnchor),
            hostingView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        self.hostingModel = hostingModel
        self.hostingView = hostingView
    }
}

final class TranscriptCollectionItem: NSCollectionViewItem, NSTextViewDelegate {
    static let identifier = NSUserInterfaceItemIdentifier("TranscriptCollectionItem")

    private let iconView = NSImageView()
    private let disclosureView = NSImageView()
    private let bodyView = ActionableTranscriptBodyView()
    private let workDisclosureButton = WorkDisclosureButton()
    private let stack = NSStackView()
    private let metaRow = NSStackView()
    private let metaSpacer = NSView()
    private let titleField = NSTextField(labelWithString: "")
    // Measurement and Markdown tables use TextKit 1. Do not create TextKit 2
    // drawing fragments first and switch engines during the first layout.
    private let textView = NSTextView(usingTextLayoutManager: false)
    private let statusField = NSTextField(labelWithString: "")
    private var bodyLeading: NSLayoutConstraint!
    private var bodyTrailing: NSLayoutConstraint!
    private var iconLeading: NSLayoutConstraint!
    private var iconTop: NSLayoutConstraint!
    private var bodyWidth: NSLayoutConstraint!
    private var textHeight: NSLayoutConstraint!
    private var stackLeading: NSLayoutConstraint!
    private var stackTrailing: NSLayoutConstraint!
    private var stackTop: NSLayoutConstraint!
    private var stackBottom: NSLayoutConstraint!
    private var openURL: ((URL) -> Void)?
    private var representedBlockID: String?
    private var representedBlock: TranscriptBlock?
    private var representedTextSize: AppTextSize?
    private var representedAccentColor: AppAccentColor?
    private var representedAppearance: TranscriptAppearance?
    private var representedAttributedText: NSAttributedString?
    private var currentFindRange: NSRange?
    private var disclosureLeading: NSLayoutConstraint!

    override func loadView() {
        view = NSView()
        // NSCollectionView owns and resizes the item root view. Disabling its
        // autoresizing-mask constraints leaves descendants at their initial
        // intrinsic height even when the flow layout assigns a tall frame.
        view.translatesAutoresizingMaskIntoConstraints = true
        // macOS 14 stopped clipping NSView children by default. A recycled
        // long reply can otherwise draw outside its collapsed collection row,
        // over the next conversation, even though it is absent from its AX tree.
        view.clipsToBounds = true

        iconView.translatesAutoresizingMaskIntoConstraints = false
        iconView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 15, weight: .medium)
        iconView.contentTintColor = .secondaryLabelColor

        bodyView.translatesAutoresizingMaskIntoConstraints = false
        bodyView.wantsLayer = true
        bodyView.clipsToBounds = true
        bodyView.layer?.cornerRadius = 0

        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.orientation = .vertical
        stack.alignment = .width
        stack.spacing = 6
        stack.distribution = .fill

        metaRow.translatesAutoresizingMaskIntoConstraints = false
        metaRow.orientation = .horizontal
        metaRow.alignment = .centerY
        metaRow.spacing = 7
        metaRow.distribution = .fill
        metaSpacer.translatesAutoresizingMaskIntoConstraints = false
        metaSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        metaSpacer.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        titleField.font = .systemFont(ofSize: NSFont.smallSystemFontSize, weight: .medium)
        titleField.textColor = .secondaryLabelColor
        titleField.lineBreakMode = .byTruncatingTail

        textView.translatesAutoresizingMaskIntoConstraints = false
        textView.isEditable = false
        textView.isSelectable = true
        textView.isRichText = true
        // Own the pixels behind glyphs. A transparent, reused text surface
        // otherwise relies on an ancestor repainting the previous layout.
        textView.drawsBackground = true
        textView.importsGraphics = false
        textView.allowsImageEditing = false
        textView.isHorizontallyResizable = false
        // Only the collection layout owns the height; NSTextView must not grow
        // itself past the row while Auto Layout is updating reused content.
        textView.isVerticallyResizable = false
        textView.clipsToBounds = true
        textView.textContainerInset = .zero
        textView.textContainer?.lineFragmentPadding = 0
        textView.textContainer?.widthTracksTextView = false
        textView.textContainer?.heightTracksTextView = false
        textView.usesFindPanel = true
        textView.delegate = self
        // Transcript rows are selectable, not editable controls. Exposing an
        // NSTextView's default text-area role makes VoiceOver and keyboard
        // navigation announce every message as an input field.
        textView.setAccessibilityRole(.staticText)
        textView.linkTextAttributes = [
            .foregroundColor: NSColor.linkColor,
            .underlineStyle: NSUnderlineStyle.single.rawValue,
        ]
        textHeight = textView.heightAnchor.constraint(equalToConstant: 1)
        // The flow layout owns the row height. While it is replacing a reused
        // item's layout attributes, AppKit can briefly expose the old height.
        // Let the text compress for that transient pass instead of forcing the
        // window server to resolve a required-constraint conflict.
        textHeight.priority = .init(999)
        textHeight.isActive = true

        statusField.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        statusField.textColor = .secondaryLabelColor

        metaRow.addArrangedSubview(titleField)
        metaRow.addArrangedSubview(metaSpacer)
        metaRow.addArrangedSubview(statusField)
        stack.addArrangedSubview(metaRow)
        stack.addArrangedSubview(textView)
        bodyView.addSubview(stack)
        view.addSubview(iconView)
        view.addSubview(bodyView)
        disclosureView.translatesAutoresizingMaskIntoConstraints = false
        disclosureView.contentTintColor = .tertiaryLabelColor
        disclosureView.setAccessibilityHidden(true)
        view.addSubview(disclosureView)
        workDisclosureButton.translatesAutoresizingMaskIntoConstraints = false
        workDisclosureButton.isHidden = true
        view.addSubview(workDisclosureButton)

        bodyLeading = bodyView.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 32)
        bodyTrailing = bodyView.trailingAnchor.constraint(equalTo: view.trailingAnchor)
        iconLeading = iconView.leadingAnchor.constraint(equalTo: view.leadingAnchor)
        iconTop = iconView.topAnchor.constraint(equalTo: textView.topAnchor)
        bodyWidth = bodyView.widthAnchor.constraint(equalToConstant: 200)
        stackLeading = stack.leadingAnchor.constraint(equalTo: bodyView.leadingAnchor)
        stackTrailing = stack.trailingAnchor.constraint(equalTo: bodyView.trailingAnchor)
        stackTop = stack.topAnchor.constraint(equalTo: bodyView.topAnchor)
        stackBottom = stack.bottomAnchor.constraint(lessThanOrEqualTo: bodyView.bottomAnchor)
        disclosureLeading = disclosureView.leadingAnchor.constraint(equalTo: textView.leadingAnchor)
        // NSStackView.edgeInsets creates required internal padding constraints.
        // Reused collection items briefly retain their previous row height;
        // making vertical padding compressible prevents an unavoidable
        // constraint fault when that temporary height is smaller than the new
        // top and bottom padding combined.
        stackTop.priority = .init(999)
        stackBottom.priority = .init(999)
        NSLayoutConstraint.activate([
            iconLeading,
            iconTop,
            iconView.widthAnchor.constraint(equalToConstant: 24),
            iconView.heightAnchor.constraint(equalToConstant: 24),
            bodyView.topAnchor.constraint(equalTo: view.topAnchor),
            bodyView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            bodyLeading,
            bodyTrailing,
            stackLeading,
            stackTrailing,
            stackTop,
            stackBottom,
            disclosureLeading,
            disclosureView.centerYAnchor.constraint(equalTo: iconView.centerYAnchor),
            disclosureView.widthAnchor.constraint(equalToConstant: 14),
            disclosureView.heightAnchor.constraint(equalToConstant: 14),
            workDisclosureButton.leadingAnchor.constraint(equalTo: iconView.leadingAnchor),
            workDisclosureButton.trailingAnchor.constraint(equalTo: bodyView.trailingAnchor, constant: 6),
            workDisclosureButton.topAnchor.constraint(equalTo: view.topAnchor),
            workDisclosureButton.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
    }

    override func prepareForReuse() {
        super.prepareForReuse()
        view.isHidden = true
        openURL = nil
        bodyView.action = nil
        workDisclosureButton.onPress = nil
        workDisclosureButton.isHidden = true
        workDisclosureButton.resetInteraction()
        representedBlockID = nil
        representedBlock = nil
        representedTextSize = nil
        representedAccentColor = nil
        representedAppearance = nil
        representedAttributedText = nil
        currentFindRange = nil
        textView.textStorage?.setAttributedString(NSAttributedString())
        textView.needsDisplay = true
        textView.toolTip = nil
    }

    func configure(
        block: TranscriptBlock,
        language: AppLanguage,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        appearance: TranscriptAppearance,
        findQuery: String,
        currentFindOccurrence: Int?,
        availableWidth: CGFloat,
        openURL: @escaping (URL) -> Void,
        activateBlock: @escaping (TranscriptBlock) -> Void
    ) {
        self.openURL = openURL
        view.isHidden = false
        let displayedBlock = Self.localizedBlock(block, language: language)
        let presentation = Self.presentation(
            for: block,
            language: language,
            accentColor: accentColor
        )
        let attributed = TranscriptTextRenderer.attributedText(
            for: displayedBlock,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        )
        let isUser = block.kind == .user
        let isWorkSummary = block.activityType?.hasPrefix("work_summary") == true
        let isAssistantOutput = block.kind == .output
        let showsIcon: Bool = switch block.kind {
        case .thought, .command, .warning, .error, .activity: true
        case .output: block.isAssistantProgress
        case .user: false
        }
        let previousSelection = representedBlockID == block.id
            ? textView.selectedRange()
            : NSRange(location: 0, length: 0)
        representedBlockID = block.id
        representedBlock = displayedBlock
        representedTextSize = textSize
        representedAccentColor = accentColor
        representedAppearance = appearance
        representedAttributedText = attributed
        let hasDetails = block.operation?.detailsAvailable == true
            || block.activityType?.hasPrefix("legacy_command") == true
        let actionable = !isWorkSummary && (hasDetails || block.activityType == "operation_more" || block.activityType == "operation_retry")
        bodyView.action = actionable ? { activateBlock(block) } : nil
        bodyView.setAccessibilityElement(actionable)
        bodyView.setAccessibilityRole(actionable ? .button : nil)
        bodyView.setAccessibilityLabel(actionable ? displayedBlock.text : nil)
        bodyView.setAccessibilityHidden(isWorkSummary)
        iconView.setAccessibilityHidden(isWorkSummary)
        workDisclosureButton.isHidden = !isWorkSummary
        workDisclosureButton.onPress = isWorkSummary ? { activateBlock(block) } : nil
        let workExpanded = block.activityType == "work_summary_expanded"
        workDisclosureButton.setAccessibilityLabel(displayedBlock.text)
        workDisclosureButton.setAccessibilityExpanded(workExpanded)
        workDisclosureButton.toolTip = workExpanded
            ? language.text("收起工作过程", "Collapse work process")
            : language.text("展开工作过程", "Expand work process")
        workDisclosureButton.setAccessibilityHelp(workDisclosureButton.toolTip)
        textView.isSelectable = !isWorkSummary
        textView.toolTip = block.operation?.command ?? (Self.isCompactWorkRow(block) ? displayedBlock.text : nil)
        let metrics = Self.metrics(
            for: displayedBlock,
            attributed: attributed,
            availableWidth: availableWidth,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        )

        let scale = textSize.transcriptScale
        iconView.symbolConfiguration = NSImage.SymbolConfiguration(
            pointSize: 15 * scale,
            weight: .regular
        )
        disclosureView.isHidden = !(isWorkSummary || hasDetails)
        disclosureView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 10 * scale, weight: .medium)
        disclosureView.image = NSImage(systemSymbolName:
            workExpanded || block.activityType == "work_operation_expanded"
                || block.activityType == "legacy_command_expanded" ? "chevron.down" : "chevron.right",
            accessibilityDescription: nil)
        titleField.font = .systemFont(
            ofSize: NSFont.smallSystemFontSize * scale,
            weight: .medium
        )
        statusField.font = .systemFont(ofSize: NSFont.smallSystemFontSize * scale)
        textView.linkTextAttributes = [
            .foregroundColor: block.operation != nil ? NSColor.secondaryLabelColor : accentColor.nsColor,
            .underlineStyle: NSUnderlineStyle.single.rawValue,
        ]

        // A reused item may change between user and Agent content. Deactivate
        // both anchors before selecting the new one so there is never a layout
        // pass with leading, trailing, and fixed-width constraints all active.
        bodyLeading.isActive = false
        bodyTrailing.isActive = false
        bodyWidth.isActive = true
        if isUser {
            bodyTrailing.constant = 0
            bodyTrailing.isActive = true
        } else {
            bodyLeading.isActive = true
        }

        iconView.isHidden = !showsIcon
        if isAssistantOutput, let brandImage = Self.brandImage {
            iconView.image = brandImage
            iconView.imageScaling = .scaleProportionallyUpOrDown
            iconView.contentTintColor = nil
        } else if !isUser {
            iconView.image = NSImage(
                systemSymbolName: presentation.icon,
                accessibilityDescription: presentation.title
            )
            iconView.imageScaling = .scaleProportionallyDown
            iconView.contentTintColor = presentation.color
        }

        titleField.isHidden = !presentation.showsTitle
        titleField.stringValue = presentation.title
        titleField.textColor = presentation.color

        let highlighted = Self.highlightedText(
            attributed,
            query: findQuery,
            currentOccurrence: currentFindOccurrence
        )
        currentFindRange = highlighted.currentRange
        let compact = Self.isCompactWorkRow(block)
        textView.textContainer?.maximumNumberOfLines = compact ? 1 : 0
        textView.textContainer?.lineBreakMode = compact ? .byTruncatingTail : .byWordWrapping
        let textChanged = textView.textStorage?.isEqual(to: highlighted.text) != true
        let canvas = ParallelCodexVisualStyle.resolvedNSColor(
            ParallelCodexVisualStyle.canvasNSColor, for: appearance
        )
        let surface = ParallelCodexVisualStyle.resolvedNSColor(presentation.background, for: appearance)
        let background = canvas.blended(withFraction: surface.alphaComponent,
            of: surface.withAlphaComponent(1)) ?? canvas
        if textView.backgroundColor != background {
            textView.backgroundColor = background
            textView.needsDisplay = true
        }
        if textChanged {
            if let storage = textView.textStorage {
                Self.updateText(storage, to: highlighted.text)
            }
            // Restyling and replacement can move already drawn lines, not
            // just append glyphs in TextKit's newly invalidated range.
            textView.needsDisplay = true
        }
        apply(metrics: metrics, availableWidth: availableWidth)
        let selectionLocation = min(previousSelection.location, attributed.length)
        let selectionLength = min(
            previousSelection.length,
            attributed.length - selectionLocation
        )
        textView.setSelectedRange(
            NSRange(location: selectionLocation, length: selectionLength)
        )

        statusField.isHidden = displayedBlock.status == nil
        statusField.stringValue = Self.localizedStatus(displayedBlock.status, language: language)
        statusField.textColor = Self.statusColor(displayedBlock.status)
        metaRow.isHidden = !presentation.showsTitle && displayedBlock.status == nil

        bodyView.layer?.cornerCurve = .continuous
        bodyView.layer?.cornerRadius = switch block.kind {
        case .user: ParallelCodexVisualStyle.Radius.message
        case .command, .warning, .error: ParallelCodexVisualStyle.Radius.tool
        case .activity where block.activityType != nil
            && block.activityType?.hasPrefix("work_summary") != true:
            ParallelCodexVisualStyle.Radius.tool
        default: 0
        }
        bodyView.layer?.backgroundColor = ParallelCodexVisualStyle.resolvedNSColor(
            presentation.background,
            for: appearance
        ).cgColor
        bodyView.layer?.borderWidth = 0
        bodyView.layer?.borderColor = if isUser {
            NSColor.clear.cgColor
        } else if isAssistantOutput {
            NSColor.clear.cgColor
        } else {
            NSColor.clear.cgColor
        }
    }

    static func updateText(_ storage: NSTextStorage, to text: NSAttributedString) {
        // Preserve laid-out glyphs while tokens arrive. Closing Markdown syntax
        // can restyle the prefix; in that case replace it without replacing the view.
        let length = storage.length
        storage.beginEditing()
        if text.length >= length,
           text.attributedSubstring(from: NSRange(location: 0, length: length)).isEqual(to: storage) {
            storage.append(text.attributedSubstring(from: NSRange(location: length, length: text.length - length)))
        } else {
            storage.setAttributedString(text)
        }
        storage.endEditing()
    }

    func revealCurrentFindMatch() {
        guard let currentFindRange else { return }
        textView.scrollRangeToVisible(currentFindRange)
    }

    private static func highlightedText(
        _ attributed: NSAttributedString,
        query: String,
        currentOccurrence: Int?
    ) -> (text: NSAttributedString, currentRange: NSRange?) {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, attributed.length > 0 else {
            return (attributed, nil)
        }

        let result = NSMutableAttributedString(attributedString: attributed)
        let source = result.string as NSString
        var searchRange = NSRange(location: 0, length: source.length)
        var occurrence = 0
        var currentRange: NSRange?

        while searchRange.length > 0 {
            let match = source.range(
                of: query,
                options: [.caseInsensitive, .diacriticInsensitive],
                range: searchRange
            )
            guard match.location != NSNotFound else { break }
            let isCurrent = occurrence == currentOccurrence
            let color = isCurrent
                ? ParallelCodexVisualStyle.appearancePreservingAlpha(
                    .selectedContentBackgroundColor,
                    alpha: 0.42
                )
                : ParallelCodexVisualStyle.appearancePreservingAlpha(
                    .systemYellow,
                    alpha: 0.34
                )
            result.addAttribute(.backgroundColor, value: color, range: match)
            if isCurrent { currentRange = match }
            occurrence += 1
            let nextLocation = NSMaxRange(match)
            searchRange = NSRange(
                location: nextLocation,
                length: source.length - nextLocation
            )
        }
        return (result, currentRange)
    }

    func updateAvailableWidth(_ availableWidth: CGFloat) {
        guard let block = representedBlock,
              let textSize = representedTextSize,
              let accentColor = representedAccentColor,
              let appearance = representedAppearance,
              let attributed = representedAttributedText else { return }
        apply(metrics: Self.metrics(
            for: block,
            attributed: attributed,
            availableWidth: availableWidth,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        ), availableWidth: availableWidth)
    }

    private func apply(metrics: TranscriptLayoutMetrics, availableWidth: CGFloat) {
        if representedBlock?.kind != .user {
            // Work text is indented after its icon; replies use the full
            // conversation column, rather than inheriting that indentation.
            let textLeading = (availableWidth - Self.readingColumnWidth(availableWidth)) / 2
            let replyOutset = representedBlock?.kind == .output
                && representedBlock?.isAssistantProgress != true ? Self.workTextInset : 0
            bodyLeading.constant = textLeading - replyOutset - metrics.cgPadding
            iconLeading.constant = textLeading - Self.workTextInset
        }
        bodyWidth.constant = metrics.cgBodyWidth
        let textContainerSize = NSSize(
            width: metrics.cgTextWidth,
            height: .greatestFiniteMagnitude
        )
        if textView.textContainer?.containerSize != textContainerSize {
            textView.textContainer?.containerSize = textContainerSize
            textView.needsDisplay = true
        }
        if let textContainer = textView.textContainer {
            textView.layoutManager?.ensureLayout(for: textContainer)
        }
        if let layout = textView.layoutManager, layout.numberOfGlyphs > 0 {
            let line = layout.lineFragmentRect(forGlyphAt: 0, effectiveRange: nil)
            iconTop.constant = textView.textContainerOrigin.y + line.midY - 12
            let usedWidth = layout.lineFragmentUsedRect(forGlyphAt: 0, effectiveRange: nil).width
            disclosureLeading.constant = min(usedWidth, metrics.cgTextWidth) + 7
        }
        textHeight.constant = metrics.cgTextHeight
        stackLeading.constant = metrics.cgPadding
        stackTrailing.constant = -metrics.cgPadding
        stackTop.constant = representedBlock?.activityType?.hasPrefix("work_summary") == true
            ? max(metrics.cgPadding, (WorkDisclosureButton.minimumHeight - metrics.cgTextHeight) / 2)
            : metrics.cgPadding
        stackBottom.constant = -metrics.cgPadding
        view.needsLayout = true
    }

    static func preferredHeight(
        for block: TranscriptBlock,
        language: AppLanguage = .english,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        appearance: TranscriptAppearance,
        availableWidth: CGFloat
    ) -> CGFloat {
        let displayedBlock = localizedBlock(block, language: language)
        let metrics = metrics(
            for: displayedBlock,
            attributed: nil,
            availableWidth: availableWidth,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        )
        var height = metrics.cgTextHeight + metrics.cgPadding * 2
        let presentation = presentation(for: displayedBlock, accentColor: accentColor)
        if presentation.showsTitle || displayedBlock.status != nil {
            height += 18 * textSize.transcriptScale + 6
        }
        let minimumHeight = block.activityType?.hasPrefix("work_summary") == true
            ? WorkDisclosureButton.minimumHeight : 22
        return max(minimumHeight, ceil(height))
    }

    private static func localizedBlock(
        _ block: TranscriptBlock,
        language: AppLanguage
    ) -> TranscriptBlock {
        var value = block
        if let operation = block.operation {
            value.text = WorkOperationPresentation.summary(operation, language: language)
            value.status = nil
        } else if block.activityType == "turn_interrupted" {
            value.text = language.text("回复已中断", "Response interrupted")
        } else if block.activityType == "missing_execution_path" {
            value.text = language.text(
                "命令未能启动，因为所需路径已不存在。",
                "The command could not start because a required path no longer exists."
            )
        } else if block.activityType == "native_image_loading" {
            value.text = language.text("正在读取图片", "Loading image")
        } else if block.activityType == "native_image" {
            if block.text == "Image preview unavailable" {
                value.text = language.text("图片暂时无法预览，原始记录仍保留。", "Image preview unavailable. The original record is preserved.")
            } else if block.text == "Image too large to preview" {
                value.text = language.text("图片过大，暂时无法预览。", "Image too large to preview.")
            }
        } else if block.kind == .error || block.kind == .warning {
            value.text = language.localizedClientMessage(block.text)
        }
        return value
    }

    static func layoutReferenceWidth(
        for block: TranscriptBlock,
        availableWidth: CGFloat
    ) -> CGFloat {
        maximumBodyWidth(for: block, availableWidth: availableWidth)
    }

    func readingAnchor(at localY: CGFloat) -> TranscriptScrollMemory.TextAnchor? {
        guard localY >= 0, let layout = textView.layoutManager,
              let container = textView.textContainer, layout.numberOfGlyphs > 0 else { return nil }
        let origin = textView.convert(textView.textContainerOrigin, to: view)
        let top = view.isFlipped ? origin.y : view.bounds.height - origin.y
        guard localY >= top else { return nil }
        let point = NSPoint(x: 0, y: localY - top)
        let glyph = layout.glyphIndex(for: point, in: container)
        guard glyph < layout.numberOfGlyphs else { return nil }
        let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
        return .init(character: layout.characterIndexForGlyph(at: glyph),
                     lineOffset: Double(localY - top - line.minY))
    }

    func readingOffset(for anchor: TranscriptScrollMemory.TextAnchor) -> CGFloat? {
        guard anchor.character >= 0, anchor.character < textView.string.utf16.count,
              let layout = textView.layoutManager, let container = textView.textContainer else { return nil }
        view.layoutSubtreeIfNeeded()
        layout.ensureLayout(for: container)
        let glyph = layout.glyphIndexForCharacter(at: anchor.character)
        guard glyph < layout.numberOfGlyphs else { return nil }
        let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
        let origin = textView.convert(textView.textContainerOrigin, to: view)
        let top = view.isFlipped ? origin.y : view.bounds.height - origin.y
        return top + line.minY + CGFloat(anchor.lineOffset)
    }

    func textView(
        _ textView: NSTextView,
        clickedOnLink link: Any,
        at charIndex: Int
    ) -> Bool {
        let url: URL?
        if let value = link as? URL {
            url = value
        } else if let value = link as? String {
            url = URL(string: value)
        } else {
            url = nil
        }
        guard let url else { return false }
        openURL?(url)
        return true
    }

    private struct Presentation {
        let title: String
        let icon: String
        let color: NSColor
        let background: NSColor
        let showsTitle: Bool
    }

    private final class MetricsBox: NSObject {
        let value: TranscriptLayoutMetrics

        init(_ value: TranscriptLayoutMetrics) {
            self.value = value
        }
    }

    private static let metricsCache: NSCache<NSString, MetricsBox> = {
        let cache = NSCache<NSString, MetricsBox>()
        cache.countLimit = 8_192
        return cache
    }()

    static let brandImage: NSImage? = {
        guard let url = Bundle.main.url(
            forResource: "parallel-codex-icon-1024",
            withExtension: "png"
        ) else { return nil }
        return NSImage(contentsOf: url)
    }()

    private static func metrics(
        for block: TranscriptBlock,
        attributed providedAttributed: NSAttributedString?,
        availableWidth: CGFloat,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        appearance: TranscriptAppearance
    ) -> TranscriptLayoutMetrics {
        let maximumBody = maximumBodyWidth(
            for: block,
            availableWidth: availableWidth
        )
        let stableKey = transcriptLayoutMetricKey(
            TranscriptLayoutMetricKeyInput(
                kind: block.kind.rawValue,
                text: block.text + (block.image.map { SHA256.hash(data: $0.data).description } ?? ""),
                statusPresent: block.status != nil,
                activityType: block.activityType,
                isHistorical: block.isHistorical,
                isFinalAnswer: block.isFinalAnswer,
                textSize: textSize.rawValue,
                maximumBodyWidth: maximumBody
            )
        )
        let cacheKey = NSString(string: stableKey)
        if let cached = metricsCache.object(forKey: cacheKey) { return cached.value }
        // Natural-width user bubbles are cheap to measure. AppKit's fallback
        // shaping can change between launches, making a persisted height clip.
        if block.kind != .user, let cached = TranscriptLayoutMetricsStore.shared.metrics(forKey: stableKey) {
            metricsCache.setObject(MetricsBox(cached), forKey: cacheKey)
            return cached
        }
        let attributed = providedAttributed ?? TranscriptTextRenderer.attributedText(
            for: block,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        )
        let isUser = block.kind == .user
        let isWorkSummary = block.activityType?.hasPrefix("work_summary") == true
        let padding = contentPadding(for: block)
        let textMaximum = max(32, maximumBody - padding * 2)
        let disclosureWidth: CGFloat = block.operation?.detailsAvailable == true || isWorkSummary
            || block.activityType?.hasPrefix("legacy_command") == true ? 24 : 0
        let bodyWidth: CGFloat
        if isUser {
            let naturalWidth = TranscriptTextRenderer.naturalTextWidth(attributed, maximum: textMaximum)
            bodyWidth = min(maximumBody, max(64, naturalWidth + padding * 2))
        } else {
            // Disclosure labels use the full reading width. Shrinking to a
            // boundingRect estimate can truncate CJK text at larger sizes.
            bodyWidth = maximumBody
        }
        let textWidth = max(32, bodyWidth - padding * 2 - disclosureWidth)
        let value = TranscriptLayoutMetrics(
            bodyWidth: bodyWidth,
            textWidth: textWidth,
            textHeight: TranscriptTextRenderer.textHeight(attributed, width: textWidth, maximumLines: isCompactWorkRow(block) ? 1 : 0),
            padding: padding
        )
        metricsCache.setObject(MetricsBox(value), forKey: cacheKey)
        if block.kind != .user { TranscriptLayoutMetricsStore.shared.store(value, forKey: stableKey) }
        return value
    }

    private static func maximumBodyWidth(
        for block: TranscriptBlock,
        availableWidth: CGFloat
    ) -> CGFloat {
        if block.kind == .user {
            return min(ParallelCodexVisualStyle.Layout.userMessageMaximumWidth,
                       max(180, availableWidth * 0.82))
        }
        let replyOutset = block.kind == .output && !block.isAssistantProgress ? workTextInset : 0
        return readingColumnWidth(availableWidth) + replyOutset + contentPadding(for: block) * 2
    }

    private static let workTextInset: CGFloat = 40

    private static func isCompactWorkRow(_ block: TranscriptBlock) -> Bool {
        block.operation != nil || block.activityType?.hasPrefix("work_summary") == true
            || block.activityType?.hasPrefix("legacy_command") == true
    }

    static func readingColumnWidth(_ availableWidth: CGFloat) -> CGFloat {
        min(ParallelCodexVisualStyle.Layout.transcriptMaximumWidth - 80,
            max(32, availableWidth - 80))
    }

    private static func contentPadding(for block: TranscriptBlock) -> CGFloat {
        switch block.kind {
        case .user, .warning, .error: 12
        case .command: 3
        case .activity where block.activityType?.hasPrefix("work_summary") == true: 4
        case .activity where block.activityType != nil: 3
        default: 0
        }
    }

    private static func presentation(
        for block: TranscriptBlock,
        language: AppLanguage = .english,
        accentColor: AppAccentColor
    ) -> Presentation {
        if let operation = block.operation {
            return .init(title: "", icon: WorkOperationPresentation.icon(operation),
                color: operation.status == .failed ? .systemRed : .secondaryLabelColor,
                background: .clear, showsTitle: false)
        }
        switch block.kind {
        case .user:
            return .init(
                title: "You",
                icon: "person.crop.circle.fill",
                color: accentColor.nsColor,
                background: ParallelCodexVisualStyle.userSurfaceNSColor,
                showsTitle: false
            )
        case .thought:
            return .init(
                title: language.text("思考", "Reasoning"),
                icon: "sparkles",
                color: .secondaryLabelColor,
                background: .clear,
                showsTitle: false
            )
        case .output:
            return .init(
                title: "Codex",
                icon: "text.bubble.fill",
                color: accentColor.nsColor,
                background: ParallelCodexVisualStyle.assistantSurfaceNSColor,
                showsTitle: false
            )
        case .command:
            return .init(
                title: language.text("命令", "Command"),
                icon: "terminal",
                color: .secondaryLabelColor,
                background: .clear,
                showsTitle: false
            )
        case .activity:
            if block.activityType == "turn_interrupted" {
                return .init(
                    title: "", icon: "stop.circle", color: .secondaryLabelColor,
                    background: .clear, showsTitle: false
                )
            }
            let value: (String, String) = switch block.activityType {
            case "web_search": (language.text("网页搜索", "Web Search"), "magnifyingglass")
            case "file_change": (language.text("文件改动", "File Changes"), "doc.badge.ellipsis")
            case "todo_list": (language.text("计划", "Plan"), "checklist")
            case "work_summary", "work_summary_expanded": (language.text("工作过程", "Work"), "wrench.and.screwdriver")
            default: (language.text("活动", "Activity"), "waveform.path")
            }
            return .init(
                title: value.0,
                icon: value.1,
                color: .secondaryLabelColor,
                background: .clear,
                showsTitle: false
            )
        case .warning:
            return .init(
                title: language.text("提醒", "Warning"),
                icon: "exclamationmark.triangle.fill",
                color: .systemOrange,
                background: ParallelCodexVisualStyle.appearancePreservingAlpha(
                    .systemOrange,
                    alpha: 0.07
                ),
                showsTitle: true
            )
        case .error:
            return .init(
                title: language.text("错误", "Error"),
                icon: "exclamationmark.triangle.fill",
                color: .systemRed,
                background: ParallelCodexVisualStyle.appearancePreservingAlpha(
                    .systemRed,
                    alpha: 0.065
                ),
                showsTitle: true
            )
        }
    }

    private static func statusColor(_ status: String?) -> NSColor {
        guard let status else { return .secondaryLabelColor }
        let normalized = status.lowercased()
        if normalized.hasPrefix("completed") { return .systemGreen }
        if normalized.hasPrefix("cancelled")
            || normalized.hasPrefix("needs confirmation") { return .systemOrange }
        if normalized.hasPrefix("failed") || normalized.hasPrefix("not sent") {
            return .systemRed
        }
        return .secondaryLabelColor
    }

    private static func localizedStatus(
        _ status: String?,
        language: AppLanguage
    ) -> String {
        guard let status else { return "" }
        switch status.lowercased() {
        case "checking delivery": return language.text("正在确认发送", "Checking delivery")
        case "message retained": return language.text("消息已保留", "Message retained")
        case "waiting for previous turn": return language.text("等待上一轮收尾", "Waiting for previous turn")
        case "awaiting receipt": return language.text("等待确认", status)
        case "show": return language.text("展开", status)
        case "hide": return language.text("收起", status)
        case "running": return language.text("运行中", status)
        case "completed": return language.text("已完成", status)
        case "failed": return language.text("失败", status)
        case "cancelled", "canceled": return language.text("已取消", status)
        case "sending": return language.text("发送中", status)
        case "retrying": return language.text("重试中", status)
        case "opening": return language.text("已发送", "Sent")
        case "sent": return language.text("已发送", "Sent")
        case "needs confirmation": return language.text("需要确认", status)
        case "not sent": return language.text("未发送", status)
        default: return status
        }
    }
}

/// One native button spans the chevron, label and gap. AppKit commits clicks
/// on release and cancels when dragged away, unlike an action in mouseDown.
private final class WorkDisclosureButton: NSButton {
    static let minimumHeight: CGFloat = 36
    var onPress: (() -> Void)?
    private var hoverTrackingArea: NSTrackingArea?
    private var hovered = false

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        title = ""
        isBordered = false
        setButtonType(.momentaryChange)
        target = self
        action = #selector(activate)
        focusRingType = .none
        setAccessibilityIdentifier("transcript.work-disclosure")
        setAccessibilityRole(.disclosureTriangle)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func activate() { onPress?() }

    func resetInteraction() {
        hovered = false
        highlight(false)
        needsDisplay = true
    }

    override func accessibilityPerformPress() -> Bool {
        guard isEnabled, !isHidden, onPress != nil else { return false }
        performClick(nil)
        return true
    }

    override var acceptsFirstResponder: Bool { !isHidden && isEnabled }
    override var isOpaque: Bool { false }

    override func keyDown(with event: NSEvent) {
        if [36, 76].contains(event.keyCode) {
            if !event.isARepeat { performClick(nil) }
        } else {
            super.keyDown(with: event)
        }
    }

    override func becomeFirstResponder() -> Bool {
        let accepted = super.becomeFirstResponder()
        needsDisplay = true
        return accepted
    }

    override func resignFirstResponder() -> Bool {
        let accepted = super.resignFirstResponder()
        needsDisplay = true
        return accepted
    }

    override func updateTrackingAreas() {
        if let hoverTrackingArea { removeTrackingArea(hoverTrackingArea) }
        let area = NSTrackingArea(rect: .zero,
            options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect], owner: self)
        addTrackingArea(area)
        hoverTrackingArea = area
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) { hovered = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hovered = false; needsDisplay = true }

    override func resetCursorRects() {
        super.resetCursorRects()
        if !isHidden && isEnabled { addCursorRect(bounds, cursor: .pointingHand) }
    }

    override func draw(_ dirtyRect: NSRect) {
        let outline = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 6, yRadius: 6)
        if hovered || isHighlighted {
            ParallelCodexVisualStyle.appearancePreservingAlpha(
                .labelColor, alpha: isHighlighted ? 0.10 : 0.045
            ).setFill()
            outline.fill()
        }
        if window?.firstResponder === self {
            NSColor.keyboardFocusIndicatorColor.setStroke()
            outline.lineWidth = 2
            outline.stroke()
        }
    }
}

private final class ActionableTranscriptBodyView: NSView {
    var action: (() -> Void)? {
        didSet { window?.invalidateCursorRects(for: self) }
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        let hit = super.hitTest(point)
        let localPoint = convert(point, from: superview)
        guard action != nil, hit != nil, bounds.contains(localPoint) else { return hit }
        if let text = hit as? NSTextView,
           let layout = text.layoutManager, let container = text.textContainer {
            // NSTextView.characterIndexForInsertion(at:) hit-tests the window
            // again. Query TextKit directly to avoid re-entering this method.
            let point = text.convert(localPoint, from: self)
            let containerPoint = NSPoint(x: point.x - text.textContainerOrigin.x,
                                         y: point.y - text.textContainerOrigin.y)
            layout.ensureLayout(for: container)
            let glyph = layout.glyphIndex(for: containerPoint, in: container)
            if glyph < layout.numberOfGlyphs,
               layout.boundingRect(forGlyphRange: NSRange(location: glyph, length: 1), in: container).contains(containerPoint) {
                let index = layout.characterIndexForGlyph(at: glyph)
                if index < text.string.utf16.count,
                   text.textStorage?.attribute(.link, at: index, effectiveRange: nil) != nil { return text }
            }
        }
        return self
    }

    override var acceptsFirstResponder: Bool { action != nil }

    override func keyDown(with event: NSEvent) {
        if action != nil && [36, 49, 76].contains(event.keyCode) { action?() }
        else { super.keyDown(with: event) }
    }

    override func accessibilityPerformPress() -> Bool {
        guard let action else { return false }
        action()
        return true
    }

    override func mouseDown(with event: NSEvent) {
        guard let action else {
            super.mouseDown(with: event)
            return
        }
        action()
    }

    override func resetCursorRects() {
        super.resetCursorRects()
        if action != nil { addCursorRect(bounds, cursor: .pointingHand) }
    }
}
