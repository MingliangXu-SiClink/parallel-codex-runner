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
    python3 parallel_codex_runner.py "fix the failing tests" -n 20

    # Resume a Codex conversation from this workspace, then send the new prompt
    # to each parallel candidate.
    python3 parallel_codex_runner.py --resume "continue the previous task"

    # Select the longest successful run instead of selecting by reasoning tokens.
    python3 parallel_codex_runner.py "fix the failing tests" -n 20 --recommend-by duration

    # Run against another workspace with a long prompt file.
    python3 parallel_codex_runner.py --prompt-file /tmp/prompt.txt -n 20 --workspace /path/to/project

    # Pipe the prompt through stdin.
    printf '%s\n' "refactor the API client and update tests" | python3 parallel_codex_runner.py -n 8 --workspace /path/to/project

    # Limit concurrency while still creating 20 isolated candidates.
    python3 parallel_codex_runner.py "implement the requested change" -n 20 --max-parallel 5 --workspace /path/to/project

    # Run candidates serially. This is useful when the machine or API quota is tight.
    python3 parallel_codex_runner.py "make the migration idempotent" -n 6 --serial --workspace /path/to/project

    # Choose a model when the installed Codex CLI supports --model.
    python3 parallel_codex_runner.py "improve error handling" -n 10 --model gpt-5 --workspace /path/to/project

    # Keep candidate workspaces for inspection and do not sync anything back.
    python3 parallel_codex_runner.py "investigate this bug" -n 5 --keep-workspaces --no-sync-back --workspace /path/to/project

    # Store run metadata outside the default location. The runs dir must not be
    # inside the target workspace.
    python3 parallel_codex_runner.py "update docs" -n 4 --workspace /path/to/project --runs-dir /tmp/codex-runs

    # Use a non-default Codex executable.
    python3 parallel_codex_runner.py "run the requested cleanup" -n 3 --codex-bin /opt/codex/bin/codex

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
import asyncio
import datetime as _dt
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .codex_cli import build_codex_command, read_codex_exec_help, read_codex_exec_resume_help
from .codex_models import CodexModelRegistry
from .models import (
    AgentResult,
    AgentState,
    CodexHistoryEntry,
    CodexSessionPromotion,
    ResumeSession,
)
from .paths import (
    absolute_path_for_display,
    choose_run_base,
    create_unique_run_root,
    default_run_anchor,
    is_relative_to,
    safe_tail,
)
from .workspace import (
    cleanup_workspace_copy,
    cleanup_workspace_copies,
    copy_workspace,
    sync_best_workspace_back,
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]

# Conda's SQLite build can deadlock inside the macOS VFS when separate Python
# threads open or close databases concurrently. PCR's SQLite sections are
# short, so serialize them process-wide and keep file/JSON work outside.
_CODEX_SQLITE_LOCK = threading.RLock()


def cancel_requested(cancel_event: Any = None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


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
        or name == "history.jsonl"
        or name.startswith("state_")
        or name.startswith("logs_")
        or name.startswith("goals_")
        or name.startswith("memories_")
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
    tmp_path = dst.with_name(f".{dst.name}.pcr-tmp-{os.getpid()}")
    shutil.copy2(src, tmp_path)
    os.replace(tmp_path, dst)


def prepare_agent_codex_home(
    real_codex_home: Path,
    agent_codex_home: Path,
    agent_workspace: Path,
    resume_session_id: Optional[str],
) -> None:
    make_private_dir(agent_codex_home)
    copy_codex_support_entries(real_codex_home, agent_codex_home)

    real_state_db = real_codex_home / "state_5.sqlite"
    agent_state_db = agent_codex_home / "state_5.sqlite"
    copy_sqlite_database(real_state_db, agent_state_db)

    if not resume_session_id or not agent_state_db.exists():
        return

    isolated_rollout: Optional[Path] = None
    rollout_path = find_rollout_path_for_session(real_codex_home, resume_session_id)
    if rollout_path is not None and rollout_path.exists():
        isolated_rollout = agent_codex_home / relative_path_or_import_path(rollout_path, real_codex_home)
        copy_file_atomic(rollout_path, isolated_rollout)

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
            if assignments:
                params.append(resume_session_id)
                conn.execute(f"UPDATE threads SET {', '.join(assignments)} WHERE id = ?", params)
                conn.commit()
        finally:
            if conn is not None:
                conn.close()


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


def load_resume_sessions_from_state(
    codex_home: Path,
    workspace: Path,
    include_non_interactive: bool = False,
) -> List[ResumeSession]:
    workspace_text = str(workspace)
    workspace = workspace.resolve()
    workspace_values = [str(workspace)]
    if workspace_text not in workspace_values:
        workspace_values.append(workspace_text)
    state_db = codex_home / "state_5.sqlite"
    if not state_db.exists():
        return []

    conn: Optional[sqlite3.Connection] = None
    _CODEX_SQLITE_LOCK.acquire()
    try:
        try:
            conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        conn.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)")}
        if not {"id", "cwd"}.issubset(columns):
            return []

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
        where = [f"cwd IN ({', '.join('?' for _ in workspace_values)})"]
        params: List[Any] = workspace_values
        if "archived" in columns:
            where.append("COALESCE(archived, 0) = 0")
        if not include_non_interactive and "source" in columns:
            where.append("COALESCE(source, '') != 'exec'")
        order_expr = "recency_at DESC, updated_at DESC" if {"recency_at", "updated_at"}.issubset(columns) else "id DESC"
        sql = f"SELECT {', '.join(selected)} FROM threads WHERE {' AND '.join(where)} ORDER BY {order_expr}"
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
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
    return sessions


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


def load_resume_sessions_from_jsonl(
    codex_home: Path,
    workspace: Path,
    include_non_interactive: bool = False,
) -> List[ResumeSession]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return []

    sessions: List[ResumeSession] = []
    for path in sessions_root.rglob("*.jsonl"):
        meta: Dict[str, Any] = {}
        first_user_message = ""
        last_preview = ""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    payload = obj.get("payload")
                    if obj.get("type") == "session_meta" and isinstance(payload, dict):
                        meta = payload
                        continue
                    if not isinstance(payload, dict):
                        continue

                    message_text = ""
                    is_user_message = False
                    if obj.get("type") == "event_msg" and payload.get("type") == "user_message":
                        message_text = str(payload.get("message") or "")
                        is_user_message = True
                    elif obj.get("type") == "response_item" and payload.get("type") == "message":
                        message_text = text_from_content(payload.get("content"))
                        is_user_message = payload.get("role") == "user"

                    if message_text:
                        last_preview = message_text
                        if is_user_message and not first_user_message:
                            first_user_message = message_text
        except OSError:
            continue

        session_id = session_meta_thread_id(meta)
        cwd = str(meta.get("cwd") or "").strip()
        source_value = meta.get("source")
        source = (
            source_value.strip()
            if isinstance(source_value, str)
            else json.dumps(source_value, ensure_ascii=False, separators=(",", ":"))
            if source_value is not None
            else ""
        )
        originator = str(meta.get("originator") or "").strip()
        if not session_id or not cwd or not same_workspace(cwd, workspace):
            continue
        if (
            codex_subagent_parent_thread_id(
                source_value,
                meta.get("thread_source"),
                meta.get("parent_thread_id") or meta.get("forked_from_id"),
            )
            is not None
        ):
            continue
        if not include_non_interactive and (source == "exec" or originator == "codex_exec"):
            continue

        title = first_user_message or last_preview or session_id
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = None
        sessions.append(
            ResumeSession(
                session_id=session_id,
                title=title,
                cwd=cwd,
                updated_at=mtime,
                created_at=parse_iso_timestamp(meta.get("timestamp")),
                source=source,
                model=str(meta.get("model") or meta.get("model_provider") or ""),
                rollout_path=str(path),
                preview=last_preview,
            )
        )

    sessions.sort(key=lambda s: s.updated_at or 0, reverse=True)
    return sessions


def list_resume_sessions(workspace: Path, include_non_interactive: bool = False) -> List[ResumeSession]:
    codex_home = get_codex_home()
    sessions = load_resume_sessions_from_state(codex_home, workspace, include_non_interactive)
    if sessions:
        return sessions
    return load_resume_sessions_from_jsonl(codex_home, workspace, include_non_interactive)


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
        except OSError:
            continue
    return None


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


def load_codex_session_history(
    codex_home: Path,
    session_id: str,
    rollout_path: str | Path | None = None,
) -> List[CodexHistoryEntry]:
    """Load the readable conversation from one Codex rollout JSONL file."""
    path = Path(rollout_path).expanduser() if rollout_path else None
    if path is None or not path.is_file():
        path = find_rollout_path_for_session(codex_home, session_id)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"Codex rollout not found for session {session_id}")

    candidates: List[Tuple[int, str, str, CodexHistoryEntry]] = []
    current_turn_id = ""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f):
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
            if not category or not text:
                continue
            if category == "user" and is_internal_codex_history_message(text):
                continue
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            turn_id = str(metadata.get("turn_id") or "") if isinstance(metadata, dict) else ""
            candidates.append(
                (line_number, source, turn_id or current_turn_id, CodexHistoryEntry(category, text))
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
            if previous.text == entry.text and (same_turn or source != previous_source):
                continue
            if same_turn:
                history[-1] = (
                    previous_source,
                    previous_turn_id,
                    CodexHistoryEntry(entry.category, f"{previous.text}\n{entry.text}"),
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


def update_rollout_session_meta(
    rollout_path: Path,
    session_id: str,
    workspace: Path,
) -> Tuple[bool, bool]:
    """Rewrite a rollout's session_meta so fallback resume discovery sees it."""
    target_cwd = str(workspace)
    changed = False
    source_promoted = False
    tmp_path = rollout_path.with_name(f".{rollout_path.name}.pcr-tmp-{os.getpid()}")

    try:
        with rollout_path.open("r", encoding="utf-8", errors="replace") as src, tmp_path.open("w", encoding="utf-8") as dst:
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

                payload = obj.get("payload")
                if obj.get("type") == "session_meta" and isinstance(payload, dict):
                    if session_meta_thread_id(payload) == session_id:
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
                        if payload.get("source") == "exec":
                            payload["source"] = "cli"
                            changed = True
                            source_promoted = True
                        if payload.get("originator") == "codex_exec":
                            payload["originator"] = "codex-tui"
                            changed = True
                            source_promoted = True
                        if not payload.get("thread_source"):
                            payload["thread_source"] = "user"
                            changed = True
                        rewritten = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                dst.write(rewritten)

        if changed:
            try:
                shutil.copystat(rollout_path, tmp_path, follow_symlinks=False)
            except OSError:
                pass
            os.replace(tmp_path, rollout_path)
        else:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

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
                promotion.rollout_updated, rollout_source_promoted = update_rollout_session_meta(rollout_path, session_id, workspace)
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
                        imported_rollout_path = rollout_destination_for_import(source_rollout, source_codex_home, real_codex_home)
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
                copy_file_atomic(source_rollout, Path(promotion.rollout_path))
                promotion.rollout_updated, rollout_source_promoted = update_rollout_session_meta(Path(promotion.rollout_path), session_id, workspace)
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
    sessions = load_resume_sessions_from_state(codex_home, workspace, include_non_interactive=True)
    if not sessions:
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
    if isinstance(item, dict) and item.get("type") == "command_execution":
        for key in ("aggregated_output", "output", "stdout", "stderr"):
            item.pop(key, None)
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text


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
    with log_path.open("ab") as f:
        async for line in iter_stream_lines(reader):
            f.write(line)
            f.flush()

            if stream_name == "stdout":
                state.stdout_lines += 1
            else:
                state.stderr_lines += 1

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            obj: Any = None
            is_json = False
            try:
                obj = json.loads(text)
                is_json = True
            except json.JSONDecodeError:
                pass
            if progress_callback is not None:
                progress_text = agent_line_for_progress(text, obj) if is_json else text
                progress_callback({"type": "agent_line", "idx": state.idx, "stream": stream_name, "text": progress_text})
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
) -> AgentResult:
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
    rollout_monitor = ReasoningRolloutMonitor(codex_home, state, progress_callback)
    started = time.perf_counter()
    returncode: Optional[int] = None
    status = "failed"
    error: Optional[str] = None
    proc: Optional[asyncio.subprocess.Process] = None
    stdout_task: Optional[asyncio.Task[None]] = None
    stderr_task: Optional[asyncio.Task[None]] = None

    try:
        requested_status = requested_agent_stop_status(cancel_event, agent_cancel_event)
        if requested_status is not None:
            status = requested_status
            raise RuntimeError("__pcr_stopped_before_start__")
        if progress_callback is not None:
            progress_callback({"type": "agent_started", "idx": idx})
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        process_options: Dict[str, Any] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(agent_workspace),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_options,
        )

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

        next_rollout_poll = 0.0
        while True:
            now = time.monotonic()
            if now >= next_rollout_poll:
                rollout_monitor.poll()
                next_rollout_poll = now + 0.5
            requested_status = requested_agent_stop_status(cancel_event, agent_cancel_event)
            if requested_status is not None:
                status = requested_status
                returncode = await terminate_process(proc)
                break
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=0.2)
                break
            except asyncio.TimeoutError:
                continue
        await asyncio.gather(stdout_task, stderr_task)
        if status not in {"cancelled", "killed"}:
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
        scrub_codex_home_support_entries(codex_home)

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
        codex_thread_id=state.codex_thread_id,
        reasoning_tokens=state.reasoning_tokens,
        reasoning_token_values=compact_token_values(state.reasoning_values),
        reasoning_token_counts=dict(sorted(state.reasoning_token_counts.items())),
        error=error,
        stdout_tail=safe_tail(stdout_log),
        stderr_tail=safe_tail(stderr_log),
    )
    status_json.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_callback is not None:
        progress_callback({"type": "agent_finished", "idx": idx, "result": asdict(result)})
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
) -> List[AgentResult]:
    results: List[AgentResult] = []
    semaphore = asyncio.Semaphore(max_parallel)
    indices = list(agent_indices) if agent_indices is not None else list(range(1, n + 1))

    async def run_limited(idx: int) -> AgentResult:
        agent_cancel_event = (agent_cancel_events or {}).get(idx)

        async def execute() -> AgentResult:
            return await run_one_agent(
                idx=idx,
                agent_workspace=workspaces_root / f"agent_{idx:03d}",
                meta_dir=meta_root / f"agent_{idx:03d}",
                codex_home=codex_home_by_agent[idx],
                prompt=prompt,
                command=command_by_agent[idx],
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_event=agent_cancel_event,
            )

        if progress_callback is not None:
            progress_callback({"type": "agent_status", "idx": idx, "status": "queued"})
        while True:
            if cancel_requested(cancel_event):
                return await execute()
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
                break
            except asyncio.TimeoutError:
                continue
        try:
            # An individual kill applies only to a process that is already
            # running. Ignore stale requests made while this agent was queued.
            clear_agent_cancel = getattr(agent_cancel_event, "clear", None)
            if callable(clear_agent_cancel):
                clear_agent_cancel()
            return await execute()
        finally:
            semaphore.release()

    tasks = [asyncio.create_task(run_limited(idx)) for idx in indices]
    total = len(tasks)

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
    if not successes:
        return None

    if recommend_by == "reasoning_tokens":
        with_tokens = [r for r in successes if r.reasoning_tokens is not None]
        if not with_tokens:
            if warn_missing_tokens:
                logger.warning("所有成功 agent 的 reasoning_tokens 都是 N/A；回退为按最长时长选择。")
            return max(successes, key=lambda r: (r.seconds, -r.idx))
        return max(successes, key=lambda r: (reasoning_score(r), r.seconds, -r.idx))

    return max(successes, key=lambda r: (r.seconds, reasoning_score(r), -r.idx))


def result_sort_key(result: AgentResult, recommend_by: str) -> Tuple[bool, float, int, int]:
    if recommend_by == "reasoning_tokens":
        return (
            result.status != "success",
            -float(reasoning_score(result)),
            -int(result.seconds * 1000),
            result.idx,
        )
    return (
        result.status != "success",
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
                f"[{status_style}]{r.status}[/{status_style}]",
                "-" if r.returncode is None else str(r.returncode),
                f"{r.seconds:.2f}",
                rtok,
                values,
            )
        return table

    lines = [f"Codex parallel run summary (recommend_by={recommend_by})"]
    lines.append("rank best agent status ret seconds rtok_max rtok_values workspace")
    for rank, r in enumerate(rows, 1):
        rtok = "-" if r.reasoning_tokens is None else str(r.reasoning_tokens)
        values = "-" if not r.reasoning_token_values else ",".join(map(str, r.reasoning_token_values))
        lines.append(
            f"{rank:>4} {('*' if r.idx == best_idx else ''):^4} {r.idx:>5} {r.status:<8} {str(r.returncode):>4} "
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
        "results": [asdict(r) for r in sorted(results, key=lambda x: x.idx)],
    }
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
            f"agent_{r.idx:03d}\tstatus={r.status}\tseconds={r.seconds:.2f}\t"
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
) -> None:
    recorded = _load_recorded_results(run_root)
    for result in new_results:
        recorded[result.idx] = result
    results = list(recorded.values())
    best = select_best_result(
        [result for result in results if result.status == "success"],
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

    write_run_files(
        run_root=run_root,
        workspace=workspace,
        prompt=prompt,
        results=results,
        best=best,
        recommend_by=recommend_by,
        synced=bool(summary.get("synced_back_to_workspace")),
        workspaces_deleted=False,
        resume_session=resume_session,
        codex_session_promotion=promotion,
    )


def _archive_retry_metadata(run_root: Path, meta_dir: Path, idx: int) -> None:
    if not meta_dir.exists():
        return
    scrub_codex_home_support_entries(meta_dir / "codex_home")
    retry_root = run_root / "retry_history" / f"agent_{idx:03d}"
    retry_root.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.move(str(meta_dir), str(retry_root / stamp))


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
) -> List[AgentResult]:
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
    help_text = (
        read_codex_exec_resume_help(args.codex_bin)
        if resume_session_id
        else read_codex_exec_help(args.codex_bin)
    )
    real_codex_home = get_codex_home()
    effective_effort = resolve_codex_reasoning_effort(
        getattr(args, "model", None),
        getattr(args, "effort", None),
        real_codex_home,
    )
    command_by_agent: Dict[int, List[str]] = {}
    codex_home_by_agent: Dict[int, Path] = {}
    prepared: List[int] = []
    touched: List[int] = []

    try:
        for idx in indices:
            if cancel_requested(cancel_event):
                break
            agent_workspace = workspaces_root / f"agent_{idx:03d}"
            agent_meta_dir = meta_root / f"agent_{idx:03d}"
            if idx in retries:
                cleanup_workspace_copy(workspace, agent_workspace)
                _archive_retry_metadata(run_root, agent_meta_dir, idx)
            elif agent_workspace.exists() or agent_workspace.is_symlink():
                raise FileExistsError(f"agent workspace already exists: {agent_workspace}")
            touched.append(idx)

            if progress_callback is not None:
                progress_callback({"type": "agent_status", "idx": idx, "status": "copying"})
            copy_workspace(workspace, agent_workspace, run_base=run_root.parent)
            agent_codex_home = agent_meta_dir / "codex_home"
            prepare_agent_codex_home(
                real_codex_home,
                agent_codex_home,
                agent_workspace,
                resume_session_id,
            )
            command, _caps = build_codex_command(
                args.codex_bin,
                help_text,
                agent_meta_dir / "final_message.md",
                model=args.model,
                effort=effective_effort,
                resume_session_id=resume_session_id,
            )
            command_by_agent[idx] = command
            codex_home_by_agent[idx] = agent_codex_home
            prepared.append(idx)
    except BaseException:
        for idx in touched:
            scrub_codex_home_support_entries(meta_root / f"agent_{idx:03d}" / "codex_home")
            cleanup_workspace_copy(workspace, workspaces_root / f"agent_{idx:03d}")
        raise

    if cancel_requested(cancel_event) or not prepared:
        for idx in prepared:
            scrub_codex_home_support_entries(codex_home_by_agent[idx])
        return []

    max_parallel = 1 if args.serial else (args.max_parallel or len(prepared))
    max_parallel = min(max_parallel, len(prepared))
    try:
        results = asyncio.run(
            run_all_agents(
                n=len(prepared),
                workspaces_root=workspaces_root,
                meta_root=meta_root,
                prompt=prompt,
                command_by_agent=command_by_agent,
                codex_home_by_agent=codex_home_by_agent,
                max_parallel=max_parallel,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                agent_cancel_events=agent_cancel_events,
                agent_indices=prepared,
            )
        )
    finally:
        for idx in prepared:
            scrub_codex_home_support_entries(codex_home_by_agent[idx])
    refresh_run_result_files(
        run_root=run_root,
        workspace=workspace,
        prompt=prompt,
        new_results=results,
        recommend_by=args.recommend_by,
    )
    return results


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run N isolated Codex agents in parallel on full copies of a workspace, then sync "
            "the selected successful result back. With no prompt on an interactive terminal, "
            "opens the PCR TUI."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt text. Omit it in a TTY to open the interactive TUI.")
    parser.add_argument("-n", "--num-agents", type=int, default=5, help="Number of Codex agents to run.")
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        help="Maximum Codex agents to run concurrently. Defaults to --num-agents.",
    )
    parser.add_argument("--serial", action="store_true", help="Run agents one at a time, equivalent to --max-parallel 1.")
    parser.add_argument(
        "--recommend-by",
        dest="recommend_by",
        type=normalize_recommend_by,
        default="reasoning_tokens",
        metavar="{duration,reasoning_tokens}",
        help="Final candidate selection strategy: duration chooses longest successful run; reasoning_tokens chooses max observed reasoning tokens.",
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
    parser.add_argument("--no-sync-back", action="store_true", help="Do not copy the selected best workspace back to the original workspace.")
    parser.add_argument("--keep-workspaces", action="store_true", help="Keep isolated candidate workspaces after the run.")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.num_agents <= 0:
        raise SystemExit("-n / --num-agents 必须大于 0。")
    if args.max_parallel is not None and args.max_parallel <= 0:
        raise SystemExit("--max-parallel 必须大于 0。")
    if args.serial and args.max_parallel not in (None, 1):
        raise SystemExit("--serial 不能和 --max-parallel > 1 同时使用。")
    effort = str(getattr(args, "effort", None) or "").strip().lower()
    args.effort = (
        None
        if effort in {"", "auto", "clear", "default", "none"}
        else effort
    )
    resume_requested = bool(
        getattr(args, "resume", False)
        or getattr(args, "resume_session_id", None)
    )
    if args.effort and (getattr(args, "model", None) or not resume_requested):
        resolve_codex_reasoning_effort(
            getattr(args, "model", None),
            args.effort,
        )


def should_start_tui(args: argparse.Namespace) -> bool:
    return args.prompt is None and args.prompt_file is None and sys.stdin.isatty()


def run_once(
    args: argparse.Namespace,
    prompt: str,
    progress_callback: ProgressCallback = None,
    print_output: bool = True,
) -> int:
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
    effective_effort = resolve_codex_reasoning_effort(
        effort_model,
        getattr(args, "effort", None),
    )

    module_dir = Path(__file__).resolve().parent
    run_anchor = default_run_anchor(module_dir, workspace)
    run_base = choose_run_base(run_anchor, workspace, args.runs_dir)
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
                    ["MODULE_DIR", absolute_path_for_display(module_dir)],
                    ["RUN_ANCHOR", absolute_path_for_display(run_anchor)],
                    ["RUNS_ROOT", absolute_path_for_display(run_root)],
                    ["AGENTS", str(args.num_agents)],
                    ["EXECUTION", "serial" if max_parallel == 1 else "parallel"],
                    ["MAX_PARALLEL", str(max_parallel)],
                    ["RECOMMEND_BY", args.recommend_by],
                    ["MODEL", args.model or "default"],
                    ["EFFORT", effective_effort or "default"],
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
        overview.add_row("MODULE_DIR", absolute_path_for_display(module_dir))
        overview.add_row("RUN_ANCHOR", absolute_path_for_display(run_anchor))
        overview.add_row("RUNS_ROOT", absolute_path_for_display(run_root))
        overview.add_row("AGENTS", str(args.num_agents))
        overview.add_row("EXECUTION", "serial" if max_parallel == 1 else "parallel")
        overview.add_row("MAX_PARALLEL", str(max_parallel))
        overview.add_row("RECOMMEND_BY", args.recommend_by)
        overview.add_row("MODEL", args.model or "default")
        overview.add_row("EFFORT", effective_effort or "default")
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
        log("info", "module_dir = {}", module_dir)
        log("info", "run_anchor = {}", run_anchor)
        log("info", "runs_root = {}", run_root)
        log("info", "agents = {}", args.num_agents)
        log("info", "execution = {}", "serial" if max_parallel == 1 else "parallel")
        log("info", "max_parallel = {}", max_parallel)
        log("info", "recommend_by = {}", args.recommend_by)
        log("info", "model = {}", args.model or "default")
        log("info", "effort = {}", effective_effort or "default")
        log("info", "resume = {}", resume_session_id or "NO")

    help_text = read_codex_exec_resume_help(args.codex_bin) if resume_session_id else read_codex_exec_help(args.codex_bin)
    real_codex_home = get_codex_home()
    if external_cancel_event is None:
        restore_cancel_signals = install_cancel_signal_handlers(cancel_event)

    log("info", "copying workspace into {} isolated agent folders", args.num_agents)
    if HAS_RICH and progress_callback is None:
        assert Progress is not None
        iterable_cm = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        iterable = None
    elif HAS_TQDM and progress_callback is None:
        iterable = tqdm(range(1, args.num_agents + 1), desc="copy workspace", unit="agent")
    else:
        iterable = range(1, args.num_agents + 1)

    command_by_agent: Dict[int, List[str]] = {}
    caps_by_agent: Dict[int, Dict[str, bool]] = {}
    codex_home_by_agent: Dict[int, Path] = {}

    def cleanup_unfinished_run() -> None:
        scrub_agent_codex_homes(meta_root)
        if not args.keep_workspaces and not is_relative_to(workspaces_root, workspace):
            cleanup_workspace_copies(workspace, workspaces_root)

    try:
        if HAS_RICH and progress_callback is None:
            with iterable_cm as progress:
                task_id = progress.add_task("copy workspace", total=args.num_agents)
                for idx in range(1, args.num_agents + 1):
                    if cancel_requested(cancel_event):
                        break
                    progress.update(task_id, description=f"copy agent_{idx:03d}")
                    agent_workspace = workspaces_root / f"agent_{idx:03d}"
                    agent_meta_dir = meta_root / f"agent_{idx:03d}"
                    agent_codex_home = agent_meta_dir / "codex_home"
                    copy_workspace(workspace, agent_workspace, run_base=run_base)
                    prepare_agent_codex_home(real_codex_home, agent_codex_home, agent_workspace, resume_session_id)
                    final_message_path = agent_meta_dir / "final_message.md"
                    cmd, caps = build_codex_command(
                        args.codex_bin,
                        help_text,
                        final_message_path,
                        model=args.model,
                        effort=effective_effort,
                        resume_session_id=resume_session_id,
                    )
                    command_by_agent[idx] = cmd
                    caps_by_agent[idx] = caps
                    codex_home_by_agent[idx] = agent_codex_home
                    progress.update(task_id, advance=1)
        else:
            assert iterable is not None
            for idx in iterable:
                if cancel_requested(cancel_event):
                    break
                if progress_callback is not None:
                    progress_callback({"type": "agent_status", "idx": idx, "status": "copying"})
                agent_workspace = workspaces_root / f"agent_{idx:03d}"
                agent_meta_dir = meta_root / f"agent_{idx:03d}"
                agent_codex_home = agent_meta_dir / "codex_home"
                copy_workspace(workspace, agent_workspace, run_base=run_base)
                prepare_agent_codex_home(real_codex_home, agent_codex_home, agent_workspace, resume_session_id)
                final_message_path = agent_meta_dir / "final_message.md"
                cmd, caps = build_codex_command(
                    args.codex_bin,
                    help_text,
                    final_message_path,
                    model=args.model,
                    effort=effective_effort,
                    resume_session_id=resume_session_id,
                )
                command_by_agent[idx] = cmd
                caps_by_agent[idx] = caps
                codex_home_by_agent[idx] = agent_codex_home
    except BaseException:
        cleanup_unfinished_run()
        restore_cancel_signals()
        if progress_callback is not None:
            progress_callback({"type": "run_failed", "message": "workspace preparation failed"})
        raise

    if cancel_requested(cancel_event):
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

    (run_root / "codex_capabilities.json").write_text(json.dumps(caps_by_agent[1], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "sample_command.json").write_text(json.dumps(command_by_agent[1], ensure_ascii=False, indent=2), encoding="utf-8")
    if resume_session is not None:
        (run_root / "resume_session.json").write_text(json.dumps(asdict(resume_session), ensure_ascii=False, indent=2), encoding="utf-8")

    caps = caps_by_agent[1]
    if not caps.get("json", False):
        log("warning", "当前 Codex CLI help 中未检测到 --json；reasoning_tokens 可能无法观测，将显示为 N/A。")
    if not (caps.get("dangerously_bypass") or caps.get("sandbox")):
        log("warning", "当前 Codex CLI help 中未检测到全权限相关参数；将按 CLI 默认权限运行。")
    log(
        "info",
        "starting {} codex agents with max_parallel={}",
        args.num_agents,
        max_parallel,
    )
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
            )
        )
    except BaseException:
        cleanup_unfinished_run()
        restore_cancel_signals()
        if progress_callback is not None:
            progress_callback({"type": "run_failed", "message": "agent execution failed"})
        raise

    scrub_agent_codex_homes(meta_root)

    cancelled = cancel_requested(cancel_event)
    successes = [] if cancelled else [r for r in results if r.status == "success"]
    best = select_best_result(successes, args.recommend_by)

    synced = False
    codex_session_promotion: Optional[CodexSessionPromotion] = None
    if cancelled:
        log("warning", "run cancelled; original workspace was not modified")
    elif best is not None and not args.no_sync_back:
        log("info", "syncing selected workspace back to original workspace")
        sync_best_workspace_back(Path(best.workspace_dir), workspace)
        synced = True
        log("info", "sync complete: {} -> {}", best.workspace_dir, workspace)
        try:
            codex_session_promotion = promote_best_codex_session_to_workspace(best, workspace)
            if codex_session_promotion is None:
                log("warning", "could not identify a Codex session id for the selected agent; --resume may not show this run.")
            elif codex_session_promotion.error:
                log("warning", "Codex session promotion partially failed: {}", codex_session_promotion.error)
            else:
                log("info", "Codex session {} is now resumable from {}", codex_session_promotion.session_id, workspace)
        except Exception as exc:  # noqa: BLE001
            log("warning", "Codex session promotion failed: {}", exc)
    elif best is not None and args.no_sync_back:
        log("warning", "--no-sync-back set; original workspace was not modified")
    else:
        log("error", "no successful agent; original workspace was not modified")

    workspaces_deleted = False
    if not args.keep_workspaces:
        # Safety check before recursive delete.
        if is_relative_to(workspaces_root, workspace):
            raise SystemExit(f"拒绝删除：workspaces_root 位于 workspace 内部：{workspaces_root}")
        cleanup_workspace_copies(workspace, workspaces_root)
        workspaces_deleted = not workspaces_root.exists()

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
    )
    if progress_callback is not None:
        progress_callback(
            {
                "type": "run_finished",
                "run_root": str(run_root),
                "best_agent": best.idx if best else None,
                "success": best is not None,
                "synced": synced,
                "cancelled": cancelled,
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

    restore_cancel_signals()
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
