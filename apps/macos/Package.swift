// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "ParallelCodexMac",
    defaultLocalization: "en",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "ParallelCodex", targets: ["PCRRemoteMac"])
    ],
    dependencies: [
        .package(path: "Vendor/HighlighterSwift"),
        .package(
            url: "https://github.com/gonzalezreal/swift-markdown-ui",
            from: "2.4.1"
        ),
        .package(path: "Vendor/SwiftMath")
    ],
    targets: [
        .executableTarget(
            name: "PCRRemoteMac",
            dependencies: [
                .product(name: "Highlighter", package: "HighlighterSwift"),
                .product(name: "MarkdownUI", package: "swift-markdown-ui"),
                .product(name: "SwiftMath", package: "SwiftMath")
            ],
            path: "Sources/PCRRemoteMac",
            resources: [.process("Resources/en.lproj"), .process("Resources/zh-Hans.lproj"),
                        .process("Resources/Highlighter-LICENSE.txt"), .process("Resources/Lucide-LICENSE.txt"),
                        .copy("Resources/FilePreview")]
        ),
        .testTarget(
            name: "TranscriptLayoutTests",
            dependencies: ["PCRRemoteMac"],
            path: "Tests/TranscriptLayout"
        ),
        .testTarget(
            name: "ClientTransportTests",
            dependencies: ["PCRRemoteMac"],
            path: "Tests/ClientTransport"
        ),
        .testTarget(
            name: "EditorInteractionTests",
            dependencies: ["PCRRemoteMac"],
            path: "Tests/EditorInteraction"
        ),
    ]
)
