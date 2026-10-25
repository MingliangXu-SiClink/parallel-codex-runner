from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

from ..work_operations import operation_for_event, operation_from_payload, wait_deadline


TRANSCRIPT_SCHEMA_VERSION = 1


_TEXT_KEYS = ("message", "text", "content", "delta", "summary", "reasoning")
_CONTAINER_KEYS = ("item", "payload")
_RUNTIME_LOG_RE = re.compile(
    r"^(?:\S+\s+)?(?P<level>ERROR|WARN(?:ING)?)\s+"
    r"(?P<source>[A-Za-z0-9_.:-]+):\s*(?P<message>.+)$",
    re.DOTALL,
)


def transcript_page(event_page: object) -> dict[str, Any] | None:
    """Build the stable, display-oriented item page consumed by native clients."""

    if not isinstance(event_page, Mapping):
        return None
    events = event_page.get("events")
    if not isinstance(events, list):
        events = []
    result = {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "cursor": _integer(event_page.get("cursor"), default=0),
        "next_cursor": _integer(event_page.get("next_cursor"), default=0),
        "has_more": bool(event_page.get("has_more", False)),
        "total": _integer(event_page.get("total"), default=len(events)),
        "items": transcript_items(events),
    }
    for key in ("serialized_bytes", "byte_limit", "oversized_event"):
        if key in event_page:
            result[key] = event_page[key]
    return result


def transcript_items(events: Sequence[object]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        item = transcript_item(event)
        if item is not None:
            if isinstance(event, Mapping):
                operation = operation_for_event(event)
                if operation is None and item.get("activity_type") == "sleep":
                    # Older workers saved native wait events before operation tracking.
                    # Their explicit call ID is enough to keep start/end on one row.
                    try:
                        payload = json.loads(str(event.get("text") or ""))
                        source = _event_item(payload)
                        identifier = _clean_text(source.get("id")) if source else ""
                        if identifier:
                            record = operation_from_payload(payload,
                                identity=f"sleep:{event.get('idx', '')}:{identifier}", event_id=int(event["event_id"]))
                            if record is not None:
                                if record.status.value == "running":
                                    record.wait_until = wait_deadline(event.get("timestamp"), record.planned_duration_ms)
                                operation = record.to_mapping()
                    except (ValueError, TypeError):
                        pass
                if operation is not None:
                    item["operation"] = operation
            items.append(item)
    return items


def transcript_item(event: object) -> dict[str, Any] | None:
    item = _transcript_item(event)
    if item is None or item["kind"] not in {"thought", "output"} or not isinstance(event, Mapping):
        return item
    identity = _clean_text(event.get("pcr_text_item_id"))
    if not identity:
        try:
            payload = json.loads(str(event.get("text") or ""))
            source = _event_item(payload) if isinstance(payload, Mapping) else None
            source_id = _clean_text(source.get("id")) if source else ""
            # These globally unique app-server IDs also repair the first
            # adapter's saved snapshots; legacy exec item_0 IDs are not unique.
            if source_id.startswith(("msg_", "rs_")):
                identity = f"{event.get('idx', '')}:{source_id}"
        except (ValueError, TypeError):
            pass
    if identity:
        fragment = _integer(event.get("pcr_fragment_index"), default=0)
        item["id"] = f"text-{identity}" + (f":part-{fragment}" if fragment else "")
    return item


def _transcript_item(event: object) -> dict[str, Any] | None:
    if not isinstance(event, Mapping) or event.get("type") != "agent_line":
        return None
    event_id = _integer(event.get("event_id"), default=-1)
    if event_id < 0:
        return None
    fragment_kind = _clean_text(event.get("pcr_transcript_kind")).casefold()
    fragment_text = _clean_text(event.get("pcr_transcript_text"))
    if fragment_kind in {
        "thought",
        "output",
        "command",
        "activity",
        "warning",
        "error",
    } and fragment_text:
        return _base_item(
            event_id,
            kind=fragment_kind,
            text=fragment_text,
            status=_clean_text(event.get("pcr_transcript_status")) or None,
            operation_id=_clean_text(event.get("pcr_operation_id")) or None,
            activity_type=_clean_text(event.get("pcr_activity_type")) or None,
        )
    raw = _clean_text(event.get("text") or event.get("message"))
    if not raw or _hidden_runtime_diagnostic(raw):
        return None

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, Mapping):
        event_item = _event_item(payload)
        item_type = _clean_text(event_item.get("type")) if event_item else ""
        if item_type == "imageGeneration":
            # Keep the native envelope and identity intact. Image-aware clients
            # reconstruct legacy event fragments before decoding the media.
            return _base_item(event_id, kind="activity", text=raw, activity_type="native_image_source")
        if item_type == "command_execution" and event_item is not None:
            command_text = _command_text(event_item.get("command"))
            if not command_text:
                return None
            result = _base_item(
                event_id,
                kind="command",
                text=_strip_shell_wrapper(command_text),
            )
            result["status"] = _command_status(event_item)
            operation_id = _clean_text(event_item.get("id"))
            if operation_id:
                result["operation_id"] = operation_id
            return result

        activity = _activity_item(payload, event_item, item_type)
        if activity is not None:
            return _base_item(event_id, **activity)

        if item_type == "error" and event_item is not None:
            message = _clean_text(event_item.get("message"))
            if message:
                return _base_item(
                    event_id,
                    kind="warning" if _recoverable_warning(message) else "error",
                    text=message,
                )

        text = _clean_text(_first_text(payload))
        if not text:
            return None
        kind = _payload_kind(payload)
        phase = _clean_text(event_item.get("phase")) if event_item else ""
        return _base_item(
            event_id,
            kind=kind,
            text=_display_text(kind, _strip_agent_prefix(text)),
            activity_type=(
                "assistant_final" if phase == "final_answer" else "assistant_progress"
            ) if kind == "output" and phase in {"final_answer", "commentary"} else None,
        )

    diagnostic = _runtime_log_item(event_id, raw)
    if diagnostic is not None:
        return diagnostic

    lowered = raw.casefold()
    if lowered.startswith("agent_reasoning:"):
        kind = "thought"
    elif lowered.startswith("agent_message:"):
        kind = "output"
    else:
        kind = "activity"
    return _base_item(
        event_id,
        kind=kind,
        text=_display_text(kind, _strip_agent_prefix(raw)),
    )


def _base_item(
    event_id: int,
    *,
    kind: str,
    text: str,
    status: str | None = None,
    operation_id: str | None = None,
    activity_type: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": f"event-{event_id}",
        "event_id": event_id,
        "kind": kind,
        "text": text,
    }
    if status:
        result["status"] = status
    if operation_id:
        result["operation_id"] = operation_id
    if activity_type:
        result["activity_type"] = activity_type
    return result


def _hidden_runtime_diagnostic(value: str) -> bool:
    normalized = value.casefold()
    return (
        "codex_models_manager" in normalized
        and "failed to refresh available models" in normalized
    )


def _runtime_log_item(event_id: int, value: str) -> dict[str, Any] | None:
    match = _RUNTIME_LOG_RE.match(value)
    if match is None:
        return None
    message = match.group("message").strip()
    if message.casefold().startswith("error="):
        message = message[len("error=") :].lstrip()
    normalized = message.casefold()
    if "createprocess" in normalized and "no such file or directory" in normalized:
        return _base_item(
            event_id,
            kind="warning",
            text="A command could not start because a required path no longer exists.",
            activity_type="missing_execution_path",
        )
    return _base_item(
        event_id,
        kind="warning"
        if match.group("level").upper().startswith("WARN")
        else "error",
        text=message,
        activity_type="runtime_diagnostic",
    )


def _recoverable_warning(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    model_resume = (
        normalized.startswith("this session was recorded with model")
        and "but is resuming with" in normalized
        and "consider switching back to" in normalized
    )
    transport_recovery = normalized.startswith(
        "falling back from websockets to https transport"
    ) or normalized.startswith("reconnecting...")
    return model_resume or transport_recovery


def _event_item(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    item = payload.get("item")
    if isinstance(item, Mapping):
        return item
    nested = payload.get("payload")
    if isinstance(nested, Mapping):
        return _event_item(nested)
    return None


def _activity_item(
    payload: Mapping[str, Any],
    item: Mapping[str, Any] | None,
    item_type: str,
) -> dict[str, str] | None:
    if item is None or item_type not in {"web_search", "file_change", "todo_list", "sleep"}:
        return None
    if item_type == "sleep":
        text = "Waiting" if _event_status(payload, item) == "running" else "Wait ended"
    elif item_type == "web_search":
        text = _web_search_text(item)
    elif item_type == "file_change":
        text = _file_change_text(item)
    else:
        text = _todo_list_text(item)
    if not text:
        return None
    result = {
        "kind": "activity",
        "text": text,
        "status": _event_status(payload, item),
        "activity_type": item_type,
    }
    operation_id = _clean_text(item.get("id"))
    if operation_id:
        result["operation_id"] = operation_id
    return result


def _web_search_text(item: Mapping[str, Any]) -> str:
    action = item.get("action")
    if isinstance(action, Mapping):
        queries = action.get("queries")
        if isinstance(queries, list):
            values = [_clean_text(query) for query in queries]
            values = [query for query in values if query]
            if values:
                return "\n".join(values)
    query = _clean_text(item.get("query"))
    return query or "..."


def _file_change_text(item: Mapping[str, Any]) -> str:
    changes = item.get("changes")
    if not isinstance(changes, list):
        return ""
    lines: list[str] = []
    markers = {
        "add": "+",
        "create": "+",
        "delete": "-",
        "remove": "-",
        "update": "~",
    }
    for change in changes:
        if not isinstance(change, Mapping):
            continue
        path = _display_path(_clean_text(change.get("path")))
        if not path:
            continue
        marker = markers.get(_clean_text(change.get("kind")).casefold(), "~")
        lines.append(f"{marker} {path}")
    return "\n".join(lines)


def _todo_list_text(item: Mapping[str, Any]) -> str:
    entries = item.get("items")
    if not isinstance(entries, list):
        return ""
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        text = _clean_text(entry.get("text"))
        if not text:
            continue
        marker = "[x]" if entry.get("completed") is True else "[ ]"
        lines.append(f"{marker} {text}")
    return "\n".join(lines)


def _event_status(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    status = _clean_text(item.get("status")).casefold()
    if status in {"completed", "complete", "success", "succeeded"}:
        return "completed"
    if status in {"failed", "failure", "error"}:
        return "failed"
    if status in {"cancelled", "canceled", "declined"}:
        return "cancelled"
    event_type = _clean_text(payload.get("type")).casefold()
    if event_type.endswith(".completed"):
        return "completed"
    if event_type.endswith(".failed"):
        return "failed"
    return "running"


def _display_path(path: str) -> str:
    marker = "/workspaces/"
    marker_index = path.find(marker)
    if marker_index < 0:
        return path
    tail = path[marker_index + len(marker) :]
    separator = tail.find("/")
    return tail[separator + 1 :] if separator >= 0 else tail


def _payload_kind(payload: Mapping[str, Any]) -> str:
    signals: list[str] = []
    _collect_signals(payload, signals)
    value = " ".join(signals).casefold()
    if "reasoning" in value or "thinking" in value:
        return "thought"
    if "agent_message" in value or "assistant" in value:
        return "output"
    if " message" in f" {value} " and "user" not in value:
        return "output"
    return "activity"


def _collect_signals(value: Mapping[str, Any], output: list[str]) -> None:
    for key in ("type", "event", "role"):
        signal = value.get(key)
        if isinstance(signal, str) and signal:
            output.append(signal)
    for key in _CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, Mapping):
            _collect_signals(nested, output)


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_first_text(item) for item in value)))
    if not isinstance(value, Mapping):
        return ""
    for key in _TEXT_KEYS:
        text = _first_text(value.get(key))
        if text:
            return text
    for key in _CONTAINER_KEYS:
        text = _first_text(value.get(key))
        if text:
            return text
    return ""


def _command_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(part) for part in value if isinstance(part, str)).strip()
    return ""


def _command_status(item: Mapping[str, Any]) -> str:
    exit_code = item.get("exit_code")
    if exit_code is not None and not isinstance(exit_code, bool):
        try:
            return "completed" if int(exit_code) == 0 else "failed"
        except (TypeError, ValueError):
            pass
    status = _clean_text(item.get("status")).casefold()
    if status in {"completed", "complete", "success", "succeeded"}:
        return "completed"
    if status in {"failed", "failure", "error"}:
        return "failed"
    if status in {"cancelled", "canceled", "declined"}:
        return "cancelled"
    return "running"


def _strip_agent_prefix(value: str) -> str:
    lowered = value.casefold()
    for prefix in ("agent_reasoning:", "agent_message:"):
        if lowered.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def _display_text(kind: str, value: str) -> str:
    if kind != "thought":
        return value
    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if (
            len(stripped) > 4
            and stripped[:2] == stripped[-2:]
            and stripped[:2] in {"**", "__"}
        ):
            lines.append(stripped[2:-2])
        else:
            lines.append(line)
    return "\n".join(lines)


def _strip_shell_wrapper(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if (
        len(parts) == 3
        and parts[0].rsplit("/", 1)[-1] in {"bash", "dash", "ksh", "sh", "zsh"}
        and parts[1] in {"-c", "-lc"}
    ):
        return parts[2]
    return command


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _integer(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
