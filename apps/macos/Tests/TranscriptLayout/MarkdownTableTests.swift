import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct MarkdownTableTests {
    private let source = #"""
    - \(-1\): left.
    - \(+1\): right.

    | 动作块 | 含义 | 出现比例 |
    |---|---|---:|
    | \((-1,-1)\) | 两步都走左侧 | 50% |
    | \((+1,+1)\) | 两步都走右侧 | 50% |

    Independently sampling each action:

    | 生成的动作块 | 概率 | 结果 |
    |---|---:|---|
    | \((-1,-1)\) | \(0.5\times0.5=25\%\) | 安全 |
    | \((-1,+1)\) | 25% | 中途换侧 |
    | \((+1,-1)\) | 25% | 中途换侧 |
    | \((+1,+1)\) | 25% | 安全 |

    The joint distribution:

    \[
    P((-1,-1))=50\%,\qquad P((+1,+1))=50\%
    \]
    """#

    @Test
    func inlineAndDisplayFormulasHaveUniqueTokensInDocumentOrder() {
        let extracted = LatexMarkdownExtractor.extract(source)
        #expect(extracted.fragments.count == 10)
        #expect(Set(extracted.fragments.map(\.token)).count == extracted.fragments.count)
        for fragment in extracted.fragments {
            #expect(extracted.markdown.components(separatedBy: fragment.token).count == 2)
        }
        #expect(extracted.fragments[5].source == #"0.5\times0.5=25\%"#)
        #expect(extracted.fragments.last?.source == #"P((-1,-1))=50\%,\qquad P((+1,+1))=50\%"#)
    }

    @Test
    func formulaAttachmentsRemainInTheirOriginalTableCells() throws {
        let text = render(source)
        var positions: [Int] = []
        text.enumerateAttribute(.attachment, in: NSRange(location: 0, length: text.length)) { value, range, _ in
            if value is NSTextAttachment { positions.append(range.location) }
        }
        #expect(positions.count == 10)
        let expected = [(1, 0), (2, 0), (1, 0), (1, 1), (2, 0), (3, 0), (4, 0)]
        for (position, coordinates) in zip(positions.dropFirst(2).dropLast(), expected) {
            let style = try #require(text.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle)
            let cell = try #require(style.textBlocks.first as? NSTextTableBlock)
            #expect(cell.startingRow == coordinates.0)
            #expect(cell.startingColumn == coordinates.1)
        }
        let displayPosition = try #require(positions.last)
        let displayStyle = try #require(text.attribute(.paragraphStyle, at: displayPosition, effectiveRange: nil) as? NSParagraphStyle)
        #expect(displayStyle.textBlocks.isEmpty)
    }

    @Test
    func formulaFallbackAndLinksPreserveTheirSurroundingStyle() throws {
        let fallback = render(#"""
        | Formula | Value |
        |---|---|
        | \(\notARealMathCommand{x}\) | retained |
        """#)
        let position = (fallback.string as NSString).range(of: #"\notARealMathCommand"#).location
        #expect(position != NSNotFound)
        let style = try #require(fallback.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle)
        #expect(style.textBlocks.first is NSTextTableBlock)

        let linked = render(#"[\(x^2\)](https://example.com)"#)
        #expect(linked.attribute(.attachment, at: 0, effectiveRange: nil) is NSTextAttachment)
        #expect(linked.attribute(.link, at: 0, effectiveRange: nil) as? URL == URL(string: "https://example.com"))
    }

    @Test
    func tableColumnAlignmentAppliesToHeadersTextAndFormulas() throws {
        let text = render(#"""
        | Left | Center | Right |
        |:---|:---:|---:|
        | text | \(x^2\) | 50% |
        """#)
        var position = 0
        var cellCount = 0
        let expected: [NSTextAlignment] = [.left, .center, .right]
        while position < text.length {
            let range = (text.string as NSString).paragraphRange(for: NSRange(location: position, length: 0))
            let style = try #require(text.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle)
            let cell = try #require(style.textBlocks.first as? NSTextTableBlock)
            #expect(style.alignment == expected[cell.startingColumn])
            cellCount += 1
            position = NSMaxRange(range)
        }
        #expect(cellCount == 6)
    }

    @Test(arguments: [2, 3, 6])
    func outerTableBordersRemainVisibleAtTheTextContainerEdge(columns: Int) throws {
        _ = NSApplication.shared
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 400),
            styleMask: [.borderless], backing: .buffered, defer: false
        )
        defer { window.contentView = nil }
        for appearance in [TranscriptAppearance.light, .dark] {
            window.appearance = appearance.nsAppearance
            for width: CGFloat in [360, 746, 760, 799.5, 800] {
                let headings = (1...columns).map { "Item \($0)" }.joined(separator: " | ")
                let separators = Array(repeating: "---", count: columns).joined(separator: " | ")
                let values = (Array(repeating: "alpha", count: columns - 1) + ["stable"]).joined(separator: " | ")
                let text = render("| \(headings) |\n| \(separators) |\n| \(values) |", appearance: appearance)
                let view = NSTextView(usingTextLayoutManager: false)
                view.isEditable = false
                view.isHorizontallyResizable = false
                view.isVerticallyResizable = false
                view.clipsToBounds = true
                view.textContainerInset = .zero
                view.textContainer?.lineFragmentPadding = 0
                view.textContainer?.widthTracksTextView = false
                view.textContainer?.containerSize = NSSize(width: width, height: 300)
                view.backgroundColor = appearance == .dark ? .black : .white
                view.textStorage?.setAttributedString(text)
                window.contentView?.addSubview(view)
                defer { view.removeFromSuperview() }
                view.frame = NSRect(x: 0, y: 0, width: width, height: 300)
                let layout = try #require(view.layoutManager)
                layout.ensureLayout(for: try #require(view.textContainer))
                let position = (text.string as NSString).range(of: "stable").location
                let row = layout.lineFragmentRect(
                    forGlyphAt: layout.glyphIndexForCharacter(at: position), effectiveRange: nil
                )
                let style = try #require(text.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle)
                let cell = try #require(style.textBlocks.first)
                let bounds = layout.boundsRect(for: cell, at: layout.glyphIndexForCharacter(at: position), effectiveRange: nil)
                #expect(bounds.maxX <= width, "Table border exceeds its text view: \(bounds), width \(width)")
                let bitmap = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
                view.cacheDisplay(in: view.bounds, to: bitmap)
                let scale = CGFloat(bitmap.pixelsWide) / width
                let y = Int(row.midY * scale)
                let background = try #require(bitmap.colorAt(x: bitmap.pixelsWide - 10, y: y)?.usingColorSpace(.deviceRGB))
                var borderContrast: CGFloat = 0
                let edge = Int(bounds.maxX * scale)
                for x in max(0, edge - 3)..<min(bitmap.pixelsWide, edge + 1) {
                    let pixel = try #require(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                    borderContrast = max(borderContrast, abs(pixel.redComponent - background.redComponent))
                }
                #expect(borderContrast > 0.04, "Right border clipped at width \(width), \(appearance): \(borderContrast)")
            }
        }
    }

    @Test
    func tableAndFollowingCodeBlockHaveAnUnboxedSeparator() throws {
        let text = render("""
        | Item | Status |
        |---|---|
        | alpha | finished |

        ```python
        items = 3
        print(items)
        ```
        """)
        let source = text.string as NSString
        let tableEnd = NSMaxRange(source.range(of: "finished"))
        let codeStart = source.range(of: "items = 3").location
        var separatorCount = 0
        var position = tableEnd + 1
        while position < codeStart {
            let range = source.paragraphRange(for: NSRange(location: position, length: 0))
            let style = try #require(text.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle)
            #expect(style.textBlocks.isEmpty, "Spacing must not extend the code accent rule into the table")
            separatorCount += 1
            position = NSMaxRange(range)
        }
        #expect(separatorCount > 0)
        let codeStyle = try #require(text.attribute(.paragraphStyle, at: codeStart, effectiveRange: nil) as? NSParagraphStyle)
        #expect(codeStyle.textBlocks.first is NSTextTableBlock)
    }

    @Test
    func tableRowsAndColumnsStayAlignedAcrossResizeAndAppearance() throws {
        _ = NSApplication.shared
        for appearance in [TranscriptAppearance.light, .dark] {
            for textSize in AppTextSize.allCases {
                let text = render(source, size: textSize, appearance: appearance)
                let storage = NSTextStorage(attributedString: text)
                let layout = NSLayoutManager()
                let container = NSTextContainer(size: NSSize(width: 760, height: CGFloat.greatestFiniteMagnitude))
                container.lineFragmentPadding = 0
                layout.addTextContainer(container)
                storage.addLayoutManager(layout)
                for width: CGFloat in [760, 360, 620, 760] {
                    container.containerSize.width = width
                    layout.ensureLayout(for: container)
                    var tables: [ObjectIdentifier: [(NSTextTableBlock, NSRect)]] = [:]
                    var position = 0
                    while position < text.length {
                        let range = (text.string as NSString).paragraphRange(for: NSRange(location: position, length: 0))
                        if let style = text.attribute(.paragraphStyle, at: position, effectiveRange: nil) as? NSParagraphStyle,
                           let cell = style.textBlocks.first as? NSTextTableBlock {
                            let glyph = layout.glyphIndexForCharacter(at: position)
                            let rect = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
                            tables[ObjectIdentifier(cell.table), default: []].append((cell, rect))
                        }
                        position = NSMaxRange(range)
                    }
                    #expect(tables.count == 2)
                    #expect(tables.values.map(\.count).sorted() == [9, 15])
                    for cells in tables.values {
                        for row in Set(cells.map { $0.0.startingRow }) {
                            let rowCells = cells.filter { $0.0.startingRow == row }.sorted { $0.0.startingColumn < $1.0.startingColumn }
                            #expect(rowCells.count == 3)
                            let top = try #require(rowCells.first?.1.minY)
                            for (_, rect) in rowCells {
                                #expect(abs(rect.minY - top) <= 0.5, "Split table row at width \(width), size \(textSize)")
                                #expect(rect.minX >= -0.5)
                                #expect(rect.maxX <= width + 0.5)
                            }
                            for pair in zip(rowCells, rowCells.dropFirst()) {
                                #expect(pair.0.1.maxX <= pair.1.1.minX + 0.5)
                            }
                        }
                    }
                }
            }
        }
    }

    private func render(_ source: String, size: AppTextSize = .standard, appearance: TranscriptAppearance = .dark) -> NSAttributedString {
        TranscriptTextRenderer.attributedText(
            for: TranscriptBlock(id: "table-formula-regression", kind: .output, text: source),
            textSize: size, accentColor: .orange, appearance: appearance
        )
    }
}
