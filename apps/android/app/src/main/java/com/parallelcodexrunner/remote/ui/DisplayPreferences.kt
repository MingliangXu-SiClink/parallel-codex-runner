package com.parallelcodexrunner.remote.ui

import android.content.Context

enum class AppColorMode(val code: String) {
    SYSTEM("system"),
    LIGHT("light"),
    DARK("dark");

    companion object {
        fun fromCode(value: String?): AppColorMode =
            entries.firstOrNull { it.code == value } ?: SYSTEM
    }
}

enum class AppTextSize(val code: String, val scale: Float) {
    SMALL("small", 0.90f),
    STANDARD("standard", 1.00f),
    LARGE("large", 1.15f);

    companion object {
        fun fromCode(value: String?): AppTextSize =
            entries.firstOrNull { it.code == value } ?: STANDARD
    }
}

enum class AppAccentColor(
    val code: String,
    val lightArgb: Long,
    val darkArgb: Long,
) {
    BLUE("blue", 0xFF3370FF, 0xFF6C8FFF),
    TEAL("teal", 0xFF008A83, 0xFF48C9C1),
    GREEN("green", 0xFF238A4B, 0xFF54C878),
    ORANGE("orange", 0xFFC85B18, 0xFFFF9F5A),
    PINK("pink", 0xFFC23B72, 0xFFFF75A7);

    companion object {
        fun fromCode(value: String?): AppAccentColor =
            entries.firstOrNull { it.code == value } ?: BLUE
    }
}

data class AppDisplayPreferences(
    val colorMode: AppColorMode = AppColorMode.SYSTEM,
    val textSize: AppTextSize = AppTextSize.STANDARD,
    val accentColor: AppAccentColor = AppAccentColor.BLUE,
    val workExpandedByDefault: Boolean = false,
    val backgroundConnectionEnabled: Boolean = false,
)

internal object DisplayPreferences {
    fun load(context: Context): AppDisplayPreferences {
        val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        return AppDisplayPreferences(
            colorMode = AppColorMode.fromCode(preferences.getString(COLOR_MODE_KEY, null)),
            textSize = AppTextSize.fromCode(preferences.getString(TEXT_SIZE_KEY, null)),
            accentColor = AppAccentColor.fromCode(
                preferences.getString(ACCENT_COLOR_KEY, null)
            ),
            workExpandedByDefault = preferences.getBoolean(
                WORK_EXPANDED_BY_DEFAULT_KEY,
                false,
            ),
            backgroundConnectionEnabled = preferences.getBoolean(
                BACKGROUND_CONNECTION_KEY,
                false,
            ),
        )
    }

    fun saveColorMode(context: Context, mode: AppColorMode) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(COLOR_MODE_KEY, mode.code)
            .apply()
    }

    fun saveTextSize(context: Context, size: AppTextSize) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(TEXT_SIZE_KEY, size.code)
            .apply()
    }

    fun saveAccentColor(context: Context, color: AppAccentColor) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(ACCENT_COLOR_KEY, color.code)
            .apply()
    }

    fun saveBackgroundConnectionEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(BACKGROUND_CONNECTION_KEY, enabled)
            .apply()
    }

    fun saveWorkExpandedByDefault(context: Context, expanded: Boolean) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(WORK_EXPANDED_BY_DEFAULT_KEY, expanded)
            .apply()
    }

    fun backgroundConnectionEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getBoolean(BACKGROUND_CONNECTION_KEY, false)

    private const val PREFERENCES = "parallel-codex-app"
    private const val COLOR_MODE_KEY = "color-mode"
    private const val TEXT_SIZE_KEY = "text-size"
    private const val ACCENT_COLOR_KEY = "accent-color"
    private const val WORK_EXPANDED_BY_DEFAULT_KEY = "work-expanded-by-default"
    private const val BACKGROUND_CONNECTION_KEY = "background-connection"
}

internal fun usesDarkTheme(mode: AppColorMode, systemDark: Boolean): Boolean = when (mode) {
    AppColorMode.SYSTEM -> systemDark
    AppColorMode.LIGHT -> false
    AppColorMode.DARK -> true
}
