import AppKit
import Highlighter

struct ReviewSyntaxRequest: Equatable, Sendable {
    let text: String
    let path: String
    let isDiff: Bool
    let fontSize: CGFloat
    let dark: Bool

    static func font(ofSize size: CGFloat) -> NSFont {
        NSFont(name: "Menlo-Regular", size: size) ?? .monospacedSystemFont(ofSize: size, weight: .regular)
    }

    var language: String {
        let name = (path as NSString).lastPathComponent.lowercased()
        if name == "dockerfile" { return "dockerfile" }
        if name == "makefile" { return "makefile" }
        return ["swift": "swift", "py": "python", "kt": "kotlin", "kts": "kotlin",
                "js": "javascript", "jsx": "javascript", "mjs": "javascript",
                "ts": "typescript", "tsx": "typescript", "json": "json",
                "html": "xml", "xml": "xml", "svg": "xml", "plist": "xml",
                "css": "css", "scss": "scss", "sh": "bash", "zsh": "bash",
                "bash": "bash", "yaml": "yaml", "yml": "yaml", "toml": "ini",
                "ini": "ini", "md": "markdown", "markdown": "markdown",
                "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp", "m": "objectivec",
                "mm": "objectivec", "rs": "rust", "go": "go", "java": "java",
                "rb": "ruby", "sql": "sql", "r": "r", "tex": "latex"][(path as NSString).pathExtension.lowercased()]
            ?? "plaintext"
    }
}

// Immutable Foundation text is transferred from the serial syntax worker to AppKit.
struct ReviewStyledText: @unchecked Sendable {
    let value: NSAttributedString
}

actor ReviewSyntaxRenderer {
    static let shared = ReviewSyntaxRenderer()
    private var highlighter: Highlighter?

    func render(_ request: ReviewSyntaxRequest) -> ReviewStyledText {
        if highlighter == nil { highlighter = Highlighter() }
        _ = highlighter?.setTheme(request.dark ? "github-dark" : "github",
                                  withFont: "Menlo-Regular", ofSize: request.fontSize)
        let foreground = request.dark ? NSColor(white: 0.88, alpha: 1) : NSColor(white: 0.12, alpha: 1)
        let base: [NSAttributedString.Key: Any] = [
            .font: ReviewSyntaxRequest.font(ofSize: request.fontSize),
            .foregroundColor: foreground
        ]
        func highlight(_ source: String) -> NSAttributedString {
            guard request.language != "plaintext",
                  let value = highlighter?.highlight(source, as: request.language),
                  value.string == source else { return NSAttributedString(string: source, attributes: base) }
            return value
        }
        guard request.isDiff else { return ReviewStyledText(value: highlight(request.text)) }

        let lines = ReviewDiffSyntax.lines(request.text)
        let before = lines.filter { $0.kind == " " || $0.kind == "-" }.map(\.source).joined()
        let after = lines.filter { $0.kind == " " || $0.kind == "+" }.map(\.source).joined()
        let oldStyle = highlight(before), newStyle = highlight(after)
        let result = NSMutableAttributedString(string: request.text, attributes: base)
        var oldOffset = 0, newOffset = 0
        for line in lines {
            let length = (line.source as NSString).length
            let offset = line.kind == "-" ? oldOffset : newOffset
            let styled = line.kind == "-" ? oldStyle : newStyle
            if line.kind != nil, offset + length <= styled.length {
                styled.enumerateAttributes(in: NSRange(location: offset, length: length)) { attributes, range, _ in
                    result.addAttributes(attributes, range: NSRange(
                        location: line.range.location + 1 + range.location - offset, length: range.length))
                }
                if line.kind != "+" { oldOffset += length }
                if line.kind != "-" { newOffset += length }
                if line.kind == "+" || line.kind == "-" {
                    let color: NSColor = line.kind == "+" ? .systemGreen : .systemRed
                    result.addAttribute(.backgroundColor, value: color.withAlphaComponent(request.dark ? 0.14 : 0.09),
                                        range: line.range)
                    result.addAttribute(.foregroundColor, value: color,
                                        range: NSRange(location: line.range.location, length: 1))
                }
            } else {
                result.addAttribute(.foregroundColor, value: request.dark
                    ? NSColor(white: 0.58, alpha: 1) : NSColor(white: 0.42, alpha: 1), range: line.range)
            }
        }
        return ReviewStyledText(value: result.copy() as! NSAttributedString)
    }
}

enum ReviewDiffSyntax {
    struct Line {
        let range: NSRange
        let source: String
        let kind: Character?
    }

    static func lines(_ patch: String) -> [Line] {
        let text = patch as NSString
        let header = try! NSRegularExpression(pattern: #"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@"#)
        var result: [Line] = [], offset = 0, old = 0, new = 0
        while offset < text.length {
            let range = text.lineRange(for: NSRange(location: offset, length: 0))
            let line = text.substring(with: range)
            var kind: Character?
            if let match = header.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                func count(_ index: Int) -> Int {
                    let capture = match.range(at: index)
                    return capture.location == NSNotFound ? 1 : Int((line as NSString).substring(with: capture)) ?? 0
                }
                old = count(1); new = count(2)
            } else if old > 0 || new > 0 {
                switch line.first {
                case " " where old > 0 && new > 0: kind = " "; old -= 1; new -= 1
                case "-" where old > 0: kind = "-"; old -= 1
                case "+" where new > 0: kind = "+"; new -= 1
                default: break
                }
            }
            result.append(Line(range: range, source: kind == nil ? "" : String(line.dropFirst()), kind: kind))
            offset = NSMaxRange(range)
        }
        return result
    }
}
