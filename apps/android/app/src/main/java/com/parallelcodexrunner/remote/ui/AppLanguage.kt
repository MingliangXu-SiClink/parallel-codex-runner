package com.parallelcodexrunner.remote.ui

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf

enum class AppLanguage(val code: String) {
    CHINESE("zh-Hans"),
    ENGLISH("en");

    companion object {
        fun load(context: Context): AppLanguage {
            val code = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .getString(KEY, CHINESE.code)
            return entries.firstOrNull { it.code == code } ?: CHINESE
        }

        fun save(context: Context, language: AppLanguage) {
            context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY, language.code)
                .apply()
        }

        private const val PREFERENCES = "parallel-codex-app"
        private const val KEY = "language"
    }
}

val LocalAppLanguage = staticCompositionLocalOf { AppLanguage.CHINESE }

@Composable
internal fun appText(chinese: String, english: String): String =
    if (LocalAppLanguage.current == AppLanguage.CHINESE) chinese else english

internal fun AppLanguage.text(chinese: String, english: String): String =
    if (this == AppLanguage.CHINESE) chinese else english
