package com.parallelcodexrunner.remote.data

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import androidx.test.platform.app.InstrumentationRegistry
import java.io.IOException
import java.util.Base64
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class PersistentDeviceLoginTest {
    private lateinit var context: Context
    private lateinit var prefix: String
    private lateinit var store: SecureStore
    private val json = Json { ignoreUnknownKeys = true }
    private val relayUrl = "http://127.0.0.1:18174"
    private val original = TokenBundle("expired-access", "expired-refresh-test-credential-0123456789", 900,
                                       "persistent-login-qa-user", "persistent-login-qa-android", 1L)

    @Before
    fun prepareIsolatedKeystoreBackedState() {
        prefix = "persistent_login_qa_${UUID.randomUUID()}_"
        context = object : ContextWrapper(InstrumentationRegistry.getInstrumentation().targetContext) {
            override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
                baseContext.getSharedPreferences(prefix + name, mode)
        }
        store = SecureStore(context)
        store.put("x25519_private_v1", Base64.getDecoder().decode("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="))
        store.putString("relay_url", relayUrl)
        store.putString("relay_tokens", json.encodeToString(original))
    }

    @After
    fun removeOnlyTestPreferences() {
        for (name in listOf("pcr_remote_secure", "pcr_remote_secure_recovery")) {
            context.deleteSharedPreferences(prefix + name)
        }
    }

    private fun api(secure: SecureStore = store) = RelayApi(secure, EnvelopeCrypto(secure),
        OkHttpClient.Builder().connectTimeout(2, TimeUnit.SECONDS).readTimeout(5, TimeUnit.SECONDS).build())

    @Test
    fun expiredSessionRecoversAndSurvivesClientRecreationWithoutPairing() = runBlocking {
        val restored = api().ensureTokens()
        assertEquals(original.deviceId, restored.deviceId)
        assertEquals(original.userId, restored.userId)
        assertNotEquals(original.refreshToken, restored.refreshToken)
        assertTrue(restored.accessExpiresAtMillis > System.currentTimeMillis())
        assertEquals(restored, api(SecureStore(context)).ensureTokens())
    }

    @Test
    fun noSessionTokensStillRetainsTrustedIdentity() = runBlocking {
        store.putString("relay_tokens", json.encodeToString(original.copy(accessToken = "", refreshToken = "")))
        val restored = api().ensureTokens()
        assertEquals(original.deviceId, restored.deviceId)
        assertTrue(restored.accessToken.isNotBlank())
    }

    @Test
    fun networkLossDoesNotRemovePairingAndReconnectRecovers() {
        store.putString("relay_url", "http://127.0.0.1:18175")
        assertThrows(IOException::class.java) { runBlocking { api().ensureTokens() } }
        assertEquals(original, json.decodeFromString<TokenBundle>(store.getString("relay_tokens")!!))
        assertTrue(api().authenticated)
        store.putString("relay_url", relayUrl)
        assertEquals(original.deviceId, runBlocking { api().ensureTokens() }.deviceId)
    }

    @Test
    fun wrongKeyNeverSignsInOrDeletesStoredIdentity() {
        store.put("x25519_private_v1", ByteArray(32) { 19 })
        assertThrows(IllegalArgumentException::class.java) { runBlocking { api().ensureTokens() } }
        assertEquals(original, json.decodeFromString<TokenBundle>(store.getString("relay_tokens")!!))
        assertTrue(api().authenticated)
    }
}
