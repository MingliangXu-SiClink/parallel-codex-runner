package com.parallelcodexrunner.remote.data

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import androidx.test.platform.app.InstrumentationRegistry
import java.io.IOException
import java.util.Base64
import java.util.Collections
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class PairingRecoveryTest {
    private lateinit var context: Context
    private lateinit var prefix: String
    private lateinit var store: SecureStore
    private val json = Json { ignoreUnknownKeys = true }
    private val origin = "https://pairing-recovery.example.test"
    private val original = TokenBundle("old-access", "old-refresh", 900, "qa-user", "qa-android", 1L)
    private val issued = original.copy(accessToken = "paired-access", refreshToken = "paired-refresh")
    private val privateKey = ByteArray(32) { it.toByte() }
    private val link = PairingLink(
        origin, "qa-pairing", Base64.getEncoder().encodeToString(ByteArray(32) { 11 }),
        "qr-approval-test-only", "qa-mac", Base64.getEncoder().encodeToString(ByteArray(32) { 23 }),
    )

    @Before
    fun prepareIsolatedKeystoreBackedState() {
        prefix = "pairing_recovery_qa_${UUID.randomUUID()}_"
        context = object : ContextWrapper(InstrumentationRegistry.getInstrumentation().targetContext) {
            override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
                baseContext.getSharedPreferences(prefix + name, mode)
        }
        store = SecureStore(context)
        store.put("x25519_private_v1", privateKey)
        store.putStrings(mapOf("relay_url" to origin, "relay_tokens" to json.encodeToString(original)))
        store.putString("pending_pairing", "test-only-pending-code")
        store.putString("reader_position_qa", "row-37")
    }

    @After
    fun removeOnlyTestPreferences() {
        for (name in listOf("pcr_remote_secure", "pcr_remote_secure_recovery")) {
            context.deleteSharedPreferences(prefix + name)
        }
    }

    private fun api(handler: (Request) -> Response): RelayApi = RelayApi(
        store, EnvelopeCrypto(store),
        OkHttpClient.Builder().addInterceptor { handler(it.request()) }.build(),
    )

    private fun response(request: Request, text: String = "{}", code: Int = 200): Response =
        Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(code)
            .message("QA fixture").body(text.toResponseBody("application/json".toMediaType())).build()

    private fun error(request: Request, code: String, detail: String, status: Int = 401) =
        response(request, """{"code":"$code","detail":"$detail"}""", status)

    private fun approve(request: Request, pairing: PairingLink = link): Response {
        assertEquals("Bearer ${issued.accessToken}", request.header("Authorization"))
        val buffer = Buffer()
        request.body!!.writeTo(buffer)
        val body = json.parseToJsonElement(buffer.readUtf8()).jsonObject
        assertEquals(pairing.approvalToken, body["approval_token"]!!.jsonPrimitive.content)
        assertEquals(EnvelopeCrypto(store).pairingProof(pairing, issued.deviceId),
            body["pairing_proof"]!!.jsonPrimitive.content)
        return response(request)
    }

    private fun storedTokens(): TokenBundle = json.decodeFromString(store.getString("relay_tokens")!!)

    @Test
    fun expiredLoginCannotPreventApprovalByANewQrCode() = runBlocking {
        val paths = mutableListOf<String>()
        val api = api { request ->
            paths += request.url.encodedPath
            when (request.url.encodedPath) {
                "/v1/auth/refresh" -> error(request, "refresh_invalid", "Refresh token is invalid or expired")
                "/v1/auth/device/challenge" -> error(request, "device_unknown", "Device not found")
                "/v1/auth/pairing" -> {
                    assertNull(request.header("Authorization"))
                    response(request, json.encodeToString(issued))
                }
                "/v1/pairings/qa-pairing/approve" -> approve(request)
                "/v1/devices" -> {
                    assertEquals("Bearer paired-access", request.header("Authorization"))
                    response(request, """{"devices":[]}""")
                }
                else -> throw AssertionError(request.url)
            }
        }
        assertTrue(api.pair(link).retainedAccount)
        assertEquals("paired-refresh", api.ensureTokens().refreshToken)
        api.devices()
        assertEquals(listOf("/v1/auth/refresh", "/v1/auth/device/challenge", "/v1/auth/pairing",
            "/v1/pairings/qa-pairing/approve", "/v1/devices"), paths)
        assertNull(store.getString("pending_pairing"))
        assertEquals(link.macPublicKey, store.getString("mac_pin_${link.macDeviceId}"))
        assertEquals("row-37", store.getString("reader_position_qa"))
        assertArrayEquals(privateKey, store.get("x25519_private_v1"))
        val recreatedStore = SecureStore(context)
        val recreatedApi = RelayApi(recreatedStore, EnvelopeCrypto(recreatedStore))
        assertEquals(api.ensureTokens(), recreatedApi.ensureTokens())
    }

    @Test
    fun rejectedCachedAccessTokenRetriesQrAuthorizationWithoutIt() = runBlocking {
        store.putString("relay_tokens", json.encodeToString(original.copy(issuedAtMillis = System.currentTimeMillis())))
        val pairingHeaders = mutableListOf<String?>()
        val api = api { request ->
            when (request.url.encodedPath) {
                "/v1/auth/pairing" -> {
                    pairingHeaders += request.header("Authorization")
                    if (request.header("Authorization") != null) {
                        error(request, "access_invalid", "Access token is invalid or expired")
                    } else response(request, json.encodeToString(issued))
                }
                "/v1/pairings/qa-pairing/approve" -> approve(request)
                else -> throw AssertionError(request.url)
            }
        }
        assertTrue(api.pair(link).retainedAccount)
        assertEquals(listOf("Bearer old-access", null), pairingHeaders)
        assertEquals(issued.refreshToken, storedTokens().refreshToken)
    }

    @Test
    fun usableAccountAuthorizationIsRetainedWhenAddingAnotherComputer() = runBlocking {
        store.putString("relay_tokens", json.encodeToString(original.copy(issuedAtMillis = System.currentTimeMillis())))
        val api = api { request ->
            when (request.url.encodedPath) {
                "/v1/auth/pairing" -> {
                    assertEquals("Bearer old-access", request.header("Authorization"))
                    response(request, json.encodeToString(issued))
                }
                "/v1/pairings/qa-pairing/approve" -> approve(request)
                else -> throw AssertionError(request.url)
            }
        }
        assertTrue(api.pair(link).retainedAccount)
    }

    @Test
    fun anotherRelayNeverReceivesOldCredentialsOrChangesStoredOriginBeforeApproval() = runBlocking {
        val other = link.copy(relayUrl = "https://other-pairing.example.test")
        val api = api { request ->
            assertEquals("other-pairing.example.test", request.url.host)
            assertEquals(origin, store.getString("relay_url"))
            assertEquals(original, storedTokens())
            when (request.url.encodedPath) {
                "/v1/auth/pairing" -> {
                    assertNull(request.header("Authorization"))
                    response(request, json.encodeToString(issued))
                }
                "/v1/pairings/qa-pairing/approve" -> approve(request, other)
                else -> throw AssertionError(request.url)
            }
        }
        assertFalse(api.pair(other).retainedAccount)
        assertEquals(other.relayUrl, api.baseUrl)
        assertEquals(issued.accessToken, api.ensureTokens().accessToken)
        val socket = api.webSocketRequest()
        assertEquals("other-pairing.example.test", socket.url.host)
        assertEquals("/v1/relay", socket.url.encodedPath)
        assertEquals("Bearer paired-access", socket.header("Authorization"))
    }

    @Test
    fun failedQrAndFailedApprovalPreserveTheOriginalPairingAndPendingCode() {
        for (failApproval in listOf(false, true)) {
            val other = link.copy(relayUrl = "https://other-pairing.example.test")
            val api = api { request ->
                if (failApproval && request.url.encodedPath == "/v1/auth/pairing") {
                    response(request, json.encodeToString(issued))
                } else error(request, "pairing_expired", "Pairing has expired", 410)
            }
            val failure = assertThrows(RelayRequestException::class.java) { runBlocking { api.pair(other) } }
            assertEquals("pairing_expired", failure.errorCode)
            assertEquals(origin, api.baseUrl)
            assertEquals(original, storedTokens())
            assertNotNull(store.getString("pending_pairing"))
            assertNull(store.getString("mac_pin_${link.macDeviceId}"))
            assertArrayEquals(privateKey, store.get("x25519_private_v1"))
        }
    }

    @Test
    fun networkFailurePreservesTrustAndLaterQrRetrySucceeds() = runBlocking {
        val other = link.copy(relayUrl = "https://other-pairing.example.test")
        val offline = api { throw IOException("Network is unavailable") }
        assertThrows(IOException::class.java) { runBlocking { offline.pair(other) } }
        assertEquals(original, storedTokens())
        assertEquals(origin, offline.baseUrl)
        val online = api { request ->
            if (request.url.encodedPath == "/v1/auth/pairing") response(request, json.encodeToString(issued))
            else approve(request, other)
        }
        online.pair(other)
        assertEquals(issued.refreshToken, storedTokens().refreshToken)
    }

    @Test
    fun backgroundLoginWaitsForQrCommitAndCannotReuseOrOverwriteOldTokens() = runBlocking {
        store.putString("relay_tokens", json.encodeToString(original.copy(issuedAtMillis = System.currentTimeMillis())))
        val approving = CountDownLatch(1)
        val release = CountDownLatch(1)
        val api = api { request ->
            when (request.url.encodedPath) {
                "/v1/auth/pairing" -> response(request, json.encodeToString(issued))
                "/v1/pairings/qa-pairing/approve" -> {
                    approving.countDown()
                    check(release.await(5, TimeUnit.SECONDS))
                    approve(request)
                }
                else -> throw AssertionError(request.url)
            }
        }
        val pairing = async(Dispatchers.IO) { api.pair(link) }
        try {
            assertTrue(approving.await(5, TimeUnit.SECONDS))
            val backgroundLogin = async(Dispatchers.IO) { api.ensureTokens() }
            val reconnect = async(Dispatchers.IO) { api.webSocketRequest() }
            delay(150)
            assertFalse(backgroundLogin.isCompleted)
            assertFalse(reconnect.isCompleted)
            release.countDown()
            pairing.await()
            assertEquals(issued.accessToken, backgroundLogin.await().accessToken)
            assertEquals("Bearer paired-access", reconnect.await().header("Authorization"))
            assertEquals(issued.refreshToken, storedTokens().refreshToken)
        } finally {
            release.countDown()
        }
    }

    @Test
    fun oldUnauthorizedResponseCannotRefreshOrPublishAnErrorAfterSuccessfulQrApproval() = runBlocking {
        store.putString("relay_tokens", json.encodeToString(original.copy(issuedAtMillis = System.currentTimeMillis())))
        val reading = CountDownLatch(1)
        val release = CountDownLatch(1)
        val paths = Collections.synchronizedList(mutableListOf<String>())
        val api = api { request ->
            paths += request.url.encodedPath
            when (request.url.encodedPath) {
                "/v1/devices" -> {
                    reading.countDown()
                    check(release.await(5, TimeUnit.SECONDS))
                    error(request, "access_invalid", "Access token is invalid or expired")
                }
                "/v1/auth/pairing" -> response(request, json.encodeToString(issued))
                "/v1/pairings/qa-pairing/approve" -> approve(request)
                else -> throw AssertionError(request.url)
            }
        }
        val stale = async(Dispatchers.IO) { runCatching { api.devices() }.exceptionOrNull() }
        try {
            assertTrue(reading.await(5, TimeUnit.SECONDS))
            api.pair(link)
            release.countDown()
            assertTrue(stale.await() is CancellationException)
            assertFalse(paths.contains("/v1/auth/refresh"))
            assertEquals(issued.refreshToken, storedTokens().refreshToken)
        } finally {
            release.countDown()
        }
    }
}
