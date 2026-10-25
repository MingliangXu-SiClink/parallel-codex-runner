from __future__ import annotations

import json
import base64
import hashlib
import mimetypes
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from ..diffing import (VISUAL_PREVIEW_EXTENSIONS, WorkspaceChange, WorkspaceEntry,
                      format_workspace_diff, iter_workspace_changes, workspace_changes)
from ..paths import is_relative_to
from ..workspace import agent_execution_workspace
from .state import ManagedRun


RUN_ROOT_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}(?:_[0-9]{3})?$")
RUN_MARKER_NAME = ".pcr-plugin-run.json"
MAX_PREVIEW_FILE_BYTES = 256 * 1024 * 1024
MAX_PREVIEW_CHUNK_BYTES = 384 * 1024


class ArtifactError(RuntimeError):
    pass


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class ArtifactStore:
    """Validate PCR-owned paths and preserve review artifacts before cleanup."""

    @staticmethod
    def _run_root_location(run: ManagedRun, workspace: Path) -> Path:
        raw_root = Path(run.run_root).expanduser()
        if raw_root.is_symlink() or not raw_root.exists() or not raw_root.is_dir():
            raise ArtifactError(
                f"The recorded run root is no longer a real directory: {raw_root}"
            )
        root = raw_root.resolve()
        configured_base = str(run.config.get("run_base") or "").strip()
        if not configured_base:
            raise ArtifactError("The run does not contain a verified run base")
        base = Path(configured_base).expanduser().resolve()
        if is_relative_to(base, workspace) or root.parent != base:
            raise ArtifactError(f"The run root is outside its recorded run base: {root}")
        if RUN_ROOT_PATTERN.fullmatch(root.name) is None:
            raise ArtifactError(f"Unexpected PCR run directory name: {root.name}")
        return root

    @staticmethod
    def _validate_marker(run: ManagedRun, root: Path) -> None:
        marker = root / RUN_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            raise ArtifactError(f"PCR plugin marker is missing: {marker}")
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"Cannot validate PCR plugin marker: {marker}") from exc
        expected = {
            "run_id": run.run_id,
            "workspace": run.workspace,
            "artifact_token": run.artifact_token,
        }
        if run.config.get("workspace_mode") == "source":
            expected["workspace_mode"] = "source"
        if not run.artifact_token or not isinstance(marker_data, dict):
            raise ArtifactError(f"PCR plugin marker is invalid: {marker}")
        if any(marker_data.get(key) != value for key, value in expected.items()):
            raise ArtifactError(f"PCR plugin marker does not match this run: {marker}")

    def workspace(self, run: ManagedRun) -> Path:
        raw = Path(run.workspace).expanduser()
        if raw.is_symlink() or not raw.exists() or not raw.is_dir():
            raise ArtifactError(
                f"The recorded workspace is no longer a real directory: {raw}"
            )
        workspace = raw.resolve()
        if str(workspace) != run.workspace:
            raise ArtifactError(
                f"The recorded workspace now resolves somewhere else: {raw}"
            )
        return workspace

    def run_root(
        self,
        run: ManagedRun,
        *,
        require_marker: bool = True,
    ) -> Path:
        if not run.run_root:
            raise ArtifactError("Run artifacts are not available")
        workspace = self.workspace(run)
        root = self._run_root_location(run, workspace)
        if not require_marker:
            return root
        self._validate_marker(run, root)
        return root

    def write_marker(self, run: ManagedRun) -> None:
        root = self.run_root(run, require_marker=False)
        marker = root / RUN_MARKER_NAME
        if marker.is_symlink():
            raise ArtifactError(f"Refusing to replace a symlinked run marker: {marker}")
        write_json_atomic(
            marker,
            {
                "version": 2,
                "run_id": run.run_id,
                "workspace": run.workspace,
                "artifact_token": run.artifact_token,
                "workspace_mode": run.config.get("workspace_mode", "isolated"),
                **({"supports_agent_questions": True} if run.config.get("supports_agent_questions") is True else {}),
            },
        )

    def workspaces_root(self, run: ManagedRun) -> Path:
        workspace = self.workspace(run)
        run_root = self.run_root(run)
        raw_root = run_root / "workspaces"
        if raw_root.is_symlink():
            raise ArtifactError(
                f"Candidate workspace root was replaced by a symlink: {raw_root}"
            )
        root = raw_root.resolve()
        if root.parent != run_root or is_relative_to(root, workspace):
            raise ArtifactError(f"Refusing to use unverified candidate workspaces: {root}")
        return root

    def agent_workspace(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: str | None = None,
    ) -> Path:
        workspaces_root = self.workspaces_root(run)
        try:
            raw_candidate = agent_execution_workspace(
                self.workspace(run), workspaces_root, agent,
                str(run.config.get("workspace_mode", "isolated")),
            )
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc
        if raw_candidate.is_symlink():
            raise ArtifactError(
                f"Agent workspace was replaced by a symlink: {raw_candidate}"
            )
        candidate = raw_candidate.resolve()
        if recorded_path:
            recorded = Path(recorded_path).expanduser().resolve()
            if recorded != candidate:
                raise ArtifactError(
                    f"Recorded AGENT-{agent:03d} workspace does not match its run"
                )
        return candidate

    def agent_meta_file(self, run: ManagedRun, agent: int, name: str) -> Path:
        root = self.run_root(run)
        path = root / "meta" / f"agent_{agent:03d}" / name
        if path.is_symlink():
            raise ArtifactError(f"Agent artifact was replaced by a symlink: {path}")
        return path

    def diff_path(self, run: ManagedRun, agent: int, *, full_context: bool = False) -> Path:
        root = self.run_root(run)
        directory = root / "plugin" / "diffs"
        if directory.is_symlink():
            raise ArtifactError(f"Diff directory was replaced by a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".full.patch" if full_context else ".patch"
        return directory / f"agent_{agent:03d}{suffix}"

    def persist_diff(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: str | None = None,
        *,
        refresh: bool = False,
        full_context: bool = False,
    ) -> Path:
        destination = self.diff_path(run, agent)
        full_destination = self.diff_path(run, agent, full_context=True)
        if destination.is_symlink() or full_destination.is_symlink():
            raise ArtifactError(f"Diff file was replaced by a symlink: {destination}")
        if destination.is_file() and not refresh:
            if full_context and not full_destination.is_file():
                # A legacy patch may predate full-context retention. Upgrade
                # only if the still-retained trees reproduce that exact patch;
                # never label today's source as an old turn's original content.
                try:
                    baseline, candidate = self._diff_workspaces(run, agent, recorded_path)
                    changes = workspace_changes(baseline, candidate, include_full_context=True)
                    if format_workspace_diff(changes) == destination.read_text(encoding="utf-8"):
                        if not full_destination.is_file():
                            write_text_atomic(full_destination, format_workspace_diff(changes, full_context=True))
                except (OSError, ArtifactError):
                    pass
            return full_destination if full_context and full_destination.is_file() else destination
        baseline, candidate = self._diff_workspaces(run, agent, recorded_path)
        try:
            directory = self._preview_directory(run, agent)
            previews = {}
            changes = []
            # Retain one file pair at a time rather than accumulating every
            # changed image and document in worker memory.
            for change in iter_workspace_changes(baseline, candidate, include_full_context=True,
                                                 include_preview_data=True):
                if change.relative_path.suffix.lower() in VISUAL_PREVIEW_EXTENSIONS:
                    previews[change.relative_path.as_posix()] = self._preview_pair(directory, change)
                changes.append(replace(change, preview_before=None, preview_after=None))
            # Both artifacts derive from the same file bytes, before finalize
            # can replace the source or remove candidate workspaces.
            write_text_atomic(full_destination, format_workspace_diff(changes, full_context=True))
            write_text_atomic(destination, format_workspace_diff(changes))
            write_json_atomic(self._preview_index(run, agent), {"schema": 1, "files": previews})
        except Exception as exc:
            raise ArtifactError(f"Cannot persist the AGENT-{agent:03d} diff: {exc}") from exc
        return full_destination if full_context else destination

    def _preview_directory(self, run: ManagedRun, agent: int) -> Path:
        directory = self.run_root(run)
        for name in ("plugin", "previews", f"agent_{agent:03d}"):
            directory /= name
            if directory.is_symlink():
                raise ArtifactError("The saved file preview directory is not a real directory")
            directory.mkdir(exist_ok=True)
        return directory

    def _preview_index(self, run: ManagedRun, agent: int) -> Path:
        path = self._preview_directory(run, agent) / "index.json"
        if path.is_symlink():
            raise ArtifactError("The saved file preview index is not a regular file")
        return path

    def _preview_version(self, directory: Path, entry: WorkspaceEntry | None,
                         data: bytes | None) -> dict[str, Any]:
        if entry is None:
            return {"state": "absent"}
        if entry.kind != "file" or data is None:
            return {"state": "unavailable"}
        if len(data) > MAX_PREVIEW_FILE_BYTES:
            return {"state": "too_large", "size_bytes": len(data)}
        digest = hashlib.sha256(data).hexdigest()
        destination = directory / digest
        if destination.is_symlink():
            raise ArtifactError("The saved file preview is not a regular file")
        if not destination.exists():
            retained = sum(path.stat().st_size for path in directory.iterdir()
                           if re.fullmatch(r"[0-9a-f]{64}", path.name) and path.is_file() and not path.is_symlink())
            if retained + len(data) > 1024 * 1024 * 1024:
                return {"state": "unavailable"}
            temporary = directory / f".{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return {"state": "available", "file_id": digest, "sha256": digest,
                "size_bytes": len(data)}

    def _preview_pair(self, directory: Path, change: WorkspaceChange) -> dict[str, Any]:
        return {"status": change.status,
                "before": self._preview_version(directory, change.before, change.preview_before),
                "after": self._preview_version(directory, change.after, change.preview_after)}

    def file_preview(self, run: ManagedRun, agent: int, path: str, *, live: bool,
                     recorded_path: str | None = None, side: str = "",
                     file_id: str = "", offset: int = 0,
                     max_bytes: int = MAX_PREVIEW_CHUNK_BYTES) -> dict[str, Any]:
        relative = Path(path)
        if (not path or relative.is_absolute() or path != relative.as_posix() or
                any(part in {".", ".."} for part in relative.parts) or
                relative.suffix.lower() not in VISUAL_PREVIEW_EXTENSIONS):
            raise ArtifactError("This file does not support a visual comparison")
        directory = self._preview_directory(run, agent)
        payload = {"run_id": run.run_id, "agent": agent, "path": path,
                   "name": relative.name, "live": live,
                   "content_type": mimetypes.guess_type(relative.name)[0] or "application/octet-stream"}
        if side:
            # Only immutable, content-addressed copies are paged. Never join
            # a client-supplied workspace path onto the artifact directory.
            if side not in {"before", "after"} or re.fullmatch(r"[0-9a-f]{64}", file_id) is None:
                raise ArtifactError("The requested file version is invalid")
            destination = directory / file_id
            if destination.is_symlink():
                raise ArtifactError("The saved file version is not a regular file")
            if not destination.exists():
                live_directory = directory / "live"
                if live_directory.is_symlink():
                    raise ArtifactError("The live file preview directory is not a real directory")
                destination = live_directory / file_id
            try:
                descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as handle:
                    metadata = os.fstat(handle.fileno())
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ArtifactError("The saved file version is not a regular file")
                    size = metadata.st_size
                    if size > MAX_PREVIEW_FILE_BYTES or offset < 0 or offset > size:
                        raise ArtifactError("The requested file version page is invalid")
                    handle.seek(offset)
                    data = handle.read(min(max(1, max_bytes), MAX_PREVIEW_CHUNK_BYTES))
            except OSError as exc:
                raise ArtifactError("The saved file version is unavailable") from exc
            return {**payload, "side": side, "file_id": file_id, "size_bytes": size,
                    "offset": offset, "next_offset": offset + len(data),
                    "has_more": offset + len(data) < size,
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "chunk_sha256": hashlib.sha256(data).hexdigest()}
        if live:
            live_directory = directory / "live"
            if live_directory.is_symlink():
                raise ArtifactError("The live file preview directory is not a real directory")
            live_directory.mkdir(exist_ok=True)
            for old in live_directory.iterdir():
                if (re.fullmatch(r"[0-9a-f]{64}", old.name) and not old.is_symlink() and
                        old.is_file() and time.time() - old.stat().st_mtime > 1800):
                    old.unlink(missing_ok=True)
            baseline, candidate = self._diff_workspaces(run, agent, recorded_path)
            def read(root: Path) -> tuple[WorkspaceEntry | None, bytes | None]:
                target = root / relative
                for parent in target.parents:
                    if parent == root:
                        break
                    if parent.is_symlink():
                        raise ArtifactError("A linked folder cannot be used as a file version")
                try:
                    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, "rb") as handle:
                        metadata = os.fstat(handle.fileno())
                        if not stat.S_ISREG(metadata.st_mode):
                            raise ArtifactError("This file version is not a regular file")
                        entry = WorkspaceEntry(target, "file", stat.S_IMODE(metadata.st_mode))
                        data = handle.read(MAX_PREVIEW_FILE_BYTES + 1)
                        after_read = os.fstat(handle.fileno())
                        if (after_read.st_size, after_read.st_mtime_ns, after_read.st_ctime_ns) != (
                                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns):
                            raise ArtifactError("The file changed while its preview was being captured")
                        return entry, data
                except FileNotFoundError:
                    return None, None
                except OSError as exc:
                    raise ArtifactError("This file version cannot be read safely") from exc
            before, before_data = read(baseline)
            after, after_data = read(candidate)
            pair = self._preview_pair(live_directory, WorkspaceChange(
                "A" if before is None else "D" if after is None else "M", relative,
                before, after, "", preview_before=before_data, preview_after=after_data))
        else:
            self.persist_diff(run, agent, recorded_path)
            index = self._preview_index(run, agent)
            files = json.loads(index.read_text(encoding="utf-8")).get("files", {}) if index.is_file() else {}
            if not isinstance(files, dict):
                raise ArtifactError("The saved file preview index is invalid")
            pair = files.get(path)
            if pair is None:
                pair = {"status": "M", "before": {"state": "unavailable"},
                        "after": {"state": "unavailable"}}
        return {**payload, **pair}

    def live_diff(self, run: ManagedRun, agent: int, *, full_context: bool) -> str:
        baseline, candidate = self._diff_workspaces(run, agent, None)
        changes = workspace_changes(baseline, candidate, include_full_context=full_context)
        return format_workspace_diff(changes, full_context=full_context)

    def _diff_workspaces(
        self, run: ManagedRun, agent: int, recorded_path: str | None,
    ) -> tuple[Path, Path]:
        baseline = self.workspace(run)
        if run.config.get("workspace_mode") == "source":
            baseline = self.workspaces_root(run) / "baseline"
            if baseline.is_symlink() or not baseline.is_dir():
                raise ArtifactError("The source-folder review baseline is unavailable")
        candidate = self.agent_workspace(run, agent, recorded_path)
        if not candidate.is_dir():
            raise ArtifactError(f"AGENT-{agent:03d} workspace is not available yet")
        return baseline, candidate

    def persist_successful_diffs(self, run: ManagedRun) -> Dict[int, Path]:
        paths: Dict[int, Path] = {}
        for agent, result in sorted(run.results.items()):
            if result.get("status") != "success":
                continue
            paths[agent] = self.persist_diff(
                run,
                agent,
                result.get("workspace_dir"),
                full_context=True,
            )
        return paths

    def remove_codex_homes(self, run: ManagedRun) -> bool:
        root = self.run_root(run)
        meta = root / "meta"
        if meta.is_symlink():
            raise ArtifactError(f"Metadata directory was replaced by a symlink: {meta}")
        if not meta.exists():
            return True
        for codex_home in meta.glob("agent_*/codex_home"):
            agent_dir = codex_home.parent
            if (
                re.fullmatch(r"agent_[0-9]+", agent_dir.name) is None
                or agent_dir.is_symlink()
                or agent_dir.parent.resolve() != meta.resolve()
            ):
                raise ArtifactError(f"Unsafe Agent metadata path: {agent_dir}")
            if codex_home.is_symlink():
                raise ArtifactError(f"Codex home was replaced by a symlink: {codex_home}")
            if codex_home.exists():
                shutil.rmtree(codex_home)
        return not any(meta.glob("agent_*/codex_home"))

    def remove_run_root(self, run: ManagedRun) -> bool:
        if not run.run_root:
            return True
        raw_root = Path(run.run_root).expanduser()
        if not raw_root.exists() and not raw_root.is_symlink():
            return True
        recorded_workspace = Path(run.workspace).expanduser().resolve()
        root = self._run_root_location(run, recorded_workspace)
        self._validate_marker(run, root)
        shutil.rmtree(root)
        return not root.exists()
