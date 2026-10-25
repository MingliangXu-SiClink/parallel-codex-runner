#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parallel_codex_runner_core.app

A deliberately simple parallel Codex runner.

Command examples:
    # After installing this package, run on the current workspace.
    pcr "fix the failing tests" -n 20

    # Run 20 agents on the current directory, select the successful run with
    # the highest observed reasoning tokens by default, and sync it back.
    pcr "fix the failing tests" -n 20

    # Resume a Codex conversation from this workspace, then send the new prompt
    # to each parallel candidate.
    pcr --resume "continue the previous task"

    # Select the longest successful run instead of selecting by reasoning tokens.
    pcr "fix the failing tests" -n 20 --recommend-by duration

    # Run against another workspace with a long prompt file.
    pcr --prompt-file /tmp/prompt.txt -n 20 --workspace /path/to/project

    # Pipe the prompt through stdin.
    printf '%s\n' "refactor the API client and update tests" | pcr -n 8 --workspace /path/to/project

    # Limit concurrency while still creating 20 isolated candidates.
    pcr "implement the requested change" -n 20 --max-parallel 5 --workspace /path/to/project

    # Run candidates serially. This is useful when the machine or API quota is tight.
    pcr "make the migration idempotent" -n 6 --serial --workspace /path/to/project

    # Choose a model when the installed Codex CLI supports --model.
    pcr "improve error handling" -n 10 --model gpt-5 --workspace /path/to/project

    # Keep candidate workspaces for inspection and do not sync anything back.
    pcr "investigate this bug" -n 5 --keep-workspaces --no-sync-back --workspace /path/to/project

    # Store run metadata outside the default location. The runs dir must not be
    # inside the target workspace.
    pcr "update docs" -n 4 --workspace /path/to/project --runs-dir /tmp/codex-runs

    # Use a non-default Codex executable.
    pcr "run the requested cleanup" -n 3 --codex-bin /opt/codex/bin/codex

Contract:
1. Read one prompt from argv / --prompt-file / stdin.
2. Make N full, isolated copies of the target workspace.
3. Run one `codex exec -` or `codex exec resume <session_id> -` inside each copied workspace, concurrently by default.
4. Wait for every Codex process to finish.
5. Pick the successful run by the requested strategy: longest duration or max reasoning tokens.
6. Sync that selected workspace back to the original workspace.
7. Keep metadata/logs under the runner directory.

Important layout rule:
    .codex_parallel_runs is NEVER placed under the target workspace.

Optional pretty output:
    pip install rich tqdm loguru
"""

from __future__ import annotations

import argparse
import hashlib
import asyncio
import concurrent.futures
import datetime as _dt
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .codex_cli import (
    build_codex_command,
    codex_process_environment,
    format_fast_mode,
    merge_codex_developer_instructions,
    read_effective_codex_developer_instructions,
    read_codex_exec_help,
    read_codex_exec_resume_help,
    resolve_codex_executable,
)
from .codex_model_cache import CodexModelCacheManager
from .work_operations import OperationOutputStore
from .limits import MAX_AGENT_COUNT, validate_agent_count
from .codex_models import CodexModelRegistry
from .models import (
    AGENT_ROLE_CANDIDATE,
    AGENT_ROLE_SYNTHESIS,
    DEFAULT_FOLLOW_UP_DELAY_SECONDS,
    DEFAULT_NUM_AGENTS,
    DEFAULT_SUBAGENTS_LIMIT,
    DEFAULT_SYNTHESIS_AGENTS,
    AgentResult,
    AgentState,
    CodexHistoryEntry,
    CodexSessionPromotion,
    ResumeSession,
    normalize_agent_role,
)
from .paths import (
    absolute_path_for_display,
    choose_run_base,
    create_unique_run_root,
    is_relative_to,
    safe_tail,
)
from .resume_history import ResumeHistory, resolve_resume_history
from .prepared_history import RolloutTemplate, clone_file
from .scoped_history import copy_scoped_database, scoped_database_bytes
from .synthesis import (
    failed_synthesis_preparation_fingerprint,
    create_synthesis_context,
    effective_synthesis_codex_settings,
    normalize_synthesis_effort,
    normalize_synthesis_fast,
    normalize_synthesis_model,
    preferred_recommendation_pool,
)
from .visible_paths import (
    load_session_workspace_mappings,
    record_session_workspace_mapping,
    rewrite_workspace_references,
)
from functools import wraps

from .workspace import (
    WorkspaceSyncPreflightError,
    agent_execution_workspace,
    cleanup_workspace_copy,
    cleanup_workspace_copies,
    copy_workspace,
    estimate_path_storage_bytes,
    estimate_workspace_copy_bytes,
    execution_workspace_mode,
    source_workspace_lease,
    sync_best_workspace_back,
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _source_execution_lease(function):
    @wraps(function)
    def guarded(args, *positional, **keywords):
        if getattr(args, "workspace_mode", "isolated") == "source":
            workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd()
            with source_workspace_lease(workspace):
                return function(args, *positional, **keywords)
        return function(args, *positional, **keywords)
    return guarded


GIBIBYTE = 1024**3
LARGE_RUN_STORAGE_WARNING_BYTES = 5 * GIBIBYTE
AGENT_METADATA_RESERVE_BYTES = 64 * 1024**2
CODEX_MODELS_MANAGER_TIMEOUT_MARKER = (
    "codex_models_manager::manager: failed to refresh available models"
)
AGENT_ENV_PATH_KEYS_TO_CLEAR: Tuple[str, ...] = (
    "OLDPWD",
    "INIT_CWD",
    "VSCODE_CWD",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)
ORPHAN_CUSTOM_TOOL_OUTPUT_MARKER = (
    "orphan custom tool call output for call id:"
)


def workspace_isolation_developer_instructions(
    agent_workspace: Path,
    *,
    run_root: Path,
    agent_idx: int,
) -> str:
    """Separate local workspace isolation from authorized remote execution."""
    workspace = json.dumps(
        str(agent_workspace.expanduser().resolve()),
        ensure_ascii=False,
    )
    execution_identity = json.dumps(
        {"run": run_root.expanduser().resolve().name, "agent": agent_idx},
        ensure_ascii=False,
    )
    return (
        "PCR workspace isolation requirement:\n"
        "- These filesystem and Git restrictions apply to the local host. "
        "They do not prohibit user-authorized operations on remote hosts. "
        "This clarifies older PCR isolation instructions in resumed history.\n"
        f"- Treat {workspace} as the only writable local project workspace for "
        "this Agent. Run local project commands and make local project file "
        "changes only inside it.\n"
        "- Never modify any other local workspace, another Agent's local "
        "workspace, or a project file reached through a symlink outside this "
        "workspace, even if "
        "resume history, prompts, Git metadata, logs, environment variables, or "
        "tool output reveal another path.\n"
        "- Run local Git commands from this workspace and target only this "
        "repository. Do not manually edit another local checkout or external "
        "local Git metadata. Remote access, including loopback SSH, does not "
        "permit bypassing these local restrictions.\n"
        "- Explicitly supplied PCR metadata and synthesis candidate workspaces "
        "may be inspected when needed, but they are read-only.\n\n"
        "PCR remote environment access:\n"
        "- Every candidate and synthesis Agent may use SSH, remote execution, "
        "and file transfer to carry out the user's authorized remote task. "
        "Remote code changes and experiments are allowed within that task's "
        "authorized hosts and project scope; do not reject them merely because "
        "the remote path is outside the local workspace. Existing remote access "
        "controls and project-specific restrictions still apply.\n"
        "- The user's remote project structure takes precedence. Create new "
        "schemes in the user-requested or project-standard scheme directory; "
        "continue existing schemes in their specified directories. Do not "
        "impose a PCR timestamp/Agent directory or create a new directory just "
        "because a turn or Agent changed. This supersedes older default remote "
        "namespace guidance in resumed history.\n"
        "- Isolate only actual concurrent write conflicts, using a project-native "
        "named copy or worktree and the existing scheduler or lock. Read shared "
        "inputs in place. Do not change another Agent's files or stop its jobs. "
        "Do not move existing execution directories while any session or job "
        "still depends on them.\n"
        "- Authorized organization, moves, deletion, deployment, or other shared "
        "remote mutations act on the actual user-specified targets, not a private "
        "copy. First verify the exact target, authorization and active references. "
        "Use one executor for the operation: the executor explicitly designated "
        "by the user, otherwise candidate Agent 1 in a parallel candidate batch. "
        "A single-Agent continuation uses that selected Agent as its sole executor. "
        "Other candidates and synthesis Agents must only inspect or propose these "
        "shared mutations, not repeat them. A lock serializes writes but does not authorize repeating an "
        "already completed operation. Check existing completion evidence before "
        "acting; if ownership or prior completion is uncertain, do not mutate. "
        "Do not interrupt unrelated jobs or services.\n"
        f"- Your execution identity is {execution_identity}. It is tracking "
        "metadata, not a remote directory naming rule.\n"
        "- PCR only syncs back and cleans up local candidate workspaces. Remote "
        "effects are not automatically adopted, rolled back, or removed when "
        "a candidate is selected or discarded. Record remote host, workspace, "
        "job identifiers, and result paths in your local workspace and final "
        "response so they remain traceable."
    )


def custom_tool_lifecycle_developer_instructions() -> str:
    """Keep long-running custom tools compatible with Codex compaction."""
    return (
        "PCR custom tool lifecycle requirement:\n"
        "- Do not keep one code-mode or custom-tool call alive while repeatedly "
        "emitting notify() or yield_control() outputs across multiple turns.\n"
        "- For monitoring, event listening, or polling, use bounded calls that "
        "return once, then start a new call for the next poll. This keeps every "
        "tool output paired with a live call when Codex compacts context."
    )


class _AgentDeveloperInstructionResolver:
    """Build PCR guidance without sharing config across private workspaces."""

    def for_workspace(
        self,
        *,
        codex_bin: str,
        codex_home: Path,
        agent_workspace: Path,
        run_root: Path,
        agent_idx: int,
        additional_instructions: Optional[str] = None,
        defer_resolution: bool = False,
    ) -> str:
        existing_instructions = None
        if not defer_resolution:
            existing_instructions = read_effective_codex_developer_instructions(
                codex_bin, codex_home, agent_workspace,
            )

        instructions = merge_codex_developer_instructions(
            existing_instructions,
            additional_instructions,
        )
        instructions = merge_codex_developer_instructions(
            instructions,
            custom_tool_lifecycle_developer_instructions(),
        )
        instructions = merge_codex_developer_instructions(
            instructions,
            workspace_isolation_developer_instructions(
                agent_workspace, run_root=run_root, agent_idx=agent_idx,
            ),
        )
        assert instructions is not None
        if defer_resolution:
            # Native resume retains older PCR fragments as conversation history.
            instructions = (
                "Current PCR execution guidance for this turn:\n"
                "These current PCR stage and workspace requirements supersede "
                "older PCR stage and workspace requirements in resumed history. "
                "They do not replace the user's task, project rules, Codex "
                "settings, or non-PCR guidance.\n\n" + instructions
            )
        return instructions


@dataclass(frozen=True)
class RunStorageEstimate:
    num_agents: int
    workspace_bytes_per_agent: int
    workspace_copies_bytes: int
    metadata_bytes_per_agent: int
    metadata_bytes: int
    total_bytes: int
    shared_source_bytes: int = 0


@dataclass(frozen=True)
class LiveCandidateRequest:
    idx: int
    retry: bool


@dataclass(frozen=True)
class SynthesisStageConfiguration:
    agents: int
    model: Optional[str]
    effort: Optional[str]
    fast: Optional[bool]


class LiveSynthesisConfigCoordinator:
    """Thread-safe synthesis settings that remain editable before stage two."""

    def __init__(
        self,
        agents: int,
        model: object = None,
        effort: object = None,
        fast: object = None,
    ) -> None:
        self._lock = threading.Lock()
        self._active = True
        self._configuration = self._normalize(agents, model, effort, fast)

    @staticmethod
    def _normalize(
        agents: int,
        model: object,
        effort: object,
        fast: object,
    ) -> SynthesisStageConfiguration:
        return SynthesisStageConfiguration(
            agents=max(0, int(agents or 0)),
            model=normalize_synthesis_model(model),
            effort=normalize_synthesis_effort(effort),
            fast=normalize_synthesis_fast(fast),
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "LiveSynthesisConfigCoordinator":
        return cls(
            getattr(args, "synthesis_agents", 0),
            getattr(args, "synthesis_model", None),
            getattr(args, "synthesis_effort", None),
            getattr(args, "synthesis_fast", None),
        )

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def update(
        self,
        agents: int,
        model: object,
        effort: object,
        fast: object,
    ) -> bool:
        configuration = self._normalize(agents, model, effort, fast)
        with self._lock:
            if not self._active:
                return False
            self._configuration = configuration
            return True

    def close_and_snapshot(self) -> SynthesisStageConfiguration:
        with self._lock:
            self._active = False
            return self._configuration

    def deactivate(self) -> None:
        with self._lock:
            self._active = False


class LiveCandidateCoordinator:
    """Thread-safe handoff for candidate changes that precede synthesis."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._pending: List[LiveCandidateRequest] = []
        self._pending_set: Set[int] = set()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def activate(self) -> None:
        with self._lock:
            self._active = True

    def request_retry(self, idx: int) -> bool:
        with self._lock:
            if not self._active or idx in self._pending_set:
                return False
            self._pending.append(LiveCandidateRequest(idx=idx, retry=True))
            self._pending_set.add(idx)
            return True

    def request_additional(self, indices: Sequence[int]) -> bool:
        normalized = list(dict.fromkeys(int(idx) for idx in indices))
        if not normalized or any(idx <= 0 for idx in normalized):
            return False
        with self._lock:
            if not self._active or any(idx in self._pending_set for idx in normalized):
                return False
            for idx in normalized:
                self._pending.append(LiveCandidateRequest(idx=idx, retry=False))
                self._pending_set.add(idx)
            return True

    def drain(self) -> List[LiveCandidateRequest]:
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
            self._pending_set.clear()
            return pending

    def close_if_idle(self) -> Optional[List[LiveCandidateRequest]]:
        """Atomically close, unless a request arrived before the stage ended."""
        with self._lock:
            if self._pending:
                pending = list(self._pending)
                self._pending.clear()
                self._pending_set.clear()
                return pending
            self._active = False
            return None

    def deactivate(self) -> None:
        with self._lock:
            self._active = False
            self._pending.clear()
            self._pending_set.clear()


# Conda's SQLite build can deadlock inside the macOS VFS when separate Python
# threads open or close databases concurrently. PCR's SQLite sections are
# short, so serialize them process-wide and keep file/JSON work outside.
_CODEX_SQLITE_LOCK = threading.RLock()
_RESUME_INDEX_LOCK = threading.RLock()

RESUME_INDEX_VERSION = 1
RESUME_INDEX_FILENAME = "resume-session-index-v1.json"
RESUME_JSONL_SCAN_SECONDS = 5.0
RESUME_JSONL_SCAN_FILES = 4_000
RESUME_JSONL_META_BYTES = 128 * 1024
RESUME_JSONL_PREVIEW_HEAD_BYTES = 512 * 1024
RESUME_JSONL_PREVIEW_TAIL_BYTES = 128 * 1024


@dataclass(frozen=True)
class ResumeStateQuery:
    sessions: List[ResumeSession]
    available: bool


class ResumeSessionList(list[ResumeSession]):
    """Resume results carrying discovery completeness without breaking callers."""

    def __init__(
        self,
        values: Iterable[ResumeSession] = (),
        *,
        complete: bool = True,
        source: str = "state",
    ) -> None:
        super().__init__(values)
        self.complete = bool(complete)
        self.source = source


def cancel_requested(cancel_event: Any = None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def cancel_agent_including_queued(agent_cancel_event: Any) -> None:
    """Request a stop that must not be cleared when a queued Agent starts."""
    setattr(agent_cancel_event, "_pcr_cancel_before_start", True)
    agent_cancel_event.set()


def requested_agent_stop_status(
    cancel_event: Any = None,
    agent_cancel_event: Any = None,
) -> Optional[str]:
    if cancel_requested(cancel_event):
        return "cancelled"
    if cancel_requested(agent_cancel_event):
        return "killed"
    return None


def install_cancel_signal_handlers(cancel_event: Any) -> Callable[[], None]:
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous: Dict[int, Any] = {}

    def mark_cancelled(_signum: int, _frame: Any) -> None:
        try:
            cancel_event.set()
        except Exception:
            pass

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        previous[signum] = signal.getsignal(signum)
        try:
            signal.signal(signum, mark_cancelled)
        except (OSError, ValueError):
            previous.pop(signum, None)

    def restore() -> None:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue

    return restore

# -----------------------------------------------------------------------------
# Optional UI dependencies
# -----------------------------------------------------------------------------

try:
    from loguru import logger as _loguru_logger  # type: ignore

    HAS_LOGURU = True
except Exception:  # pragma: no cover
    _loguru_logger = None
    HAS_LOGURU = False

try:
    from rich.console import Console  # type: ignore
    from rich.panel import Panel  # type: ignore
    from rich.progress import (  # type: ignore
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table  # type: ignore

    HAS_RICH = True
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    Panel = None  # type: ignore
    Progress = None  # type: ignore
    Table = None  # type: ignore
    HAS_RICH = False

try:
    from tqdm import tqdm  # type: ignore

    HAS_TQDM = True
except Exception:  # pragma: no cover
    tqdm = None  # type: ignore
    HAS_TQDM = False


class FallbackLogger:
    def __init__(self) -> None:
        import logging

        self._logger = logging.getLogger("parallel-codex")
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
            self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(msg.format(*args, **kwargs) if args or kwargs else msg)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(msg.format(*args, **kwargs) if args or kwargs else msg)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(msg.format(*args, **kwargs) if args or kwargs else msg)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(msg.format(*args, **kwargs) if args or kwargs else msg)

    def add(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def remove(self, *_args: Any, **_kwargs: Any) -> None:
        return None


logger = _loguru_logger if HAS_LOGURU else FallbackLogger()
console = Console() if HAS_RICH else None


# -----------------------------------------------------------------------------
# Prompt
# -----------------------------------------------------------------------------


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        p = Path(args.prompt_file).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"prompt file not found: {p}")
        text = p.read_text(encoding="utf-8")
    elif args.prompt:
        text = args.prompt
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit("需要提供 prompt：使用 --prompt-file /path/to/prompt.txt，或把 prompt 作为位置参数传入。")

    text = text.strip()
    if not text:
        raise SystemExit("prompt 为空。")
    return text


# -----------------------------------------------------------------------------
# Codex resume session discovery / selection
# -----------------------------------------------------------------------------


def get_codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser().resolve() if value else (Path.home() / ".codex").resolve()


def resolve_codex_reasoning_effort(
    model: Optional[str],
    effort: Optional[str],
    codex_home: Optional[Path] = None,
) -> Optional[str]:
    registry = CodexModelRegistry.load(codex_home or get_codex_home())
    try:
        return registry.resolve_effort(model, effort)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def is_codex_state_entry(name: str) -> bool:
    return (
        name == "sessions"
        or name == "archived_sessions"
        or name == "history.jsonl"
        or name.startswith("state_")
        or name.startswith("logs_")
        or name.startswith("goals_")
        or name.startswith("memories_")
        or name.startswith("thread_history_")
        or name.endswith("-wal")
        or name.endswith("-shm")
    )


CODEX_SUPPORT_FILES: Set[str] = {
    "auth.json",
    "config.json",
    "config.toml",
    "instructions.md",
    "mcp.json",
    "settings.json",
}
CODEX_SUPPORT_DIRS: Set[str] = {"profiles"}


def is_codex_support_entry(name: str) -> bool:
    return name in CODEX_SUPPORT_FILES or name in CODEX_SUPPORT_DIRS


def ignore_symlinked_children(directory: str, names: List[str]) -> List[str]:
    return [name for name in names if (Path(directory) / name).is_symlink()]


def copy_codex_support_entries(real_codex_home: Path, agent_codex_home: Path) -> None:
    if not real_codex_home.exists():
        return

    for entry in real_codex_home.iterdir():
        if is_codex_state_entry(entry.name) or not is_codex_support_entry(entry.name):
            continue
        target = agent_codex_home / entry.name
        if target.exists() or target.is_symlink():
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.copytree(entry, target, symlinks=False, ignore=ignore_symlinked_children)
            elif entry.is_file() or (entry.is_symlink() and entry.resolve().is_file()):
                shutil.copy2(entry, target, follow_symlinks=True)
        except FileNotFoundError:
            continue


def make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def remove_path_best_effort(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def scrub_codex_home_support_entries(agent_codex_home: Path) -> List[str]:
    """Remove copied auth/config support files while preserving resumable state."""
    if not agent_codex_home.exists():
        return []
    removed: List[str] = []
    try:
        entries = list(agent_codex_home.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if is_codex_state_entry(entry.name):
            continue
        if remove_path_best_effort(entry):
            removed.append(entry.name)
    return removed


def scrub_agent_codex_homes(meta_root: Path) -> None:
    if not meta_root.exists():
        return
    for path in meta_root.glob("agent_*/codex_home"):
        scrub_codex_home_support_entries(path)


def remove_agent_codex_homes(meta_root: Path) -> bool:
    """Delete isolated Codex homes when candidate workspaces are not retained."""
    if not meta_root.exists():
        return True
    if meta_root.is_symlink() or not meta_root.is_dir():
        raise RuntimeError(f"refusing to clean unsafe metadata path: {meta_root}")
    resolved_meta = meta_root.resolve()
    for path in meta_root.glob("agent_*/codex_home"):
        agent_dir = path.parent
        if (
            not agent_dir.name.removeprefix("agent_").isdigit()
            or agent_dir.is_symlink()
            or agent_dir.parent.resolve() != resolved_meta
            or path.is_symlink()
        ):
            raise RuntimeError(f"refusing to clean unsafe Codex home: {path}")
        if path.exists():
            shutil.rmtree(path)
    return not any(meta_root.glob("agent_*/codex_home"))


def copy_sqlite_database(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    with _CODEX_SQLITE_LOCK:
        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        try:
            src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(dst)
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()


def relative_path_or_import_path(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path("sessions") / "imported" / path.name


def copy_file_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dst.with_name(f".{dst.name}.pcr-tmp-{os.getpid()}-{time.time_ns()}")
    try:
        clone_file(src, tmp_path)
        shutil.copystat(src, tmp_path)
        os.replace(tmp_path, dst)
    finally:
        tmp_path.unlink(missing_ok=True)


def _replace_workspace_path_prefix(value: Any, old_workspace: str, new_workspace: str) -> Any:
    """Replace a recorded workspace path without touching unrelated text."""
    if not isinstance(value, str) or not old_workspace:
        return value
    old_workspace = old_workspace.rstrip("/")
    new_workspace = new_workspace.rstrip("/")
    if value == old_workspace:
        return new_workspace
    prefix = old_workspace + "/"
    if value.startswith(prefix):
        return new_workspace + value[len(old_workspace) :]
    return value


def _replace_known_workspace_paths(
    value: Any,
    old_workspaces: Iterable[str],
    new_workspace: str,
) -> Any:
    """Replace paths from any workspace recorded in an older turn."""
    if not isinstance(value, str):
        return value
    for old_workspace in sorted(
        {item.rstrip("/") for item in old_workspaces if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        replaced = _replace_workspace_path_prefix(value, old_workspace, new_workspace)
        if replaced != value:
            return replaced
    return value


def _replace_known_workspace_text(
    value: Any,
    old_workspaces: Iterable[str],
    new_workspace: str,
) -> Any:
    """Replace workspace paths embedded in Codex's serialized environment text."""
    if not isinstance(value, str):
        return value
    for old_workspace in sorted(
        {item.rstrip("/") for item in old_workspaces if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        value = value.replace(old_workspace, new_workspace.rstrip("/"))
    return value


def _replace_known_workspace_references(
    value: Any,
    old_workspaces: Iterable[str],
    new_workspace: str,
    old_codex_homes: Iterable[str] = (),
    new_codex_home: Optional[str] = None,
) -> Any:
    """Rebind workspace paths inside a serialized tool request or result."""
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                structured = json.loads(value)
                rebound = _replace_known_workspace_references(structured, old_workspaces, new_workspace, old_codex_homes, new_codex_home)
                if rebound != structured:
                    return json.dumps(rebound, ensure_ascii=False, separators=(",", ":"))
                return value
            except (ValueError, RecursionError):
                pass
        value = _replace_known_workspace_text(value, old_workspaces, new_workspace)
        if new_codex_home:
            value = _replace_known_workspace_text(
                value,
                old_codex_homes,
                new_codex_home,
            )
        return value
    if isinstance(value, list):
        return [
            _replace_known_workspace_references(
                item,
                old_workspaces,
                new_workspace,
                old_codex_homes,
                new_codex_home,
            )
            for item in value
        ]
    if isinstance(value, dict):
        rebound: Dict[Any, Any] = {}
        for key, item in value.items():
            new_key = (
                _replace_known_workspace_references(
                    key,
                    old_workspaces,
                    new_workspace,
                    old_codex_homes,
                    new_codex_home,
                )
                if isinstance(key, str)
                else key
            )
            rebound[new_key] = _replace_known_workspace_references(
                item,
                old_workspaces,
                new_workspace,
                old_codex_homes,
                new_codex_home,
            )
        return rebound
    return value


_PCR_AGENT_WORKSPACE_RE = re.compile(
    r"/(?:"
    r"(?:\.codex_parallel_runs/[^/\"'\r\n<>]+|\d{8}_\d{6})"
    r"/workspaces/agent_\d+)"
)

_PCR_AGENT_CODEX_HOME_RE = re.compile(
    r"/(?:"
    r"(?:\.codex_parallel_runs/[^/\"'\r\n<>]+|\d{8}_\d{6})"
    r"/meta/agent_\d+/codex_home)"
)


def _serialized_pcr_paths(value: Any, pattern: re.Pattern[str], marker: str) -> Set[str]:
    if isinstance(value, str):
        if marker not in value:
            return set()
        paths: Set[str] = set()
        cursor = 0
        # Match the distinctive suffix first. Retrying an arbitrary path
        # prefix at every slash is quadratic on large serialized tool output.
        for match in pattern.finditer(value):
            boundary = max(
                value.rfind(character, cursor, match.start())
                for character in "\"'\r\n<>"
            ) + 1
            doubled_slash = value.rfind("//", cursor, match.start() + 1)
            start = value.find("/", max(cursor, boundary, doubled_slash + 1), match.start() + 1)
            if start >= 0:
                paths.add(value[start:match.end()])
            cursor = match.end()
        return paths
    if isinstance(value, list):
        paths = set()
        for item in value:
            paths.update(_serialized_pcr_paths(item, pattern, marker))
        return paths
    if isinstance(value, dict):
        paths = set()
        for key, item in value.items():
            paths.update(_serialized_pcr_paths(key, pattern, marker))
            paths.update(_serialized_pcr_paths(item, pattern, marker))
        return paths
    return set()


def _serialized_pcr_workspace_paths(value: Any) -> Set[str]:
    """Find stale PCR workspace roots embedded in executable history."""
    return _serialized_pcr_paths(value, _PCR_AGENT_WORKSPACE_RE, "/workspaces/agent_")


def _serialized_pcr_codex_home_paths(value: Any) -> Set[str]:
    """Find stale per-Agent Codex homes embedded in executable history."""
    return _serialized_pcr_paths(value, _PCR_AGENT_CODEX_HOME_RE, "/meta/agent_")


def _rebind_file_system_policy_paths(
    policy: Any,
    old_workspaces: Iterable[str],
    new_workspace: str,
) -> bool:
    """Rebind both permission-profile and direct sandbox-policy entries."""
    if not isinstance(policy, dict):
        return False

    containers = [policy]
    file_system = policy.get("file_system")
    if isinstance(file_system, dict):
        containers.append(file_system)

    changed = False
    for container in containers:
        entries = container.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path_value = entry.get("path")
            if (
                not isinstance(path_value, dict)
                or path_value.get("type") != "path"
            ):
                continue
            old_path = path_value.get("path")
            new_path = _replace_known_workspace_paths(
                old_path,
                old_workspaces,
                new_workspace,
            )
            if new_path != old_path:
                path_value["path"] = new_path
                changed = True
    return changed


_GENERATED_CONTEXT_PREFIXES = (
    "<app-context>",
    "<environment_context>",
    "<permissions instructions>",
    "<plugins_instructions>",
    "<recommended_plugins>",
    "<skills_instructions>",
)


def _is_generated_context_part(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    text = value.get("text")
    if not isinstance(text, str):
        return False
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _GENERATED_CONTEXT_PREFIXES)


def _rebind_serialized_response_item_paths(
    payload: Dict[str, Any],
    target_workspace: str,
    old_workspaces: Set[str],
    target_codex_home: Optional[str],
    old_codex_homes: Set[str],
) -> bool:
    """Rebind executable paths in one serialized Codex response item."""
    item_type = payload.get("type")
    serialized_keys: Tuple[str, ...] = ()
    if item_type == "custom_tool_call":
        serialized_keys = ("input",)
    elif item_type == "function_call":
        serialized_keys = ("arguments",)
    elif item_type == "local_shell_call":
        serialized_keys = ("action",)

    changed = False
    for key in serialized_keys:
        old_value = payload.get(key)
        old_workspaces.update(_serialized_pcr_workspace_paths(old_value))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(old_value))
        new_value = _replace_known_workspace_references(
            old_value,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_value != old_value:
            payload[key] = new_value
            changed = True

    if item_type in {
        "custom_tool_call_output",
        "function_call_output",
        "local_shell_call_output",
    }:
        old_output = payload.get("output")
        old_workspaces.update(_serialized_pcr_workspace_paths(old_output))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(old_output))
        new_output = _replace_known_workspace_references(
            old_output,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_output != old_output:
            payload["output"] = new_output
            changed = True

    # Developer and system messages are generated execution context, not user
    # prose. Rebind only these roles so a user's historical path references
    # remain an accurate transcript.
    if item_type == "message" and payload.get("role") in {"developer", "system"}:
        content = payload.get("content")
        old_workspaces.update(_serialized_pcr_workspace_paths(content))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(content))
        new_content = _replace_known_workspace_references(
            content,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_content != content:
            payload["content"] = new_content
            changed = True

    # Codex serializes injected environment/skill context as extra user-role
    # parts. Rewrite those individual parts without touching the user's own
    # message in the same response item.
    if item_type == "message" and payload.get("role") == "user":
        content = payload.get("content")
        if isinstance(content, list):
            for index, part in enumerate(content):
                if not _is_generated_context_part(part):
                    continue
                old_workspaces.update(_serialized_pcr_workspace_paths(part))
                old_codex_homes.update(_serialized_pcr_codex_home_paths(part))
                new_part = _replace_known_workspace_references(
                    part,
                    old_workspaces,
                    target_workspace,
                    old_codex_homes,
                    target_codex_home,
                )
                if new_part != part:
                    content[index] = new_part
                    changed = True

    return changed


def _rebind_rollout_record_workspace(
    obj: Dict[str, Any],
    target_workspace: str,
    old_workspaces: Set[str],
    target_codex_home: Optional[str],
    old_codex_homes: Set[str],
) -> bool:
    """Rebind one persisted Codex execution-context record.

    A resumed rollout contains more than ``session_meta.cwd``. Codex also
    restores the most recent ``turn_context``, ``thread_settings`` and
    ``world_state`` records, and the model can imitate absolute paths from old
    serialized tool calls. If those records still point at a removed PCR
    workspace copy, a later ``exec_command`` can fail inside Codex with ENOENT even
    though PCR launched the process with a valid cwd.
    """
    record_type = obj.get("type")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return False

    changed = False
    if record_type == "turn_context":
        old_workspace = str(payload.get("cwd") or "").rstrip("/")
        if old_workspace:
            old_workspaces.add(old_workspace)
        if old_workspace and old_workspace != target_workspace:
            payload["cwd"] = target_workspace
            changed = True
        roots = payload.get("workspace_roots")
        if isinstance(roots, list):
            for index, root in enumerate(roots):
                new_root = _replace_known_workspace_paths(root, old_workspaces, target_workspace)
                if new_root != root:
                    roots[index] = new_root
                    changed = True
        for key in (
            "permission_profile",
            "active_permission_profile",
            "file_system_sandbox_policy",
        ):
            if _rebind_file_system_policy_paths(
                payload.get(key),
                old_workspaces,
                target_workspace,
            ):
                changed = True
        old_workspaces.update(_serialized_pcr_workspace_paths(payload))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(payload))
        new_payload = _replace_known_workspace_references(
            payload,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_payload != payload:
            obj["payload"] = new_payload
            changed = True

    elif record_type == "event_msg":
        settings = payload.get("thread_settings")
        if isinstance(settings, dict):
            old_workspace = str(settings.get("cwd") or "").rstrip("/")
            if old_workspace:
                old_workspaces.add(old_workspace)
            if old_workspace and old_workspace != target_workspace:
                settings["cwd"] = target_workspace
                changed = True
            for key in (
                "permission_profile",
                "active_permission_profile",
                "file_system_sandbox_policy",
            ):
                if _rebind_file_system_policy_paths(
                    settings.get(key),
                    old_workspaces,
                    target_workspace,
                ):
                    changed = True
            old_workspaces.update(_serialized_pcr_workspace_paths(settings))
            old_codex_homes.update(_serialized_pcr_codex_home_paths(settings))
            new_settings = _replace_known_workspace_references(
                settings,
                old_workspaces,
                target_workspace,
                old_codex_homes,
                target_codex_home,
            )
            if new_settings != settings:
                payload["thread_settings"] = new_settings
                changed = True

        # Patch events are generated tool telemetry. Their stdout and
        # path-keyed change maps are restored with the rollout, so leaving an
        # old Agent workspace here can teach a resumed model to reuse a
        # deleted working directory. Do not apply this to user-message events.
        event_type = str(payload.get("type") or "")
        if event_type.startswith("patch_apply_"):
            old_workspaces.update(_serialized_pcr_workspace_paths(payload))
            old_codex_homes.update(_serialized_pcr_codex_home_paths(payload))
            new_payload = _replace_known_workspace_references(
                payload,
                old_workspaces,
                target_workspace,
                old_codex_homes,
                target_codex_home,
            )
            if new_payload != payload:
                obj["payload"] = new_payload
                changed = True

    elif record_type == "world_state":
        state = payload.get("state")
        environments = state.get("environments") if isinstance(state, dict) else None
        environment_map = environments.get("environments") if isinstance(environments, dict) else None
        local = environment_map.get("local") if isinstance(environment_map, dict) else None
        if isinstance(local, dict):
            old_workspace = str(local.get("cwd") or "").rstrip("/")
            if old_workspace:
                old_workspaces.add(old_workspace)
            if old_workspace and old_workspace != target_workspace:
                local["cwd"] = target_workspace
                changed = True
        old_workspaces.update(_serialized_pcr_workspace_paths(state))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(state))
        new_state = _replace_known_workspace_references(
            state,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_state != state:
            payload["state"] = new_state
            changed = True

    elif record_type == "response_item":
        if _rebind_serialized_response_item_paths(
            payload,
            target_workspace,
            old_workspaces,
            target_codex_home,
            old_codex_homes,
        ):
            changed = True

    elif record_type == "compacted":
        summary = payload.get("message")
        old_workspaces.update(_serialized_pcr_workspace_paths(summary))
        old_codex_homes.update(_serialized_pcr_codex_home_paths(summary))
        new_summary = _replace_known_workspace_references(
            summary,
            old_workspaces,
            target_workspace,
            old_codex_homes,
            target_codex_home,
        )
        if new_summary != summary:
            payload["message"] = new_summary
            changed = True
        replacement_history = payload.get("replacement_history")
        if isinstance(replacement_history, list):
            for item in replacement_history:
                if not isinstance(item, dict):
                    continue
                if _rebind_serialized_response_item_paths(
                    item,
                    target_workspace,
                    old_workspaces,
                    target_codex_home,
                    old_codex_homes,
                ):
                    changed = True

    return changed


def prepare_agent_codex_home(
    real_codex_home: Path,
    agent_codex_home: Path,
    agent_workspace: Path,
    resume_session_id: Optional[str],
    prepared_source: Optional["AgentCodexSource"] = None,
) -> None:
    make_private_dir(agent_codex_home)
    copy_codex_support_entries(real_codex_home, agent_codex_home)
    if not resume_session_id:
        return
    if prepared_source is not None:
        prepared_source.populate(agent_codex_home, agent_workspace)
        return

    real_state_db = real_codex_home / "state_5.sqlite"
    agent_state_db = agent_codex_home / "state_5.sqlite"
    rollout_path = find_rollout_path_for_session(real_codex_home, resume_session_id)
    resume_history = resolve_resume_history(real_codex_home, rollout_path) if rollout_path else None
    copy_scoped_database(real_state_db, agent_state_db, "state", (resume_session_id,),
                         tuple(str(segment.path) for segment in resume_history.segments) if resume_history else ())

    if not resume_session_id or not agent_state_db.exists():
        return

    isolated_rollout: Optional[Path] = None
    if rollout_path is not None and rollout_path.exists():
        relative = relative_path_or_import_path(rollout_path, real_codex_home)
        isolated_rollout = agent_codex_home / relative.with_name(resume_history.segments[-1].destination_name(resume_session_id))
        isolated_rollout.parent.mkdir(parents=True, exist_ok=True)
        _rewrite_rollout_session(
            isolated_rollout,
            resume_session_id,
            agent_workspace,
            promote_source=False,
            codex_home=agent_codex_home,
            source_path=rollout_path,
            resume_history=resume_history,
        )

    with _CODEX_SQLITE_LOCK:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(agent_state_db, timeout=30)
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)")}
            assignments = []
            params: List[Any] = []
            if isolated_rollout is not None and "rollout_path" in columns:
                assignments.append("rollout_path = ?")
                params.append(str(isolated_rollout))
            if "cwd" in columns:
                assignments.append("cwd = ?")
                params.append(str(agent_workspace))
            if resume_history is not None and resume_history.paginated and "history_mode" in columns:
                assignments.append("history_mode = ?")
                params.append("legacy")
            if assignments:
                params.append(resume_session_id)
                conn.execute(f"UPDATE threads SET {', '.join(assignments)} WHERE id = ?", params)
                conn.commit()
        finally:
            if conn is not None:
                conn.close()


class AgentCodexSource:
    """Prepare one fixed source generation per batch, not once per Agent."""

    def __init__(self, home: Path, session: Optional[str], root: Path):
        self.home, self.session, self.root = home, session, root
        self._lock = threading.Lock()
        self._ready = False
        self._error: Optional[BaseException] = None
        self.template: Optional[RolloutTemplate] = None
        self.template_name: Optional[str] = None
        self.raw_segments: List[Tuple[Path, Path, Path, str]] = []
        self.source_workspaces: Set[str] = set()
        self.metrics: Dict[str, Any] = {}
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._future: Optional[concurrent.futures.Future] = None

    def start(self) -> None:
        # Source work overlaps independent workspace preparation, without an
        # all-Agent barrier or using an Agent's preparation slot to prewarm.
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pcr-source")
        self._future = self._executor.submit(self._ensure_prepared)

    def _ensure_prepared(self) -> None:
        with self._lock:
            if self._error is not None:
                raise self._error
            if not self._ready:
                try:
                    self._prepare()
                except BaseException as error:
                    self._error = error
                    raise

    def _prepare(self) -> None:
        make_private_dir(self.root)
        began = time.monotonic()
        rollout = find_rollout_path_for_session(self.home, self.session)
        if rollout is None or not rollout.is_file():
            raise ValueError(f"Cannot prepare the selected Codex history: {self.session}")
        history = resolve_resume_history(self.home, rollout)
        original_history = history
        if history.paginated:
            history.physical_ids  # Validate names and ancestry before publishing any copy.
        else:
            self.template_name = history.segments[-1].destination_name(self.session)
        identities = {segment.path: (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                      for segment in history.segments for stat in (segment.path.stat(),)}
        copy_scoped_database(self.home / "state_5.sqlite", self.root / "state_5.sqlite", "state",
                             (self.session,), tuple(str(segment.path) for segment in history.segments))
        self.metrics["database_snapshot_seconds"] = time.monotonic() - began
        # Resolve through the database snapshot, including revert's authoritative
        # selected rollout, then freeze every inherited prefix before parsing.
        selected = find_rollout_path_for_session(self.root, self.session)
        if selected is not None and selected.resolve() != rollout.resolve():
            raise ValueError("Codex selected history changed while preparing its database snapshot")
        if rollout is not None and rollout.exists():
            began = time.monotonic()
            history = resolve_resume_history(self.home, rollout)
            if history.paginated:
                # Snapshot the projection before the immutable files, so a
                # concurrently appended native index cannot outrun their bytes.
                copy_scoped_database(self.home / "thread_history_1.sqlite", self.root / "thread_history_1.sqlite",
                                     "projection", history.physical_ids)
                history = resolve_resume_history(self.home, rollout)
            physical_ids = history.physical_ids if history.paginated else ()
            self.source_workspaces = history.workspaces
            frozen = []
            for number, segment in enumerate(history.segments):
                target = self.root / f"source-{number}.jsonl"
                before = segment.path.stat()
                clone_file(segment.path, target)
                after = segment.path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError("Codex source history changed while taking its fixed snapshot")
                if not history.paginated:
                    with target.open("r+b") as stream:
                        stream.truncate(segment.end)
                frozen.append(replace(segment, path=target))
                if history.paginated:
                    physical_id = physical_ids[number]
                    relative = Path("sessions/imported") / f"pcr-{self.root.name}" / segment.destination_name()
                    self.raw_segments.append((relative, target, segment.path, physical_id))
            history = ResumeHistory(tuple(frozen))
            if history.paginated:
                # Paging relies on physical byte/ordinal positions. Keep the
                # full reachable closure unchanged, in each private home.
                projection = self.root / "thread_history_1.sqlite"
                if projection.exists():
                    with sqlite3.connect(projection) as connection:
                        has_turn_keys = all({"thread_id", "turn_id"} <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                                            for table in ("thread_items", "thread_turns"))
                        rebuilds = {}
                        for _, frozen_path, _, physical in self.raw_segments:
                            end = frozen_path.stat().st_size
                            row = connection.execute("SELECT next_rollout_byte_offset FROM thread_history_projection_state WHERE thread_id = ?", (physical,)).fetchone()
                            orphan = has_turn_keys and connection.execute(
                                "SELECT 1 FROM thread_items AS item WHERE item.thread_id = ? AND NOT EXISTS "
                                "(SELECT 1 FROM thread_turns AS turn WHERE turn.thread_id = item.thread_id "
                                "AND turn.turn_id = item.turn_id) LIMIT 1", (physical,)).fetchone()
                            if (row and row[0] > end) or orphan:
                                # A watermark cannot certify a cache with missing
                                # turns. Rebuild this private unit from raw bytes.
                                rebuilds[physical] = "missing_turn" if orphan else "watermark_ahead"
                                for table in ("thread_turns", "thread_items", "thread_realtime_items", "thread_history_projection_state"):
                                    if any(row[1] == "thread_id" for row in connection.execute(f"PRAGMA table_info({table})")):
                                        connection.execute(f"DELETE FROM {table} WHERE thread_id = ?", (physical,))
                        self.metrics["projection_rebuild_threads"] = rebuilds
                    projection.chmod(0o400)
            else:
                self.template = RolloutTemplate(self.root / "history.jsonl")
                _rewrite_rollout_session(
                    self.template.path, self.session, Path(self.template.workspace),
                    promote_source=False, codex_home=Path(self.template.home),
                    source_path=rollout, resume_history=history, template=self.template,
                )
                self.template.path.chmod(0o400)
                for segment in frozen:
                    segment.path.unlink()
            self.metrics.update(history_preparation_seconds=time.monotonic() - began,
                                source_history_bytes=history.size_bytes,
                                source_segments=len(history.segments),
                                parsed_records=self.template.records if self.template else 0,
                                database_scope="selected_history_closure",
                                state_database_bytes=(self.root / "state_5.sqlite").stat().st_size if (self.root / "state_5.sqlite").exists() else 0,
                                projection_database_bytes=(self.root / "thread_history_1.sqlite").stat().st_size if (self.root / "thread_history_1.sqlite").exists() else 0,
                                history_mode="paginated" if history.paginated else "legacy")
        # SQLite transactions do not freeze external rollouts or another DB.
        # Reject a mixed generation before any Agent can clone this baseline.
        current_rollout = find_rollout_path_for_session(self.home, self.session)
        if current_rollout is None or current_rollout.resolve() != rollout.resolve() or resolve_resume_history(self.home, current_rollout) != original_history:
            raise ValueError("Codex selected history changed while preparing its fixed snapshot")
        for path, identity in identities.items():
            stat = path.stat()
            if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != identity:
                raise ValueError("Codex source history changed while preparing its fixed snapshot")
        database = self.root / "state_5.sqlite"
        if database.exists():
            database.chmod(0o400)
        self._ready = True

    def populate(self, home: Path, workspace: Path) -> None:
        began = time.monotonic()
        if self._future is not None:
            self._future.result()
        else:
            self._ensure_prepared()
        report = {**self.metrics, "shared_source_wait_seconds": time.monotonic() - began}
        began = time.monotonic()
        database = self.root / "state_5.sqlite"
        if database.exists():
            report["database_cloned"] = clone_file(database, home / database.name)
        isolated = None
        relocation: Dict[str, str] = {}
        if self.raw_segments:
            for relative, source, original, _ in self.raw_segments:
                destination = home / relative
                clone_file(source, destination)
                relocation[str(original)] = str(destination)
                isolated = destination
            projection = self.root / "thread_history_1.sqlite"
            if projection.exists():
                report["projection_cloned"] = clone_file(projection, home / projection.name)
        if self.template is not None:
            isolated = home / "sessions" / "imported" / self.template_name
            isolated.parent.mkdir(parents=True, exist_ok=True)
            report.update(self.template.materialize(isolated, workspace, home, _rebind_rollout_record_workspace))
        if database.exists():
            # This connection is private. It must not serialize unrelated Agent
            # updates behind the global source/import database lock.
            with sqlite3.connect(home / database.name, timeout=30) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                for old, new in relocation.items():
                    connection.execute("UPDATE threads SET rollout_path = ? WHERE rollout_path = ?", (new, old))
                fields, values = [], []
                if "cwd" in columns:
                    fields.append("cwd = ?")
                    values.append(str(workspace))
                if isolated is not None:
                    fields.append("rollout_path = ?")
                    values.append(str(isolated))
                    if "history_mode" in columns:
                        fields.append("history_mode = ?")
                        values.append("paginated" if self.raw_segments else "legacy")
                if fields:
                    connection.execute(f"UPDATE threads SET {', '.join(fields)} WHERE id = ?", [*values, self.session])
        report["private_materialization_seconds"] = time.monotonic() - began
        if self.template is not None:
            report["active_custom_calls"] = sorted(self.template.active_calls)
        (home.parent / "resume_preparation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def execution_context(self, workspace: Path, home: Path) -> Optional[str]:
        if not self.raw_segments:
            return None
        return ("PCR execution environment: historical records describe prior environments and remain unchanged. "
                f"This turn's workspace is {workspace}; its private Codex home is {home}. "
                "Resolve project files against this workspace, not an older Agent copy. Historical workspace mappings: "
                + json.dumps({old: str(workspace) for old in sorted(self.source_workspaces)}, ensure_ascii=False))


def compact_display_text(text: str, limit: int = 96) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def parse_epoch(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_iso_timestamp(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(_dt.datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def format_session_time(epoch_seconds: Optional[int]) -> str:
    if epoch_seconds is None:
        return "-"
    try:
        return _dt.datetime.fromtimestamp(epoch_seconds).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(epoch_seconds)


def same_workspace(value: str, workspace: Path) -> bool:
    try:
        return Path(value).expanduser().resolve() == workspace.resolve()
    except Exception:
        return value == str(workspace)


def session_meta_thread_id(meta: Dict[str, Any]) -> str:
    """Return the actual thread id from Codex session metadata."""
    return str(meta.get("id") or meta.get("session_id") or "").strip()


def codex_subagent_parent_thread_id(
    source: Any,
    thread_source: Any = None,
    parent_thread_id: Any = None,
) -> Optional[str]:
    """Return a parent id for a Codex subagent, or None for a root thread."""
    parent = str(parent_thread_id or "").strip()
    normalized_thread_source = str(thread_source or "").strip().lower()
    parsed_source = source
    if isinstance(source, str):
        source_text = source.strip()
        if source_text.lower() == "subagent":
            return parent
        try:
            parsed_source = json.loads(source_text)
        except (json.JSONDecodeError, TypeError):
            parsed_source = None

    if isinstance(parsed_source, dict) and "subagent" in parsed_source:
        subagent = parsed_source.get("subagent")
        if isinstance(subagent, dict):
            thread_spawn = subagent.get("thread_spawn")
            if isinstance(thread_spawn, dict):
                parent = str(thread_spawn.get("parent_thread_id") or parent).strip()
        return parent
    if normalized_thread_source == "subagent":
        return parent
    return None


def query_resume_sessions_from_state(
    codex_home: Path,
    workspace: Path,
    include_non_interactive: bool = False,
) -> ResumeStateQuery:
    workspace_text = str(workspace)
    workspace = workspace.resolve()
    workspace_values = [str(workspace)]
    if workspace_text not in workspace_values:
        workspace_values.append(workspace_text)
    state_db = codex_home / "state_5.sqlite"
    if not state_db.exists():
        return ResumeStateQuery([], False)

    conn: Optional[sqlite3.Connection] = None
    _CODEX_SQLITE_LOCK.acquire()
    try:
        try:
            conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        except sqlite3.Error:
            return ResumeStateQuery([], False)
        conn.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)")}
        if not {"id", "cwd"}.issubset(columns):
            return ResumeStateQuery([], False)

        wanted = [
            "id",
            "cwd",
            "title",
            "first_user_message",
            "preview",
            "created_at",
            "updated_at",
            "recency_at",
            "source",
            "thread_source",
            "parent_thread_id",
            "model",
            "model_provider",
            "rollout_path",
            "tokens_used",
            "archived",
        ]
        selected = [name for name in wanted if name in columns]
        order_expr = (
            "recency_at DESC, updated_at DESC"
            if {"recency_at", "updated_at"}.issubset(columns)
            else "id DESC"
        )

        def fetch_rows(cwd_values: Sequence[str]) -> List[sqlite3.Row]:
            where = [f"cwd IN ({', '.join('?' for _ in cwd_values)})"]
            if "archived" in columns:
                where.append("COALESCE(archived, 0) = 0")
            if not include_non_interactive and "source" in columns:
                where.append("COALESCE(source, '') != 'exec'")
            sql = (
                f"SELECT {', '.join(selected)} FROM threads "
                f"WHERE {' AND '.join(where)} ORDER BY {order_expr}"
            )
            return conn.execute(sql, list(cwd_values)).fetchall()

        rows = fetch_rows(workspace_values)
        if not rows:
            aliases = [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT cwd FROM threads WHERE cwd IS NOT NULL"
                )
                if str(row[0] or "").strip()
                and same_workspace(str(row[0]), workspace)
                and str(row[0]) not in workspace_values
            ]
            if aliases:
                rows = fetch_rows(aliases)
    except sqlite3.Error:
        return ResumeStateQuery([], False)
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            _CODEX_SQLITE_LOCK.release()

    sessions: List[ResumeSession] = []
    for row in rows:
        data = dict(row)
        session_id = str(data.get("id") or "").strip()
        cwd = str(data.get("cwd") or "").strip()
        if not session_id or not cwd:
            continue
        if (
            codex_subagent_parent_thread_id(
                data.get("source"),
                data.get("thread_source"),
                data.get("parent_thread_id"),
            )
            is not None
        ):
            continue
        title = str(data.get("title") or data.get("first_user_message") or data.get("preview") or session_id)
        updated_at = parse_epoch(data.get("recency_at")) or parse_epoch(data.get("updated_at"))
        model = str(data.get("model") or data.get("model_provider") or "")
        sessions.append(
            ResumeSession(
                session_id=session_id,
                title=title,
                cwd=cwd,
                updated_at=updated_at,
                created_at=parse_epoch(data.get("created_at")),
                source=str(data.get("source") or ""),
                model=model,
                rollout_path=str(data.get("rollout_path") or ""),
                preview=str(data.get("preview") or ""),
                tokens_used=parse_epoch(data.get("tokens_used")),
            )
        )
    return ResumeStateQuery(sessions, True)


def load_resume_sessions_from_state(
    codex_home: Path,
    workspace: Path,
    include_non_interactive: bool = False,
) -> List[ResumeSession]:
    return query_resume_sessions_from_state(
        codex_home,
        workspace,
        include_non_interactive,
    ).sessions


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = item.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
    return "\n".join(parts)


def _resume_index_path(codex_home: Path) -> Path:
    return codex_home / ".pcr" / RESUME_INDEX_FILENAME


def _read_resume_index(path: Path) -> Dict[str, Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(value, dict) or value.get("version") != RESUME_INDEX_VERSION:
        return {}
    entries = value.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        str(key): dict(entry)
        for key, entry in entries.items()
        if isinstance(entry, dict)
    }


def _write_resume_index(path: Path, entries: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": RESUME_INDEX_VERSION,
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "entries": entries,
                },
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _iter_resume_rollouts(directory: Path) -> Iterator[Path]:
    """Yield recent rollout names first without materializing the whole tree."""
    try:
        entries = sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_resume_rollouts(Path(entry.path))
            elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
                yield Path(entry.path)
        except OSError:
            continue


def _jsonl_objects(data: bytes, *, discard_first_partial: bool = False) -> Iterator[Dict[str, Any]]:
    if discard_first_partial:
        newline = data.find(b"\n")
        if newline < 0:
            return
        data = data[newline + 1 :]
    for raw_line in data.splitlines():
        try:
            value = json.loads(raw_line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            yield value


def _message_from_rollout_object(value: Dict[str, Any]) -> Tuple[str, bool]:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return "", False
    if value.get("type") == "event_msg" and payload.get("type") == "user_message":
        return str(payload.get("message") or ""), True
    if value.get("type") == "response_item" and payload.get("type") == "message":
        return text_from_content(payload.get("content")), payload.get("role") == "user"
    return "", False


def _read_resume_index_entry(
    path: Path,
    stat: os.stat_result,
    workspace: Path,
) -> Dict[str, Any]:
    try:
        with path.open("rb") as handle:
            meta_data = handle.read(RESUME_JSONL_META_BYTES)
    except OSError:
        return {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "session": None,
        }

    meta: Dict[str, Any] = {}
    for value in _jsonl_objects(meta_data):
        payload = value.get("payload")
        if value.get("type") == "session_meta" and isinstance(payload, dict):
            meta = payload
            break

    session_id = session_meta_thread_id(meta)
    cwd = str(meta.get("cwd") or "").strip()
    if not session_id or not cwd:
        return {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "session": None,
        }

    source_value = meta.get("source")
    source = (
        source_value.strip()
        if isinstance(source_value, str)
        else json.dumps(source_value, ensure_ascii=False, separators=(",", ":"))
        if source_value is not None
        else ""
    )
    subagent = codex_subagent_parent_thread_id(
        source_value,
        meta.get("thread_source"),
        meta.get("parent_thread_id") or meta.get("forked_from_id"),
    ) is not None
    interactive = not (
        source == "exec" or str(meta.get("originator") or "").strip() == "codex_exec"
    )
    first_user_message = ""
    last_preview = ""
    preview_loaded = same_workspace(cwd, workspace)
    if preview_loaded:
        try:
            with path.open("rb") as handle:
                head = handle.read(RESUME_JSONL_PREVIEW_HEAD_BYTES)
                tail = b""
                tail_is_partial = False
                if stat.st_size > len(head):
                    offset = max(0, stat.st_size - RESUME_JSONL_PREVIEW_TAIL_BYTES)
                    handle.seek(offset)
                    tail = handle.read(RESUME_JSONL_PREVIEW_TAIL_BYTES)
                    tail_is_partial = offset > 0
        except OSError:
            head = meta_data
            tail = b""
            tail_is_partial = False
        for value in _jsonl_objects(head):
            message, is_user = _message_from_rollout_object(value)
            if message:
                last_preview = message
                if is_user and not first_user_message:
                    first_user_message = message
        for value in _jsonl_objects(tail, discard_first_partial=tail_is_partial):
            message, is_user = _message_from_rollout_object(value)
            if message:
                last_preview = message
                if is_user and not first_user_message:
                    first_user_message = message

    title = first_user_message or last_preview or session_id
    return {
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "preview_loaded": preview_loaded,
        "interactive": interactive,
        "subagent": subagent,
        "session": asdict(
            ResumeSession(
                session_id=session_id,
                title=title,
                cwd=cwd,
                updated_at=int(stat.st_mtime),
                created_at=parse_iso_timestamp(meta.get("timestamp")),
                source=source,
                model=str(meta.get("model") or meta.get("model_provider") or ""),
                rollout_path=str(path),
                preview=last_preview,
            )
        ),
    }


def load_resume_sessions_from_jsonl(
    codex_home: Path,
    workspace: Path,
    include_non_interactive: bool = False,
    *,
    max_scan_seconds: float = RESUME_JSONL_SCAN_SECONDS,
    max_scan_files: int = RESUME_JSONL_SCAN_FILES,
) -> ResumeSessionList:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return ResumeSessionList([], source="jsonl")

    workspace = workspace.expanduser().resolve()
    index_path = _resume_index_path(codex_home)
    started = time.monotonic()
    changed = False
    complete = True
    seen: Set[str] = set()
    with _RESUME_INDEX_LOCK:
        entries = _read_resume_index(index_path)
        scanned = 0
        for path in _iter_resume_rollouts(sessions_root):
            if scanned >= max(1, int(max_scan_files)) or (
                time.monotonic() - started >= max(0.05, float(max_scan_seconds))
            ):
                complete = False
                break
            scanned += 1
            key = str(path)
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = entries.get(key)
            cached_session = cached.get("session") if isinstance(cached, dict) else None
            needs_preview = (
                isinstance(cached_session, dict)
                and not bool(cached.get("preview_loaded"))
                and same_workspace(str(cached_session.get("cwd") or ""), workspace)
            )
            if (
                isinstance(cached, dict)
                and cached.get("mtime_ns") == stat.st_mtime_ns
                and cached.get("size") == stat.st_size
                and not needs_preview
            ):
                continue
            entries[key] = _read_resume_index_entry(path, stat, workspace)
            changed = True

        if complete:
            stale = set(entries) - seen
            for key in stale:
                entries.pop(key, None)
            changed = changed or bool(stale)
        if changed or not index_path.exists():
            try:
                _write_resume_index(index_path, entries)
            except OSError:
                pass

        sessions: List[ResumeSession] = []
        for path_text, entry in entries.items():
            value = entry.get("session") if isinstance(entry, dict) else None
            if not isinstance(value, dict):
                continue
            if bool(entry.get("subagent")):
                continue
            if not include_non_interactive and not bool(entry.get("interactive", True)):
                continue
            if not same_workspace(str(value.get("cwd") or ""), workspace):
                continue
            if path_text not in seen and not Path(path_text).is_file():
                continue
            try:
                sessions.append(ResumeSession(**value))
            except (TypeError, ValueError):
                continue

    sessions.sort(key=lambda session: session.updated_at or 0, reverse=True)
    return ResumeSessionList(sessions, complete=complete, source="jsonl")


def list_resume_sessions(
    workspace: Path,
    include_non_interactive: bool = False,
) -> ResumeSessionList:
    codex_home = get_codex_home()
    state = query_resume_sessions_from_state(
        codex_home,
        workspace,
        include_non_interactive,
    )
    if state.available:
        return ResumeSessionList(state.sessions, source="state")
    return load_resume_sessions_from_jsonl(
        codex_home,
        workspace,
        include_non_interactive,
    )


def print_resume_sessions(sessions: List[ResumeSession], workspace: Path) -> None:
    if HAS_RICH:
        assert console is not None
        table = Table(
            title=f"Codex resume sessions for {absolute_path_for_display(workspace)}",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("#", justify="right")
        table.add_column("updated")
        table.add_column("source")
        table.add_column("session_id")
        table.add_column("title")
        for idx, session in enumerate(sessions, 1):
            table.add_row(
                str(idx),
                format_session_time(session.updated_at),
                session.source or "-",
                session.session_id,
                compact_display_text(session.title),
            )
        console.print(table)
        return

    print(f"Codex resume sessions for {absolute_path_for_display(workspace)}")
    print(" #  updated           source  session_id                            title")
    for idx, session in enumerate(sessions, 1):
        print(
            f"{idx:>2}  {format_session_time(session.updated_at):<16} "
            f"{(session.source or '-'): <6}  {session.session_id:<36}  {compact_display_text(session.title)}"
        )


def read_interactive_line(prompt: str) -> str:
    if sys.stdin.isatty():
        return input(prompt)
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline()
    except OSError as exc:
        raise SystemExit("--resume 需要交互式 TTY 来选择 session；或使用 --resume-session-id 显式指定。") from exc


def choose_resume_session(sessions: List[ResumeSession], workspace: Path) -> ResumeSession:
    print_resume_sessions(sessions, workspace)
    while True:
        answer = read_interactive_line("选择要 resume 的序号（Enter=1，q=取消）：").strip()
        if not answer:
            return sessions[0]
        if answer.lower() in {"q", "quit", "cancel"}:
            raise SystemExit("已取消 --resume。")
        try:
            idx = int(answer)
        except ValueError:
            print("请输入列表中的数字序号。")
            continue
        if 1 <= idx <= len(sessions):
            return sessions[idx - 1]
        print(f"请输入 1 到 {len(sessions)} 之间的数字。")


def resolve_resume_session(args: argparse.Namespace, workspace: Path) -> Optional[ResumeSession]:
    if not args.resume and not args.resume_session_id:
        return None

    if args.resume_session_id:
        session_id = args.resume_session_id.strip()
        if not session_id:
            raise SystemExit("--resume-session-id 不能为空。")
        error = subagent_resume_error(get_codex_home(), session_id)
        if error:
            raise SystemExit(error)
        for session in list_resume_sessions(workspace, include_non_interactive=True):
            if session.session_id == session_id:
                return session
        return ResumeSession(
            session_id=session_id,
            title="explicit session id",
            cwd=str(workspace),
            updated_at=None,
        )

    sessions = list_resume_sessions(workspace, include_non_interactive=args.resume_include_non_interactive)
    if not sessions:
        hint = "可加 --resume-include-non-interactive 包含 codex exec 产生的非交互会话。"
        raise SystemExit(f"当前 workspace 没有可 resume 的 Codex 会话：{workspace}\n{hint}")
    return choose_resume_session(sessions, workspace)


def append_promotion_error(promotion: CodexSessionPromotion, message: str) -> None:
    promotion.error = f"{promotion.error}; {message}" if promotion.error else message


def find_rollout_path_for_session(codex_home: Path, session_id: str) -> Optional[Path]:
    database = codex_home / "state_5.sqlite"
    if database.exists():
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                fields = "rollout_path, history_mode" if "history_mode" in columns else "rollout_path"
                row = connection.execute(f"SELECT {fields} FROM threads WHERE id = ?", (session_id,)).fetchone()
            if row and row[0]:
                selected = Path(row[0]).expanduser()
                if selected.is_file():
                    # Fork/revert rollouts share a stable thread id. Only the
                    # database identifies the selected physical generation.
                    with selected.open("rb") as stream:
                        metadata = next((record for record in _jsonl_objects(stream.read(RESUME_JSONL_META_BYTES))
                                         if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict)), None)
                    if not metadata or session_meta_thread_id(metadata["payload"]) == session_id:
                        return selected
            if row and len(row) > 1 and row[1] == "paginated":
                return None
        except sqlite3.Error:
            pass
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return None

    try:
        matches = sorted(
            sessions_root.rglob(f"*{session_id}*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        matches = []
    if matches:
        return matches[0]

    for path in sessions_root.rglob("*.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    payload = obj.get("payload")
                    if obj.get("type") != "session_meta" or not isinstance(payload, dict):
                        continue
                    if session_meta_thread_id(payload) == session_id:
                        return path
                    # Once identified, this rollout cannot belong to the target
                    # session. Its later message bodies can be very large.
                    if session_meta_thread_id(payload):
                        break
        except OSError:
            continue
    return None


def _copied_codex_entry_storage_bytes(path: Path) -> int:
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError:
            return 0
        return estimate_path_storage_bytes(target) if target.is_file() else 0
    return estimate_path_storage_bytes(path)


def estimate_agent_metadata_bytes(
    codex_home: Path,
    resume_session_id: Optional[str] = None,
    resume_codex_home: Optional[Path] = None,
    runtime_reserve_bytes: int = AGENT_METADATA_RESERVE_BYTES,
) -> int:
    """Estimate one agent's copied CODEX_HOME and runtime metadata."""
    codex_home = codex_home.expanduser().resolve()
    total = max(0, int(runtime_reserve_bytes))
    for name in sorted(CODEX_SUPPORT_FILES | CODEX_SUPPORT_DIRS):
        total += _copied_codex_entry_storage_bytes(codex_home / name)

    total += estimate_resume_source_bytes(resume_codex_home or codex_home, resume_session_id)
    return total


def estimate_resume_source_bytes(home: Path, session: Optional[str]) -> int:
    if not session:
        return 0
    home = home.expanduser().resolve()
    rollout = find_rollout_path_for_session(home, session)
    history = resolve_resume_history(home, rollout) if rollout else None
    total = scoped_database_bytes(home / "state_5.sqlite", "state", (session,),
                                  tuple(str(segment.path) for segment in history.segments) if history else ())
    if history is not None:
        total += sum(segment.path.stat().st_size for segment in history.segments) if history.paginated else history.size_bytes
        if history.paginated and (home / "thread_history_1.sqlite").exists():
            total += scoped_database_bytes(home / "thread_history_1.sqlite", "projection", history.physical_ids)
    return total


def estimate_run_storage(
    workspace: Path,
    num_agents: int,
    resume_session_id: Optional[str] = None,
    codex_home: Optional[Path] = None,
    resume_codex_home: Optional[Path] = None,
    metadata_reserve_bytes: int = AGENT_METADATA_RESERVE_BYTES,
) -> RunStorageEstimate:
    """Estimate storage PCR will allocate before starting an agent run."""
    validate_agent_count(num_agents, "num_agents")
    workspace_bytes = estimate_workspace_copy_bytes(workspace)
    metadata_bytes = estimate_agent_metadata_bytes(
        codex_home or get_codex_home(),
        None,
        None,
        metadata_reserve_bytes,
    )
    shared_source_bytes = estimate_resume_source_bytes(resume_codex_home or codex_home or get_codex_home(), resume_session_id)
    metadata_bytes += shared_source_bytes
    workspace_copies_bytes = workspace_bytes * num_agents
    all_metadata_bytes = metadata_bytes * num_agents + shared_source_bytes
    return RunStorageEstimate(
        num_agents=num_agents,
        workspace_bytes_per_agent=workspace_bytes,
        workspace_copies_bytes=workspace_copies_bytes,
        metadata_bytes_per_agent=metadata_bytes,
        metadata_bytes=all_metadata_bytes,
        total_bytes=workspace_copies_bytes + all_metadata_bytes,
        shared_source_bytes=shared_source_bytes,
    )


def estimate_staged_run_storage(
    workspace: Path,
    candidate_agents: int,
    synthesis_agents: int = 0,
    resume_session_id: Optional[str] = None,
    codex_home: Optional[Path] = None,
    resume_codex_home: Optional[Path] = None,
    metadata_reserve_bytes: int = AGENT_METADATA_RESERVE_BYTES,
) -> RunStorageEstimate:
    """Estimate candidates and synthesis agents sharing one resume baseline."""
    validate_agent_count(candidate_agents, "candidate_agents")
    validate_agent_count(synthesis_agents, "synthesis_agents", allow_zero=True)
    if candidate_agents + synthesis_agents > MAX_AGENT_COUNT:
        raise ValueError(
            "candidate_agents and synthesis_agents together must not exceed "
            f"{MAX_AGENT_COUNT}"
        )

    codex_home = codex_home or get_codex_home()
    workspace_bytes = estimate_workspace_copy_bytes(workspace)
    metadata_per_agent = estimate_agent_metadata_bytes(
        codex_home,
        None,
        None,
        metadata_reserve_bytes,
    )
    shared_source_bytes = estimate_resume_source_bytes(resume_codex_home or codex_home, resume_session_id)
    metadata_per_agent += shared_source_bytes
    total_agents = candidate_agents + synthesis_agents
    workspace_copies_bytes = workspace_bytes * total_agents
    metadata_bytes = metadata_per_agent * total_agents + shared_source_bytes
    return RunStorageEstimate(
        num_agents=total_agents,
        workspace_bytes_per_agent=workspace_bytes,
        workspace_copies_bytes=workspace_copies_bytes,
        metadata_bytes_per_agent=metadata_per_agent,
        metadata_bytes=metadata_bytes,
        total_bytes=workspace_copies_bytes + metadata_bytes,
        shared_source_bytes=shared_source_bytes,
    )


def available_storage_bytes(path: Path) -> int:
    """Return free bytes on the filesystem that will contain path."""
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if candidate.is_file():
        candidate = candidate.parent
    return int(shutil.disk_usage(candidate).free)


def format_storage_bytes(value: int) -> str:
    size = max(0, int(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            precision = 0 if unit == "B" else 2
            return f"{amount:.{precision}f} {unit}"
        amount /= 1024
    return f"{size} B"


_INTERNAL_HISTORY_PREFIXES = (
    "# AGENTS.md instructions",
    "<apps_instructions>",
    "<collaboration_mode>",
    "<environment_context>",
    "<permissions instructions>",
    "<recommended_plugins>",
    "<skills_instructions>",
)


def codex_history_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(text for item in value if (text := codex_history_text(item)))
    if isinstance(value, dict):
        for key in ("message", "text", "input_text", "output_text", "content", "summary", "reasoning"):
            text = codex_history_text(value.get(key))
            if text:
                return text
    return ""


def is_internal_codex_history_message(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _INTERNAL_HISTORY_PREFIXES)


def clean_codex_history_text(category: str, text: str) -> str:
    if category != "thought":
        return text.strip()
    return "\n".join(line for line in text.splitlines() if line.strip() != "<!-- -->").strip()


_OAI_MEMORY_CITATION_PATTERN = re.compile(
    r"<oai-mem-citation\b[^>]*>.*?</oai-mem-citation\s*>",
    re.IGNORECASE | re.DOTALL,
)


def comparable_codex_history_text(category: str, text: str) -> str:
    """Normalize mirrored Codex events without changing retained history text."""
    value = text
    if category == "output":
        value = _OAI_MEMORY_CITATION_PATTERN.sub("", value)
    return " ".join(value.split())


def load_codex_session_history(
    codex_home: Path,
    session_id: str,
    rollout_path: str | Path | None = None,
    max_lines: int | None = None,
) -> List[CodexHistoryEntry]:
    """Load the readable conversation from one Codex rollout JSONL file."""
    path = Path(rollout_path).expanduser() if rollout_path else None
    if path is None or not path.is_file():
        path = find_rollout_path_for_session(codex_home, session_id)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"Codex rollout not found for session {session_id}")
    path_mappings = load_session_workspace_mappings(codex_home, session_id)

    candidates: List[Tuple[int, str, str, CodexHistoryEntry]] = []
    current_turn_id = ""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f):
            if max_lines is not None and line_number >= max(0, int(max_lines)):
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            obj_type = str(obj.get("type") or "")
            payload_type = str(payload.get("type") or "")
            if obj_type == "event_msg" and payload_type == "task_started":
                current_turn_id = str(payload.get("turn_id") or "")
                continue
            category = ""
            source = ""
            text = ""
            if obj_type == "session_meta":
                rollout_session_id = session_meta_thread_id(payload)
                if rollout_session_id and rollout_session_id != session_id:
                    raise ValueError(
                        f"Codex rollout session mismatch: expected {session_id}, found {rollout_session_id}"
                    )
                continue
            if obj_type == "event_msg":
                source = "event"
                if payload_type == "user_message":
                    category = "user"
                    text = codex_history_text(payload.get("message"))
                elif payload_type == "agent_reasoning":
                    category = "thought"
                    text = codex_history_text(
                        payload.get("message") or payload.get("text") or payload.get("reasoning")
                    )
                elif payload_type == "agent_message":
                    category = "output"
                    text = codex_history_text(payload.get("message"))
            elif obj_type == "response_item":
                source = "response"
                if payload_type == "message":
                    role = str(payload.get("role") or "")
                    if role == "user":
                        category = "user"
                    elif role == "assistant":
                        category = "output"
                    text = codex_history_text(payload.get("content"))
                elif payload_type == "reasoning":
                    category = "thought"
                    text = codex_history_text(payload.get("summary"))

            text = clean_codex_history_text(category, text)
            if category == "output":
                text = rewrite_workspace_references(text, path_mappings)
            if not category or not text:
                continue
            if category == "user" and is_internal_codex_history_message(text):
                continue
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            turn_id = str(metadata.get("turn_id") or "") if isinstance(metadata, dict) else ""
            resolved_turn_id = turn_id or current_turn_id
            candidates.append(
                (
                    line_number,
                    source,
                    resolved_turn_id,
                    CodexHistoryEntry(
                        category,
                        text,
                        id=f"history:{session_id}:{line_number}",
                        turn_id=resolved_turn_id,
                    ),
                )
            )

    history: List[Tuple[str, str, CodexHistoryEntry]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for _line_number, source, turn_id, entry in sorted(candidates, key=lambda item: item[0]):
        entry_key = (turn_id, entry.category, entry.text)
        if entry_key in seen:
            continue
        seen.add(entry_key)
        if history and history[-1][2].category == entry.category:
            previous_source, previous_turn_id, previous = history[-1]
            same_turn = bool(turn_id and previous_turn_id and turn_id == previous_turn_id)
            equivalent_mirror = comparable_codex_history_text(
                previous.category,
                previous.text,
            ) == comparable_codex_history_text(entry.category, entry.text)
            if equivalent_mirror and (same_turn or source != previous_source):
                # A final answer is recorded twice by Codex: first as the clean
                # event_msg shown in the UI, then as a response_item that may
                # append internal citation metadata. Prefer the clean event
                # instead of concatenating both copies into one history turn.
                if source == "event" and previous_source != "event":
                    history[-1] = (source, turn_id, entry)
                continue
            if same_turn:
                history[-1] = (
                    previous_source,
                    previous_turn_id,
                    CodexHistoryEntry(
                        entry.category,
                        f"{previous.text}\n{entry.text}",
                        id=previous.id,
                        turn_id=previous.turn_id or entry.turn_id,
                    ),
                )
                continue
        history.append((source, turn_id, entry))
    return [entry for _source, _turn_id, entry in history]


def subagent_resume_error(codex_home: Path, session_id: str) -> Optional[str]:
    """Explain why a known Codex v2 subagent cannot be resumed directly."""
    state_db = codex_home / "state_5.sqlite"
    if state_db.exists():
        conn: Optional[sqlite3.Connection] = None
        _CODEX_SQLITE_LOCK.acquire()
        try:
            conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)")}
            selected = [name for name in ("source", "thread_source", "parent_thread_id") if name in columns]
            if "id" in columns and selected:
                row = conn.execute(
                    f"SELECT {', '.join(selected)} FROM threads WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row is not None:
                    data = dict(row)
                    parent = codex_subagent_parent_thread_id(
                        data.get("source"),
                        data.get("thread_source"),
                        data.get("parent_thread_id"),
                    )
                    if parent is None:
                        return None
                    return format_subagent_resume_error(session_id, parent)
        except sqlite3.Error:
            pass
        finally:
            try:
                if conn is not None:
                    conn.close()
            finally:
                _CODEX_SQLITE_LOCK.release()

    rollout_path = find_rollout_path_for_session(codex_home, session_id)
    if rollout_path is None:
        return None
    try:
        with rollout_path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") if isinstance(obj, dict) else None
                if (
                    not isinstance(payload, dict)
                    or obj.get("type") != "session_meta"
                    or session_meta_thread_id(payload) != session_id
                ):
                    continue
                parent = codex_subagent_parent_thread_id(
                    payload.get("source"),
                    payload.get("thread_source"),
                    payload.get("parent_thread_id") or payload.get("forked_from_id"),
                )
                if parent is not None:
                    return format_subagent_resume_error(session_id, parent)
                return None
    except OSError:
        pass
    return None


def format_subagent_resume_error(session_id: str, parent_thread_id: str) -> str:
    parent_hint = f"；请改用父线程 {parent_thread_id}" if parent_thread_id else ""
    return f"Codex multi-agent v2 子线程不能直接 resume：{session_id}{parent_hint}。"


def _custom_tool_call_ids(items: Iterable[Any]) -> Set[str]:
    return {
        str(item.get("call_id"))
        for item in items
        if isinstance(item, dict)
        and item.get("type") == "custom_tool_call"
        and item.get("call_id")
    }


def _remove_orphan_custom_tool_outputs(items: List[Any]) -> int:
    call_ids = _custom_tool_call_ids(items)
    kept: List[Any] = []
    removed = 0
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("type") == "custom_tool_call_output"
            and item.get("call_id")
            and str(item["call_id"]) not in call_ids
        ):
            removed += 1
            continue
        kept.append(item)
    if removed:
        items[:] = kept
    return removed


def _sanitize_rollout_record(obj: Dict[str, Any], active_call_ids: Set[str]) -> Tuple[bool, int]:
    payload = obj.get("payload")
    if obj.get("type") == "compacted":
        active_call_ids.clear()
        replacement_history = payload.get("replacement_history") if isinstance(payload, dict) else None
        if isinstance(replacement_history, list):
            removed = _remove_orphan_custom_tool_outputs(replacement_history)
            active_call_ids.update(_custom_tool_call_ids(replacement_history))
            return True, removed
    elif obj.get("type") == "response_item" and isinstance(payload, dict):
        item_type = payload.get("type")
        call_id = payload.get("call_id")
        if item_type == "custom_tool_call" and call_id:
            active_call_ids.add(str(call_id))
        elif item_type == "custom_tool_call_output" and call_id and str(call_id) not in active_call_ids:
            return False, 1
    return True, 0


def sanitize_rollout_orphan_custom_tool_outputs(rollout_path: Path, *, start_offset: int = 0,
                                               baseline_calls: Optional[Set[str]] = None) -> int:
    """Remove custom-tool outputs Codex already rejects after compaction.

    Code-mode tools may emit several asynchronous outputs under one call ID. If
    context compaction replaces the history without that call, later outputs
    become permanently unpairable and Codex logs the same error on every
    normalization pass. PCR repairs only its copied/imported rollout.
    """
    if start_offset:
        # The prepared prefix was already checked. Repair only new records,
        # retaining byte positions so native paging/projections remain valid.
        patches = []
        calls = set(baseline_calls or ())
        known = baseline_calls is not None
        removed = 0
        with rollout_path.open("rb") as source:
            source.seek(start_offset)
            while raw := source.readline():
                offset = source.tell() - len(raw)
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "compacted":
                    known = True
                if not known:
                    continue
                keep, count = _sanitize_rollout_record(record, calls)
                if count:
                    data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() if keep else b""
                    if len(data) >= len(raw):
                        raise ValueError("History repair cannot preserve its native byte positions")
                    patches.append((offset, data + b" " * (len(raw) - len(data) - 1) + b"\n"))
                    removed += count
        if patches:
            temporary = rollout_path.with_name(f".{rollout_path.name}-repair-{time.time_ns()}")
            try:
                clone_file(rollout_path, temporary)
                with temporary.open("r+b") as output:
                    for offset, data in patches:
                        output.seek(offset)
                        output.write(data)
                os.replace(temporary, rollout_path)
            finally:
                temporary.unlink(missing_ok=True)
        return removed
    active_call_ids: Set[str] = set()
    removed = 0
    changed = False
    tmp_path = rollout_path.with_name(
        f".{rollout_path.name}.pcr-tool-history-{os.getpid()}"
    )

    try:
        with rollout_path.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as src, tmp_path.open("w", encoding="utf-8") as dst:
            for line in src:
                rewritten = line
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(rewritten)
                    continue
                if not isinstance(obj, dict):
                    dst.write(rewritten)
                    continue

                keep, removed_here = _sanitize_rollout_record(obj, active_call_ids)
                removed += removed_here
                changed = changed or bool(removed_here)
                if not keep:
                    continue
                if removed_here:
                    rewritten = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"

                dst.write(rewritten)

        if changed:
            try:
                shutil.copystat(
                    rollout_path,
                    tmp_path,
                    follow_symlinks=False,
                )
            except OSError:
                pass
            os.replace(tmp_path, rollout_path)
        else:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return removed


def _rewrite_rollout_session(
    rollout_path: Path,
    session_id: str,
    workspace: Path,
    *,
    promote_source: bool,
    codex_home: Optional[Path] = None,
    source_path: Optional[Path] = None,
    resume_history: Optional[ResumeHistory] = None,
    template: Optional[RolloutTemplate] = None,
) -> Tuple[bool, bool]:
    """Copy/rebind and repair a rollout in one atomic streaming pass."""
    target_cwd = str(workspace)
    changed = resume_history is not None and resume_history.paginated
    source_promoted = False
    session_found = False
    old_workspaces: Set[str] = set(resume_history.workspaces) if resume_history is not None else set()
    target_codex_home = str(codex_home) if codex_home is not None else None
    old_codex_homes: Set[str] = set()
    active_call_ids: Set[str] = set()
    source_path = source_path or rollout_path
    tmp_path = rollout_path.with_name(f".{rollout_path.name}.pcr-tmp-{os.getpid()}")

    try:
        with ExitStack() as stack:
            src = resume_history.lines(session_id) if resume_history is not None else stack.enter_context(source_path.open("r", encoding="utf-8", errors="replace"))
            if resume_history is not None:
                stack.callback(src.close)
            dst = stack.enter_context(tmp_path.open("wb"))
            for line in src:
                if template is not None and (template.workspace in line or template.home in line):
                    raise ValueError("Prepared history binding marker occurs in source content")
                rewritten = line
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(rewritten.encode("utf-8"))
                    continue
                if not isinstance(obj, dict):
                    dst.write(rewritten.encode("utf-8"))
                    continue

                keep, removed = _sanitize_rollout_record(obj, active_call_ids)
                if removed:
                    changed = True
                    if not keep:
                        continue
                    rewritten = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                bound = False
                payload = obj.get("payload")
                if obj.get("type") == "session_meta" and isinstance(payload, dict):
                    if session_meta_thread_id(payload) == session_id:
                        session_found = True
                        old_workspace = str(payload.get("cwd") or "").rstrip("/")
                        if old_workspace:
                            old_workspaces.add(old_workspace)
                        parent = codex_subagent_parent_thread_id(
                            payload.get("source"),
                            payload.get("thread_source"),
                            payload.get("parent_thread_id") or payload.get("forked_from_id"),
                        )
                        if parent is not None:
                            raise ValueError(format_subagent_resume_error(session_id, parent))
                        if payload.get("cwd") != target_cwd:
                            payload["cwd"] = target_cwd
                            changed = True
                        if promote_source and payload.get("source") == "exec":
                            payload["source"] = "cli"
                            changed = True
                            source_promoted = True
                        if promote_source and payload.get("originator") == "codex_exec":
                            payload["originator"] = "codex-tui"
                            changed = True
                            source_promoted = True
                        if promote_source and not payload.get("thread_source"):
                            payload["thread_source"] = "user"
                            changed = True
                        rewritten = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                        bound = True
                elif session_found and _rebind_rollout_record_workspace(
                    obj,
                    target_cwd,
                    old_workspaces,
                    target_codex_home,
                    old_codex_homes,
                ):
                    changed = True
                    rewritten = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                    bound = True
                if template is None:
                    dst.write(rewritten.encode("utf-8"))
                else:
                    template.write(dst, obj, rewritten, bound=bound)

        if template is not None:
            template.active_calls = active_call_ids

        if changed or source_path != rollout_path:
            try:
                shutil.copystat(source_path, tmp_path, follow_symlinks=False)
            except OSError:
                pass
            os.replace(tmp_path, rollout_path)
        else:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return changed, source_promoted


def update_rollout_session_meta(
    rollout_path: Path,
    session_id: str,
    workspace: Path,
    codex_home: Optional[Path] = None,
) -> Tuple[bool, bool]:
    """Rewrite a rollout so resume discovery and execution use ``workspace``."""
    changed, source_promoted = _rewrite_rollout_session(
        rollout_path,
        session_id,
        workspace,
        promote_source=True,
        codex_home=codex_home,
    )
    return changed, source_promoted


def promote_codex_session_to_workspace(
    codex_home: Path,
    session_id: str,
    workspace: Path,
) -> CodexSessionPromotion:
    """Make the selected Codex exec session resumable from the real workspace."""
    workspace = workspace.resolve()
    promotion = CodexSessionPromotion(session_id=session_id, workspace=str(workspace))
    state_db = codex_home / "state_5.sqlite"
    rollout_path_text = ""

    if state_db.exists():
        promotion.state_path = str(state_db)
        conn: Optional[sqlite3.Connection] = None
        _CODEX_SQLITE_LOCK.acquire()
        try:
            conn = sqlite3.connect(state_db, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)")}
            if not {"id", "cwd"}.issubset(columns):
                append_promotion_error(promotion, "state_5.sqlite threads table is missing id/cwd columns")
            else:
                selected = [
                    name
                    for name in (
                        "id",
                        "cwd",
                        "rollout_path",
                        "source",
                        "thread_source",
                        "archived",
                        "archived_at",
                        "updated_at",
                        "updated_at_ms",
                        "recency_at",
                        "recency_at_ms",
                    )
                    if name in columns
                ]
                row = conn.execute(f"SELECT {', '.join(selected)} FROM threads WHERE id = ?", (session_id,)).fetchone()
                if row is not None:
                    data = dict(row)
                    promotion.state_found = True
                    promotion.old_cwd = str(data.get("cwd") or "")
                    rollout_path_text = str(data.get("rollout_path") or "")

                    parent = codex_subagent_parent_thread_id(
                        data.get("source"),
                        data.get("thread_source"),
                        data.get("parent_thread_id"),
                    )
                    if parent is not None:
                        append_promotion_error(promotion, format_subagent_resume_error(session_id, parent))
                        return promotion

                    assignments = []
                    params: List[Any] = []
                    if data.get("cwd") != str(workspace):
                        assignments.append("cwd = ?")
                        params.append(str(workspace))
                    if "source" in columns and data.get("source") == "exec":
                        assignments.append("source = ?")
                        params.append("cli")
                        promotion.source_promoted = True
                    if "thread_source" in columns and not data.get("thread_source"):
                        assignments.append("thread_source = ?")
                        params.append("user")
                    if "archived" in columns and data.get("archived"):
                        assignments.append("archived = ?")
                        params.append(0)
                        if "archived_at" in columns:
                            assignments.append("archived_at = ?")
                            params.append(None)

                    if "recency_at" in columns:
                        recency_at = parse_epoch(data.get("updated_at")) or int(time.time())
                        if parse_epoch(data.get("recency_at")) != recency_at:
                            assignments.append("recency_at = ?")
                            params.append(recency_at)
                    if "recency_at_ms" in columns:
                        recency_at_ms = parse_epoch(data.get("updated_at_ms"))
                        if recency_at_ms is None:
                            recency_at_ms = (parse_epoch(data.get("updated_at")) or int(time.time())) * 1000
                        if parse_epoch(data.get("recency_at_ms")) != recency_at_ms:
                            assignments.append("recency_at_ms = ?")
                            params.append(recency_at_ms)

                    if assignments:
                        params.append(session_id)
                        conn.execute(f"UPDATE threads SET {', '.join(assignments)} WHERE id = ?", params)
                        conn.commit()
                        promotion.state_updated = True
                else:
                    append_promotion_error(promotion, f"session not found in state_5.sqlite: {session_id}")
        except sqlite3.Error as exc:
            append_promotion_error(promotion, f"sqlite update failed: {exc}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            finally:
                _CODEX_SQLITE_LOCK.release()

    rollout_path = Path(rollout_path_text).expanduser() if rollout_path_text else find_rollout_path_for_session(codex_home, session_id)
    if rollout_path is not None:
        promotion.rollout_path = str(rollout_path)
        if rollout_path.exists():
            promotion.rollout_found = True
            try:
                promotion.rollout_updated, rollout_source_promoted = update_rollout_session_meta(
                    rollout_path,
                    session_id,
                    workspace,
                    codex_home,
                )
                promotion.source_promoted = promotion.source_promoted or rollout_source_promoted
            except Exception as exc:  # noqa: BLE001
                append_promotion_error(promotion, f"rollout update failed: {exc}")
        else:
            append_promotion_error(promotion, f"rollout file not found: {rollout_path}")

    if not promotion.state_found and not promotion.rollout_found and not promotion.error:
        append_promotion_error(promotion, f"session metadata not found: {session_id}")

    return promotion


def rollout_destination_for_import(source_rollout: Path, source_codex_home: Path, real_codex_home: Path) -> Path:
    rel = relative_path_or_import_path(source_rollout, source_codex_home)
    if rel.parts and rel.parts[0] == "sessions":
        return real_codex_home / rel
    return real_codex_home / "sessions" / "imported" / source_rollout.name


def import_history_projection(source_home: Path, destination_home: Path, physical_ids: Tuple[str, ...]) -> None:
    """Carry the selected native projection, never another candidate's cache."""
    source = source_home / "thread_history_1.sqlite"
    destination = destination_home / source.name
    if not destination.exists():
        copy_scoped_database(source, destination, "projection", physical_ids)
        return
    with _CODEX_SQLITE_LOCK, sqlite3.connect(destination, timeout=30, uri=True) as connection:
        if source.exists():
            connection.execute("ATTACH DATABASE ? AS candidate", (f"file:{source}?mode=ro",))
        for table in ("thread_turns", "thread_items", "thread_realtime_items", "thread_history_projection_state"):
            columns = [row[1] for row in connection.execute(f"PRAGMA main.table_info({table})")]
            if "thread_id" not in columns:
                continue
            source_columns = ([row[1] for row in connection.execute(f"PRAGMA candidate.table_info({table})")]
                              if source.exists() else [])
            placeholders = ",".join("?" for _ in physical_ids)
            connection.execute(f"DELETE FROM {table} WHERE thread_id IN ({placeholders})", physical_ids)
            if columns == source_columns:
                # These are native-produced rows, not a reconstructed index.
                names = ",".join(f'"{name}"' for name in columns)
                connection.execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM candidate.{table} WHERE thread_id IN ({placeholders})", physical_ids)


def import_codex_session_to_workspace(
    real_codex_home: Path,
    source_codex_home: Path,
    session_id: str,
    workspace: Path,
) -> CodexSessionPromotion:
    """Import one selected session from an isolated agent CODEX_HOME."""
    workspace = workspace.resolve()
    real_codex_home = real_codex_home.resolve()
    source_codex_home = source_codex_home.resolve()
    promotion = CodexSessionPromotion(
        session_id=session_id,
        workspace=str(workspace),
        source_codex_home=str(source_codex_home),
    )

    if source_codex_home == real_codex_home:
        return promote_codex_session_to_workspace(real_codex_home, session_id, workspace)

    source_db = source_codex_home / "state_5.sqlite"
    real_db = real_codex_home / "state_5.sqlite"
    source_rollout_text = ""
    imported_rollout_path: Optional[Path] = None
    native_history: Optional[ResumeHistory] = None
    source_rollout = find_rollout_path_for_session(source_codex_home, session_id)
    if source_rollout is not None and source_rollout.exists():
        candidate_history = resolve_resume_history(source_codex_home, source_rollout)
        if candidate_history.paginated:
            native_history = candidate_history
            destination_root = real_codex_home / "sessions" / "imported" / f"pcr-{session_id}-{time.time_ns()}"
            # Keep adopted ancestry self-contained, with unchanged bytes and
            # physical identities. Never overwrite a live ancestor's file.
            for segment, physical in zip(native_history.segments, native_history.physical_ids):
                destination = destination_root / segment.destination_name()
                copy_file_atomic(segment.path, destination)
                imported_rollout_path = destination
            import_history_projection(source_codex_home, real_codex_home, native_history.physical_ids)
        else:
            imported_rollout_path = rollout_destination_for_import(source_rollout, source_codex_home, real_codex_home).with_name(
                candidate_history.segments[-1].destination_name(session_id))
            if imported_rollout_path.name != source_rollout.name and imported_rollout_path.exists():
                raise ValueError(f"Cannot normalize imported Codex history over an existing rollout: {imported_rollout_path}")

    if source_db.exists() and real_db.exists():
        promotion.state_path = str(real_db)
        source_conn: Optional[sqlite3.Connection] = None
        real_conn: Optional[sqlite3.Connection] = None
        _CODEX_SQLITE_LOCK.acquire()
        try:
            source_conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
            source_conn.row_factory = sqlite3.Row
            real_conn = sqlite3.connect(real_db, timeout=30)
            real_conn.row_factory = sqlite3.Row
            real_conn.execute("PRAGMA busy_timeout = 30000")

            source_columns = {str(row[1]) for row in source_conn.execute("PRAGMA table_info(threads)")}
            real_columns = {str(row[1]) for row in real_conn.execute("PRAGMA table_info(threads)")}
            common_columns = [name for name in source_columns.intersection(real_columns) if name != "id"]
            if "id" not in source_columns or "id" not in real_columns:
                append_promotion_error(promotion, "threads table is missing id column")
            else:
                selected = ["id", *common_columns]
                row = source_conn.execute(f"SELECT {', '.join(selected)} FROM threads WHERE id = ?", (session_id,)).fetchone()
                if row is None:
                    append_promotion_error(promotion, f"session not found in isolated state_5.sqlite: {session_id}")
                else:
                    data = dict(row)
                    promotion.state_found = True
                    promotion.old_cwd = str(data.get("cwd") or "")
                    source_rollout_text = str(data.get("rollout_path") or "")

                    parent = codex_subagent_parent_thread_id(
                        data.get("source"),
                        data.get("thread_source"),
                        data.get("parent_thread_id"),
                    )
                    if parent is not None:
                        append_promotion_error(promotion, format_subagent_resume_error(session_id, parent))
                        return promotion

                    source_rollout = Path(source_rollout_text).expanduser() if source_rollout_text else find_rollout_path_for_session(source_codex_home, session_id)
                    if source_rollout is not None:
                        imported_rollout_path = imported_rollout_path or rollout_destination_for_import(source_rollout, source_codex_home, real_codex_home)
                        if "rollout_path" in common_columns:
                            data["rollout_path"] = str(imported_rollout_path)

                    if "cwd" in common_columns:
                        data["cwd"] = str(workspace)
                    if "source" in common_columns and data.get("source") == "exec":
                        data["source"] = "cli"
                        promotion.source_promoted = True
                    if "thread_source" in common_columns and not data.get("thread_source"):
                        data["thread_source"] = "user"
                    if "archived" in common_columns:
                        data["archived"] = 0
                    if "archived_at" in common_columns:
                        data["archived_at"] = None
                    if "recency_at" in common_columns:
                        data["recency_at"] = parse_epoch(data.get("updated_at")) or parse_epoch(data.get("recency_at")) or int(time.time())
                    if "recency_at_ms" in common_columns:
                        data["recency_at_ms"] = parse_epoch(data.get("updated_at_ms")) or (
                            (parse_epoch(data.get("recency_at")) or int(time.time())) * 1000
                        )

                    insert_columns = ["id", *common_columns]
                    values = [data.get(name) for name in insert_columns]
                    placeholders = ", ".join("?" for _ in insert_columns)
                    update_assignments = ", ".join(f"{name} = excluded.{name}" for name in common_columns)
                    real_conn.execute(
                        (
                            f"INSERT INTO threads ({', '.join(insert_columns)}) VALUES ({placeholders}) "
                            f"ON CONFLICT(id) DO UPDATE SET {update_assignments}"
                        ),
                        values,
                    )
                    real_conn.commit()
                    promotion.state_updated = True
        except sqlite3.Error as exc:
            append_promotion_error(promotion, f"sqlite import failed: {exc}")
        finally:
            try:
                if real_conn is not None:
                    real_conn.close()
                if source_conn is not None:
                    source_conn.close()
            finally:
                _CODEX_SQLITE_LOCK.release()
    elif source_db.exists() and not real_db.exists():
        append_promotion_error(promotion, f"real state_5.sqlite not found: {real_db}")

    source_rollout = Path(source_rollout_text).expanduser() if source_rollout_text else find_rollout_path_for_session(source_codex_home, session_id)
    if source_rollout is not None:
        promotion.rollout_path = str(imported_rollout_path or rollout_destination_for_import(source_rollout, source_codex_home, real_codex_home))
        if source_rollout.exists():
            promotion.rollout_found = True
            try:
                if native_history is not None:
                    promotion.rollout_updated = True
                else:
                    copy_file_atomic(source_rollout, Path(promotion.rollout_path))
                    promotion.rollout_updated, rollout_source_promoted = update_rollout_session_meta(
                        Path(promotion.rollout_path), session_id, workspace, real_codex_home,
                    )
                    promotion.source_promoted = promotion.source_promoted or rollout_source_promoted
            except Exception as exc:  # noqa: BLE001
                append_promotion_error(promotion, f"rollout import failed: {exc}")
        else:
            append_promotion_error(promotion, f"isolated rollout file not found: {source_rollout}")

    if not promotion.state_found and not promotion.rollout_found and not promotion.error:
        append_promotion_error(promotion, f"isolated session metadata not found: {session_id}")

    return promotion


def infer_codex_thread_id_for_result(result: AgentResult, codex_home: Path) -> Optional[str]:
    if result.codex_thread_id:
        return result.codex_thread_id

    workspace = Path(result.workspace_dir)
    state = query_resume_sessions_from_state(
        codex_home,
        workspace,
        include_non_interactive=True,
    )
    sessions = state.sessions
    if not state.available:
        sessions = load_resume_sessions_from_jsonl(codex_home, workspace, include_non_interactive=True)
    if not sessions:
        return None
    return sessions[0].session_id


def promote_best_codex_session_to_workspace(best: AgentResult, workspace: Path) -> Optional[CodexSessionPromotion]:
    real_codex_home = get_codex_home()
    source_codex_home = Path(best.codex_home).expanduser().resolve() if best.codex_home else real_codex_home
    session_id = infer_codex_thread_id_for_result(best, source_codex_home)
    if not session_id:
        return None

    best.codex_thread_id = session_id
    try:
        record_session_workspace_mapping(
            real_codex_home,
            session_id,
            best.workspace_dir,
            workspace,
        )
    except OSError as exc:
        logger.warning(
            "could not persist accepted workspace link mapping for session {}: {}",
            session_id,
            exc,
        )
    if source_codex_home != real_codex_home:
        return import_codex_session_to_workspace(real_codex_home, source_codex_home, session_id, workspace)
    return promote_codex_session_to_workspace(real_codex_home, session_id, workspace)


# -----------------------------------------------------------------------------
# Reasoning-token parsing
# -----------------------------------------------------------------------------


def extract_reasoning_tokens_from_json(obj: Any) -> List[int]:
    values: List[int] = []

    def visit(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                key = str(k)
                if key in {"reasoning_output_tokens", "reasoning_tokens"}:
                    if isinstance(v, int):
                        values.append(v)
                    elif isinstance(v, float) and v.is_integer():
                        values.append(int(v))
                    elif isinstance(v, str) and v.isdigit():
                        values.append(int(v))
                visit(v)
        elif isinstance(x, list):
            for v in x:
                visit(v)

    visit(obj)
    return values


def extract_codex_thread_id_from_json(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "thread.started":
        value = obj.get("thread_id") or obj.get("session_id") or obj.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()

    payload = obj.get("payload")
    if isinstance(payload, dict) and obj.get("type") == "session_meta":
        value = payload.get("session_id") or payload.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def compact_token_values(values: Iterable[int], limit: int = 16) -> List[int]:
    seen: List[int] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    if len(seen) <= limit:
        return seen
    # Preserve early structure and final maximum/cumulative tail.
    return seen[: limit - 1] + [seen[-1]]


def extract_reasoning_total_from_rollout_event(obj: Any) -> Optional[int]:
    if not isinstance(obj, dict) or obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("reasoning_output_tokens")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def iter_jsonl_lines_reverse(
    path: Path,
    end_offset: Optional[int] = None,
    chunk_size: int = 65536,
) -> Iterator[bytes]:
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            position = file.tell()
            if end_offset is not None:
                position = min(position, max(0, end_offset))
            remainder = b""
            while position > 0:
                read_size = min(chunk_size, position)
                position -= read_size
                file.seek(position)
                parts = (file.read(read_size) + remainder).split(b"\n")
                remainder = parts[0]
                for line in reversed(parts[1:]):
                    if line:
                        yield line
            if remainder:
                yield remainder
    except OSError:
        return


def last_reasoning_total_from_rollout(path: Path, end_offset: Optional[int] = None) -> Optional[int]:
    for raw_line in iter_jsonl_lines_reverse(path, end_offset=end_offset):
        if b'"token_count"' not in raw_line or b'"reasoning_output_tokens"' not in raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        total = extract_reasoning_total_from_rollout_event(obj)
        if total is not None:
            return total
    return None


def reasoning_token_progress_event(state: AgentState) -> Dict[str, Any]:
    return {
        "type": "agent_tokens",
        "idx": state.idx,
        "reasoning_tokens": state.reasoning_tokens,
        "reasoning_token_counts": dict(sorted(state.reasoning_token_counts.items())),
    }


class ReasoningRolloutMonitor:
    """Incrementally tails one isolated Codex rollout without rescanning its history."""

    def __init__(
        self,
        codex_home: Path,
        state: AgentState,
        progress_callback: ProgressCallback = None,
    ) -> None:
        self.codex_home = codex_home
        self.state = state
        self.progress_callback = progress_callback
        self.path: Optional[Path] = None
        self.offset = 0
        self._thread_path_checked_for: Optional[str] = None
        self._initial_sizes = self._snapshot_rollouts()
        if self._initial_sizes:
            newest = max(self._initial_sizes, key=self._rollout_mtime)
            self._attach(newest)

    @property
    def sessions_root(self) -> Path:
        return self.codex_home / "sessions"

    def _rollout_mtime(self, path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _snapshot_rollouts(self) -> Dict[Path, int]:
        if not self.sessions_root.exists():
            return {}
        snapshot: Dict[Path, int] = {}
        try:
            paths = self.sessions_root.rglob("*.jsonl")
            for path in paths:
                try:
                    snapshot[path.resolve()] = path.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        return snapshot

    def _attach(self, path: Path) -> None:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path
        initial_size = self._initial_sizes.get(normalized)
        self.path = normalized
        if initial_size is None:
            self.offset = 0
            self.state.seed_reasoning_total(0)
            return
        self.offset = initial_size
        baseline = last_reasoning_total_from_rollout(normalized, end_offset=initial_size)
        self.state.seed_reasoning_total(baseline if baseline is not None else 0)

    def _discover_rollout(self) -> Optional[Path]:
        if self.state.codex_thread_id:
            path = find_rollout_path_for_session(self.codex_home, self.state.codex_thread_id)
            if path is not None:
                return path
        if not self.sessions_root.exists():
            return None
        candidates: List[Path] = []
        try:
            for path in self.sessions_root.rglob("*.jsonl"):
                try:
                    path.stat()
                except OSError:
                    continue
                candidates.append(path)
        except OSError:
            return None
        return max(candidates, key=self._rollout_mtime) if candidates else None

    def _ensure_rollout(self) -> None:
        thread_id = self.state.codex_thread_id
        if thread_id and thread_id != self._thread_path_checked_for:
            self._thread_path_checked_for = thread_id
            path = find_rollout_path_for_session(self.codex_home, thread_id)
            if path is not None:
                try:
                    normalized = path.resolve()
                except OSError:
                    normalized = path
                if normalized != self.path:
                    self._attach(normalized)
                    return
        if self.path is None or not self.path.exists():
            path = self._discover_rollout()
            if path is not None:
                self._attach(path)

    def poll(self, final: bool = False) -> bool:
        self._ensure_rollout()
        if self.path is None:
            return False
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.state.seed_reasoning_total(0)
            if size <= self.offset:
                return False
            with self.path.open("rb") as file:
                file.seek(self.offset)
                appended = file.read(size - self.offset)
        except OSError:
            return False

        if final:
            consumed = appended
        else:
            newline = appended.rfind(b"\n")
            if newline < 0:
                return False
            consumed = appended[: newline + 1]
        self.offset += len(consumed)

        changed = False
        for raw_line in consumed.splitlines():
            if (
                b'"token_count"' not in raw_line
                or b'"total_token_usage"' not in raw_line
                or b'"reasoning_output_tokens"' not in raw_line
            ):
                continue
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            total = extract_reasoning_total_from_rollout_event(obj)
            if total is not None:
                changed = self.state.observe_reasoning_total(total) or changed

        if changed and self.progress_callback is not None:
            self.progress_callback(reasoning_token_progress_event(self.state))
        if changed:
            self.state.last_meaningful_activity_at = time.monotonic()
        return changed


def agent_line_for_progress(text: str, obj: Any = None) -> str:
    if obj is None:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return text
    if not isinstance(obj, dict):
        return text

    item = obj.get("item")
    if not isinstance(item, dict):
        payload = obj.get("payload")
        if isinstance(payload, dict):
            item = payload.get("item")
    if isinstance(item, dict) and item.get("type") in {"command_execution", "file_change", "tool_call", "web_search"}:
        for key in ("aggregated_output", "aggregatedOutput", "output_delta", "output", "stdout", "stderr", "arguments", "result", "results"):
            item.pop(key, None)
        for change in item.get("changes", []):
            if isinstance(change, dict):
                change.pop("diff", None)
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text


def is_orphan_custom_tool_output_diagnostic(text: str) -> bool:
    return ORPHAN_CUSTOM_TOOL_OUTPUT_MARKER in text.casefold()


def is_models_manager_timeout_diagnostic(text: str) -> bool:
    normalized = text.casefold()
    return (
        CODEX_MODELS_MANAGER_TIMEOUT_MARKER in normalized
        and "timeout waiting for child process to exit" in normalized
    )


async def stream_to_log(
    reader: Optional[asyncio.StreamReader],
    log_path: Path,
    state: AgentState,
    stream_name: str,
    progress_callback: ProgressCallback = None,
) -> None:
    if reader is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_store = OperationOutputStore(log_path.parent, state.operation_output_sizes, state.operation_output_streams)
    with log_path.open("ab") as f:
        async for line in iter_stream_lines(reader):
            log_offset = f.tell()
            f.write(line)
            f.flush()

            if stream_name == "stdout":
                state.stdout_lines += 1
            else:
                state.stderr_lines += 1

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            observed_at = time.monotonic()
            obj: Any = None
            is_json = False
            try:
                obj = json.loads(text)
                is_json = True
            except json.JSONDecodeError:
                pass
            if stream_name == "stdout" and isinstance(obj, dict):
                previous_phase = state.phase
                if obj.get("type") == "pcr.recovery":
                    state.transition("resuming", observed_at)
                    if progress_callback is not None:
                        progress_callback({"type": "agent_phase", "idx": state.idx, "phase": "resuming",
                                           "phase_timings": dict(state.phase_timings)})
                        progress_callback({"type": "agent_status", "idx": state.idx, "status": "resuming",
                                           "recovery": obj})
                    continue
                if obj.get("type") == "turn.completed":
                    state.native_error = None
                elif obj.get("type") in {"turn.failed", "error"} and isinstance(obj.get("error"), dict):
                    state.native_error = obj["error"]
                if obj.get("type") == "pcr.phase":
                    phase = str(obj.get("phase") or "")
                    if phase and not state.turn_started:
                        state.transition(phase, observed_at)
                        if progress_callback is not None:
                            progress_callback({"type": "agent_phase", "idx": state.idx, "phase": phase,
                                               "phase_timings": dict(state.phase_timings)})
                            progress_callback({"type": "agent_status", "idx": state.idx,
                                               "status": "resuming" if phase in {"resuming", "resume_slot"} else "submitting" if phase == "submitting" else "starting"})
                    continue
                kind = obj.get("type")
                accepted = kind == "turn.started" or (kind in {"item.started", "item.completed", "item.updated"} and isinstance(obj.get("item"), dict))
                if accepted and not state.turn_started:
                    state.turn_started = True
                    state.transition("waiting_for_output", observed_at)
                    if progress_callback is not None:
                        progress_callback({"type": "agent_started", "idx": state.idx})
                        progress_callback({"type": "agent_status", "idx": state.idx, "status": "waiting_for_output"})
                if state.turn_started and kind in {"item.started", "item.completed", "item.updated"}:
                    state.transition("working", observed_at)
                if progress_callback is not None and accepted and previous_phase != state.phase:
                    progress_callback({"type": "agent_phase", "idx": state.idx, "phase": state.phase,
                                       "phase_timings": dict(state.phase_timings)})
                    if state.phase == "working":
                        progress_callback({"type": "agent_status", "idx": state.idx, "status": "running"})
            if stream_name == "stdout":
                state.meaningful_stdout_lines += 1
                state.last_meaningful_activity_at = observed_at
                if state.models_refresh_status_reported and state.phase == "working":
                    state.models_refresh_status_reported = False
                    if progress_callback is not None:
                        progress_callback(
                            {
                                "type": "agent_status",
                                "idx": state.idx,
                                "status": "running",
                            }
                        )
            elif is_models_manager_timeout_diagnostic(text):
                state.models_manager_timeouts += 1
                state.last_models_manager_timeout_at = observed_at
                if (
                    progress_callback is not None
                    and not state.turn_started
                    and not state.models_refresh_status_reported
                ):
                    state.models_refresh_status_reported = True
                    progress_callback(
                        {
                            "type": "agent_status",
                            "idx": state.idx,
                            "status": "waiting_for_models",
                        }
                    )
            elif is_orphan_custom_tool_output_diagnostic(text):
                state.orphan_output_diagnostics += 1
            if progress_callback is not None and not (
                stream_name == "stderr"
                and is_orphan_custom_tool_output_diagnostic(text)
            ):
                operation = state.work_operations.observe(obj, observed_at) if is_json else None
                output_unavailable = False
                if operation is not None and stream_name == "stdout":
                    try:
                        if operation["id"] in state.operation_output_errors:
                            raise OSError("Operation output indexing is unavailable")
                        output_size = output_store.observe(obj.get("item"), operation)
                    except OSError:
                        # Optional output indexing cannot fail or interrupt the Agent.
                        # The original native chunk remains in stdout.log.
                        output_size = None
                        output_unavailable = True
                        state.operation_output_errors.add(operation["id"])
                    if output_size is not None:
                        operation["output_bytes"] = output_size
                progress_text = agent_line_for_progress(text, obj) if is_json else text
                progress_event = {"type": "agent_line", "idx": state.idx, "stream": stream_name, "text": progress_text}
                item = obj.get("item") if isinstance(obj, dict) else None
                if isinstance(item, dict) and item.get("type") in {"agent_message", "reasoning"} and item.get("id"):
                    progress_event["pcr_text_item_id"] = f"{state.work_operations.execution_id}:{item['id']}"
                if operation is not None:
                    if output_unavailable:
                        progress_event["pcr_output_unavailable"] = True
                    if operation.get("output_bytes") is not None:
                        progress_event["pcr_output_bytes"] = operation["output_bytes"]
                    progress_event.update({
                        "pcr_work_operation": operation,
                        "pcr_log_offset": log_offset,
                        "pcr_log_length": len(line),
                        "pcr_log_sha256": hashlib.sha256(line).hexdigest(),
                    })
                progress_callback(progress_event)
            if not is_json:
                continue
            state.json_events += 1
            thread_id = extract_codex_thread_id_from_json(obj)
            if thread_id and not state.codex_thread_id:
                state.codex_thread_id = thread_id
            vals = extract_reasoning_tokens_from_json(obj)
            if vals:
                for value in vals:
                    state.record_reasoning_total(value)
                if progress_callback is not None:
                    progress_callback(reasoning_token_progress_event(state))


async def iter_stream_lines(reader: asyncio.StreamReader, chunk_size: int = 65536) -> AsyncIterator[bytes]:
    pending = b""
    while True:
        chunk = await reader.read(chunk_size)
        if not chunk:
            if pending:
                yield pending
            return
        pending += chunk
        while True:
            line, sep, rest = pending.partition(b"\n")
            if not sep:
                pending = line
                break
            yield line + sep
            pending = rest


async def terminate_process(proc: Optional[asyncio.subprocess.Process], timeout: float = 2.0) -> Optional[int]:
    if proc is None:
        return None

    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return proc.returncode
        try:
            returncode = proc.returncode
            if returncode is None:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            returncode = None
        try:
            # The group leader may exit before descendants which still hold the
            # stdout/stderr pipes open. Force the rest of the group down too.
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return await proc.wait() if returncode is None else returncode

    if proc.returncode is not None:
        return proc.returncode
    try:
        proc.terminate()
    except ProcessLookupError:
        return proc.returncode
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return proc.returncode
        return await proc.wait()


# -----------------------------------------------------------------------------
# Agent execution
# -----------------------------------------------------------------------------


def agent_process_environment(
    agent_workspace: Path,
    codex_home: Path,
    executable: str = "codex",
) -> Dict[str, str]:
    """Build an Agent environment without pointers to the source checkout."""
    env = os.environ.copy()
    for key in AGENT_ENV_PATH_KEYS_TO_CLEAR:
        env.pop(key, None)
    env["PWD"] = str(agent_workspace.expanduser().resolve())
    from .codex_auth import SOURCE_ENV, selected_home

    env[SOURCE_ENV] = str(selected_home())
    env["CODEX_HOME"] = str(codex_home.expanduser().resolve())
    env["PCR_CODEX_RESUME_LOCK"] = str(codex_home.expanduser().resolve().parent.parent / "codex-resume.lock")
    return codex_process_environment(executable, env)


def retained_agent_execution(
    meta_dir: Path, command: List[str], workspace: Path, codex_home: Path,
    prompt: str, instructions: Optional[str],
) -> bool:
    """A dead adapter may resume its receipt, never replay an unjournaled task."""
    from .plugin.lifecycle import pid_is_alive
    from .codex_app_server import retryable_blocked_execution

    try:
        value = json.loads((meta_dir / "execution_recovery.json").read_text(encoding="utf-8"))
        binding = [command, str(workspace.resolve()), str(codex_home.resolve()), prompt, instructions]
        identity = hashlib.sha256(json.dumps(binding, ensure_ascii=False).encode()).hexdigest()
        return (
            isinstance(value, dict) and value.get("version") == 1 and value.get("identity") == identity
            and (value.get("stage") in {"submitting", "accepted", "continuation_ready", "completed"}
                 or retryable_blocked_execution(value, codex_home))
            and isinstance(value.get("native_pid"), int) and not isinstance(value["native_pid"], bool)
            and value["native_pid"] > 0
            and not pid_is_alive(value["native_pid"])
            and not (meta_dir / "execution_stop").exists()
        )
    except (OSError, ValueError, TypeError):
        return False


async def run_one_agent(
    idx: int,
    agent_workspace: Path,
    meta_dir: Path,
    codex_home: Path,
    prompt: str,
    command: List[str],
    progress_callback: ProgressCallback = None,
    cancel_event: Any = None,
    agent_cancel_event: Any = None,
    agent_role: str = AGENT_ROLE_CANDIDATE,
) -> AgentResult:
    agent_role = normalize_agent_role(agent_role)
    if command:
        command = [resolve_codex_executable(command[0]), *command[1:]]
    meta_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = meta_dir / "stdout.log"
    stderr_log = meta_dir / "stderr.log"
    final_message = meta_dir / "final_message.md"
    command_json = meta_dir / "command.json"
    status_json = meta_dir / "status.json"

    command_json.write_text(
        json.dumps(
            {
                "idx": idx,
                "role": agent_role,
                "cwd": str(agent_workspace),
                "codex_home": str(codex_home),
                "command": command,
                "prompt_transport": "stdin",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state = AgentState(idx=idx)
    try:
        state.phase_timings.update(json.loads((meta_dir / "preparation_timings.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    state.last_meaningful_activity_at = time.monotonic()
    rollout_monitor = ReasoningRolloutMonitor(codex_home, state, progress_callback)
    started = time.perf_counter()
    returncode: Optional[int] = None
    status = "failed"
    status_decided = False
    error: Optional[str] = None
    proc: Optional[asyncio.subprocess.Process] = None
    stdout_task: Optional[asyncio.Task[None]] = None
    stderr_task: Optional[asyncio.Task[None]] = None

    try:
        requested_status = requested_agent_stop_status(cancel_event, agent_cancel_event)
        if requested_status is not None:
            status = requested_status
            (meta_dir / "execution_stop").write_text(requested_status, encoding="utf-8")
            raise RuntimeError("__pcr_stopped_before_start__")
        env = agent_process_environment(agent_workspace, codex_home, command[0])
        env["PCR_AGENT_STOP_FILES"] = json.dumps([str(signal.path) for signal in (cancel_event, agent_cancel_event)
                                                 if isinstance(getattr(signal, "path", None), Path)])
        process_options: Dict[str, Any] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True
        from .codex_app_server import RECOVERY_MIN_DELAY, RECOVERY_MAX_DELAY, adapted_command, uses_app_server_transport

        instruction_path = meta_dir / "developer_instructions.json"
        additional_instructions = None
        if uses_app_server_transport(command[0]) or instruction_path.exists():
            try:
                additional_instructions = json.loads(instruction_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "Required prepared PCR developer instructions are unavailable or invalid. "
                    "No input was submitted."
                ) from exc
            if not isinstance(additional_instructions, str) or not additional_instructions.strip():
                raise ValueError("Invalid prepared PCR developer instructions. No input was submitted.")
            recorded = json.loads(command_json.read_text(encoding="utf-8"))
            recorded["additional_developer_instructions"] = additional_instructions
            command_json.write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8")

        execution_command = adapted_command(command, additional_developer_instructions=additional_instructions)

        async def start_process() -> None:
            nonlocal proc, stdout_task, stderr_task
            proc = await asyncio.create_subprocess_exec(
                *execution_command, cwd=str(agent_workspace), env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **process_options,
            )
            if progress_callback is not None:
                progress_callback({"type": "agent_process_started", "idx": idx, "role": agent_role, "pid": proc.pid})
            stdout_task = asyncio.create_task(stream_to_log(proc.stdout, stdout_log, state, "stdout", progress_callback))
            stderr_task = asyncio.create_task(stream_to_log(proc.stderr, stderr_log, state, "stderr", progress_callback))
            assert proc.stdin is not None
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

        await start_process()

        next_rollout_poll = 0.0
        adapter_restarts = 0
        while True:
            now = time.monotonic()
            if now >= next_rollout_poll:
                rollout_monitor.poll()
                next_rollout_poll = now + 0.5
            requested_status = requested_agent_stop_status(cancel_event, agent_cancel_event)
            if requested_status is not None:
                status = requested_status
                status_decided = True
                (meta_dir / "execution_stop").write_text(requested_status, encoding="utf-8")
                returncode = await terminate_process(proc)
                break
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=0.2)
                await asyncio.gather(stdout_task, stderr_task)
                if returncode and retained_agent_execution(meta_dir, command, agent_workspace, codex_home, prompt,
                                                          additional_instructions):
                    state.transition("resuming")
                    if progress_callback is not None:
                        progress_callback({"type": "agent_status", "idx": idx, "status": "resuming"})
                    adapter_restarts += 1
                    delay = min(RECOVERY_MAX_DELAY, RECOVERY_MIN_DELAY * 2 ** min(adapter_restarts - 1, 10))
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline:
                        if requested_agent_stop_status(cancel_event, agent_cancel_event) is not None:
                            break
                        await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))
                    requested_status = requested_agent_stop_status(cancel_event, agent_cancel_event)
                    if requested_status is not None:
                        status, status_decided = requested_status, True
                        (meta_dir / "execution_stop").write_text(requested_status, encoding="utf-8")
                        break
                    await start_process()
                    continue
                break
            except asyncio.TimeoutError:
                continue
        await asyncio.gather(stdout_task, stderr_task)
        if not status_decided:
            status = requested_agent_stop_status(cancel_event, agent_cancel_event) or (
                "success" if returncode == 0 else "failed"
            )
    except asyncio.CancelledError:
        status = "cancelled"
        error = None
        returncode = await terminate_process(proc)
    except Exception as exc:  # noqa: BLE001
        if status in {"cancelled", "killed"}:
            error = None
        else:
            returncode = await terminate_process(proc)
            error = repr(exc)
            status = "error"
            logger.error("agent_{:03d} error: {}", idx, error)
    finally:
        tasks = [task for task in (stdout_task, stderr_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        rollout_monitor.poll(final=True)
        state.transition("cleanup")
        if state.codex_thread_id and rollout_monitor.path is not None:
            try:
                baseline_calls = None
                try:
                    prepared = json.loads((meta_dir / "resume_preparation.json").read_text(encoding="utf-8"))
                    if "active_custom_calls" in prepared:
                        baseline_calls = set(prepared["active_custom_calls"])
                except (OSError, ValueError):
                    pass
                removed_orphans = sanitize_rollout_orphan_custom_tool_outputs(
                    rollout_monitor.path,
                    start_offset=rollout_monitor._initial_sizes.get(rollout_monitor.path, 0),
                    baseline_calls=baseline_calls,
                )
                if removed_orphans:
                    logger.debug(
                        "agent_{:03d} removed {} orphan custom tool outputs "
                        "from resumable history",
                        idx,
                        removed_orphans,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "agent_{:03d} could not sanitize custom tool history: {}",
                    idx,
                    exc,
                )
        scrub_codex_home_support_entries(codex_home)
        state.transition("finished")

    seconds = time.perf_counter() - started
    result = AgentResult(
        idx=idx,
        workspace_dir=str(agent_workspace),
        meta_dir=str(meta_dir),
        codex_home=str(codex_home),
        stdout_log=str(stdout_log),
        stderr_log=str(stderr_log),
        final_message=str(final_message),
        command=command,
        returncode=returncode,
        status=status,
        seconds=seconds,
        phase_timings=state.phase_timings,
        role=agent_role,
        codex_thread_id=state.codex_thread_id,
        reasoning_tokens=state.reasoning_tokens,
        reasoning_token_values=compact_token_values(state.reasoning_values),
        reasoning_token_counts=dict(sorted(state.reasoning_token_counts.items())),
        error=error or (str(state.native_error.get("message") or "Codex execution failed")
                       if status in {"failed", "error"} and state.native_error else None),
        native_error=state.native_error,
        stdout_tail=safe_tail(stdout_log),
        stderr_tail=safe_tail(stderr_log),
    )
    status_json.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_callback is not None:
        for operation in state.work_operations.finish(status):
            text = operation.get("command") or operation.get("query") or "\n".join(
                target["path"] for target in operation.get("targets", [])
            ) or "Tool"
            progress_callback({
                "type": "agent_line", "idx": idx, "stream": "stdout", "text": text,
                "pcr_transcript_kind": "command" if operation.get("command") else "activity",
                "pcr_transcript_text": text, "pcr_transcript_status": operation["status"],
                "pcr_work_operation": operation,
            })
        progress_callback(
            {
                "type": "agent_finished",
                "idx": idx,
                "role": agent_role,
                "result": asdict(result),
            }
        )
    return result


async def run_all_agents(
    n: int,
    workspaces_root: Path,
    meta_root: Path,
    prompt: str,
    command_by_agent: Dict[int, List[str]],
    codex_home_by_agent: Dict[int, Path],
    max_parallel: int,
    progress_callback: ProgressCallback = None,
    cancel_event: Any = None,
    agent_cancel_events: Optional[Dict[int, Any]] = None,
    agent_indices: Optional[Sequence[int]] = None,
    agent_role: str = AGENT_ROLE_CANDIDATE,
    live_candidate_coordinator: Optional[LiveCandidateCoordinator] = None,
    live_agent_preparer: Optional[
        Callable[[int, bool], Tuple[List[str], Path]]
    ] = None,
    agent_start_preparer: Optional[Callable[[Path], bool]] = None,
    source_workspace: Optional[Path] = None,
    initial_agent_preparer: Optional[Callable[[int], Tuple[List[str], Path]]] = None,
) -> List[AgentResult]:
    agent_role = normalize_agent_role(agent_role)
    results: List[AgentResult] = []
    semaphore = asyncio.Semaphore(max_parallel)
    indices = list(agent_indices) if agent_indices is not None else list(range(1, n + 1))
    if live_candidate_coordinator is not None and live_agent_preparer is None:
        raise ValueError("live_agent_preparer is required with live_candidate_coordinator")

    def preparation_failure(
        idx: int,
        command: List[str],
        codex_home: Path,
        exc: Exception,
    ) -> AgentResult:
        agent_workspace = agent_execution_workspace(
            source_workspace or workspaces_root, workspaces_root, idx,
            "source" if source_workspace is not None else "isolated",
        )
        meta_dir = meta_root / f"agent_{idx:03d}"
        stopped = requested_agent_stop_status(cancel_event, (agent_cancel_events or {}).get(idx))
        result = AgentResult(
            idx=idx,
            workspace_dir=str(agent_workspace),
            meta_dir=str(meta_dir),
            codex_home=str(codex_home),
            stdout_log=str(meta_dir / "stdout.log"),
            stderr_log=str(meta_dir / "stderr.log"),
            final_message=str(meta_dir / "final_message.md"),
            command=command,
            returncode=None,
            status=stopped or "error",
            seconds=0.0,
            role=agent_role,
            error=None if stopped else f"{agent_role} preparation failed: {exc}",
        )
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "status.json").write_text(
                json.dumps(asdict(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "agent_finished",
                    "idx": idx,
                    "role": agent_role,
                    "result": asdict(result),
                }
            )
        return result

    async def prepare_in_thread(function: Callable, *arguments: Any) -> Any:
        # A cancelled coroutine must join its copy before callers scrub or delete
        # the same files. Cancelling to_thread alone leaves the writer running.
        task = asyncio.create_task(asyncio.to_thread(function, *arguments))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise

    async def run_limited(
        idx: int,
        *,
        prepare: bool = False,
        retry: bool = False,
    ) -> AgentResult:
        agent_cancel_event = (agent_cancel_events or {}).get(idx)

        async def execute() -> AgentResult:
            command = command_by_agent.get(idx, [])
            codex_home = codex_home_by_agent.get(
                idx,
                meta_root / f"agent_{idx:03d}" / "codex_home",
            )
            if requested_agent_stop_status(cancel_event, agent_cancel_event) is None:
                try:
                    if prepare:
                        assert live_agent_preparer is not None
                        command, codex_home = await prepare_in_thread(live_agent_preparer, idx, retry)
                    elif initial_agent_preparer is not None:
                        command, codex_home = await prepare_in_thread(initial_agent_preparer, idx)
                    command_by_agent[idx] = command
                    codex_home_by_agent[idx] = codex_home
                    if agent_start_preparer is not None and requested_agent_stop_status(
                        cancel_event, agent_cancel_event
                    ) is None:
                        await prepare_in_thread(agent_start_preparer, codex_home)
                except Exception as exc:  # noqa: BLE001
                    return preparation_failure(idx, command, codex_home, exc)
            return await run_one_agent(
                idx=idx,
                agent_workspace=agent_execution_workspace(
                    source_workspace or workspaces_root, workspaces_root, idx,
                    "source" if source_workspace is not None else "isolated",
                ),
                meta_dir=meta_root / f"agent_{idx:03d}",
                codex_home=codex_home,
                prompt=prompt,
                command=command,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_event=agent_cancel_event,
                agent_role=agent_role,
            )

        if progress_callback is not None:
            progress_callback(
                {
                    "type": "agent_status",
                    "idx": idx,
                    "status": "queued",
                    "role": agent_role,
                }
            )
        while True:
            if cancel_requested(cancel_event):
                return await execute()
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
                break
            except asyncio.TimeoutError:
                continue
        try:
            # Normal /kill requests made before a process starts are stale and
            # must not suppress queued Agents. Stage cancellation is explicit.
            preserve_prestart_cancel = bool(
                getattr(agent_cancel_event, "_pcr_cancel_before_start", False)
            )
            if retry or not preserve_prestart_cancel:
                clear_agent_cancel = getattr(agent_cancel_event, "clear", None)
                if callable(clear_agent_cancel):
                    clear_agent_cancel()
                if preserve_prestart_cancel:
                    delattr(agent_cancel_event, "_pcr_cancel_before_start")
            return await execute()
        finally:
            semaphore.release()

    tasks = [asyncio.create_task(run_limited(idx)) for idx in indices]
    total = len(tasks)

    if live_candidate_coordinator is not None:
        active_tasks = set(tasks)
        results_by_index: Dict[int, AgentResult] = {}
        live_candidate_coordinator.activate()
        try:
            while True:
                for request in live_candidate_coordinator.drain():
                    task = asyncio.create_task(
                        run_limited(request.idx, prepare=True, retry=request.retry)
                    )
                    tasks.append(task)
                    active_tasks.add(task)
                if not active_tasks:
                    trailing = live_candidate_coordinator.close_if_idle()
                    if trailing is None:
                        break
                    for request in trailing:
                        task = asyncio.create_task(
                            run_limited(request.idx, prepare=True, retry=request.retry)
                        )
                        tasks.append(task)
                        active_tasks.add(task)
                    continue

                finished, active_tasks = await asyncio.wait(
                    active_tasks,
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in finished:
                    result = await task
                    results_by_index[result.idx] = result
        finally:
            live_candidate_coordinator.deactivate()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return list(results_by_index.values())

    try:
        if HAS_RICH and progress_callback is None:
            assert Progress is not None
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold]{task.description}[/bold]"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task_id = progress.add_task(f"codex agents max_parallel={max_parallel}", total=total)
                for finished in asyncio.as_completed(tasks):
                    res = await finished
                    results.append(res)
                    token_text = "NA" if res.reasoning_tokens is None else str(res.reasoning_tokens)
                    progress.update(
                        task_id,
                        advance=1,
                        description=f"agent_{res.idx:03d} {res.status} rtok={token_text}",
                    )
        elif HAS_TQDM and progress_callback is None:
            with tqdm(total=total, desc="codex agents", unit="agent") as bar:
                for finished in asyncio.as_completed(tasks):
                    res = await finished
                    results.append(res)
                    token_text = "NA" if res.reasoning_tokens is None else str(res.reasoning_tokens)
                    bar.set_postfix_str(f"last=agent_{res.idx:03d} {res.status} rtok={token_text}")
                    bar.update(1)
        else:
            for finished in asyncio.as_completed(tasks):
                res = await finished
                results.append(res)
                if progress_callback is None:
                    logger.info(
                        "completed {}/{}: agent_{:03d} {} {:.2f}s",
                        len(results),
                        total,
                        res.idx,
                        res.status,
                        res.seconds,
                    )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return results


# -----------------------------------------------------------------------------
# Best-result selection
# -----------------------------------------------------------------------------


RECOMMEND_BY_ALIASES = {
    "duration": "duration",
    "seconds": "duration",
    "time": "duration",
    "longest": "duration",
    "reasoning_tokens": "reasoning_tokens",
    "reasoning_token": "reasoning_tokens",
    "reasoning-tokens": "reasoning_tokens",
    "reasoning-token": "reasoning_tokens",
    "resoning_tokens": "reasoning_tokens",
    "resoning-token": "reasoning_tokens",
    "resoning-tokens": "reasoning_tokens",
    "reasoning": "reasoning_tokens",
    "rtok": "reasoning_tokens",
    "tokens": "reasoning_tokens",
}


def normalize_recommend_by(value: str) -> str:
    key = value.strip().lower()
    try:
        return RECOMMEND_BY_ALIASES[key]
    except KeyError as exc:
        choices = ", ".join(sorted(RECOMMEND_BY_ALIASES))
        raise argparse.ArgumentTypeError(f"不支持的候选选择策略：{value!r}。可用值：{choices}") from exc


def reasoning_score(result: AgentResult) -> int:
    return result.reasoning_tokens if result.reasoning_tokens is not None else -1


def select_best_result(
    successes: List[AgentResult],
    recommend_by: str,
    *,
    warn_missing_tokens: bool = True,
) -> Optional[AgentResult]:
    candidates = preferred_recommendation_pool(successes)
    if not candidates:
        return None

    if recommend_by == "reasoning_tokens":
        with_tokens = [r for r in candidates if r.reasoning_tokens is not None]
        if not with_tokens:
            if warn_missing_tokens:
                logger.warning(
                    "当前推荐范围内所有成功 agent 的 reasoning_tokens 都是 N/A；"
                    "回退为按最长时长选择。"
                )
            return max(candidates, key=lambda r: (r.seconds, -r.idx))
        return max(candidates, key=lambda r: (reasoning_score(r), r.seconds, -r.idx))

    return max(candidates, key=lambda r: (r.seconds, reasoning_score(r), -r.idx))


def result_sort_key(
    result: AgentResult,
    recommend_by: str,
) -> Tuple[bool, int, float, int, int]:
    role_rank = 0 if result.role == AGENT_ROLE_SYNTHESIS else 1
    if recommend_by == "reasoning_tokens":
        return (
            result.status != "success",
            role_rank,
            -float(reasoning_score(result)),
            -int(result.seconds * 1000),
            result.idx,
        )
    return (
        result.status != "success",
        role_rank,
        -result.seconds,
        -reasoning_score(result),
        result.idx,
    )


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------


def make_summary_table(
    results: List[AgentResult],
    run_root: Path,
    best: Optional[AgentResult],
    recommend_by: str,
) -> Any:
    rows = sorted(results, key=lambda r: result_sort_key(r, recommend_by))
    best_idx = best.idx if best is not None else None
    if HAS_RICH:
        table = Table(
            title=f"Codex parallel run summary (recommend_by={recommend_by})",
            show_header=True,
            header_style="bold cyan",
            show_lines=False,
        )
        table.add_column("rank", justify="right")
        table.add_column("best", justify="center")
        table.add_column("agent", justify="right")
        table.add_column("role")
        table.add_column("status")
        table.add_column("ret", justify="right")
        table.add_column("seconds", justify="right")
        table.add_column("rtok_max", justify="right")
        table.add_column("rtok_values")
        for rank, r in enumerate(rows, 1):
            status_style = "green" if r.status == "success" else "red"
            rtok = "-" if r.reasoning_tokens is None else str(r.reasoning_tokens)
            values = "-" if not r.reasoning_token_values else ",".join(map(str, r.reasoning_token_values))
            table.add_row(
                str(rank),
                "*" if r.idx == best_idx else "",
                f"{r.idx:03d}",
                r.role,
                f"[{status_style}]{r.status}[/{status_style}]",
                "-" if r.returncode is None else str(r.returncode),
                f"{r.seconds:.2f}",
                rtok,
                values,
            )
        return table

    lines = [f"Codex parallel run summary (recommend_by={recommend_by})"]
    lines.append("rank best agent role status ret seconds rtok_max rtok_values workspace")
    for rank, r in enumerate(rows, 1):
        rtok = "-" if r.reasoning_tokens is None else str(r.reasoning_tokens)
        values = "-" if not r.reasoning_token_values else ",".join(map(str, r.reasoning_token_values))
        lines.append(
            f"{rank:>4} {('*' if r.idx == best_idx else ''):^4} {r.idx:>5} {r.role:<9} "
            f"{r.status:<8} {str(r.returncode):>4} "
            f"{r.seconds:>8.2f} {rtok:>8} {values:<24} {absolute_path_for_display(Path(r.workspace_dir))}"
        )
    return "\n".join(lines)


def print_failure_diagnostics(results: List[AgentResult]) -> None:
    failed = [r for r in results if r.status != "success"]
    if not failed:
        return
    longest_failed = max(failed, key=lambda r: r.seconds)
    tail = longest_failed.stderr_tail or longest_failed.stdout_tail
    if not tail:
        return
    stream_name = "stderr" if longest_failed.stderr_tail else "stdout"

    if HAS_RICH:
        assert console is not None
        console.print(
            Panel(
                tail,
                title=f"{stream_name} tail: agent_{longest_failed.idx:03d}",
                border_style="red",
            )
        )
    else:
        print(f"\n--- {stream_name} tail: agent_{longest_failed.idx:03d} ---")
        print(tail)


def print_summary(
    results: List[AgentResult],
    workspace: Path,
    run_root: Path,
    best: Optional[AgentResult],
    recommend_by: str,
    synced: bool,
    codex_session_promotion: Optional[CodexSessionPromotion] = None,
) -> None:
    success_count = sum(1 for r in results if r.status == "success")
    if HAS_RICH:
        assert console is not None
        console.print(make_summary_table(results, run_root, best, recommend_by))
        if best is not None:
            selected = Table.grid(padding=(0, 2))
            selected.add_column(style="bold")
            selected.add_column()
            selected.add_row("runs_root", absolute_path_for_display(run_root))
            selected.add_row("success", f"{success_count}/{len(results)}")
            selected.add_row("RECOMMEND_BY", recommend_by)
            selected.add_row("BEST_AGENT", f"[bold green]agent_{best.idx:03d}[/bold green]")
            selected.add_row("BEST_SECONDS", f"{best.seconds:.2f}")
            selected.add_row("BEST_REASONING_TOKENS", str(best.reasoning_tokens if best.reasoning_tokens is not None else "N/A"))
            selected.add_row("BEST_CODEX_SESSION", best.codex_thread_id or "N/A")
            if codex_session_promotion is not None:
                selected.add_row("CODEX_SESSION_PROMOTED", "YES" if not codex_session_promotion.error else "PARTIAL")
            selected.add_row("FINAL_RESULT_WORKSPACE", absolute_path_for_display(workspace) if synced else "NO")
            selected.add_row("BEST_META", absolute_path_for_display(Path(best.meta_dir)))
            console.print(Panel(selected, title="Selected result", border_style="green"))
        else:
            console.print(
                Panel(
                    f"runs_root = {absolute_path_for_display(run_root)}\n"
                    f"success = 0/{len(results)}\n"
                    f"RECOMMEND_BY = {recommend_by}\n"
                    f"BEST_AGENT = \n"
                    f"NO_SUCCESSFUL_RUN = 1\n"
                    f"workspace was not modified",
                    title="No successful agent",
                    border_style="red",
                )
            )
    else:
        print(make_summary_table(results, run_root, best, recommend_by))
        print(f"runs_root={run_root}")
        print(f"success={success_count}/{len(results)}")
        print(f"RECOMMEND_BY={recommend_by}")
        if best is not None:
            print(f"BEST_AGENT=agent_{best.idx:03d}")
            print(f"BEST_SECONDS={best.seconds:.2f}")
            print(f"BEST_REASONING_TOKENS={best.reasoning_tokens if best.reasoning_tokens is not None else 'N/A'}")
            print(f"BEST_CODEX_SESSION={best.codex_thread_id or 'N/A'}")
            if codex_session_promotion is not None:
                print(f"CODEX_SESSION_PROMOTED={'PARTIAL' if codex_session_promotion.error else 'YES'}")
            print(f"FINAL_RESULT_WORKSPACE={workspace if synced else 'NO'}")
            print(f"BEST_META={best.meta_dir}")
        else:
            print("BEST_AGENT=")
            print("NO_SUCCESSFUL_RUN=1")
            print("workspace was not modified")

    if best is None:
        print_failure_diagnostics(results)


def write_run_files(
    run_root: Path,
    workspace: Path,
    prompt: str,
    results: List[AgentResult],
    best: Optional[AgentResult],
    recommend_by: str,
    synced: bool,
    workspaces_deleted: bool,
    resume_session: Optional[ResumeSession] = None,
    codex_session_promotion: Optional[CodexSessionPromotion] = None,
    synthesis_info: Optional[Dict[str, Any]] = None,
    finalization_error: Optional[str] = None,
    finalization_errors: Optional[Sequence[Dict[str, str]]] = None,
) -> None:
    (run_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    summary = {
        "run_root": str(run_root),
        "workspace": str(workspace),
        "success": sum(1 for r in results if r.status == "success"),
        "total": len(results),
        "recommend_by": recommend_by,
        "best_agent": f"agent_{best.idx:03d}" if best else None,
        "best": asdict(best) if best else None,
        "resume_session": asdict(resume_session) if resume_session else None,
        "codex_session_promotion": asdict(codex_session_promotion) if codex_session_promotion else None,
        "synced_back_to_workspace": str(workspace) if synced else None,
        "workspaces_deleted": workspaces_deleted,
        "finalization_error": finalization_error,
        "results": [asdict(r) for r in sorted(results, key=lambda x: x.idx)],
    }
    if finalization_errors:
        summary["finalization_errors"] = list(finalization_errors)
    if synthesis_info is not None:
        normalized_synthesis = dict(synthesis_info)
        normalized_synthesis["launched_agents"] = [
            result.idx
            for result in sorted(results, key=lambda item: item.idx)
            if result.role == AGENT_ROLE_SYNTHESIS
        ]
        normalized_synthesis["successful_agents"] = [
            result.idx
            for result in sorted(results, key=lambda item: item.idx)
            if result.role == AGENT_ROLE_SYNTHESIS
            and result.status == "success"
        ]
        summary["synthesis"] = normalized_synthesis
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "BEST_AGENT.txt").write_text((f"agent_{best.idx:03d}" if best else "") + "\n", encoding="utf-8")
    (run_root / "FINAL_RESULT_WORKSPACE.txt").write_text((str(workspace) if synced else "") + "\n", encoding="utf-8")
    (run_root / "BEST_CODEX_SESSION.txt").write_text(((best.codex_thread_id or "") if best else "") + "\n", encoding="utf-8")
    if codex_session_promotion is not None:
        (run_root / "codex_session_promotion.json").write_text(
            json.dumps(asdict(codex_session_promotion), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    token_lines = []
    for r in sorted(results, key=lambda x: x.idx):
        increments = ",".join(
            f"{delta}:{count}"
            for delta, count in sorted(r.reasoning_token_counts.items())
        )
        token_lines.append(
            f"agent_{r.idx:03d}\trole={r.role}\tstatus={r.status}\tseconds={r.seconds:.2f}\t"
            f"reasoning_tokens={r.reasoning_tokens if r.reasoning_tokens is not None else 'N/A'}\t"
            f"values={','.join(map(str, r.reasoning_token_values)) if r.reasoning_token_values else 'N/A'}\t"
            f"increments={increments or 'N/A'}"
        )
    (run_root / "reasoning_tokens.tsv").write_text("\n".join(token_lines) + "\n", encoding="utf-8")


def _load_recorded_results(run_root: Path) -> Dict[int, AgentResult]:
    summary_path = run_root / "summary.json"
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    results: Dict[int, AgentResult] = {}
    for value in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(value, dict):
            continue
        try:
            result = AgentResult(**value)
        except (TypeError, ValueError):
            continue
        results[result.idx] = result
    return results


def refresh_run_result_files(
    run_root: Path,
    workspace: Path,
    prompt: str,
    new_results: Sequence[AgentResult],
    recommend_by: str,
    recommendation_excluded_indices: Optional[Set[int]] = None,
    synthesis_source_agents: Optional[Sequence[int]] = None,
    synthesis_requested_agents: Optional[int] = None,
    synthesis_status: Optional[str] = None,
    synthesis_error: Optional[str] = None,
    remove_result_indices: Optional[Set[int]] = None,
) -> None:
    recorded_prompt = prompt
    prompt_path = run_root / "prompt.txt"
    if prompt_path.exists():
        try:
            recorded_prompt = prompt_path.read_text(encoding="utf-8")
        except OSError:
            pass
    recorded = _load_recorded_results(run_root)
    for idx in remove_result_indices or set():
        recorded.pop(int(idx), None)
    for result in new_results:
        recorded[result.idx] = result
    results = list(recorded.values())
    excluded = recommendation_excluded_indices or set()
    best = select_best_result(
        [
            result
            for result in results
            if result.status == "success" and result.idx not in excluded
        ],
        recommend_by,
    )

    summary: Dict[str, Any] = {}
    try:
        value = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            summary = value
    except (OSError, ValueError):
        pass

    resume_session = None
    if isinstance(summary.get("resume_session"), dict):
        try:
            resume_session = ResumeSession(**summary["resume_session"])
        except TypeError:
            resume_session = None
    promotion = None
    if isinstance(summary.get("codex_session_promotion"), dict):
        try:
            promotion = CodexSessionPromotion(**summary["codex_session_promotion"])
        except TypeError:
            promotion = None

    synthesis_info = (
        dict(summary["synthesis"])
        if isinstance(summary.get("synthesis"), dict)
        else None
    )
    if synthesis_source_agents is not None:
        synthesis_info = synthesis_info or {}
        requested_agents = (
            max(0, int(synthesis_requested_agents))
            if synthesis_requested_agents is not None
            else sum(
                result.role == AGENT_ROLE_SYNTHESIS
                for result in new_results
            )
        )
        if requested_agents == 0 and synthesis_requested_agents is None:
            requested_agents = len(remove_result_indices or set()) or int(
                synthesis_info.get("requested_agents", 0) or 0
            )
        synthesis_info.update(
            {
                "requested_agents": requested_agents,
                "source_agents": list(
                    dict.fromkeys(
                        int(idx)
                        for idx in synthesis_source_agents
                        if int(idx) > 0
                    )
                ),
                "context_path": str(run_root / "synthesis_context.md"),
                "instructions_path": str(
                    run_root / "synthesis_instructions.txt"
                ),
                "status": synthesis_status or "completed",
                "reason": None,
                "error": synthesis_error,
            }
        )

    write_run_files(
        run_root=run_root,
        workspace=workspace,
        prompt=recorded_prompt,
        results=results,
        best=best,
        recommend_by=recommend_by,
        synced=bool(summary.get("synced_back_to_workspace")),
        workspaces_deleted=False,
        resume_session=resume_session,
        codex_session_promotion=promotion,
        synthesis_info=synthesis_info,
        finalization_error=(
            str(summary["finalization_error"])
            if summary.get("finalization_error")
            else None
        ),
        finalization_errors=(
            summary.get("finalization_errors")
            if isinstance(summary.get("finalization_errors"), list)
            else None
        ),
    )


def _archive_retry_metadata(run_root: Path, meta_dir: Path, idx: int) -> None:
    if not meta_dir.exists():
        return
    scrub_codex_home_support_entries(meta_dir / "codex_home")
    retry_root = run_root / "retry_history" / f"agent_{idx:03d}"
    retry_root.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.move(str(meta_dir), str(retry_root / stamp))


def retained_resume_preparation(
    run_root: Path, idx: int, workspace: Path, resume_session_id: str, prompt: str,
) -> Tuple[List[str], Path, str]:
    """Prove an old resume deadline failed before submitting the original input."""
    from .codex_app_server import parse_exec_command

    meta = run_root / "meta" / f"agent_{idx:03d}"
    home = meta / "codex_home"
    instruction_path = meta / "developer_instructions.json"
    files = [meta / name for name in ("status.json", "command.json", "stdout.log")]
    if any(path.is_symlink() for path in (
        meta, home, home / "sessions", *files, instruction_path, run_root / "prompt.txt",
    )):
        raise ValueError("Retained startup paths must not be symlinks")
    instructions = instruction_path.read_text(encoding="utf-8") if instruction_path.exists() else None
    if instructions is not None:
        value = json.loads(instructions)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Invalid retained PCR developer instructions")
    status_text, command_text, stdout = [path.read_text(encoding="utf-8") for path in files]
    status, recorded = json.loads(status_text), json.loads(command_text)
    error = {"type": "error", "message": "Codex thread/resume did not respond within 90 seconds."}
    lines = stdout.splitlines()
    if (
        status.get("status") != "failed" or status.get("returncode") != 1
        or status.get("codex_thread_id") or status.get("idx") != idx
        or len(lines) != 1 or json.loads(lines[0]) != error
        or status.get("stdout_tail") != lines[0]
        or (run_root / "prompt.txt").read_text(encoding="utf-8") != prompt
    ):
        raise ValueError("The retained failure does not prove input was never submitted")
    command = recorded.get("command")
    if not isinstance(command, list) or not all(isinstance(value, str) for value in command):
        raise ValueError("Invalid retained Codex command")
    _server, _params, output, resume = parse_exec_command(command)
    final = meta / "final_message.md"
    if (
        resume != resume_session_id or status.get("command") != command
        or output != final or final.is_symlink() or (final.exists() and final.stat().st_size)
        or recorded.get("idx") != idx or recorded.get("role") != AGENT_ROLE_CANDIDATE
        or recorded.get("cwd") != str(workspace) or status.get("workspace_dir") != str(workspace)
        or recorded.get("codex_home") != str(home) or status.get("codex_home") != str(home)
        or not home.is_dir()
    ):
        raise ValueError("Original startup command, workspace, or history binding changed")
    rollout = find_rollout_path_for_session(home, resume_session_id)
    if rollout is None or rollout.is_symlink() or not is_relative_to(rollout.resolve(), home):
        raise ValueError("Original private resume history is unavailable")
    with rollout.open(encoding="utf-8") as stream:
        header = json.loads(stream.readline(128 * 1024))
    if header.get("type") != "session_meta" or session_meta_thread_id(header.get("payload", {})) != resume_session_id:
        raise ValueError("Retained history belongs to a different session")
    binding = json.dumps([status_text, command_text, stdout, prompt, str(rollout),
                          rollout.stat().st_size, rollout.stat().st_mtime_ns, instructions])
    return command, home, hashlib.sha256(binding.encode("utf-8")).hexdigest()


@contextmanager
def _retained_execution_lease(meta: Path):
    if os.name != "posix":
        raise ValueError("Retained execution recovery requires POSIX ownership locks")
    import fcntl

    lock = meta / "execution_recovery.lock"
    if lock.is_symlink():
        raise ValueError("Retained execution lock must not be a symlink")
    with lock.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_retained_execution(
    run_root: Path, idx: int, workspace: Path, prompt: str,
) -> Tuple[List[str], Path, str]:
    """Validate an accepted execution without rebuilding its workspace or history."""
    from .codex_app_server import parse_exec_command

    meta = run_root / "meta" / f"agent_{idx:03d}"
    home = meta / "codex_home"
    names = ("command.json", "status.json", "developer_instructions.json", "execution_recovery.json")
    paths = [meta / name for name in names]
    if any(path.is_symlink() for path in (meta, home, workspace, home / "sessions", *paths,
                                          run_root / "prompt.txt")):
        raise ValueError("Retained execution paths must not be symlinks")
    texts = [path.read_text(encoding="utf-8") if path.exists() else "" for path in paths]
    recorded, status, instructions, receipt = [json.loads(text) if text else None for text in texts]
    if (not isinstance(recorded, dict) or not isinstance(receipt, dict)
        or (status is not None and not isinstance(status, dict))):
        raise ValueError("Invalid retained execution metadata")
    # A supervisor can exit before writing the result. Its accepted native
    # receipt, exclusive lease and dead owner still prove the original task.
    unfinished = status is None and receipt.get("stage") in {"accepted", "continuation_ready"}
    if status is None and not unfinished:
        raise ValueError("Missing result without an accepted unfinished execution")
    if unfinished:
        status = {"idx": idx, "role": AGENT_ROLE_CANDIDATE, "workspace_dir": str(workspace),
                  "codex_home": str(home), "command": recorded.get("command"), "status": "interrupted"}
    command = recorded.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
        raise ValueError("Invalid retained execution command")
    _server, _params, output, _resume = parse_exec_command(command)
    if (
        not isinstance(instructions, str) or not instructions.strip()
        or recorded.get("idx") != idx or status.get("idx") != idx
        or recorded.get("role") != AGENT_ROLE_CANDIDATE or status.get("role") != AGENT_ROLE_CANDIDATE
        or recorded.get("cwd") != str(workspace) or status.get("workspace_dir") != str(workspace)
        or recorded.get("codex_home") != str(home) or status.get("codex_home") != str(home)
        or status.get("command") != command or status.get("status") not in {"failed", "error", "interrupted"}
        or recorded.get("additional_developer_instructions") != instructions
        or output != meta / "final_message.md" or output.is_symlink()
        or not workspace.is_dir() or not home.is_dir()
        or ((run_root / "prompt.txt").exists()
            and (run_root / "prompt.txt").read_text(encoding="utf-8") != prompt)
        or not isinstance(receipt.get("thread_id"), str) or not receipt["thread_id"].strip()
        or not isinstance(receipt.get("client_message_id"), str) or not receipt["client_message_id"].strip()
        or not isinstance(receipt.get("turn_id"), str) or not receipt["turn_id"].strip()
        or not retained_agent_execution(meta, command, workspace, home, prompt, instructions)
    ):
        raise ValueError("Original execution receipt, command, workspace or stop state is not resumable")
    rollout = find_rollout_path_for_session(home, receipt["thread_id"])
    if rollout is None or rollout.is_symlink() or not is_relative_to(rollout.resolve(), home):
        raise ValueError("Retained private execution history is unavailable")
    with rollout.open(encoding="utf-8") as stream:
        header = json.loads(stream.readline(128 * 1024))
    if header.get("type") != "session_meta" or session_meta_thread_id(header.get("payload", {})) != receipt["thread_id"]:
        raise ValueError("Retained execution history belongs to a different thread")
    binding = json.dumps([texts, prompt, str(workspace), str(rollout),
                          rollout.stat().st_size, rollout.stat().st_mtime_ns])
    return command, home, hashlib.sha256(binding.encode("utf-8")).hexdigest()


def retained_execution_preparation(
    run_root: Path, idx: int, workspace: Path, prompt: str,
) -> Tuple[List[str], Path, str]:
    with _retained_execution_lease(run_root / "meta" / f"agent_{idx:03d}"):
        return _read_retained_execution(run_root, idx, workspace, prompt)


def _prepare_retained_execution(
    *, run_root: Path, idx: int, workspace: Path, prompt: str,
    fingerprint: str, real_codex_home: Path,
) -> Tuple[List[str], Path]:
    with _retained_execution_lease(run_root / "meta" / f"agent_{idx:03d}"):
        command, home, current = _read_retained_execution(run_root, idx, workspace, prompt)
        if current != fingerprint:
            raise ValueError("The retained execution changed after recovery was requested")
        archive = run_root / "retry_history" / f"agent_{idx:03d}" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive.mkdir(parents=True)
        for name in ("status.json", "command.json", "execution_recovery.json", "final_message.md"):
            path = home.parent / name
            if path.exists():
                shutil.copy2(path, archive / name)
        copy_codex_support_entries(real_codex_home, home)
    return command, home


def _prepare_retained_resume(
    *, run_root: Path, idx: int, workspace: Path, resume_session_id: str,
    prompt: str, fingerprint: str, real_codex_home: Path,
) -> Tuple[List[str], Path]:
    command, home, current = retained_resume_preparation(run_root, idx, workspace, resume_session_id, prompt)
    if current != fingerprint:
        raise ValueError("The retained startup changed after recovery was requested")
    from .codex_app_server import uses_app_server_transport

    instruction_path = home.parent / "developer_instructions.json"
    if uses_app_server_transport(command[0]) and not instruction_path.exists():
        # This explicit, fingerprint-checked recovery predates per-turn metadata.
        # Prepare current PCR requirements without changing its original command,
        # input or private history. Missing metadata in normal execution still fails.
        instructions = _AgentDeveloperInstructionResolver().for_workspace(
            codex_bin=command[0], codex_home=home, agent_workspace=workspace,
            run_root=run_root, agent_idx=idx, defer_resolution=True,
        )
        instruction_path.write_text(json.dumps(instructions, ensure_ascii=False), encoding="utf-8")
    archive = run_root / "retry_history" / f"agent_{idx:03d}" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive.mkdir(parents=True)
    # Keep the private history and SQLite paths in place; never recopy a newer
    # source conversation or turn this known pre-submission failure into a new task.
    for name in ("status.json", "command.json", "stdout.log", "stderr.log", "final_message.md"):
        path = home.parent / name
        if path.exists():
            shutil.move(str(path), str(archive / name))
    copy_codex_support_entries(real_codex_home, home)
    return command, home


def agent_role_execution_args(
    args: argparse.Namespace,
    agent_role: str,
) -> argparse.Namespace:
    if normalize_agent_role(agent_role) != AGENT_ROLE_SYNTHESIS:
        return args
    settings = effective_synthesis_codex_settings(args)
    stage_args = argparse.Namespace(**vars(args))
    stage_args.model = settings.model
    stage_args.effort = settings.effort
    stage_args.fast = settings.fast
    return stage_args


def _prepare_additional_agent(
    *,
    args: argparse.Namespace,
    idx: int,
    run_root: Path,
    workspace: Path,
    workspaces_root: Path,
    meta_root: Path,
    help_text: str,
    real_codex_home: Path,
    effective_effort: Optional[str],
    resume_session_id: Optional[str],
    retry: bool,
    progress_callback: ProgressCallback,
    agent_role: str,
    developer_instructions: Optional[str],
    instruction_resolver: _AgentDeveloperInstructionResolver,
    prepared_source: Optional[AgentCodexSource] = None,
    initial: bool = False,
    capabilities_by_agent: Optional[Dict[int, Dict[str, bool]]] = None,
) -> Tuple[List[str], Path]:
    source_mode = getattr(args, "workspace_mode", "isolated") == "source"
    agent_workspace = agent_execution_workspace(
        workspace, workspaces_root, idx, "source" if source_mode else "isolated"
    )
    agent_meta_dir = meta_root / f"agent_{idx:03d}"
    agent_codex_home = agent_meta_dir / "codex_home"
    preparation_timings: Dict[str, float] = {}

    def measured(name: str, began: float) -> None:
        preparation_timings[name] = time.monotonic() - began
        if progress_callback is not None:
            progress_callback({"type": "agent_phase", "idx": idx, "phase": "preparing",
                               "phase_timings": dict(preparation_timings)})
    try:
        if retry:
            if not source_mode:
                cleanup_workspace_copy(workspace, agent_workspace)
            _archive_retry_metadata(run_root, agent_meta_dir, idx)
        elif (source_mode and not initial) or (
            not source_mode and (agent_workspace.exists() or agent_workspace.is_symlink())
        ):
            raise FileExistsError(f"agent workspace already exists: {agent_workspace}")

        if progress_callback is not None:
            progress_callback(
                {
                    "type": "agent_status",
                    "idx": idx,
                    "status": "copying",
                    "role": agent_role,
                }
            )
        began = time.monotonic()
        if not source_mode:
            copy_workspace(workspace, agent_workspace, run_base=run_root.parent)
        measured("workspace_copy", began)
        began = time.monotonic()
        prepare_agent_codex_home(
            real_codex_home,
            agent_codex_home,
            agent_workspace,
            resume_session_id,
            prepared_source=prepared_source,
        )
        measured("history_preparation", began)
        if prepared_source is not None:
            context = prepared_source.execution_context(agent_workspace, agent_codex_home)
            if context:
                developer_instructions = merge_codex_developer_instructions(developer_instructions, context)
        began = time.monotonic()
        from .codex_app_server import uses_app_server_transport

        defer_resolution = uses_app_server_transport(resolve_codex_executable(args.codex_bin))
        effective_developer_instructions = instruction_resolver.for_workspace(
            codex_bin=args.codex_bin,
            codex_home=agent_codex_home,
            agent_workspace=agent_workspace,
            run_root=run_root,
            agent_idx=idx,
            additional_instructions=developer_instructions,
            defer_resolution=defer_resolution,
        )
        if defer_resolution:
            (agent_meta_dir / "developer_instructions.json").write_text(
                json.dumps(effective_developer_instructions, ensure_ascii=False), encoding="utf-8",
            )
        measured("configuration", began)
        (agent_meta_dir / "preparation_timings.json").write_text(json.dumps(preparation_timings), encoding="utf-8")
        command, _caps = build_codex_command(
            args.codex_bin,
            help_text,
            agent_meta_dir / "final_message.md",
            model=args.model,
            effort=effective_effort,
            fast=getattr(args, "fast", None),
            resume_session_id=resume_session_id,
            developer_instructions=None if defer_resolution else effective_developer_instructions,
            subagents=bool(getattr(args, "subagents", False)),
            subagents_limit=int(
                getattr(args, "subagents_limit", DEFAULT_SUBAGENTS_LIMIT)
            ),
        )
        if capabilities_by_agent is not None:
            capabilities_by_agent[idx] = _caps
        return command, agent_codex_home
    except BaseException:
        try:
            scrub_codex_home_support_entries(agent_codex_home)
        except BaseException:
            pass
        try:
            if not source_mode:
                cleanup_workspace_copy(workspace, agent_workspace)
        except BaseException:
            pass
        raise


@_source_execution_lease
def run_additional_agents(
    args: argparse.Namespace,
    prompt: str,
    agent_indices: Sequence[int],
    run_root: Path,
    workspace: Path,
    resume_session_id: Optional[str] = None,
    retry_indices: Optional[Set[int]] = None,
    progress_callback: ProgressCallback = None,
    cancel_event: Any = None,
    agent_cancel_events: Optional[Dict[int, Any]] = None,
    agent_role: str = AGENT_ROLE_CANDIDATE,
    refresh_result_files: bool = True,
    developer_instructions: Optional[str] = None,
    recommendation_excluded_indices: Optional[Set[int]] = None,
    synthesis_source_agents: Optional[Sequence[int]] = None,
    synthesis_requested_agents: Optional[int] = None,
    synthesis_status: Optional[str] = None,
    synthesis_error: Optional[str] = None,
    retained_startup_fingerprint: Optional[str] = None,
    retained_execution_fingerprints: Optional[Dict[int, str]] = None,
    synthesis_preparation_fingerprints: Optional[Dict[int, str]] = None,
) -> List[AgentResult]:
    agent_role = normalize_agent_role(agent_role)
    execution_args = agent_role_execution_args(args, agent_role)
    indices = list(dict.fromkeys(int(idx) for idx in agent_indices))
    if not indices or any(idx <= 0 for idx in indices):
        raise ValueError("agent indices must contain positive integers")

    run_root = run_root.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    workspaces_root = run_root / "workspaces"
    meta_root = run_root / "meta"
    if is_relative_to(run_root, workspace):
        raise RuntimeError(f"run root must be outside workspace: {run_root}")
    workspaces_root.mkdir(parents=True, exist_ok=True)
    meta_root.mkdir(parents=True, exist_ok=True)

    retries = set(retry_indices or set())
    retained_executions = dict(retained_execution_fingerprints or {})
    synthesis_preparations = dict(synthesis_preparation_fingerprints or {})
    if synthesis_preparations and (
        set(synthesis_preparations) != set(indices) or retries != set(indices)
        or agent_role != AGENT_ROLE_SYNTHESIS or retained_executions or retained_startup_fingerprint
    ):
        raise ValueError("Synthesis preparation recovery requires exact unsubmitted synthesis slots")
    for idx, expected in synthesis_preparations.items():
        if failed_synthesis_preparation_fingerprint(run_root, idx, prompt) != expected:
            raise ValueError("Synthesis preparation changed before dispatch")
    if retained_executions and (
        retained_startup_fingerprint or set(retained_executions) != set(indices)
        or retries != set(indices) or agent_role != AGENT_ROLE_CANDIDATE
    ):
        raise ValueError("Retained execution recovery must cover exactly the requested candidate retries")
    if retained_startup_fingerprint and (
        len(indices) != 1 or retries != set(indices) or not resume_session_id
        or agent_role != AGENT_ROLE_CANDIDATE or getattr(args, "workspace_mode", "isolated") != "source"
    ):
        raise ValueError("Retained startup recovery requires one original source-mode resume")
    help_text = (
        read_codex_exec_resume_help(execution_args.codex_bin)
        if resume_session_id
        else read_codex_exec_help(execution_args.codex_bin)
    )
    real_codex_home = get_codex_home()
    model_cache_manager = CodexModelCacheManager(
        execution_args.codex_bin,
        real_codex_home,
        copy_codex_support_entries,
    )
    effective_effort = resolve_codex_reasoning_effort(
        getattr(execution_args, "model", None),
        getattr(execution_args, "effort", None),
        real_codex_home,
    )
    command_by_agent: Dict[int, List[str]] = {}
    codex_home_by_agent: Dict[int, Path] = {}
    instruction_resolver = _AgentDeveloperInstructionResolver()
    for idx, expected in retained_executions.items():
        agent_workspace = agent_execution_workspace(workspace, workspaces_root, idx,
            getattr(args, "workspace_mode", "isolated"))
        command, home, current = retained_execution_preparation(run_root, idx, agent_workspace, prompt)
        if current != expected:
            raise ValueError("The retained execution changed before recovery dispatch")
        command_by_agent[idx], codex_home_by_agent[idx] = command, home

    def prepare_agent(idx: int) -> Tuple[List[str], Path]:
        if synthesis_preparations and failed_synthesis_preparation_fingerprint(run_root, idx, prompt) != synthesis_preparations[idx]:
            raise ValueError("Synthesis preparation changed before its private environment was prepared")
        if retained_executions:
            agent_workspace = agent_execution_workspace(workspace, workspaces_root, idx,
                getattr(args, "workspace_mode", "isolated"))
            return _prepare_retained_execution(run_root=run_root, idx=idx, workspace=agent_workspace,
                prompt=prompt, fingerprint=retained_executions[idx], real_codex_home=real_codex_home)
        if retained_startup_fingerprint:
            return _prepare_retained_resume(
                run_root=run_root, idx=idx, workspace=workspace,
                resume_session_id=resume_session_id, prompt=prompt,
                fingerprint=retained_startup_fingerprint, real_codex_home=real_codex_home,
            )
        return _prepare_additional_agent(
            args=execution_args,
            idx=idx,
            run_root=run_root,
            workspace=workspace,
            workspaces_root=workspaces_root,
            meta_root=meta_root,
            help_text=help_text,
            real_codex_home=real_codex_home,
            effective_effort=effective_effort,
            resume_session_id=resume_session_id,
            retry=idx in retries,
            progress_callback=progress_callback,
            agent_role=agent_role,
            developer_instructions=developer_instructions,
            instruction_resolver=instruction_resolver,
            prepared_source=prepared_source,
        )

    if cancel_requested(cancel_event):
        return []

    max_parallel = (
        1
        if execution_args.serial
        else (execution_args.max_parallel or len(indices))
    )
    max_parallel = min(max_parallel, len(indices))
    prepared_source = AgentCodexSource(real_codex_home, resume_session_id, meta_root / f".source-{time.time_ns()}") if resume_session_id and not retained_startup_fingerprint and not retained_executions else None
    if prepared_source is not None:
        prepared_source.start()
    try:
        results = asyncio.run(
            run_all_agents(
                n=len(indices),
                workspaces_root=workspaces_root,
                meta_root=meta_root,
                prompt=prompt,
                command_by_agent=command_by_agent,
                codex_home_by_agent=codex_home_by_agent,
                max_parallel=max_parallel,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_events=agent_cancel_events,
                agent_indices=indices,
                agent_role=agent_role,
                agent_start_preparer=model_cache_manager.populate_ready,
                source_workspace=workspace if getattr(args, "workspace_mode", "isolated") == "source" else None,
                initial_agent_preparer=prepare_agent,
            )
        )
    finally:
        if prepared_source is not None:
            prepared_source.close()
        for idx in indices:
            scrub_codex_home_support_entries(meta_root / f"agent_{idx:03d}" / "codex_home")
    if refresh_result_files:
        refresh_run_result_files(
            run_root=run_root,
            workspace=workspace,
            prompt=prompt,
            new_results=results,
            recommend_by=execution_args.recommend_by,
            recommendation_excluded_indices=recommendation_excluded_indices,
            synthesis_source_agents=synthesis_source_agents,
            synthesis_requested_agents=synthesis_requested_agents,
            synthesis_status=synthesis_status,
            synthesis_error=synthesis_error,
        )
    return results


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def _explicit_tui_settings(argv: Sequence[str]) -> set[str]:
    """Record CLI settings so workspace defaults do not override explicit flags."""
    option_to_setting = {
        "-n": "agents",
        "--num-agents": "agents",
        "--synthesis-agents": "synthesis_agents",
        "--follow-up-delay": "follow_up_delay",
        "--serial": "execution",
        "--max-parallel": "max_parallel",
        "--subagents": "subagents",
        "--no-subagents": "subagents",
        "--subagents-limit": "subagents_limit",
        "--recommend-by": "recommend_by",
        "--model": "model",
        "--effort": "effort",
        "--fast": "fast",
        "--no-fast": "fast",
        "--synthesis-model": "synthesis_model",
        "--synthesis-effort": "synthesis_effort",
        "--synthesis-fast": "synthesis_fast",
        "--no-synthesis-fast": "synthesis_fast",
        "--no-sync-back": "sync_back",
        "--keep-workspaces": "keep_workspaces",
        "--resume": "resume",
        "--resume-session-id": "resume",
        "--resume-include-non-interactive": "resume_include_non_interactive",
        "--runs-dir": "runs_dir",
        "--codex-bin": "codex_bin",
    }
    explicit: set[str] = set()
    for raw in argv:
        option = raw.split("=", 1)[0]
        setting = option_to_setting.get(option)
        if setting is not None:
            explicit.add(setting)
        elif raw.startswith("-n") and raw != "-n":
            explicit.add("agents")
    return explicit


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="pcr",
        description=(
            "Run isolated Codex candidates, optionally synthesize their results, then sync "
            "the recommended successful result back. With no prompt on an interactive terminal, "
            "opens the PCR TUI."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    try:
        installed_version = distribution_version("parallel-codex-runner")
    except PackageNotFoundError:
        installed_version = "development"
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {installed_version}",
    )
    parser.add_argument("prompt", nargs="?", help="Prompt text. Omit it in a TTY to open the interactive TUI.")
    parser.add_argument(
        "-n",
        "--num-agents",
        type=int,
        default=DEFAULT_NUM_AGENTS,
        help="Number of Codex agents to run.",
    )
    parser.add_argument(
        "--synthesis-agents",
        type=int,
        default=DEFAULT_SYNTHESIS_AGENTS,
        help=(
            "After all candidates finish, run this many isolated agents to review "
            "and synthesize their successful results. Zero disables synthesis."
        ),
    )
    parser.add_argument(
        "--follow-up-delay",
        type=int,
        default=DEFAULT_FOLLOW_UP_DELAY_SECONDS,
        metavar="SECONDS",
        help=(
            "TUI review delay before a queued follow-up starts from the selected "
            "agent. Zero starts it immediately."
        ),
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        help="Maximum Codex agents to run concurrently within each stage. Defaults to that stage's agent count.",
    )
    parser.add_argument(
        "--subagents",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Allow each PCR agent to create nested Codex subagents. Disabled by "
            "default because nested parallelism can multiply resource usage."
        ),
    )
    parser.add_argument(
        "--subagents-limit",
        type=int,
        default=DEFAULT_SUBAGENTS_LIMIT,
        help=(
            "Maximum active nested Codex subagents per PCR agent when "
            "--subagents is enabled."
        ),
    )
    parser.add_argument("--serial", action="store_true", help="Run agents one at a time, equivalent to --max-parallel 1.")
    parser.add_argument(
        "--recommend-by",
        dest="recommend_by",
        type=normalize_recommend_by,
        default="reasoning_tokens",
        metavar="{duration,reasoning_tokens}",
        help="Recommendation strategy: duration chooses the longest successful run; reasoning_tokens chooses the maximum observed reasoning tokens.",
    )
    parser.add_argument("--prompt-file", type=str, default=None, help="Read prompt from UTF-8 text file.")
    parser.add_argument("--workspace", type=str, default=None, help="Workspace to copy. Defaults to current directory.")
    parser.add_argument("--runs-dir", type=str, default=None, help="Directory for .codex_parallel_runs. Must not be inside workspace.")
    parser.add_argument("--codex-bin", type=str, default="codex", help="Codex CLI executable.")
    parser.add_argument("--model", type=str, default=None, help="Optional Codex model name if your CLI supports --model.")
    parser.add_argument(
        "--effort",
        type=str,
        default=None,
        help="Optional model reasoning effort. Supported values are validated against the Codex model cache.",
    )
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Force Codex Fast mode on or off. When omitted, inherit the "
            "Codex service-tier configuration. Fast mode uses more credits."
        ),
    )
    parser.add_argument(
        "--synthesis-model",
        type=normalize_synthesis_model,
        default=None,
        metavar="MODEL",
        help="Codex model for synthesis agents. Defaults to the Codex configuration.",
    )
    parser.add_argument(
        "--synthesis-effort",
        type=normalize_synthesis_effort,
        default=None,
        metavar="LEVEL",
        help="Reasoning effort for synthesis agents. Defaults to auto.",
    )
    parser.add_argument(
        "--synthesis-fast",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Force Codex Fast mode on or off for synthesis agents. When "
            "omitted, use the Codex service-tier configuration."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Show Codex resume sessions for this workspace and run agents with the selected session.",
    )
    parser.add_argument(
        "--resume-session-id",
        type=str,
        default=None,
        help="Resume this Codex session id without showing the interactive picker.",
    )
    parser.add_argument(
        "--resume-include-non-interactive",
        action="store_true",
        help="Include non-interactive codex exec sessions in the --resume picker.",
    )
    parser.add_argument("--no-sync-back", action="store_true", help="Do not copy the recommended workspace back to the original workspace.")
    parser.add_argument("--keep-workspaces", action="store_true", help="Keep isolated candidate and synthesis workspaces after the run.")
    args = parser.parse_args(raw_argv)
    args._pcr_explicit_tui_settings = _explicit_tui_settings(raw_argv)
    return args


def validate_args(args: argparse.Namespace) -> None:
    try:
        validate_agent_count(args.num_agents, "-n / --num-agents")
        validate_agent_count(
            getattr(args, "synthesis_agents", 0),
            "--synthesis-agents",
            allow_zero=True,
        )
        if args.num_agents + getattr(args, "synthesis_agents", 0) > MAX_AGENT_COUNT:
            raise ValueError(
                "candidate and synthesis Agents together must not exceed "
                f"{MAX_AGENT_COUNT}"
            )
    except ValueError as exc:
        raise SystemExit(f"{exc}。") from exc
    follow_up_delay = getattr(
        args,
        "follow_up_delay",
        DEFAULT_FOLLOW_UP_DELAY_SECONDS,
    )
    if isinstance(follow_up_delay, bool) or not isinstance(follow_up_delay, int):
        raise SystemExit("--follow-up-delay 必须是非负整数秒数。")
    if follow_up_delay < 0:
        raise SystemExit("--follow-up-delay 不能小于 0。")
    args.follow_up_delay = follow_up_delay
    if args.max_parallel is not None and args.max_parallel <= 0:
        raise SystemExit("--max-parallel 必须大于 0。")
    subagents_limit = getattr(
        args,
        "subagents_limit",
        DEFAULT_SUBAGENTS_LIMIT,
    )
    if isinstance(subagents_limit, bool) or not isinstance(subagents_limit, int):
        raise SystemExit("--subagents-limit 必须是正整数。")
    if subagents_limit <= 0:
        raise SystemExit("--subagents-limit 必须大于 0。")
    args.subagents = bool(getattr(args, "subagents", False))
    args.subagents_limit = subagents_limit
    if args.serial and args.max_parallel not in (None, 1):
        raise SystemExit("--serial 不能和 --max-parallel > 1 同时使用。")
    effort = str(getattr(args, "effort", None) or "").strip().lower()
    args.effort = (
        None
        if effort in {"", "auto", "clear", "default", "none"}
        else effort
    )
    fast = getattr(args, "fast", None)
    if fast is not None and not isinstance(fast, bool):
        raise SystemExit("--fast / --no-fast 配置无效。")
    args.fast = fast
    args.synthesis_model = normalize_synthesis_model(
        getattr(args, "synthesis_model", None)
    )
    args.synthesis_effort = normalize_synthesis_effort(
        getattr(args, "synthesis_effort", None)
    )
    try:
        args.synthesis_fast = normalize_synthesis_fast(
            getattr(args, "synthesis_fast", None)
        )
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(str(exc)) from exc
    resume_requested = bool(
        getattr(args, "resume", False)
        or getattr(args, "resume_session_id", None)
    )
    if args.effort and (getattr(args, "model", None) or not resume_requested):
        resolve_codex_reasoning_effort(
            getattr(args, "model", None),
            args.effort,
        )
    synthesis_settings = effective_synthesis_codex_settings(args)
    if synthesis_settings.effort and (
        synthesis_settings.model or not resume_requested
    ):
        resolve_codex_reasoning_effort(
            synthesis_settings.model,
            synthesis_settings.effort,
        )


def _allocate_synthesis_agent_indices(
    results: Sequence[AgentResult],
    first_stage_agents: int,
    synthesis_agents: int,
) -> List[int]:
    """Use reserved stage-two slots without colliding with live /more Agents."""
    occupied = {result.idx for result in results}
    indices: List[int] = []
    candidate = max(0, int(first_stage_agents)) + 1
    while len(indices) < max(0, int(synthesis_agents)):
        if candidate not in occupied:
            indices.append(candidate)
        candidate += 1
    return indices


def run_synthesis_refresh(
    *,
    args: argparse.Namespace,
    prompt: str,
    run_root: Path,
    workspace: Path,
    recorded_results: Sequence[AgentResult],
    resume_session_id: Optional[str] = None,
    progress_callback: ProgressCallback = None,
    cancel_event: Any = None,
    agent_cancel_events: Optional[Dict[int, Any]] = None,
    synthesis_indices: Optional[Sequence[int]] = None,
    retry_synthesis_indices: Optional[Set[int]] = None,
    recommendation_excluded_indices: Optional[Set[int]] = None,
    synthesis_preparation_fingerprints: Optional[Dict[int, str]] = None,
) -> List[AgentResult]:
    """Rerun stage two after late candidates or retry selected synthesis Agents."""
    sources = sorted(
        (
            result
            for result in recorded_results
            if result.role == AGENT_ROLE_CANDIDATE and result.status == "success"
        ),
        key=lambda result: result.idx,
    )
    existing_synthesis = sorted(
        result.idx
        for result in recorded_results
        if result.role == AGENT_ROLE_SYNTHESIS
    )
    configured_count = max(0, int(getattr(args, "synthesis_agents", 0) or 0))
    if synthesis_indices is None:
        indices = existing_synthesis[:configured_count]
        occupied = {result.idx for result in recorded_results}
        candidate = max(0, int(getattr(args, "num_agents", 0) or 0)) + 1
        while len(indices) < configured_count:
            if candidate not in occupied and candidate not in indices:
                indices.append(candidate)
            candidate += 1
    else:
        indices = list(dict.fromkeys(int(idx) for idx in synthesis_indices))
        if any(idx <= 0 for idx in indices):
            raise ValueError("synthesis indices must contain positive integers")

    targeted_retry = (
        synthesis_indices is not None and retry_synthesis_indices is not None
    )
    excluded = set(recommendation_excluded_indices or set())
    if not targeted_retry:
        excluded.update(set(existing_synthesis) - set(indices))
    if not sources or not indices:
        refresh_run_result_files(
            run_root=run_root,
            workspace=workspace,
            prompt=prompt,
            new_results=[],
            recommend_by=args.recommend_by,
            recommendation_excluded_indices=excluded | set(existing_synthesis),
            synthesis_source_agents=[result.idx for result in sources],
            synthesis_requested_agents=configured_count,
            synthesis_status="skipped",
            synthesis_error=(
                "no successful first-stage candidates" if not sources else None
            ),
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "synthesis_finished",
                    "indices": indices,
                    "source_agents": [result.idx for result in sources],
                    "success": 0,
                    "cancelled": cancel_requested(cancel_event),
                    "skipped": True,
                }
            )
        return []

    for idx, expected in (synthesis_preparation_fingerprints or {}).items():
        if failed_synthesis_preparation_fingerprint(run_root, idx, prompt) != expected:
            raise ValueError("Synthesis preparation changed before its context was refreshed")
    context_path, instructions = create_synthesis_context(
        run_root,
        prompt,
        sources,
    )
    if progress_callback is not None:
        settings = effective_synthesis_codex_settings(args)
        progress_callback(
            {
                "type": "synthesis_started",
                "indices": indices,
                "source_agents": [result.idx for result in sources],
                "context_path": str(context_path),
                "instructions_path": str(run_root / "synthesis_instructions.txt"),
                "prompt": prompt,
                "user_prompt": prompt,
                "developer_instructions": instructions,
                "resume_session_id": resume_session_id,
                "model": settings.model,
                "effort": settings.effort,
                "fast": settings.fast,
                "refresh": True,
            }
        )
    if agent_cancel_events is not None:
        for idx in indices:
            agent_cancel_events.setdefault(idx, threading.Event())
    try:
        results = run_additional_agents(
            args=args,
            prompt=prompt,
            agent_indices=indices,
            run_root=run_root,
            workspace=workspace,
            resume_session_id=resume_session_id,
            retry_indices=(
                set(retry_synthesis_indices)
                if retry_synthesis_indices is not None
                else set(indices) & set(existing_synthesis)
            ),
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            agent_cancel_events=agent_cancel_events,
            agent_role=AGENT_ROLE_SYNTHESIS,
            developer_instructions=instructions,
            recommendation_excluded_indices=excluded,
            synthesis_source_agents=[result.idx for result in sources],
            synthesis_requested_agents=configured_count,
            synthesis_status="completed",
            synthesis_preparation_fingerprints=synthesis_preparation_fingerprints,
        )
    except BaseException as exc:
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "synthesis_finished",
                    "indices": indices,
                    "source_agents": [result.idx for result in sources],
                    "success": 0,
                    "cancelled": cancel_requested(cancel_event),
                    "refresh": True,
                    "error": str(exc),
                }
            )
        raise
    if progress_callback is not None:
        progress_callback(
            {
                "type": "synthesis_finished",
                "indices": indices,
                "source_agents": [result.idx for result in sources],
                "success": sum(result.status == "success" for result in results),
                "cancelled": cancel_requested(cancel_event),
                "refresh": True,
            }
        )
    return results


def should_start_tui(args: argparse.Namespace) -> bool:
    return args.prompt is None and args.prompt_file is None and sys.stdin.isatty()


def run_once(
    args: argparse.Namespace,
    prompt: str,
    progress_callback: ProgressCallback = None,
    print_output: bool = True,
) -> int:
    synthesis_agents = max(0, int(getattr(args, "synthesis_agents", 0) or 0))
    mode = getattr(args, "workspace_mode", None) or execution_workspace_mode(
        args.num_agents, synthesis_agents, not args.no_sync_back, args.keep_workspaces
    )
    if mode == "source" and (args.num_agents != 1 or synthesis_agents != 0):
        raise ValueError("Source-folder runs support one Agent without synthesis")
    args.workspace_mode = mode
    return _run_once(args, prompt, progress_callback, print_output)


@_source_execution_lease
def _run_once(
    args: argparse.Namespace,
    prompt: str,
    progress_callback: ProgressCallback = None,
    print_output: bool = True,
) -> int:
    discovery_started = time.monotonic()
    run_timings: Dict[str, float] = {}
    if progress_callback is not None:
        progress_callback({"type": "run_phase", "phase": "discovery"})
    synthesis_agents = max(0, int(getattr(args, "synthesis_agents", 0) or 0))
    mode = args.workspace_mode
    source_mode = mode == "source"
    subagents = bool(getattr(args, "subagents", False))
    subagents_limit = int(
        getattr(args, "subagents_limit", DEFAULT_SUBAGENTS_LIMIT)
    )
    max_parallel = 1 if args.serial else (args.max_parallel or args.num_agents)
    max_parallel = min(max_parallel, args.num_agents)
    external_cancel_event = getattr(args, "cancel_event", None)
    cancel_event = external_cancel_event or threading.Event()
    configured_agent_cancel_events = getattr(args, "agent_cancel_events", None)
    agent_cancel_events: Dict[int, Any] = (
        configured_agent_cancel_events
        if isinstance(configured_agent_cancel_events, dict)
        else {}
    )
    live_synthesis_config = getattr(
        args,
        "live_synthesis_config_coordinator",
        None,
    )
    if not isinstance(live_synthesis_config, LiveSynthesisConfigCoordinator):
        live_synthesis_config = None
    restore_cancel_signals: Callable[[], None] = lambda: None

    def log(level: str, message: str, *values: Any) -> None:
        if progress_callback is None or HAS_LOGURU:
            getattr(logger, level)(message, *values)

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise SystemExit(f"workspace 不存在或不是目录：{workspace}")

    resume_session = resolve_resume_session(args, workspace)
    resume_session_id = resume_session.session_id if resume_session else None
    effort_model = args.model or (resume_session.model if resume_session else None)
    model_registry = CodexModelRegistry.load(get_codex_home())
    model_display = model_registry.model_display(args.model)
    fast_display = format_fast_mode(
        getattr(args, "fast", None),
        model_registry.configured_service_tier,
    )
    effective_effort = resolve_codex_reasoning_effort(
        effort_model,
        getattr(args, "effort", None),
    )
    synthesis_settings = effective_synthesis_codex_settings(args)
    synthesis_effort_model = synthesis_settings.model or (
        resume_session.model if resume_session else None
    )
    synthesis_effective_effort = resolve_codex_reasoning_effort(
        synthesis_effort_model,
        synthesis_settings.effort,
    )
    synthesis_model_display = model_registry.model_display(
        synthesis_settings.model
    )
    synthesis_effort_display = model_registry.effort_display(
        synthesis_effort_model,
        synthesis_settings.effort,
    )
    synthesis_fast_display = format_fast_mode(
        synthesis_settings.fast,
        model_registry.configured_service_tier,
    )
    run_base = choose_run_base(workspace, args.runs_dir)
    if is_relative_to(run_base, workspace):
        raise SystemExit(f"内部错误：run_base 位于 workspace 内部：{run_base}")

    run_root = create_unique_run_root(run_base)
    workspaces_root = run_root / "workspaces"
    meta_root = run_root / "meta"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    meta_root.mkdir(parents=True, exist_ok=True)

    if progress_callback is not None:
        progress_callback(
            {
                "type": "run_prepared",
                "rows": [
                    ["WORKSPACE", absolute_path_for_display(workspace)],
                    ["RUNS_ROOT", absolute_path_for_display(run_root)],
                    ["AGENTS", str(args.num_agents)],
                    ["SYNTHESIS_AGENTS", str(synthesis_agents)],
                    ["EXECUTION", "serial" if max_parallel == 1 else "parallel"],
                    ["MAX_PARALLEL", str(max_parallel)],
                    ["SUBAGENTS", "YES" if subagents else "NO"],
                    ["SUBAGENTS_LIMIT", str(subagents_limit)],
                    ["RECOMMEND_BY", args.recommend_by],
                    ["MODEL", model_display],
                    ["EFFORT", effective_effort or "default"],
                    ["FAST", fast_display],
                    ["SYNTHESIS_MODEL", synthesis_model_display],
                    ["SYNTHESIS_EFFORT", synthesis_effort_display],
                    ["SYNTHESIS_FAST", synthesis_fast_display],
                    ["CODEX_BIN", str(args.codex_bin)],
                    ["SYNC_BACK", "NO" if args.no_sync_back else "YES"],
                    ["KEEP_WORKSPACES", "YES" if args.keep_workspaces else "NO"],
                    ["RESUME", resume_session_id or "NO"],
                    ["METADATA", absolute_path_for_display(meta_root)],
                    ["WORKSPACE COPIES", absolute_path_for_display(workspaces_root)],
                ],
            }
        )

    if HAS_LOGURU:
        logger.remove()
        if progress_callback is None:
            logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
        logger.add(run_root / "runner.log", level="DEBUG", encoding="utf-8", format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}")

    stale_workspace_runs = workspace / ".codex_parallel_runs"
    if stale_workspace_runs.exists():
        log(
            "warning",
            "workspace 内存在历史残留 .codex_parallel_runs，本脚本不会使用、不会复制、不会同步它：{}",
            stale_workspace_runs,
        )

    if HAS_RICH and progress_callback is None:
        assert console is not None
        overview = Table.grid(padding=(0, 2))
        overview.add_column(style="bold")
        overview.add_column()
        overview.add_row("WORKSPACE", absolute_path_for_display(workspace))
        overview.add_row("RUNS_ROOT", absolute_path_for_display(run_root))
        overview.add_row("AGENTS", str(args.num_agents))
        overview.add_row("SYNTHESIS_AGENTS", str(synthesis_agents))
        overview.add_row("EXECUTION", "serial" if max_parallel == 1 else "parallel")
        overview.add_row("MAX_PARALLEL", str(max_parallel))
        overview.add_row("SUBAGENTS", "YES" if subagents else "NO")
        overview.add_row("SUBAGENTS_LIMIT", str(subagents_limit))
        overview.add_row("RECOMMEND_BY", args.recommend_by)
        overview.add_row("MODEL", model_display)
        overview.add_row("EFFORT", effective_effort or "default")
        overview.add_row("FAST", fast_display)
        overview.add_row("SYNTHESIS_MODEL", synthesis_model_display)
        overview.add_row("SYNTHESIS_EFFORT", synthesis_effort_display)
        overview.add_row("SYNTHESIS_FAST", synthesis_fast_display)
        overview.add_row("RESUME", resume_session_id or "NO")
        overview.add_row("METADATA", absolute_path_for_display(meta_root))
        overview.add_row("WORKSPACE COPIES", absolute_path_for_display(workspaces_root))
        console.print(
            Panel(
                overview,
                title="parallel-codex-runner",
                border_style="cyan",
            )
        )
    elif progress_callback is None:
        log("info", "workspace = {}", workspace)
        log("info", "runs_root = {}", run_root)
        log("info", "agents = {}", args.num_agents)
        log("info", "synthesis_agents = {}", synthesis_agents)
        log("info", "execution = {}", "serial" if max_parallel == 1 else "parallel")
        log("info", "max_parallel = {}", max_parallel)
        log("info", "subagents = {}", "YES" if subagents else "NO")
        log("info", "subagents_limit = {}", subagents_limit)
        log("info", "recommend_by = {}", args.recommend_by)
        log("info", "model = {}", model_display)
        log("info", "effort = {}", effective_effort or "default")
        log("info", "fast = {}", fast_display)
        log("info", "synthesis_model = {}", synthesis_model_display)
        log("info", "synthesis_effort = {}", synthesis_effort_display)
        log("info", "synthesis_fast = {}", synthesis_fast_display)
        log("info", "resume = {}", resume_session_id or "NO")

    help_text = read_codex_exec_resume_help(args.codex_bin) if resume_session_id else read_codex_exec_help(args.codex_bin)
    real_codex_home = get_codex_home()
    model_cache_manager = CodexModelCacheManager(
        args.codex_bin,
        real_codex_home,
        copy_codex_support_entries,
    )
    if external_cancel_event is None:
        restore_cancel_signals = install_cancel_signal_handlers(cancel_event)

    command_by_agent: Dict[int, List[str]] = {}
    caps_by_agent: Dict[int, Dict[str, bool]] = {}
    codex_home_by_agent: Dict[int, Path] = {}
    instruction_resolver = _AgentDeveloperInstructionResolver()
    sample_lock = threading.Lock()
    prepared_source = AgentCodexSource(real_codex_home, resume_session_id, meta_root / f".source-{time.time_ns()}") if resume_session_id else None
    if prepared_source is not None:
        prepared_source.start()
    known_candidate_indices = set(range(1, args.num_agents + 1))
    run_timings["discovery"] = time.monotonic() - discovery_started
    if progress_callback is not None:
        progress_callback({"type": "run_phase", "phase": "baseline", "phase_timings": dict(run_timings)})

    def cleanup_unfinished_run() -> None:
        if prepared_source is not None:
            prepared_source.close()
        scrub_agent_codex_homes(meta_root)
        if not args.keep_workspaces and not is_relative_to(workspaces_root, workspace):
            cleanup_workspace_copies(workspace, workspaces_root)
            remove_agent_codex_homes(meta_root)

    baseline_started = time.monotonic()
    try:
        if source_mode:
            # Only a review baseline is copied. The Agent runs in the source tree.
            copy_workspace(workspace, workspaces_root / "baseline", run_base=run_base)
    except BaseException:
        if live_synthesis_config is not None:
            live_synthesis_config.deactivate()
        cleanup_unfinished_run()
        restore_cancel_signals()
        if progress_callback is not None:
            progress_callback({"type": "run_failed", "message": "workspace preparation failed"})
        raise
    finally:
        if source_mode:
            run_timings["baseline"] = time.monotonic() - baseline_started
        if progress_callback is not None:
            progress_callback({"type": "run_phase", "phase": "agent_preparation", "phase_timings": dict(run_timings)})

    if cancel_requested(cancel_event):
        if live_synthesis_config is not None:
            live_synthesis_config.deactivate()
        cleanup_unfinished_run()
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "run_finished",
                    "run_root": str(run_root),
                    "best_agent": None,
                    "success": False,
                    "synced": False,
                    "cancelled": True,
                }
            )
        restore_cancel_signals()
        return 130

    if resume_session is not None:
        (run_root / "resume_session.json").write_text(json.dumps(asdict(resume_session), ensure_ascii=False, indent=2), encoding="utf-8")

    log(
        "info",
        "preparing and starting {} codex agents with max_parallel={}",
        args.num_agents,
        max_parallel,
    )
    live_candidate_coordinator = getattr(args, "live_candidate_coordinator", None)
    if not isinstance(live_candidate_coordinator, LiveCandidateCoordinator):
        live_candidate_coordinator = None

    def prepare_live_candidate(idx: int, retry: bool) -> Tuple[List[str], Path]:
        with sample_lock:
            if retry and idx not in known_candidate_indices:
                raise ValueError(f"cannot retry unknown first-stage agent: {idx}")
            known_candidate_indices.add(idx)
        command, codex_home = _prepare_additional_agent(
            args=args,
            idx=idx,
            run_root=run_root,
            workspace=workspace,
            workspaces_root=workspaces_root,
            meta_root=meta_root,
            help_text=help_text,
            real_codex_home=real_codex_home,
            effective_effort=effective_effort,
            resume_session_id=resume_session_id,
            retry=retry,
            progress_callback=progress_callback,
            agent_role=AGENT_ROLE_CANDIDATE,
            developer_instructions=None,
            instruction_resolver=instruction_resolver,
            prepared_source=prepared_source,
            initial=idx <= args.num_agents and not retry,
            capabilities_by_agent=caps_by_agent,
        )
        with sample_lock:
            if not (run_root / "sample_command.json").exists():
                caps = caps_by_agent[idx]
                (run_root / "codex_capabilities.json").write_text(
                    json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                (run_root / "sample_command.json").write_text(
                    json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                if not caps.get("json", False):
                    log("warning", "当前 Codex CLI help 中未检测到 --json；reasoning_tokens 可能无法观测，将显示为 N/A。")
                if not (caps.get("dangerously_bypass") or caps.get("sandbox")):
                    log("warning", "当前 Codex CLI help 中未检测到全权限相关参数；将按 CLI 默认权限运行。")
        return command, codex_home

    try:
        results = asyncio.run(
            run_all_agents(
                args.num_agents,
                workspaces_root,
                meta_root,
                prompt,
                command_by_agent,
                codex_home_by_agent,
                max_parallel,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_events=agent_cancel_events,
                live_candidate_coordinator=live_candidate_coordinator,
                live_agent_preparer=(
                    prepare_live_candidate
                    if live_candidate_coordinator is not None
                    else None
                ),
                agent_start_preparer=model_cache_manager.populate_ready,
                source_workspace=workspace if source_mode else None,
                initial_agent_preparer=lambda idx: prepare_live_candidate(idx, False),
            )
        )
    except BaseException:
        if live_synthesis_config is not None:
            live_synthesis_config.deactivate()
        cleanup_unfinished_run()
        restore_cancel_signals()
        if progress_callback is not None:
            progress_callback({"type": "run_failed", "message": "agent execution failed"})
        raise

    finally:
        if prepared_source is not None:
            prepared_source.close()

    initial_results = list(results)
    if live_synthesis_config is not None:
        latest_synthesis = live_synthesis_config.close_and_snapshot()
        synthesis_agents = 0 if source_mode else latest_synthesis.agents
        args.synthesis_agents = synthesis_agents
        args.synthesis_model = latest_synthesis.model
        args.synthesis_effort = latest_synthesis.effort
        args.synthesis_fast = latest_synthesis.fast
        synthesis_settings = effective_synthesis_codex_settings(args)
        synthesis_effort_model = synthesis_settings.model or (
            resume_session.model if resume_session else None
        )
        synthesis_effective_effort = resolve_codex_reasoning_effort(
            synthesis_effort_model,
            synthesis_settings.effort,
        )
    synthesis_info: Dict[str, Any] = {
        "requested_agents": synthesis_agents,
        "source_agents": [],
        "launched_agents": [],
        "successful_agents": [],
        "context_path": None,
        "instructions_path": None,
        "resume_session_id": resume_session_id,
        "model": synthesis_settings.model,
        "effort": synthesis_effective_effort,
        "fast": synthesis_settings.fast,
        "status": "disabled" if synthesis_agents == 0 else "pending",
        "reason": None,
        "error": None,
    }
    initial_successes = [
        result for result in initial_results if result.status == "success"
    ]
    if synthesis_agents and cancel_requested(cancel_event):
        synthesis_info["status"] = "cancelled"
        synthesis_info["reason"] = "run cancelled after first stage"
    elif synthesis_agents and not initial_successes:
        synthesis_info["status"] = "skipped"
        synthesis_info["reason"] = "no successful first-stage candidates"
    elif synthesis_agents:
        synthesis_indices = _allocate_synthesis_agent_indices(
            initial_results,
            args.num_agents,
            synthesis_agents,
        )
        synthesis_info["source_agents"] = [
            result.idx
            for result in sorted(initial_successes, key=lambda item: item.idx)
        ]
        synthesis_info["launched_agents"] = synthesis_indices
        for idx in synthesis_indices:
            agent_cancel_events.setdefault(idx, threading.Event())
        try:
            context_path, synthesis_instructions = create_synthesis_context(
                run_root,
                prompt,
                initial_successes,
            )
            synthesis_info["context_path"] = str(context_path)
            synthesis_info["instructions_path"] = str(
                run_root / "synthesis_instructions.txt"
            )
            synthesis_info["status"] = "running"
            if progress_callback is not None:
                progress_callback(
                    {
                        "type": "synthesis_started",
                        "indices": synthesis_indices,
                        "source_agents": synthesis_info["source_agents"],
                        "context_path": str(context_path),
                        "instructions_path": synthesis_info["instructions_path"],
                        "prompt": prompt,
                        "user_prompt": prompt,
                        "developer_instructions": synthesis_instructions,
                        "resume_session_id": resume_session_id,
                        "model": synthesis_settings.model,
                        "effort": synthesis_effective_effort,
                        "fast": synthesis_settings.fast,
                    }
                )
            synthesis_results = run_additional_agents(
                args=args,
                prompt=prompt,
                agent_indices=synthesis_indices,
                run_root=run_root,
                workspace=workspace,
                resume_session_id=resume_session_id,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_events=agent_cancel_events,
                agent_role=AGENT_ROLE_SYNTHESIS,
                refresh_result_files=False,
                developer_instructions=synthesis_instructions,
            )
            results.extend(synthesis_results)
            synthesis_info["status"] = (
                "cancelled" if cancel_requested(cancel_event) else "completed"
            )
            if not cancel_requested(cancel_event) and synthesis_results and all(
                result.status == "error" for result in synthesis_results
            ):
                synthesis_info["status"] = "failed"
                synthesis_info["error"] = synthesis_results[0].error
            if cancel_requested(cancel_event):
                synthesis_info["reason"] = "run cancelled during synthesis"
            if progress_callback is not None:
                progress_callback(
                    {
                        "type": "synthesis_finished",
                        "indices": synthesis_indices,
                        "success": sum(
                            result.status == "success"
                            for result in synthesis_results
                        ),
                        "cancelled": cancel_requested(cancel_event),
                        "error": synthesis_info["error"],
                    }
                )
        except Exception as exc:  # noqa: BLE001
            synthesis_info["status"] = "failed"
            synthesis_info["error"] = str(exc)
            log(
                "warning",
                "synthesis stage failed; falling back to first-stage candidates: {}",
                exc,
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "type": "synthesis_finished",
                        "indices": synthesis_indices,
                        "success": 0,
                        "cancelled": cancel_requested(cancel_event),
                        "error": str(exc),
                    }
                )

    cancelled = cancel_requested(cancel_event)
    successes = [] if cancelled else [r for r in results if r.status == "success"]
    best = select_best_result(successes, args.recommend_by)

    synced = False
    codex_session_promotion: Optional[CodexSessionPromotion] = None
    workspaces_deleted = False
    finalization_error: Optional[BaseException] = None
    finalization_traceback: Any = None
    finalization_failures: List[Tuple[str, BaseException]] = []

    def remember_finalization_error(stage: str, exc: BaseException) -> None:
        nonlocal finalization_error, finalization_traceback
        finalization_failures.append((stage, exc))
        if finalization_error is None:
            finalization_error = exc
            finalization_traceback = exc.__traceback__
        else:
            log("error", "{} also failed during run finalization: {}", stage, exc)

    try:
        try:
            scrub_agent_codex_homes(meta_root)
        except BaseException as exc:  # noqa: BLE001
            remember_finalization_error("Codex home scrubbing", exc)

        try:
            if cancelled:
                log("warning", "run cancelled; source-folder edits are retained" if source_mode else "run cancelled; original workspace was not modified")
            elif best is not None and not args.no_sync_back:
                log("info", "syncing selected workspace back to original workspace")
                if not source_mode:
                    sync_best_workspace_back(Path(best.workspace_dir), workspace)
                synced = True
                log("info", "sync complete: {} -> {}", best.workspace_dir, workspace)
                try:
                    codex_session_promotion = promote_best_codex_session_to_workspace(
                        best,
                        workspace,
                    )
                    if codex_session_promotion is None:
                        log(
                            "warning",
                            "could not identify a Codex session id for the selected "
                            "agent; --resume may not show this run.",
                        )
                    elif codex_session_promotion.error:
                        log(
                            "warning",
                            "Codex session promotion partially failed: {}",
                            codex_session_promotion.error,
                        )
                    else:
                        log(
                            "info",
                            "Codex session {} is now resumable from {}",
                            codex_session_promotion.session_id,
                            workspace,
                        )
                except Exception as exc:  # noqa: BLE001
                    log("warning", "Codex session promotion failed: {}", exc)
            elif best is not None and args.no_sync_back:
                log("info", "source-folder edits are already present; session finalization is deferred" if source_mode else "--no-sync-back set; original workspace was not modified")
            else:
                log("error", "no successful agent; source-folder edits are retained" if source_mode else "no successful agent; original workspace was not modified")
        except WorkspaceSyncPreflightError as exc:
            remember_finalization_error("workspace sync (preflight; untouched)", exc)
        except BaseException as exc:  # noqa: BLE001
            remember_finalization_error("workspace sync", exc)

    finally:
        if not args.keep_workspaces:
            try:
                # Safety check before recursive delete.
                if is_relative_to(workspaces_root, workspace):
                    raise SystemExit(
                        "拒绝删除：workspaces_root 位于 workspace 内部："
                        f"{workspaces_root}"
                    )
                cleanup_workspace_copies(workspace, workspaces_root)
                workspaces_deleted = not workspaces_root.exists()
            except BaseException as exc:  # noqa: BLE001
                remember_finalization_error("workspace cleanup", exc)
            try:
                remove_agent_codex_homes(meta_root)
            except BaseException as exc:  # noqa: BLE001
                remember_finalization_error("Codex home cleanup", exc)

        try:
            try:
                write_run_files(
                    run_root,
                    workspace,
                    prompt,
                    results,
                    best,
                    args.recommend_by,
                    synced,
                    workspaces_deleted,
                    resume_session,
                    codex_session_promotion,
                    synthesis_info,
                    str(finalization_error) if finalization_error is not None else None,
                    [
                        {"stage": stage, "message": str(exc)}
                        for stage, exc in finalization_failures
                    ],
                )
            except BaseException as exc:  # noqa: BLE001
                remember_finalization_error("result file writing", exc)
        finally:
            restore_cancel_signals()

    if finalization_error is not None:
        if progress_callback is not None:
            try:
                progress_callback(
                    {
                        "type": "run_failed",
                        "message": "run finalization failed: "
                        + "; ".join(
                            f"{stage}: {exc}"
                            for stage, exc in finalization_failures
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log("warning", "run_failed callback failed: {}", exc)
        raise finalization_error.with_traceback(finalization_traceback)

    if progress_callback is not None:
        progress_callback(
            {
                "type": "run_finished",
                "run_root": str(run_root),
                "best_agent": best.idx if best else None,
                "success": best is not None,
                "synced": synced,
                "cancelled": cancelled,
                "synthesis": synthesis_info,
            }
        )
    if print_output:
        print_summary(
            results,
            workspace,
            run_root,
            best,
            args.recommend_by,
            synced,
            codex_session_promotion,
        )

    return 130 if cancelled else (0 if best is not None else 2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    validate_args(args)
    if should_start_tui(args):
        from .tui_textual import run_textual_tui

        return run_textual_tui(args)
    prompt = read_prompt(args)
    return run_once(args, prompt)


if __name__ == "__main__":
    raise SystemExit(main())
