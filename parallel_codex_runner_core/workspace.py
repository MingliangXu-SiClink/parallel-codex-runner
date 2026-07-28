from __future__ import annotations

import hashlib
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
GIT_BASE_STATE_FILE = "pcr-base-state.json"
GIT_INDEX_LOCK_TIMEOUT_SECONDS = 5.0
GIT_INDEX_LOCK_POLL_SECONDS = 0.05
PCR_INDEX_LOCK_OWNER_SUFFIX = ".pcr-owner"
PCR_INDEX_LOCK_OWNER_FILE_MARKER = ".pcr-lock-owner-"
GIT_ISOLATION_KIND = "local-clone-v1"


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
                if rp == excluded:
                    ignored.add(name)
                    break
        return ignored

    return ignore


def _allocated_bytes(stat_result: os.stat_result) -> int:
    """Estimate destination storage, including allocation for small files."""
    block_bytes = int(getattr(stat_result, "st_blocks", 0) or 0) * 512
    return max(int(stat_result.st_size), block_bytes)


def estimate_path_storage_bytes(path: Path) -> int:
    """Estimate storage for a copied path without following nested symlinks."""
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return 0

    total = _allocated_bytes(root_stat)
    if path.is_symlink() or not path.is_dir():
        return total

    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                total += _allocated_bytes(stat_result)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return total


def estimate_workspace_copy_bytes(workspace: Path) -> int:
    """Estimate one isolated workspace copy using PCR's exclusion rules."""
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {workspace}")

    total = _allocated_bytes(workspace.stat())
    pending = [workspace]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                if entry.name in EXCLUDE_NAMES:
                    continue
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                total += _allocated_bytes(stat_result)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))

    if git_workspace_toplevel(workspace) is not None:
        # A same-filesystem local clone hard-links immutable source objects,
        # but still receives independent Git administration data and an index.
        index_paths = {_git_index_path(workspace), _git_shared_index_path(workspace)}
        for index_path in index_paths:
            if index_path is None:
                continue
            try:
                total += _allocated_bytes(index_path.stat())
            except FileNotFoundError:
                continue
    return total


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


def _tracked_submodule_paths(workspace: Path) -> List[Path]:
    """Return direct gitlinks from the current index, including dirty checkouts."""
    if git_workspace_toplevel(workspace) is None:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "--stage", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"failed to read Git submodules in {workspace}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to read Git submodules in {workspace}: "
            f"{os.fsdecode(result.stderr).strip() or 'unknown Git error'}"
        )

    paths: List[Path] = []
    workspace_resolved = workspace.resolve()
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator or metadata.split(b" ", 1)[0] != b"160000":
            continue
        relative = Path(os.fsdecode(raw_path))
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or not is_relative_to((workspace / relative).resolve(), workspace_resolved)
        ):
            raise RuntimeError(
                f"unsafe Git submodule path in {workspace}: {os.fsdecode(raw_path)!r}"
            )
        if relative not in paths:
            paths.append(relative)
    return sorted(paths, key=lambda path: path.as_posix())


def _initialized_submodule_paths(workspace: Path) -> List[Path]:
    initialized: List[Path] = []
    for relative in _tracked_submodule_paths(workspace):
        submodule = workspace / relative
        if (
            (submodule / ".git").exists()
            and git_workspace_toplevel(submodule) == submodule.resolve()
        ):
            initialized.append(relative)
    return initialized


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


def _git_base_state_path(workspace: Path) -> Optional[Path]:
    return _resolved_git_path(workspace, "--git-path", GIT_BASE_STATE_FILE)


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
        # Strip path-specific index caches before installing the index elsewhere.
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
    """Return the legacy sidecar path used by PCR versions before owner files."""
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


def _index_lock_owner_file_prefix(lock_path: Path) -> str:
    return f".{lock_path.name}{PCR_INDEX_LOCK_OWNER_FILE_MARKER}"


def _index_lock_owner_file(lock_path: Path) -> Path:
    prefix = _index_lock_owner_file_prefix(lock_path)
    for attempt in range(1000):
        suffix = f"{os.getpid()}-{time.time_ns()}"
        if attempt:
            suffix += f"-{attempt}"
        candidate = lock_path.with_name(prefix + suffix)
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise RuntimeError(f"could not allocate PCR Git index owner file: {lock_path}")


def _index_lock_owner_pid(lock_path: Path, owner_path: Path) -> Optional[int]:
    prefix = _index_lock_owner_file_prefix(lock_path)
    if owner_path.parent != lock_path.parent or not owner_path.name.startswith(prefix):
        return None
    value = owner_path.name.removeprefix(prefix).split("-", 1)[0]
    try:
        return int(value)
    except ValueError:
        return None


def _same_file(left: Path, right: Path) -> bool:
    try:
        left_stat = left.stat()
        right_stat = right.stat()
    except OSError:
        return False
    return (
        int(left_stat.st_dev),
        int(left_stat.st_ino),
    ) == (
        int(right_stat.st_dev),
        int(right_stat.st_ino),
    )


def _file_inode_identity(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return int(stat_result.st_dev), int(stat_result.st_ino)


def _owned_index_lock_file(lock_path: Path) -> Optional[Path]:
    if lock_path.is_symlink():
        try:
            target = Path(os.readlink(lock_path))
        except OSError:
            return None
        if target.is_absolute() or len(target.parts) != 1:
            return None
        owner_path = lock_path.parent / target
        return (
            owner_path
            if _index_lock_owner_pid(lock_path, owner_path) is not None
            else None
        )

    prefix = _index_lock_owner_file_prefix(lock_path)
    for owner_path in lock_path.parent.glob(prefix + "*"):
        if (
            _index_lock_owner_pid(lock_path, owner_path) is not None
            and _same_file(lock_path, owner_path)
        ):
            return owner_path
    return None


def _cleanup_orphaned_index_lock_owners(lock_path: Path) -> None:
    prefix = _index_lock_owner_file_prefix(lock_path)
    for owner_path in lock_path.parent.glob(prefix + "*"):
        pid = _index_lock_owner_pid(lock_path, owner_path)
        if pid is None or _process_is_alive(pid):
            continue
        if lock_path.exists() or lock_path.is_symlink():
            if _owned_index_lock_file(lock_path) == owner_path:
                continue
        try:
            owner_path.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_stale_pcr_index_lock(lock_path: Path) -> bool:
    owner_file = _owned_index_lock_file(lock_path)
    if owner_file is not None:
        pid = _index_lock_owner_pid(lock_path, owner_file)
        if pid is None or _process_is_alive(pid):
            # A current atomic owner is authoritative. Never let a leftover
            # legacy sidecar override proof that another PCR process owns it.
            return False
        try:
            if _owned_index_lock_file(lock_path) != owner_file:
                return False
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        try:
            owner_file.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _index_lock_owner_path(lock_path).unlink(missing_ok=True)
        except OSError:
            pass
        _debug("removed stale PCR-owned Git index lock: {}", lock_path)
        return True

    # Recover locks created by PCR releases that used a JSON sidecar.
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
) -> Tuple[Path, Path]:
    mode = source_index.stat().st_mode & 0o777
    started = time.monotonic()
    deadline = started + max(0.0, timeout)
    _cleanup_orphaned_index_lock_owners(lock_path)
    owner_path = _index_lock_owner_file(lock_path)
    descriptor = os.open(
        owner_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode,
    )
    try:
        with os.fdopen(descriptor, "wb") as target, source_index.open("rb") as source:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        while True:
            try:
                try:
                    # A hard link makes lock ownership recoverable before the
                    # lock becomes visible, eliminating the sidecar gap.
                    os.link(owner_path, lock_path)
                    install_path = lock_path
                except FileExistsError:
                    raise
                except OSError:
                    # A symlink retains the owner filename as its atomic proof
                    # when hard links are unavailable.
                    os.symlink(owner_path.name, lock_path)
                    install_path = owner_path
            except FileExistsError as exc:
                if _remove_stale_pcr_index_lock(lock_path):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Git index remained locked for "
                        f"{max(0.0, timeout):.1f}s: {lock_path}"
                    ) from exc
                time.sleep(min(max(0.001, poll_interval), remaining))
                continue
            waited = time.monotonic() - started
            if waited >= poll_interval:
                _debug("waited {:.2f}s for Git index lock: {}", waited, lock_path)
            return owner_path, install_path
    except BaseException:
        try:
            owner_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@contextmanager
def _locked_git_index(
    source_index: Path,
    destination_index: Path,
    timeout: float = GIT_INDEX_LOCK_TIMEOUT_SECONDS,
    poll_interval: float = GIT_INDEX_LOCK_POLL_SECONDS,
) -> Iterator[Path]:
    lock_path = destination_index.with_name(f"{destination_index.name}.lock")
    owner_path, install_path = _acquire_git_index_lock(
        source_index,
        lock_path,
        timeout,
        poll_interval,
    )
    owner_identity = _file_inode_identity(owner_path)
    try:
        yield install_path
    finally:
        try:
            symlink_owner = _owned_index_lock_file(lock_path)
            if (
                symlink_owner == owner_path
                or (
                    owner_identity is not None
                    and _file_inode_identity(lock_path) == owner_identity
                )
            ):
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            owner_path.unlink(missing_ok=True)
        except OSError:
            pass


def _copy_git_workspace_state(
    source_workspace: Path,
    destination_workspace: Path,
    excluded_paths: Sequence[Path] = (),
) -> None:
    with _prepared_git_index(source_workspace, destination_workspace) as (source_index, destination_index):
        with _locked_git_index(source_index, destination_index) as locked_index:
            # Keep Git/status watchers out until files and the copied index agree.
            if excluded_paths:
                sync_back_with_python(
                    source_workspace,
                    destination_workspace,
                    excluded_paths=excluded_paths,
                )
            else:
                sync_back_with_python(
                    source_workspace,
                    destination_workspace,
                )
            os.replace(locked_index, destination_index)


def _git_source_identity(path: Path) -> str:
    resolved = os.fsencode(str(path.expanduser().resolve()))
    return hashlib.sha256(resolved).hexdigest()


def _record_git_base_state(
    workspace: Path,
    base_head: str,
    base_ref: Optional[str],
    submodule_paths: Sequence[Path] = (),
    *,
    original_workspace: Optional[Path] = None,
) -> None:
    marker = _git_base_state_path(workspace)
    if marker is None:
        raise RuntimeError("could not locate the isolated Git metadata directory")
    payload: dict[str, object] = {
        "head": base_head,
        "ref": base_ref,
        "submodules": [path.as_posix() for path in submodule_paths],
    }
    if original_workspace is not None:
        original_workspace = original_workspace.resolve()
        original_common_dir = _git_common_dir(original_workspace)
        if original_common_dir is None:
            raise RuntimeError("could not locate the original Git metadata directory")
        payload.update(
            {
                "isolation": GIT_ISOLATION_KIND,
                "original_workspace_id": _git_source_identity(
                    original_workspace
                ),
                "original_common_dir_id": _git_source_identity(
                    original_common_dir
                ),
            }
        )
    marker.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_git_base_state_payload(workspace: Path) -> Optional[dict[str, object]]:
    marker = _git_base_state_path(workspace)
    if marker is None or not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_git_base_state(workspace: Path) -> Optional[Tuple[str, Optional[str]]]:
    payload = _read_git_base_state_payload(workspace)
    head = payload.get("head") if isinstance(payload, dict) else None
    ref = payload.get("ref") if isinstance(payload, dict) else None
    if not isinstance(head, str) or not head:
        return None
    return head, ref if isinstance(ref, str) else None


def _read_git_submodule_paths(workspace: Path) -> List[Path]:
    if git_workspace_toplevel(workspace) != workspace.resolve():
        return []
    marker = _git_base_state_path(workspace)
    if marker is not None and marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        values = payload.get("submodules") if isinstance(payload, dict) else None
        if isinstance(values, list):
            paths = []
            workspace_resolved = workspace.resolve()
            for value in values:
                if not isinstance(value, str):
                    continue
                relative = Path(value)
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or ".." in relative.parts
                    or not is_relative_to(
                        (workspace / relative).resolve(),
                        workspace_resolved,
                    )
                ):
                    continue
                paths.append(relative)
            return sorted(set(paths), key=lambda path: path.as_posix())
    return _initialized_submodule_paths(workspace)


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
    try:
        submodule_paths = _initialized_submodule_paths(original_workspace)
    except RuntimeError as exc:
        _debug("could not enumerate submodules while pruning worktrees: {}", exc)
        return
    for relative in submodule_paths:
        prune_git_worktrees(original_workspace / relative)


def ensure_removed(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise OSError(f"failed to remove path: {path}")


def remove_tree_checked(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)
    ensure_removed(path)


def _remove_linked_git_worktree(workspace_copy: Path) -> bool:
    if not (workspace_copy / ".git").is_file():
        return False
    common_dir = _git_common_dir(workspace_copy)
    if common_dir is None:
        return False
    result = subprocess.run(
        [
            "git",
            f"--git-dir={common_dir}",
            "worktree",
            "remove",
            "--force",
            "--force",
            str(workspace_copy),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", f"--git-dir={common_dir}", "worktree", "prune", "--expire", "now"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    ensure_removed(workspace_copy)
    return True


def _cleanup_nested_git_workspaces(
    workspace_copy: Path,
    original_workspace: Optional[Path] = None,
) -> None:
    for relative in reversed(_read_git_submodule_paths(workspace_copy)):
        submodule = workspace_copy / relative
        original_submodule = (
            original_workspace / relative
            if original_workspace is not None
            else None
        )
        if not submodule.exists() and not submodule.is_symlink():
            if (
                original_submodule is not None
                and git_workspace_toplevel(original_submodule)
                == original_submodule.resolve()
            ):
                prune_git_worktrees(original_submodule)
            continue
        _cleanup_nested_git_workspaces(submodule, original_submodule)
        common_dir = _git_common_dir(submodule)
        if not _remove_linked_git_worktree(submodule):
            remove_tree_checked(submodule)
            if common_dir is not None:
                subprocess.run(
                    [
                        "git",
                        f"--git-dir={common_dir}",
                        "worktree",
                        "prune",
                        "--expire",
                        "now",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )


def cleanup_workspace_copy(original_workspace: Path, workspace_copy: Path) -> None:
    if not workspace_copy.exists() and not workspace_copy.is_symlink():
        return
    _cleanup_nested_git_workspaces(workspace_copy, original_workspace)
    if _remove_linked_git_worktree(workspace_copy):
        prune_git_worktrees(original_workspace)
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


def _copy_local_git_identity(source: Path, destination: Path) -> None:
    for key in ("user.name", "user.email"):
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "config",
                "--local",
                "--null",
                "--get-all",
                key,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode == 1:
            continue
        if result.returncode != 0:
            message = os.fsdecode(result.stderr).strip() or "unknown Git error"
            raise RuntimeError(f"could not read local Git identity: {message}")
        subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "config",
                "--local",
                "--unset-all",
                key,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        for raw_value in result.stdout.split(b"\0"):
            if not raw_value:
                continue
            configured = subprocess.run(
                [
                    "git",
                    "-C",
                    str(destination),
                    "config",
                    "--local",
                    "--add",
                    key,
                    os.fsdecode(raw_value),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if configured.returncode != 0:
                message = (
                    configured.stderr.strip()
                    or configured.stdout.strip()
                    or "unknown Git error"
                )
                raise RuntimeError(
                    f"could not copy local Git identity into isolated workspace: {message}"
                )


def copy_workspace_with_isolated_git(workspace: Path, dst: Path) -> bool:
    if git_workspace_toplevel(workspace) is None:
        return False

    base_head = _git_head(workspace)
    if base_head is None:
        return False
    base_ref = _git_head_ref(workspace)

    result: Optional[subprocess.CompletedProcess[str]] = None
    for locality in ("--local", "--no-local"):
        result = subprocess.run(
            [
                "git",
                "clone",
                locality,
                "--no-checkout",
                "--no-recurse-submodules",
                "--quiet",
                str(workspace),
                str(dst),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            break
        if dst.exists() or dst.is_symlink():
            remove_tree_checked(dst)
    assert result is not None
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise RuntimeError(f"could not create an isolated Git workspace: {message}")

    try:
        detach = subprocess.run(
            [
                "git",
                "-C",
                str(dst),
                "update-ref",
                "--no-deref",
                "HEAD",
                base_head,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if detach.returncode != 0:
            message = detach.stderr.strip() or detach.stdout.strip() or "unknown Git error"
            raise RuntimeError(f"could not detach the isolated Git HEAD: {message}")

        remove_origin = subprocess.run(
            ["git", "-C", str(dst), "remote", "remove", "origin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if remove_origin.returncode != 0:
            message = (
                remove_origin.stderr.strip()
                or remove_origin.stdout.strip()
                or "unknown Git error"
            )
            raise RuntimeError(
                f"could not remove the isolated clone's source remote: {message}"
            )
        _copy_local_git_identity(workspace, dst)

        submodule_paths = _initialized_submodule_paths(workspace)
        _copy_git_workspace_state(
            workspace,
            dst,
            excluded_paths=submodule_paths,
        )
        for relative in submodule_paths:
            source_submodule = workspace / relative
            destination_submodule = dst / relative
            remove_existing_path(destination_submodule)
            destination_submodule.parent.mkdir(parents=True, exist_ok=True)
            if not copy_workspace_with_isolated_git(
                source_submodule,
                destination_submodule,
            ):
                raise RuntimeError(
                    f"could not isolate initialized Git submodule: {relative}"
                )
        _record_git_base_state(
            dst,
            base_head,
            base_ref,
            submodule_paths,
            original_workspace=workspace,
        )
    except Exception as exc:
        _debug("isolated Git copy failed: {}", exc)
        cleanup_workspace_copy(workspace, dst)
        raise
    return True


def copy_workspace(workspace: Path, dst: Path, run_base: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    if copy_workspace_with_isolated_git(workspace, dst):
        return

    workspace_resolved = workspace.resolve()
    run_base_resolved = run_base.resolve()
    excluded_paths = (
        [run_base_resolved]
        if is_relative_to(run_base_resolved, workspace_resolved)
        else []
    )
    shutil.copytree(
        workspace,
        dst,
        symlinks=True,
        ignore=make_ignore_func(excluded_paths),
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


def _is_excluded_relative(path: Path, excluded_paths: Sequence[Path]) -> bool:
    return any(
        path == excluded or excluded in path.parents
        for excluded in excluded_paths
    )


def remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def sync_back_with_python(
    src: Path,
    dst: Path,
    excluded_paths: Sequence[Path] = (),
) -> None:
    src = src.resolve()
    dst = dst.resolve()
    normalized_excluded = tuple(
        path
        for path in excluded_paths
        if not path.is_absolute() and ".." not in path.parts
    )

    for root, dirs, files in os.walk(dst, topdown=False):
        root_p = Path(root)
        rel_root = root_p.relative_to(dst)
        if (
            should_skip_rel(rel_root, SYNC_EXCLUDE_NAMES)
            or _is_excluded_relative(rel_root, normalized_excluded)
        ):
            continue

        for fname in files:
            rel = rel_root / fname
            if (
                should_skip_rel(rel, SYNC_EXCLUDE_NAMES)
                or _is_excluded_relative(rel, normalized_excluded)
            ):
                continue
            src_equiv = src / rel
            dst_equiv = dst / rel
            if not src_equiv.exists() and not src_equiv.is_symlink():
                dst_equiv.unlink(missing_ok=True)

        for dname in dirs:
            rel = rel_root / dname
            if (
                should_skip_rel(rel, SYNC_EXCLUDE_NAMES)
                or _is_excluded_relative(rel, normalized_excluded)
            ):
                continue
            src_equiv = src / rel
            dst_equiv = dst / rel
            if not src_equiv.exists() and not src_equiv.is_symlink():
                remove_existing_path(dst_equiv)

    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        rel_root = root_p.relative_to(src)
        if (
            should_skip_rel(rel_root, SYNC_EXCLUDE_NAMES)
            or _is_excluded_relative(rel_root, normalized_excluded)
        ):
            dirs[:] = []
            continue

        target_root = dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for dname in list(dirs):
            rel = rel_root / dname
            if (
                should_skip_rel(rel, SYNC_EXCLUDE_NAMES)
                or _is_excluded_relative(rel, normalized_excluded)
            ):
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
            if (
                should_skip_rel(rel, SYNC_EXCLUDE_NAMES)
                or _is_excluded_relative(rel, normalized_excluded)
            ):
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


def _sync_workspace_files(
    src: Path,
    dst: Path,
    excluded_paths: Sequence[Path] = (),
) -> None:
    if _rsync_available() and not excluded_paths:
        sync_back_with_rsync(src, dst)
    elif excluded_paths:
        sync_back_with_python(src, dst, excluded_paths=excluded_paths)
    else:
        sync_back_with_python(src, dst)


def _isolated_git_source_matches_destination(
    payload: dict[str, object],
    destination_workspace: Path,
    destination_common_dir: Path,
) -> bool:
    if payload.get("isolation") != GIT_ISOLATION_KIND:
        return False
    recorded_workspace_id = payload.get("original_workspace_id")
    recorded_common_dir_id = payload.get("original_common_dir_id")
    if not isinstance(recorded_workspace_id, str) or not isinstance(
        recorded_common_dir_id,
        str,
    ):
        raise RuntimeError("isolated Git workspace is missing original repository metadata")
    if (
        recorded_workspace_id != _git_source_identity(destination_workspace)
        or recorded_common_dir_id != _git_source_identity(
            destination_common_dir
        )
    ):
        raise RuntimeError(
            "isolated Git workspace belongs to a different original repository"
        )
    return True


def _git_index_object_ids(workspace: Path) -> List[str]:
    result = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "--stage", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        message = os.fsdecode(result.stderr).strip() or "unknown Git error"
        raise RuntimeError(f"could not enumerate isolated Git index objects: {message}")

    object_ids: Set[str] = set()
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, _raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) < 2 or fields[0] == b"160000":
            continue
        object_id = os.fsdecode(fields[1])
        if object_id and set(object_id) != {"0"}:
            object_ids.add(object_id)
    return sorted(object_ids)


def _import_isolated_git_objects(
    source_workspace: Path,
    destination_workspace: Path,
    source_head: str,
    base_head: str,
) -> None:
    revisions = [source_head, f"^{base_head}"]
    revisions.extend(_git_index_object_ids(source_workspace))
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"

    with tempfile.TemporaryFile() as pack_file:
        packed = subprocess.run(
            [
                "git",
                "-C",
                str(source_workspace),
                "pack-objects",
                "--stdout",
                "--revs",
            ],
            input=("\n".join(revisions) + "\n").encode("ascii"),
            stdout=pack_file,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
        if packed.returncode != 0:
            message = os.fsdecode(packed.stderr).strip() or "unknown Git error"
            raise RuntimeError(f"could not export isolated Git objects: {message}")

        pack_file.seek(0)
        imported = subprocess.run(
            [
                "git",
                "-C",
                str(destination_workspace),
                "index-pack",
                "--stdin",
            ],
            stdin=pack_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=environment,
        )
    if imported.returncode != 0:
        message = imported.stderr.strip() or imported.stdout.strip() or "unknown Git error"
        raise RuntimeError(f"could not import isolated Git objects: {message}")

    verify = subprocess.run(
        [
            "git",
            "-C",
            str(destination_workspace),
            "cat-file",
            "-e",
            f"{source_head}^{{commit}}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=environment,
    )
    if verify.returncode != 0:
        raise RuntimeError("isolated Git commit was not imported into the original repository")


def _sync_git_workspace_back(src: Path, dst: Path) -> bool:
    src_top = git_workspace_toplevel(src)
    dst_top = git_workspace_toplevel(dst)
    if src_top is None or dst_top is None:
        return False

    src_common = _git_common_dir(src_top)
    dst_common = _git_common_dir(dst_top)
    if src_common is None or dst_common is None:
        return False
    base_payload = _read_git_base_state_payload(src_top)
    isolated_git = False
    if src_common != dst_common:
        if base_payload is None:
            return False
        isolated_git = _isolated_git_source_matches_destination(
            base_payload,
            dst_top,
            dst_common,
        )
        if not isolated_git:
            return False

    src_head = _git_head(src_top)
    dst_head = _git_head(dst_top)
    if src_head is None or dst_head is None:
        return False

    base_state = _read_git_base_state(src_top)
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

    submodule_paths = _read_git_submodule_paths(src_top)
    for relative in submodule_paths:
        src_submodule = src_top / relative
        dst_submodule = dst_top / relative
        src_submodule_top = git_workspace_toplevel(src_submodule)
        if src_submodule_top is not None:
            if git_workspace_toplevel(dst_submodule) is None:
                raise RuntimeError(
                    "original initialized Git submodule is unavailable during sync: "
                    f"{relative}"
                )
            if not _sync_git_workspace_back(src_submodule, dst_submodule):
                raise RuntimeError(f"could not sync Git submodule: {relative}")
        elif not src_submodule.exists() and dst_submodule.exists():
            destination_common_dir = _git_common_dir(dst_submodule)
            remove_existing_path(dst_submodule)
            if destination_common_dir is not None:
                subprocess.run(
                    [
                        "git",
                        f"--git-dir={destination_common_dir}",
                        "worktree",
                        "prune",
                        "--expire",
                        "now",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
        elif src_submodule.exists():
            raise RuntimeError(
                f"isolated Git metadata is missing for submodule: {relative}"
            )

    if isolated_git:
        if base_head is None:
            raise RuntimeError("isolated Git workspace is missing its base commit")
        _import_isolated_git_objects(
            src_top,
            dst_top,
            src_head,
            base_head,
        )

    with _prepared_git_index(src_top, dst_top) as (source_index, destination_index):
        with _locked_git_index(source_index, destination_index) as locked_index:
            _sync_workspace_files(
                src_top,
                dst_top,
                excluded_paths=submodule_paths,
            )

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
