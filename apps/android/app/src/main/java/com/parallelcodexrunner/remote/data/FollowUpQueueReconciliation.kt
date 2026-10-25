package com.parallelcodexrunner.remote.data

internal fun newestFollowUpSnapshot(incoming: RemoteFollowUpQueue?, previous: RemoteFollowUpQueue?): RemoteFollowUpQueue? =
    when {
        previous == null -> incoming
        incoming == null || incoming.generation < previous.generation -> previous
        else -> incoming
    }

internal fun reconcileFollowUpItems(
    remote: List<PersistedQueuedFollowUp>,
    local: List<PersistedQueuedFollowUp>,
    acknowledged: Set<String> = emptySet(),
    completedId: String? = null,
): List<PersistedQueuedFollowUp> {
    val known = remote.map { it.id }.toSet() + acknowledged
    return remote + local.filter {
        it.daemonAcknowledged != true && it.id !in known && it.id != completedId
    }
}
