package com.parallelcodexrunner.remote.data

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TokenBundleTest {
    @Test
    fun persistsIssueTimeAcrossSecureStoreRoundTrip() {
        val json = Json { ignoreUnknownKeys = true }
        val tokens = TokenBundle(
            accessToken = "access",
            refreshToken = "refresh",
            expiresIn = 900,
            userId = "user-1",
            deviceId = "android-1",
            issuedAtMillis = 123_456,
        )

        val encoded = json.encodeToString(tokens)
        val restored = json.decodeFromString<TokenBundle>(encoded)

        assertTrue(encoded.contains("issuedAtMillis"))
        assertEquals(tokens.issuedAtMillis, restored.issuedAtMillis)
        assertEquals(1_023_456L, restored.accessExpiresAtMillis)
    }

    @Test
    fun missingLegacyIssueTimeIsImmediatelyExpired() {
        val restored = Json.decodeFromString<TokenBundle>(
            """{"access_token":"a","refresh_token":"r","expires_in":900,"user_id":"u","device_id":"d"}""",
        )

        assertEquals(0L, restored.issuedAtMillis)
        assertTrue(restored.accessExpiresAtMillis < System.currentTimeMillis())
    }
}
