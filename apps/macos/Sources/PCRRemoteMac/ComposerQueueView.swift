import SwiftUI

enum QueuedFollowUpChange: Sendable {
    case edit(original: String, replacement: String)
    case remove
    case move(Int)
    case place(relativeTo: UUID, after: Bool)
    case retry

    func applying(to items: [QueuedFollowUp], id: UUID, lockedIDs: Set<UUID> = []) -> [QueuedFollowUp]? {
        guard let index = items.firstIndex(where: { $0.id == id }),
              items[index].dispatch == nil, !lockedIDs.contains(id) else { return nil }
        var updated = items
        switch self {
        case let .edit(original, replacement):
            let prompt = replacement.trimmingCharacters(in: .whitespacesAndNewlines)
            guard items[index].prompt == original, !prompt.isEmpty else { return nil }
            updated[index].prompt = prompt
        case .remove:
            updated.remove(at: index)
        case let .move(offset):
            guard offset == -1 || offset == 1 else { return nil }
            let destination = index + offset
            guard updated.indices.contains(destination),
                  updated[destination].dispatch == nil,
                  !lockedIDs.contains(updated[destination].id) else { return nil }
            updated.swapAt(index, destination)
        case let .place(anchor, after):
            guard anchor != id, let target = items.firstIndex(where: { $0.id == anchor }),
                  items[target].dispatch == nil, !lockedIDs.contains(anchor) else { return nil }
            let destination = target - (index < target ? 1 : 0) + (after ? 1 : 0)
            guard items[min(index, destination)...max(index, destination)].allSatisfy({
                $0.dispatch == nil && !lockedIDs.contains($0.id)
            }) else { return nil }
            updated.insert(updated.remove(at: index), at: destination)
        case .retry:
            updated[index].retryManually()
        }
        return updated
    }
}

struct ComposerQueueView: View {
    @Environment(\.appLanguage) private var language
    @Environment(\.appTextSize) private var textSize
    @State private var updating = false

    let items: [QueuedFollowUp]
    let status: String
    let dispatching: Bool
    var context: String = ""
    let change: (UUID, QueuedFollowUpChange) async -> Bool
    let edit: (QueuedFollowUp) -> Void
    let manageQueue: () -> Void

    static let maximumVisibleRows = 3
    static let rowHeight: CGFloat = 42

    static func pendingCount(items: [QueuedFollowUp], dispatching: Bool) -> Int {
        items.enumerated().filter { index, item in
            item.dispatch == nil && !(dispatching && index == 0)
        }.count
    }

    var body: some View {
        VStack(spacing: 0) {
            QueuedMessageList(items: items, dispatching: dispatching, context: context, updating: updating,
                change: apply, edit: edit, manageQueue: manageQueue)
            .frame(height: CGFloat(min(items.count, Self.maximumVisibleRows)) * Self.rowHeight * textSize.scale)

            HStack(spacing: 8) {
                Text(status)
                    .accessibilityIdentifier("queue.delivery-status")
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(status)
                Spacer(minLength: 4)
                Button(action: manageQueue) {
                    HStack(spacing: 5) {
                        QueueIcon().frame(width: 15, height: 15)
                        let pending = Self.pendingCount(items: items, dispatching: dispatching)
                        if pending > 0 {
                            Text(language.text("\(pending) 条待发送", "\(pending) queued"))
                                .accessibilityIdentifier("queue.pending-count")
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityLabel(language.text("管理排队消息", "Manage queued messages"))
                .disabled(updating)
                .help(language.text("管理排队消息", "Manage queued messages"))
            }
            .appScaledTextStyle(.caption)
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.top, 2)
            .padding(.bottom, 8)
        }
        .padding(.horizontal, 8)
        .padding(.top, 6)
        .padding(.bottom, 16)
        .background(
            ParallelCodexVisualStyle.surface,
            in: RoundedRectangle(cornerRadius: ParallelCodexVisualStyle.Radius.composer, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: ParallelCodexVisualStyle.Radius.composer, style: .continuous)
                .stroke(ParallelCodexVisualStyle.composerStroke, lineWidth: 1)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(language.text("排队消息", "Queued messages"))
    }

    private func apply(_ id: UUID, _ action: QueuedFollowUpChange) async -> Bool {
        guard !updating else { return false }
        updating = true
        defer { updating = false }
        return await change(id, action)
    }
}

struct QueuedMessageList: View {
    @Environment(\.appTextSize) private var textSize
    let items: [QueuedFollowUp]
    let dispatching: Bool
    let context: String
    let updating: Bool
    var expanded = false
    let change: (UUID, QueuedFollowUpChange) async -> Bool
    let edit: (QueuedFollowUp) -> Void
    var manageQueue: (() -> Void)? = nil

    var body: some View {
        InspectorScrollView {
            VStack(spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    ComposerQueueRow(item: item, position: index + 1, first: index == 0,
                        last: index == items.count - 1,
                        locked: updating || item.dispatch != nil || (dispatching && index == 0),
                        sending: item.dispatch != nil || (dispatching && index == 0),
                        canMoveEarlier: index > 0 && !(dispatching && index == 1),
                        context: context, expanded: expanded,
                        change: { await change(item.id, $0) }, drop: change,
                        edit: { edit(item) }, manageQueue: manageQueue)
                        .frame(height: (expanded ? 76 : ComposerQueueView.rowHeight) * textSize.scale)
                }
            }
        }
    }
}

struct ComposerQueueRow: View {
    @Environment(\.appLanguage) private var language
    @Environment(\.appTextSize) private var textSize
    @State private var hovered = false
    @State private var insertAfter: Bool?
    @FocusState private var focused: Bool

    let item: QueuedFollowUp
    let position: Int
    let first: Bool
    let last: Bool
    let locked: Bool
    let sending: Bool
    let canMoveEarlier: Bool
    let context: String
    let expanded: Bool
    let change: (QueuedFollowUpChange) async -> Bool
    let drop: (UUID, QueuedFollowUpChange) async -> Bool
    let edit: () -> Void
    let manageQueue: (() -> Void)?

    var body: some View {
        HStack(spacing: 8) {
            dragHandle

            Button(action: edit) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: item.prompt)
                        .lineLimit(expanded ? 2 : 1)
                        .truncationMode(.tail)
                    if let state = itemStatus {
                        Text(state).appScaledTextStyle(.caption)
                            .foregroundStyle(item.isPaused ? Color.orange : .secondary)
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(itemStatus ?? item.prompt)
            .disabled(locked)
            .focused($focused)
            .accessibilityLabel(language.text("编辑排队消息：\(item.prompt)", "Edit queued message: \(item.prompt)"))
            .accessibilityValue(item.needsDaemonAcknowledgement
                ? language.text("已保存在此设备", "Saved on this device")
                : language.text("服务器已接收", "Received by server"))

            Button(role: .destructive) {
                Task { _ = await change(.remove) }
            } label: {
                Image(systemName: "trash")
                    .foregroundStyle(.secondary)
                    .frame(width: 28 * textSize.scale, height: 28 * textSize.scale)
            }
            .help(language.text("从队列移除", "Remove from queue"))
            .accessibilityLabel(language.text("从队列移除", "Remove from queue"))
            .opacity(hovered || focused ? 1 : 0)
            .accessibilityHidden(!(hovered || focused))
            .disabled(locked || !(hovered || focused))

            Menu {
                Button(action: edit) {
                    Label(language.text("编辑消息", "Edit message"), systemImage: "pencil")
                }
                Divider()
                Button(role: .destructive) {
                    Task { _ = await change(.remove) }
                } label: {
                    Label(language.text("从队列移除", "Remove from queue"), systemImage: "trash")
                }
                Divider()
                Button {
                    Task { _ = await change(.move(-1)) }
                } label: {
                    Label(language.text("提前", "Move earlier"), systemImage: "arrow.up")
                }
                .disabled(first || !canMoveEarlier)
                Button {
                    Task { _ = await change(.move(1)) }
                } label: {
                    Label(language.text("延后", "Move later"), systemImage: "arrow.down")
                }
                .disabled(last)
                if item.isPaused || item.lastError?.isEmpty == false {
                    Button {
                        Task { _ = await change(.retry) }
                    } label: {
                        Label(language.text("重试", "Retry"), systemImage: "arrow.clockwise")
                    }
                }
                if let manageQueue {
                    Divider()
                    Button(action: manageQueue) {
                        Label(language.text("查看全部队列", "View full queue"), systemImage: "list.bullet")
                    }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .foregroundStyle(.secondary)
                    .frame(width: 28 * textSize.scale, height: 28 * textSize.scale)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .tint(.secondary)
            .fixedSize()
            .help(language.text("更多操作", "More actions"))
            .accessibilityLabel(language.text("更多队列操作", "More queue actions"))
            .disabled(locked)
        }
        .appScaledTextStyle(.callout)
        .buttonStyle(.plain)
        .padding(.horizontal, 10)
        .background(hovered ? ParallelCodexVisualStyle.sidebarHover : .clear,
                    in: RoundedRectangle(cornerRadius: ParallelCodexVisualStyle.Radius.selection))
        .onHover { hovered = $0 }
        .overlay(alignment: insertAfter == true ? .bottom : .top) {
            if insertAfter != nil && !locked {
                Rectangle().fill(Color.accentColor).frame(height: 2).allowsHitTesting(false)
            }
        }
        .onDrop(of: [QueuedMessageDrag.type], delegate: QueuedMessageDrop(context: context, target: item.id,
            height: (expanded ? 76 : ComposerQueueView.rowHeight) * textSize.scale, locked: locked,
            insertAfter: $insertAfter, commit: drop))
    }

    @ViewBuilder
    private var dragHandle: some View {
        if locked {
            if sending {
                ProgressView().controlSize(.small)
                    .frame(width: 22 * textSize.scale, height: 28 * textSize.scale)
                    .accessibilityLabel(language.text("正在续接原会话", "Continuing the original session"))
            } else {
                Image(systemName: "ellipsis")
                    .foregroundStyle(.secondary)
                    .frame(width: 22 * textSize.scale, height: 28 * textSize.scale)
                    .help(itemStatus ?? "")
            }
        } else {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.secondary.opacity(0.65))
                .frame(width: 22 * textSize.scale, height: 28 * textSize.scale)
                .contentShape(Rectangle())
                .onDrag { QueuedMessageDrag(context: context, id: item.id).provider } preview: {
                    Text(verbatim: item.prompt)
                        .appScaledTextStyle(.callout)
                        .lineLimit(2)
                        .padding(12)
                        .frame(width: 320, height: 64, alignment: .leading)
                        .background(ParallelCodexVisualStyle.surface, in: RoundedRectangle(cornerRadius: 6))
                }
                .help(language.text("拖动调整顺序", "Drag to reorder"))
                .accessibilityLabel(language.text("第 \(position) 条排队消息，拖动调整顺序", "Queued message \(position), drag to reorder"))
        }
    }

    private var itemStatus: String? {
        if sending { return nil }
        if let error = item.lastError, !error.isEmpty {
            return language.localizedClientMessage(error)
        }
        if item.isPaused { return language.text("需要处理", "Needs attention") }
        return nil
    }
}

struct QueuedFollowUpEditor: View {
    @Environment(\.appLanguage) private var language
    @State private var draft: String
    @State private var originalPrompt: String
    @State private var editorHeight: CGFloat = 96
    @State private var saving = false
    @State private var failed = false

    let save: (QueuedFollowUpChange) async -> Bool
    let close: () -> Void

    init(item: QueuedFollowUp, save: @escaping (QueuedFollowUpChange) async -> Bool, close: @escaping () -> Void) {
        self.save = save
        self.close = close
        _draft = State(initialValue: item.prompt)
        _originalPrompt = State(initialValue: item.prompt)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(language.text("编辑排队消息", "Edit queued message"))
                .appScaledTextStyle(.callout, weight: .semibold)
            NativeMultilineEditor(
                text: $draft, measuredHeight: $editorHeight,
                minimumHeight: 96, maximumHeight: 260, fontSize: 15,
                accessibilityLabel: language.text("排队消息内容", "Queued message text"),
                isInitialFirstResponder: true, onSubmit: nil
            )
            .frame(height: editorHeight)
            if failed {
                Text(language.text("未能保存，队列可能已更新。你的编辑仍保留在这里；可复制后关闭，或重试。", "Could not save; the queue may have changed. Your draft is kept here. Copy it before closing, or retry."))
                    .appScaledTextStyle(.caption)
                    .foregroundStyle(.red)
                Button(language.text("复制编辑内容", "Copy draft")) {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(draft, forType: .string)
                }
            }
            HStack {
                Spacer()
                Button(language.text("取消", "Cancel"), action: close)
                    .keyboardShortcut(.cancelAction)
                Button(language.text("保存", "Save")) {
                    Task {
                        saving = true
                        let saved = await save(.edit(original: originalPrompt, replacement: draft))
                        saving = false
                        failed = !saved
                        if saved { close() }
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .disabled(saving)
        .padding(16)
        .frame(width: 420)
    }
}
