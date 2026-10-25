"""Observed Agent activity, independent of run ownership and external job health."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .conversation_protocol import AgentActivityRecord
from .work_operations import tool_item, wait_deadline


@dataclass
class AgentActivityProjection:
    state: str = "unknown"
    observed_at: str | None = None
    last_output_at: str | None = None
    operations: set[str] = field(default_factory=set)
    waits: dict[str, str | None] = field(default_factory=dict)
    output_sizes: dict[str, int] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)

    def observe(self, event: dict[str, Any]) -> None:
        if event.get("type") != "agent_line" or event.get("stream", "stdout") != "stdout":
            return
        try:
            payload = json.loads(event.get("text") or "")
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") in {"turn.completed", "turn.failed"}:
            self.closed.update(self.operations)
            self.closed.update(self.waits)
            self.operations.clear()
            self.waits.clear()
            self.state = "awaiting_event"
            self.observed_at = str(event.get("timestamp") or "") or self.observed_at
            return
        if payload.get("type") not in {"item.started", "item.updated", "item.completed"}:
            return
        item = tool_item(payload)
        if item is None:
            return
        kind, identifier = item.get("type"), str(item.get("id") or "")
        timestamp = str(event.get("timestamp") or "") or None
        ended = payload["type"] == "item.completed" or item.get("status") in {"completed", "failed", "cancelled"}
        if kind in {"sleep", "command_execution", "file_change", "web_search", "tool_call"} and identifier:
            # Replayed or delayed starts must not reopen an already ended call.
            if not ended and identifier in self.closed:
                return
            if kind == "sleep":
                if ended:
                    self.waits.pop(identifier, None)
                elif identifier not in self.waits:
                    self.waits[identifier] = wait_deadline(timestamp, item.get("durationMs"))
            elif ended:
                self.operations.discard(identifier)
            else:
                self.operations.add(identifier)
            if ended:
                self.closed.add(identifier)
            operation = event.get("pcr_work_operation") or {}
            size = operation.get("output_bytes") if isinstance(operation, dict) else None
            if type(size) is int and size > self.output_sizes.get(identifier, 0):
                self.output_sizes[identifier] = size
                self.last_output_at = timestamp
            elif item.get("output_delta") or item.get("aggregated_output"):
                self.last_output_at = timestamp
            self.state = "awaiting_event" if ended else "executing"
        elif kind in {"reasoning", "agent_message"}:
            self.state = "thinking" if kind == "reasoning" else "responding"
            if kind == "agent_message" and item.get("text"):
                self.last_output_at = timestamp
        else:
            return
        self.observed_at = timestamp

    def finish(self, timestamp: str) -> None:
        self.state = "finished"
        self.observed_at = timestamp or self.observed_at
        self.operations.clear()
        self.waits.clear()

    def snapshot(self) -> dict[str, Any]:
        # A planned wake-up never proves a tool ended, or an external job lives.
        state = "executing" if self.operations else "waiting" if self.waits else self.state
        deadlines = [value for value in self.waits.values() if value is not None]
        return AgentActivityRecord(
            state=state, observed_at=self.observed_at, last_output_at=self.last_output_at,
            wait_until=min(deadlines) if deadlines else None,
            active_operation_count=len(self.operations),
        ).to_mapping()
