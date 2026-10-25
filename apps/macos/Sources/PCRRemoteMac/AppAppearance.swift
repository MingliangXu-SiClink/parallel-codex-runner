import AppKit
import SwiftUI

enum AppColorMode: String, CaseIterable, Identifiable, Sendable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .system: return language.text("跟随系统", "System")
        case .light: return language.text("浅色", "Light")
        case .dark: return language.text("深色", "Dark")
        }
    }
}

enum TranscriptAppearance: String, Equatable, Sendable {
    case light
    case dark

    init(_ colorScheme: ColorScheme) {
        self = colorScheme == .dark ? .dark : .light
    }

    var nsAppearance: NSAppearance {
        NSAppearance(named: self == .dark ? .darkAqua : .aqua)!
    }
}

enum AppAccentColor: String, CaseIterable, Identifiable, Sendable {
    case blue
    case teal
    case green
    case orange
    case pink

    var id: String { rawValue }

    var color: Color {
        switch self {
        case .blue: return Color(nsColor: .systemBlue)
        case .teal: return Color(nsColor: .systemTeal)
        case .green: return Color(nsColor: .systemGreen)
        case .orange: return Color(nsColor: .systemOrange)
        case .pink: return Color(nsColor: .systemPink)
        }
    }

    var nsColor: NSColor {
        switch self {
        case .blue: return .systemBlue
        case .teal: return .systemTeal
        case .green: return .systemGreen
        case .orange: return .systemOrange
        case .pink: return .systemPink
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .blue: return language.text("蓝色", "Blue")
        case .teal: return language.text("青色", "Teal")
        case .green: return language.text("绿色", "Green")
        case .orange: return language.text("橙色", "Orange")
        case .pink: return language.text("粉色", "Pink")
        }
    }
}

private struct AppAccentColorEnvironmentKey: EnvironmentKey {
    static let defaultValue = AppAccentColor.blue
}

extension EnvironmentValues {
    var appAccentColor: AppAccentColor {
        get { self[AppAccentColorEnvironmentKey.self] }
        set { self[AppAccentColorEnvironmentKey.self] = newValue }
    }
}

private struct AppTextSizeEnvironmentKey: EnvironmentKey {
    static let defaultValue = AppTextSize.standard
}

extension EnvironmentValues {
    var appTextSize: AppTextSize {
        get { self[AppTextSizeEnvironmentKey.self] }
        set { self[AppTextSizeEnvironmentKey.self] = newValue }
    }
}

enum AppTextSize: String, CaseIterable, Identifiable, Sendable {
    case small
    case standard
    case large
    case extraLarge

    var id: String { rawValue }

    var dynamicTypeSize: DynamicTypeSize {
        switch self {
        case .small: return .small
        case .standard: return .large
        case .large: return .xxLarge
        case .extraLarge: return .accessibility1
        }
    }

    var scale: CGFloat {
        switch self {
        case .small: return 0.90
        case .standard: return 1
        case .large: return 1.20
        case .extraLarge: return 1.40
        }
    }

    var transcriptScale: CGFloat { scale }

    var controlSize: ControlSize {
        switch self {
        case .small:
            return .small
        case .standard:
            return .regular
        case .large, .extraLarge:
            return .large
        }
    }

    func title(language: AppLanguage) -> String {
        switch self {
        case .small: return language.text("较小", "Small")
        case .standard: return language.text("标准", "Standard")
        case .large: return language.text("较大", "Large")
        case .extraLarge: return language.text("特大", "Extra Large")
        }
    }

    var larger: AppTextSize? {
        switch self {
        case .small: return .standard
        case .standard: return .large
        case .large: return .extraLarge
        case .extraLarge: return nil
        }
    }

    var smaller: AppTextSize? {
        switch self {
        case .small: return nil
        case .standard: return .small
        case .large: return .standard
        case .extraLarge: return .large
        }
    }
}

enum AppScaledTextStyle {
    case title2
    case title3
    case headline
    case body
    case callout
    case subheadline
    case caption
    case caption2

    var size: CGFloat {
        switch self {
        case .title2: return 20
        case .title3: return 17
        case .headline: return 13
        case .body: return 13
        case .callout: return 12
        case .subheadline: return 12
        case .caption: return 10
        case .caption2: return 9
        }
    }

    var weight: Font.Weight {
        switch self {
        case .headline: return .semibold
        default: return .regular
        }
    }
}

private struct AppScaledSystemFontModifier: ViewModifier {
    @Environment(\.appTextSize) private var textSize

    let size: CGFloat
    let weight: Font.Weight
    let design: Font.Design

    func body(content: Content) -> some View {
        content.font(
            .system(
                size: size * textSize.scale,
                weight: weight,
                design: design
            )
        )
    }
}

extension View {
    func appScaledSystemFont(
        size: CGFloat,
        weight: Font.Weight = .regular,
        design: Font.Design = .default
    ) -> some View {
        modifier(
            AppScaledSystemFontModifier(
                size: size,
                weight: weight,
                design: design
            )
        )
    }

    func appScaledTextStyle(
        _ style: AppScaledTextStyle,
        weight: Font.Weight? = nil,
        design: Font.Design = .default
    ) -> some View {
        appScaledSystemFont(
            size: style.size,
            weight: weight ?? style.weight,
            design: design
        )
    }
}

enum AppAppearanceStore {
    static func loadColorMode() -> AppColorMode {
        load(AppColorMode.self, key: colorModeKey, fallback: .system)
    }

    static func loadAccentColor() -> AppAccentColor {
        load(AppAccentColor.self, key: accentColorKey, fallback: .blue)
    }

    static func loadTextSize() -> AppTextSize {
        load(AppTextSize.self, key: textSizeKey, fallback: .standard)
    }

    static func loadWorkExpandedByDefault() -> Bool {
        loadBool(key: workExpandedByDefaultKey, fallback: false)
    }

    static func loadCompletionNotificationsEnabled() -> Bool {
        loadBool(key: completionNotificationsKey, fallback: true)
    }

    static func save(_ value: AppColorMode) {
        UserDefaults.standard.set(value.rawValue, forKey: colorModeKey)
    }

    static func save(_ value: AppAccentColor) {
        UserDefaults.standard.set(value.rawValue, forKey: accentColorKey)
    }

    static func save(_ value: AppTextSize) {
        UserDefaults.standard.set(value.rawValue, forKey: textSizeKey)
    }

    static func saveWorkExpandedByDefault(_ value: Bool) {
        UserDefaults.standard.set(value, forKey: workExpandedByDefaultKey)
    }

    static func saveCompletionNotificationsEnabled(_ value: Bool) {
        UserDefaults.standard.set(value, forKey: completionNotificationsKey)
    }

    private static func load<Value: RawRepresentable>(
        _ type: Value.Type,
        key: String,
        fallback: Value
    ) -> Value where Value.RawValue == String {
        guard let rawValue = UserDefaults.standard.string(forKey: key),
              let value = Value(rawValue: rawValue) else { return fallback }
        return value
    }

    private static func loadBool(key: String, fallback: Bool) -> Bool {
        guard UserDefaults.standard.object(forKey: key) != nil else { return fallback }
        return UserDefaults.standard.bool(forKey: key)
    }

    private static let colorModeKey = "ParallelCodex.AppColorMode"
    private static let accentColorKey = "ParallelCodex.AppAccentColor"
    private static let textSizeKey = "ParallelCodex.AppTextSize"
    private static let workExpandedByDefaultKey = "ParallelCodex.WorkExpandedByDefault"
    private static let completionNotificationsKey = "ParallelCodex.CompletionNotifications"
}
