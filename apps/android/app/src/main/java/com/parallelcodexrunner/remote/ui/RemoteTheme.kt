package com.parallelcodexrunner.remote.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape

internal val SystemBlue = Color(0xFF316BFF)
internal val SystemBlueDark = Color(0xFF76A7FF)
internal val SystemGreen = Color(0xFF16A274)
internal val SystemOrange = Color(0xFFEA8524)
internal val SystemRed = Color(0xFFE5484D)
internal val SystemGray = Color(0xFF717885)
internal val SystemTeal = Color(0xFF148A87)
internal val SystemBackground = Color(0xFFF7F8FA)
internal val SystemSurface = Color(0xFFFFFFFF)
internal val SystemSeparator = Color(0xFFE5E8ED)

private val LightColors = lightColorScheme(
    primary = SystemBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE8EEFF),
    onPrimaryContainer = Color(0xFF17233B),
    inversePrimary = Color(0xFF89ABFF),
    secondary = Color(0xFF5F6772),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFECEFF3),
    onSecondaryContainer = Color(0xFF2E333B),
    tertiary = SystemOrange,
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFFFE8D2),
    onTertiaryContainer = Color(0xFF5A2600),
    background = SystemBackground,
    onBackground = Color(0xFF17191D),
    surface = SystemSurface,
    onSurface = Color(0xFF17191D),
    surfaceVariant = Color(0xFFF1F3F6),
    onSurfaceVariant = Color(0xFF656C77),
    surfaceTint = SystemBlue,
    inverseSurface = Color(0xFF2D3036),
    inverseOnSurface = Color(0xFFF3F4F6),
    outline = SystemSeparator,
    outlineVariant = Color(0xFFD4D8DE),
    error = SystemRed,
    onError = Color.White,
    errorContainer = Color(0xFFFDE8E8),
    onErrorContainer = Color(0xFF6F1014),
    scrim = Color.Black,
    surfaceBright = Color.White,
    surfaceDim = Color(0xFFE1E4E9),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFFAFBFC),
    surfaceContainer = Color(0xFFF4F5F7),
    surfaceContainerHigh = Color(0xFFEFF1F4),
    surfaceContainerHighest = Color(0xFFE8EBEF),
    primaryFixed = Color(0xFFE8EEFF),
    primaryFixedDim = Color(0xFFD5E0FF),
    onPrimaryFixed = Color(0xFF17233B),
    onPrimaryFixedVariant = Color(0xFF405171),
    secondaryFixed = Color(0xFFECEFF3),
    secondaryFixedDim = Color(0xFFDDE1E7),
    onSecondaryFixed = Color(0xFF2E333B),
    onSecondaryFixedVariant = Color(0xFF555D68),
    tertiaryFixed = Color(0xFFFFE8D2),
    tertiaryFixedDim = Color(0xFFFFD5AE),
    onTertiaryFixed = Color(0xFF5A2600),
    onTertiaryFixedVariant = Color(0xFF82420C),
)

private val DarkColors = darkColorScheme(
    primary = SystemBlueDark,
    onPrimary = Color.White,
    primaryContainer = Color(0xFF26395C),
    onPrimaryContainer = Color(0xFFE8EEFF),
    inversePrimary = SystemBlue,
    secondary = Color(0xFFAEAEB2),
    onSecondary = Color(0xFF20242A),
    secondaryContainer = Color(0xFF2A3038),
    onSecondaryContainer = Color(0xFFE5E8ED),
    tertiary = Color(0xFFFF9F0A),
    onTertiary = Color(0xFF301700),
    tertiaryContainer = Color(0xFF4A2A10),
    onTertiaryContainer = Color(0xFFFFE8D2),
    background = Color(0xFF0D1015),
    onBackground = Color(0xFFF4F6F8),
    surface = Color(0xFF171B21),
    onSurface = Color(0xFFF4F6F8),
    surfaceVariant = Color(0xFF232830),
    onSurfaceVariant = Color(0xFFB8BEC7),
    surfaceTint = SystemBlueDark,
    inverseSurface = Color(0xFFE8EBEF),
    inverseOnSurface = Color(0xFF25282D),
    outline = Color(0xFF333943),
    outlineVariant = Color(0xFF414852),
    error = Color(0xFFFF6B66),
    onError = Color(0xFF3A0A0C),
    errorContainer = Color(0xFF4C1D20),
    onErrorContainer = Color(0xFFFFDAD9),
    scrim = Color.Black,
    surfaceBright = Color(0xFF30353D),
    surfaceDim = Color(0xFF0D1015),
    surfaceContainerLowest = Color(0xFF090B0F),
    surfaceContainerLow = Color(0xFF12161B),
    surfaceContainer = Color(0xFF171B21),
    surfaceContainerHigh = Color(0xFF1E232A),
    surfaceContainerHighest = Color(0xFF282E36),
    primaryFixed = Color(0xFFD8E4FF),
    primaryFixedDim = Color(0xFFADC6FF),
    onPrimaryFixed = Color(0xFF0F1D35),
    onPrimaryFixedVariant = Color(0xFF34486C),
    secondaryFixed = Color(0xFFE0E4EA),
    secondaryFixedDim = Color(0xFFC4CAD2),
    onSecondaryFixed = Color(0xFF20242A),
    onSecondaryFixedVariant = Color(0xFF49515C),
    tertiaryFixed = Color(0xFFFFE1C2),
    tertiaryFixedDim = Color(0xFFFFC98F),
    onTertiaryFixed = Color(0xFF321800),
    onTertiaryFixedVariant = Color(0xFF72400F),
)

private fun appColorScheme(dark: Boolean, accent: Color) =
    (if (dark) DarkColors else LightColors).let { base ->
        val primaryContainer = lerp(base.surface, accent, if (dark) 0.24f else 0.12f)
        val primaryFixed = lerp(base.surface, accent, if (dark) 0.34f else 0.12f)
        val primaryFixedDim = lerp(base.surface, accent, if (dark) 0.44f else 0.20f)
        base.copy(
            primary = accent,
            primaryContainer = primaryContainer,
            onPrimaryContainer = base.onSurface,
            inversePrimary = accent,
            surfaceTint = accent,
            primaryFixed = primaryFixed,
            primaryFixedDim = primaryFixedDim,
            onPrimaryFixed = base.onSurface,
            onPrimaryFixedVariant = base.onSurfaceVariant,
        )
    }

private val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small = RoundedCornerShape(6.dp),
    medium = RoundedCornerShape(8.dp),
    large = RoundedCornerShape(8.dp),
    extraLarge = RoundedCornerShape(8.dp),
)

private fun appTypography(scale: Float) = Typography(
    headlineSmall = TextStyle(
        fontSize = (24 * scale).sp,
        lineHeight = (30 * scale).sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.sp,
    ),
    titleLarge = TextStyle(
        fontSize = (20 * scale).sp,
        lineHeight = (26 * scale).sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.sp,
    ),
    titleMedium = TextStyle(
        fontSize = (17 * scale).sp,
        lineHeight = (22 * scale).sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.sp,
    ),
    titleSmall = TextStyle(
        fontSize = (15 * scale).sp,
        lineHeight = (20 * scale).sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.sp,
    ),
    bodyLarge = TextStyle(
        fontSize = (16 * scale).sp,
        lineHeight = (23 * scale).sp,
        letterSpacing = 0.sp,
    ),
    bodyMedium = TextStyle(
        fontSize = (14 * scale).sp,
        lineHeight = (21 * scale).sp,
        letterSpacing = 0.sp,
    ),
    bodySmall = TextStyle(
        fontSize = (12 * scale).sp,
        lineHeight = (17 * scale).sp,
        letterSpacing = 0.sp,
    ),
    labelLarge = TextStyle(
        fontSize = (14 * scale).sp,
        lineHeight = (19 * scale).sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.sp,
    ),
    labelMedium = TextStyle(
        fontSize = (12 * scale).sp,
        lineHeight = (16 * scale).sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.sp,
    ),
    labelSmall = TextStyle(
        fontSize = (11 * scale).sp,
        lineHeight = (15 * scale).sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.sp,
    ),
)

@Composable
internal fun ParallelCodexTheme(
    colorMode: AppColorMode = AppColorMode.SYSTEM,
    textSize: AppTextSize = AppTextSize.STANDARD,
    accentColor: AppAccentColor = AppAccentColor.BLUE,
    content: @Composable () -> Unit,
) {
    val dark = usesDarkTheme(colorMode, isSystemInDarkTheme())
    val accent = Color(if (dark) accentColor.darkArgb else accentColor.lightArgb)
    MaterialTheme(
        colorScheme = appColorScheme(dark, accent),
        shapes = AppShapes,
        typography = appTypography(textSize.scale),
        content = content,
    )
}
