import AppKit
import SwiftUI

@MainActor
struct AgentQuestionActions {
    var answerContext: AgentQuestionAnswerContext?
    var setAnswer: (AgentQuestionPresentation, String, String) -> Void = { _, _, _ in }
    var submit: (AgentQuestionPresentation) async throws -> Void = { _ in }
    var toggleDetails: (AgentQuestionPresentation) -> Void = { _ in }

    static func answering(using outbox: AgentAnswerOutbox, target: PcrdRequestTarget, workspaceID: String,
                          toggleDetails: @escaping (AgentQuestionPresentation) -> Void = { _ in }) -> Self {
        Self(answerContext: .init(outbox: outbox, target: target, workspaceID: workspaceID),
            setAnswer: { question, id, value in
                outbox.setDraft(request: question.request, target: target, workspaceID: workspaceID,
                    runID: question.runID, agent: question.agent, questionID: id, value: value)
            },
            submit: { question in
                try await outbox.commit(request: question.request, target: target, workspaceID: workspaceID,
                    runID: question.runID, agent: question.agent)
            }, toggleDetails: toggleDetails)
    }
}

@MainActor
struct AgentQuestionAnswerContext {
    let outbox: AgentAnswerOutbox
    let target: PcrdRequestTarget
    let workspaceID: String

    func current(_ snapshot: AgentQuestionPresentation) -> AgentQuestionPresentation {
        outbox.presentation(for: snapshot, target: target, workspaceID: workspaceID)
    }
}

struct AgentQuestionForm: View {
    let presentation: AgentQuestionPresentation
    let language: AppLanguage
    let actions: AgentQuestionActions

    var body: some View {
        if let context = actions.answerContext {
            ObservedAgentQuestionForm(presentation: presentation, language: language, actions: actions,
                outbox: context.outbox)
        } else {
            AgentQuestionFormContent(presentation: presentation, language: language, actions: actions)
        }
    }
}

private struct ObservedAgentQuestionForm: View {
    let presentation: AgentQuestionPresentation
    let language: AppLanguage
    let actions: AgentQuestionActions
    @ObservedObject var outbox: AgentAnswerOutbox

    var body: some View {
        AgentQuestionFormContent(presentation: actions.answerContext?.current(presentation) ?? presentation,
            language: language, actions: actions)
    }
}

private struct AgentQuestionFormContent: View {
    let presentation: AgentQuestionPresentation
    let language: AppLanguage
    let actions: AgentQuestionActions
    @State private var submitting = false
    @State private var failure: String?

    private var editable: Bool { presentation.editable && !submitting }
    private var current: AgentQuestionPresentation { actions.answerContext?.current(presentation) ?? presentation }
    // A timeline snapshot may be deferred by scrolling or a collection transaction.
    // Text bindings must read back the synchronous draft write, not that snapshot.
    private func value(_ question: AgentInputQuestion) -> String { current.values[question.id] ?? "" }
    private func custom(_ question: AgentInputQuestion) -> Bool {
        question.options?.isEmpty != false || current.values[question.id] != nil
            && !(question.options ?? []).contains { $0.label == value(question) }
    }
    private func title(_ question: AgentInputQuestion) -> String {
        let header = question.header.trimmingCharacters(in: .whitespacesAndNewlines)
        let generic = header.range(of: #"^Question\s+\d+$"#, options: .regularExpression) != nil
        return editable || presentation.expanded || header.isEmpty || generic ? question.question : header
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(language.text("Agent \(presentation.agent) · 提问", "Agent \(presentation.agent) · Question"))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 8)
                if !editable {
                    Button { actions.toggleDetails(presentation) } label: {
                        Image(systemName: presentation.expanded ? "chevron.up" : "chevron.down")
                            .appScaledSystemFont(size: 11, weight: .medium).frame(width: 24, height: 20)
                    }
                    .buttonStyle(.plain)
                    .help(language.text(presentation.expanded ? "收起问答详情" : "展开问答详情",
                        presentation.expanded ? "Collapse question details" : "Expand question details"))
                    .accessibilityIdentifier("agent-question.details.\(presentation.request.id)")
                }
            }
            ForEach(presentation.request.questions) { question in
                VStack(alignment: .leading, spacing: 10) {
                    Text(title(question)).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if editable || presentation.expanded {
                        ForEach(question.options ?? [], id: \.label) { option in
                            optionRow(label: option.label, detail: option.description,
                                selected: !custom(question) && value(question) == option.label,
                                choose: { actions.setAnswer(presentation, question.id, option.label) })
                        }
                    }
                    if editable {
                        if question.isOther == true, question.options?.isEmpty == false {
                            optionRow(label: language.text("其他", "Other"), detail: "",
                                selected: custom(question), choose: { actions.setAnswer(presentation, question.id, "") })
                        }
                        if custom(question) {
                            let binding = Binding(get: { value(question) },
                                set: { actions.setAnswer(presentation, question.id, $0) })
                            if question.isSecret == true {
                                SecureField(question.header, text: binding)
                            } else {
                                TextField(question.header, text: binding, axis: .vertical)
                                    .lineLimit(1...4)
                            }
                        }
                    } else if !value(question).isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(language.text("你的回答", "Your answer")).font(.caption).foregroundStyle(.secondary)
                            Text(question.isSecret == true ? "********" : value(question))
                                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if editable {
                Button {
                    submitting = true
                    failure = nil
                    let original = presentation
                    Task {
                        defer { submitting = false }
                        do { try await actions.submit(original) }
                        catch { failure = error.localizedDescription }
                    }
                } label: {
                    Label(language.text("提交回答", "Submit answer"), systemImage: "arrow.up")
                }
                .buttonStyle(AgentQuestionSubmitButtonStyle())
                .disabled(!current.canSubmit)
                .accessibilityIdentifier("agent-question.submit.\(presentation.request.id)")
            } else {
                Label(presentation.receiptLabel(language: language), systemImage: receiptIcon)
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("agent-question.receipt.\(presentation.request.id)")
            }
            if let error = failure ?? presentation.error {
                Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.leading, 24)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(language.text("Agent \(presentation.agent) 的问题", "Question from Agent \(presentation.agent)"))
    }

    private var receiptIcon: String {
        switch presentation.delivery {
        case .received, .sent: return "checkmark.circle"
        case .unsaved, .unconfirmed: return "exclamationmark.circle"
        case .closed: return "bubble.left"
        default: return "clock"
        }
    }

    private func optionRow(label: String, detail: String, selected: Bool,
                           choose: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 6) {
            AgentQuestionRadio(label: label, selected: selected, enabled: editable, select: choose)
            VStack(alignment: .leading, spacing: 3) {
                Text(label).fixedSize(horizontal: false, vertical: true)
                if !detail.isEmpty {
                    Text(detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle()).onTapGesture { if editable { choose() } }
        }
        .padding(.leading, -24)
    }
}

struct AgentQuestionSubmitButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    @Environment(\.isFocused) private var focused

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout)
            .foregroundStyle(enabled ? ParallelCodexVisualStyle.canvas : Color.secondary)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color.primary.opacity(enabled ? 0.92 : 0.10), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .strokeBorder(Color.accentColor.opacity(enabled && focused ? 0.65 : 0), lineWidth: 2))
            .opacity(configuration.isPressed && enabled ? 0.75 : 1)
    }
}

private struct AgentQuestionConfiguration: Equatable {
    let presentation: AgentQuestionPresentation
    let language: AppLanguage
    let textSize: AppTextSize
    let accentColor: AppAccentColor
    let appearance: TranscriptAppearance
    let width: CGFloat
}

private final class AgentQuestionHostingModel: ObservableObject {
    @Published var configuration: AgentQuestionConfiguration
    var actions: AgentQuestionActions
    var heightChanged: ((AgentQuestionPresentation, CGSize) -> Void)?
    init(configuration: AgentQuestionConfiguration, actions: AgentQuestionActions) {
        self.configuration = configuration
        self.actions = actions
    }
}

private struct HostedAgentQuestion: View {
    @ObservedObject var model: AgentQuestionHostingModel
    var body: some View {
        let value = model.configuration
        AgentQuestionForm(presentation: value.presentation, language: value.language, actions: model.actions)
            .id(value.presentation.id)
            .frame(width: value.width)
            .appScaledSystemFont(size: 14)
            .environment(\.locale, value.language.locale)
            .environment(\.appLanguage, value.language)
            .environment(\.appTextSize, value.textSize)
            .dynamicTypeSize(value.textSize.dynamicTypeSize)
            .preferredColorScheme(value.appearance == .dark ? .dark : .light)
            .tint(value.accentColor.color)
            .background(GeometryReader { geometry in
                Color.clear.onAppear { model.heightChanged?(value.presentation, geometry.size) }
                    .onChange(of: geometry.size) { model.heightChanged?(value.presentation, $0) }
            })
    }
}

final class AgentQuestionCollectionItem: NSCollectionViewItem {
    static let identifier = NSUserInterfaceItemIdentifier("AgentQuestionCollectionItem")
    private let icon = NSImageView()
    private var model: AgentQuestionHostingModel?
    private var host: AgentSwitchTrackingHostingView<HostedAgentQuestion>?
    private var bodyWidth: NSLayoutConstraint?

    override func loadView() {
        view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = true
        view.clipsToBounds = true
        view.wantsLayer = true
        icon.image = TranscriptCollectionItem.brandImage
            ?? NSImage(systemSymbolName: "text.bubble.fill", accessibilityDescription: nil)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.setAccessibilityHidden(true)
        view.addSubview(icon)
        NSLayoutConstraint.activate([
            icon.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            icon.topAnchor.constraint(equalTo: view.topAnchor),
            icon.widthAnchor.constraint(equalToConstant: 24), icon.heightAnchor.constraint(equalToConstant: 24)
        ])
    }

    func configure(question: AgentQuestionPresentation, language: AppLanguage, textSize: AppTextSize,
                   accentColor: AppAccentColor, appearance: TranscriptAppearance, availableWidth: CGFloat,
                   actions: AgentQuestionActions, interactionChanged: @escaping (Bool) -> Void = { _ in },
                   heightChanged: @escaping (CGFloat) -> Void) {
        _ = view
        view.isHidden = false
        let configuration = AgentQuestionConfiguration(presentation: question, language: language,
            textSize: textSize, accentColor: accentColor, appearance: appearance, width: Self.contentWidth(availableWidth) + 24)
        view.layer?.backgroundColor = ParallelCodexVisualStyle.resolvedNSColor(
            ParallelCodexVisualStyle.canvasNSColor, for: appearance).cgColor
        let report: (AgentQuestionPresentation, CGSize) -> Void = { source, size in
            guard source == question, abs(size.width - configuration.width) < 0.5 else { return }
            heightChanged(size.height)
        }
        if let model {
            model.actions = actions
            model.heightChanged = report
            if model.configuration != configuration { model.configuration = configuration }
        } else {
            let model = AgentQuestionHostingModel(configuration: configuration, actions: actions)
            model.heightChanged = report
            let host = AgentSwitchTrackingHostingView(rootView: HostedAgentQuestion(model: model))
            host.sizingOptions = [.intrinsicContentSize]
            host.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(host)
            let width = host.widthAnchor.constraint(equalToConstant: configuration.width)
            NSLayoutConstraint.activate([
                host.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
                host.topAnchor.constraint(equalTo: view.topAnchor), width
            ])
            self.model = model
            self.host = host
            bodyWidth = width
        }
        host?.interactionChanged = interactionChanged
        bodyWidth?.constant = configuration.width
        host?.appearance = NSAppearance(named: appearance == .dark ? .darkAqua : .aqua)
    }

    override func prepareForReuse() {
        super.prepareForReuse()
        view.isHidden = true
        model?.heightChanged = nil
        model?.actions = AgentQuestionActions()
        host?.interactionChanged = nil
    }

    static func contentWidth(_ availableWidth: CGFloat) -> CGFloat {
        TranscriptCollectionItem.readingColumnWidth(availableWidth)
    }

    static func preferredHeight(question: AgentQuestionPresentation, language: AppLanguage,
                                textSize: AppTextSize, accentColor: AppAccentColor,
                                appearance: TranscriptAppearance, availableWidth: CGFloat) -> CGFloat {
        let model = AgentQuestionHostingModel(configuration: .init(presentation: question, language: language,
            textSize: textSize, accentColor: accentColor, appearance: appearance,
            width: contentWidth(availableWidth) + 24), actions: .init())
        let host = NSHostingView(rootView: HostedAgentQuestion(model: model))
        host.translatesAutoresizingMaskIntoConstraints = false
        host.frame = NSRect(x: 0, y: 0, width: contentWidth(availableWidth) + 24, height: 1)
        host.widthAnchor.constraint(equalToConstant: contentWidth(availableWidth) + 24).isActive = true
        host.layoutSubtreeIfNeeded()
        return max(24, ceil(host.fittingSize.height))
    }
}

private struct AgentQuestionRadio: NSViewRepresentable {
    let label: String
    let selected: Bool
    let enabled: Bool
    let select: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(select) }
    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(radioButtonWithTitle: "", target: context.coordinator, action: #selector(Coordinator.choose))
        button.setAccessibilityLabel(label)
        return button
    }
    func updateNSView(_ button: NSButton, context: Context) {
        context.coordinator.select = select
        button.state = selected ? .on : .off
        button.isEnabled = enabled
        button.setAccessibilityLabel(label)
    }
    static func dismantleNSView(_ button: NSButton, coordinator: Coordinator) {
        button.target = nil
        button.action = nil
        coordinator.select = {}
    }
    final class Coordinator: NSObject {
        var select: () -> Void
        init(_ select: @escaping () -> Void) { self.select = select }
        @objc func choose() { select() }
    }
    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSButton, context: Context) -> CGSize? {
        CGSize(width: 18, height: 20)
    }
}
