import AppKit

@MainActor
class TranscriptViewportScrollView: NSScrollView {
    private var viewportUpdateDepth = 0
    private var needsScrollerReflection = false

    final func beginViewportUpdate() {
        viewportUpdateDepth += 1
    }

    final func endViewportUpdate() {
        precondition(viewportUpdateDepth > 0)
        viewportUpdateDepth -= 1
        guard viewportUpdateDepth == 0, needsScrollerReflection else { return }
        needsScrollerReflection = false
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0
            context.allowsImplicitAnimation = false
            super.reflectScrolledClipView(contentView)
        }
        CATransaction.commit()
    }

    override func reflectScrolledClipView(_ clipView: NSClipView) {
        // AppKit reflects a resized document before its reading anchor is
        // restored. Native overlay scrollers must consume the committed pair,
        // not animate that intermediate extent/offset independently of text.
        if viewportUpdateDepth > 0, clipView === contentView {
            needsScrollerReflection = true
            return
        }
        super.reflectScrolledClipView(clipView)
    }
}
