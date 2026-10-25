import Foundation

struct LatexMarkdownFragment: Equatable, Sendable {
    let token: String
    let source: String
    let original: String
    let isDisplay: Bool
}

struct LatexMarkdownExtraction: Equatable, Sendable {
    let markdown: String
    let fragments: [LatexMarkdownFragment]
}

struct LatexNativeTextFormula: Equatable, Sendable {
    let text: String
    let boxed: Bool
}

enum LatexNativeTextFormulaParser {
    static func parse(_ source: String) -> LatexNativeTextFormula? {
        let wrapper = unwrapBox(source)
        let value = wrapper.source
        var output = ""
        var sawText = false
        var index = value.startIndex

        while index < value.endIndex {
            if value[index] != "\\" {
                let character = value[index]
                guard character != "{" && character != "}" else { return nil }
                output.append(character)
                index = value.index(after: index)
                continue
            }

            let commandStart = value.index(after: index)
            var commandEnd = commandStart
            while commandEnd < value.endIndex, value[commandEnd].isLetter {
                commandEnd = value.index(after: commandEnd)
            }
            let command = String(value[commandStart..<commandEnd])
            if command == "text" {
                guard commandEnd < value.endIndex,
                      value[commandEnd] == "{",
                      let group = bracedContent(in: value, openingAt: commandEnd) else {
                    return nil
                }
                output += group.content
                index = group.nextIndex
                sawText = true
                continue
            }
            guard let replacement = commandReplacements[command] else { return nil }
            output += replacement
            index = commandEnd
        }

        guard sawText else { return nil }
        return LatexNativeTextFormula(
            text: readableOperators(in: output),
            boxed: wrapper.boxed
        )
    }

    static func unwrapBox(_ source: String) -> (source: String, boxed: Bool) {
        let value = source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.hasPrefix("\\boxed{") else { return (value, false) }
        let opening = value.index(value.startIndex, offsetBy: 6)
        guard let group = bracedContent(in: value, openingAt: opening),
              group.nextIndex == value.endIndex else { return (value, false) }
        return (group.content, true)
    }

    private static func bracedContent(
        in value: String,
        openingAt opening: String.Index
    ) -> (content: String, nextIndex: String.Index)? {
        guard opening < value.endIndex, value[opening] == "{" else { return nil }
        var depth = 1
        var index = value.index(after: opening)
        let contentStart = index
        while index < value.endIndex {
            switch value[index] {
            case "{": depth += 1
            case "}":
                depth -= 1
                if depth == 0 {
                    return (
                        String(value[contentStart..<index]),
                        value.index(after: index)
                    )
                }
            default: break
            }
            index = value.index(after: index)
        }
        return nil
    }

    private static func readableOperators(in source: String) -> String {
        var value = source
        for symbol in ["=", "+", "×", "±", "→", "≤", "≥", "≠"] {
            value = value.replacingOccurrences(of: symbol, with: " \(symbol) ")
        }
        return value
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private static let commandReplacements: [String: String] = [
        "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
        "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π",
        "sigma": "σ", "phi": "φ", "omega": "ω", "times": "×",
        "cdot": "·", "pm": "±", "infty": "∞", "le": "≤",
        "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠",
        "to": "→", "rightarrow": "→", "left": "", "right": "",
    ]
}

enum LatexMarkdownExtractor {
    static func extract(_ source: String) -> LatexMarkdownExtraction {
        var outputLines: [String] = []
        var fragments: [LatexMarkdownFragment] = []
        var fencedDelimiter: Character?
        var displayBlock: DisplayBlock?

        for line in source.split(separator: "\n", omittingEmptySubsequences: false) {
            let value = String(line)
            let trimmed = value.drop(while: { $0 == " " || $0 == "\t" })
            if var block = displayBlock {
                block.original.append(value)
                if isDisplayMarker(value, marker: block.close) {
                    let formula = block.content
                        .joined(separator: "\n")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !formula.isEmpty {
                        let token = "PCRLATEXFORMULA\(fragments.count)TOKEN"
                        fragments.append(
                            LatexMarkdownFragment(
                                token: token,
                                source: formula,
                                original: block.original.joined(separator: "\n"),
                                isDisplay: true
                            )
                        )
                        outputLines.append(token)
                    } else {
                        outputLines.append(contentsOf: block.original)
                    }
                    displayBlock = nil
                } else {
                    block.content.append(value)
                    displayBlock = block
                }
                continue
            }
            if let marker = fenceMarker(trimmed) {
                if fencedDelimiter == nil {
                    fencedDelimiter = marker
                } else if fencedDelimiter == marker {
                    fencedDelimiter = nil
                }
                outputLines.append(value)
            } else if fencedDelimiter != nil {
                outputLines.append(value)
            } else if let close = openingDisplayMarker(value) {
                displayBlock = DisplayBlock(close: close, original: [value], content: [])
            } else {
                outputLines.append(
                    extractLine(
                        value,
                        fragments: &fragments
                    )
                )
            }
        }
        if let displayBlock {
            outputLines.append(contentsOf: displayBlock.original)
        }
        var output = outputLines.joined(separator: "\n")
        if source.last == "\n" { output += "\n" }
        return LatexMarkdownExtraction(markdown: output, fragments: fragments)
    }

    private struct DisplayBlock {
        let close: String
        var original: [String]
        var content: [String]
    }

    private static func openingDisplayMarker(_ line: String) -> String? {
        let value = displayMarkerText(line)
        if value == "\\[" { return "\\]" }
        if value == "$$" { return "$$" }
        return nil
    }

    private static func isDisplayMarker(_ line: String, marker: String) -> Bool {
        displayMarkerText(line) == marker
    }

    private static func displayMarkerText(_ line: String) -> String {
        var value = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("**") { value.removeFirst(2) }
        if value.hasSuffix("**") { value.removeLast(2) }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func fenceMarker(_ value: Substring) -> Character? {
        guard let first = value.first, first == "`" || first == "~" else { return nil }
        let prefix = value.prefix(3)
        return prefix.count == 3 && prefix.allSatisfy { $0 == first } ? first : nil
    }

    private static func extractLine(
        _ line: String,
        fragments: inout [LatexMarkdownFragment]
    ) -> String {
        if let formula = bracketedDisplayFormula(line) {
            let token = "PCRLATEXFORMULA\(fragments.count)TOKEN"
            fragments.append(
                LatexMarkdownFragment(
                    token: token,
                    source: formula,
                    original: line,
                    isDisplay: true
                )
            )
            return "\n\n\(token)\n\n"
        }
        let characters = Array(line)
        var output = ""
        var index = 0
        var inlineCodeDelimiter = 0

        while index < characters.count {
            if characters[index] == "`" {
                let count = repeatedCount(of: "`", at: index, in: characters)
                if inlineCodeDelimiter == 0 {
                    inlineCodeDelimiter = count
                } else if count == inlineCodeDelimiter {
                    inlineCodeDelimiter = 0
                }
                output += String(characters[index..<min(characters.count, index + count)])
                index += count
                continue
            }
            guard inlineCodeDelimiter == 0,
                  let delimiter = delimiter(at: index, in: characters),
                  let end = closingDelimiter(
                    delimiter,
                    after: index + delimiter.open.count,
                    in: characters
                  ) else {
                output.append(characters[index])
                index += 1
                continue
            }

            let contentStart = index + delimiter.open.count
            let content = String(characters[contentStart..<end])
            guard !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                output.append(characters[index])
                index += 1
                continue
            }
            if delimiter.open == ["$"],
               (characters[contentStart].isWhitespace || characters[end - 1].isWhitespace) {
                output.append(characters[index])
                index += 1
                continue
            }

            let originalEnd = end + delimiter.close.count
            let original = String(characters[index..<originalEnd])
            let token = "PCRLATEXFORMULA\(fragments.count)TOKEN"
            fragments.append(
                LatexMarkdownFragment(
                    token: token,
                    source: content,
                    original: original,
                    isDisplay: delimiter.display
                )
            )
            output += delimiter.display ? "\n\n\(token)\n\n" : token
            index = originalEnd
        }
        return output
    }

    private static func bracketedDisplayFormula(_ line: String) -> String? {
        var value = line.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.hasPrefix("**"), value.hasSuffix("**"), value.count > 4 {
            value = String(value.dropFirst(2).dropLast(2))
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard value.hasPrefix("["), value.hasSuffix("]"), value.count > 2 else {
            return nil
        }
        let formula = String(value.dropFirst().dropLast())
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !formula.isEmpty,
              formula.contains("\\")
                || formula.contains("^")
                || formula.contains("_") else {
            return nil
        }
        return formula
    }

    private struct Delimiter {
        let open: [Character]
        let close: [Character]
        let display: Bool
    }

    private static func delimiter(
        at index: Int,
        in characters: [Character]
    ) -> Delimiter? {
        guard !isEscaped(index, in: characters) else { return nil }
        if matches(["$", "$"], at: index, in: characters) {
            return Delimiter(open: ["$", "$"], close: ["$", "$"], display: true)
        }
        if matches(["\\", "["], at: index, in: characters) {
            return Delimiter(open: ["\\", "["], close: ["\\", "]"], display: true)
        }
        if matches(["\\", "("], at: index, in: characters) {
            return Delimiter(open: ["\\", "("], close: ["\\", ")"], display: false)
        }
        if characters[index] == "$" {
            return Delimiter(open: ["$"], close: ["$"], display: false)
        }
        return nil
    }

    private static func closingDelimiter(
        _ delimiter: Delimiter,
        after start: Int,
        in characters: [Character]
    ) -> Int? {
        var index = start
        while index + delimiter.close.count <= characters.count {
            if matches(delimiter.close, at: index, in: characters),
               !isEscaped(index, in: characters) {
                return index
            }
            index += 1
        }
        return nil
    }

    private static func matches(
        _ value: [Character],
        at index: Int,
        in characters: [Character]
    ) -> Bool {
        guard index + value.count <= characters.count else { return false }
        return Array(characters[index..<(index + value.count)]) == value
    }

    private static func repeatedCount(
        of value: Character,
        at index: Int,
        in characters: [Character]
    ) -> Int {
        var end = index
        while end < characters.count, characters[end] == value { end += 1 }
        return end - index
    }

    private static func isEscaped(_ index: Int, in characters: [Character]) -> Bool {
        var cursor = index
        var slashCount = 0
        while cursor > 0, characters[cursor - 1] == "\\" {
            slashCount += 1
            cursor -= 1
        }
        return slashCount % 2 == 1
    }
}
