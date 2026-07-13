from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is process-local
    fcntl = None


STATE_VERSION = 2


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def int_key_dict(value: Any) -> Dict[int, Any]:
    if not isinstance(value, dict):
        return {}
    result: Dict[int, Any] = {}
    for raw_key, item in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            continue
        result[key] = item
    return result


def positive_ints(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    for item in value:
        number = safe_int(item)
        if number > 0 and number not in result:
            result.append(number)
    return result


@dataclass
class ManagedRun:
    run_id: str
    prompt: str
    workspace: str
    config: Dict[str, Any]
    status: str = "preparing"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    run_root: str = ""
    error: str = ""
    event_count: int = 0
    agent_statuses: Dict[int, str] = field(default_factory=dict)
    agent_tokens: Dict[int, int | None] = field(default_factory=dict)
    results: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    rejected_agents: list[int] = field(default_factory=list)
    recommended_agent: int | None = None
    finalized_agent: int | None = None
    synced_back: bool = False
    workspaces_deleted: bool = False
    codex_homes_deleted: bool = False
    session_promotion: Dict[str, Any] | None = None
    artifact_token: str = ""
    worker_pid: int | None = None
    worker_operation: str = ""
    worker_started_at: str = ""
    worker_deadline: str = ""
    estimated_bytes: int = 0
    expires_at: str = ""

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ManagedRun":
        raw_config = value.get("config")
        return cls(
            run_id=str(value.get("run_id") or ""),
            prompt=str(value.get("prompt") or ""),
            workspace=str(value.get("workspace") or ""),
            config=dict(raw_config) if isinstance(raw_config, dict) else {},
            status=str(value.get("status") or "failed"),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            run_root=str(value.get("run_root") or ""),
            error=str(value.get("error") or ""),
            event_count=max(0, safe_int(value.get("event_count"))),
            agent_statuses={
                key: str(item)
                for key, item in int_key_dict(value.get("agent_statuses")).items()
            },
            agent_tokens={
                key: int(item)
                if isinstance(item, int) and not isinstance(item, bool)
                else None
                for key, item in int_key_dict(value.get("agent_tokens")).items()
            },
            results={
                key: dict(item)
                for key, item in int_key_dict(value.get("results")).items()
                if isinstance(item, dict)
            },
            rejected_agents=positive_ints(value.get("rejected_agents") or []),
            recommended_agent=(
                safe_int(value.get("recommended_agent"))
                if isinstance(value.get("recommended_agent"), int)
                and not isinstance(value.get("recommended_agent"), bool)
                else None
            ),
            finalized_agent=(
                safe_int(value.get("finalized_agent"))
                if isinstance(value.get("finalized_agent"), int)
                and not isinstance(value.get("finalized_agent"), bool)
                else None
            ),
            synced_back=bool(value.get("synced_back")),
            workspaces_deleted=bool(value.get("workspaces_deleted")),
            codex_homes_deleted=bool(value.get("codex_homes_deleted")),
            session_promotion=(
                dict(value["session_promotion"])
                if isinstance(value.get("session_promotion"), dict)
                else None
            ),
            artifact_token=str(value.get("artifact_token") or ""),
            worker_pid=(
                safe_int(value.get("worker_pid"))
                if safe_int(value.get("worker_pid")) > 0
                else None
            ),
            worker_operation=str(value.get("worker_operation") or ""),
            worker_started_at=str(value.get("worker_started_at") or ""),
            worker_deadline=str(value.get("worker_deadline") or ""),
            estimated_bytes=max(0, safe_int(value.get("estimated_bytes"))),
            expires_at=str(value.get("expires_at") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JsonStateStore:
    """Atomic plugin state storage with a short cross-process write lock."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "runs.json"
        self.lock_path = state_dir / "state.lock"
        self._thread_lock = threading.RLock()

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            handle = self.lock_path.open("a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()

    def load_payload(self) -> Any:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise
        return value

    def write_runs(self, runs: Dict[str, ManagedRun]) -> None:
        payload = {
            "version": STATE_VERSION,
            "runs": [
                run.to_dict()
                for run in sorted(runs.values(), key=lambda item: item.created_at)
            ],
        }
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
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
