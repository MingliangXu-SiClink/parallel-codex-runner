package com.parallelcodexrunner.remote.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class SecureStoreRecoveryReport(
    val resetIdentity: Boolean,
    val recoveredEntryCount: Int,
)

class SecureStore(context: Context) {
    private val preferences = context.getSharedPreferences("pcr_remote_secure", Context.MODE_PRIVATE)
    private val recoveryPreferences = context.getSharedPreferences(
        "pcr_remote_secure_recovery",
        Context.MODE_PRIVATE,
    )
    private val lock = Any()
    private val plaintextCache = mutableMapOf<String, ByteArray>()
    private var cachedMasterKey: SecretKey? = null
    private var prepared = false

    fun prepare(): SecureStoreRecoveryReport? = synchronized(lock) {
        if (prepared) return@synchronized null
        val entries = preferences.all
        if (entries.isEmpty()) {
            prepared = true
            return@synchronized null
        }
        val key = runCatching { existingMasterKey() }.getOrNull()
            ?: return@synchronized recover(entries, resetIdentity = true).also {
                prepared = true
            }
        cachedMasterKey = key
        val readable = mutableMapOf<String, ByteArray>()
        val unreadable = entries.filter { (entryKey, rawValue) ->
            val encoded = rawValue as? String
            if (encoded == null) {
                true
            } else {
                runCatching { decrypt(entryKey, encoded, key) }
                    .onSuccess { readable[entryKey] = it }
                    .isFailure
            }
        }
        plaintextCache.clear()
        plaintextCache.putAll(readable)
        if (unreadable.isEmpty()) {
            prepared = true
            return@synchronized null
        }
        val resetIdentity = secureStoreRecoveryResetsIdentity(unreadable.keys)
        recover(
            if (resetIdentity) entries else unreadable,
            resetIdentity = resetIdentity,
        ).also { prepared = true }
    }

    private fun existingMasterKey(): SecretKey? {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return store.getKey(MASTER_KEY_ALIAS, null) as? SecretKey
    }

    private fun masterKey(): SecretKey {
        cachedMasterKey?.let { return it }
        existingMasterKey()?.let {
            cachedMasterKey = it
            return it
        }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(
                KeyGenParameterSpec.Builder(
                    MASTER_KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
            generateKey()
        }.also { cachedMasterKey = it }
    }

    fun put(key: String, value: ByteArray) = synchronized(lock) {
        val stableValue = value.copyOf()
        check(preferences.edit().putString(key, encrypt(key, stableValue)).commit()) {
            "Unable to persist encrypted PCR state"
        }
        plaintextCache[key] = stableValue
    }

    fun putStrings(values: Map<String, String>, removeKeys: Set<String> = emptySet()) = synchronized(lock) {
        require(values.keys.intersect(removeKeys).isEmpty())
        val stableValues = values.mapValues { it.value.toByteArray(Charsets.UTF_8) }
        val editor = preferences.edit()
        stableValues.forEach { (key, value) -> editor.putString(key, encrypt(key, value)) }
        removeKeys.forEach(editor::remove)
        check(editor.commit()) { "Unable to persist encrypted PCR state" }
        plaintextCache.putAll(stableValues)
        removeKeys.forEach(plaintextCache::remove)
    }

    private fun encrypt(key: String, value: ByteArray): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, masterKey())
        cipher.updateAAD(key.toByteArray(Charsets.UTF_8))
        val encrypted = cipher.doFinal(value)
        val packed = ByteArray(1 + cipher.iv.size + encrypted.size)
        packed[0] = cipher.iv.size.toByte()
        cipher.iv.copyInto(packed, 1)
        encrypted.copyInto(packed, 1 + cipher.iv.size)
        return Base64.encodeToString(packed, Base64.NO_WRAP)
    }

    fun get(key: String): ByteArray? = synchronized(lock) {
        plaintextCache[key]?.let { return@synchronized it.copyOf() }
        val encoded = preferences.getString(key, null) ?: return null
        decrypt(key, encoded, masterKey()).also {
            plaintextCache[key] = it
        }.copyOf()
    }

    fun putString(key: String, value: String) = put(key, value.toByteArray(Charsets.UTF_8))

    fun getString(key: String): String? = get(key)?.toString(Charsets.UTF_8)

    fun remove(key: String) = synchronized(lock) {
        check(preferences.edit().remove(key).commit()) {
            "Unable to remove encrypted PCR state"
        }
        plaintextCache.remove(key)
    }

    private fun decrypt(key: String, encoded: String, masterKey: SecretKey): ByteArray {
        val packed = Base64.decode(encoded, Base64.NO_WRAP)
        require(packed.isNotEmpty()) { "Encrypted PCR state is empty" }
        val nonceSize = packed.first().toInt() and 0xff
        require(nonceSize == 12 && packed.size > nonceSize + 1) {
            "Encrypted PCR state has an invalid nonce"
        }
        val nonce = packed.copyOfRange(1, 1 + nonceSize)
        val encrypted = packed.copyOfRange(1 + nonceSize, packed.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, masterKey, GCMParameterSpec(128, nonce))
        cipher.updateAAD(key.toByteArray(Charsets.UTF_8))
        return cipher.doFinal(encrypted)
    }

    private fun recover(
        entries: Map<String, *>,
        resetIdentity: Boolean,
    ): SecureStoreRecoveryReport {
        val recoveryID = "${System.currentTimeMillis()}-${UUID.randomUUID()}"
        val backup = recoveryPreferences.edit()
            .putBoolean("$recoveryID.reset_identity", resetIdentity)
        entries.forEach { (entryKey, rawValue) ->
            backup.putString("$recoveryID.entry.$entryKey", rawValue?.toString().orEmpty())
        }
        check(backup.commit()) { "Unable to preserve unreadable PCR state" }
        val editor = preferences.edit()
        if (resetIdentity) editor.clear() else entries.keys.forEach(editor::remove)
        check(editor.commit()) { "Unable to recover unreadable PCR state" }
        if (resetIdentity) {
            plaintextCache.clear()
            val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (store.containsAlias(MASTER_KEY_ALIAS)) store.deleteEntry(MASTER_KEY_ALIAS)
            cachedMasterKey = null
        } else {
            entries.keys.forEach(plaintextCache::remove)
        }
        return SecureStoreRecoveryReport(
            resetIdentity = resetIdentity,
            recoveredEntryCount = entries.size,
        )
    }

    companion object {
        private const val MASTER_KEY_ALIAS = "pcr_remote_master_v1"
    }
}

internal fun secureStoreRecoveryResetsIdentity(keys: Set<String>): Boolean = keys.any {
    it in setOf(
        "x25519_private_v1",
        "relay_tokens",
        "relay_url",
        "relay_sequence",
    )
}
