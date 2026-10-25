from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from .journal import CommandJournal
from .protocol import RemoteRequest, utc_now


FOLLOW_UP_QUEUE_VERSION = 1
ACTIVE_RUN_STATES = {"preparing", "running", "stopping", "finalizing"}
UNSAFE_RUN_STATES = {"sync_ambiguous", "cleanup_failed"}
TERMINAL_AGENT_STATES = {
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
TRANSIENT_COMMAND_CODES = {
    "command_in_progress",
    "pcr_operation_pending",
    "relay_error",
    "controller_relay_error",
    "relay_timeout",
}
FOLLOW_UP_CONTEXT_CONFLICT_MESSAGE = (
    "This workspace already has queued messages for another conversation. "
    "Open the existing queue before changing it."
)
FOLLOW_UP_RUN_CHANGED_MESSAGE = (
    "The conversation changed before this queued message could be sent. Open the queue to review it."
)


class FollowUpQueueError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class FollowUpCommandError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _bounded_int(
    value: object,
    default: int,
    *,
    minimum: int = 0,
    maximum: int = 4_096,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(maximum, max(minimum, parsed))


def _optional_text(value: object, default: str = "") -> str:
    return str(value or default).strip()


def canonical_follow_up_settings(value: object) -> dict[str, Any]:
    """Normalize native-client setting names into one durable queue format."""

    raw = dict(value) if isinstance(value, Mapping) else {}
    agents = _bounded_int(raw.get("agents", raw.get("num_agents")), 4, minimum=1)
    raw_execution = raw.get("execution")
    execution = _optional_text(raw_execution, "parallel").lower()
    if raw_execution is None:
        execution = "serial" if raw.get("serial") is True else "parallel"
    elif execution not in {"parallel", "serial"}:
        execution = "serial" if raw.get("serial") is True else "parallel"
    automatic = raw.get("automatic_max_parallel")
    if not isinstance(automatic, bool):
        automatic = raw.get("max_parallel") in {None, "", "auto", "automatic"}
    raw_fast = raw.get("fast", raw.get("fast_mode"))
    if isinstance(raw_fast, bool):
        fast = "on" if raw_fast else "off"
    else:
        fast = _optional_text(raw_fast, "automatic").lower()
        if fast not in {"automatic", "on", "off"}:
            fast = "automatic"
    raw_synthesis_fast = raw.get(
        "synthesis_fast", raw.get("synthesis_fast_mode")
    )
    if isinstance(raw_synthesis_fast, bool):
        synthesis_fast = "on" if raw_synthesis_fast else "off"
    else:
        synthesis_fast = _optional_text(
            raw_synthesis_fast,
            "automatic",
        ).lower()
        if synthesis_fast not in {"automatic", "on", "off"}:
            synthesis_fast = "automatic"
    recommendation = _optional_text(
        raw.get("recommendation", raw.get("recommend_by")),
        "reasoning_tokens",
    ).lower()
    if recommendation not in {"reasoning_tokens", "duration"}:
        recommendation = "reasoning_tokens"
    return {
        "agents": agents,
        "synthesis_agents": _bounded_int(
            raw.get("synthesis_agents"), 2, minimum=0
        ),
        "follow_up_delay": _bounded_int(
            raw.get("follow_up_delay"), 60, minimum=0, maximum=86_400
        ),
        "execution": execution,
        "automatic_max_parallel": automatic,
        "max_parallel": _bounded_int(
            raw.get("max_parallel"), agents, minimum=1
        ),
        "subagents": raw.get("subagents") is True,
        "subagents_limit": _bounded_int(
            raw.get("subagents_limit"), 8, minimum=1
        ),
        "recommendation": recommendation,
        "model": _optional_text(raw.get("model")),
        "effort": _optional_text(raw.get("effort")),
        "fast": fast,
        "synthesis_model": _optional_text(raw.get("synthesis_model")),
        "synthesis_effort": _optional_text(raw.get("synthesis_effort")),
        "synthesis_fast": synthesis_fast,
        "sync_back": raw.get("sync_back") is not False,
        "keep_workspaces": raw.get("keep_workspaces") is True,
        "resume_session_id": _optional_text(raw.get("resume_session_id")),
        "resume_include_non_interactive": (
            raw.get("resume_include_non_interactive") is True
        ),
        "runs_directory": _optional_text(
            raw.get("runs_directory", raw.get("runs_dir"))
        ),
        "codex_executable": _optional_text(
            raw.get("codex_executable", raw.get("codex_bin")), "codex"
        )
        or "codex",
    }


class FollowUpQueueStore:
    """Atomic daemon-owned follow-up queues, one queue per workspace."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.changed = threading.Event()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": FOLLOW_UP_QUEUE_VERSION, "revision": 0, "queues": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, ValueError) as exc:
            raise FollowUpQueueError(
                "follow_up_state_error", "The queued-message state cannot be read."
            ) from exc
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != FOLLOW_UP_QUEUE_VERSION:
            raise FollowUpQueueError(
                "follow_up_state_error", "The queued-message state uses an unsupported version."
            )
        queues = payload.get("queues")
        if not isinstance(queues, dict):
            raise FollowUpQueueError(
                "follow_up_state_error", "The queued-message state is invalid."
            )
        return {
            "version": FOLLOW_UP_QUEUE_VERSION,
            "revision": max(0, int(payload.get("revision") or 0)),
            "queues": queues,
        }

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        self.changed.set()

    @staticmethod
    def _public_queue(queue: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(queue))
        deadline = value.pop("deadline", None)
        value["deadline_epoch_ms"] = (
            int(float(deadline) * 1_000) if deadline is not None else None
        )
        for item in value.get("items", []):
            if isinstance(item, dict):
                retry_at = item.pop("retry_at", None)
                item["retry_at_epoch_ms"] = (
                    int(float(retry_at) * 1_000) if retry_at is not None else None
                )
                item.pop("dispatch", None)
        return value

    def get(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked()
            queue = payload["queues"].get(workspace_id)
            return {
                "revision": payload["revision"],
                "queue": self._public_queue(queue) if isinstance(queue, dict) else None,
            }

    def get_internal(self, workspace_id: str) -> dict[str, Any] | None:
        """Return an implementation snapshot, including an in-flight dispatch."""

        with self._lock:
            queue = self._read_unlocked()["queues"].get(workspace_id)
            return deepcopy(queue) if isinstance(queue, dict) else None

    def list(self) -> dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked()
            return {
                "revision": payload["revision"],
                "queues": [
                    self._public_queue(queue)
                    for queue in payload["queues"].values()
                    if isinstance(queue, dict)
                ],
            }

    @staticmethod
    def _normalize_items(items: object) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise FollowUpQueueError(
                "invalid_argument", "items must be a list of queued messages"
            )
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, Mapping):
                raise FollowUpQueueError(
                    "invalid_argument", "Each queued message must be an object"
                )
            item_id = _optional_text(raw.get("id"))
            prompt = _optional_text(raw.get("prompt"))
            if not item_id or len(item_id) > 128 or item_id in seen:
                raise FollowUpQueueError(
                    "invalid_argument", "Each queued message needs a unique id"
                )
            if not prompt:
                continue
            if len(prompt.encode("utf-8")) > 1_000_000:
                raise FollowUpQueueError(
                    "invalid_argument", "A queued message is too large"
                )
            seen.add(item_id)
            normalized.append(
                {
                    "id": item_id,
                    "prompt": prompt,
                    "settings": canonical_follow_up_settings(raw.get("settings")),
                    "attempt": _bounded_int(
                        raw.get("attempt"), 0, minimum=0, maximum=1_000_000
                    ),
                    "failure_count": 0,
                    "retry_at": None,
                    "last_error": "",
                    "paused": False,
                    "dispatch": None,
                    "created_at": utc_now(),
                }
            )
        return normalized

    @staticmethod
    def _conversation_for_update(
        previous: object,
        *,
        run_id: str,
        conversation_id: str,
    ) -> str:
        incoming = _optional_text(conversation_id)
        if not isinstance(previous, Mapping) or not previous.get("items"):
            return incoming

        current = _optional_text(previous.get("conversation_id"))
        if current and incoming and current != incoming:
            raise FollowUpQueueError(
                "follow_up_context_conflict",
                FOLLOW_UP_CONTEXT_CONFLICT_MESSAGE,
            )

        # Queues written before conversation_id was persisted can only be
        # adopted safely by the Run they were originally attached to.
        previous_run_id = _optional_text(previous.get("run_id"))
        if not current and incoming and previous_run_id and previous_run_id != run_id:
            raise FollowUpQueueError(
                "follow_up_context_conflict",
                FOLLOW_UP_CONTEXT_CONFLICT_MESSAGE,
            )
        return current or incoming

    def replace(
        self,
        *,
        workspace_id: str,
        run_id: str,
        conversation_id: str = "",
        items: object,
        selected_agent: int | None,
        reset_countdown: bool,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_items(items)
        now = utc_now()
        with self._lock:
            payload = self._read_unlocked()
            queues = payload["queues"]
            previous = queues.get(workspace_id)
            if expected_generation is not None and int((previous or {}).get("generation") or 0) != expected_generation:
                raise FollowUpQueueError("follow_up_conflict", "The queue changed while you were editing. Your draft has not been sent.")
            completed_item_id = (
                _optional_text(previous.get("last_completed_item_id"))
                if isinstance(previous, dict)
                else ""
            )
            if completed_item_id:
                completed_index = next(
                    (
                        index
                        for index, item in enumerate(normalized)
                        if item.get("id") == completed_item_id
                    ),
                    None,
                )
                if completed_index is not None:
                    normalized = normalized[completed_index + 1 :]
                    run_id = _optional_text(previous.get("run_id"), run_id)
            queue_conversation_id = self._conversation_for_update(
                previous,
                run_id=run_id,
                conversation_id=conversation_id,
            )
            # Legacy selected_agent values are browsing state, not consent to
            # continue a branch. Only the explicit selection action owns this.
            selection_source = None
            selected_agent = None
            if isinstance(previous, dict) and previous.get("run_id") == run_id:
                selection_source = previous.get("selection_source")
                if selection_source in {"manual", "automatic"}:
                    selected_agent = previous.get("selected_agent")
            was_dispatching = (
                isinstance(previous, dict)
                and previous.get("state") == "dispatching"
            )
            if was_dispatching:
                old_head = (previous.get("items") or [{}])[0]
                new_head = normalized[0] if normalized else {}
                if (
                    old_head.get("id") != new_head.get("id")
                    or old_head.get("prompt") != new_head.get("prompt")
                    or old_head.get("settings") != new_head.get("settings")
                    or int(old_head.get("attempt") or 0)
                    != int(new_head.get("attempt") or 0)
                    or previous.get("selected_agent") != selected_agent
                ):
                    raise FollowUpQueueError(
                        "follow_up_busy",
                        "The first queued message is already being sent.",
                        retryable=True,
                    )
            old_by_id = {
                str(item.get("id")): item
                for item in (previous.get("items") if isinstance(previous, dict) else []) or []
                if isinstance(item, dict)
            }
            for item in normalized:
                old = old_by_id.get(item["id"])
                item["enqueued_run_id"] = (
                    _optional_text(old.get("enqueued_run_id"))
                    if isinstance(old, dict) else run_id
                )
                if not isinstance(old, dict):
                    continue
                unchanged = (
                    old.get("prompt") == item["prompt"]
                    and old.get("settings") == item["settings"]
                    and int(old.get("attempt") or 0) == item["attempt"]
                )
                if unchanged:
                    for key in (
                        "failure_count",
                        "retry_at",
                        "last_error",
                        "paused",
                        "dispatch",
                        "created_at",
                    ):
                        item[key] = deepcopy(old.get(key))

            old_selection = (
                previous.get("selected_agent") if isinstance(previous, dict) else None
            )
            queue = {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "conversation_id": queue_conversation_id,
                "items": normalized,
                "selected_agent": selected_agent,
                "selection_source": selection_source,
                "state": (
                    "dispatching"
                    if was_dispatching and normalized
                    else "waiting"
                    if normalized
                    else "idle"
                ),
                "deadline": (
                    previous.get("deadline")
                    if isinstance(previous, dict)
                    and not was_dispatching
                    and not reset_countdown
                    and old_selection == selected_agent
                    else None
                ),
                "last_error": "",
                "created_at": (
                    previous.get("created_at") if isinstance(previous, dict) else now
                ),
                "updated_at": now,
                "generation": int(
                    (previous.get("generation") if isinstance(previous, dict) else 0)
                    or 0
                )
                + 1,
                "last_completed_item_id": (
                    previous.get("last_completed_item_id")
                    if isinstance(previous, dict)
                    else None
                ),
            }
            queues[workspace_id] = queue
            payload["revision"] += 1
            self._write_unlocked(payload)
            return {
                "revision": payload["revision"],
                "queue": self._public_queue(queue),
            }

    def enqueue(
        self,
        *,
        workspace_id: str,
        run_id: str,
        conversation_id: str = "",
        item: object,
        selected_agent: int | None,
        ancestor_run_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Append one message without replacing edits made by another client."""

        normalized = self._normalize_items([item])
        if not normalized:
            raise FollowUpQueueError(
                "invalid_argument", "A queued message must include a prompt"
            )
        queued_item = normalized[0]
        queued_item["enqueued_run_id"] = run_id
        now = utc_now()
        with self._lock:
            payload = self._read_unlocked()
            queues = payload["queues"]
            previous = queues.get(workspace_id)
            previous_items = (
                previous.get("items")
                if isinstance(previous, dict)
                and isinstance(previous.get("items"), list)
                else []
            )
            completed_item_id = (
                _optional_text(previous.get("last_completed_item_id"))
                if isinstance(previous, dict)
                else ""
            )
            queue_conversation_id = self._conversation_for_update(
                previous,
                run_id=run_id,
                conversation_id=conversation_id,
            )
            if queued_item["id"] == completed_item_id or any(
                isinstance(existing, Mapping)
                and existing.get("id") == queued_item["id"]
                for existing in previous_items
            ):
                return {
                    "revision": payload["revision"],
                    "queue": (
                        self._public_queue(previous)
                        if isinstance(previous, dict)
                        else None
                    ),
                }

            has_pending_items = bool(previous_items)
            advanced_same_conversation = (
                isinstance(previous, dict)
                and bool(previous.get("last_completed_item_id"))
                and bool(conversation_id)
                and previous.get("conversation_id") == conversation_id
                # A completion tombstone protects late appends from regressing
                # an already consumed turn, not new appends to a later turn.
                and previous.get("run_id") not in ancestor_run_ids
            )
            keep_selection = isinstance(previous, dict) and (
                has_pending_items or advanced_same_conversation or previous.get("run_id") == run_id
            ) and previous.get("selection_source") in {"manual", "automatic"}
            queue = {
                "workspace_id": workspace_id,
                "run_id": (
                    _optional_text(previous.get("run_id"), run_id)
                    if (has_pending_items or advanced_same_conversation) and isinstance(previous, dict)
                    else run_id
                ),
                "conversation_id": queue_conversation_id,
                "items": [*deepcopy(previous_items), queued_item],
                "selected_agent": (
                    previous.get("selected_agent")
                    if keep_selection else None
                ),
                "selection_source": previous.get("selection_source") if keep_selection else None,
                "state": (
                    "dispatching"
                    if isinstance(previous, dict)
                    and previous.get("state") == "dispatching"
                    and has_pending_items
                    else "waiting"
                ),
                "deadline": (
                    previous.get("deadline")
                    if has_pending_items and isinstance(previous, dict)
                    else None
                ),
                "last_error": (
                    _optional_text(previous.get("last_error"))
                    if has_pending_items and isinstance(previous, dict)
                    else ""
                ),
                "created_at": (
                    previous.get("created_at")
                    if isinstance(previous, dict)
                    else now
                ),
                "updated_at": now,
                "generation": int(
                    (previous.get("generation") if isinstance(previous, dict) else 0)
                    or 0
                )
                + 1,
                "last_completed_item_id": (
                    previous.get("last_completed_item_id")
                    if isinstance(previous, dict)
                    else None
                ),
            }
            queues[workspace_id] = queue
            payload["revision"] += 1
            self._write_unlocked(payload)
            return {
                "revision": payload["revision"],
                "queue": self._public_queue(queue),
            }

    def reconcile_enqueue(
        self, *, workspace_id: str, run_id: str, conversation_id: str,
        item: object, original_queue: Mapping[str, Any],
        ancestor_run_ids: frozenset[str],
    ) -> dict[str, Any]:
        """Repair only a journal-verified original enqueue misbound by 0.1.72."""
        normalized = self._normalize_items([item])
        if not normalized:
            return self.get(workspace_id)
        original_item = normalized[0]
        with self._lock:
            payload = self._read_unlocked()
            queue = payload["queues"].get(workspace_id)
            if not isinstance(queue, dict):
                return {"revision": payload["revision"], "queue": None}
            items = queue.get("items") or []
            admitted_items = original_queue.get("items") or []
            old_run_id = _optional_text(original_queue.get("run_id"))
            # Exactly one untouched message, immediately followed by the
            # automatic mismatch pause. User edits/stops/dispatches invalidate
            # this proof; sharing a conversation alone never authorizes a move.
            eligible = (
                len(items) == len(admitted_items) == 1
                and original_queue.get("state") == "waiting"
                and not admitted_items[0].get("paused")
                and not admitted_items[0].get("failure_count")
                and not admitted_items[0].get("attempt")
                and old_run_id in ancestor_run_ids
                and queue.get("run_id") == old_run_id
                and queue.get("conversation_id") == conversation_id
                and original_queue.get("conversation_id") == conversation_id
                and queue.get("last_completed_item_id")
                == original_queue.get("last_completed_item_id")
                and bool(queue.get("last_completed_item_id"))
                and int(queue.get("generation") or 0)
                == int(original_queue.get("generation") or 0) + 1
                and queue.get("state") == "paused"
                and items[0].get("paused") is True
                and items[0].get("last_error") == FOLLOW_UP_RUN_CHANGED_MESSAGE
                and not items[0].get("dispatch")
                and not items[0].get("failure_count")
                and not items[0].get("attempt")
                and all(items[0].get(key) == admitted_items[0].get(key)
                        for key in ("id", "prompt", "settings", "attempt", "created_at"))
                and all(items[0].get(key) == original_item.get(key)
                        for key in ("id", "prompt", "settings", "attempt"))
            )
            if eligible:
                queue.update(run_id=run_id, selected_agent=None, selection_source=None,
                             state="waiting", deadline=None, last_error="",
                             generation=int(queue.get("generation") or 0) + 1,
                             updated_at=utc_now())
                items[0].update(enqueued_run_id=run_id, paused=False, last_error="", retry_at=None)
                payload["revision"] += 1
                self._write_unlocked(payload)
            return {"revision": payload["revision"], "queue": self._public_queue(queue)}

    def clear(
        self,
        workspace_id: str,
        *,
        run_id: str = "",
        conversation_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._read_unlocked()
            previous = payload["queues"].get(workspace_id)
            if isinstance(previous, dict) and previous.get("state") == "dispatching":
                raise FollowUpQueueError(
                    "follow_up_busy",
                    "The first queued message is already being sent.",
                    retryable=True,
                )
            queue_conversation_id = self._conversation_for_update(
                previous,
                run_id=run_id,
                conversation_id=conversation_id,
            )
            now = utc_now()
            keep_selection = isinstance(previous, dict) and (
                not run_id or previous.get("run_id") == run_id
            ) and previous.get("selection_source") in {"manual", "automatic"}
            queue = {
                "workspace_id": workspace_id,
                "run_id": run_id or str((previous or {}).get("run_id") or ""),
                "conversation_id": queue_conversation_id,
                "items": [],
                "selected_agent": previous.get("selected_agent") if keep_selection else None,
                "selection_source": previous.get("selection_source") if keep_selection else None,
                "state": "idle",
                "deadline": None,
                "last_error": "",
                "created_at": (previous or {}).get("created_at") or now,
                "updated_at": now,
                "generation": int((previous or {}).get("generation") or 0) + 1,
                "last_completed_item_id": (previous or {}).get(
                    "last_completed_item_id"
                ),
            }
            payload["queues"][workspace_id] = queue
            payload["revision"] += 1
            self._write_unlocked(payload)
            return {
                "revision": payload["revision"],
                "queue": self._public_queue(queue),
            }

    def select_agent(self, *, workspace_id: str, run_id: str,
                     conversation_id: str, agent: int) -> dict[str, Any]:
        """Persist a deliberate post-completion choice without rewriting messages."""
        with self._lock:
            previous = self.get_internal(workspace_id)
            self._conversation_for_update(previous, run_id=run_id, conversation_id=conversation_id)
            if previous and previous.get("items") and previous.get("run_id") != run_id:
                raise FollowUpQueueError("follow_up_context_conflict", FOLLOW_UP_CONTEXT_CONFLICT_MESSAGE)
            if previous and previous.get("state") == "dispatching":
                raise FollowUpQueueError("follow_up_busy", "The first queued message is already being sent.", retryable=True)
            if previous is None or previous.get("run_id") != run_id:
                self.clear(workspace_id, run_id=run_id, conversation_id=conversation_id)

            def select(queue: dict[str, Any]) -> bool:
                queue["selected_agent"] = agent
                queue["selection_source"] = "manual"
                queue["deadline"] = None
                if queue.get("items"):
                    queue["state"] = "waiting"
                return True

            self.mutate(workspace_id, select)
            return self.get(workspace_id)

    def reset_selection(self, workspace_id: str, run_id: str) -> None:
        def reset(queue: dict[str, Any]) -> bool:
            if queue.get("run_id") != run_id or queue.get("state") == "dispatching":
                return False
            if queue.get("selected_agent") is None and queue.get("selection_source") is None:
                return False
            queue["selected_agent"] = None
            queue["selection_source"] = None
            queue["deadline"] = None
            return True
        self.mutate(workspace_id, reset)

    def update_delay(self, workspace_id: str, delay: int) -> None:
        delay = _bounded_int(delay, 60, maximum=86_400)

        def update(queue: dict[str, Any]) -> bool:
            changed = False
            for index, item in enumerate(queue.get("items", [])):
                # A claimed command is immutable; only its successors can change.
                if index == 0 and (queue.get("state") == "dispatching" or item.get("dispatch")):
                    continue
                settings = canonical_follow_up_settings(item.get("settings"))
                if settings["follow_up_delay"] == delay:
                    continue
                item["settings"] = {**settings, "follow_up_delay": delay}
                changed = True
                if index == 0 and queue.get("state") == "countdown" and not item.get("paused"):
                    queue["deadline"] = time.time() + delay
            return changed

        self.mutate(workspace_id, update)

    def pause_for_run(self, run_id: str) -> None:
        """Persist the user's stop before Agents become eligible to continue."""
        with self._lock:
            payload = self._read_unlocked()
            queues = [q for q in payload["queues"].values()
                      if q.get("run_id") == run_id and q.get("items")]
            if any(q.get("state") == "dispatching" for q in queues):
                raise FollowUpQueueError("follow_up_busy", "The next message is already being sent. Open the latest turn to stop it.")
            for queue in queues:
                queue["items"][0].update(paused=True, retry_at=None, last_error="Queue paused after stopping this turn.")
                queue.update(state="paused", deadline=None, last_error="Queue paused after stopping this turn.", updated_at=utc_now())
                queue["generation"] = int(queue.get("generation") or 0) + 1
            if queues:
                payload["revision"] += 1
                self._write_unlocked(payload)

    def mutate(
        self,
        workspace_id: str,
        callback: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any] | None:
        with self._lock:
            payload = self._read_unlocked()
            queue = payload["queues"].get(workspace_id)
            if not isinstance(queue, dict):
                return None
            changed = callback(queue)
            if changed:
                queue["updated_at"] = utc_now()
                queue["generation"] = int(queue.get("generation") or 0) + 1
                payload["revision"] += 1
                self._write_unlocked(payload)
            return deepcopy(queue)


class FollowUpQueueCoordinator:
    """Continues durable queues while every native client is closed."""

    def __init__(
        self,
        store: FollowUpQueueStore,
        controller: Any,
        execute: Callable[[str, Mapping[str, Any], str], dict[str, Any]],
        *,
        poll_interval: float = 1.0,
        workers: int = 4,
    ) -> None:
        self.store = store
        self.controller = controller
        self.execute = execute
        self.poll_interval = max(0.1, float(poll_interval))
        self._stop = threading.Event()
        self._active: set[str] = set()
        self._active_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="pcr-follow-up"
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="pcr-follow-up-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.store.changed.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                queues = self.store.list().get("queues", [])
            except FollowUpQueueError:
                queues = []
            for queue in queues:
                workspace_id = str(queue.get("workspace_id") or "")
                if not workspace_id or not queue.get("items"):
                    continue
                with self._active_lock:
                    if workspace_id in self._active:
                        continue
                    self._active.add(workspace_id)
                self._executor.submit(self._process_and_release, workspace_id)
            self.store.changed.wait(self.poll_interval)
            self.store.changed.clear()

    def _process_and_release(self, workspace_id: str) -> None:
        try:
            self.process_workspace(workspace_id)
        finally:
            with self._active_lock:
                self._active.discard(workspace_id)

    def process_once(self) -> None:
        for queue in self.store.list().get("queues", []):
            if queue.get("items"):
                self.process_workspace(str(queue.get("workspace_id") or ""))

    def _pause(
        self,
        workspace_id: str,
        message: str,
        *,
        expected_item_id: str = "",
        expected_run_id: str = "",
    ) -> None:
        def update(queue: dict[str, Any]) -> bool:
            items = queue.get("items") or []
            if not items:
                return False
            if expected_run_id and str(queue.get("run_id") or "") != expected_run_id:
                return False
            if expected_item_id and str(items[0].get("id") or "") != expected_item_id:
                return False
            items[0]["paused"] = True
            items[0]["retry_at"] = None
            items[0]["last_error"] = message
            queue["state"] = "paused"
            queue["deadline"] = None
            queue["last_error"] = message
            return True

        self.store.mutate(workspace_id, update)

    @staticmethod
    def _eligible_agents(run: Mapping[str, Any]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for raw in run.get("agents", []):
            if not isinstance(raw, Mapping):
                continue
            status = _optional_text(raw.get("status")).lower()
            can_continue = raw.get("can_continue")
            if can_continue is None:
                can_continue = (
                    status == "success"
                    and raw.get("workspace_available") is not False
                    and raw.get("can_accept") is not False
                )
            if can_continue:
                values.append(dict(raw))
        return values

    @staticmethod
    def _source_agent(
        queue: Mapping[str, Any], run: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        eligible = FollowUpQueueCoordinator._eligible_agents(run)
        by_id = {
            int(value.get("agent")): value
            for value in eligible
            if isinstance(value.get("agent"), int)
        }
        for raw in (
            run.get("finalized_agent"),
            queue.get("selected_agent") if queue.get("selection_source") == "manual" else None,
        ):
            if isinstance(raw, int) and raw in by_id:
                return by_id[raw]
        automatic = {
            agent: value for agent, value in by_id.items()
            if not value.get("rejected") and not value.get("stale")
        }
        for raw in (
            queue.get("selected_agent") if queue.get("selection_source") == "automatic" else None,
            run.get("continuation_agent"),
            run.get("recommended_agent"),
        ):
            if isinstance(raw, int) and raw in automatic:
                return automatic[raw]
        return next(iter(automatic.values()), None)

    @staticmethod
    def _head_matches(
        queue: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        head: Mapping[str, Any],
    ) -> bool:
        items = queue.get("items")
        if not isinstance(items, list) or not items:
            return False
        current = items[0]
        if not isinstance(current, Mapping):
            return False
        return (
            str(queue.get("run_id") or "")
            == str(snapshot.get("run_id") or "")
            and queue.get("selected_agent") == snapshot.get("selected_agent")
            and queue.get("generation") == snapshot.get("generation")
            and current.get("id") == head.get("id")
            and current.get("prompt") == head.get("prompt")
            and current.get("settings") == head.get("settings")
            and int(current.get("attempt") or 0) == int(head.get("attempt") or 0)
            and current.get("paused") is not True
        )

    @staticmethod
    def _all_agents_terminal(run: Mapping[str, Any]) -> bool:
        agents = [value for value in run.get("agents", []) if isinstance(value, Mapping)]
        return bool(agents) and all(
            _optional_text(value.get("status")).lower() in TERMINAL_AGENT_STATES
            for value in agents
        )

    @staticmethod
    def _continue_body(
        queue: Mapping[str, Any], item: Mapping[str, Any], agent: int
    ) -> dict[str, Any]:
        settings = canonical_follow_up_settings(item.get("settings"))
        serial = settings["execution"] == "serial"
        return {
            "workspace_id": queue["workspace_id"],
            "run_id": queue["run_id"],
            "agent": agent,
            "prompt": item["prompt"],
            "num_agents": settings["agents"],
            "max_parallel": (
                1
                if serial
                else None
                if settings["automatic_max_parallel"]
                else settings["max_parallel"]
            ),
            "automatic_max_parallel": settings["automatic_max_parallel"],
            "serial": serial,
            "recommend_by": settings["recommendation"],
            "model": settings["model"],
            "effort": settings["effort"],
            "fast_mode": settings["fast"],
            "synthesis_agents": settings["synthesis_agents"],
            "follow_up_delay": settings["follow_up_delay"],
            "synthesis_model": settings["synthesis_model"],
            "synthesis_effort": settings["synthesis_effort"],
            "synthesis_fast_mode": settings["synthesis_fast"],
            "sync_back": settings["sync_back"],
            "keep_workspaces": settings["keep_workspaces"],
            "subagents": settings["subagents"],
            "subagents_limit": settings["subagents_limit"],
            "runs_dir": settings["runs_directory"],
            "codex_bin": settings["codex_executable"],
            "confirm_large_run": True,
        }

    def _latest_run_id(self, workspace_id: str) -> str:
        return str(self.controller.registry.require(workspace_id).last_run_id or "").strip()

    def _matches_dispatched_follow_up(
        self,
        run_id: str,
        *,
        workspace_id: str,
        source_run_id: str,
        head: Mapping[str, Any],
        dispatch: Mapping[str, Any] | None = None,
    ) -> bool:
        try:
            run = self.controller.manager.get_run(run_id, include_events=False)
        except Exception:
            return False
        if not (
            str(run.get("continued_from_run_id") or "") == source_run_id
            and str(run.get("prompt") or "").strip() == str(head.get("prompt") or "").strip()
        ):
            return False
        dispatch = dispatch or head.get("dispatch")
        if isinstance(dispatch, Mapping) and isinstance(dispatch.get("body"), Mapping):
            body = dict(dispatch["body"])
            command_id = str(dispatch.get("command_id") or "")
        else:
            agent = run.get("continued_from_agent")
            if not isinstance(agent, int) or agent < 1:
                return False
            body = self._continue_body({"workspace_id": workspace_id, "run_id": source_run_id}, head, agent)
            command_id = f"follow-up-{head.get('id')}-attempt-{int(head.get('attempt') or 0)}"
        if not command_id or body.get("run_id") != source_run_id or body.get("workspace_id") != workspace_id:
            return False
        # Identical text is not a receipt: another client can legitimately send
        # it again. Only this item's original command and complete input prove it.
        expected = CommandJournal.identity(RemoteRequest(action="turns.continue", body=body,
            command_id=command_id, client_id="pcrd-follow-up"))
        config = run.get("config")
        return isinstance(config, Mapping) and config.get("submission_command") == expected

    def _complete(self, workspace_id: str, item_id: str, next_run_id: str) -> None:
        def update(queue: dict[str, Any]) -> bool:
            items = queue.get("items") or []
            if not items or str(items[0].get("id")) != item_id:
                return False
            queue["items"] = items[1:]
            queue["run_id"] = next_run_id
            queue["selected_agent"] = None
            queue["selection_source"] = None
            queue["state"] = "waiting" if len(items) > 1 else "idle"
            queue["deadline"] = None
            queue["last_error"] = ""
            queue["last_completed_item_id"] = item_id
            return True

        self.store.mutate(workspace_id, update)

    def _record_retry(
        self,
        workspace_id: str,
        item_id: str,
        message: str,
    ) -> None:
        def update(queue: dict[str, Any]) -> bool:
            items = queue.get("items") or []
            if not items or str(items[0].get("id")) != item_id:
                return False
            item = items[0]
            failures = int(item.get("failure_count") or 0) + 1
            retry_at = time.time() + min(300, 5 * (2 ** min(failures - 1, 6)))
            item["failure_count"] = failures
            item["retry_at"] = retry_at
            item["last_error"] = ""
            # Finalization may still be running, or its response may be lost.
            # Keep the claimed command immutable; retry is not a new countdown.
            claimed = isinstance(item.get("dispatch"), Mapping)
            queue["state"] = "dispatching" if claimed else "waiting"
            queue["deadline"] = None if claimed else retry_at
            queue["last_error"] = ""
            # When retrying a claimed dispatch, preserve the selection identity
            # so process_workspace recognizes it as an active dispatch rather
            # than starting a new countdown from scratch.
            if claimed and "selected_agent" not in queue:
                dispatch = item["dispatch"]
                if isinstance(dispatch, Mapping):
                    body = dispatch.get("body")
                    if isinstance(body, Mapping):
                        source_agent = body.get("continued_from_agent")
                        if isinstance(source_agent, int):
                            queue["selected_agent"] = source_agent
                            if "selection_source" not in queue:
                                queue["selection_source"] = "automatic"
            return True

        self.store.mutate(workspace_id, update)

    def _execute_claimed(
        self,
        workspace_id: str,
        head: Mapping[str, Any],
        source_run_id: str,
        dispatch: Mapping[str, Any],
    ) -> None:
        item_id = str(head.get("id") or "")
        action = _optional_text(dispatch.get("action"))
        body = dispatch.get("body")
        command_id = _optional_text(dispatch.get("command_id"))
        if not item_id or action != "turns.continue" or not isinstance(body, Mapping):
            self._pause(
                workspace_id,
                "This queued message has invalid saved state. Open the queue to retry it.",
                expected_item_id=item_id,
                expected_run_id=source_run_id,
            )
            return
        if not command_id:
            command_id = (
                f"follow-up-{item_id}-attempt-{int(head.get('attempt') or 0)}"
            )
        try:
            result = self.execute(action, dict(body), command_id)
            next_run_id = str(
                result.get("run_id")
                or (result.get("run") or {}).get("run_id")
                or ""
            ).strip()
            if not next_run_id:
                raise FollowUpCommandError(
                    "invalid_response", "The queued message started without a run id."
                )
            self._complete(workspace_id, item_id, next_run_id)
        except FollowUpCommandError as exc:
            try:
                latest = self._latest_run_id(workspace_id)
            except Exception:
                latest = source_run_id
            if latest != source_run_id:
                if self._matches_dispatched_follow_up(
                    latest,
                    workspace_id=workspace_id,
                    source_run_id=source_run_id,
                    head=head,
                    dispatch=dispatch,
                ):
                    self._complete(workspace_id, item_id, latest)
                else:
                    self._pause(
                        workspace_id,
                        FOLLOW_UP_RUN_CHANGED_MESSAGE,
                        expected_item_id=item_id,
                        expected_run_id=source_run_id,
                    )
                return
            if exc.retryable or exc.code in TRANSIENT_COMMAND_CODES:
                self._record_retry(workspace_id, item_id, exc.message)
            else:
                self._pause(
                    workspace_id,
                    "This queued message could not be sent. Open the queue to retry it.",
                    expected_item_id=item_id,
                    expected_run_id=source_run_id,
                )
        except Exception:
            self._record_retry(
                workspace_id,
                item_id,
                "The queued message could not be sent yet.",
            )

    def process_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            return
        snapshot = self.store.get_internal(workspace_id)
        if not isinstance(snapshot, dict) or not snapshot.get("items"):
            return
        head = snapshot["items"][0]
        item_id = str(head.get("id") or "")
        source_run_id = str(snapshot.get("run_id") or "")
        if head.get("paused"):
            return
        retry_at = head.get("retry_at")
        if isinstance(retry_at, (int, float)) and retry_at > time.time():
            return
        try:
            latest_run_id = self._latest_run_id(workspace_id)
        except Exception:
            self._pause(
                workspace_id,
                "This workspace is no longer available.",
                expected_item_id=item_id,
                expected_run_id=source_run_id,
            )
            return
        if not latest_run_id:
            return
        if latest_run_id != source_run_id:
            if self._matches_dispatched_follow_up(
                latest_run_id,
                workspace_id=workspace_id,
                source_run_id=source_run_id,
                head=head,
            ):
                self._complete(workspace_id, item_id, latest_run_id)
            else:
                # A workspace may receive a new Run from another client or a
                # different conversation while this message is waiting. Never
                # retarget the message silently: its selected Agent and resume
                # lineage belong to the Run it was queued against.
                self._pause(
                    workspace_id,
                    FOLLOW_UP_RUN_CHANGED_MESSAGE,
                    expected_item_id=item_id,
                    expected_run_id=source_run_id,
                )
            return
        try:
            run = self.controller.manager.get_run(source_run_id, include_events=False)
        except Exception:
            return
        status = _optional_text(run.get("status")).lower()
        saved_dispatch = head.get("dispatch")
        if bool(run.get("worker_active")) or status in ACTIVE_RUN_STATES:
            if isinstance(saved_dispatch, Mapping):
                return
            def waiting(queue: dict[str, Any]) -> bool:
                if not self._head_matches(queue, snapshot, head):
                    return False
                if (queue.get("state") == "waiting" and queue.get("deadline") is None
                        and queue.get("selected_agent") is None and queue.get("selection_source") is None):
                    return False
                queue["state"] = "waiting"
                queue["deadline"] = None
                queue["selected_agent"] = None
                queue["selection_source"] = None
                queue["last_error"] = ""
                return True

            self.store.mutate(workspace_id, waiting)
            return
        if status in UNSAFE_RUN_STATES:
            self._pause(
                workspace_id,
                "Workspace synchronization needs attention before this message can be sent.",
                expected_item_id=item_id,
                expected_run_id=source_run_id,
            )
            return
        if isinstance(saved_dispatch, Mapping):
            self._execute_claimed(
                workspace_id,
                head,
                source_run_id,
                saved_dispatch,
            )
            return
        source = self._source_agent(snapshot, run)
        if source is None:
            if self._all_agents_terminal(run):
                self._pause(
                    workspace_id,
                    "No successful Agent is available for this queued message.",
                    expected_item_id=item_id,
                    expected_run_id=source_run_id,
                )
            return
        source_agent = int(source["agent"])
        deadline = snapshot.get("deadline")
        if (snapshot.get("selection_source") not in {"manual", "automatic"}
                or snapshot.get("selected_agent") != source_agent):
            deadline = None
        if not isinstance(deadline, (int, float)):
            delay = canonical_follow_up_settings(head.get("settings"))["follow_up_delay"]

            def start_countdown(queue: dict[str, Any]) -> bool:
                if not self._head_matches(queue, snapshot, head):
                    return False
                queue["selected_agent"] = source_agent
                if queue.get("selection_source") != "manual":
                    queue["selection_source"] = "automatic"
                queue["state"] = "countdown"
                queue["deadline"] = time.time() + delay
                queue["last_error"] = ""
                return True

            countdown = self.store.mutate(workspace_id, start_countdown)
            if not countdown or countdown.get("state") != "countdown":
                return
            if delay > 0:
                return
            snapshot = countdown
            head = countdown["items"][0]
            deadline = time.time()
        if deadline > time.time():
            return

        command_id = f"follow-up-{item_id}-attempt-{int(head.get('attempt') or 0)}"
        body = self._continue_body(snapshot, head, source_agent)

        def claim(queue: dict[str, Any]) -> bool:
            if not self._head_matches(queue, snapshot, head):
                return False
            items = queue["items"]
            items[0]["dispatch"] = {
                "action": "turns.continue",
                "body": body,
                "command_id": command_id,
            }
            items[0]["retry_at"] = None
            queue["state"] = "dispatching"
            queue["deadline"] = None
            queue["last_error"] = ""
            return True

        claimed = self.store.mutate(workspace_id, claim)
        if not claimed or claimed.get("state") != "dispatching":
            return
        self._execute_claimed(
            workspace_id,
            head,
            source_run_id,
            claimed["items"][0]["dispatch"],
        )
