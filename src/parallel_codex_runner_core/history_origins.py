"""Provenance for control inputs, without changing the native conversation."""

from __future__ import annotations

import json
import re
from pathlib import Path


RECOVERY_INPUT = "Continue the unfinished task from its saved progress, without restarting completed work."
RECOVERY_ORIGIN = "pcr_recovery"
RECOVERY_CONTENT_KIND = "additional_content.parallel_codex_runner_recovery"
RECOVERY_ID_PREFIX = "PCR recovery input ID: "


def retained_recovery_turns(cwd: str, session_id: str) -> set[str]:
    """Read only a matching PCR owner's retained journal, never arbitrary cwd logs.

    Older native versions emitted unchanged additionalContext only once. Their
    later recovery turns can still be proven by the original execution journal.
    Missing, oversized or mismatched evidence leaves input provenance unknown.
    """
    workspace = Path(cwd)
    if workspace.parent.name != "workspaces" or not re.fullmatch(r"agent_\d{3,}", workspace.name):
        return set()
    meta = workspace.parent.parent / "meta" / workspace.name
    try:
        receipt_path = meta / "execution_recovery.json"
        if receipt_path.stat().st_size > 1024 * 1024:
            return set()
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("version") != 1 or receipt.get("thread_id") != session_id or not receipt.get("identity"):
            return set()
        turns: set[str] = set()
        pending: str | None = None
        thread = ""
        remaining = 64 * 1024 * 1024
        with (meta / "stdout.log").open("rb") as stream:
            while remaining > 0:
                line = stream.readline(min(remaining, 1024 * 1024))
                if not line:
                    break
                remaining -= len(line)
                if not line.endswith(b"\n"):
                    # Never interpret the tail of an oversized tool result as
                    # an execution envelope.
                    while line and not line.endswith(b"\n") and remaining > 0:
                        line = stream.readline(min(remaining, 1024 * 1024))
                        remaining -= len(line)
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "thread.started":
                    thread = event.get("thread_id", "")
                    if thread != session_id:
                        pending = None
                elif (kind == "pcr.recovery" and event.get("thread_id") == session_id
                      and type(event.get("attempt")) is int and event["attempt"] > 0):
                    pending = event.get("turn_id")
                elif kind == "turn.started" and thread == session_id:
                    turn = event.get("turn_id")
                    if isinstance(turn, str) and turn and (
                        event.get("input_origin") == RECOVERY_ORIGIN or (pending and turn != pending)
                    ):
                        turns.add(turn)
                    pending = None
        return turns
    except (OSError, ValueError, TypeError, AttributeError):
        return set()
