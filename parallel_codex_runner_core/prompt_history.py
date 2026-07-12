from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from platformdirs import user_state_path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


_PROCESS_LOCK = threading.Lock()


def default_prompt_history_path() -> Path:
    override = os.environ.get("PCR_PROMPT_HISTORY_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return user_state_path("parallel-codex-runner", appauthor=False) / "prompt_history.json"


class PromptHistoryStore:
    """Persistent prompt history partitioned by workspace and Codex session."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_prompt_history_path()).expanduser().resolve()
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    @staticmethod
    def context_key(workspace: Path | str, session_id: str | None) -> tuple[str, str]:
        workspace_key = str(Path(workspace).expanduser().resolve())
        return workspace_key, str(session_id or "").strip()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            with self.lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "workspaces": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "workspaces": {}}
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        return {"version": 1, "workspaces": workspaces}

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def entries(self, workspace: Path | str, session_id: str | None) -> list[str]:
        workspace_key, session_key = self.context_key(workspace, session_id)
        try:
            with self._locked():
                payload = self._read_unlocked()
                workspaces = payload["workspaces"]
                sessions = workspaces.get(workspace_key)
                if not isinstance(sessions, dict):
                    return []
                values = sessions.get(session_key)
                if not isinstance(values, list):
                    return []
                return [value for value in values if isinstance(value, str) and value]
        except OSError:
            return []

    def append(
        self,
        workspace: Path | str,
        session_id: str | None,
        prompt: str,
        *,
        deduplicate_last: bool = False,
    ) -> bool:
        if not prompt:
            return False
        workspace_key, session_key = self.context_key(workspace, session_id)
        try:
            with self._locked():
                payload = self._read_unlocked()
                workspaces = payload["workspaces"]
                sessions = workspaces.setdefault(workspace_key, {})
                if not isinstance(sessions, dict):
                    sessions = {}
                    workspaces[workspace_key] = sessions
                values = sessions.setdefault(session_key, [])
                if not isinstance(values, list):
                    values = []
                    sessions[session_key] = values
                if deduplicate_last and values and values[-1] == prompt:
                    return True
                values.append(prompt)
                self._write_unlocked(payload)
            return True
        except OSError:
            return False


class PromptHistoryNavigator:
    """Shell-style history navigation with a mutable newest draft slot."""

    def __init__(self, entries: Sequence[str] = (), draft: str = "") -> None:
        self.reset(entries, draft)

    def reset(self, entries: Sequence[str], draft: str = "") -> None:
        self.entries = [entry for entry in entries if isinstance(entry, str) and entry]
        self.draft = draft
        self.index = len(self.entries)

    def note_edit(self, text: str) -> None:
        self.draft = text
        self.index = len(self.entries)

    def navigate(self, direction: int, current_text: str) -> str:
        if self.index < len(self.entries) and current_text != self.entries[self.index]:
            self.note_edit(current_text)
        elif self.index == len(self.entries):
            self.draft = current_text

        if direction < 0 and self.entries:
            self.index = max(0, self.index - 1)
        elif direction > 0 and self.index < len(self.entries):
            self.index += 1

        if self.index == len(self.entries):
            return self.draft
        return self.entries[self.index]
