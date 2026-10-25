package com.parallelcodexrunner.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PairingCodeTest {
    @Test
    fun addingAMacToTheSameRelayAccountKeepsLocalWorkspaceState() {
        assertEquals(
            true,
            pairingRetainsAccount(
                previousRelayUrl = "https://relay.example",
                previousUserId = "user-1",
                nextRelayUrl = "https://relay.example",
                nextUserId = "user-1",
            ),
        )
        assertEquals(
            false,
            pairingRetainsAccount(
                previousRelayUrl = "https://old-relay.example",
                previousUserId = "user-1",
                nextRelayUrl = "https://new-relay.example",
                nextUserId = "user-1",
            ),
        )
    }

    private val currentFormat = buildString {
        append("pcr://pair?")
        append("relay=https%3A%2F%2F8.153.193.1&")
        append("pairing=pair-123&")
        append("secret=abc%2Bdef%2Fghi%3D&")
        append("auth=token%2Bvalue%3D&")
        append("mac=mac-123&")
        append("mac_key=key%2Bvalue%3D")
    }

    @Test
    fun parsesTheMacPairingFormat() {
        val link = PairingCode.parse(currentFormat)

        assertEquals("https://8.153.193.1", link.relayUrl)
        assertEquals("pair-123", link.pairingId)
        assertEquals("abc+def/ghi=", link.secret)
        assertEquals("token+value=", link.approvalToken)
        assertEquals("mac-123", link.macDeviceId)
        assertEquals("key+value=", link.macPublicKey)
    }

    @Test
    fun acceptsCommonScannerWrappersAndAlreadyDecodedBase64() {
        val wrapped = "\uFEFF URI: `" +
            currentFormat.replace("%2B", "+", ignoreCase = true) + "`\n"

        assertEquals("abc+def/ghi=", PairingCode.parse(wrapped).secret)
    }

    @Test
    fun rejectsIncompleteOrInsecureCodes() {
        assertThrows(IllegalArgumentException::class.java) {
            PairingCode.parse("pcr://pair?relay=https%3A%2F%2Frelay.test")
        }
        assertThrows(IllegalArgumentException::class.java) {
            PairingCode.parse(currentFormat.replace("https%3A", "http%3A"))
        }
    }

    @Test
    fun normalizesRelayAddressesForStableComparison() {
        assertEquals(
            "https://relay.example:8443/api",
            normalizeRelayBaseUrl("HTTPS://Relay.Example:8443/api/"),
        )
        assertEquals("http://localhost:9000", normalizeRelayBaseUrl("http://LOCALHOST:9000/"))
    }
}
