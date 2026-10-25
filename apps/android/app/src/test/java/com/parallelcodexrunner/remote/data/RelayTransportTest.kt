package com.parallelcodexrunner.remote.data

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RelayTransportTest {
    @Test
    fun defaultRelayClientDetectsStaleWebSockets() {
        val client = defaultRelayHttpClient()

        assertEquals(20_000, client.pingIntervalMillis)
        assertEquals(15_000, client.connectTimeoutMillis)
        assertEquals(15_000, client.writeTimeoutMillis)
    }

    @Test
    fun malformedSequenceStillRetainsMessageIdForAcknowledgement() {
        val delivery = relayDelivery(buildJsonObject {
            put("message_id", "message-1")
            put("sender_device_id", "mac-1")
            put("target_device_id", "android-1")
            put("sequence", "not-a-number")
            put("ciphertext", "ciphertext")
        })

        assertEquals("message-1", delivery?.messageId)
        assertNull(delivery?.sequence)
    }

    @Test
    fun invalidNestedFieldsDoNotPreventDeliveryAcknowledgementMetadata() {
        val delivery = relayDelivery(buildJsonObject {
            put("message_id", "message-2")
            put("sender_device_id", buildJsonObject { put("invalid", true) })
            put("sequence", 12)
        })

        assertEquals("message-2", delivery?.messageId)
        assertNull(delivery?.sender)
        assertEquals(12L, delivery?.sequence)
    }

    @Test
    fun frameWithoutMessageIdCannotBeAcknowledged() {
        assertNull(relayDelivery(buildJsonObject { put("sequence", 1) }))
    }
}
