package com.parallelcodexrunner.remote.data

import org.junit.Assert.assertEquals
import org.junit.Test

class SecureStoreTest {
    @Test
    fun identityMaterialFailureRequiresAConsistentReset() {
        assertEquals(
            true,
            secureStoreRecoveryResetsIdentity(setOf("relay_tokens")),
        )
        assertEquals(
            true,
            secureStoreRecoveryResetsIdentity(setOf("x25519_private_v1")),
        )
    }

    @Test
    fun corruptCachesCanBeIsolatedWithoutDroppingPairing() {
        assertEquals(
            false,
            secureStoreRecoveryResetsIdentity(
                setOf("mobile-client-state-v1", "android_update_history_mac-1"),
            ),
        )
    }
}
