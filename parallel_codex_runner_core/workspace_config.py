from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from platformdirs import user_state_path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


_PROCESS_LOCK = threading.Lock()
_CONFIG_VERSION = 1


def default_workspace_config_path() -> Path:
    override = os.environ.get("PCR_WORKSPACE_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return user_state_path("parallel-codex-runner", appauthor=False) / "workspace_config.json"


@dataclass(frozen=True)
class WorkspaceSettings:
    """The TUI settings remembered for one workspace."""

    agents: int | None = None
    synthesis_agents: int | None = None
    execution: str | None = None
    max_parallel: int | None = None
    subagents: bool | None = None
    subagents_limit: int | None = None
    recommend_by: str | None = None
    model: str | None = None
    effort: str | None = None
    sync_back: bool | None = None
    keep_workspaces: bool | None = None
    resume_session_id: str | None = None
    present_fields: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_mapping(cls, value: object) -> "WorkspaceSettings | None":
        if not isinstance(value, dict):
            return None

        def positive_int(raw: object, *, allow_zero: bool = False) -> int | None:
            if isinstance(raw, bool):
                return None
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                return None
            if parsed > 0 or (allow_zero and parsed == 0):
                return parsed
            return None

        def optional_text(raw: object) -> str | None:
            text = str(raw or "").strip()
            return text or None

        execution = optional_text(value.get("EXECUTION"))
        if execution is not None:
            execution = execution.lower()
            if execution not in {"parallel", "serial"}:
                execution = None

        max_parallel_raw = value.get("MAX_PARALLEL")
        max_parallel: int | None
        if max_parallel_raw is None or str(max_parallel_raw).strip().lower() in {
            "",
            "auto",
            "default",
            "none",
        }:
            max_parallel = None
        else:
            max_parallel = positive_int(max_parallel_raw)

        recommend_by = optional_text(value.get("RECOMMEND_BY"))
        if recommend_by is not None:
            recommend_by = recommend_by.lower()
            if recommend_by not in {"duration", "reasoning_tokens"}:
                recommend_by = None

        def optional_bool(raw: object) -> bool | None:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                normalized = raw.strip().lower()
                if normalized in {"yes", "y", "true", "on", "1"}:
                    return True
                if normalized in {"no", "n", "false", "off", "0"}:
                    return False
            return None

        effort = optional_text(value.get("EFFORT"))
        if effort is not None and effort.lower() in {"auto", "default", "none", "clear"}:
            effort = None

        resume = optional_text(value.get("RESUME"))
        if resume is not None and resume.upper() == "NO":
            resume = None

        present_fields = frozenset(
            field_name
            for json_name, field_name in {
                "AGENTS": "agents",
                "SYNTHESIS_AGENTS": "synthesis_agents",
                "EXECUTION": "execution",
                "MAX_PARALLEL": "max_parallel",
                "SUBAGENTS": "subagents",
                "SUBAGENTS_LIMIT": "subagents_limit",
                "RECOMMEND_BY": "recommend_by",
                "MODEL": "model",
                "EFFORT": "effort",
                "SYNC_BACK": "sync_back",
                "KEEP_WORKSPACES": "keep_workspaces",
                "RESUME": "resume_session_id",
            }.items()
            if json_name in value
        )
        settings = cls(
            agents=positive_int(value.get("AGENTS")),
            synthesis_agents=positive_int(value.get("SYNTHESIS_AGENTS"), allow_zero=True),
            execution=execution,
            max_parallel=max_parallel,
            subagents=optional_bool(value.get("SUBAGENTS")),
            subagents_limit=positive_int(value.get("SUBAGENTS_LIMIT")),
            recommend_by=recommend_by,
            model=(
                None
                if optional_text(value.get("MODEL")) in {None, "default", "none"}
                else optional_text(value.get("MODEL"))
            ),
            effort=effort.lower() if effort else None,
            sync_back=optional_bool(value.get("SYNC_BACK")),
            keep_workspaces=optional_bool(value.get("KEEP_WORKSPACES")),
            resume_session_id=resume,
            present_fields=present_fields,
        )
        return settings if present_fields else None

    @classmethod
    def from_runtime(
        cls,
        agents: int,
        synthesis_agents: int,
        args: Any,
        resume_session_id: str,
    ) -> "WorkspaceSettings":
        serial = bool(getattr(args, "serial", False))
        raw_max_parallel = getattr(args, "max_parallel", None)
        try:
            raw_max_parallel_int = int(raw_max_parallel) if raw_max_parallel is not None else None
        except (TypeError, ValueError):
            raw_max_parallel_int = None
        serial = serial or raw_max_parallel_int == 1
        max_parallel = 1 if serial else raw_max_parallel
        if max_parallel is not None:
            try:
                max_parallel = int(max_parallel)
            except (TypeError, ValueError):
                max_parallel = None
        model = str(getattr(args, "model", None) or "").strip() or None
        effort = str(getattr(args, "effort", None) or "").strip().lower() or None
        return cls(
            agents=int(agents),
            synthesis_agents=int(synthesis_agents),
            execution="serial" if serial else "parallel",
            max_parallel=max_parallel,
            subagents=bool(getattr(args, "subagents", False)),
            subagents_limit=int(getattr(args, "subagents_limit", 8)),
            recommend_by=str(getattr(args, "recommend_by", "reasoning_tokens")),
            model=model,
            effort=effort,
            sync_back=not bool(getattr(args, "no_sync_back", False)),
            keep_workspaces=bool(getattr(args, "keep_workspaces", False)),
            resume_session_id=str(resume_session_id or "").strip() or None,
            present_fields=frozenset(
                {
                    "agents",
                    "synthesis_agents",
                    "execution",
                    "max_parallel",
                    "subagents",
                    "subagents_limit",
                    "recommend_by",
                    "model",
                    "effort",
                    "sync_back",
                    "keep_workspaces",
                    "resume_session_id",
                }
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "AGENTS": self.agents,
            "SYNTHESIS_AGENTS": self.synthesis_agents,
            "EXECUTION": self.execution,
            "MAX_PARALLEL": self.max_parallel if self.max_parallel is not None else "auto",
            "SUBAGENTS": self.subagents,
            "SUBAGENTS_LIMIT": self.subagents_limit,
            "RECOMMEND_BY": self.recommend_by,
            "MODEL": self.model or "",
            "EFFORT": self.effort or "auto",
            "SYNC_BACK": self.sync_back,
            "KEEP_WORKSPACES": self.keep_workspaces,
            "RESUME": self.resume_session_id or "NO",
        }

    def apply_to_args(self, args: Any, explicit_settings: set[str] | frozenset[str] = frozenset()) -> None:
        def present(name: str) -> bool:
            return name in self.present_fields

        if present("agents") and self.agents is not None and "agents" not in explicit_settings:
            args.num_agents = self.agents
        if present("synthesis_agents") and self.synthesis_agents is not None and "synthesis_agents" not in explicit_settings:
            args.synthesis_agents = self.synthesis_agents
        if (
            present("execution")
            and self.execution is not None
            and "execution" not in explicit_settings
            and "max_parallel" not in explicit_settings
        ):
            args.serial = self.execution == "serial"
        if (
            present("max_parallel")
            and "max_parallel" not in explicit_settings
            and "execution" not in explicit_settings
            and self.execution is not None
        ):
            args.max_parallel = self.max_parallel
            if self.execution == "serial" and args.max_parallel is None:
                args.max_parallel = 1
        if present("subagents") and self.subagents is not None and "subagents" not in explicit_settings:
            args.subagents = self.subagents
        if present("subagents_limit") and self.subagents_limit is not None and "subagents_limit" not in explicit_settings:
            args.subagents_limit = self.subagents_limit
        if present("recommend_by") and self.recommend_by is not None and "recommend_by" not in explicit_settings:
            args.recommend_by = self.recommend_by
        if present("model") and "model" not in explicit_settings:
            args.model = self.model or None
        if present("effort") and "effort" not in explicit_settings:
            args.effort = self.effort or None
        if present("sync_back") and self.sync_back is not None and "sync_back" not in explicit_settings:
            args.no_sync_back = not self.sync_back
        if present("keep_workspaces") and self.keep_workspaces is not None and "keep_workspaces" not in explicit_settings:
            args.keep_workspaces = self.keep_workspaces
        if present("resume_session_id") and "resume" not in explicit_settings:
            args.resume_session_id = self.resume_session_id


class WorkspaceConfigStore:
    """Atomically persist settings keyed by canonical workspace path."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_workspace_config_path()).expanduser().resolve()
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    @staticmethod
    def context_key(workspace: Path | str) -> str:
        return str(Path(workspace).expanduser().resolve())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            with self.lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": _CONFIG_VERSION, "workspaces": {}}
        if not isinstance(payload, dict):
            return {"version": _CONFIG_VERSION, "workspaces": {}}
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        return {"version": _CONFIG_VERSION, "workspaces": workspaces}

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, workspace: Path | str) -> WorkspaceSettings | None:
        try:
            with self._locked():
                payload = self._read_unlocked()
                value = payload["workspaces"].get(self.context_key(workspace))
                return WorkspaceSettings.from_mapping(value)
        except OSError:
            return None

    def save(self, workspace: Path | str, settings: WorkspaceSettings) -> bool:
        try:
            with self._locked():
                payload = self._read_unlocked()
                workspaces = payload["workspaces"]
                workspaces[self.context_key(workspace)] = settings.to_mapping()
                self._write_unlocked(payload)
            return True
        except OSError:
            return False
