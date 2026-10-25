package com.parallelcodexrunner.remote.data

import java.io.File
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceLoginProofTest {
    private val vector = generateSequence(File(requireNotNull(System.getProperty("user.dir")))) { it.parentFile }
        .map { File(it, "remote/protocol/device-login-v1-vector.json") }
        .first { it.isFile }
        .readText().let { Json.parseToJsonElement(it).jsonObject }
    private val challenge = Json.decodeFromJsonElement(DeviceLoginChallenge.serializer(), vector.getValue("challenge"))
    private val privateKey = Base64.getDecoder().decode(vector.getValue("device_private_key").jsonPrimitive.content)

    private fun proof(value: DeviceLoginChallenge = challenge): String = createDeviceLoginProof(
        privateKey, challenge.publicKey, value, challenge.deviceId, challenge.userId, challenge.relayUrl,
    )

    @Test
    fun matchesSharedPythonServerVector() {
        assertEquals(vector.getValue("proof").jsonPrimitive.content, proof())
    }

    @Test
    fun refusesDifferentDeviceAccountOriginPurposeOrKey() {
        for (invalid in listOf(
            challenge.copy(deviceId = "other"), challenge.copy(userId = "other"),
            challenge.copy(relayUrl = "https://different.example"), challenge.copy(purpose = "different"),
            challenge.copy(publicKey = "different"), challenge.copy(version = 2),
            challenge.copy(authorizationGeneration = -1), challenge.copy(challengeId = ""),
        )) {
            assertThrows(IllegalArgumentException::class.java) { proof(invalid) }
        }
    }

    @Test
    fun refusesInvalidNonceAndLowOrderPublicKey() {
        for (invalid in listOf(
            challenge.copy(nonce = "invalid"), challenge.copy(nonce = Base64.getEncoder().encodeToString(ByteArray(31))),
            challenge.copy(serverPublicKey = Base64.getEncoder().encodeToString(ByteArray(32))),
        )) {
            assertThrows(IllegalArgumentException::class.java) { proof(invalid) }
        }
    }

    @Test
    fun onlyExpiredOrMissingSessionMayAttemptDeviceLogin() {
        for (code in listOf("refresh_invalid", "refresh_expired", "")) {
            assertTrue(refreshAllowsDeviceLogin(RelayRequestException(401, "Test", code)))
        }
        for (code in listOf("refresh_revoked", "device_inactive", "device_unknown", "user_inactive")) {
            assertFalse(refreshAllowsDeviceLogin(RelayRequestException(401, "Test", code)))
        }
        for (status in listOf(429, 500, 502, 503)) {
            assertFalse(refreshAllowsDeviceLogin(RelayRequestException(status, "Test", "refresh_invalid")))
        }
    }
}
