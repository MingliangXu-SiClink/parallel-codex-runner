from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .filesystem import FileSystemBrowser
from .protocol import utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - supported Unix endpoints have fcntl
    fcntl = None


REGISTRY_VERSION = 1


class WorkspaceRegistryError(RuntimeError):
    """Workspace registry state or revision is invalid."""


@dataclass
class RegisteredWorkspace:
    workspace_id: str
    path: str
    name: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_opened_at: str = field(default_factory=utc_now)
    last_run_id: str = ""
    last_resume_session_id: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "RegisteredWorkspace | None":
        if not isinstance(value, dict):
            return None
        workspace_id = str(value.get("workspace_id") or "").strip()
        path = str(value.get("path") or "").strip()
        if not workspace_id or not path:
            return None
        return cls(
            workspace_id=workspace_id,
            path=path,
            name=str(value.get("name") or Path(path).name or path),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            last_opened_at=str(value.get("last_opened_at") or utc_now()),
            last_run_id=str(value.get("last_run_id") or ""),
            last_resume_session_id=str(
                value.get("last_resume_session_id") or ""
            ),
        )


class WorkspaceRegistry:
    """Persistent bookmarks for multiple remotely managed workspaces.

    Registration is not an access grant. A paired device may browse any path the
    daemon's machine user can read, then register useful directories for quick access.
    """

    def __init__(self, path: Path, browser: FileSystemBrowser | None = None) -> None:
        self.path = path.expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.browser = browser or FileSystemBrowser()
        self._thread_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            handle = self.lock_path.open("a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()

    def _read_unlocked(self) -> tuple[int, str, dict[str, RegisteredWorkspace]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0, "", {}
        except (OSError, ValueError) as exc:
            raise WorkspaceRegistryError(
                f"Cannot read workspace registry: {self.path}"
            ) from exc
        if not isinstance(payload, dict):
            raise WorkspaceRegistryError("Workspace registry root is invalid")
        try:
            version = int(payload.get("version") or 0)
            revision = max(0, int(payload.get("revision") or 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise WorkspaceRegistryError("Workspace registry state is invalid") from exc
        if version != REGISTRY_VERSION:
            raise WorkspaceRegistryError(
                f"Workspace registry version {version} is not supported"
            )
        records: dict[str, RegisteredWorkspace] = {}
        for value in payload.get("workspaces", []):
            record = RegisteredWorkspace.from_mapping(value)
            if record is not None:
                records[record.workspace_id] = record
        return (
            revision,
            str(payload.get("selected_workspace_id") or ""),
            records,
        )

    def _write_unlocked(
        self,
        revision: int,
        selected_workspace_id: str,
        records: dict[str, RegisteredWorkspace],
    ) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "revision": revision,
            "selected_workspace_id": selected_workspace_id,
            "workspaces": [
                asdict(record)
                for record in sorted(
                    records.values(), key=lambda item: item.created_at
                )
            ],
        }
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _check_revision(actual: int, expected: int | None) -> None:
        if expected is not None and expected != actual:
            raise WorkspaceRegistryError(
                f"Workspace registry changed: expected revision {expected}, "
                f"current revision is {actual}"
            )

    @staticmethod
    def _record_mapping(
        record: RegisteredWorkspace,
        selected_workspace_id: str,
    ) -> dict[str, Any]:
        path = Path(record.path)
        value = asdict(record)
        value.update(
            {
                "selected": record.workspace_id == selected_workspace_id,
                "available": path.is_dir(),
                "readable": os.access(path, os.R_OK | os.X_OK),
                "writable": os.access(path, os.W_OK),
            }
        )
        return value

    def list(self) -> dict[str, Any]:
        with self._locked():
            revision, selected, records = self._read_unlocked()
        ordered = sorted(
            records.values(),
            key=lambda item: (
                item.created_at,
                item.name.casefold(),
                item.workspace_id,
            ),
        )
        return {
            "revision": revision,
            "selected_workspace_id": selected or None,
            "workspaces": [
                self._record_mapping(record, selected) for record in ordered
            ],
        }

    def register(
        self,
        raw_path: str,
        *,
        name: str = "",
        select: bool = True,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        path = self.browser.resolve_directory(raw_path)
        now = utc_now()
        with self._locked():
            revision, selected, records = self._read_unlocked()
            self._check_revision(revision, expected_revision)
            record = next(
                (item for item in records.values() if item.path == str(path)),
                None,
            )
            if record is None:
                record = RegisteredWorkspace(
                    workspace_id=f"ws-{uuid.uuid4().hex[:16]}",
                    path=str(path),
                    name=name.strip() or path.name or str(path),
                )
                records[record.workspace_id] = record
            else:
                record.name = name.strip() or record.name
                record.updated_at = now
                record.last_opened_at = now
            if select:
                selected = record.workspace_id
            revision += 1
            self._write_unlocked(revision, selected, records)
            return {
                "revision": revision,
                "workspace": self._record_mapping(record, selected),
            }

    def require(self, workspace_id: str) -> RegisteredWorkspace:
        with self._locked():
            _revision, _selected, records = self._read_unlocked()
        try:
            return records[workspace_id]
        except KeyError as exc:
            raise WorkspaceRegistryError(
                f"Unknown workspace: {workspace_id}"
            ) from exc

    def select(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            revision, _selected, records = self._read_unlocked()
            self._check_revision(revision, expected_revision)
            try:
                record = records[workspace_id]
            except KeyError as exc:
                raise WorkspaceRegistryError(
                    f"Unknown workspace: {workspace_id}"
                ) from exc
            self.browser.resolve_directory(record.path)
            record.last_opened_at = utc_now()
            record.updated_at = record.last_opened_at
            revision += 1
            self._write_unlocked(revision, workspace_id, records)
            return {
                "revision": revision,
                "workspace": self._record_mapping(record, workspace_id),
            }

    def remove(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            revision, selected, records = self._read_unlocked()
            self._check_revision(revision, expected_revision)
            if workspace_id not in records:
                raise WorkspaceRegistryError(f"Unknown workspace: {workspace_id}")
            records.pop(workspace_id)
            if selected == workspace_id:
                selected = ""
            revision += 1
            self._write_unlocked(revision, selected, records)
            return {
                "revision": revision,
                "removed_workspace_id": workspace_id,
                "selected_workspace_id": selected or None,
            }

    def update_context(
        self,
        workspace_id: str,
        *,
        run_id: str | None = None,
        resume_session_id: str | None = None,
        expected_run_id: str | None = None,
    ) -> int | None:
        with self._locked():
            revision, selected, records = self._read_unlocked()
            record = records.get(workspace_id)
            if record is None:
                return None
            if (
                expected_run_id is not None
                and record.last_run_id != expected_run_id
            ):
                return None
            changed = False
            if run_id is not None:
                changed = changed or record.last_run_id != run_id
                record.last_run_id = run_id
            if resume_session_id is not None:
                changed = (
                    changed
                    or record.last_resume_session_id != resume_session_id
                )
                record.last_resume_session_id = resume_session_id
            if not changed:
                return revision
            record.updated_at = utc_now()
            record.last_opened_at = record.updated_at
            revision += 1
            self._write_unlocked(revision, selected, records)
            return revision
