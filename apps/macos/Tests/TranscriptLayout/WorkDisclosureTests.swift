import AppKit
import Testing
@testable import PCRRemoteMac

@Suite(.serialized)
@MainActor
struct WorkDisclosureTests {
    @Test
    func chevronLabelGapAndVerticalPaddingShareOneNativeButton() throws {
        let window = makeWindow()
        defer { window.close() }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        var activations = 0
        for appearance in [TranscriptAppearance.light, .dark] {
            for size in AppTextSize.allCases {
                for width: CGFloat in [320, 920, 1200] {
                    for expanded in [false, true] {
                        configure(item, width: width, size: size, appearance: appearance,
                            expanded: expanded) { _ in activations += 1 }
                        let button = try disclosureButton(in: item.view)
                        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                        let icon = try #require(item.view.subviews.compactMap { $0 as? NSImageView }.first)
                        let textFrame = text.convert(text.bounds, to: item.view)
                        let points = [
                            NSPoint(x: icon.frame.midX, y: icon.frame.midY),
                            NSPoint(x: (icon.frame.maxX + textFrame.minX) / 2, y: icon.frame.midY),
                            NSPoint(x: textFrame.minX + 8, y: textFrame.midY),
                            NSPoint(x: button.frame.midX, y: button.frame.minY + 2),
                            NSPoint(x: button.frame.midX, y: button.frame.maxY - 2),
                        ]
                        #expect(button.frame.height >= 36)
                        #expect(button.frame.minX >= 0 && button.frame.maxX <= width)
                        #expect(button.isAccessibilityExpanded() == expanded)
                        #expect(button.accessibilityLabel() == "工作过程 · 2 条思考 · 8 条命令")
                        #expect(button.toolTip == (expanded ? "收起工作过程" : "展开工作过程"))
                        for point in points {
                            let hit = item.view.hitTest(item.view.convert(point, to: item.view.superview))
                            #expect(hit === button, "\(point) missed the disclosure at \(width), \(size)")
                        }
                        let oldMask = NSEvent.EventTypeMask(rawValue: UInt64(button.sendAction(on: .leftMouseUp)))
                        #expect(oldMask.contains(.leftMouseUp))
                        #expect(!oldMask.contains(.leftMouseDown))
                        let before = activations
                        #expect(button.accessibilityPerformPress())
                        #expect(activations == before + 1)
                    }
                }
            }
        }
    }

    @Test
    func keyboardFocusAndActivationSurviveRepeatedDisclosureUpdates() throws {
        let window = makeWindow()
        defer { window.close() }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        var activations = 0
        configure(item) { _ in activations += 1 }
        let button = try disclosureButton(in: item.view)
        #expect(window.makeFirstResponder(button))
        for index in 0..<20 {
            configure(item, expanded: index.isMultiple(of: 2)) { _ in activations += 1 }
            #expect(try disclosureButton(in: item.view) === button)
            #expect(window.firstResponder === button)
            let character = index.isMultiple(of: 2) ? "\r" : " "
            let event = try #require(NSEvent.keyEvent(with: .keyDown, location: .zero,
                modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber, context: nil,
                characters: character, charactersIgnoringModifiers: character,
                isARepeat: false, keyCode: index.isMultiple(of: 2) ? 36 : 49))
            button.keyDown(with: event)
            #expect(activations == index + 1)
        }
    }

    @Test
    func reusingDisclosureAsAReplyRestoresTextSelectionAndRemovesItsHitTarget() throws {
        let window = makeWindow()
        defer { window.close() }
        let item = TranscriptCollectionItem()
        window.contentView?.addSubview(item.view)
        var activations = 0
        configure(item) { _ in activations += 1 }
        let button = try disclosureButton(in: item.view)
        let formerTarget = NSPoint(x: button.frame.midX, y: button.frame.midY)
        item.prepareForReuse()
        let reply = TranscriptBlock(id: "reply", kind: .output, text: "The answer remains selectable.")
        item.configure(block: reply, language: .chinese, textSize: .standard,
            accentColor: .orange, appearance: .dark, findQuery: "", currentFindOccurrence: nil,
            availableWidth: 920, openURL: { _ in }, activateBlock: { _ in activations += 1 })
        item.view.layoutSubtreeIfNeeded()
        #expect(button.isHidden)
        #expect(!button.accessibilityPerformPress())
        #expect(item.view.hitTest(item.view.convert(formerTarget, to: item.view.superview)) !== button)
        let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
        #expect(text.isSelectable)
        #expect(!text.isAccessibilityHidden())
        #expect(activations == 0)
    }

    @Test
    func hoverPressedAndKeyboardFocusHaveVisibleNativeFeedback() throws {
        let window = makeWindow()
        defer { window.close() }
        for appearance in [TranscriptAppearance.dark, .light] {
            window.appearance = NSAppearance(named: appearance == .dark ? .darkAqua : .aqua)
            let entered = try #require(NSEvent.enterExitEvent(with: .mouseEntered, location: .zero,
                modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber, context: nil,
                eventNumber: 0, trackingNumber: 0, userData: nil))
            var images: [Data] = []
            var baselineGlyphPixels = 0
            for state in ["normal", "hover", "pressed", "focus"] {
                // A fresh host avoids AppKit reusing layer-backed drawing from
                // an earlier offscreen cacheDisplay pass.
                let item = TranscriptCollectionItem()
                window.contentView?.addSubview(item.view)
                configure(item, width: 440, appearance: appearance) { _ in }
                let button = try disclosureButton(in: item.view)
                if state == "hover" { button.mouseEntered(with: entered) }
                if state == "pressed" { button.highlight(true) }
                if state == "focus" {
                    button.highlight(false)
                    button.mouseExited(with: entered)
                    #expect(window.makeFirstResponder(button))
                }
                for view in [item.view] + descendants(item.view) { view.needsDisplay = true }
                let bitmap = try #require(item.view.bitmapImageRepForCachingDisplay(in: item.view.bounds))
                item.view.cacheDisplay(in: item.view.bounds, to: bitmap)
                let text = try #require(descendants(item.view).compactMap { $0 as? NSTextView }.first)
                let textFrame = text.convert(text.bounds, to: item.view)
                let scale = CGFloat(bitmap.pixelsWide) / item.view.bounds.width
                var glyphPixels = 0
                for x in Int(textFrame.minX * scale)..<Int(textFrame.maxX * scale) {
                    for y in 0..<bitmap.pixelsHigh {
                        if (bitmap.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.25 { glyphPixels += 1 }
                    }
                }
                if state == "normal" { baselineGlyphPixels = glyphPixels }
                #expect(baselineGlyphPixels > 100)
                #expect(glyphPixels >= baselineGlyphPixels * 9 / 10,
                    "\(appearance) \(state) must not hide the label")
                let canvas = try #require(NSBitmapImageRep(bitmapDataPlanes: nil,
                    pixelsWide: bitmap.pixelsWide, pixelsHigh: bitmap.pixelsHigh,
                    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
                let context = try #require(NSGraphicsContext(bitmapImageRep: canvas)?.cgContext)
                let rect = CGRect(x: 0, y: 0, width: bitmap.pixelsWide, height: bitmap.pixelsHigh)
                context.setFillColor(ParallelCodexVisualStyle.resolvedNSColor(
                    ParallelCodexVisualStyle.canvasNSColor, for: appearance).cgColor)
                context.fill(rect)
                context.draw(try #require(bitmap.cgImage), in: rect)
                let png = try #require(canvas.representation(using: .png, properties: [:]))
                images.append(png)
                if let directory = ProcessInfo.processInfo.environment["PCR_QA_ARTIFACT_DIR"] {
                    try png.write(to: URL(fileURLWithPath: directory)
                        .appendingPathComponent("disclosure-\(appearance)-\(state).png"))
                }
                window.makeFirstResponder(window)
                item.view.removeFromSuperview()
            }
            #expect(Set(images).count == 4)
        }
    }

    private func configure(_ item: TranscriptCollectionItem, width: CGFloat = 920,
                           size: AppTextSize = .standard, appearance: TranscriptAppearance = .dark,
                           expanded: Bool = false, activate: @escaping (TranscriptBlock) -> Void) {
        let block = TranscriptBlock(id: "work", kind: .activity,
            text: "工作过程 · 2 条思考 · 8 条命令",
            activityType: expanded ? "work_summary_expanded" : "work_summary")
        item.configure(block: block, language: .chinese, textSize: size,
            accentColor: .orange, appearance: appearance, findQuery: "", currentFindOccurrence: nil,
            availableWidth: width, openURL: { _ in }, activateBlock: activate)
        let height = TranscriptCollectionItem.preferredHeight(for: block, language: .chinese,
            textSize: size, accentColor: .orange, appearance: appearance, availableWidth: width)
        item.view.frame = NSRect(x: 0, y: 100, width: width, height: height)
        item.view.layoutSubtreeIfNeeded()
    }

    private func disclosureButton(in view: NSView) throws -> NSButton {
        try #require(descendants(view).compactMap { $0 as? NSButton }.first {
            $0.accessibilityIdentifier() == "transcript.work-disclosure"
        })
    }

    private func descendants(_ view: NSView) -> [NSView] {
        view.subviews.flatMap { [$0] + descendants($0) }
    }

    private func makeWindow() -> NSWindow {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 800),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }
}
