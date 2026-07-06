#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parallel_codex_runner.py

A deliberately simple parallel Codex runner.

Command examples:
    # After installing this package, run on the current workspace.
    pcr "fix the failing tests" -n 20

    # Run 20 agents on the current directory, select the successful run with
    # the highest observed reasoning tokens by default, and sync it back.
    python3 parallel_codex_runner.py "fix the failing tests" -n 20

    # Select the longest successful run instead of selecting by reasoning tokens.
    python3 parallel_codex_runner.py "fix the failing tests" -n 20 --best-by duration

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
3. Run one `codex exec -` inside each copied workspace, concurrently by default.
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
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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

# Runner-private names. These are excluded from copy/sync so they never become
# part of a normal Codex workspace result.
EXCLUDE_NAMES: Set[str] = {".codex_parallel_runs", ".codex_parallel_meta"}

# The candidate workspaces may include .git so Codex can inspect repository
# context, but syncing .git back would overwrite the user's original repo state.
SYNC_EXCLUDE_NAMES: Set[str] = EXCLUDE_NAMES | {".git"}


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------


@dataclass
class AgentResult:
    idx: int
    workspace_dir: str
    meta_dir: str
    stdout_log: str
    stderr_log: str
    final_message: str
    command: List[str]
    returncode: Optional[int]
    status: str
    seconds: float
    reasoning_tokens: Optional[int] = None
    reasoning_token_values: List[int] = field(default_factory=list)
    error: Optional[str] = None
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass
class AgentState:
    idx: int
    reasoning_values: List[int] = field(default_factory=list)
    json_events: int = 0
    stdout_lines: int = 0
    stderr_lines: int = 0

    @property
    def reasoning_tokens(self) -> Optional[int]:
        if not self.reasoning_values:
            return None
        # Codex may emit per-turn or cumulative values. The maximum observed
        # value is the most stable scalar summary for a run.
        return max(self.reasoning_values)


# -----------------------------------------------------------------------------
# Path utilities
# -----------------------------------------------------------------------------


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def absolute_path_for_display(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path.absolute())


def safe_tail(path: Path, max_chars: int = 5000) -> str:
    try:
        if not path.exists():
            return ""
        data = path.read_bytes()
        if len(data) > max_chars:
            data = data[-max_chars:]
        return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def choose_run_base(default_anchor: Path, workspace: Path, explicit_runs_dir: Optional[str]) -> Path:
    """Return a run-base directory that is guaranteed not to be inside workspace.

    Default preference is the provided anchor directory:
        <default_anchor>/.codex_parallel_runs

    However, if that anchor itself is inside the workspace, putting run artifacts
    there would violate the central invariant. In that case, walk upward until
    the selected parent is outside workspace, then create .codex_parallel_runs
    there.
    """
    workspace = workspace.resolve()

    if explicit_runs_dir:
        run_base = Path(explicit_runs_dir).expanduser().resolve()
        if is_relative_to(run_base, workspace):
            raise SystemExit(
                f"--runs-dir 不能位于 workspace 内部：\n"
                f"  runs_dir = {run_base}\n"
                f"  workspace = {workspace}"
            )
        return run_base

    parent = default_anchor.resolve()
    run_base = parent / ".codex_parallel_runs"
    if not is_relative_to(run_base, workspace):
        return run_base

    # Anchor lives inside workspace. Move run_base to the nearest ancestor that
    # is outside workspace, usually workspace.parent/.codex_parallel_runs.
    while is_relative_to(parent, workspace):
        if parent.parent == parent:
            raise SystemExit("无法找到 workspace 外部的运行目录。请显式指定 --runs-dir。")
        parent = parent.parent

    return parent / ".codex_parallel_runs"


def create_unique_run_root(run_base: Path, timestamp: Optional[str] = None) -> Path:
    name = timestamp or _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_base.mkdir(parents=True, exist_ok=True)
    for attempt in range(1000):
        suffix = "" if attempt == 0 else f"_{attempt:03d}"
        run_root = run_base / f"{name}{suffix}"
        try:
            run_root.mkdir(exist_ok=False)
            return run_root
        except FileExistsError:
            continue
    raise SystemExit(f"无法创建唯一运行目录：{run_base / name}")


def is_site_package_dir(path: Path) -> bool:
    return any(part in {"site-packages", "dist-packages"} for part in path.resolve().parts)


def default_run_anchor(module_dir: Path, workspace: Path) -> Path:
    if is_site_package_dir(module_dir):
        return workspace
    return module_dir


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
# Codex CLI detection / command construction
# -----------------------------------------------------------------------------


def read_codex_exec_help(codex_bin: str) -> str:
    try:
        completed = subprocess.run(
            [codex_bin, "exec", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"找不到 codex 命令：{codex_bin!r}。请确认 Codex CLI 已安装且在 PATH 中。") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("执行 `codex exec --help` 超时。") from exc

    help_text = completed.stdout or ""
    if completed.returncode not in (0, None):
        logger.warning("`codex exec --help` returned code {}", completed.returncode)
    return help_text


def flag_supported(help_text: str, flag: str) -> bool:
    return flag in help_text


def short_flag_supported(help_text: str, flag: str) -> bool:
    return any(token in help_text for token in (f"{flag},", f"{flag} ", f"{flag}\t"))


def build_codex_command(
    codex_bin: str,
    help_text: str,
    final_message_path: Path,
    model: Optional[str] = None,
) -> Tuple[List[str], Dict[str, bool]]:
    """Build a conservative, version-adaptive `codex exec` command.

    No --search, no --cd, no fragile positional prompt.
    The subprocess cwd is the agent workspace.
    The prompt is sent through stdin via the final '-' argument.
    """
    cmd: List[str] = [codex_bin, "exec"]
    caps: Dict[str, bool] = {}

    caps["json"] = flag_supported(help_text, "--json")
    if caps["json"]:
        cmd.append("--json")

    caps["output_last_message"] = flag_supported(help_text, "--output-last-message")
    if caps["output_last_message"]:
        cmd.extend(["--output-last-message", str(final_message_path)])

    model_flag = "--model" if flag_supported(help_text, "--model") else "-m" if short_flag_supported(help_text, "-m") else None
    caps["model"] = model_flag is not None
    if model:
        if caps["model"]:
            assert model_flag is not None
            cmd.extend([model_flag, model])
        else:
            logger.warning("当前 Codex CLI help 中未检测到 --model；忽略 --model {}", model)

    caps["dangerously_bypass"] = flag_supported(help_text, "--dangerously-bypass-approvals-and-sandbox")
    caps["sandbox"] = flag_supported(help_text, "--sandbox")
    caps["ask_for_approval"] = flag_supported(help_text, "--ask-for-approval")
    caps["skip_git_repo_check"] = flag_supported(help_text, "--skip-git-repo-check")

    if caps["dangerously_bypass"]:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        if caps["sandbox"]:
            cmd.extend(["--sandbox", "danger-full-access"])
        if caps["ask_for_approval"]:
            cmd.extend(["--ask-for-approval", "never"])

    if caps["skip_git_repo_check"]:
        cmd.append("--skip-git-repo-check")

    cmd.append("-")
    return cmd, caps


# -----------------------------------------------------------------------------
# Workspace copy/sync
# -----------------------------------------------------------------------------


def make_ignore_func(extra_excluded_abs: Sequence[Path]):
    resolved_extra = [p.resolve() for p in extra_excluded_abs]

    def ignore(src_dir: str, names: List[str]) -> Set[str]:
        ignored: Set[str] = set()
        src = Path(src_dir)
        for name in names:
            if name in EXCLUDE_NAMES:
                ignored.add(name)
                continue
            candidate = src / name
            try:
                rp = candidate.resolve()
            except FileNotFoundError:
                rp = candidate.absolute()
            for excluded in resolved_extra:
                if rp == excluded or is_relative_to(rp, excluded):
                    ignored.add(name)
                    break
        return ignored

    return ignore


def copy_workspace(workspace: Path, dst: Path, run_base: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        workspace,
        dst,
        symlinks=True,
        ignore=make_ignore_func([run_base]),
        copy_function=shutil.copy2,
    )


def _rsync_available() -> bool:
    return shutil.which("rsync") is not None


def sync_back_with_rsync(src: Path, dst: Path) -> None:
    cmd = [
        "rsync",
        "-a",
        "--delete",
    ]
    for name in sorted(SYNC_EXCLUDE_NAMES):
        cmd.extend(["--exclude", name])
    cmd.extend([f"{src.resolve()}/", f"{dst.resolve()}/"])
    subprocess.run(cmd, check=True)


def should_skip_rel(path: Path, excluded_names: Set[str] = EXCLUDE_NAMES) -> bool:
    return any(part in excluded_names for part in path.parts)


def remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def sync_back_with_python(src: Path, dst: Path) -> None:
    """Python fallback for rsync -a --delete, excluding EXCLUDE_NAMES."""
    src = src.resolve()
    dst = dst.resolve()

    # Delete files/dirs that exist in dst but not in src.
    for root, dirs, files in os.walk(dst, topdown=False):
        root_p = Path(root)
        rel_root = root_p.relative_to(dst)
        if should_skip_rel(rel_root, SYNC_EXCLUDE_NAMES):
            continue

        for fname in files:
            rel = rel_root / fname
            if should_skip_rel(rel, SYNC_EXCLUDE_NAMES):
                continue
            src_equiv = src / rel
            dst_equiv = dst / rel
            if not src_equiv.exists() and not src_equiv.is_symlink():
                dst_equiv.unlink(missing_ok=True)

        for dname in dirs:
            rel = rel_root / dname
            if should_skip_rel(rel, SYNC_EXCLUDE_NAMES):
                continue
            src_equiv = src / rel
            dst_equiv = dst / rel
            if not src_equiv.exists() and not src_equiv.is_symlink():
                remove_existing_path(dst_equiv)

    # Copy/update everything from src to dst.
    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        rel_root = root_p.relative_to(src)
        if should_skip_rel(rel_root, SYNC_EXCLUDE_NAMES):
            dirs[:] = []
            continue

        target_root = dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for dname in list(dirs):
            rel = rel_root / dname
            if should_skip_rel(rel, SYNC_EXCLUDE_NAMES):
                dirs.remove(dname)
                continue
            src_dir = src / rel
            dst_dir = dst / rel
            if src_dir.is_symlink():
                if dst_dir.exists() or dst_dir.is_symlink():
                    remove_existing_path(dst_dir)
                dst_dir.symlink_to(os.readlink(src_dir))
                dirs.remove(dname)
            else:
                if dst_dir.is_symlink() or (dst_dir.exists() and not dst_dir.is_dir()):
                    remove_existing_path(dst_dir)
                dst_dir.mkdir(parents=True, exist_ok=True)

        for fname in files:
            rel = rel_root / fname
            if should_skip_rel(rel, SYNC_EXCLUDE_NAMES):
                continue
            src_file = src / rel
            dst_file = dst / rel
            if src_file.is_symlink():
                if dst_file.exists() or dst_file.is_symlink():
                    remove_existing_path(dst_file)
                dst_file.symlink_to(os.readlink(src_file))
            else:
                if dst_file.is_symlink() or (dst_file.exists() and dst_file.is_dir()):
                    remove_existing_path(dst_file)
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)


def sync_best_workspace_back(src: Path, dst: Path) -> None:
    if _rsync_available():
        sync_back_with_rsync(src, dst)
    else:
        sync_back_with_python(src, dst)


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


def compact_token_values(values: Iterable[int], limit: int = 16) -> List[int]:
    seen: List[int] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    if len(seen) <= limit:
        return seen
    # Preserve early structure and final maximum/cumulative tail.
    return seen[: limit - 1] + [seen[-1]]


async def stream_to_log(
    reader: Optional[asyncio.StreamReader],
    log_path: Path,
    state: AgentState,
    stream_name: str,
) -> None:
    if reader is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as f:
        while True:
            line = await reader.readline()
            if not line:
                break
            f.write(line)
            f.flush()

            if stream_name == "stdout":
                state.stdout_lines += 1
            else:
                state.stderr_lines += 1

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            state.json_events += 1
            vals = extract_reasoning_tokens_from_json(obj)
            if vals:
                state.reasoning_values.extend(vals)


# -----------------------------------------------------------------------------
# Agent execution
# -----------------------------------------------------------------------------


async def run_one_agent(
    idx: int,
    agent_workspace: Path,
    meta_dir: Path,
    prompt: str,
    command: List[str],
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
                "command": command,
                "prompt_transport": "stdin",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state = AgentState(idx=idx)
    started = time.perf_counter()
    returncode: Optional[int] = None
    status = "failed"
    error: Optional[str] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(agent_workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        stdout_task = asyncio.create_task(stream_to_log(proc.stdout, stdout_log, state, "stdout"))
        stderr_task = asyncio.create_task(stream_to_log(proc.stderr, stderr_log, state, "stderr"))

        returncode = await proc.wait()
        await asyncio.gather(stdout_task, stderr_task)
        status = "success" if returncode == 0 else "failed"
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        status = "error"
        logger.error("agent_{:03d} error: {}", idx, error)

    seconds = time.perf_counter() - started
    result = AgentResult(
        idx=idx,
        workspace_dir=str(agent_workspace),
        meta_dir=str(meta_dir),
        stdout_log=str(stdout_log),
        stderr_log=str(stderr_log),
        final_message=str(final_message),
        command=command,
        returncode=returncode,
        status=status,
        seconds=seconds,
        reasoning_tokens=state.reasoning_tokens,
        reasoning_token_values=compact_token_values(state.reasoning_values),
        error=error,
        stdout_tail=safe_tail(stdout_log),
        stderr_tail=safe_tail(stderr_log),
    )
    status_json.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def run_all_agents(
    n: int,
    workspaces_root: Path,
    meta_root: Path,
    prompt: str,
    command_by_agent: Dict[int, List[str]],
    max_parallel: int,
) -> List[AgentResult]:
    results: List[AgentResult] = []
    semaphore = asyncio.Semaphore(max_parallel)

    async def run_limited(idx: int) -> AgentResult:
        async with semaphore:
            return await run_one_agent(
                idx=idx,
                agent_workspace=workspaces_root / f"agent_{idx:03d}",
                meta_dir=meta_root / f"agent_{idx:03d}",
                prompt=prompt,
                command=command_by_agent[idx],
            )

    tasks = [asyncio.create_task(run_limited(idx)) for idx in range(1, n + 1)]

    if HAS_RICH:
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
            task_id = progress.add_task(f"codex agents max_parallel={max_parallel}", total=n)
            for finished in asyncio.as_completed(tasks):
                res = await finished
                results.append(res)
                token_text = "NA" if res.reasoning_tokens is None else str(res.reasoning_tokens)
                progress.update(
                    task_id,
                    advance=1,
                    description=f"agent_{res.idx:03d} {res.status} rtok={token_text}",
                )
    elif HAS_TQDM:
        with tqdm(total=n, desc="codex agents", unit="agent") as bar:
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
            logger.info(
                "completed {}/{}: agent_{:03d} {} {:.2f}s",
                len(results),
                n,
                res.idx,
                res.status,
                res.seconds,
            )
    return results


# -----------------------------------------------------------------------------
# Best-result selection
# -----------------------------------------------------------------------------


BEST_BY_ALIASES = {
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


def normalize_best_by(value: str) -> str:
    key = value.strip().lower()
    try:
        return BEST_BY_ALIASES[key]
    except KeyError as exc:
        choices = ", ".join(sorted(BEST_BY_ALIASES))
        raise argparse.ArgumentTypeError(f"不支持的候选选择策略：{value!r}。可用值：{choices}") from exc


def reasoning_score(result: AgentResult) -> int:
    return result.reasoning_tokens if result.reasoning_tokens is not None else -1


def select_best_result(successes: List[AgentResult], best_by: str) -> Optional[AgentResult]:
    if not successes:
        return None

    if best_by == "reasoning_tokens":
        with_tokens = [r for r in successes if r.reasoning_tokens is not None]
        if not with_tokens:
            logger.warning("所有成功 agent 的 reasoning_tokens 都是 N/A；回退为按最长时长选择。")
            return max(successes, key=lambda r: (r.seconds, -r.idx))
        return max(successes, key=lambda r: (reasoning_score(r), r.seconds, -r.idx))

    return max(successes, key=lambda r: (r.seconds, reasoning_score(r), -r.idx))


def result_sort_key(result: AgentResult, best_by: str) -> Tuple[bool, float, int, int]:
    if best_by == "reasoning_tokens":
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
    best_by: str,
) -> Any:
    rows = sorted(results, key=lambda r: result_sort_key(r, best_by))
    best_idx = best.idx if best is not None else None
    if HAS_RICH:
        table = Table(
            title=f"Codex parallel run summary (best_by={best_by})",
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

    lines = [f"Codex parallel run summary (best_by={best_by})"]
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
    best_by: str,
    synced: bool,
) -> None:
    success_count = sum(1 for r in results if r.status == "success")
    if HAS_RICH:
        assert console is not None
        console.print(make_summary_table(results, run_root, best, best_by))
        if best is not None:
            selected = Table.grid(padding=(0, 2))
            selected.add_column(style="bold")
            selected.add_column()
            selected.add_row("runs_root", absolute_path_for_display(run_root))
            selected.add_row("success", f"{success_count}/{len(results)}")
            selected.add_row("BEST_BY", best_by)
            selected.add_row("BEST_AGENT", f"[bold green]agent_{best.idx:03d}[/bold green]")
            selected.add_row("BEST_SECONDS", f"{best.seconds:.2f}")
            selected.add_row("BEST_REASONING_TOKENS", str(best.reasoning_tokens if best.reasoning_tokens is not None else "N/A"))
            selected.add_row("FINAL_RESULT_WORKSPACE", absolute_path_for_display(workspace) if synced else "NO")
            selected.add_row("BEST_META", absolute_path_for_display(Path(best.meta_dir)))
            console.print(Panel(selected, title="Selected result", border_style="green"))
        else:
            console.print(
                Panel(
                    f"runs_root = {absolute_path_for_display(run_root)}\n"
                    f"success = 0/{len(results)}\n"
                    f"BEST_BY = {best_by}\n"
                    f"BEST_AGENT = \n"
                    f"NO_SUCCESSFUL_RUN = 1\n"
                    f"workspace was not modified",
                    title="No successful agent",
                    border_style="red",
                )
            )
    else:
        print(make_summary_table(results, run_root, best, best_by))
        print(f"runs_root={run_root}")
        print(f"success={success_count}/{len(results)}")
        print(f"BEST_BY={best_by}")
        if best is not None:
            print(f"BEST_AGENT=agent_{best.idx:03d}")
            print(f"BEST_SECONDS={best.seconds:.2f}")
            print(f"BEST_REASONING_TOKENS={best.reasoning_tokens if best.reasoning_tokens is not None else 'N/A'}")
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
    best_by: str,
    synced: bool,
    workspaces_deleted: bool,
) -> None:
    (run_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    summary = {
        "run_root": str(run_root),
        "workspace": str(workspace),
        "success": sum(1 for r in results if r.status == "success"),
        "total": len(results),
        "best_by": best_by,
        "best_agent": f"agent_{best.idx:03d}" if best else None,
        "best": asdict(best) if best else None,
        "synced_back_to_workspace": str(workspace) if synced else None,
        "workspaces_deleted": workspaces_deleted,
        "results": [asdict(r) for r in sorted(results, key=lambda x: x.idx)],
    }
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "BEST_AGENT.txt").write_text((f"agent_{best.idx:03d}" if best else "") + "\n", encoding="utf-8")
    (run_root / "FINAL_RESULT_WORKSPACE.txt").write_text((str(workspace) if synced else "") + "\n", encoding="utf-8")

    token_lines = []
    for r in sorted(results, key=lambda x: x.idx):
        token_lines.append(
            f"agent_{r.idx:03d}\tstatus={r.status}\tseconds={r.seconds:.2f}\t"
            f"reasoning_tokens={r.reasoning_tokens if r.reasoning_tokens is not None else 'N/A'}\t"
            f"values={','.join(map(str, r.reasoning_token_values)) if r.reasoning_token_values else 'N/A'}"
        )
    (run_root / "reasoning_tokens.tsv").write_text("\n".join(token_lines) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run N isolated Codex agents in parallel on full copies of a workspace, then sync "
            "the selected successful result back."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt text. Prefer --prompt-file for long prompts.")
    parser.add_argument("-n", "--num-agents", type=int, default=20, help="Number of Codex agents to run.")
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        help="Maximum Codex agents to run concurrently. Defaults to --num-agents.",
    )
    parser.add_argument("--serial", action="store_true", help="Run agents one at a time, equivalent to --max-parallel 1.")
    parser.add_argument(
        "--best-by",
        "--candidate-by",
        dest="best_by",
        type=normalize_best_by,
        default="reasoning_tokens",
        metavar="{duration,reasoning_tokens}",
        help="Final candidate selection strategy: duration chooses longest successful run; reasoning_tokens chooses max observed reasoning tokens.",
    )
    parser.add_argument("--prompt-file", type=str, default=None, help="Read prompt from UTF-8 text file.")
    parser.add_argument("--workspace", type=str, default=None, help="Workspace to copy. Defaults to current directory.")
    parser.add_argument("--runs-dir", type=str, default=None, help="Directory for .codex_parallel_runs. Must not be inside workspace.")
    parser.add_argument("--codex-bin", type=str, default="codex", help="Codex CLI executable.")
    parser.add_argument("--model", type=str, default=None, help="Optional Codex model name if your CLI supports --model.")
    parser.add_argument("--no-sync-back", action="store_true", help="Do not copy the selected best workspace back to the original workspace.")
    parser.add_argument("--keep-workspaces", action="store_true", help="Keep isolated candidate workspaces after the run.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.num_agents <= 0:
        raise SystemExit("-n / --num-agents 必须大于 0。")
    if args.max_parallel is not None and args.max_parallel <= 0:
        raise SystemExit("--max-parallel 必须大于 0。")
    if args.serial and args.max_parallel not in (None, 1):
        raise SystemExit("--serial 不能和 --max-parallel > 1 同时使用。")

    max_parallel = 1 if args.serial else (args.max_parallel or args.num_agents)
    max_parallel = min(max_parallel, args.num_agents)

    prompt = read_prompt(args)
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise SystemExit(f"workspace 不存在或不是目录：{workspace}")

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

    if HAS_LOGURU:
        logger.remove()
        logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
        logger.add(run_root / "runner.log", level="DEBUG", encoding="utf-8", format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}")

    stale_workspace_runs = workspace / ".codex_parallel_runs"
    if stale_workspace_runs.exists():
        logger.warning(
            "workspace 内存在历史残留 .codex_parallel_runs，本脚本不会使用、不会复制、不会同步它：{}",
            stale_workspace_runs,
        )

    if HAS_RICH:
        assert console is not None
        overview = Table.grid(padding=(0, 2))
        overview.add_column(style="bold")
        overview.add_column()
        overview.add_row("workspace", absolute_path_for_display(workspace))
        overview.add_row("module_dir", absolute_path_for_display(module_dir))
        overview.add_row("run_anchor", absolute_path_for_display(run_anchor))
        overview.add_row("runs_root", absolute_path_for_display(run_root))
        overview.add_row("agents", str(args.num_agents))
        overview.add_row("execution", "serial" if max_parallel == 1 else "parallel")
        overview.add_row("max_parallel", str(max_parallel))
        overview.add_row("best_by", args.best_by)
        overview.add_row("metadata", absolute_path_for_display(meta_root))
        overview.add_row("workspace copies", absolute_path_for_display(workspaces_root))
        console.print(
            Panel(
                overview,
                title="codex runner",
                border_style="cyan",
            )
        )
    else:
        logger.info("workspace = {}", workspace)
        logger.info("module_dir = {}", module_dir)
        logger.info("run_anchor = {}", run_anchor)
        logger.info("runs_root = {}", run_root)
        logger.info("agents = {}", args.num_agents)
        logger.info("execution = {}", "serial" if max_parallel == 1 else "parallel")
        logger.info("max_parallel = {}", max_parallel)
        logger.info("best_by = {}", args.best_by)

    help_text = read_codex_exec_help(args.codex_bin)

    logger.info("copying workspace into {} isolated agent folders", args.num_agents)
    if HAS_RICH:
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
    elif HAS_TQDM:
        iterable = tqdm(range(1, args.num_agents + 1), desc="copy workspace", unit="agent")
    else:
        iterable = range(1, args.num_agents + 1)

    command_by_agent: Dict[int, List[str]] = {}
    caps_by_agent: Dict[int, Dict[str, bool]] = {}
    if HAS_RICH:
        with iterable_cm as progress:
            task_id = progress.add_task("copy workspace", total=args.num_agents)
            for idx in range(1, args.num_agents + 1):
                progress.update(task_id, description=f"copy agent_{idx:03d}")
                agent_workspace = workspaces_root / f"agent_{idx:03d}"
                copy_workspace(workspace, agent_workspace, run_base=run_base)
                final_message_path = meta_root / f"agent_{idx:03d}" / "final_message.md"
                cmd, caps = build_codex_command(args.codex_bin, help_text, final_message_path, model=args.model)
                command_by_agent[idx] = cmd
                caps_by_agent[idx] = caps
                progress.update(task_id, advance=1)
    else:
        assert iterable is not None
        for idx in iterable:
            agent_workspace = workspaces_root / f"agent_{idx:03d}"
            copy_workspace(workspace, agent_workspace, run_base=run_base)
            final_message_path = meta_root / f"agent_{idx:03d}" / "final_message.md"
            cmd, caps = build_codex_command(args.codex_bin, help_text, final_message_path, model=args.model)
            command_by_agent[idx] = cmd
            caps_by_agent[idx] = caps

    (run_root / "codex_capabilities.json").write_text(json.dumps(caps_by_agent[1], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "sample_command.json").write_text(json.dumps(command_by_agent[1], ensure_ascii=False, indent=2), encoding="utf-8")

    caps = caps_by_agent[1]
    if not caps.get("json", False):
        logger.warning("当前 Codex CLI help 中未检测到 --json；reasoning_tokens 可能无法观测，将显示为 N/A。")
    if not (caps.get("dangerously_bypass") or caps.get("sandbox")):
        logger.warning("当前 Codex CLI help 中未检测到全权限相关参数；将按 CLI 默认权限运行。")

    logger.info(
        "starting {} codex agents with max_parallel={}",
        args.num_agents,
        max_parallel,
    )
    results = asyncio.run(
        run_all_agents(
            args.num_agents,
            workspaces_root,
            meta_root,
            prompt,
            command_by_agent,
            max_parallel,
        )
    )

    successes = [r for r in results if r.status == "success"]
    best = select_best_result(successes, args.best_by)

    synced = False
    if best is not None and not args.no_sync_back:
        logger.info("syncing selected workspace back to original workspace")
        sync_best_workspace_back(Path(best.workspace_dir), workspace)
        synced = True
        logger.info("sync complete: {} -> {}", best.workspace_dir, workspace)
    elif best is not None and args.no_sync_back:
        logger.warning("--no-sync-back set; original workspace was not modified")
    else:
        logger.error("no successful agent; original workspace was not modified")

    workspaces_deleted = False
    if not args.keep_workspaces:
        # Safety check before recursive delete.
        if is_relative_to(workspaces_root, workspace):
            raise SystemExit(f"拒绝删除：workspaces_root 位于 workspace 内部：{workspaces_root}")
        shutil.rmtree(workspaces_root, ignore_errors=True)
        workspaces_deleted = not workspaces_root.exists()

    write_run_files(run_root, workspace, prompt, results, best, args.best_by, synced, workspaces_deleted)
    print_summary(results, workspace, run_root, best, args.best_by, synced)

    return 0 if best is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
