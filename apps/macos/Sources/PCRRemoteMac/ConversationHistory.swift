import Foundation

enum AgentQuestionTranscript {
    static func historyQuestions(in entry: ResumeHistoryEntry) -> [AgentQuestionPresentation] {
        if let question = entry.question { return [question] }
        return historyRanges(in: entry, text: entry.text).map { $0.1 }
    }

    static func projectHistory(_ block: TranscriptBlock, entry: ResumeHistoryEntry) -> [TranscriptBlock] {
        guard block.kind == .output, block.question == nil else { return [block] }
        let ranges = historyRanges(in: entry, text: block.text)
        guard !ranges.isEmpty else { return [block] }
        var result: [TranscriptBlock] = []
        var cursor = block.text.startIndex
        func appendProse(until end: String.Index) {
            let text = block.text[cursor..<end].trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty {
                result.append(TranscriptBlock(id: "\(block.id):part-\(result.count)", kind: block.kind,
                    text: text, isHistorical: true, isFinalAnswer: block.isFinalAnswer, linkOrigin: block.linkOrigin))
            }
        }
        for (range, question) in ranges {
            appendProse(until: range.lowerBound)
            var value = question
            value.editable = false
            var rendered = message(value)
            rendered.isHistorical = true
            result.append(rendered)
            cursor = range.upperBound
        }
        appendProse(until: block.text.endIndex)
        return result
    }

    private static func nativeText(_ question: AgentQuestionPresentation) -> String {
        question.request.questions.flatMap { item in
            [item.question] + (item.options ?? []).map { "- " + $0.label }
        }.joined(separator: "\n")
    }

    private static func historyRanges(in entry: ResumeHistoryEntry, text: String)
        -> [(Range<String.Index>, AgentQuestionPresentation)] {
        guard entry.category.lowercased().contains("output"), !entry.turnId.isEmpty else { return [] }
        let unique = Dictionary((entry.questions ?? []).map { ($0.id, $0) }, uniquingKeysWith: { _, next in next })
        let candidates = unique.values.compactMap { question -> (Range<String.Index>, AgentQuestionPresentation)? in
            guard question.request.turnId == entry.turnId else { return nil }
            let expected = nativeText(question)
            guard !expected.isEmpty, let range = text.range(of: expected),
                  text.range(of: expected, range: range.upperBound..<text.endIndex) == nil,
                  range.lowerBound == text.startIndex || text[text.index(before: range.lowerBound)] == "\n",
                  range.upperBound == text.endIndex || text[range.upperBound] == "\n" else { return nil }
            return (range, question)
        }.sorted { $0.0.lowerBound < $1.0.lowerBound }
        // The server coalesces prose within a native turn. Keep its original
        // text intact unless one known request has an unambiguous exact span.
        return candidates.filter { candidate in
            !candidates.contains { $0.1.id != candidate.1.id && $0.0.overlaps(candidate.0) }
        }
    }

    static func project(_ blocks: [TranscriptBlock], questions: [AgentQuestionPresentation]) -> [TranscriptBlock] {
        guard !questions.isEmpty else { return blocks }
        let unique = Dictionary(questions.map { ($0.id, $0) }, uniquingKeysWith: { _, next in next })
        let ordered = unique.values.sorted {
            $0.request.createdAt == $1.request.createdAt ? $0.id < $1.id : $0.request.createdAt < $1.request.createdAt
        }
        var remaining = unique
        var seen = Set<String>()
        var result: [TranscriptBlock] = []
        for block in blocks {
            let matched = ordered.first { question in
                block.question?.id == question.id
                    || block.linkOrigin == TranscriptLinkOrigin(runID: question.runID, agent: question.agent)
                        && (block.id.hasPrefix("text-") && block.id.hasSuffix(":" + question.request.itemId)
                            || block.activityType == "agent_question_source" && block.text == question.request.id)
            }
            guard let question = matched else { result.append(block); continue }
            remaining.removeValue(forKey: question.id)
            guard seen.insert(question.id).inserted else { continue }
            // Only an identity-matched native question can replace its plain
            // compatibility rendering. Preserve any unrelated accompanying prose.
            let expected = nativeText(question)
            let lines: (String) -> [String] = { text in
                text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            }
            if block.question == nil, block.activityType != "agent_question_source", lines(block.text) != lines(expected) {
                result.append(block)
            }
            result.append(message(question))
        }
        // A request can arrive before its native text page. Keep it accessible
        // inside this turn, never in a separate viewport or ahead of old history.
        var anchored: [TranscriptBlock] = []
        for block in result {
            anchored.append(block)
            for question in ordered where remaining[question.id] != nil && question.anchorAfterID == block.id {
                anchored.append(message(question))
                remaining.removeValue(forKey: question.id)
            }
        }
        anchored += ordered.filter { remaining[$0.id] != nil }.map(message)
        return anchored
    }

    private static func message(_ question: AgentQuestionPresentation) -> TranscriptBlock {
        TranscriptBlock(id: question.id, kind: .output, text: question.searchableText,
            activityType: "agent_question", linkOrigin: .init(runID: question.runID, agent: question.agent),
            question: question)
    }
}

func userTextBeforeCodexInterruption(_ text: String) -> String? {
    let marker = "<turn_aborted>"
    let closing = "</turn_aborted>"
    guard text.hasSuffix(closing),
          let range = text.range(of: marker, options: .backwards),
          range.lowerBound == text.startIndex || text[text.index(before: range.lowerBound)] == "\n"
    else { return nil }
    let prefix = String(text[..<range.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    // Preserve quoted code and unknown payloads; only the known runtime notice
    // becomes a status row. The stored rollout and message IDs stay untouched.
    guard !prefix.contains("```"), !prefix.contains("~~~") else { return nil }
    let body = text[range.upperBound...].dropLast(closing.count)
        .split(whereSeparator: \.isWhitespace).joined(separator: " ")
    guard body == "The user interrupted the previous turn on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed."
    else { return nil }
    return prefix
}

func conversationHistoryEntries(
    from blocks: [TranscriptBlock]
) -> [ResumeHistoryEntry] {
    blocks.compactMap { block in
        let belongsToConversation = block.kind == .user
            || block.kind == .output
        guard belongsToConversation else { return nil }
        var question = block.question
        question?.editable = false
        return ResumeHistoryEntry(
            category: block.kind.rawValue,
            text: block.text,
            id: block.isHistorical || block.question != nil ? block.id : "",
            turnId: question?.request.turnId ?? "",
            linkOrigin: block.linkOrigin,
            question: question
        )
    }
}

func branchConversationHistory(
    history: [ResumeHistoryEntry],
    prompt: String,
    transcript: AgentTranscript
) -> [ResumeHistoryEntry] {
    var blocks = TranscriptFormatter.blocks(history: history, prompt: prompt, transcript: transcript)
    if let runID = transcript.sourceRunID, let agent = transcript.sourceAgent {
        let questions = (transcript.inputRequests ?? []).map { request in
            var value = AgentQuestionPresentation(runID: runID, agent: agent, request: request, command: nil)
            value.anchorAfterID = transcript.questionAnchors?[request.id]
            return value
        }
        blocks = AgentQuestionTranscript.project(blocks, questions: questions)
    }
    return conversationHistoryEntries(from: blocks)
}

func mergeConversationHistory(
    current: [ResumeHistoryEntry],
    authoritative: [ResumeHistoryEntry]
) -> [ResumeHistoryEntry] {
    if authoritative.isEmpty { return current }
    if current.isEmpty { return authoritative }

    let optimisticConversationCount = current.count { entry in
        let category = entry.category.lowercased()
        return category.contains("user") || category.contains("output")
    }
    // The paged server history carries stable rollout identities. Never align
    // it by display text because separate turns can legitimately repeat the
    // same prompt or answer. Keep a longer optimistic projection only until the
    // server has flushed it, then replace it atomically and discard attempt-only
    // work from permanent history.
    guard authoritative.count >= optimisticConversationCount else { return current }
    let questions = current.flatMap { AgentQuestionTranscript.historyQuestions(in: $0) }
    return authoritative.map { entry in
        var value = entry
        let candidates = questions.filter { !$0.request.turnId.isEmpty && $0.request.turnId == entry.turnId }
        value.questions = (entry.questions ?? []) + candidates
        value.questions = AgentQuestionTranscript.historyQuestions(in: value)
        if value.questions?.isEmpty == true { value.questions = nil }
        return value
    }
}
