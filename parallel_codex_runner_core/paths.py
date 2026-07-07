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


def choose_run_base(default_anchor: Path, workspace: Path, explicit_runs_dir: Optional[str]) -> Path:
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
