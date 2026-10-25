# HighlighterSwift 3.1.0

Source: https://github.com/smittytone/HighlighterSwift
Revision: fe7aae9c9b31d3b296fd3d2dd575e1a207bb29e0
License: LICENCE.md (MIT); highlight.js license is Sources/Assets/LICENCE.

The only source patch resolves resources from a macOS application's
Contents/Resources before using SwiftPM's development resource accessor.
Installed applications never fall back to a path on the build machine.
Keep this behavior when updating the dependency.
