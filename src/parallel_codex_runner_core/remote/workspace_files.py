from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit


MAX_WORKSPACE_FILE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_FILE_CHUNK_BYTES = 384 * 1024
_LINE_SUFFIX = re.compile(r"^(.*?):([0-9]+)(?::[0-9]+)?$")
_LINE_FRAGMENT = re.compile(r"^L?([0-9]+)(?:-L?[0-9]+)?$", re.IGNORECASE)


class WorkspaceFileError(RuntimeError):
    """A workspace file reference that cannot be served safely."""


@dataclass(frozen=True)
class WorkspaceFileContext:
    workspace_root: Path
    candidate_root: Path | None = None
    expected_candidate_root: Path | None = None
    candidate_synced_to_workspace: bool = False
    literal_paths: bool = False


def is_source_workspace_reference(workspace: Path, reference: str) -> bool:
    path, _ = _reference_path(reference)
    return path.is_absolute() and _relative_to(_normalized_absolute(path), workspace) is not None


def resolve_workspace_item(
    context: WorkspaceFileContext,
    reference: str,
    *,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, object]:
    path, line = _reference_path(reference)
    root, relative = _select_root(context, path)
    target = _resolve_item(root, relative)
    info = target.stat()
    directory = target.is_dir()
    result: dict[str, object] = {
        "name": target.name,
        "path": str(target),
        "relative_path": relative.as_posix(),
        "kind": "directory" if directory else "file",
        "content_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        "size_bytes": info.st_size,
        "scope": "source" if root == context.workspace_root.resolve() else "candidate",
        "entries": [],
        "has_more": False,
        "next_offset": 0,
    }
    if line is not None:
        result["line"] = line
    if directory:
        try:
            children = sorted(target.iterdir(), key=lambda child: child.name.casefold())
            entries = []
            for child in children[max(0, offset):max(0, offset) + min(200, max(1, limit))]:
                try:
                    resolved = _resolve_item(root, child.relative_to(root))
                except WorkspaceFileError:
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(resolved),
                    "kind": "directory" if resolved.is_dir() else "file",
                })
            next_offset = min(len(children), max(0, offset) + min(200, max(1, limit)))
            result.update(entries=entries, next_offset=next_offset, has_more=next_offset < len(children))
        except OSError as exc:
            raise WorkspaceFileError("Cannot read this workspace folder") from exc
    return result


def read_workspace_file(
    context: WorkspaceFileContext,
    reference: str,
    *,
    offset: int = 0,
    max_bytes: int = MAX_WORKSPACE_FILE_CHUNK_BYTES,
    expected_file_id: str = "",
) -> dict[str, object]:
    if offset < 0:
        raise WorkspaceFileError("File offset must not be negative")
    if max_bytes < 1:
        raise WorkspaceFileError("File page size must be positive")
    max_bytes = min(max_bytes, MAX_WORKSPACE_FILE_CHUNK_BYTES)

    path, line = _reference_path(reference)
    root, relative = _select_root(context, path)
    target = _resolve_item(root, relative)
    if not target.is_file():
        raise WorkspaceFileError("The selected workspace item is not a file")

    try:
        with target.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise WorkspaceFileError("The selected workspace item is not a file")
            if before.st_size > MAX_WORKSPACE_FILE_BYTES:
                raise WorkspaceFileError(
                    "This file is larger than the 256 MiB mobile transfer limit"
                )
            if offset > before.st_size:
                raise WorkspaceFileError("File offset is beyond the end of the file")
            file_id = _file_id(before)
            if expected_file_id and expected_file_id != file_id:
                raise WorkspaceFileError(
                    "The workspace file changed while it was being downloaded"
                )
            stream.seek(offset)
            content = stream.read(max_bytes)
            after = os.fstat(stream.fileno())
    except WorkspaceFileError:
        raise
    except OSError as exc:
        raise WorkspaceFileError(f"Cannot read this workspace file: {exc}") from exc

    if _file_id(after) != file_id:
        raise WorkspaceFileError(
            "The workspace file changed while it was being downloaded"
        )

    next_offset = offset + len(content)
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    result: dict[str, object] = {
        "name": target.name,
        "relative_path": relative.as_posix(),
        "content_type": content_type,
        "size_bytes": before.st_size,
        "modified_ns": before.st_mtime_ns,
        "file_id": file_id,
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset < before.st_size,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "chunk_sha256": hashlib.sha256(content).hexdigest(),
    }
    if line is not None:
        result["line"] = line
    return result


def _reference_path(reference: str) -> tuple[Path, int | None]:
    value = str(reference or "").strip()
    if not value or "\x00" in value:
        raise WorkspaceFileError("The workspace file link is empty or invalid")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"", "file"}:
        raise WorkspaceFileError("Only links to files in this workspace can be opened")
    if scheme == "file" and parsed.netloc not in {"", "localhost"}:
        raise WorkspaceFileError("Remote file URLs are not supported")

    raw_path = unquote(parsed.path)
    line: int | None = None
    fragment = unquote(parsed.fragment).strip()
    match = _LINE_FRAGMENT.fullmatch(fragment)
    if match is not None:
        line = int(match.group(1))
    suffix = _LINE_SUFFIX.fullmatch(raw_path)
    if suffix is not None:
        raw_path = suffix.group(1)
        line = line or int(suffix.group(2))
    if not raw_path:
        raise WorkspaceFileError("The workspace file link does not contain a path")
    return Path(raw_path).expanduser(), line


def _select_root(
    context: WorkspaceFileContext,
    reference: Path,
) -> tuple[Path, Path]:
    workspace = _verified_root(context.workspace_root, "workspace")
    # An explicit source path names the source, not whichever candidate happens
    # to be selected. It remains usable after the run or its copies are removed.
    if context.literal_paths and reference.is_absolute():
        relative = _relative_to(_normalized_absolute(reference), workspace)
        if relative is not None:
            return workspace, relative
    candidate = _optional_verified_root(context.candidate_root)
    expected_candidate = _normalized_absolute(context.expected_candidate_root)
    if (
        candidate is not None
        and expected_candidate is not None
        and candidate != expected_candidate
    ):
        raise WorkspaceFileError("This Agent working copy does not match the selected run")

    if not reference.is_absolute():
        relative = _safe_relative(reference)
        if candidate is not None:
            return candidate, relative
        if context.literal_paths and expected_candidate is not None:
            raise WorkspaceFileError("This Agent working copy is no longer available")
        if context.candidate_synced_to_workspace or context.candidate_root is None:
            return workspace, relative
        raise WorkspaceFileError("This Agent working copy is no longer available")

    absolute = _normalized_absolute(reference)
    if candidate is not None:
        relative = _relative_to(absolute, candidate)
        if relative is not None:
            return candidate, relative
    relative = _relative_to(absolute, workspace)
    if relative is not None:
        if expected_candidate is None:
            return workspace, relative
        if candidate is not None:
            return candidate, relative
        if context.candidate_synced_to_workspace:
            return workspace, relative
        raise WorkspaceFileError("This Agent working copy is no longer available")
    if expected_candidate is not None:
        relative = _relative_to(absolute, expected_candidate)
        if relative is not None:
            if candidate is not None:
                return candidate, relative
            if context.candidate_synced_to_workspace and not context.literal_paths:
                return workspace, relative
            raise WorkspaceFileError("This Agent working copy is no longer available")
    raise WorkspaceFileError("This link points outside the selected workspace")


def _verified_root(path: Path, label: str) -> Path:
    try:
        if path.is_symlink():
            raise WorkspaceFileError(f"The {label} path is not trusted")
        root = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise WorkspaceFileError(f"The {label} is no longer available") from exc
    if not root.is_dir():
        raise WorkspaceFileError(f"The {label} is no longer available")
    return root


def _optional_verified_root(path: Path | None) -> Path | None:
    if path is None or not path.is_dir():
        return None
    return _verified_root(path, "Agent workspace")


def _normalized_absolute(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve(strict=False)


def _safe_relative(path: Path) -> Path:
    normalized = Path(os.path.normpath(str(path)))
    if normalized.is_absolute() or normalized == Path("..") or ".." in normalized.parts:
        raise WorkspaceFileError("This link points outside the selected workspace")
    return normalized


def _relative_to(path: Path | None, root: Path) -> Path | None:
    if path is None:
        return None
    try:
        return _safe_relative(path.relative_to(root))
    except ValueError:
        return None


def _resolve_item(root: Path, relative: Path) -> Path:
    raw_target = root / relative
    try:
        target = raw_target.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceFileError("This workspace file is no longer available") from exc
    if _relative_to(target, root) is None:
        raise WorkspaceFileError("This link points outside the selected workspace")
    if not target.is_file() and not target.is_dir():
        raise WorkspaceFileError("The selected workspace item is not a regular file or folder")
    return target


def _file_id(info: os.stat_result) -> str:
    identity = ":".join(
        str(value)
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
    )
    return hashlib.sha256(identity.encode("ascii")).hexdigest()
