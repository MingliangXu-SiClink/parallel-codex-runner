import AppKit

enum ConversationClipboard {
    static func copyConversation(_ blocks: [TranscriptBlock]) {
        copy(
            blocks.filter { block in
                switch block.kind {
                case .thought, .command, .activity:
                    return false
                case .output:
                    return block.isHistorical || block.isFinalAnswer
                default:
                    return true
                }
            }
        )
    }

    static func copyWork(_ blocks: [TranscriptBlock]) {
        copy(
            blocks.filter { block in
                guard !block.isHistorical else { return false }
                switch block.kind {
                case .thought, .command, .activity:
                    return true
                case .output:
                    return !block.isFinalAnswer
                default:
                    return false
                }
            }
        )
    }

    private static func copy(_ blocks: [TranscriptBlock]) {
        let text = blocks.map { block in
            let heading: String = switch block.kind {
            case .user: "You"
            case .thought: "Reasoning"
            case .output: block.isFinalAnswer || block.isHistorical ? "Codex" : "Update"
            case .command: "Command"
            case .activity: "Activity"
            case .warning: "Warning"
            case .error: "Error"
            }
            let status = block.status.map { "\n\($0)" } ?? ""
            return "\(heading)\n\(block.text)\(status)"
        }.joined(separator: "\n\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}
