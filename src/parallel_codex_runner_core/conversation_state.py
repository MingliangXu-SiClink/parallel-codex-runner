from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local lock
    fcntl = None

from .conversation_protocol import (
    AgentCandidateRecord,
    AgentActivityRecord,
    CandidateRole,
    CandidateStatus,
    ConversationRecord,
    ConversationSnapshot,
    MachineRecord,
    RunSettingsRecord,
    TurnPhase,
    TurnRecord,
    WorkspaceRecord,
)
from .models import DEFAULT_FOLLOW_UP_DELAY_SECONDS, DEFAULT_SUBAGENTS_LIMIT


CONVERSATION_STATE_VERSION = 1


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def local_machine_record(
    machine_id: str = "local",
    machine_name: str = "",
    updated_at: str = "",
) -> MachineRecord:
    return MachineRecord(
        machine_id=machine_id.strip() or "local",
        name=machine_name.strip() or platform.node().strip() or "Computer",
        platform=platform.system().lower() or "unknown",
        online=True,
        updated_at=updated_at or utc_now(),
    )


def workspace_id_for_path(path: str, machine_id: str = "local") -> str:
    normalized = str(Path(path).expanduser().resolve())
    digest = hashlib.sha256(f"{machine_id}\0{normalized}".encode("utf-8")).hexdigest()
    return f"workspace-{digest[:24]}"


def candidate_id(turn_id: str, agent: int) -> str:
    return f"{turn_id}:agent:{int(agent)}"


def _safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _conversation_title(prompt: str) -> str:
    collapsed = " ".join(str(prompt or "").split())
    if not collapsed:
        return "Untitled conversation"
    for marker in ("。", "！", "？", ".", "!", "?"):
        position = collapsed.find(marker)
        if 7 <= position < 55:
            return collapsed[: position + 1]
    return collapsed if len(collapsed) <= 56 else collapsed[:55] + "…"


_GENERIC_CONVERSATION_OPENERS = {
    "hello",
    "hey",
    "hi",
    "在吗",
    "在么",
    "您好",
    "你好",
    "你好呀",
    "嗨",
    "哈喽",
}


def _is_generic_conversation_opener(value: str) -> bool:
    normalized = " ".join(str(value or "").casefold().split()).strip(
        " \t\r\n,.!?，。！？~～"
    )
    return not normalized or normalized in _GENERIC_CONVERSATION_OPENERS


def _preferred_conversation_title(prompts: list[str]) -> str:
    values = [
        str(prompt or "").strip()
        for prompt in prompts
        if str(prompt or "").strip()
    ]
    if not values:
        return _conversation_title("")
    prompt = next(
        (value for value in values if not _is_generic_conversation_opener(value)),
        values[0],
    )
    return _conversation_title(prompt)


def _turn_phase(run: Mapping[str, Any], agents: list[Mapping[str, Any]]) -> TurnPhase:
    status = str(run.get("status") or "").strip().lower()
    direct = {
        "preparing": TurnPhase.PREPARING,
        "finalizing": TurnPhase.FINALIZING,
        "finalized": TurnPhase.FINALIZED,
        "discarded": TurnPhase.DISCARDED,
        "expired": TurnPhase.EXPIRED,
        "cleanup_failed": TurnPhase.CLEANUP_FAILED,
        "sync_ambiguous": TurnPhase.SYNC_AMBIGUOUS,
        "failed": TurnPhase.FAILED,
        "error": TurnPhase.FAILED,
        "stopped": TurnPhase.STOPPED,
        "cancelled": TurnPhase.STOPPED,
        "canceled": TurnPhase.STOPPED,
        "interrupted": TurnPhase.STOPPED,
    }
    if status in direct:
        return direct[status]
    active_statuses = {
        "queued",
        "copying",
        "starting",
        "resuming",
        "submitting",
        "waiting_for_output",
        "running",
        "waiting_for_models",
        "stopping",
    }
    if status in {"running", "starting"} or any(
        str(agent.get("status") or "").lower() in active_statuses for agent in agents
    ):
        synthesis_active = any(
            str(agent.get("role") or "candidate").lower() == "synthesis"
            and str(agent.get("status") or "").lower() in active_statuses
            for agent in agents
        )
        return TurnPhase.SYNTHESIS_RUNNING if synthesis_active else TurnPhase.PRIMARY_RUNNING
    has_success = any(
        str(agent.get("status") or "").lower() == "success" for agent in agents
    )
    if has_success:
        return TurnPhase.REVIEW
    if status == "completed" and agents:
        return TurnPhase.FAILED
    return TurnPhase.UNKNOWN


def _run_settings(config: Mapping[str, Any]) -> RunSettingsRecord:
    return RunSettingsRecord(
        num_agents=max(1, _safe_int(config.get("num_agents"), 1)),
        synthesis_agents=max(0, _safe_int(config.get("synthesis_agents"))),
        max_parallel=(
            max(1, _safe_int(config.get("max_parallel")))
            if config.get("max_parallel") is not None
            else None
        ),
        serial=bool(config.get("serial")),
        recommend_by=str(config.get("recommend_by") or "reasoning_tokens"),
        model=_optional_text(config.get("model")),
        effort=_optional_text(config.get("effort")),
        fast=config.get("fast") if isinstance(config.get("fast"), bool) else None,
        synthesis_model=_optional_text(config.get("synthesis_model")),
        synthesis_effort=_optional_text(config.get("synthesis_effort")),
        synthesis_fast=(
            config.get("synthesis_fast")
            if isinstance(config.get("synthesis_fast"), bool)
            else None
        ),
        follow_up_delay=max(
            0,
            _safe_int(
                config.get("follow_up_delay"), DEFAULT_FOLLOW_UP_DELAY_SECONDS
            ),
        ),
        subagents=bool(config.get("subagents")),
        subagents_limit=max(
            1,
            _safe_int(config.get("subagents_limit"), DEFAULT_SUBAGENTS_LIMIT),
        ),
        sync_back=bool(config.get("sync_back", True)),
        keep_workspaces=bool(config.get("keep_workspaces", False)),
        resume_session_id=_optional_text(config.get("resume_session_id")),
        runs_dir=_optional_text(config.get("runs_dir")),
        codex_bin=str(config.get("codex_bin") or "codex"),
    )


class ConversationStateStore:
    """Durable user-domain state, separate from technical PCR run state."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.path = self.state_dir / "conversations.json"
        self.lock_path = self.state_dir / "conversations.lock"
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._batch_active = False
        self._batch_changed = False
        self.machines: dict[str, MachineRecord] = {}
        self.workspaces: dict[str, WorkspaceRecord] = {}
        self.conversations: dict[str, ConversationRecord] = {}
        self.turns: dict[str, TurnRecord] = {}
        self.candidates: dict[str, AgentCandidateRecord] = {}
        # Read paths (list_conversations, snapshot_for_run, get_conversation,
        # candidate_location, run_id_for_turn) each re-parsed conversations.json
        # on every call. Cache the file's (mtime_ns, size) so a read whose file
        # has not changed reuses the in-memory dicts. Any writer (this process
        # or a peer pcrd/MCP instance) does an atomic os.replace which always
        # changes mtime, so the cache is never stale across processes.
        self._file_stamp: tuple[int, int] | None = None  # (mtime_ns, size)
        self._load()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            if self._lock_depth > 0:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            handle = self.lock_path.open("a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._lock_depth = 1
                yield
            finally:
                self._lock_depth = 0
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()

    def _load(self) -> None:
        with self._locked():
            self._load_locked()

    def _load_locked(self) -> None:
        # Skip the disk read + JSON parse + dict rebuild when conversations.json
        # has not changed since the last load. Any writer (this process or a
        # peer pcrd/MCP instance sharing the state dir) does an atomic
        # os.replace which always changes mtime, so the cache is never stale.
        try:
            st = self.path.stat()
            current_stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            current_stamp = None
        if current_stamp is not None and current_stamp == self._file_stamp:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._file_stamp = None
            return
        except (OSError, ValueError):
            return
        self._file_stamp = current_stamp
        if not isinstance(payload, dict):
            return
        self.machines = {
            item.machine_id: item
            for raw in payload.get("machines", [])
            if isinstance(raw, dict)
            for item in [MachineRecord.from_mapping(raw)]
            if item.machine_id
        }
        self.workspaces = {
            item.workspace_id: item
            for raw in payload.get("workspaces", [])
            if isinstance(raw, dict)
            for item in [WorkspaceRecord.from_mapping(raw)]
            if item.workspace_id
        }
        self.conversations = {
            item.conversation_id: item
            for raw in payload.get("conversations", [])
            if isinstance(raw, dict)
            for item in [ConversationRecord.from_mapping(raw)]
            if item.conversation_id
        }
        self.turns = {
            item.turn_id: item
            for raw in payload.get("turns", [])
            if isinstance(raw, dict)
            for item in [TurnRecord.from_mapping(raw)]
            if item.turn_id
        }
        self.candidates = {
            item.candidate_id: item
            for raw in payload.get("candidates", [])
            if isinstance(raw, dict)
            for item in [AgentCandidateRecord.from_mapping(raw)]
            if item.candidate_id
        }

    def _write_locked(self) -> None:
        payload = {
            "version": CONVERSATION_STATE_VERSION,
            "machines": [item.to_mapping() for item in self.machines.values()],
            "workspaces": [item.to_mapping() for item in self.workspaces.values()],
            "conversations": [
                item.to_mapping() for item in self.conversations.values()
            ],
            "turns": [item.to_mapping() for item in self.turns.values()],
            "candidates": [item.to_mapping() for item in self.candidates.values()],
        }
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        # Record the stamp of the file we just wrote so the in-memory dicts
        # (already current) are not overwritten by a redundant re-parse on the
        # next read. A peer process's stale stamp will still differ and re-load.
        try:
            st = self.path.stat()
            self._file_stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._file_stamp = None

    def upsert_run(
        self,
        run: Mapping[str, Any],
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
    ) -> ConversationSnapshot:
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required to build a conversation snapshot")
        workspace_path = str(run.get("workspace") or "").strip()
        raw_turn_id = str(run.get("turn_id") or "").strip()
        raw_conversation_id = str(run.get("conversation_id") or "").strip()
        config = run.get("config") if isinstance(run.get("config"), Mapping) else {}
        raw_agents = [
            item for item in run.get("agents", []) if isinstance(item, Mapping)
        ]
        timestamp = str(run.get("updated_at") or utc_now())
        created_at = str(run.get("created_at") or timestamp)

        with self._locked():
            # A pcrd and a plugin MCP process may share this store. Merge the
            # latest durable state while holding the cross-process lock.
            if not self._batch_active:
                self._load_locked()
            previous_turn = next(
                (item for item in self.turns.values() if item.run_id == run_id),
                None,
            )
            previous_conversation = (
                self.conversations.get(previous_turn.conversation_id)
                if previous_turn
                else None
            )
            previous_workspace = (
                self.workspaces.get(previous_conversation.workspace_id)
                if previous_conversation
                else None
            )
            previous_machine = (
                self.machines.get(previous_workspace.machine_id)
                if previous_workspace
                else None
            )
            # Workers intentionally keep the PCR version they started with.
            # A pre-conversation worker can therefore write a run record that
            # lacks the newer lineage fields. Reuse the durable conversation
            # record instead of splitting that turn into a new conversation.
            turn_id = raw_turn_id or (
                previous_turn.turn_id if previous_turn else f"turn-{run_id}"
            )
            conversation_id = raw_conversation_id or (
                previous_turn.conversation_id if previous_turn else turn_id
            )
            resolved_machine_id = str(machine_id or "").strip() or "local"
            if previous_machine is not None and resolved_machine_id == "local":
                resolved_machine_id = previous_machine.machine_id
            resolved_machine = local_machine_record(
                resolved_machine_id,
                machine_name or (previous_machine.name if previous_machine else ""),
                max(
                    timestamp,
                    previous_machine.updated_at if previous_machine else timestamp,
                ),
            )
            resolved_workspace_id = (
                workspace_id.strip()
                or (previous_workspace.workspace_id if previous_workspace else "")
                or workspace_id_for_path(workspace_path, resolved_machine.machine_id)
            )
            changed = self.machines.get(resolved_machine.machine_id) != resolved_machine
            self.machines[resolved_machine.machine_id] = resolved_machine
            workspace = WorkspaceRecord(
                workspace_id=resolved_workspace_id,
                machine_id=resolved_machine.machine_id,
                name=Path(workspace_path).name or workspace_path or "Workspace",
                path=workspace_path,
                available=Path(workspace_path).is_dir() if workspace_path else False,
                updated_at=max(
                    timestamp,
                    previous_workspace.updated_at if previous_workspace else timestamp,
                ),
            )
            changed = changed or self.workspaces.get(workspace.workspace_id) != workspace
            self.workspaces[workspace.workspace_id] = workspace

            previous_turn_id = None
            previous_run_id = str(run.get("continued_from_run_id") or "").strip()
            if previous_run_id:
                previous_turn_id = next(
                    (
                        item.turn_id
                        for item in self.turns.values()
                        if item.run_id == previous_run_id
                    ),
                    f"turn-{previous_run_id}",
                )
            previous_agent = _safe_int(run.get("continued_from_agent"))
            previous_candidate_id = (
                candidate_id(previous_turn_id, previous_agent)
                if previous_turn_id and previous_agent > 0
                else None
            )

            candidates: list[AgentCandidateRecord] = []
            for raw in raw_agents:
                agent_index = _safe_int(raw.get("agent") or raw.get("idx"))
                if agent_index <= 0:
                    continue
                item_id = candidate_id(turn_id, agent_index)
                role = CandidateRole.from_value(raw.get("role") or "candidate")
                status = CandidateStatus.from_value(raw.get("status") or "unknown")
                counts = raw.get("reasoning_token_counts")
                reasoning_counts = {
                    str(key): max(0, _safe_int(value))
                    for key, value in (counts.items() if isinstance(counts, Mapping) else [])
                    if _safe_int(value) > 0
                }
                item = AgentCandidateRecord(
                    candidate_id=item_id,
                    turn_id=turn_id,
                    agent_index=agent_index,
                    display_name=str(raw.get("name") or f"AGENT-{agent_index:03d}"),
                    role=role,
                    status=status,
                    seconds=_safe_float(raw.get("seconds")),
                    started_at=_optional_text(raw.get("started_at")),
                    finished_at=_optional_text(raw.get("finished_at")),
                    activity=AgentActivityRecord.from_mapping(raw["activity"]) if isinstance(raw.get("activity"), Mapping) else None,
                    reasoning_tokens=(
                        _safe_int(raw.get("reasoning_tokens"))
                        if raw.get("reasoning_tokens") is not None
                        else None
                    ),
                    reasoning_token_counts=reasoning_counts,
                    codex_thread_id=_optional_text(raw.get("codex_thread_id")),
                    error=_optional_text(raw.get("error")),
                    rejected=bool(raw.get("rejected")),
                    stale=bool(raw.get("stale")),
                    recommended=bool(raw.get("recommended")),
                    finalized=bool(raw.get("finalized")),
                    workspace_dir=_optional_text(raw.get("workspace_dir")),
                    workspace_available=bool(raw.get("workspace_available")),
                    can_accept=bool(raw.get("can_accept")),
                    can_continue=bool(raw.get("can_continue")),
                    can_retry=bool(raw.get("can_retry")),
                )
                changed = changed or self.candidates.get(item_id) != item
                self.candidates[item_id] = item
                candidates.append(item)

            recommended_agent = _safe_int(run.get("recommended_agent"))
            finalized_agent = _safe_int(run.get("finalized_agent"))
            turn = TurnRecord(
                turn_id=turn_id,
                conversation_id=conversation_id,
                run_id=run_id,
                turn_index=max(0, _safe_int(run.get("turn_index"))),
                prompt=str(run.get("prompt") or ""),
                phase=_turn_phase(run, raw_agents),
                settings=_run_settings(config),
                created_at=created_at,
                updated_at=timestamp,
                continued_from_turn_id=previous_turn_id,
                continued_from_candidate_id=previous_candidate_id,
                recommended_candidate_id=(
                    candidate_id(turn_id, recommended_agent)
                    if recommended_agent > 0
                    else None
                ),
                finalized_candidate_id=(
                    candidate_id(turn_id, finalized_agent)
                    if finalized_agent > 0
                    else None
                ),
                candidate_ids=[item.candidate_id for item in candidates],
            )
            changed = changed or self.turns.get(turn_id) != turn
            self.turns[turn_id] = turn

            current_conversation = self.conversations.get(conversation_id)
            title_owner = current_conversation
            if title_owner is None:
                resumed_id = _optional_text(config.get("resume_session_id"))
                title_owner = next((item for item in self.conversations.values()
                    if resumed_id and item.workspace_id == workspace.workspace_id
                    and item.resume_session_id == resumed_id and item.custom_title), None)
            turn_ids = list(current_conversation.turn_ids) if current_conversation else []
            if turn_id not in turn_ids:
                turn_ids.append(turn_id)
            turn_ids.sort(
                key=lambda value: (
                    self.turns.get(value, turn).turn_index,
                    self.turns.get(value, turn).created_at,
                    value,
                )
            )
            active_turn = self.turns[turn_ids[-1]]
            is_active_turn = active_turn.turn_id == turn_id
            promotion = run.get("session_promotion")
            promoted_session = (
                _optional_text(promotion.get("session_id"))
                if isinstance(promotion, Mapping)
                else None
            )
            resume_session_id = (
                current_conversation.resume_session_id
                if current_conversation
                else None
            )
            if is_active_turn:
                resume_session_id = (
                    promoted_session
                    or _optional_text(config.get("resume_session_id"))
                    or resume_session_id
                )
            custom_title = title_owner.custom_title if title_owner else None
            existing_title = current_conversation.title if current_conversation else ""
            conversation_title = existing_title
            if custom_title is not None:
                conversation_title = custom_title
            elif _is_generic_conversation_opener(existing_title):
                conversation_title = _preferred_conversation_title(
                    [self.turns[value].prompt for value in turn_ids if value in self.turns]
                )
            conversation = ConversationRecord(
                conversation_id=conversation_id,
                workspace_id=workspace.workspace_id,
                title=conversation_title,
                custom_title=custom_title,
                title_revision=(title_owner.title_revision or 0) if title_owner else 0,
                created_at=(
                    current_conversation.created_at
                    if current_conversation
                    else created_at
                ),
                updated_at=max(
                    timestamp,
                    current_conversation.updated_at
                    if current_conversation
                    else timestamp,
                ),
                active_turn_id=active_turn.turn_id,
                resume_session_id=resume_session_id,
                turn_ids=turn_ids,
            )
            changed = changed or self.conversations.get(conversation_id) != conversation
            self.conversations[conversation_id] = conversation
            if changed:
                if self._batch_active:
                    self._batch_changed = True
                else:
                    self._write_locked()
            return ConversationSnapshot(
                schema_version=CONVERSATION_STATE_VERSION,
                machine=resolved_machine,
                workspace=workspace,
                conversation=conversation,
                turn=turn,
                candidates=candidates,
            )

    def upsert_runs(
        self,
        runs: list[Mapping[str, Any]],
        *,
        machine_id: str = "local",
        machine_name: str = "",
        workspace_id: str = "",
    ) -> list[ConversationSnapshot]:
        """Merge one run page with a single state load and atomic write."""
        if not runs:
            return []
        with self._locked():
            if self._batch_active:
                raise RuntimeError("Nested conversation state batches are not supported")
            self._load_locked()
            self._batch_active = True
            self._batch_changed = False
            try:
                snapshots = [
                    self.upsert_run(
                        run,
                        machine_id=machine_id,
                        machine_name=machine_name,
                        workspace_id=workspace_id,
                    )
                    for run in runs
                ]
                if self._batch_changed:
                    self._write_locked()
                return snapshots
            except BaseException:
                # Discard an incomplete in-memory batch. The durable file was
                # not replaced, so reloading restores the last complete state.
                self._load_locked()
                raise
            finally:
                self._batch_active = False
                self._batch_changed = False

    def snapshot_for_run(self, run_id: str) -> ConversationSnapshot | None:
        with self._locked():
            self._load_locked()
            turn = next(
                (item for item in self.turns.values() if item.run_id == run_id),
                None,
            )
            return self._snapshot_for_turn_locked(turn) if turn else None

    def snapshot_for_turn(self, turn_id: str) -> ConversationSnapshot | None:
        with self._locked():
            self._load_locked()
            return self._snapshot_for_turn_locked(self.turns.get(turn_id))

    def _snapshot_for_turn_locked(self, turn: TurnRecord | None) -> ConversationSnapshot | None:
        if turn is None:
            return None
        conversation = self.conversations.get(turn.conversation_id)
        if conversation is None:
            return None
        workspace = self.workspaces.get(conversation.workspace_id)
        if workspace is None:
            return None
        machine = self.machines.get(workspace.machine_id)
        if machine is None:
            return None
        return ConversationSnapshot(
            schema_version=CONVERSATION_STATE_VERSION,
            machine=machine,
            workspace=workspace,
            conversation=conversation,
            turn=turn,
            candidates=[
                self.candidates[value]
                for value in turn.candidate_ids
                if value in self.candidates
            ],
        )

    def run_id_for_turn(self, turn_id: str) -> str | None:
        with self._locked():
            self._load_locked()
            turn = self.turns.get(turn_id)
            return turn.run_id if turn else None

    def candidate_location(self, value: str) -> tuple[str, int] | None:
        with self._locked():
            self._load_locked()
            candidate = self.candidates.get(value)
            if candidate is None:
                return None
            turn = self.turns.get(candidate.turn_id)
            if turn is None:
                return None
            return turn.run_id, candidate.agent_index

    def conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._locked():
            self._load_locked()
            return self.conversations.get(conversation_id)

    def rename_conversation(
        self, conversation_id: str, title: str, *, workspace_id: str,
        resume_session_id: str = "", workspace_path: str = "",
        machine_id: str = "local", machine_name: str = "",
    ) -> ConversationRecord:
        title = " ".join(title.split())
        if not title or len(title) > 200:
            raise ValueError("Conversation title must contain 1 to 200 characters")
        with self._locked():
            self._load_locked()
            current = self.conversations.get(conversation_id)
            if current is None:
                if not resume_session_id or conversation_id != f"resume:{resume_session_id}":
                    raise ValueError(f"Unknown conversation_id: {conversation_id}")
                now = utc_now()
                machine = local_machine_record(machine_id, machine_name, now)
                self.machines[machine.machine_id] = machine
                self.workspaces[workspace_id] = WorkspaceRecord(
                    workspace_id=workspace_id, machine_id=machine.machine_id,
                    name=Path(workspace_path).name, path=workspace_path, updated_at=now,
                )
                current = ConversationRecord(
                    conversation_id=conversation_id, workspace_id=workspace_id,
                    created_at=now, updated_at=now, resume_session_id=resume_session_id,
                )
            if current.workspace_id != workspace_id:
                raise ValueError("Conversation belongs to another workspace")
            if current.custom_title == title:
                return current
            renamed = replace(current, title=title, custom_title=title,
                title_revision=(current.title_revision or 0) + 1, updated_at=utc_now())
            self.conversations[conversation_id] = renamed
            self._write_locked()
            return renamed

    def list_conversations(
        self,
        *,
        machine_id: str = "",
        workspace_id: str = "",
    ) -> list[ConversationRecord]:
        with self._locked():
            self._load_locked()
            values = list(self.conversations.values())
            if workspace_id:
                values = [item for item in values if item.workspace_id == workspace_id]
            if machine_id:
                allowed = {
                    item.workspace_id
                    for item in self.workspaces.values()
                    if item.machine_id == machine_id
                }
                values = [item for item in values if item.workspace_id in allowed]
            return sorted(
                values,
                key=lambda item: (item.updated_at, item.conversation_id),
                reverse=True,
            )
