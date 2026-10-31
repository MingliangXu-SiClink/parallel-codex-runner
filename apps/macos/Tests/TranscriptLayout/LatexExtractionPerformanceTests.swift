import Foundation
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
struct LatexExtractionPerformanceTests {
    @Test func ordinaryLongConversationDoesNotAllocateDelimiterSlicesForEveryCharacter() {
        let paragraph = String(repeating: "中文 conversation with numbers 123, paths /project/results and punctuation. ", count: 12)
        let source = Array(repeating: paragraph, count: 200).joined(separator: "\n")
        let began = ContinuousClock.now
        let extraction = LatexMarkdownExtractor.extract(source)
        let elapsed = began.duration(to: .now)
        #expect(extraction.markdown == source)
        #expect(extraction.fragments.isEmpty)
        #expect(elapsed < .milliseconds(50))
        print("Plain conversation LaTeX scan: \(source.utf8.count) bytes in \(elapsed)")
    }

    @Test func fastPathPreservesMathCodeEscapesAndUnicode() {
        let source = #"""
        普通文本 `not $math$` and escaped \$currency.
        Inline $x_1$ and \(\alpha + \beta\).
        [x^2 + y_1]
        **[z_2]**
        ```text
        $not_math$ and \[not_math\]
        ```
        \[
        \frac{a}{b}
        \]
        """#
        let extraction = LatexMarkdownExtractor.extract(source)
        #expect(extraction.fragments.map(\.source) == ["x_1", #"\alpha + \beta"#, "x^2 + y_1", "z_2", #"\frac{a}{b}"#])
        #expect(extraction.fragments.map(\.isDisplay) == [false, false, true, true, true])
        #expect(extraction.markdown.contains(#"`not $math$` and escaped \$currency."#))
        #expect(extraction.markdown.contains(#"$not_math$ and \[not_math\]"#))
    }
}
