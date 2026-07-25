from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional


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


RUNS_DIRECTORY_NAME = ".codex_parallel_runs"


def _path_device(path: Path) -> int:
    return int(path.stat().st_dev)


def filesystem_mount_root(path: Path) -> Path:
    """Return the highest ancestor that remains on path's filesystem."""
    current = path.expanduser().resolve()
    device = _path_device(current)
    while current.parent != current:
        parent = current.parent
        if _path_device(parent) != device:
            break
        current = parent
    return current


def choose_run_base(
    workspace: Path,
    explicit_runs_dir: Optional[str],
    *,
    system_home: Optional[Path] = None,
) -> Path:
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

    home = (system_home or Path.home()).expanduser().resolve()
    system_root = Path(home.anchor).resolve()
    try:
        workspace_device = _path_device(workspace)
        system_devices = {
            _path_device(home),
            _path_device(system_root),
        }
        anchor = (
            home
            if workspace_device in system_devices
            else filesystem_mount_root(workspace)
        )
    except OSError as exc:
        raise SystemExit(f"无法确定 workspace 所在磁盘：{workspace}\n  {exc}") from exc

    run_base = anchor / RUNS_DIRECTORY_NAME
    if is_relative_to(run_base, workspace):
        raise SystemExit(
            "默认运行目录不能位于 workspace 内部。请通过 --runs-dir "
            "指定 workspace 外部目录：\n"
            f"  runs_dir = {run_base}\n"
            f"  workspace = {workspace}"
        )
    return run_base


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
