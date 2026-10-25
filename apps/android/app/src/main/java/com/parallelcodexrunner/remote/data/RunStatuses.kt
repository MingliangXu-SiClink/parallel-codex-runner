package com.parallelcodexrunner.remote.data

internal val TERMINAL_RUN_STATUSES = setOf(
    "completed",
    "failed",
    "error",
    "finalized",
    "discarded",
    "stopped",
    "cancelled",
    "canceled",
    "interrupted",
    "expired",
    "cleanup_failed",
    "sync_ambiguous",
)

internal val TERMINAL_AGENT_STATUSES = setOf(
    "success",
    "failed",
    "error",
    "killed",
    "stopped",
    "cancelled",
    "canceled",
    "interrupted",
    "discarded",
)

internal val UNSUCCESSFUL_AGENT_STATUSES = TERMINAL_AGENT_STATUSES - "success"

internal fun isTerminalRunStatusValue(value: String): Boolean =
    value.trim().lowercase() in TERMINAL_RUN_STATUSES

internal fun isTerminalAgentStatusValue(value: String): Boolean =
    value.trim().lowercase() in TERMINAL_AGENT_STATUSES
