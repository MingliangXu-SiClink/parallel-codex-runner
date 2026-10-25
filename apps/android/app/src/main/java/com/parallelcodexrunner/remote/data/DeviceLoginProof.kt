package com.parallelcodexrunner.remote.data

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.bouncycastle.math.ec.rfc7748.X25519
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

internal fun createDeviceLoginProof(
    privateKey: ByteArray, publicKey: String, challenge: DeviceLoginChallenge,
    deviceId: String, userId: String, relayUrl: String,
): String {
    require(
        challenge.version == 1 && challenge.purpose == "session-bootstrap" &&
            challenge.deviceId == deviceId && challenge.userId == userId &&
            challenge.publicKey == publicKey && challenge.relayUrl == relayUrl.trimEnd('/') &&
            challenge.authorizationGeneration >= 0 && challenge.challengeId.isNotBlank()
    ) { "Device sign-in challenge does not match this device" }
    val decoder = Base64.getDecoder()
    val peer = decoder.decode(challenge.serverPublicKey)
    val nonce = decoder.decode(challenge.nonce)
    require(peer.size == 32 && nonce.size == 32 && privateKey.size == 32) {
        "Invalid device sign-in challenge"
    }
    val shared = ByteArray(X25519.POINT_SIZE)
    require(X25519.calculateAgreement(privateKey, 0, peer, 0, shared, 0)) {
        "Invalid device sign-in public key"
    }
    val key = hkdfSha256(shared, nonce, "PCR Relay session bootstrap v1".toByteArray(), 32)
    val transcript = buildJsonObject {
        put("authorization_generation", challenge.authorizationGeneration)
        put("challenge_id", challenge.challengeId)
        put("device_id", challenge.deviceId)
        put("nonce", challenge.nonce)
        put("public_key", challenge.publicKey)
        put("purpose", challenge.purpose)
        put("relay_url", challenge.relayUrl)
        put("server_public_key", challenge.serverPublicKey)
        put("user_id", challenge.userId)
        put("version", challenge.version)
    }
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(key, "HmacSHA256"))
    return Base64.getEncoder().encodeToString(mac.doFinal(canonicalProtocolJson(transcript).toByteArray()))
}
