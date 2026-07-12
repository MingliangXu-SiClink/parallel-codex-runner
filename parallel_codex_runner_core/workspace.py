from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Set, Tuple

from .paths import is_relative_to


EXCLUDE_NAMES: Set[str] = {".codex_parallel_runs", ".codex_parallel_meta", ".git"}
SYNC_EXCLUDE_NAMES: Set[str] = EXCLUDE_NAMES | {".git"}
WORKTREE_BASE_STATE_FILE = "pcr-base-state.json"
GIT_INDEX_LOCK_TIMEOUT_SECONDS = 5.0
GIT_INDEX_LOCK_POLL_SECONDS = 0.05
PCR_INDEX_LOCK_OWNER_SUFFIX = ".pcr-owner"


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


def _git_output(workspace: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _resolved_git_path(workspace: Path, *args: str) -> Optional[Path]:
    value = _git_output(workspace, "rev-parse", *args)
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _git_head(workspace: Path) -> Optional[str]:
    return _git_output(workspace, "rev-parse", "--verify", "HEAD")


def _git_head_ref(workspace: Path) -> Optional[str]:
    return _git_output(workspace, "symbolic-ref", "--quiet", "HEAD")


def _git_common_dir(workspace: Path) -> Optional[Path]:
    return _resolved_git_path(workspace, "--git-common-dir")


def _git_index_path(workspace: Path) -> Optional[Path]:
    return _resolved_git_path(workspace, "--git-path", "index")


def _git_shared_index_path(workspace: Path) -> Optional[Path]:
    return _resolved_git_path(workspace, "--shared-index-path")


def _worktree_base_state_path(workspace: Path) -> Optional[Path]:
    return _resolved_git_path(workspace, "--git-path", WORKTREE_BASE_STATE_FILE)


@contextmanager
def _prepared_git_index(
    source_workspace: Path,
    destination_workspace: Path,
) -> Iterator[Tuple[Path, Path]]:
    source_index = _git_index_path(source_workspace)
    destination_index = _git_index_path(destination_workspace)
    if source_index is None or destination_index is None or not source_index.is_file():
        raise RuntimeError("could not locate the Git index while preparing workspace state")

    destination_index.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcr-index-", dir=destination_index.parent) as tmp:
        staging_dir = Path(tmp)
        staged_index = staging_dir / "index"
        shutil.copy2(source_index, staged_index)
        shared_index = _git_shared_index_path(source_workspace)
        if shared_index is not None and shared_index.is_file():
            shutil.copy2(shared_index, staging_dir / shared_index.name)

        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(staged_index)
        # Strip path-specific index caches before installing the index in another worktree.
        result = subprocess.run(
            [
                "git",
                "-C",
                str(destination_workspace),
                "update-index",
                "--no-split-index",
                "--no-untracked-cache",
                "--no-fsmonitor",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
            raise RuntimeError(f"could not normalize the copied Git index: {message}")
        yield staged_index, destination_index


def _index_lock_owner_path(lock_path: Path) -> Path:
    return lock_path.with_name(f"{lock_path.name}{PCR_INDEX_LOCK_OWNER_SUFFIX}")


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _lock_identity(stat_result: os.stat_result) -> Tuple[int, int, int]:
    ctime_ns = getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1_000_000_000))
    return int(stat_result.st_dev), int(stat_result.st_ino), int(ctime_ns)


def _write_index_lock_owner(lock_path: Path) -> None:
    owner_path = _index_lock_owner_path(lock_path)
    temporary = owner_path.with_name(
        f".{owner_path.name}.tmp-{os.getpid()}-{time.time_ns()}"
    )
    try:
        device, inode, ctime_ns = _lock_identity(lock_path.stat())
        payload = {
            "pid": os.getpid(),
            "device": device,
            "inode": inode,
            "ctime_ns": ctime_ns,
        }
        owner_path.unlink(missing_ok=True)
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, owner_path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_stale_pcr_index_lock(lock_path: Path) -> bool:
    owner_path = _index_lock_owner_path(lock_path)
    try:
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        recorded_identity = (
            int(payload["device"]),
            int(payload["inode"]),
            int(payload["ctime_ns"]),
        )
        current_identity = _lock_identity(lock_path.stat())
    except (KeyError, OSError, TypeError, ValueError):
        return False
    if recorded_identity != current_identity or _process_is_alive(pid):
        return False

    try:
        if _lock_identity(lock_path.stat()) != recorded_identity:
            return False
        lock_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        owner_path.unlink(missing_ok=True)
    except OSError:
        pass
    _debug("removed stale PCR-owned Git index lock: {}", lock_path)
    return True


def _acquire_git_index_lock(
    source_index: Path,
    lock_path: Path,
    timeout: float,
    poll_interval: float,
) -> int:
    mode = source_index.stat().st_mode & 0o777
    started = time.monotonic()
    deadline = started + max(0.0, timeout)
    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode,
            )
            waited = time.monotonic() - started
            if waited >= poll_interval:
                _debug("waited {:.2f}s for Git index lock: {}", waited, lock_path)
            return descriptor
        except FileExistsError as exc:
            if _remove_stale_pcr_index_lock(lock_path):
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Git index remained locked for {max(0.0, timeout):.1f}s: {lock_path}"
                ) from exc
            time.sleep(min(max(0.001, poll_interval), remaining))


@contextmanager
def _locked_git_index(
    source_index: Path,
    destination_index: Path,
    timeout: float = GIT_INDEX_LOCK_TIMEOUT_SECONDS,
    poll_interval: float = GIT_INDEX_LOCK_POLL_SECONDS,
) -> Iterator[Path]:
    lock_path = destination_index.with_name(f"{destination_index.name}.lock")
    owner_path = _index_lock_owner_path(lock_path)
    descriptor = _acquire_git_index_lock(
        source_index,
        lock_path,
        timeout,
        poll_interval,
    )
    try:
        with os.fdopen(descriptor, "wb") as target, source_index.open("rb") as source:
            shutil.copyfileobj(source, target)
        _write_index_lock_owner(lock_path)
        yield lock_path
    finally:
        try:
            owner_path.unlink(missing_ok=True)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def _copy_git_worktree_state(source_workspace: Path, destination_workspace: Path) -> None:
    with _prepared_git_index(source_workspace, destination_workspace) as (source_index, destination_index):
        with _locked_git_index(source_index, destination_index) as locked_index:
            # Keep Git/status watchers out until files and the copied index agree.
            sync_back_with_python(source_workspace, destination_workspace)
            os.replace(locked_index, destination_index)


def _record_worktree_base_state(workspace: Path, base_head: str, base_ref: Optional[str]) -> None:
    marker = _worktree_base_state_path(workspace)
    if marker is None:
        raise RuntimeError("could not locate the Git worktree metadata directory")
    marker.write_text(
        json.dumps({"head": base_head, "ref": base_ref}, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_worktree_base_state(workspace: Path) -> Optional[Tuple[str, Optional[str]]]:
    marker = _worktree_base_state_path(workspace)
    if marker is None or not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    head = payload.get("head") if isinstance(payload, dict) else None
    ref = payload.get("ref") if isinstance(payload, dict) else None
    if not isinstance(head, str) or not head:
        return None
    return head, ref if isinstance(ref, str) else None


def _is_git_ancestor(workspace: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(workspace), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


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


def ensure_removed(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise OSError(f"failed to remove path: {path}")


def remove_tree_checked(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)
    ensure_removed(path)


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
            ensure_removed(workspace_copy)
            return
    remove_tree_checked(workspace_copy)
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
        remove_tree_checked(workspaces_root)
    prune_git_worktrees(original_workspace)


def copy_workspace_with_git_worktree(workspace: Path, dst: Path) -> bool:
    if git_workspace_toplevel(workspace) is None:
        return False

    base_head = _git_head(workspace)
    if base_head is None:
        return False
    base_ref = _git_head_ref(workspace)

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
        _copy_git_worktree_state(workspace, dst)
        _record_worktree_base_state(dst, base_head, base_ref)
    except Exception as exc:
        _debug("git worktree copy failed: {}", exc)
        cleanup_workspace_copy(workspace, dst)
        raise
    return True


def copy_workspace(workspace: Path, dst: Path, run_base: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    if copy_workspace_with_git_worktree(workspace, dst):
        return

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


def _sync_workspace_files(src: Path, dst: Path) -> None:
    if _rsync_available():
        sync_back_with_rsync(src, dst)
    else:
        sync_back_with_python(src, dst)


def _sync_git_workspace_back(src: Path, dst: Path) -> bool:
    src_top = git_workspace_toplevel(src)
    dst_top = git_workspace_toplevel(dst)
    if src_top is None or dst_top is None:
        return False

    src_common = _git_common_dir(src_top)
    dst_common = _git_common_dir(dst_top)
    if src_common is None or dst_common is None or src_common != dst_common:
        return False

    src_head = _git_head(src_top)
    dst_head = _git_head(dst_top)
    if src_head is None or dst_head is None:
        return False

    base_state = _read_worktree_base_state(src_top)
    base_head = base_state[0] if base_state is not None else None
    if base_state is not None and _git_head_ref(dst_top) != base_state[1]:
        raise RuntimeError("original Git branch changed while agents were running")
    if base_head is not None and dst_head not in {base_head, src_head}:
        raise RuntimeError(
            "original Git HEAD changed while agents were running; "
            f"expected {base_head[:12]}, found {dst_head[:12]}"
        )
    if base_head is None and src_head != dst_head and not _is_git_ancestor(dst_top, dst_head, src_head):
        raise RuntimeError(
            "cannot safely recover Git state from a legacy agent worktree because "
            "its HEAD is not based on the original workspace HEAD"
        )

    with _prepared_git_index(src_top, dst_top) as (source_index, destination_index):
        with _locked_git_index(source_index, destination_index) as locked_index:
            _sync_workspace_files(src_top, dst_top)

            # Move the checked-out branch (or detached HEAD) without touching the synced files.
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(dst_top),
                    "update-ref",
                    "-m",
                    "parallel-codex-runner: sync selected agent",
                    "HEAD",
                    src_head,
                    dst_head,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
                raise RuntimeError(f"failed to move the original Git HEAD to the selected agent: {message}")

            os.replace(locked_index, destination_index)
    return True


def sync_best_workspace_back(src: Path, dst: Path) -> None:
    if _sync_git_workspace_back(src, dst):
        return
    _sync_workspace_files(src, dst)
