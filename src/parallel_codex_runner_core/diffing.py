from __future__ import annotations

import difflib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from .workspace import EXCLUDE_NAMES

VISUAL_PREVIEW_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".ico", ".heic", ".avif", ".svg", ".pdf", ".docx",
})

__all__ = [
    "WorkspaceChange",
    "WorkspaceEntry",
    "build_workspace_diff_text",
    "format_workspace_diff",
    "workspace_changes",
]


@dataclass(frozen=True)
class WorkspaceEntry:
    path: Path
    kind: str
    mode: int
    link_target: str = ""


@dataclass(frozen=True)
class WorkspaceChange:
    status: str
    relative_path: Path
    before: Optional[WorkspaceEntry]
    after: Optional[WorkspaceEntry]
    patch: str
    full_patch: str | None = None
    preview_before: bytes | None = None
    preview_after: bytes | None = None


def _workspace_entries(root: Path) -> Dict[Path, WorkspaceEntry]:
    entries: Dict[Path, WorkspaceEntry] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_root = current_path.relative_to(root)

        kept_directories = []
        for name in directories:
            if name in EXCLUDE_NAMES:
                continue
            path = current_path / name
            relative_path = relative_root / name
            if path.is_symlink():
                entries[relative_path] = WorkspaceEntry(
                    path=path,
                    kind="symlink",
                    mode=stat.S_IMODE(path.lstat().st_mode),
                    link_target=os.readlink(path),
                )
            else:
                kept_directories.append(name)
        directories[:] = kept_directories

        for name in files:
            if name in EXCLUDE_NAMES:
                continue
            path = current_path / name
            relative_path = relative_root / name
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                entries[relative_path] = WorkspaceEntry(
                    path=path,
                    kind="symlink",
                    mode=stat.S_IMODE(metadata.st_mode),
                    link_target=os.readlink(path),
                )
            elif stat.S_ISREG(metadata.st_mode):
                entries[relative_path] = WorkspaceEntry(
                    path=path,
                    kind="file",
                    mode=stat.S_IMODE(metadata.st_mode),
                )
    return entries


def _read_entry(entry: Optional[WorkspaceEntry]) -> Optional[bytes]:
    if entry is None:
        return b""
    if entry.kind == "symlink":
        return (entry.link_target + "\n").encode("utf-8")
    try:
        return entry.path.read_bytes()
    except OSError:
        return None


def _decode_text(data: bytes) -> Optional[str]:
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _git_mode(entry: WorkspaceEntry) -> int:
    if entry.kind == "symlink":
        return 0o120000
    return 0o100000 | entry.mode


def _mode_lines(before: Optional[WorkspaceEntry], after: Optional[WorkspaceEntry]) -> list[str]:
    if before is None and after is not None:
        return [f"new file mode {_git_mode(after):06o}\n"]
    if before is not None and after is None:
        return [f"deleted file mode {_git_mode(before):06o}\n"]
    if before is None or after is None:
        return []
    before_mode = _git_mode(before)
    after_mode = _git_mode(after)
    if before_mode == after_mode:
        return []
    return [f"old mode {before_mode:06o}\n", f"new mode {after_mode:06o}\n"]


def _entry_patch(
    relative_path: Path,
    before: Optional[WorkspaceEntry],
    after: Optional[WorkspaceEntry],
    before_data: Optional[bytes],
    after_data: Optional[bytes],
    *,
    full_context: bool = False,
) -> str:
    path_text = relative_path.as_posix()
    if before_data is None or after_data is None:
        return f"Binary or unreadable file changed: {path_text}\n"
    if before_data == after_data and before is not None and after is not None:
        return "".join(_mode_lines(before, after))

    before_text = _decode_text(before_data)
    after_text = _decode_text(after_data)
    mode_text = "".join(_mode_lines(before, after))
    if before_text is None or after_text is None:
        return mode_text + f"Binary files a/{path_text} and b/{path_text} differ\n"

    from_file = f"a/{path_text}" if before is not None else "/dev/null"
    to_file = f"b/{path_text}" if after is not None else "/dev/null"
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=from_file,
        tofile=to_file,
        n=max(len(before_lines), len(after_lines)) if full_context else 3,
    )
    patch_parts: list[str] = []
    for line in diff_lines:
        has_newline = line.endswith("\n")
        patch_parts.append(line if has_newline else line + "\n")
        if (
            not has_newline
            and line.startswith((" ", "+", "-"))
            and not line.startswith(("+++", "---"))
        ):
            patch_parts.append("\\ No newline at end of file\n")
    patch = "".join(patch_parts)
    return mode_text + patch


def workspace_changes(
    baseline: Path, candidate: Path, *, include_full_context: bool = False,
    include_preview_data: bool = False,
) -> list[WorkspaceChange]:
    return list(iter_workspace_changes(baseline, candidate,
                include_full_context=include_full_context, include_preview_data=include_preview_data))


def iter_workspace_changes(
    baseline: Path, candidate: Path, *, include_full_context: bool = False,
    include_preview_data: bool = False,
) -> Iterator[WorkspaceChange]:
    baseline = baseline.expanduser().resolve()
    candidate = candidate.expanduser().resolve()
    if not baseline.is_dir():
        raise FileNotFoundError(f"baseline workspace not found: {baseline}")
    if not candidate.is_dir():
        raise FileNotFoundError(f"agent workspace not found: {candidate}")

    before_entries = _workspace_entries(baseline)
    after_entries = _workspace_entries(candidate)
    for relative_path in sorted(set(before_entries) | set(after_entries), key=lambda path: path.as_posix()):
        before = before_entries.get(relative_path)
        after = after_entries.get(relative_path)
        before_data = _read_entry(before)
        after_data = _read_entry(after)
        if before is None:
            status = "A"
        elif after is None:
            status = "D"
        elif before.kind != after.kind:
            status = "T"
        else:
            if (
                before_data is not None
                and after_data is not None
                and before_data == after_data
                and before.mode == after.mode
            ):
                continue
            status = "M"
        yield WorkspaceChange(
                status=status,
                relative_path=relative_path,
                before=before,
                after=after,
                patch=_entry_patch(
                    relative_path,
                    before,
                    after,
                    before_data,
                    after_data,
                ),
                full_patch=_entry_patch(
                    relative_path, before, after, before_data, after_data,
                    full_context=True,
                ) if include_full_context else None,
                preview_before=before_data if include_preview_data and
                    relative_path.suffix.lower() in VISUAL_PREVIEW_EXTENSIONS else None,
                preview_after=after_data if include_preview_data and
                    relative_path.suffix.lower() in VISUAL_PREVIEW_EXTENSIONS else None,
        )


def format_workspace_diff(changes: Iterable[WorkspaceChange], *, full_context: bool = False) -> str:
    change_list = list(changes)
    if not change_list:
        return "No workspace changes."

    counts = {
        status: sum(change.status == status for change in change_list)
        for status in ("A", "M", "D", "T")
    }
    file_label = "file" if len(change_list) == 1 else "files"
    lines = [
        *(f"{change.status}  {change.relative_path.as_posix()}" for change in change_list),
        "",
        (
            f"{len(change_list)} {file_label} changed "
            f"({counts['A']} added, {counts['M']} modified, "
            f"{counts['D']} deleted, {counts['T']} type changed)"
        ),
    ]
    patches = [
        f"diff --git {json.dumps('a/' + change.relative_path.as_posix(), ensure_ascii=False)} "
        f"{json.dumps('b/' + change.relative_path.as_posix(), ensure_ascii=False)}\n"
        + (change.full_patch if full_context and change.full_patch is not None else change.patch).rstrip("\n")
        for change in change_list if change.patch
    ]
    if patches:
        lines.extend(["", "\n\n".join(patches)])
    return "\n".join(lines)


def build_workspace_diff_text(baseline: Path, candidate: Path) -> str:
    return format_workspace_diff(workspace_changes(baseline, candidate))
