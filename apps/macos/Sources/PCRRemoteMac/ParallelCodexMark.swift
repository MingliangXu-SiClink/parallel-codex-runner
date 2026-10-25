import AppKit
import SwiftUI

struct ParallelCodexMark: View {
    var tiled = false

    var body: some View {
        Group {
            if let image = Self.brandImage {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
            } else {
                Image(systemName: "arrow.triangle.branch")
                    .resizable()
                    .scaledToFit()
                    .padding(12)
                    .foregroundStyle(.tint)
            }
        }
        .shadow(
            color: tiled ? .black.opacity(0.10) : .clear,
            radius: tiled ? 7 : 0,
            y: tiled ? 3 : 0
        )
        .accessibilityHidden(true)
    }

    private static let brandImage: NSImage? = {
        guard let url = Bundle.main.url(
            forResource: "parallel-codex-icon-1024",
            withExtension: "png"
        ) else { return nil }
        return NSImage(contentsOf: url)
    }()
}
