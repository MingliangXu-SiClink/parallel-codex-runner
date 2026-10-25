package com.parallelcodexrunner.remote.ui

import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationDateTest {
    @Test
    fun conversationDatesFollowTheAppLanguageNotThePhoneLanguage() {
        val originalLocale = Locale.getDefault()
        try {
            val today = LocalDate.now()
            val date = if (today.dayOfYear > 1) today.minusDays(1) else today.plusDays(1)
            val value = date.atStartOfDay(ZoneId.systemDefault()).toInstant().toString()
            Locale.setDefault(Locale.ENGLISH)
            val chinese = checkNotNull(formatConversationDate(value, AppLanguage.CHINESE))
            assertTrue(chinese.contains("月"))
            Locale.setDefault(Locale.SIMPLIFIED_CHINESE)
            assertEquals(chinese, formatConversationDate(value, AppLanguage.CHINESE))
            val english = checkNotNull(formatConversationDate(value, AppLanguage.ENGLISH))
            assertTrue(english.any { it in 'A'..'Z' })
        } finally {
            Locale.setDefault(originalLocale)
        }
    }
}
