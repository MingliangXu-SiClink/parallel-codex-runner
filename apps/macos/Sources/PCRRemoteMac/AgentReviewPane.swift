import MarkdownUI
import SwiftUI

enum AgentReviewMode: String, Hashable {
    case answer, changes
}

struct AgentComparisonRequest: Identifiable {
    let id = UUID()
    let context: ReviewContext
    let agents: [Int]
}

@MainActor
extension AppModel {
    func presentAgentComparison() {
        guard let run = activeRun, let workspaceID = selectedWorkspaceID,
              run.agents.count > 1 else { return }
        comparisonRequest = AgentComparisonRequest(
            context: ReviewContext(target: client.currentTarget(), workspaceID: workspaceID,
                                   runID: run.runId, agent: selectedAgent,
                                   conversationID: selectedConversationID),
            agents: run.agents.map(\.agent).sorted()
        )
    }
}

struct AgentReviewPane: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.appTextSize) private var textSize
    @StateObject private var loader = AgentReviewLoader()
    @State private var selectedFile: String?
    @State private var selectionContext: ReviewContext?
    @State private var retrySequence = 0
    @State private var consumedRetrySequence = 0
    @State private var showSource = false
    let context: ReviewContext
    let mode: AgentReviewMode
    var initialFile: String? = nil

    private struct LoadRequest: Hashable {
        let context: ReviewContext
        let mode: AgentReviewMode
        let file: String?
        let retry: Int
    }

    private var key: AgentReviewKey { AgentReviewKey(context: context, mode: mode) }
    private var requestedFile: String? { selectionContext == context ? selectedFile : initialFile }
    private var snapshot: AgentReviewSnapshot? { loader.loadedKey == key ? loader.snapshot : nil }
    private var currentFile: ReviewFile? {
        guard let snapshot else { return nil }
        if let path = requestedFile ?? snapshot.paths.first {
            return snapshot.completeFiles.first { $0.path == path }
        }
        return snapshot.completeFiles.first
    }

    private var shouldRefresh: Bool {
        model.activeRun?.runId == context.runID &&
            model.activeRun?.agents.first { $0.agent == context.agent }?.isTerminal == false
    }

    private var copyText: String { mode == .changes ? currentFile?.text ?? "" : snapshot?.text ?? "" }

    var body: some View {
        VStack(spacing: 0) {
            if mode == .changes, let snapshot, !snapshot.paths.isEmpty {
                Picker(model.appLanguage.text("文件", "File"), selection: Binding(
                    get: { requestedFile ?? snapshot.paths.first ?? "" },
                    set: { selectionContext = context; selectedFile = $0 }
                )) {
                    ForEach(snapshot.paths, id: \.self) { path in Text(path).tag(path) }
                }
                .pickerStyle(.menu).labelsHidden().padding(12)
                Divider()
            }
            if let file = currentFile, mode == .changes {
                if VisualPreviewKind.resolve(path: file.path) != nil && !showSource {
                    FileComparisonView(context: context, path: file.path, live: shouldRefresh)
                        .id("visual:\(context):\(file.path)")
                } else {
                ReviewTextView(text: file.text, isDiff: true, fontSize: 13 * textSize.scale,
                    filePath: file.path,
                    initialPosition: model.agentReviewCache.position(for: key, file: file.path),
                    onSavePosition: { model.agentReviewCache.remember($0, for: key, file: file.path) },
                    onSelection: { selection in
                        model.addReviewReference(context: context,
                            file: file.path + " [Diff]", selection: selection)
                        model.comparisonRequest = nil
                    })
                    .id("\(context):\(file.path)")
                }
            } else if let snapshot, !snapshot.text.isEmpty, mode == .answer {
                ScrollView {
                    Markdown(snapshot.text)
                        .markdownTheme(.gitHub)
                        .markdownTextStyle { FontSize(15 * textSize.scale) }
                        .textSelection(.enabled)
                        .padding(20)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        .environment(\.openURL, OpenURLAction { url in
                            model.openTranscriptLink(url, reviewContext: context)
                            model.comparisonRequest = nil
                            return .handled
                        })
                }
            } else {
                VStack(spacing: 12) {
                    if loader.loading { ProgressView().accessibilityLabel(model.appLanguage.text("正在打开文件", "Opening file")) }
                    else if loader.error == nil {
                        Image(systemName: mode == .changes ? "doc" : "text.bubble")
                        Text(mode == .changes ? model.appLanguage.text("没有文件更改", "No file changes")
                             : model.appLanguage.text("尚无最终回答", "No final answer yet"))
                    }
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if let error = loader.error {
                HStack {
                    Text(error).textSelection(.enabled).lineLimit(3)
                    Spacer()
                    Button(model.appLanguage.text("重试", "Retry")) { retrySequence &+= 1 }
                        .disabled(loader.loading)
                        .accessibilityIdentifier("review.diff-retry")
                }.padding(12)
            }
            HStack {
                if mode == .changes, let file = currentFile, VisualPreviewKind.resolve(path: file.path) != nil {
                    Picker(model.appLanguage.text("显示方式", "Display"), selection: $showSource) {
                        Image(systemName: "photo.on.rectangle").tag(false)
                        Image(systemName: "chevron.left.forwardslash.chevron.right").tag(true)
                    }.pickerStyle(.segmented).labelsHidden().frame(width: 78)
                        .help(model.appLanguage.text("预览或原始差异", "Preview or raw changes"))
                }
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(copyText, forType: .string)
                } label: { Image(systemName: "doc.on.doc") }
                    .help(mode == .changes ? model.appLanguage.text("复制文件差异", "Copy file changes")
                          : model.appLanguage.text("复制回答", "Copy answer"))
                    .disabled(copyText.isEmpty || (mode == .answer && snapshot?.hasMore != false))
            }.padding(12)
        }
        .task(id: LoadRequest(context: context, mode: mode, file: requestedFile, retry: retrySequence)) {
            let retry = retrySequence != consumedRetrySequence
            consumedRetrySequence = retrySequence
            await loader.load(client: model.client, context: context, mode: mode,
                language: model.appLanguage, cache: model.agentReviewCache, requestedFile: requestedFile,
                refresh: retry && snapshot?.isReady(mode: mode, file: requestedFile) == true)
            while !Task.isCancelled && shouldRefresh && loader.error == nil {
                do { try await Task.sleep(for: .seconds(8)) } catch { return }
                guard !Task.isCancelled else { return }
                await loader.load(client: model.client, context: context, mode: mode,
                    language: model.appLanguage, cache: model.agentReviewCache,
                    requestedFile: requestedFile, refresh: true)
            }
        }
    }
}

struct AgentComparisonView: View {
    @EnvironmentObject private var model: AppModel
    @State private var left: Int
    @State private var right: Int
    @State private var mode: AgentReviewMode = .answer
    let request: AgentComparisonRequest

    init(request: AgentComparisonRequest) {
        self.request = request
        let selected = request.context.agent ?? request.agents[0]
        _left = State(initialValue: selected)
        _right = State(initialValue: request.agents.first { $0 != selected } ?? selected)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(model.appLanguage.text("比较 Agent", "Compare Agents")).appScaledTextStyle(.headline)
                Spacer()
                Picker("", selection: $mode) {
                    Text(model.appLanguage.text("回答", "Answers")).tag(AgentReviewMode.answer)
                    Text(model.appLanguage.text("文件更改", "Changes")).tag(AgentReviewMode.changes)
                }.pickerStyle(.segmented).frame(width: 200)
                Button { model.comparisonRequest = nil } label: { Image(systemName: "xmark") }
                    .help(model.appLanguage.localized("Close"))
            }.padding(16)
            Divider()
            HStack(spacing: 0) {
                column($left, excluding: right)
                Divider()
                column($right, excluding: left)
            }
        }
    }

    private func column(_ agent: Binding<Int>, excluding: Int) -> some View {
        let context = ReviewContext(target: request.context.target, workspaceID: request.context.workspaceID,
                                runID: request.context.runID, agent: agent.wrappedValue,
                                conversationID: request.context.conversationID)
        return VStack(spacing: 0) {
            Picker("Agent", selection: agent) {
                ForEach(request.agents, id: \.self) { id in
                    Text("Agent \(id)").tag(id).disabled(id == excluding)
                }
            }.pickerStyle(.menu).padding(12)
            Divider()
            AgentReviewPane(context: context, mode: mode)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
