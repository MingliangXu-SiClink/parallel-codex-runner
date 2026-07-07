from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .paths import is_relative_to


EXCLUDE_NAMES: Set[str] = {".codex_parallel_runs", ".codex_parallel_meta"}
SYNC_EXCLUDE_NAMES: Set[str] = EXCLUDE_NAMES | {".git"}


def _debug(message: str, *args: object) -> None:
    try:
        from .app import logger
    except Exception:
        return
    logger.debug(message, *args)


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


def git_workspace_toplevel(workspace: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None

    top = Path(result.stdout.strip()).resolve()
    if top != workspace.resolve():
        return None

    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return top if head.returncode == 0 else None


def git_worktree_paths(original_workspace: Path) -> List[Path]:
    if git_workspace_toplevel(original_workspace) is None:
        return []
    result = subprocess.run(
        ["git", "-C", str(original_workspace), "worktree", "list", "--porcelain"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    paths: List[Path] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.removeprefix("worktree ")).resolve())
    return paths


def prune_git_worktrees(original_workspace: Path) -> None:
    if git_workspace_toplevel(original_workspace) is None:
        return
    subprocess.run(
        ["git", "-C", str(original_workspace), "worktree", "prune", "--expire", "now"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def cleanup_workspace_copy(original_workspace: Path, workspace_copy: Path) -> None:
    if not workspace_copy.exists() and not workspace_copy.is_symlink():
        return
    if (workspace_copy / ".git").is_file() and git_workspace_toplevel(original_workspace) is not None:
        result = subprocess.run(
            ["git", "-C", str(original_workspace), "worktree", "remove", "--force", "--force", str(workspace_copy)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
    shutil.rmtree(workspace_copy, ignore_errors=True)
    prune_git_worktrees(original_workspace)


def cleanup_workspace_copies(original_workspace: Path, workspaces_root: Path) -> None:
    root = workspaces_root.resolve()
    original_top = git_workspace_toplevel(original_workspace)
    if original_top is not None:
        for path in git_worktree_paths(original_workspace):
            if path != original_top and (path == root or is_relative_to(path, root)):
                cleanup_workspace_copy(original_workspace, path)
    if workspaces_root.exists():
        for child in workspaces_root.iterdir():
            cleanup_workspace_copy(original_workspace, child)
        shutil.rmtree(workspaces_root, ignore_errors=True)
    prune_git_worktrees(original_workspace)


def copy_workspace_with_git_worktree(workspace: Path, dst: Path) -> bool:
    if git_workspace_toplevel(workspace) is None:
        return False

    result = subprocess.run(
        ["git", "-C", str(workspace), "worktree", "add", "--detach", str(dst), "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        cleanup_workspace_copy(workspace, dst)
        return False

    try:
        sync_back_with_python(workspace, dst)
    except Exception as exc:
        _debug("git worktree copy failed, falling back to plain copy: {}", exc)
        cleanup_workspace_copy(workspace, dst)
        raise
    return True


def copy_workspace(workspace: Path, dst: Path, run_base: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        if copy_workspace_with_git_worktree(workspace, dst):
            return
    except Exception:
        cleanup_workspace_copy(workspace, dst)

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
    src = src.resolve()
    dst = dst.resolve()

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
