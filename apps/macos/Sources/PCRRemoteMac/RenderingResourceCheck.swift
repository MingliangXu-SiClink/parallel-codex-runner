import AppKit

enum RenderingResourceCheck {
    @MainActor
    static func failures() async -> [String] {
        _ = NSApplication.shared
        if Bundle.main.bundleURL.pathExtension == "app" {
            for name in ["SwiftMath_SwiftMath", "Highlighter_Highlighter"] {
                guard let url = Bundle.main.url(forResource: name, withExtension: "bundle"),
                      let resources = Bundle.main.resourceURL,
                      url.path.hasPrefix(resources.path + "/") else {
                    return ["Missing packaged resource: \(name)"]
                }
            }
        }
        let math = TranscriptTextRenderer.attributedText(
            for: TranscriptBlock(id: "resource-check", kind: .output,
                text: #"Integral $\int_0^1 x^2 dx = \frac{1}{3}$ and $\pi_0$."#),
            textSize: .standard, accentColor: .orange, appearance: .dark)
        var equations = 0
        math.enumerateAttribute(.attachment, in: NSRange(location: 0, length: math.length)) { value, _, _ in
            if let attachment = value as? NSTextAttachment,
               let image = attachment.image, image.size.width > 0, image.size.height > 0 {
                equations += 1
            }
        }
        var result = equations == 2 ? [] : ["Math attachments did not render."]
        if let shell = FilePreviewSession.shellURL {
            if Bundle.main.bundleURL.pathExtension == "app",
               let resources = Bundle.main.resourceURL,
               !shell.path.hasPrefix(resources.path + "/") {
                result.append("Document preview did not use packaged resources.")
            }
            for name in ["preview.js", "preview.css", "docx-preview.min.js", "jszip.min.js"] {
                if !FileManager.default.fileExists(atPath: shell.deletingLastPathComponent().appendingPathComponent(name).path) {
                    result.append("Missing packaged preview resource: \(name)")
                }
            }
        } else { result.append("Missing packaged document preview.") }
        let source = "let value = 42\n// Packaged syntax resources\nprint(\"ready\")\n"
        for dark in [true, false] {
            let styled = await ReviewSyntaxRenderer.shared.render(ReviewSyntaxRequest(
                text: source, path: "check.swift", isDiff: false, fontSize: 14, dark: dark)).value
            var colors = Set<String>()
            styled.enumerateAttribute(.foregroundColor, in: NSRange(location: 0, length: styled.length)) { value, _, _ in
                if let color = (value as? NSColor)?.usingColorSpace(.deviceRGB) {
                    colors.insert("\(color.redComponent),\(color.greenComponent),\(color.blueComponent)")
                }
            }
            if styled.string != source || colors.count < 2 {
                result.append("Syntax highlighting did not render (dark: \(dark)).")
            }
        }
        return result
    }
}
