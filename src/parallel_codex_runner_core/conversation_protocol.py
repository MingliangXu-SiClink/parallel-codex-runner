from __future__ import annotations

"""Generated from remote/protocol/pcr-conversation-v1.schema.json. Do not edit."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

CONVERSATION_SCHEMA_VERSION = 1


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _encode(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_mapping"):
        return value.to_mapping()  # type: ignore[union-attr]
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    return value


class WorkOperationKind(str, Enum):
    FILE_READ = "file_read"
    FILE_SEARCH = "file_search"
    DIRECTORY_LIST = "directory_list"
    COMMAND = "command"
    FILE_CHANGE = "file_change"
    WEB_SEARCH = "web_search"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> "WorkOperationKind":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.UNKNOWN


class WorkOperationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> "WorkOperationStatus":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.UNKNOWN


class WorkFileChangeKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RENAME = "rename"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> "WorkFileChangeKind":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.UNKNOWN


class TurnPhase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    PRIMARY_RUNNING = "primary_running"
    SYNTHESIS_RUNNING = "synthesis_running"
    REVIEW = "review"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    STOPPED = "stopped"
    FAILED = "failed"
    DISCARDED = "discarded"
    EXPIRED = "expired"
    CLEANUP_FAILED = "cleanup_failed"
    SYNC_AMBIGUOUS = "sync_ambiguous"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> "TurnPhase":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.UNKNOWN


class CandidateRole(str, Enum):
    CANDIDATE = "candidate"
    SYNTHESIS = "synthesis"

    @classmethod
    def from_value(cls, value: object) -> "CandidateRole":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.CANDIDATE


class CandidateStatus(str, Enum):
    QUEUED = "queued"
    COPYING = "copying"
    STARTING = "starting"
    RESUMING = "resuming"
    SUBMITTING = "submitting"
    WAITING_FOR_OUTPUT = "waiting_for_output"
    RUNNING = "running"
    WAITING_FOR_MODELS = "waiting_for_models"
    STOPPING = "stopping"
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"
    KILLED = "killed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"
    DISCARDED = "discarded"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: object) -> "CandidateStatus":
        try:
            return cls(str(value or ""))
        except ValueError:
            return cls.UNKNOWN


@dataclass
class WorkOperationTarget:
    path: str = ""
    change: WorkFileChangeKind = WorkFileChangeKind.UNKNOWN
    added: int | None = None
    deleted: int | None = None

    @classmethod
    def from_mapping(cls, value: object) -> "WorkOperationTarget":
        payload = _as_dict(value)
        return cls(
            path=str(payload.get("path") or ""),
            change=WorkFileChangeKind.from_value(payload.get("change")),
            added=None if payload.get("added") is None else _as_int(payload.get("added")),
            deleted=None if payload.get("deleted") is None else _as_int(payload.get("deleted")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": _encode(self.path),
            "change": _encode(self.change),
            "added": _encode(self.added),
            "deleted": _encode(self.deleted),
        }


@dataclass
class WorkOperationRecord:
    id: str = ""
    event_id: int = 0
    kind: WorkOperationKind = WorkOperationKind.UNKNOWN
    status: WorkOperationStatus = WorkOperationStatus.UNKNOWN
    command: str | None = None
    targets: list[WorkOperationTarget] = field(default_factory=list)
    query: str | None = None
    duration_ms: int | None = None
    duration_source: str | None = None
    planned_duration_ms: int | None = None
    wait_until: str | None = None
    output_bytes: int | None = None
    exit_code: int | None = None
    details_available: bool = False

    @classmethod
    def from_mapping(cls, value: object) -> "WorkOperationRecord":
        payload = _as_dict(value)
        return cls(
            id=str(payload.get("id") or ""),
            event_id=_as_int(payload.get("event_id")),
            kind=WorkOperationKind.from_value(payload.get("kind")),
            status=WorkOperationStatus.from_value(payload.get("status")),
            command=None if payload.get("command") is None else str(payload.get("command") or ""),
            targets=[WorkOperationTarget.from_mapping(item) for item in _as_list(payload.get("targets"))],
            query=None if payload.get("query") is None else str(payload.get("query") or ""),
            duration_ms=None if payload.get("duration_ms") is None else _as_int(payload.get("duration_ms")),
            duration_source=None if payload.get("duration_source") is None else str(payload.get("duration_source") or ""),
            planned_duration_ms=None if payload.get("planned_duration_ms") is None else _as_int(payload.get("planned_duration_ms")),
            wait_until=None if payload.get("wait_until") is None else str(payload.get("wait_until") or ""),
            output_bytes=None if payload.get("output_bytes") is None else _as_int(payload.get("output_bytes")),
            exit_code=None if payload.get("exit_code") is None else _as_int(payload.get("exit_code")),
            details_available=bool(payload.get("details_available")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": _encode(self.id),
            "event_id": _encode(self.event_id),
            "kind": _encode(self.kind),
            "status": _encode(self.status),
            "command": _encode(self.command),
            "targets": _encode(self.targets),
            "query": _encode(self.query),
            "duration_ms": _encode(self.duration_ms),
            "duration_source": _encode(self.duration_source),
            "planned_duration_ms": _encode(self.planned_duration_ms),
            "wait_until": _encode(self.wait_until),
            "output_bytes": _encode(self.output_bytes),
            "exit_code": _encode(self.exit_code),
            "details_available": _encode(self.details_available),
        }


@dataclass
class WorkOperationDetailPage:
    text: str = ""
    next_cursor: int = 0
    has_more: bool = False
    total_bytes: int = 0
    availability: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "WorkOperationDetailPage":
        payload = _as_dict(value)
        return cls(
            text=str(payload.get("text") or ""),
            next_cursor=_as_int(payload.get("next_cursor")),
            has_more=bool(payload.get("has_more")),
            total_bytes=_as_int(payload.get("total_bytes")),
            availability=str(payload.get("availability") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "text": _encode(self.text),
            "next_cursor": _encode(self.next_cursor),
            "has_more": _encode(self.has_more),
            "total_bytes": _encode(self.total_bytes),
            "availability": _encode(self.availability),
        }


@dataclass
class AgentActivityRecord:
    state: str = ""
    observed_at: str | None = None
    last_output_at: str | None = None
    wait_until: str | None = None
    active_operation_count: int = 0

    @classmethod
    def from_mapping(cls, value: object) -> "AgentActivityRecord":
        payload = _as_dict(value)
        return cls(
            state=str(payload.get("state") or ""),
            observed_at=None if payload.get("observed_at") is None else str(payload.get("observed_at") or ""),
            last_output_at=None if payload.get("last_output_at") is None else str(payload.get("last_output_at") or ""),
            wait_until=None if payload.get("wait_until") is None else str(payload.get("wait_until") or ""),
            active_operation_count=_as_int(payload.get("active_operation_count")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "state": _encode(self.state),
            "observed_at": _encode(self.observed_at),
            "last_output_at": _encode(self.last_output_at),
            "wait_until": _encode(self.wait_until),
            "active_operation_count": _encode(self.active_operation_count),
        }


@dataclass
class RunSettingsRecord:
    num_agents: int = 0
    synthesis_agents: int = 0
    max_parallel: int | None = None
    serial: bool = False
    recommend_by: str = ""
    model: str | None = None
    effort: str | None = None
    fast: bool | None = None
    synthesis_model: str | None = None
    synthesis_effort: str | None = None
    synthesis_fast: bool | None = None
    follow_up_delay: int = 0
    subagents: bool = False
    subagents_limit: int = 0
    sync_back: bool = False
    keep_workspaces: bool = False
    resume_session_id: str | None = None
    runs_dir: str | None = None
    codex_bin: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "RunSettingsRecord":
        payload = _as_dict(value)
        return cls(
            num_agents=_as_int(payload.get("num_agents")),
            synthesis_agents=_as_int(payload.get("synthesis_agents")),
            max_parallel=None if payload.get("max_parallel") is None else _as_int(payload.get("max_parallel")),
            serial=bool(payload.get("serial")),
            recommend_by=str(payload.get("recommend_by") or ""),
            model=None if payload.get("model") is None else str(payload.get("model") or ""),
            effort=None if payload.get("effort") is None else str(payload.get("effort") or ""),
            fast=None if payload.get("fast") is None else bool(payload.get("fast")),
            synthesis_model=None if payload.get("synthesis_model") is None else str(payload.get("synthesis_model") or ""),
            synthesis_effort=None if payload.get("synthesis_effort") is None else str(payload.get("synthesis_effort") or ""),
            synthesis_fast=None if payload.get("synthesis_fast") is None else bool(payload.get("synthesis_fast")),
            follow_up_delay=_as_int(payload.get("follow_up_delay")),
            subagents=bool(payload.get("subagents")),
            subagents_limit=_as_int(payload.get("subagents_limit")),
            sync_back=bool(payload.get("sync_back")),
            keep_workspaces=bool(payload.get("keep_workspaces")),
            resume_session_id=None if payload.get("resume_session_id") is None else str(payload.get("resume_session_id") or ""),
            runs_dir=None if payload.get("runs_dir") is None else str(payload.get("runs_dir") or ""),
            codex_bin=str(payload.get("codex_bin") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "num_agents": _encode(self.num_agents),
            "synthesis_agents": _encode(self.synthesis_agents),
            "max_parallel": _encode(self.max_parallel),
            "serial": _encode(self.serial),
            "recommend_by": _encode(self.recommend_by),
            "model": _encode(self.model),
            "effort": _encode(self.effort),
            "fast": _encode(self.fast),
            "synthesis_model": _encode(self.synthesis_model),
            "synthesis_effort": _encode(self.synthesis_effort),
            "synthesis_fast": _encode(self.synthesis_fast),
            "follow_up_delay": _encode(self.follow_up_delay),
            "subagents": _encode(self.subagents),
            "subagents_limit": _encode(self.subagents_limit),
            "sync_back": _encode(self.sync_back),
            "keep_workspaces": _encode(self.keep_workspaces),
            "resume_session_id": _encode(self.resume_session_id),
            "runs_dir": _encode(self.runs_dir),
            "codex_bin": _encode(self.codex_bin),
        }


@dataclass
class MachineRecord:
    machine_id: str = ""
    name: str = ""
    platform: str = ""
    online: bool = False
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "MachineRecord":
        payload = _as_dict(value)
        return cls(
            machine_id=str(payload.get("machine_id") or ""),
            name=str(payload.get("name") or ""),
            platform=str(payload.get("platform") or ""),
            online=bool(payload.get("online")),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "machine_id": _encode(self.machine_id),
            "name": _encode(self.name),
            "platform": _encode(self.platform),
            "online": _encode(self.online),
            "updated_at": _encode(self.updated_at),
        }


@dataclass
class WorkspaceRecord:
    workspace_id: str = ""
    machine_id: str = ""
    name: str = ""
    path: str = ""
    available: bool = False
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "WorkspaceRecord":
        payload = _as_dict(value)
        return cls(
            workspace_id=str(payload.get("workspace_id") or ""),
            machine_id=str(payload.get("machine_id") or ""),
            name=str(payload.get("name") or ""),
            path=str(payload.get("path") or ""),
            available=bool(payload.get("available")),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "workspace_id": _encode(self.workspace_id),
            "machine_id": _encode(self.machine_id),
            "name": _encode(self.name),
            "path": _encode(self.path),
            "available": _encode(self.available),
            "updated_at": _encode(self.updated_at),
        }


@dataclass
class ConversationRecord:
    conversation_id: str = ""
    workspace_id: str = ""
    title: str = ""
    custom_title: str | None = None
    title_revision: int | None = None
    created_at: str = ""
    updated_at: str = ""
    active_turn_id: str | None = None
    resume_session_id: str | None = None
    turn_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: object) -> "ConversationRecord":
        payload = _as_dict(value)
        return cls(
            conversation_id=str(payload.get("conversation_id") or ""),
            workspace_id=str(payload.get("workspace_id") or ""),
            title=str(payload.get("title") or ""),
            custom_title=None if payload.get("custom_title") is None else str(payload.get("custom_title") or ""),
            title_revision=None if payload.get("title_revision") is None else _as_int(payload.get("title_revision")),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            active_turn_id=None if payload.get("active_turn_id") is None else str(payload.get("active_turn_id") or ""),
            resume_session_id=None if payload.get("resume_session_id") is None else str(payload.get("resume_session_id") or ""),
            turn_ids=[str(item or "") for item in _as_list(payload.get("turn_ids"))],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "conversation_id": _encode(self.conversation_id),
            "workspace_id": _encode(self.workspace_id),
            "title": _encode(self.title),
            "custom_title": _encode(self.custom_title),
            "title_revision": _encode(self.title_revision),
            "created_at": _encode(self.created_at),
            "updated_at": _encode(self.updated_at),
            "active_turn_id": _encode(self.active_turn_id),
            "resume_session_id": _encode(self.resume_session_id),
            "turn_ids": _encode(self.turn_ids),
        }


@dataclass
class TurnRecord:
    turn_id: str = ""
    conversation_id: str = ""
    run_id: str = ""
    turn_index: int = 0
    prompt: str = ""
    phase: TurnPhase = TurnPhase.UNKNOWN
    settings: RunSettingsRecord = field(default_factory=RunSettingsRecord)
    created_at: str = ""
    updated_at: str = ""
    continued_from_turn_id: str | None = None
    continued_from_candidate_id: str | None = None
    recommended_candidate_id: str | None = None
    finalized_candidate_id: str | None = None
    candidate_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: object) -> "TurnRecord":
        payload = _as_dict(value)
        return cls(
            turn_id=str(payload.get("turn_id") or ""),
            conversation_id=str(payload.get("conversation_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            turn_index=_as_int(payload.get("turn_index")),
            prompt=str(payload.get("prompt") or ""),
            phase=TurnPhase.from_value(payload.get("phase")),
            settings=RunSettingsRecord.from_mapping(payload.get("settings")),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            continued_from_turn_id=None if payload.get("continued_from_turn_id") is None else str(payload.get("continued_from_turn_id") or ""),
            continued_from_candidate_id=None if payload.get("continued_from_candidate_id") is None else str(payload.get("continued_from_candidate_id") or ""),
            recommended_candidate_id=None if payload.get("recommended_candidate_id") is None else str(payload.get("recommended_candidate_id") or ""),
            finalized_candidate_id=None if payload.get("finalized_candidate_id") is None else str(payload.get("finalized_candidate_id") or ""),
            candidate_ids=[str(item or "") for item in _as_list(payload.get("candidate_ids"))],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "turn_id": _encode(self.turn_id),
            "conversation_id": _encode(self.conversation_id),
            "run_id": _encode(self.run_id),
            "turn_index": _encode(self.turn_index),
            "prompt": _encode(self.prompt),
            "phase": _encode(self.phase),
            "settings": _encode(self.settings),
            "created_at": _encode(self.created_at),
            "updated_at": _encode(self.updated_at),
            "continued_from_turn_id": _encode(self.continued_from_turn_id),
            "continued_from_candidate_id": _encode(self.continued_from_candidate_id),
            "recommended_candidate_id": _encode(self.recommended_candidate_id),
            "finalized_candidate_id": _encode(self.finalized_candidate_id),
            "candidate_ids": _encode(self.candidate_ids),
        }


@dataclass
class AgentCandidateRecord:
    candidate_id: str = ""
    turn_id: str = ""
    agent_index: int = 0
    display_name: str = ""
    role: CandidateRole = CandidateRole.CANDIDATE
    status: CandidateStatus = CandidateStatus.UNKNOWN
    seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    activity: AgentActivityRecord | None = None
    reasoning_tokens: int | None = None
    reasoning_token_counts: dict[str, int] = field(default_factory=dict)
    codex_thread_id: str | None = None
    error: str | None = None
    rejected: bool = False
    stale: bool = False
    recommended: bool = False
    finalized: bool = False
    workspace_dir: str | None = None
    workspace_available: bool = False
    can_accept: bool = False
    can_continue: bool = False
    can_retry: bool = False

    @classmethod
    def from_mapping(cls, value: object) -> "AgentCandidateRecord":
        payload = _as_dict(value)
        return cls(
            candidate_id=str(payload.get("candidate_id") or ""),
            turn_id=str(payload.get("turn_id") or ""),
            agent_index=_as_int(payload.get("agent_index")),
            display_name=str(payload.get("display_name") or ""),
            role=CandidateRole.from_value(payload.get("role")),
            status=CandidateStatus.from_value(payload.get("status")),
            seconds=None if payload.get("seconds") is None else _as_float(payload.get("seconds")),
            started_at=None if payload.get("started_at") is None else str(payload.get("started_at") or ""),
            finished_at=None if payload.get("finished_at") is None else str(payload.get("finished_at") or ""),
            activity=None if payload.get("activity") is None else AgentActivityRecord.from_mapping(payload.get("activity")),
            reasoning_tokens=None if payload.get("reasoning_tokens") is None else _as_int(payload.get("reasoning_tokens")),
            reasoning_token_counts={str(key): _as_int(item) for key, item in _as_dict(payload.get("reasoning_token_counts")).items()},
            codex_thread_id=None if payload.get("codex_thread_id") is None else str(payload.get("codex_thread_id") or ""),
            error=None if payload.get("error") is None else str(payload.get("error") or ""),
            rejected=bool(payload.get("rejected")),
            stale=bool(payload.get("stale")),
            recommended=bool(payload.get("recommended")),
            finalized=bool(payload.get("finalized")),
            workspace_dir=None if payload.get("workspace_dir") is None else str(payload.get("workspace_dir") or ""),
            workspace_available=bool(payload.get("workspace_available")),
            can_accept=bool(payload.get("can_accept")),
            can_continue=bool(payload.get("can_continue")),
            can_retry=bool(payload.get("can_retry")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "candidate_id": _encode(self.candidate_id),
            "turn_id": _encode(self.turn_id),
            "agent_index": _encode(self.agent_index),
            "display_name": _encode(self.display_name),
            "role": _encode(self.role),
            "status": _encode(self.status),
            "seconds": _encode(self.seconds),
            "started_at": _encode(self.started_at),
            "finished_at": _encode(self.finished_at),
            "activity": _encode(self.activity),
            "reasoning_tokens": _encode(self.reasoning_tokens),
            "reasoning_token_counts": _encode(self.reasoning_token_counts),
            "codex_thread_id": _encode(self.codex_thread_id),
            "error": _encode(self.error),
            "rejected": _encode(self.rejected),
            "stale": _encode(self.stale),
            "recommended": _encode(self.recommended),
            "finalized": _encode(self.finalized),
            "workspace_dir": _encode(self.workspace_dir),
            "workspace_available": _encode(self.workspace_available),
            "can_accept": _encode(self.can_accept),
            "can_continue": _encode(self.can_continue),
            "can_retry": _encode(self.can_retry),
        }


@dataclass
class ConversationSnapshot:
    schema_version: int = 0
    machine: MachineRecord = field(default_factory=MachineRecord)
    workspace: WorkspaceRecord = field(default_factory=WorkspaceRecord)
    conversation: ConversationRecord = field(default_factory=ConversationRecord)
    turn: TurnRecord = field(default_factory=TurnRecord)
    candidates: list[AgentCandidateRecord] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: object) -> "ConversationSnapshot":
        payload = _as_dict(value)
        return cls(
            schema_version=_as_int(payload.get("schema_version")),
            machine=MachineRecord.from_mapping(payload.get("machine")),
            workspace=WorkspaceRecord.from_mapping(payload.get("workspace")),
            conversation=ConversationRecord.from_mapping(payload.get("conversation")),
            turn=TurnRecord.from_mapping(payload.get("turn")),
            candidates=[AgentCandidateRecord.from_mapping(item) for item in _as_list(payload.get("candidates"))],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": _encode(self.schema_version),
            "machine": _encode(self.machine),
            "workspace": _encode(self.workspace),
            "conversation": _encode(self.conversation),
            "turn": _encode(self.turn),
            "candidates": _encode(self.candidates),
        }
