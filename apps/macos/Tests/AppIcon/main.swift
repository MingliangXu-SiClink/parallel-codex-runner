import AppKit
import ImageIO

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else {
        fputs("App icon regression: \(message)\n", stderr)
        exit(1)
    }
}

let root = URL(fileURLWithPath: CommandLine.arguments[1])
let appPlist = try PropertyListSerialization.propertyList(
    from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])),
    options: [], format: nil
) as! [String: Any]
let iconFile = appPlist["CFBundleIconFile"] as! String
check(iconFile == "ParallelCodex.icns", "the bundle must use the refreshed icon resource")
let slots = try FileManager.default.contentsOfDirectory(
    at: root.appendingPathComponent("AppIcon.iconset"), includingPropertiesForKeys: nil
)
check(slots.count == 8, "all eight PNG-backed icon slots must be generated")
for slot in slots {
    let name = slot.deletingPathExtension().lastPathComponent
    let pointSize = Int(name.split(separator: "_")[1].split(separator: "x")[0])!
    let pixels = pointSize * (name.contains("@2x") ? 2 : 1)
    let bitmap = NSBitmapImageRep(data: try Data(contentsOf: slot))!
    check(bitmap.pixelsWide == pixels && bitmap.pixelsHigh == pixels,
          "\(name) must preserve its declared pixel size")
    for fraction in [0.0, 0.5, 0.999] {
        let p = min(pixels - 1, Int(Double(pixels) * fraction))
        for (x, y) in [(p, 0), (p, pixels - 1), (0, p), (pixels - 1, p)] {
            check(bitmap.colorAt(x: x, y: y)!.alphaComponent == 0,
                  "\(name) must keep the canvas perimeter fully transparent")
        }
    }
    check(bitmap.colorAt(x: pixels / 2, y: pixels / 2)!.alphaComponent > 0.999,
          "\(name) must have an opaque face, not a translucent glyph")
}

let icns = root.appendingPathComponent("AppIcon.icns")
let source = CGImageSourceCreateWithURL(icns as CFURL, nil)!
let sizes = Set((0..<CGImageSourceGetCount(source)).map {
    CGImageSourceCreateImageAtIndex(source, $0, nil)!.width
})
check(sizes == Set([32, 64, 128, 256, 512, 1024]), "ICNS must contain every PNG raster size")

// Ask the real icon service to render a unique bundle, not just the source PNG.
// A fresh path and identifier avoid mistaking a cached pre-fix icon for this build.
let bundle = root.appendingPathComponent("Icon-\(UUID().uuidString).app")
let contents = bundle.appendingPathComponent("Contents")
try FileManager.default.createDirectory(at: contents.appendingPathComponent("Resources"),
                                        withIntermediateDirectories: true)
try FileManager.default.copyItem(at: icns,
                                 to: contents.appendingPathComponent("Resources/\(iconFile)"))
try FileManager.default.createDirectory(at: contents.appendingPathComponent("MacOS"),
                                        withIntermediateDirectories: true)
try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"),
                                 to: contents.appendingPathComponent("MacOS/IconTest"))
let plist: [String: String] = [
    "CFBundleIdentifier": "com.parallelcodexrunner.icon-test.\(UUID().uuidString)",
    "CFBundleName": "Icon Test", "CFBundlePackageType": "APPL",
    "CFBundleVersion": "1", "CFBundleIconFile": iconFile,
    "CFBundleExecutable": "IconTest", "LSMinimumSystemVersion": "13.0",
]
try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
    .write(to: contents.appendingPathComponent("Info.plist"))
let icon = NSWorkspace.shared.icon(forFile: bundle.path)
for pixels in [1024, 512, 256, 128, 64, 32, 16] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                  isPlanar: false, colorSpaceName: .deviceRGB,
                                  bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    icon.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels),
              from: .zero, operation: .copy, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    try bitmap.representation(using: .png, properties: [:])!
        .write(to: root.appendingPathComponent("native-\(pixels).png"))
    var outerColors: [NSColor] = []
    for (x, y) in [(0.5, 0.15), (0.15, 0.5), (0.85, 0.5), (0.5, 0.85)] {
        let color = bitmap.colorAt(x: Int(Double(pixels) * x), y: Int(Double(pixels) * y))!
            .usingColorSpace(.sRGB)!
        check(color.alphaComponent > 0.95, "system icon must have a visible dark tile")
        outerColors.append(color)
    }
    check(outerColors.allSatisfy { max($0.redComponent, $0.greenComponent, $0.blueComponent) < 0.6 },
          "\(pixels) px system icon must not acquire a white backing tile: \(outerColors)")
}
print("App icon tests passed: 8 PNG slots, ICNS sizes, and 7 native rendering sizes (16...1024 px).")
