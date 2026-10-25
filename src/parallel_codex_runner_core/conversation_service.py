from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any, Mapping

from .conversation_state import ConversationStateStore
from .plugin_runtime import PluginRunManager


DEFAULT_CONVERSATION_LIST_PAGE_BYTES = 512 * 1024
MAX_CONVERSATION_LIST_PAGE_BYTES = 640 * 1024
MAX_CONVERSATION_LIST_PAGE_SIZE = 100


def _conversation_cursor(updated_at: str, conversation_id: str) -> str:
    raw = json.dumps(
        {"updated_at": updated_at, "conversation_id": conversation_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parse_conversation_cursor(value: str) -> tuple[str, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at = str(payload["updated_at"]).strip()
        conversation_id = str(payload["conversation_id"]).strip()
        if not updated_at or not conversation_id or len(conversation_id) > 512:
            raise ValueError
        return updated_at, conversation_id
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("Conversation list cursor is invalid") from exc


class ConversationApplicationService:
    """One start, continue, inspect, and finalize flow for every PCR client."""

    def __init__(
        self,
        manager: PluginRunManager,
        state_dir: Path | None = None,
    ) -> None:
        self.manager = manager
        self.state = ConversationStateStore(state_dir or manager.state_dir)

    def _attach_snapshot(
        self,
        result: Mapping[str, Any],
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
    ) -> dict[str, Any]:
        response = dict(result)
        run = response.get("run")
        if not isinstance(run, Mapping) and "run_id" in response and "agents" in response:
            run = response
        if isinstance(run, Mapping) and run.get("run_id"):
            snapshot = self.state.upsert_run(
                run,
                machine_id=machine_id,
                machine_name=machine_name,
                workspace_id=workspace_id,
            )
            response["conversation_snapshot"] = snapshot.to_mapping()
            if isinstance(response.get("run"), Mapping):
                enriched_run = dict(response["run"])
                enriched_run["conversation_snapshot"] = snapshot.to_mapping()
                response["run"] = enriched_run
        return response

    def start_turn(
        self,
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._attach_snapshot(
            self.manager.start_run(**kwargs),
            machine_id=machine_id,
            machine_name=machine_name,
            workspace_id=workspace_id,
        )

    def get_turn(
        self,
        *,
        run_id: str = "",
        turn_id: str = "",
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or self.state.run_id_for_turn(turn_id) or ""
        if not resolved_run_id:
            raise ValueError("run_id or a known turn_id is required")
        return self._attach_snapshot(
            self.manager.get_run(resolved_run_id, **kwargs),
            machine_id=machine_id,
            machine_name=machine_name,
            workspace_id=workspace_id,
        )

    def continue_turn(
        self,
        *,
        run_id: str,
        agent: int,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        get_run = getattr(self.manager, "get_run", None)
        if callable(get_run):
            self._attach_snapshot(
                get_run(run_id),
                machine_id=machine_id,
                machine_name=machine_name,
                workspace_id=workspace_id,
            )
        return self._attach_snapshot(
            self.manager.continue_from_agent(run_id, agent, **kwargs),
            machine_id=machine_id,
            machine_name=machine_name,
            workspace_id=workspace_id,
        )

    def finalize_candidate(
        self,
        *,
        run_id: str = "",
        agent: int | None = None,
        candidate_id: str = "",
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if candidate_id:
            location = self.state.candidate_location(candidate_id)
            if location is None:
                raise ValueError(f"Unknown candidate_id: {candidate_id}")
            run_id, agent = location
        if not run_id or agent is None:
            raise ValueError("run_id and agent, or candidate_id, are required")
        return self._attach_snapshot(
            self.manager.accept_agent(run_id, int(agent), **kwargs),
            machine_id=machine_id,
            machine_name=machine_name,
            workspace_id=workspace_id,
        )

    def synchronize_run(
        self,
        run: Mapping[str, Any],
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
    ) -> dict[str, Any]:
        return self._attach_snapshot(
            run,
            machine_id=machine_id,
            machine_name=machine_name,
            workspace_id=workspace_id,
        )

    def synchronize_runs(
        self,
        runs: list[Mapping[str, Any]],
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
    ) -> list[dict[str, Any]]:
        return [
            snapshot.to_mapping()
            for snapshot in self.state.upsert_runs(
                runs,
                machine_id=machine_id,
                machine_name=machine_name,
                workspace_id=workspace_id,
            )
        ]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.state.conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Unknown conversation_id: {conversation_id}")
        snapshots = [
            snapshot.to_mapping()
            for turn_id in conversation.turn_ids
            for snapshot in [self.state.snapshot_for_turn(turn_id)]
            if snapshot is not None
        ]
        return {
            "conversation": conversation.to_mapping(),
            "turns": snapshots,
        }

    def list_conversations(
        self,
        *,
        machine_id: str = "",
        workspace_id: str = "",
        limit: int = MAX_CONVERSATION_LIST_PAGE_SIZE,
        cursor: str = "",
        max_bytes: int = DEFAULT_CONVERSATION_LIST_PAGE_BYTES,
    ) -> dict[str, Any]:
        values = self.state.list_conversations(
            machine_id=machine_id,
            workspace_id=workspace_id,
        )
        total = len(values)
        boundary = _parse_conversation_cursor(cursor) if cursor else None
        if boundary is not None:
            values = [
                item
                for item in values
                if (item.updated_at, item.conversation_id) < boundary
            ]

        page_limit = min(max(1, int(limit)), MAX_CONVERSATION_LIST_PAGE_SIZE)
        byte_limit = min(
            max(4 * 1024, int(max_bytes)),
            MAX_CONVERSATION_LIST_PAGE_BYTES,
        )
        page: list[dict[str, Any]] = []
        serialized_bytes = 2
        oversized_conversation = False
        for item in values:
            if len(page) >= page_limit:
                break
            payload = item.to_mapping()
            encoded_size = len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            separator = 1 if page else 0
            if page and serialized_bytes + separator + encoded_size > byte_limit:
                break
            if not page and serialized_bytes + encoded_size > byte_limit:
                oversized_conversation = True
            page.append(payload)
            serialized_bytes += separator + encoded_size

        has_more = len(page) < len(values)
        next_cursor = ""
        if has_more and page:
            last = page[-1]
            next_cursor = _conversation_cursor(
                str(last.get("updated_at") or ""),
                str(last.get("conversation_id") or ""),
            )
        return {
            "conversations": page,
            "total": total,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "serialized_bytes": serialized_bytes,
            "byte_limit": byte_limit,
            "oversized_conversation": oversized_conversation,
        }
