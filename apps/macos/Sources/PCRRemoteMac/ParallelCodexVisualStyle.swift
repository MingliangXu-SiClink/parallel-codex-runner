import AppKit
import SwiftUI

enum ParallelCodexVisualStyle {
    // The UI follows a 4-point grid. Components use semantic roles instead of
    // inventing local values, so sidebar, conversation and inspector changes
    // continue to read as one product.
    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 6
        static let small: CGFloat = 8
        static let medium: CGFloat = 12
        static let large: CGFloat = 16
        static let xlarge: CGFloat = 24
        static let xxlarge: CGFloat = 32
    }

    enum Radius {
        static let selection: CGFloat = 7
        static let panel: CGFloat = 8
        static let tool: CGFloat = 10
        static let message: CGFloat = 16
        static let composer: CGFloat = 22
    }

    enum Control {
        static let compact: CGFloat = 28
        static let standard: CGFloat = 30
        static let toolbarHeight: CGFloat = 50
        static let sidebarIcon: CGFloat = 26
        static let statusDot: CGFloat = 6
    }

    enum Layout {
        static let sidebarMinimum: CGFloat = 228
        static let sidebarIdeal: CGFloat = 286
        static let sidebarMaximum: CGFloat = 380
        static let transcriptNarrowInset: CGFloat = 16
        static let transcriptInset: CGFloat = 28
        static let transcriptMaximumWidth: CGFloat = 2000
        static let workProcessIndent: CGFloat = 32
        static let composerMaximumWidth: CGFloat = 1_040
        static let userMessageMaximumWidth: CGFloat = 820
        static let inspectorBreakpoint: CGFloat = 980
        static let inspectorMinimum: CGFloat = 320
        static let inspectorIdeal: CGFloat = 360
        static let inspectorMaximum: CGFloat = 400
    }

    enum Composer {
        static let minimumEditorHeight: CGFloat = 40
        static let maximumEditorHeight: CGFloat = 200
        static let fontSize: CGFloat = 15
        static let controlSize: CGFloat = 28
    }

    enum Typography {
        static let body: CGFloat = 16
        static let work: CGFloat = 13
        static let title: CGFloat = 17
        static let sidebar: CGFloat = 14
        static let section: CGFloat = 13
        static let caption: CGFloat = 12
        static let bodyLineSpacing: CGFloat = 5
        static let paragraphSpacing: CGFloat = 12
    }

    enum Motion {
        static let quick: Double = 0.16
        static let standard: Double = 0.22
    }

    static var canvas: Color {
        Color(nsColor: canvasNSColor)
    }

    static var canvasNSColor: NSColor {
        adaptive(
            light: gray(255),
            dark: gray(33)
        )
    }

    static var sidebar: Color {
        Color(nsColor: adaptive(
            light: gray(249),
            dark: gray(40)
        ))
    }

    static var surface: Color {
        Color(nsColor: adaptive(
            light: gray(255),
            dark: gray(55)
        ))
    }

    static var subtleSurface: Color {
        Color(nsColor: adaptive(
            light: gray(245),
            dark: gray(43)
        ))
    }

    static var sidebarSelection: Color {
        Color(nsColor: adaptive(
            light: gray(234),
            dark: gray(58)
        ))
    }

    static var sidebarHover: Color {
        Color.primary.opacity(0.055)
    }

    static var composerStroke: Color {
        Color(nsColor: adaptive(light: gray(0, alpha: 0.055), dark: gray(255, alpha: 0.055)))
    }

    static var composerFocusStroke: Color {
        Color(nsColor: adaptive(light: gray(0, alpha: 0.16), dark: gray(255, alpha: 0.14)))
    }

    static var composerSurface: Color {
        Color(nsColor: adaptive(
            light: gray(244),
            dark: gray(52)
        ))
    }

    static var composerToolSurface: Color {
        Color(nsColor: adaptive(
            light: gray(231),
            dark: gray(62)
        ))
    }

    static var floatingControlSurface: Color {
        Color(nsColor: adaptive(
            light: NSColor(calibratedWhite: 1, alpha: 0.98),
            dark: NSColor(calibratedWhite: 0.24, alpha: 0.98)
        ))
    }

    static var floatingControlStroke: Color {
        Color(nsColor: adaptive(
            light: NSColor(calibratedWhite: 0.12, alpha: 0.16),
            dark: NSColor(calibratedWhite: 1, alpha: 0.20)
        ))
    }

    static var separator: Color {
        Color(nsColor: separatorNSColor)
    }

    static var primaryText: Color { Color(nsColor: primaryTextNSColor) }
    static var secondaryText: Color { Color(nsColor: secondaryTextNSColor) }

    static var primaryTextNSColor: NSColor {
        adaptive(light: gray(36), dark: gray(230))
    }

    static var secondaryTextNSColor: NSColor {
        adaptive(light: gray(100), dark: gray(168))
    }

    static var placeholderTextNSColor: NSColor {
        adaptive(light: gray(110), dark: gray(162))
    }

    static func selectedSurface(accent: Color = .accentColor) -> Color {
        accent.opacity(0.13)
    }

    static var assistantSurfaceNSColor: NSColor {
        .clear
    }

    static var userSurfaceNSColor: NSColor {
        adaptive(
            light: gray(244),
            dark: gray(48)
        )
    }

    static var toolSurfaceNSColor: NSColor {
        adaptive(
            light: gray(247),
            dark: gray(38)
        )
    }

    static var reasoningTextNSColor: NSColor {
        secondaryTextNSColor
    }

    static var separatorNSColor: NSColor {
        adaptive(light: gray(0, alpha: 0.09), dark: gray(255, alpha: 0.09))
    }

    static var tableBorderNSColor: NSColor {
        adaptive(light: gray(0, alpha: 0.18), dark: gray(255, alpha: 0.18))
    }

    static func appearancePreservingAlpha(
        _ color: NSColor,
        alpha: CGFloat
    ) -> NSColor {
        NSColor(name: nil) { appearance in
            var resolved = color
            appearance.performAsCurrentDrawingAppearance {
                resolved = color.usingColorSpace(.deviceRGB) ?? color
            }
            return resolved.withAlphaComponent(alpha)
        }
    }

    static func resolvedNSColor(
        _ color: NSColor,
        for appearance: TranscriptAppearance
    ) -> NSColor {
        var resolved = color
        appearance.nsAppearance.performAsCurrentDrawingAppearance {
            resolved = color.usingColorSpace(.deviceRGB) ?? color
        }
        return resolved
    }

    private static func adaptive(light: NSColor, dark: NSColor) -> NSColor {
        NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? dark
                : light
        }
    }

    private static func gray(_ value: CGFloat, alpha: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: value / 255, green: value / 255, blue: value / 255, alpha: alpha)
    }
}

struct ParallelCodexStatusDot: View {
    let color: Color
    var size = ParallelCodexVisualStyle.Control.statusDot
    var isPulsing = false

    var body: some View {
        ExecutionActivityIndicator(isWorking: isPulsing, color: color, diameterFraction: 1)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

struct ParallelCodexIconButtonStyle: ButtonStyle {
    var size = ParallelCodexVisualStyle.Control.standard

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(width: size, height: size)
            .contentShape(Circle())
            .background(
                configuration.isPressed
                    ? Color.primary.opacity(0.08)
                    : Color.clear,
                in: Circle()
            )
            .opacity(configuration.isPressed ? 0.82 : 1)
    }
}

private struct ParallelCodexSidebarRowModifier: ViewModifier {
    let selected: Bool
    @State private var hovering = false

    func body(content: Content) -> some View {
        content.background(
            selected
                ? ParallelCodexVisualStyle.sidebarSelection
                : (hovering
                    ? ParallelCodexVisualStyle.sidebarHover
                    : Color.clear),
            in: RoundedRectangle(
                cornerRadius: ParallelCodexVisualStyle.Radius.selection,
                style: .continuous
            )
        )
        .onHover { hovering = $0 }
    }
}

extension View {
    func parallelCodexSidebarRow(selected: Bool) -> some View {
        modifier(ParallelCodexSidebarRowModifier(
            selected: selected
        ))
    }
}
