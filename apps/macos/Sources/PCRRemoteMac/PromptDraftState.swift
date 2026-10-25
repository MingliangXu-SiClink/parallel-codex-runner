import Combine

// Keystrokes belong to the composer, not the conversation reader or sidebar.
@MainActor
final class PromptDraftState: ObservableObject {
    @Published private(set) var text = ""
    @Published private(set) var historyEntries: [String] = []
    @Published private(set) var historyIndex = 0

    func setText(_ value: String) {
        guard text != value else { return }
        text = value
    }

    func setHistoryEntries(_ value: [String]) {
        guard historyEntries != value else { return }
        historyEntries = value
    }

    func setHistoryIndex(_ value: Int) {
        guard historyIndex != value else { return }
        historyIndex = value
    }
}
