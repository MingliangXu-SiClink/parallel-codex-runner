from __future__ import annotations


TERMINAL_AGENT_STATUSES = frozenset(
    {
        "success",
        "failed",
        "error",
        "killed",
        "stopped",
        "cancelled",
        "canceled",
        "interrupted",
        "discarded",
    }
)

TERMINAL_RUN_STATUSES = frozenset(
    {
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
    }
)
