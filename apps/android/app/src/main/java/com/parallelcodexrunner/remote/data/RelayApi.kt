package com.parallelcodexrunner.remote.data

import android.net.Uri
import android.os.Build
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

class RelayRequestException(
    val statusCode: Int,
    message: String,
    val errorCode: String = "",
) : IOException(message)

class RelayApi(
    private val secureStore: SecureStore,
    private val crypto: EnvelopeCrypto,
    val httpClient: OkHttpClient = defaultRelayHttpClient(),
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val tokenMutex = Mutex()
    private var pairingGeneration = 0L

    private data class AuthenticatedSession(
        val origin: String,
        val tokens: TokenBundle,
        val generation: Long,
    )
    private val updateHttpClient = httpClient.newBuilder()
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    val baseUrl: String
        get() = secureStore.getString(RELAY_URL).orEmpty()

    private var tokens: TokenBundle?
        get() = secureStore.getString(TOKENS)?.let {
            runCatching { json.decodeFromString<TokenBundle>(it) }.getOrNull()
        }
        set(value) {
            if (value == null) secureStore.remove(TOKENS)
            else secureStore.putString(TOKENS, json.encodeToString(value))
        }

    val authenticated: Boolean
        get() = tokens != null && baseUrl.isNotBlank()

    suspend fun pair(link: PairingLink): PairingResult = withContext(Dispatchers.IO) {
        tokenMutex.withLock {
            val previousBaseUrl = baseUrl
            val previousTokens = tokens
            val origin = normalizeRelayBaseUrl(link.relayUrl)
            // A fresh QR code authorizes pairing independently of the old login.
            val accountAccessToken = if (previousTokens != null && previousBaseUrl == origin) {
                try {
                    ensureTokensLocked().accessToken
                } catch (_: IOException) {
                    null
                }
            } else null
            val login = PairingLoginRequest(
                pairingId = link.pairingId,
                approvalToken = link.approvalToken,
                deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
                publicKey = crypto.publicKey,
            )
            suspend fun authenticate(accessToken: String?) = requireNotNull(
                request(
                    TokenBundle.serializer(),
                    "POST",
                    "/v1/auth/pairing",
                    json.encodeToJsonElement(PairingLoginRequest.serializer(), login),
                    explicitAccessToken = accessToken,
                    expectedBaseUrl = origin,
                )
            )
            val issued = (try {
                authenticate(accountAccessToken)
            } catch (error: RelayRequestException) {
                if (accountAccessToken == null || error.statusCode !in setOf(401, 403)) throw error
                authenticate(null)
            }).copy(issuedAtMillis = System.currentTimeMillis())

            val proof = crypto.pairingProof(link, issued.deviceId)
            request<Unit>(
                null,
                "POST",
                "/v1/pairings/${link.pairingId}/approve",
                json.encodeToJsonElement(
                    PairingApproveRequest.serializer(),
                    PairingApproveRequest(proof, link.approvalToken),
                ),
                explicitAccessToken = issued.accessToken,
                expectedBaseUrl = origin,
            )
            secureStore.putStrings(
                mapOf(
                    RELAY_URL to origin,
                    TOKENS to json.encodeToString(issued),
                    macPinKey(link.macDeviceId) to link.macPublicKey,
                ),
                removeKeys = setOf(PENDING_PAIRING),
            )
            pairingGeneration += 1
            PairingResult(
                retainedAccount = pairingRetainsAccount(
                    previousRelayUrl = previousBaseUrl,
                    previousUserId = previousTokens?.userId,
                    nextRelayUrl = origin,
                    nextUserId = issued.userId,
                ),
            )
        }
    }

    suspend fun ensureTokens(): TokenBundle = tokenMutex.withLock {
        ensureTokensLocked()
    }

    private suspend fun ensureTokensLocked(): TokenBundle {
        val current = tokens ?: error("Scan a device pairing code first")
        if (current.accessToken.isNotBlank() &&
            current.accessExpiresAtMillis > System.currentTimeMillis() + 60_000) {
            return current
        }
        return refreshTokens(current)
    }

    private suspend fun authenticatedSession(): AuthenticatedSession = tokenMutex.withLock {
        val current = ensureTokensLocked()
        AuthenticatedSession(baseUrl, current, pairingGeneration)
    }

    private suspend fun refreshAfterUnauthorized(failedSession: AuthenticatedSession): AuthenticatedSession =
        tokenMutex.withLock {
            requireCurrentSessionLocked(failedSession)
            val current = tokens ?: error("Scan a device pairing code first")
            if (current != failedSession.tokens) {
                return@withLock failedSession.copy(tokens = current)
            }
            failedSession.copy(tokens = refreshTokens(current))
        }

    private fun requireCurrentSessionLocked(session: AuthenticatedSession) {
        val current = tokens
        if (baseUrl != session.origin || pairingGeneration != session.generation ||
            current?.deviceId != session.tokens.deviceId || current.userId != session.tokens.userId
        ) throw CancellationException("Device pairing changed while the request was completing")
    }

    private suspend fun requireCurrentSession(session: AuthenticatedSession?) {
        if (session != null) tokenMutex.withLock { requireCurrentSessionLocked(session) }
    }

    private suspend fun refreshTokens(current: TokenBundle): TokenBundle {
        val origin = baseUrl
        val refreshed = (if (current.refreshToken.isBlank()) {
            deviceLogin(current, origin)
        } else try {
            requireNotNull(request(
                TokenBundle.serializer(), "POST", "/v1/auth/refresh",
                json.encodeToJsonElement(RefreshRequest.serializer(), RefreshRequest(current.refreshToken)),
                expectedBaseUrl = origin,
            ))
        } catch (error: RelayRequestException) {
            if (!refreshAllowsDeviceLogin(error)) throw error
            deviceLogin(current, origin)
        }).copy(issuedAtMillis = System.currentTimeMillis())
        check(baseUrl == origin && tokens == current) {
            "Device pairing changed while sign-in was completing"
        }
        check(refreshed.deviceId == current.deviceId && refreshed.userId == current.userId) {
            "Relay sign-in returned a different device or account"
        }
        tokens = refreshed
        return refreshed
    }

    private suspend fun deviceLogin(current: TokenBundle, origin: String): TokenBundle {
        val challenge = requireNotNull(request(
            DeviceLoginChallenge.serializer(), "POST", "/v1/auth/device/challenge",
            buildJsonObject { put("device_id", current.deviceId) }, expectedBaseUrl = origin,
        ))
        val proof = crypto.deviceLoginProof(challenge, current.deviceId, current.userId, origin)
        return requireNotNull(request(
            TokenBundle.serializer(), "POST", "/v1/auth/device",
            buildJsonObject {
                put("device_id", current.deviceId)
                put("challenge_id", challenge.challengeId)
                put("proof", proof)
            }, expectedBaseUrl = origin,
        ))
    }

    suspend fun devices(): List<RelayDevice> {
        val response = requireNotNull(
            request(
                DeviceListResponse.serializer(),
                "GET",
                "/v1/devices",
                authenticated = true,
            )
        )
        return response.devices.filter { device ->
            if (!device.kind.equals("mac", ignoreCase = true)) {
                true
            } else {
                val pinned = secureStore.getString(macPinKey(device.deviceId))
                when {
                    pinned == null -> false
                    pinned != device.publicKey -> error(
                        "Relay returned a different public key for ${device.name}"
                    )
                    else -> true
                }
            }
        }
    }

    suspend fun submitFeedback(
        clientFeedbackId: String,
        kind: String,
        description: String,
        diagnostics: JsonObject,
        relatedDeviceId: String,
    ): FeedbackItem = requireNotNull(
        request(
            FeedbackResponse.serializer(),
            "POST",
            "/v1/feedback",
            buildJsonObject {
                put("client_feedback_id", clientFeedbackId)
                put("kind", kind)
                put("description", description)
                put("diagnostics", diagnostics)
                if (relatedDeviceId.isNotBlank()) {
                    put("related_device_id", relatedDeviceId)
                }
            },
            authenticated = true,
        )
    ).feedback

    suspend fun feedback(cursor: String = "", limit: Int = 30): FeedbackListResponse {
        val path = buildString {
            append("/v1/feedback?limit=")
            append(limit.coerceIn(1, 50))
            if (cursor.isNotBlank()) {
                append("&cursor=")
                append(Uri.encode(cursor))
            }
        }
        return requireNotNull(
            request(
                FeedbackListResponse.serializer(),
                "GET",
                path,
                authenticated = true,
            )
        )
    }

    suspend fun addFeedbackNote(
        feedbackId: String,
        text: String,
        clientNoteId: String,
    ): FeedbackItem = requireNotNull(
        request(
            FeedbackResponse.serializer(),
            "POST",
            "/v1/feedback/${Uri.encode(feedbackId)}/notes",
            buildJsonObject {
                put("client_note_id", clientNoteId)
                put("text", text)
            },
            authenticated = true,
        )
    ).feedback

    suspend fun addFeedbackAttachment(
        feedbackId: String,
        contentType: String,
        encoded: String,
    ): FeedbackItem = requireNotNull(
        request(
            FeedbackResponse.serializer(),
            "POST",
            "/v1/feedback/${Uri.encode(feedbackId)}/attachments",
            buildJsonObject {
                put("content_type", contentType)
                put("data", encoded)
            },
            authenticated = true,
        )
    ).feedback

    suspend fun removeFeedbackAttachment(
        feedbackId: String,
        attachmentId: String,
    ): FeedbackItem = requireNotNull(
        request(
            FeedbackResponse.serializer(),
            "DELETE",
            "/v1/feedback/${Uri.encode(feedbackId)}/attachments/" +
                Uri.encode(attachmentId),
            authenticated = true,
        )
    ).feedback

    suspend fun withdrawFeedback(feedbackId: String): FeedbackItem = requireNotNull(
        request(
            FeedbackResponse.serializer(),
            "POST",
            "/v1/feedback/${Uri.encode(feedbackId)}/withdraw",
            authenticated = true,
        )
    ).feedback

    suspend fun checkAndroidUpdate(
        macDeviceId: String,
        currentVersionCode: Long,
    ): AndroidUpdateCheckResponse {
        val body = AndroidUpdateCheckRequest(
            macDeviceId = macDeviceId,
            packageName = com.parallelcodexrunner.remote.BuildConfig.APPLICATION_ID,
            currentVersionCode = currentVersionCode,
        )
        return requireNotNull(
            request(
                AndroidUpdateCheckResponse.serializer(),
                "POST",
                "/v1/app-updates/android/check",
                json.encodeToJsonElement(AndroidUpdateCheckRequest.serializer(), body),
                authenticated = true,
            )
        )
    }

    suspend fun androidUpdateHistory(
        macDeviceId: String,
        cursor: Int = 0,
        limit: Int = 20,
    ): AndroidUpdateHistoryResponse {
        val response = requireNotNull(
            request(
                AndroidUpdateHistoryResponse.serializer(),
                "GET",
                "/v1/app-updates/android/releases" +
                    "?mac_device_id=${Uri.encode(macDeviceId)}" +
                    "&cursor=${cursor.coerceAtLeast(0)}" +
                    "&limit=${limit.coerceIn(1, 50)}",
                authenticated = true,
            )
        )
        if (cursor == 0) {
            secureStore.putString(
                updateHistoryKey(macDeviceId),
                json.encodeToString(AndroidUpdateHistoryResponse.serializer(), response),
            )
        }
        return response
    }

    fun cachedAndroidUpdateHistory(macDeviceId: String): AndroidUpdateHistoryResponse? =
        secureStore.getString(updateHistoryKey(macDeviceId))?.let { value ->
            runCatching {
                json.decodeFromString(AndroidUpdateHistoryResponse.serializer(), value)
            }.getOrNull()
        }

    suspend fun createAndroidUpdateTicket(
        macDeviceId: String,
        releaseId: String,
    ): AndroidUpdateTicketResponse {
        val body = AndroidUpdateTicketRequest(macDeviceId, releaseId)
        return requireNotNull(
            request(
                AndroidUpdateTicketResponse.serializer(),
                "POST",
                "/v1/app-updates/android/tickets",
                json.encodeToJsonElement(AndroidUpdateTicketRequest.serializer(), body),
                authenticated = true,
            )
        )
    }

    suspend fun downloadAndroidUpdate(
        ticket: AndroidUpdateTicketResponse,
        destination: File,
        onProgress: (downloadedBytes: Long, totalBytes: Long) -> Unit = { _, _ -> },
    ): File = withContext(Dispatchers.IO) {
        require(ticket.downloadPath == ANDROID_UPDATE_DOWNLOAD_PATH) {
            "Relay returned an invalid update download path"
        }
        val expectedSize = ticket.release.sizeBytes
        require(expectedSize in 1..MAX_ANDROID_UPDATE_BYTES) {
            "Android update size is invalid"
        }
        require(ticket.release.sha256.matches(SHA256_PATTERN)) {
            "Android update checksum is invalid"
        }

        var session = authenticatedSession()
        val url = session.origin + ticket.downloadPath

        fun buildDownloadRequest(token: String): Request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .header("X-PCR-Update-Ticket", ticket.ticket)
            .header("Cache-Control", "no-cache")
            .post(ByteArray(0).toRequestBody())
            .build()

        var response = updateHttpClient.newCall(buildDownloadRequest(session.tokens.accessToken)).execute()
        if (response.code == 401) {
            response.close()
            session = refreshAfterUnauthorized(session)
            response = updateHttpClient.newCall(buildDownloadRequest(session.tokens.accessToken)).execute()
        }
        response.use { result ->
            requireCurrentSession(session)
            if (!result.isSuccessful) {
                val detail = result.body.string().trim()
                throw responseError(
                    detail,
                    result.code,
                    "Android update download failed",
                )
            }
            val body = result.body
            if (body.contentLength() >= 0 && body.contentLength() != expectedSize) {
                throw IOException("Android update size does not match its metadata")
            }
            val parent = destination.parentFile
                ?: throw IOException("Android update destination is invalid")
            parent.mkdirs()
            val temporary = File(parent, ".${destination.name}.part")
            temporary.delete()
            try {
                val digest = MessageDigest.getInstance("SHA-256")
                var downloaded = 0L
                body.byteStream().use { input ->
                    temporary.outputStream().use { output ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            if (count == 0) continue
                            downloaded += count
                            if (downloaded > expectedSize ||
                                downloaded > MAX_ANDROID_UPDATE_BYTES
                            ) {
                                throw IOException("Android update is larger than expected")
                            }
                            digest.update(buffer, 0, count)
                            output.write(buffer, 0, count)
                            onProgress(downloaded, expectedSize)
                        }
                        output.flush()
                    }
                }
                if (downloaded != expectedSize) {
                    throw IOException("Android update download is incomplete")
                }
                val actual = digest.digest().toHex()
                if (!actual.equals(ticket.release.sha256, ignoreCase = true)) {
                    throw IOException("Android update checksum verification failed")
                }
                requireCurrentSession(session)
                destination.delete()
                if (!temporary.renameTo(destination)) {
                    throw IOException("Unable to prepare Android update")
                }
            } catch (error: Throwable) {
                temporary.delete()
                throw error
            }
        }
        destination
    }

    suspend fun revokeDevice(deviceId: String) {
        request<Unit>(
            null,
            "DELETE",
            "/v1/devices/$deviceId",
            authenticated = true,
        )
        secureStore.remove(macPinKey(deviceId))
        secureStore.remove(updateHistoryKey(deviceId))
    }

    fun currentDeviceId(): String = tokens?.deviceId.orEmpty()

    fun rememberPendingPairing(code: String) {
        secureStore.putString(PENDING_PAIRING, PairingCode.normalize(code))
    }

    fun pendingPairing(): String? = secureStore.getString(PENDING_PAIRING)

    fun clearPendingPairing() {
        secureStore.remove(PENDING_PAIRING)
    }

    internal suspend fun webSocketRequest(): Request = tokenMutex.withLock {
        val current = ensureTokensLocked()
        Request.Builder().url(webSocketUrl(baseUrl))
            .header("Authorization", "Bearer ${current.accessToken}").build()
    }

    private fun webSocketUrl(origin: String): String {
        val uri = Uri.parse(normalizeRelayBaseUrl(origin))
        val scheme = if (uri.scheme == "https") "wss" else "ws"
        return uri.buildUpon()
            .scheme(scheme)
            .appendEncodedPath("v1/relay")
            .build()
            .toString()
    }

    private suspend fun <T> request(
        serializer: DeserializationStrategy<T>?,
        method: String,
        path: String,
        body: JsonElement? = null,
        authenticated: Boolean = false,
        explicitAccessToken: String? = null,
        expectedBaseUrl: String? = null,
    ): T? = withContext(Dispatchers.IO) {
        var session = if (authenticated && explicitAccessToken == null) authenticatedSession() else null
        val url = normalizeRelayBaseUrl(expectedBaseUrl ?: session?.origin ?: baseUrl) + path
        val encoded = body?.let { json.encodeToString(JsonElement.serializer(), it) }
        var accessToken = explicitAccessToken
            ?: session?.tokens?.accessToken

        fun buildRequest(token: String?): Request {
            val builder = Request.Builder().url(url)
            if (token != null) {
                builder.header("Authorization", "Bearer $token")
            }
            when (method) {
                "GET" -> builder.get()
                "DELETE" -> builder.delete()
                else -> builder.method(
                    method,
                    (encoded ?: "{}").toRequestBody(JSON_MEDIA_TYPE),
                )
            }
            return builder.build()
        }

        try {
            var response = executeRequestWithRetry(
                request = buildRequest(accessToken),
                retryConnectivity = method == "GET",
            )
            if (
                authenticated &&
                explicitAccessToken == null &&
                response.code == 401 &&
                accessToken != null
            ) {
                response.close()
                session = refreshAfterUnauthorized(requireNotNull(session))
                accessToken = session.tokens.accessToken
                response = executeRequestWithRetry(
                    request = buildRequest(accessToken),
                    retryConnectivity = method == "GET",
                )
            }
            response.use {
                val text = it.body.string()
                requireCurrentSession(session)
                if (!it.isSuccessful) {
                    throw responseError(text, it.code, "Relay request failed")
                }
                if (serializer == null) null else json.decodeFromString(serializer, text)
            }
        } catch (error: IOException) {
            requireCurrentSession(session)
            throw error
        }
    }

    private suspend fun executeRequestWithRetry(
        request: Request,
        retryConnectivity: Boolean,
    ): okhttp3.Response {
        var lastFailure: IOException? = null
        repeat(if (retryConnectivity) 3 else 1) { attempt ->
            try {
                return httpClient.newCall(request).execute()
            } catch (error: IOException) {
                lastFailure = error
                if (!retryConnectivity || attempt == 2) throw error
                delay(if (attempt == 0) 300L else 900L)
            }
        }
        throw lastFailure ?: IOException("Relay request failed")
    }

    private fun responseError(text: String, code: Int, fallback: String): IOException {
        val detail = runCatching {
            json.parseToJsonElement(text).jsonObject["detail"]?.jsonPrimitive?.content
        }.getOrNull()?.trim()?.takeIf(String::isNotEmpty)
        val errorCode = runCatching {
            json.parseToJsonElement(text).jsonObject["code"]?.jsonPrimitive?.content
        }.getOrNull().orEmpty()
        return RelayRequestException(code, detail ?: "$fallback with HTTP $code", errorCode)
    }

    companion object {
        private const val RELAY_URL = "relay_url"
        private const val TOKENS = "relay_tokens"
        private const val PENDING_PAIRING = "pending_pairing"
        private const val ANDROID_UPDATE_DOWNLOAD_PATH =
            "/v1/app-updates/android/download"
        private const val MAX_ANDROID_UPDATE_BYTES = 512L * 1024 * 1024
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val SHA256_PATTERN = Regex("[0-9a-fA-F]{64}")

        private fun macPinKey(deviceId: String): String = "mac_pin_$deviceId"
        private fun updateHistoryKey(deviceId: String): String =
            "android_update_history_$deviceId"
    }
}

internal fun refreshAllowsDeviceLogin(error: RelayRequestException): Boolean =
    error.statusCode in setOf(401, 403) && error.errorCode !in setOf(
        "refresh_revoked", "device_inactive", "device_unknown", "user_inactive",
    )

internal fun defaultRelayHttpClient(): OkHttpClient = OkHttpClient.Builder()
    // Without application-level pings a severed mobile connection can still look open to
    // OkHttp. send() then succeeds locally and every command waits for its full timeout.
    .pingInterval(20, TimeUnit.SECONDS)
    .connectTimeout(15, TimeUnit.SECONDS)
    .writeTimeout(15, TimeUnit.SECONDS)
    .build()

internal fun pairingRetainsAccount(
    previousRelayUrl: String,
    previousUserId: String?,
    nextRelayUrl: String,
    nextUserId: String,
): Boolean = previousRelayUrl.isNotBlank() &&
    previousRelayUrl == nextRelayUrl &&
    previousUserId != null &&
    previousUserId == nextUserId

private fun ByteArray.toHex(): String = joinToString("") {
    "%02x".format(it.toInt() and 0xff)
}
