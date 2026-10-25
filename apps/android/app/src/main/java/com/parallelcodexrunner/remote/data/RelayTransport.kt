package com.parallelcodexrunner.remote.data

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

internal data class RelayDelivery(
    val messageId: String,
    val sender: String?,
    val target: String?,
    val sequence: Long?,
    val ciphertext: String?,
)

internal fun relayDelivery(envelope: JsonObject): RelayDelivery? {
    fun string(name: String): String? =
        (envelope[name] as? JsonPrimitive)?.contentOrNull?.trim()?.takeIf(String::isNotEmpty)

    val messageId = string("message_id") ?: return null
    return RelayDelivery(
        messageId = messageId,
        sender = string("sender_device_id"),
        target = string("target_device_id"),
        sequence = (envelope["sequence"] as? JsonPrimitive)?.longOrNull,
        ciphertext = string("ciphertext"),
    )
}

class RelayTransport(
    private val api: RelayApi,
    private val crypto: EnvelopeCrypto,
    private val secureStore: SecureStore,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val connectionMutex = Mutex()
    private val pending = ConcurrentHashMap<String, CompletableDeferred<JsonObject>>()
    private val pendingSubmissions = ConcurrentHashMap<String, CompletableDeferred<Unit>>()
    private val deviceKeys = ConcurrentHashMap<String, String>()
    @Volatile private var socket: WebSocket? = null
    @Volatile private var connectedSocket: WebSocket? = null
    @Volatile private var connecting: CompletableDeferred<Unit>? = null

    suspend fun command(
        target: RelayDevice,
        action: String,
        body: JsonObject = JsonObject(emptyMap()),
        expectedRevision: Int? = null,
        timeoutMillis: Long = 60_000,
        commandId: String? = null,
        maxAttempts: Int = 2,
    ): JsonObject {
        require(
            target.kind.equals("mac", ignoreCase = true) &&
                target.status.equals("active", ignoreCase = true)
        ) { "Target machine is not active" }
        deviceKeys[target.deviceId] = target.publicKey
        // A command id identifies one logical mutation and must survive relay
        // retries. A request id identifies one transport attempt and must not.
        val stableCommandId = commandId?.trim()?.takeIf(String::isNotEmpty)
            ?: UUID.randomUUID().toString()
        var lastFailure: Throwable? = null
        val attempts = maxAttempts.coerceIn(1, 2)
        repeat(attempts) { attempt ->
            try {
                ensureConnected()
                val requestId = UUID.randomUUID().toString()
                val request = buildJsonObject {
                    put("protocol_version", 1)
                    put("request_id", requestId)
                    put("command_id", stableCommandId)
                    put("client_id", api.currentDeviceId())
                    put("action", action)
                    put("body", body)
                    expectedRevision?.let { put("expected_revision", it) }
                }
                val plaintext = buildJsonObject {
                    put("type", "request")
                    put("request", request)
                }
                val messageId = "message-${UUID.randomUUID()}"
                val sequence = nextSequence()
                val ciphertext = crypto.encrypt(
                    target.publicKey,
                    plaintext,
                    messageId,
                    api.currentDeviceId(),
                    target.deviceId,
                    sequence,
                )
                val wire = buildJsonObject {
                    put("message_id", messageId)
                    put("target_device_id", target.deviceId)
                    put("sequence", sequence)
                    put("ciphertext", ciphertext)
                }
                val deferred = CompletableDeferred<JsonObject>()
                val submission = CompletableDeferred<Unit>()
                pending[requestId] = deferred
                pendingSubmissions[messageId] = submission
                val activeSocket = socket
                if (activeSocket?.send(
                        json.encodeToString(JsonObject.serializer(), wire)
                    ) != true
                ) {
                    pending.remove(requestId, deferred)
                    pendingSubmissions.remove(messageId, submission)
                    val error = IOException("Relay WebSocket is not connected")
                    disconnect(
                        error,
                        expectedSocket = activeSocket,
                        cancelSocket = true,
                    )
                    throw error
                }
                try {
                    return withTimeout(timeoutMillis) {
                        submission.await()
                        deferred.await()
                    }
                } finally {
                    pending.remove(requestId, deferred)
                    pendingSubmissions.remove(messageId, submission)
                }
            } catch (error: TimeoutCancellationException) {
                lastFailure = error
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                lastFailure = error
            }
            if (attempt + 1 < attempts) delay(350L)
        }
        throw lastFailure ?: IOException("Relay request failed")
    }

    suspend fun close() = withContext(Dispatchers.IO) {
        disconnect(IOException("Relay connection was replaced"), cancelSocket = true)
    }

    fun rememberDevices(devices: List<RelayDevice>) {
        deviceKeys.clear()
        devices.asSequence()
            .filter { it.status.equals("active", ignoreCase = true) }
            .filter { it.deviceId.isNotBlank() && it.publicKey.isNotBlank() }
            .forEach { deviceKeys[it.deviceId] = it.publicKey }
    }

    private suspend fun ensureConnected() {
        connectionMutex.withLock {
            val existing = socket
            if (existing != null && connectedSocket === existing) return
            if (existing != null) {
                disconnect(
                    IOException("Replacing an incomplete relay connection"),
                    expectedSocket = existing,
                    cancelSocket = true,
                )
            }
            api.devices().forEach { deviceKeys[it.deviceId] = it.publicKey }
            val ready = CompletableDeferred<Unit>()
            connecting = ready
            // Capture the origin and refreshed credential together, including
            // when a new QR approval races with reconnecting this socket.
            val newSocket = try {
                val request = api.webSocketRequest()
                api.httpClient.newWebSocket(request, Listener(ready))
            } catch (error: Throwable) {
                disconnect(error, cancelSocket = true)
                throw error
            }
            if (!registerSocket(newSocket, ready)) {
                newSocket.cancel()
            }
            try {
                withTimeout(15_000) { ready.await() }
            } catch (error: Throwable) {
                disconnect(error, expectedSocket = newSocket, cancelSocket = true)
                throw error
            }
        }
    }

    @Synchronized
    private fun nextSequence(): Long {
        val current = secureStore.getString(SEQUENCE)?.toLongOrNull() ?: 0
        require(current < Long.MAX_VALUE) { "Relay sequence is exhausted; sign in again" }
        val next = current + 1
        secureStore.putString(SEQUENCE, next.toString())
        return next
    }

    private fun disconnect(
        error: Throwable,
        expectedSocket: WebSocket? = null,
        cancelSocket: Boolean,
    ) {
        val detached = synchronized(this) {
            if (expectedSocket != null && socket !== expectedSocket) return
            val current = socket
            val ready = connecting
            socket = null
            connectedSocket = null
            connecting = null
            current to ready
        }
        if (cancelSocket) detached.first?.cancel()
        detached.second?.let { if (!it.isCompleted) it.completeExceptionally(error) }
        pending.values.forEach { it.completeExceptionally(error) }
        pending.clear()
        pendingSubmissions.values.forEach { it.completeExceptionally(error) }
        pendingSubmissions.clear()
    }

    private fun markConnected(
        webSocket: WebSocket,
        ready: CompletableDeferred<Unit>,
    ): Boolean = synchronized(this) {
        if (!registerSocket(webSocket, ready)) return@synchronized false
        connectedSocket = webSocket
        true
    }

    private fun registerSocket(
        webSocket: WebSocket,
        ready: CompletableDeferred<Unit>,
    ): Boolean = synchronized(this) {
        if (connecting !== ready) return@synchronized false
        if (socket == null) socket = webSocket
        socket === webSocket
    }

    private inner class Listener(
        private val ready: CompletableDeferred<Unit>,
    ) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (markConnected(webSocket, ready)) {
                ready.complete(Unit)
            } else {
                webSocket.cancel()
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val envelope = json.parseToJsonElement(text).jsonObject
                when (envelope["type"]?.jsonPrimitive?.content) {
                    "accepted" -> {
                        val messageId = envelope["message_id"]
                            ?.jsonPrimitive
                            ?.content
                            ?: return
                        pendingSubmissions.remove(messageId)?.complete(Unit)
                        return
                    }
                    "rejected" -> {
                        val messageId = envelope["message_id"]
                            ?.jsonPrimitive
                            ?.content
                            ?: return
                        val message = envelope["message"]
                            ?.jsonPrimitive
                            ?.content
                            ?.trim()
                            .orEmpty()
                            .ifBlank { "Relay rejected the request" }
                        pendingSubmissions.remove(messageId)?.completeExceptionally(
                            IOException(message)
                        )
                        return
                    }
                    "error" -> {
                        val message = envelope["message"]
                            ?.jsonPrimitive
                            ?.content
                            ?.trim()
                            .orEmpty()
                            .ifBlank { "Relay connection failed" }
                        disconnect(
                            IOException(message),
                            expectedSocket = webSocket,
                            cancelSocket = true,
                        )
                        return
                    }
                    "envelope" -> Unit
                    else -> return
                }
                val delivery = relayDelivery(envelope) ?: return
                try {
                    val sender = delivery.sender ?: return
                    val target = delivery.target ?: return
                    val sequence = delivery.sequence ?: return
                    val ciphertext = delivery.ciphertext ?: return
                    if (target != api.currentDeviceId()) return
                    val peer = deviceKeys[sender] ?: return
                    val plain = runCatching {
                        crypto.decrypt(
                            peer,
                            ciphertext,
                            delivery.messageId,
                            sender,
                            target,
                            sequence,
                        )
                    }.getOrNull() ?: return
                    if (plain["type"]?.jsonPrimitive?.content == "response") {
                        val response = plain["response"]?.jsonObject
                        val requestId = response?.get("request_id")?.jsonPrimitive?.content
                        if (requestId != null) {
                            pending.remove(requestId)?.complete(response)
                        }
                    }
                } finally {
                    // The relay cannot repair malformed, stale-key, or wrong-target
                    // deliveries. Acknowledge them so one bad record cannot poison
                    // the device's offline queue forever.
                    acknowledge(webSocket, delivery.messageId)
                }
            } catch (_: Exception) {
                // A malformed or unauthenticated relay frame must not stop the client.
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (registerSocket(webSocket, ready)) {
                disconnect(t, expectedSocket = webSocket, cancelSocket = false)
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (registerSocket(webSocket, ready)) {
                disconnect(
                    IOException("Relay connection closed: $reason"),
                    expectedSocket = webSocket,
                    cancelSocket = false,
                )
            }
        }
    }

    private fun acknowledge(webSocket: WebSocket, messageId: String) {
        if (messageId.isBlank()) return
        val ack = buildJsonObject {
            put("type", "ack")
            put("message_id", messageId)
        }
        webSocket.send(json.encodeToString(JsonObject.serializer(), ack))
    }

    companion object {
        private const val SEQUENCE = "relay_sequence"
    }
}
