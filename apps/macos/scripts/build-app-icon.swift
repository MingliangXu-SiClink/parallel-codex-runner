import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

func fail(_ message: String) throws -> Never {
    throw NSError(domain: "AppIcon", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
}

func buildIcon(source: URL, output: URL) throws {
    guard let imageSource = CGImageSourceCreateWithURL(source as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil),
          image.width == 1024, image.height == 1024,
          let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else {
        try fail("The canonical app icon must be a readable 1024 x 1024 image.")
    }
    let bitmapInfo = CGBitmapInfo.byteOrder32Big.rawValue
        | CGImageAlphaInfo.premultipliedLast.rawValue
    guard let master = CGContext(data: nil, width: 1024, height: 1024,
                                 bitsPerComponent: 8, bytesPerRow: 4096,
                                 space: colorSpace, bitmapInfo: bitmapInfo),
          let data = master.data?.assumingMemoryBound(to: UInt8.self) else {
        try fail("Cannot allocate the macOS icon bitmap.")
    }
    master.draw(image, in: CGRect(x: 0, y: 0, width: 1024, height: 1024))

    // The shared artwork has a nearly opaque face and faint alpha noise outside it.
    // Tahoe treats that as a floating glyph and adds a white tile. Normalize alpha
    // without changing unpremultiplied RGB; keep the shared/Android artwork intact.
    for pixel in 0..<(1024 * 1024) {
        let offset = pixel * 4
        let alpha = Int(data[offset + 3])
        let normalized = alpha < 8 ? 0 : min(255, alpha * 255 / 250)
        for channel in 0..<3 {
            data[offset + channel] = alpha == 0 ? 0
                : UInt8(min(255, Int(data[offset + channel]) * normalized / alpha))
        }
        data[offset + 3] = UInt8(normalized)
    }
    guard let normalizedImage = master.makeImage() else {
        try fail("Cannot render the macOS icon master.")
    }
    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    // iconutil encodes the 16/32 px 1x slots as legacy ARGB (ic04/ic05), which
    // Tahoe wraps in a backing tile even when the PNG representations are valid.
    // Keep their PNG Retina slots; Icon Services downsamples them on 1x displays.
    let slots: [(Int, String)] = [
        (32, "icon_16x16@2x.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for (pixels, filename) in slots {
        guard let context = CGContext(data: nil, width: pixels, height: pixels,
                                      bitsPerComponent: 8, bytesPerRow: pixels * 4,
                                      space: colorSpace, bitmapInfo: bitmapInfo) else {
            try fail("Cannot allocate the \(pixels) px icon.")
        }
        context.interpolationQuality = .high
        // The artwork's existing inset plus this canvas gutter places its dark
        // rounded tile at approximately 100...924 on the 1024 px macOS grid.
        let side = CGFloat(pixels) * 0.875
        let inset = (CGFloat(pixels) - side) / 2
        context.draw(normalizedImage, in: CGRect(x: inset, y: inset, width: side, height: side))
        guard let bitmap = context.makeImage(),
              let destination = CGImageDestinationCreateWithURL(
                output.appendingPathComponent(filename) as CFURL,
                UTType.png.identifier as CFString, 1, nil
              ) else {
            try fail("Cannot write \(filename).")
        }
        CGImageDestinationAddImage(destination, bitmap, nil)
        guard CGImageDestinationFinalize(destination) else {
            try fail("Cannot finish writing \(filename).")
        }
    }
}

do {
    guard CommandLine.arguments.count == 3 else {
        try fail("Usage: build-app-icon.swift SOURCE.png OUTPUT.iconset")
    }
    try buildIcon(source: URL(fileURLWithPath: CommandLine.arguments[1]),
                  output: URL(fileURLWithPath: CommandLine.arguments[2]))
} catch {
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}
