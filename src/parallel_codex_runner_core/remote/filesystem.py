from __future__ import annotations

import datetime as dt
import logging
import os
import platform
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
MAX_DIRECTORY_PAGE = 500
VOLUME_ENUM_TIMEOUT_SECONDS = 2.0
_VOLUME_ENUMERATION_LOCK = threading.Lock()
_VOLUME_ENUMERATIONS_IN_FLIGHT: set[Path] = set()


class RemoteFileSystemError(RuntimeError):
    """A path cannot be exposed through the remote directory browser."""


def _timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class FileSystemEntry:
    name: str
    path: str
    kind: str
    is_symlink: bool
    readable: bool
    writable: bool
    hidden: bool
    size: int | None = None
    modified_at: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "is_symlink": self.is_symlink,
            "readable": self.readable,
            "writable": self.writable,
            "hidden": self.hidden,
            "size": self.size,
            "modified_at": self.modified_at,
        }


class FileSystemBrowser:
    """Browse every directory available to the logged-in machine user.

    There is deliberately no PCR allowlist. Operating-system permissions remain
    authoritative; inaccessible paths fail instead of being hidden behind a
    second, conflicting permission model. On macOS this includes TCC.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.home = (home or Path.home()).expanduser().resolve()

    def resolve_directory(self, raw_path: str | os.PathLike[str] | None) -> Path:
        text = str(raw_path or "").strip()
        candidate = self.home if not text else Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = self.home / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RemoteFileSystemError(f"Directory does not exist: {candidate}") from exc
        if not resolved.is_dir():
            raise RemoteFileSystemError(f"Path is not a directory: {resolved}")
        if not os.access(resolved, os.R_OK | os.X_OK):
            raise RemoteFileSystemError(
                f"The operating system denied access to directory: {resolved}"
            )
        return resolved

    @staticmethod
    def _entry(value: os.DirEntry[str]) -> FileSystemEntry:
        path = Path(value.path)
        is_symlink = value.is_symlink()
        try:
            is_directory = value.is_dir(follow_symlinks=True)
        except OSError:
            is_directory = False
        try:
            is_file = value.is_file(follow_symlinks=True)
        except OSError:
            is_file = False
        kind = "directory" if is_directory else "file" if is_file else "other"
        size: int | None = None
        modified: float | None = None
        try:
            stat = value.stat(follow_symlinks=False)
            modified = stat.st_mtime
            if kind == "file":
                size = stat.st_size
        except OSError:
            pass
        return FileSystemEntry(
            name=value.name,
            path=str(path.absolute()),
            kind=kind,
            is_symlink=is_symlink,
            readable=os.access(path, os.R_OK),
            writable=os.access(path, os.W_OK),
            hidden=value.name.startswith("."),
            size=size,
            modified_at=_timestamp(modified),
        )

    def roots(self) -> dict[str, Any]:
        candidates = [Path("/"), self.home]
        candidates.extend(
            self.home / name for name in ("Desktop", "Documents", "Downloads")
        )
        system = platform.system()
        volume_parents = [Path("/Volumes")] if system == "Darwin" else [
            Path("/mnt"),
            Path("/media"),
            Path("/run/media") / os.environ.get("USER", ""),
        ]
        volume_paths: set[Path] = set()
        for parent in volume_parents:
            try:
                discovered = self._enumerate_volumes_with_timeout(parent)
            except OSError:
                continue
            for path in discovered:
                candidates.append(path)
                volume_paths.add(path.resolve())
        roots: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            key = str(resolved)
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            roots.append(
                {
                    "name": (
                        ("Macintosh HD" if system == "Darwin" else "File System")
                        if resolved == Path("/")
                        else resolved.name or str(resolved)
                    ),
                    "path": key,
                    "readable": os.access(resolved, os.R_OK | os.X_OK),
                    "writable": os.access(resolved, os.W_OK),
                    "kind": "volume" if resolved in volume_paths else "shortcut",
                }
            )
        return {"roots": roots, "home": str(self.home)}

    @staticmethod
    def _enumerate_volumes_with_timeout(parent: Path) -> list[Path]:
        """List directories under ``parent`` with a timeout.

        Stale network mounts can cause ``iterdir`` / ``is_dir`` to hang
        indefinitely. We run the enumeration in a worker thread and give up
        after a short timeout so the rest of the response is still delivered.
        """
        if not parent.exists():
            return []
        with _VOLUME_ENUMERATION_LOCK:
            if parent in _VOLUME_ENUMERATIONS_IN_FLIGHT:
                return []
            _VOLUME_ENUMERATIONS_IN_FLIGHT.add(parent)
        result: queue.Queue[list[Path] | Exception] = queue.Queue(maxsize=1)

        def enumerate_volumes() -> None:
            try:
                result.put_nowait(_list_subdirectories(parent))
            except Exception as exc:  # noqa: BLE001
                result.put_nowait(exc)
            finally:
                with _VOLUME_ENUMERATION_LOCK:
                    _VOLUME_ENUMERATIONS_IN_FLIGHT.discard(parent)

        # ThreadPoolExecutor waits for its worker while leaving the context, so
        # using future.result(timeout=...) there does not actually bound a stale
        # mount. A daemon thread lets this request return while the OS call is
        # still blocked; volume-root discovery is best effort.
        threading.Thread(
            target=enumerate_volumes,
            name="pcr-volenum",
            daemon=True,
        ).start()
        try:
            value = result.get(timeout=VOLUME_ENUM_TIMEOUT_SECONDS)
        except queue.Empty:
            LOGGER.warning(
                "Volume enumeration timed out after %.1fs for %s; "
                "skipping unresponsive mounts",
                VOLUME_ENUM_TIMEOUT_SECONDS,
                parent,
            )
            return []
        if isinstance(value, Exception):
            raise value
        return value

    def inspect(self, raw_path: str | os.PathLike[str] | None) -> dict[str, Any]:
        path = self.resolve_directory(raw_path)
        try:
            stat = path.stat()
        except OSError as exc:
            raise RemoteFileSystemError(f"Cannot inspect directory: {path}") from exc
        return {
            "name": path.name or str(path),
            "path": str(path),
            "parent": str(path.parent) if path != path.parent else None,
            "readable": os.access(path, os.R_OK | os.X_OK),
            "writable": os.access(path, os.W_OK),
            "modified_at": _timestamp(stat.st_mtime),
            "is_git_repository": (path / ".git").exists(),
        }

    def create_directory(
        self,
        raw_parent: str | os.PathLike[str] | None,
        name: str,
    ) -> dict[str, Any]:
        parent = self.resolve_directory(raw_parent)
        if not os.access(parent, os.W_OK | os.X_OK):
            raise RemoteFileSystemError(
                f"The operating system denied permission to create a folder in: {parent}"
            )
        folder_name = str(name or "").strip()
        if not folder_name:
            raise RemoteFileSystemError("Folder name must not be empty")
        if folder_name in {".", ".."} or "/" in folder_name or "\x00" in folder_name:
            raise RemoteFileSystemError("Folder name is not valid")
        destination = parent / folder_name
        try:
            destination.mkdir(mode=0o755)
        except FileExistsError as exc:
            raise RemoteFileSystemError(
                f"A file or folder named {folder_name} already exists in {parent}"
            ) from exc
        except PermissionError as exc:
            raise RemoteFileSystemError(
                f"The operating system denied permission to create a folder in: {parent}"
            ) from exc
        except OSError as exc:
            raise RemoteFileSystemError(
                f"Cannot create folder {folder_name} in {parent}: {exc}"
            ) from exc
        return {
            **self.inspect(destination),
            "created": True,
            "parent": str(parent),
        }

    def list_directory(
        self,
        raw_path: str | os.PathLike[str] | None = None,
        *,
        cursor: int = 0,
        limit: int = 100,
        include_hidden: bool = False,
        directories_only: bool = False,
        sort: str = "name",
    ) -> dict[str, Any]:
        path = self.resolve_directory(raw_path)
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), MAX_DIRECTORY_PAGE)
        sort = str(sort or "name").strip().lower()
        if sort not in {"name", "modified_desc"}:
            raise RemoteFileSystemError(f"Unsupported directory sort: {sort}")
        try:
            with os.scandir(path) as iterator:
                entries = [self._entry(value) for value in iterator]
        except PermissionError as exc:
            raise RemoteFileSystemError(
                f"The operating system denied access to directory: {path}"
            ) from exc
        except OSError as exc:
            raise RemoteFileSystemError(f"Cannot list directory: {path}") from exc
        if not include_hidden:
            entries = [entry for entry in entries if not entry.hidden]
        if directories_only:
            entries = [entry for entry in entries if entry.kind == "directory"]
        if sort == "modified_desc":
            entries.sort(
                key=lambda entry: (
                    entry.kind != "directory",
                    -dt.datetime.fromisoformat(entry.modified_at).timestamp()
                    if entry.modified_at
                    else float("inf"),
                    entry.name.casefold(),
                    entry.name,
                )
            )
        else:
            entries.sort(
                key=lambda entry: (
                    entry.kind != "directory",
                    entry.name.casefold(),
                    entry.name,
                )
            )
        total = len(entries)
        cursor = min(cursor, total)
        next_cursor = min(total, cursor + limit)
        return {
            **self.inspect(path),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < total,
            "total": total,
            "sort": sort,
            "entries": [entry.to_mapping() for entry in entries[cursor:next_cursor]],
        }


def _list_subdirectories(parent: Path) -> list[Path]:
    result: list[Path] = []
    for path in parent.iterdir():
        try:
            if path.is_dir():
                result.append(path)
        except OSError:
            continue
    return result
