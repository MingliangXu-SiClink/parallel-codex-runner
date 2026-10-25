import SwiftUI

struct ConversationRunActions {
    let canSwitchAgent: Bool
    let canStop: Bool
    let selectPreviousAgent: () -> Void
    let selectNextAgent: () -> Void
    let stop: () -> Void
}

private struct ConversationRunActionsKey: FocusedValueKey {
    typealias Value = ConversationRunActions
}

extension FocusedValues {
    var conversationRunActions: ConversationRunActions? {
        get { self[ConversationRunActionsKey.self] }
        set { self[ConversationRunActionsKey.self] = newValue }
    }
}

struct ConversationRunCommands: Commands {
    @FocusedValue(\.conversationRunActions) private var actions
    @FocusedObject private var model: AppModel?
    let language: AppLanguage

    var body: some Commands {
        CommandMenu(language.text("对话", "Conversation")) {
            Button(language.text("重命名对话", "Rename Conversation")) {
                model?.requestConversationRename()
            }
            .keyboardShortcut("r", modifiers: [.command, .shift])
            .disabled(model?.selectedConversation == nil)
            Divider()
            Button(language.text("上一个 Agent", "Previous Agent")) {
                actions?.selectPreviousAgent()
            }
            .keyboardShortcut("[", modifiers: [.command, .shift])
            .disabled(actions?.canSwitchAgent != true)

            Button(language.text("下一个 Agent", "Next Agent")) {
                actions?.selectNextAgent()
            }
            .keyboardShortcut("]", modifiers: [.command, .shift])
            .disabled(actions?.canSwitchAgent != true)

            Divider()

            Button(language.text("停止当前回合", "Stop Current Turn")) {
                actions?.stop()
            }
            .keyboardShortcut(".", modifiers: .command)
            .disabled(actions?.canStop != true)
        }
    }
}
