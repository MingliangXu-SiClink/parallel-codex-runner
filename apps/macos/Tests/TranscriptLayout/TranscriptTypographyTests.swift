import AppKit
import Testing
@testable import PCRRemoteMac

extension TranscriptLayoutTests {
    @Test
    @MainActor
    func replyTypographyDoesNotChangeWhenStreamingBecomesFinalOrHistorical() throws {
        for size in AppTextSize.allCases {
            let blocks = [
                TranscriptBlock(id: "stream", kind: .output, text: "Readable English and 中文正文"),
                TranscriptBlock(id: "progress", kind: .output, text: "Readable English and 中文正文",
                                activityType: "assistant_progress"),
                TranscriptBlock(id: "final", kind: .output, text: "Readable English and 中文正文", isFinalAnswer: true),
                TranscriptBlock(id: "history", kind: .output, text: "Readable English and 中文正文", isHistorical: true),
            ]
            let texts = blocks.map {
                TranscriptTextRenderer.attributedText(for: $0, textSize: size,
                    accentColor: .orange, appearance: .dark)
            }
            for text in texts {
                let color = try #require(text.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor)
                #expect(color == texts[0].attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor)
                let font = try #require(text.attribute(.font, at: 0, effectiveRange: nil) as? NSFont)
                #expect(font.pointSize == 16 * size.scale)
                let style = try #require(text.attribute(.paragraphStyle, at: 0, effectiveRange: nil) as? NSParagraphStyle)
                #expect(style.lineSpacing == 5 * size.scale)
                #expect(TranscriptTextRenderer.textHeight(text, width: 320)
                    == TranscriptTextRenderer.textHeight(texts[0], width: 320))
            }
        }
    }

    @Test
    @MainActor
    func paragraphRhythmUsesSpacingRatherThanFullHeightEmptyLines() throws {
        for size in AppTextSize.allCases {
            let text = TranscriptTextRenderer.attributedText(for:
                TranscriptBlock(id: "rhythm", kind: .output,
                    text: "First paragraph.\n\n第二段内容。\n\n- First item\n- Second item"),
                textSize: size, accentColor: .orange, appearance: .dark)
            #expect(text.string == "First paragraph.\n第二段内容。\n- First item\n- Second item")
            let paragraph = try #require(text.attribute(.paragraphStyle, at: 0,
                effectiveRange: nil) as? NSParagraphStyle)
            #expect(paragraph.paragraphSpacing == 12 * size.scale)
            let itemIndex = (text.string as NSString).range(of: "First item").location
            let item = try #require(text.attribute(.paragraphStyle, at: itemIndex,
                effectiveRange: nil) as? NSParagraphStyle)
            #expect(item.paragraphSpacing == 0)
            #expect(item.lineSpacing == 5 * size.scale)
        }
    }

    @Test
    @MainActor
    func emphasisHasAReadableWeightWithoutMakingInlineCodeBold() throws {
        let text = TranscriptTextRenderer.attributedText(for:
            TranscriptBlock(id: "weights", kind: .output, text: "Regular **Emphasis** `code`"),
            textSize: .standard, accentColor: .orange, appearance: .dark)
        func font(_ word: String) throws -> NSFont {
            let index = (text.string as NSString).range(of: word).location
            return try #require(text.attribute(.font, at: index, effectiveRange: nil) as? NSFont)
        }
        #expect(try font("Regular") == NSFont.systemFont(ofSize: 16))
        #expect(try font("Emphasis") == NSFont.systemFont(ofSize: 16, weight: .semibold))
        #expect(try font("code") == NSFont.monospacedSystemFont(ofSize: 15, weight: .regular))
    }

    @Test
    @MainActor
    func neutralReadingColorsKeepAccessibleContrastInBothAppearances() throws {
        func luminance(_ color: NSColor, _ appearance: TranscriptAppearance) throws -> CGFloat {
            let resolved = try #require(ParallelCodexVisualStyle.resolvedNSColor(color, for: appearance)
                .usingColorSpace(.sRGB))
            func linear(_ component: CGFloat) -> CGFloat {
                component <= 0.04045 ? component / 12.92 : pow((component + 0.055) / 1.055, 2.4)
            }
            return 0.2126 * linear(resolved.redComponent) + 0.7152 * linear(resolved.greenComponent)
                + 0.0722 * linear(resolved.blueComponent)
        }
        for appearance in [TranscriptAppearance.light, .dark] {
            for background in [ParallelCodexVisualStyle.canvasNSColor, ParallelCodexVisualStyle.userSurfaceNSColor] {
                for foreground in [ParallelCodexVisualStyle.primaryTextNSColor,
                                   ParallelCodexVisualStyle.secondaryTextNSColor,
                                   ParallelCodexVisualStyle.placeholderTextNSColor] {
                    let a = try luminance(background, appearance)
                    let b = try luminance(foreground, appearance)
                    #expect((max(a, b) + 0.05) / (min(a, b) + 0.05) >= 4.5)
                }
            }
        }
    }

    @Test
    func updatedTypographyCannotReuseOldPersistedRowHeights() {
        let input = TranscriptLayoutMetricKeyInput(kind: "output", text: "A paragraph.",
            statusPresent: false, activityType: nil, isHistorical: false, isFinalAnswer: true,
            textSize: "standard", maximumBodyWidth: 920)
        #expect(transcriptLayoutMetricKey(input) != transcriptLayoutMetricKey(input, schemaVersion: 13))
    }

    @Test
    @MainActor
    func codeAndTableSeparatorsDoNotInheritFullBodyLineHeight() throws {
        let text = TranscriptTextRenderer.attributedText(for:
            TranscriptBlock(id: "block-spacing", kind: .output,
                text: "Before.\n\n```swift\nlet value = 1\n```\n\nAfter.\n\n| Key | Value |\n| --- | --- |\n| a | b |\n\nLast."),
            textSize: .standard, accentColor: .orange, appearance: .dark)
        let source = text.string as NSString
        var location = 0
        var spacerCount = 0
        while location < source.length {
            let range = source.paragraphRange(for: NSRange(location: location, length: 0))
            if source.substring(with: range) == "\n" {
                let style = try #require(text.attribute(.paragraphStyle, at: location,
                    effectiveRange: nil) as? NSParagraphStyle)
                #expect(style.textBlocks.isEmpty)
                #expect(style.minimumLineHeight == 12)
                #expect(style.maximumLineHeight == 12)
                spacerCount += 1
            }
            location = NSMaxRange(range)
        }
        #expect(spacerCount == 4)
    }
}
