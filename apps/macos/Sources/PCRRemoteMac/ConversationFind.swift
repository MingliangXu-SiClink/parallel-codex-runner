import Foundation
import SwiftUI

struct ConversationFindMatch: Equatable, Sendable {
    let itemID: String
    let occurrence: Int
}

struct ConversationFindActions {
    let isEnabled: Bool
    let open: () -> Void
    let next: () -> Void
    let previous: () -> Void
}

struct ConversationSidebarSearchActions {
    let isEnabled: Bool
    let open: () -> Void
}

enum ConversationFindCommand: Equatable, Sendable {
    case open
    case next
    case previous
}

struct ConversationFindCommandRequest: Equatable, Sendable {
    let sequence: Int
    let command: ConversationFindCommand

    static let idle = ConversationFindCommandRequest(
        sequence: 0,
        command: .open
    )

    func advanced(to command: ConversationFindCommand) -> ConversationFindCommandRequest {
        ConversationFindCommandRequest(
            sequence: sequence + 1,
            command: command
        )
    }
}

private struct ConversationFindActionsKey: FocusedValueKey {
    typealias Value = ConversationFindActions
}

private struct ConversationSidebarSearchActionsKey: FocusedValueKey {
    typealias Value = ConversationSidebarSearchActions
}

extension FocusedValues {
    var conversationFindActions: ConversationFindActions? {
        get { self[ConversationFindActionsKey.self] }
        set { self[ConversationFindActionsKey.self] = newValue }
    }

    var conversationSidebarSearchActions: ConversationSidebarSearchActions? {
        get { self[ConversationSidebarSearchActionsKey.self] }
        set { self[ConversationSidebarSearchActionsKey.self] = newValue }
    }
}

struct ConversationFindCommands: Commands {
    @FocusedValue(\.conversationFindActions) private var actions
    @FocusedValue(\.conversationSidebarSearchActions) private var sidebarSearchActions
    let language: AppLanguage

    var body: some Commands {
        CommandGroup(after: .textEditing) {
            Divider()
            Button(language.text("搜索对话", "Search Conversations")) {
                sidebarSearchActions?.open()
            }
            .keyboardShortcut("k", modifiers: .command)
            .disabled(sidebarSearchActions?.isEnabled != true)

            Button(language.text("在当前对话中查找", "Find in Conversation")) {
                actions?.open()
            }
            .keyboardShortcut("f", modifiers: .command)
            .disabled(actions?.isEnabled != true)

            Button(language.text("查找下一个", "Find Next")) {
                actions?.next()
            }
            .keyboardShortcut("g", modifiers: .command)
            .disabled(actions?.isEnabled != true)

            Button(language.text("查找上一个", "Find Previous")) {
                actions?.previous()
            }
            .keyboardShortcut("g", modifiers: [.command, .shift])
            .disabled(actions?.isEnabled != true)
        }
    }
}

enum ConversationFindIndex {
    static func matches(
        in items: [ConversationTimelineItem],
        query: String
    ) -> [ConversationFindMatch] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return [] }

        return items.flatMap { item -> [ConversationFindMatch] in
            if case .workProcess(let process) = item {
                return matches(in: process.blocks.map(ConversationTimelineItem.message), query: query)
            }
            guard case .message(let block) = item else { return [] }
            let source = block.text as NSString
            var searchRange = NSRange(location: 0, length: source.length)
            var occurrence = 0
            var matches: [ConversationFindMatch] = []
            while searchRange.length > 0 {
                let match = source.range(
                    of: query,
                    options: [.caseInsensitive, .diacriticInsensitive],
                    range: searchRange
                )
                guard match.location != NSNotFound else { break }
                matches.append(ConversationFindMatch(
                    itemID: item.id,
                    occurrence: occurrence
                ))
                occurrence += 1
                let nextLocation = NSMaxRange(match)
                searchRange = NSRange(
                    location: nextLocation,
                    length: source.length - nextLocation
                )
            }
            return matches
        }
    }

    static func normalizedSelection(_ selection: Int, count: Int) -> Int {
        guard count > 0 else { return 0 }
        return ((selection % count) + count) % count
    }

    static func advancedSelection(
        _ selection: Int,
        count: Int,
        delta: Int
    ) -> Int {
        normalizedSelection(selection + delta, count: count)
    }
}
