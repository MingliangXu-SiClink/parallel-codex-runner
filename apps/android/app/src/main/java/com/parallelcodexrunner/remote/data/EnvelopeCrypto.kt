package com.parallelcodexrunner.remote.data

import android.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.bouncycastle.math.ec.rfc7748.X25519
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class EnvelopeCrypto(private val secureStore: SecureStore) {
    private val random = SecureRandom()
    private val privateKey: ByteArray by lazy {
        secureStore.get(PRIVATE_KEY) ?: ByteArray(X25519.SCALAR_SIZE).also {
            X25519.generatePrivateKey(random, it)
            secureStore.put(PRIVATE_KEY, it)
        }
    }

    val publicKey: String
        get() {
            val raw = ByteArray(X25519.POINT_SIZE)
            X25519.generatePublicKey(privateKey, 0, raw, 0)
            return raw.base64()
        }

    fun deviceLoginProof(
        challenge: DeviceLoginChallenge, deviceId: String, userId: String, relayUrl: String,
    ): String = createDeviceLoginProof(privateKey, publicKey, challenge, deviceId, userId, relayUrl)

    fun pairingProof(link: PairingLink, androidDeviceId: String): String {
        val secret = link.secret.base64Decode()
        require(secret.size == 32) { "Invalid pairing secret" }
        val context = buildJsonObject {
            put("android_device_id", androidDeviceId)
            put("android_public_key", publicKey)
            put("mac_device_id", link.macDeviceId)
            put("mac_public_key", link.macPublicKey)
            put("pairing_id", link.pairingId)
            put("version", 1)
        }
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret, "HmacSHA256"))
        val encoded = canonicalProtocolJson(context).toByteArray()
        return mac.doFinal(encoded).base64()
    }

    fun encrypt(
        peerPublicKey: String,
        plaintext: JsonObject,
        messageId: String,
        senderDeviceId: String,
        targetDeviceId: String,
        sequence: Long,
    ): String {
        val encodedPlaintext = canonicalProtocolJson(plaintext).toByteArray()
        require(encodedPlaintext.size <= MAX_PLAINTEXT_BYTES) {
            "Request is too large"
        }
        val nonce = ByteArray(12).also(random::nextBytes)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.ENCRYPT_MODE,
            SecretKeySpec(sharedKey(peerPublicKey), "AES"),
            GCMParameterSpec(128, nonce),
        )
        cipher.updateAAD(aad(messageId, senderDeviceId, targetDeviceId, sequence))
        val encrypted = cipher.doFinal(encodedPlaintext)
        val wire = buildJsonObject {
            put("ciphertext", encrypted.base64())
            put("nonce", nonce.base64())
            put("version", 1)
        }
        return canonicalProtocolJson(wire).toByteArray().base64()
    }

    fun decrypt(
        peerPublicKey: String,
        ciphertext: String,
        messageId: String,
        senderDeviceId: String,
        targetDeviceId: String,
        sequence: Long,
    ): JsonObject {
        val wire = Json.parseToJsonElement(ciphertext.base64Decode().toString(Charsets.UTF_8)) as JsonObject
        require(wire.getValue("version").jsonPrimitive.int == 1) { "Unsupported envelope version" }
        val nonce = wire.getValue("nonce").jsonPrimitive.content.base64Decode()
        require(nonce.size == 12) { "Invalid envelope nonce" }
        val encrypted = wire.getValue("ciphertext").jsonPrimitive.content.base64Decode()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(sharedKey(peerPublicKey), "AES"),
            GCMParameterSpec(128, nonce),
        )
        cipher.updateAAD(aad(messageId, senderDeviceId, targetDeviceId, sequence))
        val plain = cipher.doFinal(encrypted).toString(Charsets.UTF_8)
        return Json.parseToJsonElement(plain) as JsonObject
    }

    private fun sharedKey(peerEncoded: String): ByteArray {
        val peer = peerEncoded.base64Decode()
        require(peer.size == X25519.POINT_SIZE) { "Invalid peer public key" }
        val shared = ByteArray(X25519.POINT_SIZE)
        require(X25519.calculateAgreement(privateKey, 0, peer, 0, shared, 0)) {
            "Invalid peer public key"
        }
        val own = publicKey.base64Decode()
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update("pcr-remote-v1\u0000".toByteArray())
        listOf(own, peer)
            .sortedWith(Comparator { left, right -> compareUnsigned(left, right) })
            .forEach(digest::update)
        return hkdfSha256(shared, digest.digest(), "PCR Remote envelope version 1".toByteArray(), 32)
    }

    private fun aad(
        messageId: String,
        sender: String,
        target: String,
        sequence: Long,
    ): ByteArray {
        val value = buildJsonObject {
            put("message_id", messageId)
            put("sender_device_id", sender)
            put("sequence", sequence)
            put("target_device_id", target)
            put("version", 1)
        }
        return canonicalProtocolJson(value).toByteArray()
    }

    private fun ByteArray.base64(): String = Base64.encodeToString(this, Base64.NO_WRAP)

    private fun String.base64Decode(): ByteArray = Base64.decode(this, Base64.NO_WRAP)

    private fun compareUnsigned(left: ByteArray, right: ByteArray): Int {
        for (index in 0 until minOf(left.size, right.size)) {
            val comparison = (left[index].toInt() and 0xff).compareTo(right[index].toInt() and 0xff)
            if (comparison != 0) return comparison
        }
        return left.size.compareTo(right.size)
    }

    companion object {
        private const val PRIVATE_KEY = "x25519_private_v1"
        private const val MAX_PLAINTEXT_BYTES = 1024 * 1024
    }
}

internal fun hkdfSha256(ikm: ByteArray, salt: ByteArray, info: ByteArray, size: Int): ByteArray {
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(salt, "HmacSHA256"))
    val pseudoRandomKey = mac.doFinal(ikm)
    mac.init(SecretKeySpec(pseudoRandomKey, "HmacSHA256"))
    val result = ByteArray(size)
    var previous = ByteArray(0)
    var offset = 0
    var counter = 1
    while (offset < size) {
        mac.reset()
        mac.update(previous)
        mac.update(info)
        mac.update(counter.toByte())
        previous = mac.doFinal()
        val count = minOf(previous.size, size - offset)
        previous.copyInto(result, offset, 0, count)
        offset += count
        counter += 1
    }
    return result
}

internal fun canonicalProtocolJson(value: JsonElement): String =
    Json.encodeToString(JsonElement.serializer(), canonicalizeProtocolJson(value))

private fun canonicalizeProtocolJson(value: JsonElement): JsonElement = when (value) {
    is JsonObject -> JsonObject(
        value.entries
            .associate { (key, item) -> key to canonicalizeProtocolJson(item) }
            .toSortedMap()
    )
    is JsonArray -> JsonArray(value.map(::canonicalizeProtocolJson))
    else -> value
}
