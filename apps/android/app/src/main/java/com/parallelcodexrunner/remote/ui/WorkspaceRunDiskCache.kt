package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.SecureStore
import com.parallelcodexrunner.remote.data.generated.ConversationRecord
import java.io.File
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.BasicFileAttributes
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

private const val WORKSPACE_RUN_CACHE_SCHEMA_VERSION = 1

@Serializable
internal data class WorkspaceRunCacheEnvelope(
    val schemaVersion: Int = WORKSPACE_RUN_CACHE_SCHEMA_VERSION,
    val macId: String,
    val workspaceId: String,
    val runId: String,
    val updatedAtMillis: Long,
    val snapshot: WorkspaceRunSnapshot,
) {
    val key: WorkspaceRunKey
        get() = WorkspaceRunKey(macId, workspaceId, runId)
}

@Serializable
private data class WorkspaceConversationIndexEnvelope(
    val schemaVersion: Int = WORKSPACE_RUN_CACHE_SCHEMA_VERSION,
    val macId: String,
    val workspaceId: String,
    val updatedAtMillis: Long,
    val conversations: List<ConversationRecord>,
)

@Serializable
private data class CachedRunRevision(val size: Long, val modified: Long, val fileKey: String?)

@Serializable
private data class CachedRunIdentity(
    val macId: String,
    val workspaceId: String,
    val runId: String,
    val conversationId: String,
    val updatedAtMillis: Long,
    val revision: CachedRunRevision,
) {
    val key: WorkspaceRunKey get() = WorkspaceRunKey(macId, workspaceId, runId)
}

@Serializable
private data class WorkspaceRunCatalog(
    val schemaVersion: Int = WORKSPACE_RUN_CACHE_SCHEMA_VERSION,
    val runs: List<CachedRunIdentity>,
)

internal data class CachedWorkspaceConversation(
    val key: WorkspaceRunKey,
    val snapshot: WorkspaceRunSnapshot,
)

/**
 * An encrypted, whole-conversation cache. Entries are evicted as complete runs;
 * transcript text is never shortened to meet the cache budget.
 */
internal class WorkspaceRunDiskCache(
    private val directory: File,
    encryptionKey: ByteArray,
    private val capacity: Int = DEFAULT_CAPACITY,
    private val maximumBytes: Long = DEFAULT_MAXIMUM_BYTES,
    private val dispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val encodeRecord: (WorkspaceRunCacheEnvelope) -> ByteArray = ::encodeRecord,
) {
    private data class PendingWrite(
        val token: Any,
        val snapshot: WorkspaceRunSnapshot,
        val job: Job,
    )

    private val secretKey = SecretKeySpec(encryptionKey.copyOf(), "AES")
    private val ioMutex = Mutex()
    private val writerMutex = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val pendingWrites = ConcurrentHashMap<WorkspaceRunKey, PendingWrite>()
    private var catalog: MutableMap<String, CachedRunIdentity>? = null
    private val accessTimes = mutableMapOf<String, Long>()
    internal var recordDecodeCount = 0
        private set
    private val json = Json {
        encodeDefaults = true
        ignoreUnknownKeys = true
    }

    init {
        require(encryptionKey.size == CACHE_KEY_BYTES) {
            "Workspace conversation cache key must be $CACHE_KEY_BYTES bytes"
        }
        require(capacity > 0) { "Workspace conversation cache capacity must be positive" }
        require(maximumBytes > 0) { "Workspace conversation cache size must be positive" }
    }

    fun scheduleWrite(key: WorkspaceRunKey, snapshot: WorkspaceRunSnapshot) {
        pendingWrites.remove(key)?.job?.cancel()
        val token = Any()
        val job = scope.launch {
            delay(WRITE_DEBOUNCE_MILLIS)
            try {
                writeNow(key, snapshot)
            } catch (_: Throwable) {
                // Conversation caching must not interrupt the live client.
            } finally {
                pendingWrites.computeIfPresent(key) { _, pending ->
                    pending.takeUnless { it.token === token }
                }
            }
        }
        pendingWrites[key] = PendingWrite(token, snapshot, job)
    }

    /**
     * Moves debounced writes onto the cache-owned scope before the Activity is
     * stopped. The writes therefore survive ViewModel cancellation without
     * blocking Android's main thread.
     */
    fun flushScheduledWrites() {
        val writes = pendingWrites.entries.mapNotNull { (key, pending) ->
            if (pendingWrites.remove(key, pending)) {
                pending.job.cancel()
                key to pending.snapshot
            } else {
                null
            }
        }
        writes.forEach { (key, snapshot) ->
            scope.launch {
                try {
                    writeNow(key, snapshot)
                } catch (_: Throwable) {
                    // The authoritative pcrd history remains available.
                }
            }
        }
    }

    suspend fun load(key: WorkspaceRunKey): WorkspaceRunSnapshot? = withContext(dispatcher) {
        ioMutex.withLock {
            val file = fileFor(key)
            val envelope = readEnvelope(file, expectedKey = key) ?: return@withLock null
            accessTimes[file.name] = System.currentTimeMillis()
            envelope.snapshot
        }
    }

    suspend fun loadConversation(
        macId: String,
        workspaceId: String,
        conversationId: String,
    ): CachedWorkspaceConversation? = withContext(dispatcher) {
        ioMutex.withLock {
            val envelope = reconciledCatalog().values
                .asSequence()
                .filter {
                    it.macId == macId &&
                        it.workspaceId == workspaceId &&
                        it.conversationId == conversationId
                }
                .sortedWith(
                    compareBy<CachedRunIdentity> { it.updatedAtMillis }
                        .thenBy { it.runId }
                        .reversed()
                )
                .mapNotNull { identity ->
                    readEnvelope(fileFor(identity.key), identity.key)
                        ?.takeIf { it.snapshot.conversationId == conversationId }
                }
                .firstOrNull()
                ?: return@withLock null
            accessTimes[fileFor(envelope.key).name] = System.currentTimeMillis()
            CachedWorkspaceConversation(envelope.key, envelope.snapshot)
        }
    }

    suspend fun loadConversationIndex(
        macId: String,
        workspaceId: String,
    ): List<ConversationRecord>? = withContext(dispatcher) {
        ioMutex.withLock {
            val file = conversationIndexFileFor(macId, workspaceId)
            val envelope = readConversationIndexEnvelope(file)
                ?: return@withLock null
            if (envelope.macId != macId || envelope.workspaceId != workspaceId) {
                file.delete()
                return@withLock null
            }
            file.setLastModified(System.currentTimeMillis())
            envelope.conversations
        }
    }

    suspend fun writeConversationIndex(
        macId: String,
        workspaceId: String,
        conversations: List<ConversationRecord>,
    ) {
        withContext(dispatcher) {
            writerMutex.withLock {
                directory.mkdirs()
                val target = conversationIndexFileFor(macId, workspaceId)
                val envelope = WorkspaceConversationIndexEnvelope(
                    macId = macId,
                    workspaceId = workspaceId,
                    updatedAtMillis = System.currentTimeMillis(),
                    conversations = conversations,
                )
                val temporary = prepareEncryptedWrite(
                    target,
                    json.encodeToString(
                        WorkspaceConversationIndexEnvelope.serializer(),
                        envelope,
                    ).toByteArray(Charsets.UTF_8),
                )
                try {
                    ensureActive()
                    ioMutex.withLock { publishPreparedWrite(temporary, target) }
                } finally { temporary.delete() }
            }
        }
    }

    suspend fun writeNow(key: WorkspaceRunKey, snapshot: WorkspaceRunSnapshot) {
        withContext(dispatcher) {
            writerMutex.withLock {
                directory.mkdirs()
                val envelope = WorkspaceRunCacheEnvelope(
                    macId = key.macId,
                    workspaceId = key.workspaceId,
                    runId = key.runId,
                    updatedAtMillis = System.currentTimeMillis(),
                    snapshot = snapshot,
                )
                // Large JSON/encryption work must not hold the navigation read
                // lock. Writers and removals remain ordered through publication.
                val plainText = encodeRecord(envelope)
                val target = fileFor(key)
                val temporary = prepareEncryptedWrite(target, plainText)
                try {
                    ensureActive()
                    ioMutex.withLock {
                        publishPreparedWrite(temporary, target)
                        pruneUnlocked(protectedFile = target)
                        rememberIdentity(target, envelope)
                        persistCatalog()
                    }
                } finally { temporary.delete() }
            }
        }
    }

    fun scheduleRemoveWhere(predicate: (WorkspaceRunKey) -> Boolean) {
        pendingWrites.entries
            .filter { predicate(it.key) }
            .forEach { (key, pending) ->
                if (pendingWrites.remove(key, pending)) pending.job.cancel()
            }
        scope.launch { removeWhereNow(predicate) }
    }

    suspend fun removeWhereNow(predicate: (WorkspaceRunKey) -> Boolean) {
        withContext(dispatcher) {
            writerMutex.withLock {
                ioMutex.withLock {
                    reconciledCatalog().values.toList().forEach { identity ->
                        if (predicate(identity.key)) removeRunFile(fileFor(identity.key))
                    }
                    persistCatalog()
                    conversationIndexFiles().forEach { file ->
                        val envelope = readConversationIndexEnvelope(file) ?: return@forEach
                        val indexKey = WorkspaceRunKey(
                            envelope.macId,
                            envelope.workspaceId,
                            CONVERSATION_INDEX_SENTINEL_RUN_ID,
                        )
                        if (predicate(indexKey)) file.delete()
                    }
                }
            }
        }
    }

    fun scheduleClear() {
        pendingWrites.values.forEach { it.job.cancel() }
        pendingWrites.clear()
        scope.launch { clearNow() }
    }

    suspend fun clearNow() {
        withContext(dispatcher) {
            writerMutex.withLock {
                ioMutex.withLock {
                    directory.listFiles().orEmpty().forEach(File::delete)
                    catalog = mutableMapOf()
                    accessTimes.clear()
                }
            }
        }
    }

    internal fun cacheFileForTest(key: WorkspaceRunKey): File = fileFor(key)

    internal fun conversationIndexFileForTest(macId: String, workspaceId: String): File =
        conversationIndexFileFor(macId, workspaceId)

    internal fun catalogFileForTest(): File = catalogFile()

    private fun revision(file: File): CachedRunRevision? = runCatching {
        val attributes = Files.readAttributes(file.toPath(), BasicFileAttributes::class.java,
            LinkOption.NOFOLLOW_LINKS)
        if (!attributes.isRegularFile) return null
        CachedRunRevision(attributes.size(), attributes.lastModifiedTime().to(TimeUnit.NANOSECONDS),
            attributes.fileKey()?.toString())
    }.getOrNull()

    private fun catalogFile() = File(directory, "runs.pcrcacheindex")

    private fun loadedCatalog(): MutableMap<String, CachedRunIdentity> {
        catalog?.let { return it }
        val saved = runCatching {
            json.decodeFromString(WorkspaceRunCatalog.serializer(),
                decrypt(catalogFile().readBytes()).toString(Charsets.UTF_8))
                .also { require(it.schemaVersion == WORKSPACE_RUN_CACHE_SCHEMA_VERSION) }
        }.getOrNull()
        return saved?.runs.orEmpty().associateBy { fileFor(it.key).name }.toMutableMap()
            .also { catalog = it }
    }

    private fun rememberIdentity(file: File, envelope: WorkspaceRunCacheEnvelope,
                                 current: CachedRunRevision? = revision(file)) {
        if (current == null) return
        loadedCatalog()[file.name] = CachedRunIdentity(envelope.macId, envelope.workspaceId,
            envelope.runId, envelope.snapshot.conversationId, envelope.updatedAtMillis, current)
    }

    private fun reconciledCatalog(): MutableMap<String, CachedRunIdentity> {
        val identities = loadedCatalog()
        val files = cacheFiles()
        val names = files.mapTo(mutableSetOf(), File::getName)
        var changed = identities.keys.retainAll(names)
        accessTimes.keys.retainAll(names)
        // Only a legacy entry or an interrupted/external replacement needs a
        // full decode. Normal cold navigation reads the encrypted identity index
        // and the selected conversation, not every cached transcript.
        files.forEach { file ->
            if (identities[file.name]?.revision != revision(file)) {
                identities.remove(file.name)
                readEnvelope(file, expectedKey = null)
                changed = true
            }
        }
        if (changed) persistCatalog()
        return identities
    }

    private fun persistCatalog() {
        val entries = loadedCatalog().values.toList()
        // The catalog is rebuildable. A crash between record and index writes
        // is detected by the file revision on the next read.
        runCatching {
            directory.mkdirs()
            val target = catalogFile()
            val temporary = prepareEncryptedWrite(target, json.encodeToString(
                WorkspaceRunCatalog.serializer(), WorkspaceRunCatalog(runs = entries),
            ).toByteArray(Charsets.UTF_8))
            publishPreparedWrite(temporary, target)
        }
    }

    private fun removeRunFile(file: File) {
        file.delete()
        catalog?.remove(file.name)
        accessTimes.remove(file.name)
    }

    private fun readEnvelope(
        file: File,
        expectedKey: WorkspaceRunKey?,
    ): WorkspaceRunCacheEnvelope? {
        val before = revision(file) ?: return null
        return runCatching {
            recordDecodeCount += 1
            val plainText = decrypt(file.readBytes())
            val envelope = json.decodeFromString(
                WorkspaceRunCacheEnvelope.serializer(),
                plainText.toString(Charsets.UTF_8),
            )
            require(envelope.schemaVersion == WORKSPACE_RUN_CACHE_SCHEMA_VERSION)
            if (expectedKey != null) require(envelope.key == expectedKey)
            require(fileFor(envelope.key) == file)
            if (revision(file) != before) return null
            envelope.copy(snapshot = envelope.snapshot.recoverLegacyReadErrors()).also {
                rememberIdentity(file, it, before)
            }
        }.getOrElse {
            // A partial write, old schema, or rotated secure-store key must not
            // make opening the workspace fail repeatedly.
            if (revision(file) == before) removeRunFile(file)
            null
        }
    }

    private fun readConversationIndexEnvelope(
        file: File,
    ): WorkspaceConversationIndexEnvelope? {
        if (!file.isFile) return null
        return runCatching {
            val plainText = decrypt(file.readBytes())
            json.decodeFromString(
                WorkspaceConversationIndexEnvelope.serializer(),
                plainText.toString(Charsets.UTF_8),
            ).also { require(it.schemaVersion == WORKSPACE_RUN_CACHE_SCHEMA_VERSION) }
        }.getOrElse {
            file.delete()
            null
        }
    }

    private fun prepareEncryptedWrite(target: File, plainText: ByteArray): File {
        val temporary = File(directory, ".${target.name}.${System.nanoTime()}.tmp")
        try {
            temporary.writeBytes(encrypt(plainText))
            return temporary
        } catch (error: Throwable) {
            temporary.delete()
            throw error
        }
    }

    private fun publishPreparedWrite(temporary: File, target: File) {
        try {
            Files.move(
                temporary.toPath(),
                target.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(
                temporary.toPath(),
                target.toPath(),
                StandardCopyOption.REPLACE_EXISTING,
            )
        } finally {
            temporary.delete()
        }
        target.setLastModified(System.currentTimeMillis())
    }

    private fun encrypt(plainText: ByteArray): ByteArray {
        val nonce = ByteArray(GCM_NONCE_BYTES).also(SecureRandom()::nextBytes)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey, GCMParameterSpec(128, nonce))
        cipher.updateAAD(CACHE_FILE_AAD)
        val cipherText = cipher.doFinal(plainText)
        return byteArrayOf(CACHE_FILE_VERSION.toByte()) + nonce + cipherText
    }

    private fun decrypt(encrypted: ByteArray): ByteArray {
        require(encrypted.size > 1 + GCM_NONCE_BYTES)
        require(encrypted[0].toInt() == CACHE_FILE_VERSION)
        val nonce = encrypted.copyOfRange(1, 1 + GCM_NONCE_BYTES)
        val cipherText = encrypted.copyOfRange(1 + GCM_NONCE_BYTES, encrypted.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, secretKey, GCMParameterSpec(128, nonce))
        cipher.updateAAD(CACHE_FILE_AAD)
        return cipher.doFinal(cipherText)
    }

    private fun fileFor(key: WorkspaceRunKey): File {
        val digest = cacheNameDigest(key.encoded)
        return File(directory, "$digest$CACHE_FILE_SUFFIX")
    }

    private fun conversationIndexFileFor(macId: String, workspaceId: String): File {
        val digest = cacheNameDigest("$macId\u001f$workspaceId")
        return File(directory, "$digest$CONVERSATION_INDEX_FILE_SUFFIX")
    }

    private fun cacheNameDigest(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun cacheFiles(): List<File> = directory.listFiles { file ->
        file.isFile && file.name.endsWith(CACHE_FILE_SUFFIX)
    }.orEmpty().toList()

    private fun conversationIndexFiles(): List<File> = directory.listFiles { file ->
        file.isFile && file.name.endsWith(CONVERSATION_INDEX_FILE_SUFFIX)
    }.orEmpty().toList()

    private fun pruneUnlocked(protectedFile: File) {
        val newestFirst = cacheFiles().sortedByDescending { accessTimes[it.name] ?: it.lastModified() }
        var retained = 0
        var retainedBytes = 0L
        newestFirst.forEach { file ->
            val size = file.length()
            val mustKeep = file == protectedFile
            val fits = retained < capacity && retainedBytes + size <= maximumBytes
            if (mustKeep || fits) {
                retained += 1
                retainedBytes += size
            } else {
                removeRunFile(file)
            }
        }
    }

    companion object {
        private val recordJson = Json { encodeDefaults = true }

        internal fun encodeRecord(record: WorkspaceRunCacheEnvelope): ByteArray = recordJson
            .encodeToString(WorkspaceRunCacheEnvelope.serializer(), record).toByteArray(Charsets.UTF_8)

        private const val CACHE_FILE_VERSION = 1
        private const val CACHE_KEY_BYTES = 32
        private const val GCM_NONCE_BYTES = 12
        private val CACHE_FILE_AAD = "parallel-codex-workspace-run-cache-v1"
            .toByteArray(Charsets.UTF_8)
        private const val CACHE_FILE_SUFFIX = ".pcrconversation"
        private const val CONVERSATION_INDEX_FILE_SUFFIX = ".pcrconversationindex"
        private const val CONVERSATION_INDEX_SENTINEL_RUN_ID = "conversation-index"
        private const val CACHE_KEY_SECURE_STORE_ENTRY = "workspace_run_cache_key_v1"
        private const val WRITE_DEBOUNCE_MILLIS = 300L
        private const val DEFAULT_CAPACITY = 64
        private const val DEFAULT_MAXIMUM_BYTES = 256L * 1024 * 1024

        fun create(directory: File, secureStore: SecureStore): WorkspaceRunDiskCache {
            val key = synchronized(secureStore) {
                secureStore.get(CACHE_KEY_SECURE_STORE_ENTRY) ?: ByteArray(CACHE_KEY_BYTES)
                    .also(SecureRandom()::nextBytes)
                    .also { secureStore.put(CACHE_KEY_SECURE_STORE_ENTRY, it) }
            }
            return WorkspaceRunDiskCache(directory, key)
        }
    }
}
