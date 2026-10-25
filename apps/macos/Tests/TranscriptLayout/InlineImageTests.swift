import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct InlineImageTests {
    private func png(width: Int = 640, height: Int = 360) throws -> Data {
        let rep = try #require(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
            bytesPerRow: 0, bitsPerPixel: 0))
        for y in 0..<height {
            for x in 0..<width {
                rep.setColor(NSColor(deviceRed: CGFloat(x % 255) / 255,
                    green: CGFloat(y % 255) / 255, blue: 0.6, alpha: 1), atX: x, y: y)
            }
        }
        return try #require(rep.representation(using: .png, properties: [:]))
    }

    private func source(_ data: Data, id: String = "native-image-call") throws -> String {
        // This is the exact adapter serialization shape, not an arbitrary
        // Base64 string or a command claiming to contain an image.
        "{\"type\": \"item.completed\", \"item\": {\"type\": \"imageGeneration\", \"id\": \"\(id)\", \"status\": \"completed\", \"result\": \"\(data.base64EncodedString())\"}}"
    }

    private func fragments(_ raw: String, startingAt event: Int = 10, size: Int = 128 * 1024) -> [TranscriptBlock] {
        let bytes = Array(raw.utf8)
        return stride(from: 0, to: bytes.count, by: size).enumerated().map { index, offset in
            TranscriptBlock(id: "event-\(event + index)", kind: .activity,
                text: String(decoding: bytes[offset..<min(bytes.count, offset + size)], as: UTF8.self))
        }
    }

    @Test func legacyFragmentPagesReconstructOneImageWithoutChangingTheSourceOrCursor() throws {
        let data = Data(repeating: 65, count: 300_000), raw = try source(data)
        let pages = fragments(raw)
        #expect(pages.count > 1)
        var loaded: [TranscriptBlock] = []
        for page in pages {
            TranscriptFormatter.append(items: [RemoteTranscriptItem(id: page.id,
                eventId: try #require(Int(page.id.dropFirst(6))), kind: "activity", text: page.text,
                status: nil, operationId: nil, activityType: nil)], to: &loaded)
            let projected = NativeImageTranscript.project(loaded)
            #expect(projected.count == 1)
            #expect(projected[0].id == "event-10")
            #expect(!projected[0].text.contains(data.base64EncodedString().prefix(32)))
            #expect(projected[0].image != nil || projected[0].activityType == "native_image_loading")
        }
        let projected = try #require(NativeImageTranscript.project(pages).first)
        #expect(projected.image?.data == data)
        #expect(projected.image?.sourceID == "native-image-call")
        #expect(projected.kind == .output)
        #expect(!TranscriptFormatter.isAgentWorkBlock(projected))
        #expect(pages.map(\.text).joined() == raw)
        let cache = AgentTranscript(sourceRunID: "run", sourceAgent: 1, blocks: pages, cursor: 500)
        let decoded = try JSONDecoder().decode(AgentTranscript.self, from: JSONEncoder().encode(cache))
        let visible = TranscriptFormatter.blocks(history: [], prompt: "Inspect", transcript: decoded)
        #expect(visible.first { $0.image != nil }?.image == projected.image)
        #expect(decoded.cursor == 500)
    }

    @Test func adjacentImagesAndRepliesKeepTheirOrderAndSourceIDs() throws {
        let first = fragments(try source(png(), id: "first"))
        let second = fragments(try source(png(width: 320, height: 480), id: "second"), startingAt: 1000)
        let before = TranscriptBlock(id: "reply-before", kind: .output, text: "Before")
        let after = TranscriptBlock(id: "reply-after", kind: .output, text: "After")
        let source = [before] + first + second + [after]
        let visible = TranscriptFormatter.coalescedVisibleConversation(NativeImageTranscript.project(source))
        #expect(visible.map(\.id) == ["reply-before", "event-10", "event-1000", "reply-after"])
        #expect(visible.compactMap(\.image).map(\.sourceID) == ["first", "second"])
    }

    @Test func ordinaryBase64CommandOutputUserTextAndOtherNativeToolsAreNotReinterpreted() throws {
        let data = try png(), raw = try source(data)
        let ordinary = [
            TranscriptBlock(id: "event-1", kind: .activity, text: data.base64EncodedString()),
            TranscriptBlock(id: "event-2:details", kind: .activity, text: raw, activityType: "operation_details"),
            TranscriptBlock(id: "event-3", kind: .user, text: raw),
            TranscriptBlock(id: "event-4", kind: .output, text: raw),
            TranscriptBlock(id: "event-5", kind: .activity, text: raw.replacingOccurrences(of: "imageGeneration", with: "otherTool")),
        ]
        #expect(NativeImageTranscript.project(ordinary) == ordinary)
        var missing = fragments(try source(Data(repeating: 65, count: 300_000)))
        missing.remove(at: 1)
        #expect(NativeImageTranscript.project(missing).first?.image == nil)
    }

    @Test func aBrokenImageCandidateCannotAbsorbTheFollowingActivityOrReply() throws {
        let first = try #require(fragments(source(Data(repeating: 65, count: 300_000))).first)
        let activity = TranscriptBlock(id: "event-11", kind: .activity, text: "Waiting")
        let reply = TranscriptBlock(id: "reply", kind: .output, text: "The next response")
        let visible = NativeImageTranscript.project([first, activity, reply])
        #expect(visible.map(\.id) == ["event-10", "event-11", "reply"])
        #expect(visible[0].image == nil)
        #expect(visible[1] == activity)
        #expect(visible[2] == reply)
        let invalid = fragments(try source(Data("not a PNG".utf8)))
        #expect(NativeImageTranscript.project(invalid + [reply]).last == reply)
    }

    @Test func attachmentsUseTheSameResponsiveBoundsForMeasurementDrawingAndCellReuse() throws {
        _ = NSApplication.shared
        let data = try png()
        let block = try #require(NativeImageTranscript.project(fragments(source(data))).first)
        let rendered = TranscriptTextRenderer.attributedText(for: block, textSize: .standard,
            accentColor: .orange, appearance: .dark)
        let attachment = try #require(rendered.attribute(.attachment, at: 0, effectiveRange: nil) as? NSTextAttachment)
        #expect(attachment.image?.size == NSSize(width: 640, height: 360))
        for width: CGFloat in [180, 360, 800] {
            let container = NSTextContainer(size: NSSize(width: width, height: 1000))
            let bounds = attachment.attachmentBounds(for: container,
                proposedLineFragment: NSRect(x: 0, y: 0, width: width, height: 1000),
                glyphPosition: .zero, characterIndex: 0)
            #expect(bounds.width <= width)
            #expect(abs(bounds.width / bounds.height - 640 / 360.0) < 0.01)
            let measured = TranscriptTextRenderer.textHeight(rendered, width: width)
            #expect(measured >= bounds.height)
            #expect(measured < bounds.height + 40)
        }
        let item = TranscriptCollectionItem()
        item.configure(block: block, language: .chinese, textSize: .standard,
            accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 420, openURL: { _ in }, activateBlock: { _ in })
        item.view.frame.size = NSSize(width: 420, height: 500)
        item.view.layoutSubtreeIfNeeded()
        #expect(item.view.subviews.isEmpty == false)
        item.prepareForReuse()
        let plain = TranscriptBlock(id: "plain", kind: .output, text: "Text after reuse")
        item.configure(block: plain, language: .chinese, textSize: .standard,
            accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 420, openURL: { _ in }, activateBlock: { _ in })
        func texts(_ view: NSView) -> [NSTextView] {
            (view as? NSTextView).map { [$0] } ?? view.subviews.flatMap(texts)
        }
        let text = try #require(texts(item.view).first)
        #expect(text.string == plain.text)
        #expect(text.textStorage?.attribute(.attachment, at: 0, effectiveRange: nil) == nil)
    }

    @Test func malformedImageDataDoesNotFallBackToPrintingTheEncoding() throws {
        let block = try #require(NativeImageTranscript.project(fragments(source(Data("not an image".utf8)))).first)
        let rendered = TranscriptTextRenderer.attributedText(for: block, textSize: .standard,
            accentColor: .orange, appearance: .dark)
        #expect(rendered.string == "Image preview unavailable")
        #expect(!rendered.string.contains("bm90IGFuIGltYWdl"))
    }

    @Test func nativeUpdatesAndTerminalCorruptionDoNotMasqueradeAsCompletedPictures() throws {
        let started = TranscriptBlock(id: "event-1", kind: .activity,
            text: #"{"type":"item.started","item":{"type":"imageGeneration","id":"call","status":"in_progress"}}"#)
        #expect(NativeImageTranscript.project([started]).first?.activityType == "native_image_loading")
        let reordered = TranscriptBlock(id: "event-2", kind: .activity,
            text: #"{"item":{"id":"call","type":"imageGeneration","result":"broken","status":"completed"},"type":"item.completed"}"#)
        #expect(NativeImageTranscript.project([reordered]).first?.text == "Image preview unavailable")
        let completed = TranscriptBlock(id: "event-3", kind: .activity,
            text: try source(png(), id: "call"))
        let stages = NativeImageTranscript.project([started, completed, started])
        #expect(stages.count == 1)
        #expect(stages.first?.id == "event-1")
        #expect(stages.first?.image != nil)
        let broken = TranscriptBlock(id: "event-3", kind: .activity,
            text: #"{"type":"item.completed","item":{"type":"imageGeneration","result":"broken}}"#)
        #expect(NativeImageTranscript.project([broken]).first?.activityType == "native_image")
        let tall = TranscriptBlock(id: "image", kind: .output, text: "Image preview unavailable",
            activityType: "native_image", image: TranscriptImageContent(sourceID: "tall", data: try png(width: 4, height: 5000)))
        let rendered = TranscriptTextRenderer.attributedText(for: tall, textSize: .standard, accentColor: .orange, appearance: .dark)
        let attachment = try #require(rendered.attribute(.attachment, at: 0, effectiveRange: nil) as? NSTextAttachment)
        let bounds = attachment.attachmentBounds(for: NSTextContainer(size: NSSize(width: 400, height: 1000)),
            proposedLineFragment: NSRect(x: 0, y: 0, width: 400, height: 1000), glyphPosition: .zero, characterIndex: 0)
        #expect(bounds.height <= 540)
    }

    @Test func currentNativeImageCanBeRenderedOffscreenWithoutOpeningAProductionWindow() throws {
        guard let source = ProcessInfo.processInfo.environment["PCR_INLINE_IMAGE_FIXTURE"],
              let folder = ProcessInfo.processInfo.environment["PCR_INLINE_IMAGE_EVIDENCE"] else { return }
        let data = try Data(contentsOf: URL(fileURLWithPath: source))
        let raw = try self.source(data)
        let blocks = fragments(raw)
        let single = try #require(NativeImageTranscript.project([
            TranscriptBlock(id: "event-2000", kind: .activity, text: raw, activityType: "native_image_source")
        ]).first)
        #expect(single.image?.data == data)
        var loaded: [TranscriptBlock] = []
        for chunk in stride(from: 0, to: blocks.count, by: 2) {
            let items = blocks[chunk..<min(blocks.count, chunk + 2)].enumerated().map { offset, block in
                RemoteTranscriptItem(id: block.id, eventId: 10 + chunk + offset, kind: "activity", text: block.text,
                    status: nil, operationId: nil, activityType: nil)
            }
            TranscriptFormatter.append(items: items, to: &loaded)
            let visible = NativeImageTranscript.project(loaded)
            #expect(visible.count == 1)
            #expect(visible.first?.id == "event-10")
        }
        #expect(loaded.count == blocks.count)
        #expect(NativeImageTranscript.project(loaded).first?.image == single.image)
        let start = Date()
        for _ in 0..<20 { #expect(NativeImageTranscript.project(blocks).first?.image?.data == data) }
        print("native_image_projection_20_replays_ms=\(Date().timeIntervalSince(start) * 1000)")
        let block = try #require(NativeImageTranscript.project(blocks).first)
        #expect(block.image?.data == data)
        for width: CGFloat in [460, 1000] {
            let item = TranscriptCollectionItem()
            let height = TranscriptCollectionItem.preferredHeight(for: block, language: .chinese,
                textSize: .standard, accentColor: .orange, appearance: .dark, availableWidth: width)
            item.configure(block: block, language: .chinese, textSize: .standard, accentColor: .orange,
                appearance: .dark, findQuery: "", currentFindOccurrence: nil, availableWidth: width,
                openURL: { _ in }, activateBlock: { _ in })
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = item.view
            defer { window.contentView = nil; window.close() }
            item.view.layoutSubtreeIfNeeded()
            let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
            item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
            var white = 0, samples = 0
            for y in stride(from: 0, to: bitmap.pixelsHigh, by: 20) {
                for x in stride(from: 0, to: bitmap.pixelsWide, by: 20) {
                    samples += 1
                    if let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB),
                       color.redComponent > 0.8, color.greenComponent > 0.8, color.blueComponent > 0.8 { white += 1 }
                }
            }
            #expect(white > samples / 3)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            try png.write(to: URL(fileURLWithPath: folder).appendingPathComponent("native-image-\(Int(width)).png"))
        }
    }
}
