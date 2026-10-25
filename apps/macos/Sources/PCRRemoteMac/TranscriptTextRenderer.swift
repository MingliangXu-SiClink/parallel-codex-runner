import AppKit
import Foundation
import ImageIO
import SwiftMath

private final class TranscriptImageAttachment: NSTextAttachment {
    override func attachmentBounds(for textContainer: NSTextContainer?, proposedLineFragment lineFrag: NSRect,
                                   glyphPosition position: NSPoint, characterIndex charIndex: Int) -> NSRect {
        guard let image else { return .zero }
        let width = max(1, min(720, (textContainer?.containerSize.width ?? lineFrag.width) - 2))
        let factor = min(1, width / max(1, image.size.width), 540 / max(1, image.size.height))
        return NSRect(x: 0, y: 0, width: image.size.width * factor, height: image.size.height * factor)
    }
}

@MainActor
enum TranscriptTextRenderer {
    private final class AttributedTextKey: NSObject {
        let kind: TranscriptKind
        let text: String
        let isHistorical: Bool
        let isFinalAnswer: Bool
        let textSize: AppTextSize
        let accentColor: AppAccentColor
        let appearance: TranscriptAppearance
        let operation: WorkOperationRecord?
        let activityType: String?
        let image: TranscriptImageContent?

        init(
            block: TranscriptBlock,
            textSize: AppTextSize,
            accentColor: AppAccentColor,
            appearance: TranscriptAppearance
        ) {
            kind = block.kind
            text = block.text
            isHistorical = block.isHistorical
            isFinalAnswer = block.isFinalAnswer
            self.textSize = textSize
            self.accentColor = accentColor
            self.appearance = appearance
            operation = block.operation
            activityType = block.activityType
            image = block.image
        }

        override var hash: Int {
            var hasher = Hasher()
            hasher.combine(kind.rawValue)
            hasher.combine(text)
            hasher.combine(isHistorical)
            hasher.combine(isFinalAnswer)
            hasher.combine(textSize.rawValue)
            hasher.combine(accentColor.rawValue)
            hasher.combine(appearance.rawValue)
            hasher.combine(operation)
            hasher.combine(activityType)
            hasher.combine(image)
            return hasher.finalize()
        }

        override func isEqual(_ object: Any?) -> Bool {
            guard let other = object as? AttributedTextKey else { return false }
            return kind == other.kind
                && text == other.text
                && isHistorical == other.isHistorical
                && isFinalAnswer == other.isFinalAnswer
                && textSize == other.textSize
                && accentColor == other.accentColor
                && appearance == other.appearance
                && operation == other.operation
                && activityType == other.activityType
                && image == other.image
        }
    }

    private static let attributedTextCache: NSCache<AttributedTextKey, NSAttributedString> = {
        let cache = NSCache<AttributedTextKey, NSAttributedString>()
        cache.countLimit = 768
        cache.totalCostLimit = 32 * 1_024 * 1_024
        return cache
    }()

    private struct TableCellRole: Hashable {
        let tableID: Int
        let columns: Int
        let row: Int
        let column: Int
        let isHeader: Bool
        let alignment: NSTextAlignment
    }

    private enum BlockRole: Equatable {
        case paragraph
        case heading(Int)
        case unorderedItem(depth: Int)
        case orderedItem(ordinal: Int, depth: Int)
        case code(Int)
        case quote
        case thematicBreak
        case tableCell(TableCellRole)

        var isTextBlock: Bool {
            switch self {
            case .code, .tableCell: true
            default: false
            }
        }
    }

    private struct BlockInfo: Equatable {
        let identity: Int
        let role: BlockRole
    }

    private final class ContainedMarkdownTable: NSTextTable {
        override func rect(
            for block: NSTextTableBlock, layoutAt startingPoint: NSPoint,
            in rect: NSRect, textContainer: NSTextContainer, characterRange: NSRange
        ) -> NSRect {
            // TextKit rounds full-width cell bounds past the container edge.
            // Reserve room for the outer border before it lays out columns.
            var available = rect
            available.size.width = max(1, rect.width - 1)
            return super.rect(for: block, layoutAt: startingPoint, in: available,
                              textContainer: textContainer, characterRange: characterRange)
        }
    }

    private final class MarkdownTableRegistry {
        private var tables: [Int: NSTextTable] = [:]
        private var cells: [TableCellRole: NSTextTableBlock] = [:]

        func cell(for role: BlockRole) -> NSTextTableBlock? {
            guard case .tableCell(let cellRole) = role else { return nil }
            if let existing = cells[cellRole] { return existing }

            let table = tables[cellRole.tableID] ?? makeTable(
                id: cellRole.tableID,
                columns: cellRole.columns
            )
            let cell = NSTextTableBlock(
                table: table,
                startingRow: cellRole.row,
                rowSpan: 1,
                startingColumn: cellRole.column,
                columnSpan: 1
            )
            cell.verticalAlignment = .topAlignment
            cell.setWidth(6, type: .absoluteValueType, for: .padding)
            cell.setWidth(0.5, type: .absoluteValueType, for: .border)
            cell.setBorderColor(ParallelCodexVisualStyle.tableBorderNSColor)
            if cellRole.isHeader {
                cell.backgroundColor = ParallelCodexVisualStyle.userSurfaceNSColor
            } else if cellRole.row.isMultiple(of: 2) {
                cell.backgroundColor = ParallelCodexVisualStyle.appearancePreservingAlpha(
                    .quaternaryLabelColor,
                    alpha: 0.035
                )
            }
            cells[cellRole] = cell
            return cell
        }

        private func makeTable(id: Int, columns: Int) -> NSTextTable {
            let table = ContainedMarkdownTable()
            table.numberOfColumns = max(1, columns)
            table.layoutAlgorithm = .fixedLayoutAlgorithm
            table.collapsesBorders = true
            table.hidesEmptyCells = false
            table.setContentWidth(100, type: .percentageValueType)
            tables[id] = table
            return table
        }
    }

    private final class MarkdownCodeBlockRegistry {
        private let accentColor: NSColor
        private var blocks: [Int: NSTextTableBlock] = [:]

        init(accentColor: NSColor) {
            self.accentColor = accentColor
        }

        func block(for role: BlockRole) -> NSTextTableBlock? {
            guard case .code(let identity) = role else { return nil }
            if let existing = blocks[identity] { return existing }

            let table = NSTextTable()
            table.numberOfColumns = 1
            table.layoutAlgorithm = .fixedLayoutAlgorithm
            table.collapsesBorders = true
            table.hidesEmptyCells = false
            table.setContentWidth(100, type: .percentageValueType)

            let block = NSTextTableBlock(
                table: table,
                startingRow: 0,
                rowSpan: 1,
                startingColumn: 0,
                columnSpan: 1
            )
            block.verticalAlignment = .topAlignment
            block.setWidth(12, type: .absoluteValueType, for: .padding, edge: .minX)
            block.setWidth(10, type: .absoluteValueType, for: .padding, edge: .maxX)
            block.setWidth(8, type: .absoluteValueType, for: .padding, edge: .minY)
            block.setWidth(8, type: .absoluteValueType, for: .padding, edge: .maxY)
            block.setWidth(2, type: .absoluteValueType, for: .border, edge: .minX)
            block.setBorderColor(
                ParallelCodexVisualStyle.appearancePreservingAlpha(
                    accentColor,
                    alpha: 0.58
                )
            )
            // Spacing and the accent rule are enough to identify a fenced
            // block; a full-width gray band makes normal replies feel chopped
            // into cards.
            block.backgroundColor = .clear
            blocks[identity] = block
            return block
        }
    }

    static func attributedText(
        for block: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        appearance: TranscriptAppearance
    ) -> NSAttributedString {
        let key = AttributedTextKey(
            block: block,
            textSize: textSize,
            accentColor: accentColor,
            appearance: appearance
        )
        if let cached = attributedTextCache.object(forKey: key) { return cached }
        var rendered = NSAttributedString()
        appearance.nsAppearance.performAsCurrentDrawingAppearance {
            if let content = block.image, let image = imageAttachment(content) {
                rendered = image
            } else {
                rendered = switch block.kind {
                case .thought, .command, .activity:
                    plain(
                        block.text,
                        block: block,
                        textSize: textSize,
                        accentColor: accentColor
                    )
                default:
                    markdown(
                        block.text,
                        block: block,
                        textSize: textSize,
                        accentColor: accentColor
                    )
                }
            }
        }
        // NSTextView resolves missing glyphs to fallback fonts when it installs
        // text. Resolve them before measuring too, so CJK/emoji cannot rewrap
        // into an extra line after the row height has already been cached.
        let resolved = NSMutableAttributedString(attributedString: rendered)
        if let operation = block.operation, operation.targets.count == 1,
           operation.kind == .fileRead || operation.kind == .fileChange {
            let path = operation.targets[0].path
            let name = WorkOperationPresentation.displayPath(path)
            let range = (resolved.string as NSString).range(of: name)
            if range.location != NSNotFound,
               let escaped = path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
               let url = URL(string: escaped.hasPrefix("/") ? escaped : "./" + escaped) {
                resolved.addAttributes([.link: url, .underlineStyle: NSUnderlineStyle.single.rawValue], range: range)
            }
        }
        resolved.fixFontAttribute(in: NSRange(location: 0, length: resolved.length))
        rendered = NSAttributedString(attributedString: resolved)
        attributedTextCache.setObject(
            rendered,
            forKey: key,
            cost: max(1, rendered.length * MemoryLayout<unichar>.stride + (block.image == nil ? 0 : 1600 * 1600 * 4))
        )
        return rendered
    }

    private static func imageAttachment(_ content: TranscriptImageContent) -> NSAttributedString? {
        guard content.data.count <= 16 * 1024 * 1024,
              let source = CGImageSourceCreateWithData(content.data as CFData,
                  [kCGImageSourceShouldCache: false] as CFDictionary),
              let type = CGImageSourceGetType(source) as String?,
              ["public.png", "public.jpeg", "com.compuserve.gif", "org.webmproject.webp"].contains(type),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Double,
              let height = properties[kCGImagePropertyPixelHeight] as? Double,
              width > 0, height > 0, width * height <= 100_000_000,
              let pixels = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceThumbnailMaxPixelSize: 1600,
                  kCGImageSourceCreateThumbnailWithTransform: true,
                  kCGImageSourceShouldCacheImmediately: true,
              ] as CFDictionary) else { return nil }
        let image = NSImage(cgImage: pixels, size: NSSize(width: pixels.width, height: pixels.height))
        image.accessibilityDescription = "Generated image"
        let attachment = TranscriptImageAttachment()
        attachment.image = image
        return NSAttributedString(attachment: attachment)
    }

    static func textHeight(_ text: NSAttributedString, width: CGFloat, maximumLines: Int = 0) -> CGFloat {
        guard width > 0, text.length > 0 else { return 1 }
        let storage = NSTextStorage(attributedString: text)
        let layout = NSLayoutManager()
        let container = NSTextContainer(
            containerSize: NSSize(width: width, height: .greatestFiniteMagnitude)
        )
        container.lineFragmentPadding = 0
        container.widthTracksTextView = false
        container.heightTracksTextView = false
        container.maximumNumberOfLines = maximumLines
        container.lineBreakMode = maximumLines > 0 ? .byTruncatingTail : .byWordWrapping
        layout.addTextContainer(container)
        storage.addLayoutManager(layout)
        layout.ensureLayout(for: container)
        return max(1, ceil(layout.usedRect(for: container).height))
    }

    static func naturalTextWidth(_ text: NSAttributedString, maximum: CGFloat) -> CGFloat {
        guard maximum > 0, text.length > 0 else { return 1 }
        let bounds = text.boundingRect(
            with: NSSize(width: maximum, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading]
        )
        return min(maximum, ceil(bounds.width))
    }

    private static func plain(
        _ text: String,
        block: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor
    ) -> NSAttributedString {
        NSAttributedString(
            string: text,
            attributes: baseAttributes(
                transcript: block,
                markdown: .paragraph,
                textSize: textSize,
                accentColor: accentColor
            )
        )
    }

    private static func markdown(
        _ source: String,
        block transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor
    ) -> NSAttributedString {
        let source = TranscriptFormatter.markdownPresentationText(source)
        let latex = LatexMarkdownExtractor.extract(source)
        guard let parsed = try? AttributedString(
            markdown: latex.markdown,
            options: .init(interpretedSyntax: .full)
        ) else {
            return plain(
                source,
                block: transcript,
                textSize: textSize,
                accentColor: accentColor
            )
        }

        let output = NSMutableAttributedString()
        var previousBlock: BlockInfo?
        let tableRegistry = MarkdownTableRegistry()
        let codeBlockRegistry = MarkdownCodeBlockRegistry(accentColor: accentColor.nsColor)
        for run in parsed.runs {
            let block = blockInfo(for: run.presentationIntent)
            if block != previousBlock {
                appendBoundary(
                    from: previousBlock,
                    to: block,
                    output: output,
                    transcript: transcript,
                    textSize: textSize,
                    accentColor: accentColor,
                    tableRegistry: tableRegistry,
                    codeBlockRegistry: codeBlockRegistry
                )
                previousBlock = block
            }

            // Markdown treats a single newline as a space. User messages must
            // keep the line breaks they entered without disabling rich text.
            let value = transcript.kind == .user && run.inlinePresentationIntent?.contains(.softBreak) == true
                ? "\n"
                : String(parsed[run.range].characters)
            guard !value.isEmpty else { continue }
            var attributes = baseAttributes(
                transcript: transcript,
                markdown: block.role,
                textSize: textSize,
                accentColor: accentColor,
                textBlock: tableRegistry.cell(for: block.role)
                    ?? codeBlockRegistry.block(for: block.role)
            )
            applyInlineStyles(run.inlinePresentationIntent, to: &attributes)
            if let link = run.link ?? run.imageURL {
                attributes[.link] = link
                attributes[.foregroundColor] = accentColor.nsColor
                attributes[.underlineStyle] = NSUnderlineStyle.single.rawValue
            }
            output.append(NSAttributedString(string: value, attributes: attributes))
        }
        if let previousBlock,
           case .tableCell = previousBlock.role {
            output.append(
                NSAttributedString(
                    string: "\n",
                    attributes: baseAttributes(
                        transcript: transcript,
                        markdown: previousBlock.role,
                        textSize: textSize,
                        accentColor: accentColor,
                        textBlock: tableRegistry.cell(for: previousBlock.role)
                    )
                )
            )
        }
        guard output.length > 0 else {
            return plain(
                source,
                block: transcript,
                textSize: textSize,
                accentColor: accentColor
            )
        }
        replaceLatexFragments(
            latex.fragments,
            in: output,
            transcript: transcript,
            textSize: textSize,
            accentColor: accentColor
        )
        return output
    }

    private static func replaceLatexFragments(
        _ fragments: [LatexMarkdownFragment],
        in output: NSMutableAttributedString,
        transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor
    ) {
        for fragment in fragments.reversed() {
            let range = (output.string as NSString).range(of: fragment.token)
            guard range.location != NSNotFound else { continue }
            let replacement = NSMutableAttributedString(
                attributedString: latexAttachment(
                    fragment,
                    transcript: transcript,
                    textSize: textSize,
                    accentColor: accentColor
                )
                    ?? NSAttributedString(string: fragment.original)
            )
            // An inline formula must retain its table cell, paragraph and link
            // attributes; an attachment alone detaches a formula-headed row.
            replacement.addAttributes(
                output.attributes(at: range.location, effectiveRange: nil),
                range: NSRange(location: 0, length: replacement.length)
            )
            output.replaceCharacters(in: range, with: replacement)
        }
    }

    private static func latexAttachment(
        _ fragment: LatexMarkdownFragment,
        transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor
    ) -> NSAttributedString? {
        if let nativeFormula = LatexNativeTextFormulaParser.parse(fragment.source) {
            return nativeTextFormulaAttachment(
                nativeFormula,
                isDisplay: fragment.isDisplay,
                transcript: transcript,
                textSize: textSize,
                accentColor: accentColor
            )
        }

        let wrapper = LatexNativeTextFormulaParser.unwrapBox(fragment.source)
        let label = MTMathUILabel()
        label.latex = wrapper.source
        label.labelMode = fragment.isDisplay ? .display : .text
        label.fontSize = (fragment.isDisplay ? 17 : 15) * textSize.transcriptScale
        let textColor = baseAttributes(
            transcript: transcript,
            markdown: .paragraph,
            textSize: textSize,
            accentColor: accentColor
        )[.foregroundColor] as? NSColor ?? .labelColor
        label.textColor = textColor
        let measured = label.fittingSize
        guard label.error == nil,
              measured.width.isFinite,
              measured.height.isFinite,
              measured.width > 0,
              measured.height > 0 else { return nil }

        let scale = min(1, 620 / measured.width)
        let size = NSSize(
            width: ceil(measured.width * scale),
            height: ceil(measured.height * scale)
        )
        label.frame = NSRect(origin: .zero, size: measured)
        guard let representation = label.bitmapImageRepForCachingDisplay(in: label.bounds) else {
            return nil
        }
        label.cacheDisplay(in: label.bounds, to: representation)
        var image = NSImage(size: size)
        image.addRepresentation(representation)
        image.size = size
        if wrapper.boxed {
            image = boxedImage(
                image,
                color: textColor,
                horizontalPadding: 8,
                verticalPadding: 5
            )
        }

        return attachment(for: image, isDisplay: fragment.isDisplay)
    }

    private static func nativeTextFormulaAttachment(
        _ formula: LatexNativeTextFormula,
        isDisplay: Bool,
        transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor
    ) -> NSAttributedString? {
        let color = baseAttributes(
            transcript: transcript,
            markdown: .paragraph,
            textSize: textSize,
            accentColor: accentColor
        )[.foregroundColor] as? NSColor ?? .labelColor
        let baseFontSize: CGFloat = (isDisplay ? 17 : 15) * textSize.transcriptScale
        var font = NSFont.systemFont(ofSize: baseFontSize, weight: .regular)
        var attributed = NSAttributedString(
            string: formula.text,
            attributes: [.font: font, .foregroundColor: color]
        )
        let maximumWidth: CGFloat = isDisplay ? 600 : 420
        let naturalWidth = ceil(attributed.size().width)
        if naturalWidth > maximumWidth {
            let scale = max(0.72, maximumWidth / naturalWidth)
            font = NSFont.systemFont(ofSize: baseFontSize * scale, weight: .regular)
            attributed = NSAttributedString(
                string: formula.text,
                attributes: [.font: font, .foregroundColor: color]
            )
        }
        let textSize = attributed.size()
        guard textSize.width.isFinite,
              textSize.height.isFinite,
              textSize.width > 0,
              textSize.height > 0 else { return nil }

        let horizontalPadding: CGFloat = formula.boxed ? 10 : 1
        let verticalPadding: CGFloat = formula.boxed ? 6 : 1
        let size = NSSize(
            width: ceil(textSize.width + horizontalPadding * 2),
            height: ceil(textSize.height + verticalPadding * 2)
        )
        let image = NSImage(size: size)
        image.lockFocus()
        attributed.draw(
            at: NSPoint(x: horizontalPadding, y: verticalPadding)
        )
        if formula.boxed {
            let path = NSBezierPath(
                rect: NSRect(origin: .zero, size: size).insetBy(dx: 1, dy: 1)
            )
            path.lineWidth = 1.25
            ParallelCodexVisualStyle.appearancePreservingAlpha(
                color,
                alpha: 0.82
            ).setStroke()
            path.stroke()
        }
        image.unlockFocus()
        return attachment(for: image, isDisplay: isDisplay)
    }

    private static func boxedImage(
        _ source: NSImage,
        color: NSColor,
        horizontalPadding: CGFloat,
        verticalPadding: CGFloat
    ) -> NSImage {
        let size = NSSize(
            width: source.size.width + horizontalPadding * 2,
            height: source.size.height + verticalPadding * 2
        )
        let image = NSImage(size: size)
        image.lockFocus()
        source.draw(
            in: NSRect(
                x: horizontalPadding,
                y: verticalPadding,
                width: source.size.width,
                height: source.size.height
            )
        )
        let path = NSBezierPath(
            rect: NSRect(origin: .zero, size: size).insetBy(dx: 1, dy: 1)
        )
        path.lineWidth = 1.25
        ParallelCodexVisualStyle.appearancePreservingAlpha(
            color,
            alpha: 0.82
        ).setStroke()
        path.stroke()
        image.unlockFocus()
        return image
    }

    private static func attachment(
        for image: NSImage,
        isDisplay: Bool
    ) -> NSAttributedString {
        let attachment = NSTextAttachment()
        attachment.image = image
        attachment.bounds = NSRect(
            x: 0,
            y: isDisplay ? -4 : -3,
            width: image.size.width,
            height: image.size.height
        )
        return NSAttributedString(attachment: attachment)
    }

    private static func blockInfo(for intent: PresentationIntent?) -> BlockInfo {
        guard let components = intent?.components, let leaf = components.first else {
            return BlockInfo(identity: 0, role: .paragraph)
        }
        let listItem = markdownListItemPresentation(from: components)
        var quoted = false
        var tableRow: Int?
        var tableColumn: Int?
        var tableHeader = false
        var tableID: Int?
        var tableColumns = 0
        var tableAlignments: [NSTextAlignment] = []

        for component in components {
            switch component.kind {
            case .header(let level):
                return BlockInfo(identity: leaf.identity, role: .heading(level))
            case .codeBlock:
                return BlockInfo(identity: leaf.identity, role: .code(leaf.identity))
            case .thematicBreak:
                return BlockInfo(identity: leaf.identity, role: .thematicBreak)
            case .listItem, .orderedList, .unorderedList:
                break
            case .blockQuote:
                quoted = true
            case .tableHeaderRow:
                tableHeader = true
                tableRow = 0
            case .tableRow(let rowIndex):
                tableRow = rowIndex
            case .tableCell(let columnIndex):
                tableColumn = columnIndex
            case .table(let columns):
                tableID = component.identity
                tableColumns = columns.count
                tableAlignments = columns.map { column in
                    switch column.alignment {
                    case .left: .left
                    case .center: .center
                    case .right: .right
                    @unknown default: .natural
                    }
                }
            case .paragraph:
                break
            @unknown default:
                break
            }
        }
        if let column = tableColumn, let tableID {
            return BlockInfo(
                identity: leaf.identity,
                role: .tableCell(TableCellRole(
                    tableID: tableID,
                    columns: max(tableColumns, column + 1),
                    row: tableRow ?? 0,
                    column: column,
                    isHeader: tableHeader,
                    alignment: tableAlignments.indices.contains(column) ? tableAlignments[column] : .natural
                ))
            )
        }
        if let listItem {
            return BlockInfo(
                identity: leaf.identity,
                role: listItem.isOrdered
                    ? .orderedItem(ordinal: listItem.ordinal, depth: listItem.depth)
                    : .unorderedItem(depth: listItem.depth)
            )
        }
        if quoted {
            return BlockInfo(identity: leaf.identity, role: .quote)
        }
        return BlockInfo(identity: leaf.identity, role: .paragraph)
    }

    private static func appendBoundary(
        from previous: BlockInfo?,
        to current: BlockInfo,
        output: NSMutableAttributedString,
        transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        tableRegistry: MarkdownTableRegistry,
        codeBlockRegistry: MarkdownCodeBlockRegistry
    ) {
        if let previous {
            switch (previous.role, current.role) {
            case (.unorderedItem, .unorderedItem),
                 (.orderedItem, .orderedItem),
                 (.unorderedItem, .orderedItem),
                 (.orderedItem, .unorderedItem):
                appendBoundaryText(
                    "\n",
                    role: current.role,
                    output: output,
                    transcript: transcript,
                    textSize: textSize,
                    accentColor: accentColor,
                    tableRegistry: tableRegistry,
                    codeBlockRegistry: codeBlockRegistry
                )
            case (.tableCell(let oldCell), .tableCell(let newCell)):
                appendBoundaryText(
                    "\n",
                    role: previous.role,
                    output: output,
                    transcript: transcript,
                    textSize: textSize,
                    accentColor: accentColor,
                    tableRegistry: tableRegistry,
                    codeBlockRegistry: codeBlockRegistry
                )
                if oldCell.tableID != newCell.tableID {
                    appendTextBlockSpacer(to: output, textSize: textSize)
                }
            case (.tableCell, _):
                appendBoundaryText(
                    "\n",
                    role: previous.role,
                    output: output,
                    transcript: transcript,
                    textSize: textSize,
                    accentColor: accentColor,
                    tableRegistry: tableRegistry,
                    codeBlockRegistry: codeBlockRegistry
                )
                appendTextBlockSpacer(to: output, textSize: textSize)
            case (_, .tableCell):
                if !output.string.hasSuffix("\n") {
                    appendBoundaryText(
                        "\n", role: previous.role, output: output,
                        transcript: transcript, textSize: textSize, accentColor: accentColor,
                        tableRegistry: tableRegistry, codeBlockRegistry: codeBlockRegistry
                    )
                }
                appendTextBlockSpacer(to: output, textSize: textSize)
            default:
                if !previous.role.isTextBlock && !current.role.isTextBlock {
                    appendBoundaryText(
                        "\n", role: previous.role, output: output,
                        transcript: transcript, textSize: textSize, accentColor: accentColor,
                        tableRegistry: tableRegistry, codeBlockRegistry: codeBlockRegistry
                    )
                    // Paragraph spacing is independent of font height. A blank
                    // text line makes every paragraph look like a new message.
                    let range = (output.string as NSString).paragraphRange(
                        for: NSRange(location: output.length - 1, length: 0)
                    )
                    if let style = output.attribute(.paragraphStyle, at: range.location,
                                                    effectiveRange: nil) as? NSParagraphStyle,
                       let spaced = style.mutableCopy() as? NSMutableParagraphStyle {
                        spaced.paragraphSpacing = ParallelCodexVisualStyle.Typography.paragraphSpacing
                            * textSize.transcriptScale
                        output.addAttribute(.paragraphStyle, value: spaced, range: range)
                    }
                    break
                }
                if !output.string.hasSuffix("\n") {
                    appendBoundaryText(
                        "\n", role: previous.role, output: output,
                        transcript: transcript, textSize: textSize, accentColor: accentColor,
                        tableRegistry: tableRegistry, codeBlockRegistry: codeBlockRegistry
                    )
                }
                appendTextBlockSpacer(to: output, textSize: textSize)
            }
        }

        let prefix: String
        switch current.role {
        case .unorderedItem(let depth):
            prefix = String(repeating: "    ", count: max(0, depth - 1)) + "- "
        case .orderedItem(let ordinal, let depth):
            prefix = String(repeating: "    ", count: max(0, depth - 1)) + "\(ordinal). "
        case .quote:
            prefix = "| "
        case .thematicBreak:
            prefix = "------------------------"
        default:
            prefix = ""
        }
        if !prefix.isEmpty {
            output.append(
                NSAttributedString(
                    string: prefix,
                    attributes: baseAttributes(
                        transcript: transcript,
                        markdown: current.role,
                        textSize: textSize,
                        accentColor: accentColor
                    )
                )
            )
        }
    }

    private static func appendBoundaryText(
        _ text: String,
        role: BlockRole,
        output: NSMutableAttributedString,
        transcript: TranscriptBlock,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        tableRegistry: MarkdownTableRegistry,
        codeBlockRegistry: MarkdownCodeBlockRegistry
    ) {
        output.append(
            NSAttributedString(
                string: text,
                attributes: baseAttributes(
                    transcript: transcript,
                    markdown: role,
                    textSize: textSize,
                    accentColor: accentColor,
                    textBlock: tableRegistry.cell(for: role)
                        ?? codeBlockRegistry.block(for: role)
                )
            )
        )
    }

    private static func appendTextBlockSpacer(to output: NSMutableAttributedString, textSize: AppTextSize) {
        // TextKit needs an unboxed paragraph between tables/code blocks. Its
        // height is a spacing token, not an extra line of full-size body text.
        let style = NSMutableParagraphStyle()
        let height = ParallelCodexVisualStyle.Typography.paragraphSpacing * textSize.transcriptScale
        style.minimumLineHeight = height
        style.maximumLineHeight = height
        output.append(NSAttributedString(string: "\n", attributes: [
            .font: NSFont.systemFont(ofSize: 1), .paragraphStyle: style,
        ]))
    }

    private static func baseAttributes(
        transcript: TranscriptBlock,
        markdown block: BlockRole,
        textSize: AppTextSize,
        accentColor: AppAccentColor,
        textBlock: NSTextBlock? = nil
    ) -> [NSAttributedString.Key: Any] {
        let kind = transcript.kind
        var semanticColor: NSColor = switch kind {
        case .thought: ParallelCodexVisualStyle.reasoningTextNSColor
        case .command where transcript.activityType?.hasPrefix("legacy_command") == true: ParallelCodexVisualStyle.secondaryTextNSColor
        case .activity: ParallelCodexVisualStyle.secondaryTextNSColor
        case .warning: .systemOrange
        case .error: .systemRed
        default: ParallelCodexVisualStyle.primaryTextNSColor
        }
        if let operation = transcript.operation {
            semanticColor = operation.status == .failed ? .systemRed :
                operation.status == .running ? ParallelCodexVisualStyle.primaryTextNSColor
                    : ParallelCodexVisualStyle.secondaryTextNSColor
        }
        // Formula attachments are rendered into bitmaps. Keep their color
        // fixed to the appearance selected for this render instead of letting
        // AppKit resolve a semantic color against the bitmap context's default
        // Aqua appearance later.
        let color = semanticColor.usingColorSpace(.deviceRGB) ?? semanticColor
        let unscaledBaseSize: CGFloat = switch kind {
        case .user: ParallelCodexVisualStyle.Typography.body
        case .output: ParallelCodexVisualStyle.Typography.body
        case .thought, .warning, .error: ParallelCodexVisualStyle.Typography.work
        case .command: 12.5
        case .activity: ParallelCodexVisualStyle.Typography.work
        }
        let compactCommand = transcript.operation != nil || transcript.activityType?.hasPrefix("legacy_command") == true
        let baseSize = (compactCommand ? ParallelCodexVisualStyle.Typography.work : unscaledBaseSize)
            * textSize.transcriptScale
        var font: NSFont
        switch block {
        case .heading(let level):
            let size = switch level {
            case 1: baseSize + 6 * textSize.transcriptScale
            case 2: baseSize + 4 * textSize.transcriptScale
            case 3: baseSize + 2 * textSize.transcriptScale
            default: baseSize
            }
            font = NSFont.systemFont(ofSize: size, weight: .semibold)
        case .code:
            font = NSFont.monospacedSystemFont(ofSize: baseSize - textSize.transcriptScale, weight: .regular)
        case .tableCell(let cell):
            font = NSFont.systemFont(
                ofSize: baseSize - 0.5,
                weight: cell.isHeader ? .semibold : .regular
            )
        default:
            font = (kind == .command && !compactCommand) || transcript.activityType == "operation_details"
                ? NSFont.monospacedSystemFont(ofSize: baseSize, weight: .regular)
                : NSFont.systemFont(ofSize: baseSize)
        }
        let paragraph = NSMutableParagraphStyle()
        let lineSpacing: CGFloat = switch kind {
        case .user, .output: ParallelCodexVisualStyle.Typography.bodyLineSpacing
        case .thought, .warning, .error: 3
        case .command, .activity: 2
        }
        paragraph.lineSpacing = lineSpacing * textSize.transcriptScale
        paragraph.lineBreakMode = .byWordWrapping
        if case .code = block {
            paragraph.lineSpacing = 2 * textSize.transcriptScale
            paragraph.paragraphSpacing = 0
            if let textBlock { paragraph.textBlocks = [textBlock] }
        }
        if case .tableCell(let cell) = block {
            paragraph.lineSpacing = 3 * textSize.transcriptScale
            paragraph.paragraphSpacing = 0
            paragraph.alignment = cell.alignment
            if let textBlock { paragraph.textBlocks = [textBlock] }
        }

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: color,
            .paragraphStyle: paragraph,
        ]
        if case .quote = block {
            attributes[.foregroundColor] = ParallelCodexVisualStyle.secondaryTextNSColor
                .usingColorSpace(.deviceRGB) ?? ParallelCodexVisualStyle.secondaryTextNSColor
        }
        return attributes
    }

    private static func applyInlineStyles(
        _ intent: InlinePresentationIntent?,
        to attributes: inout [NSAttributedString.Key: Any]
    ) {
        guard let intent else { return }
        if intent.contains(.code) {
            let size = (attributes[.font] as? NSFont)?.pointSize ?? NSFont.systemFontSize
            attributes[.font] = NSFont.monospacedSystemFont(
                ofSize: max(10, size - 1),
                weight: .regular
            )
            attributes[.foregroundColor] =
                ParallelCodexVisualStyle.appearancePreservingAlpha(
                    ParallelCodexVisualStyle.primaryTextNSColor,
                    alpha: 0.86
                )
        } else if let current = attributes[.font] as? NSFont {
            var traits: NSFontTraitMask = []
            let emphasisFont = intent.contains(.stronglyEmphasized)
                ? NSFont.systemFont(ofSize: current.pointSize, weight: .semibold)
                : current
            if intent.contains(.emphasized) { traits.insert(.italicFontMask) }
            attributes[.font] = traits.isEmpty ? emphasisFont
                : NSFontManager.shared.convert(emphasisFont, toHaveTrait: traits)
        }
        if intent.contains(.strikethrough) {
            attributes[.strikethroughStyle] = NSUnderlineStyle.single.rawValue
        }
    }
}
