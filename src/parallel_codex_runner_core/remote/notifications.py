from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..run_statuses import TERMINAL_AGENT_STATUSES, TERMINAL_RUN_STATUSES


def execution_completion_eligible(run: Mapping[str, Any]) -> bool:
    """Review/adoption/cleanup are not execution-completion events."""
    return (
        str(run.get("status") or "").strip().lower() in {"completed", "failed", "error"}
        and str(run.get("completion_reason") or "").strip().lower() in {"", "execution"}
        and run.get("finalized_agent") is None
        and run.get("finalizing_agent") is None
        and str(run.get("worker_operation") or "") not in {"finalize", "recover_finalization", "discard"}
    )


class CompletionSnapshotSource(Protocol):
    def completion_snapshot(self) -> list[dict[str, Any]]: ...


class RelayCompletionProxy(Protocol):
    def call(
        self,
        *,
        machine_id: str,
        machine_public_key: str,
        action: str,
        body: Mapping[str, Any],
        expected_revision: int | None,
        command_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class MacOSRunNotifier:
    """Deliver a macOS notification without requiring the SwiftUI app to run."""

    def __call__(self, workspace_name: str, run_id: str) -> bool:
        if sys.platform != "darwin":
            return False
        app = Path(os.environ.get("PCR_MAC_NOTIFICATION_APP") or "/Applications/Parallel Codex.app")
        executable = app / "Contents/MacOS/Parallel Codex"
        if not executable.is_file():
            return False
        try:
            with (app / "Contents/Info.plist").open("rb") as handle:
                if plistlib.load(handle).get("PCRNativeCompletionNotifications") is not True:
                    return False
        except (OSError, ValueError):
            return False
        try:
            completed = subprocess.run(
                [
                    str(executable),
                    "--pcr-notify-run-completed",
                    run_id,
                    workspace_name or "Parallel Codex",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0


class RunCompletionMonitor:
    """Persist terminal-transition tracking across daemon and app restarts."""

    def __init__(
        self,
        source: CompletionSnapshotSource,
        state_path: Path,
        *,
        notifier: Callable[[str, str], bool] | None = None,
        poll_interval: float = 3.0,
        clock: Callable[[], float] | None = None,
        retry_base_seconds: float = 30.0,
        retry_max_seconds: float = 3600.0,
    ) -> None:
        self.source = source
        self.state_path = state_path
        self.notifier = notifier or MacOSRunNotifier()
        self.poll_interval = max(0.1, float(poll_interval))
        self.clock = clock or time.time
        self.retry_base_seconds = max(1.0, float(retry_base_seconds))
        self.retry_max_seconds = max(
            self.retry_base_seconds,
            float(retry_max_seconds),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        state = self._read_state()
        self._initialized = bool(state.get("initialized", False))
        self._known = self._string_set(state.get("known_runs"))
        self._observed_working = self._string_set(
            state.get("observed_working_runs")
        )
        self._completed = self._string_set(state.get("completed_runs"))
        if int(state.get("version") or 0) < 3:
            self._completed.update(self._known - self._observed_working)
        self._notification_retries = self._retry_state(
            state.get("notification_retries")
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="pcrd-run-completion-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.poll_interval + 1.0))
        self._thread = None

    def poll_once(self) -> None:
        records = {
            str(record.get("run_id") or "").strip(): record
            for record in self.source.completion_snapshot()
            if isinstance(record, dict)
            and str(record.get("run_id") or "").strip()
        }
        current_ids = set(records)
        changed = False

        if not self._initialized:
            self._initialized = True
            self._known = current_ids
            self._observed_working = {
                run_id
                for run_id, record in records.items()
                if bool(record.get("working"))
            }
            self._completed = current_ids - self._observed_working
            self._save_state()
            return

        missing = self._known - current_ids
        if missing:
            self._known.difference_update(missing)
            self._observed_working.difference_update(missing)
            self._completed.difference_update(missing)
            for run_id in missing:
                self._notification_retries.pop(run_id, None)
            changed = True

        for run_id, record in records.items():
            if run_id in self._completed:
                continue
            is_new = run_id not in self._known
            if is_new:
                self._known.add(run_id)
                changed = True
            if bool(record.get("working")):
                if self._notification_retries.pop(run_id, None) is not None:
                    changed = True
                if run_id not in self._observed_working:
                    self._observed_working.add(run_id)
                    changed = True
                continue

            terminal = bool(record.get("all_agents_terminal"))
            should_notify = terminal and execution_completion_eligible(record) and (
                is_new or run_id in self._observed_working
            )
            if should_notify:
                if run_id not in self._observed_working:
                    self._observed_working.add(run_id)
                    changed = True
                workspace_name = str(record.get("workspace_name") or "").strip()
                now = self.clock()
                attempts, next_retry = self._notification_retries.get(
                    run_id,
                    (0, 0.0),
                )
                if next_retry > now:
                    continue
                if self.notifier(workspace_name, run_id):
                    self._completed.add(run_id)
                    self._observed_working.discard(run_id)
                    self._notification_retries.pop(run_id, None)
                    changed = True
                else:
                    attempts += 1
                    delay = min(
                        self.retry_base_seconds * (2 ** min(attempts - 1, 16)),
                        self.retry_max_seconds,
                    )
                    self._notification_retries[run_id] = (attempts, now + delay)
                    changed = True
            elif run_id in self._observed_working and self._terminal_run(record):
                self._completed.add(run_id)
                self._observed_working.discard(run_id)
                self._notification_retries.pop(run_id, None)
                changed = True

        if changed:
            self._save_state()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # A monitor failure must never terminate pcrd or a running task.
                pass
            self._stop.wait(self.poll_interval)

    @staticmethod
    def _terminal_run(record: dict[str, Any]) -> bool:
        return (
            str(record.get("status") or "").strip().lower()
            in TERMINAL_RUN_STATUSES
        )

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        payload = {
            "version": 3,
            "initialized": self._initialized,
            "known_runs": sorted(self._known),
            "observed_working_runs": sorted(self._observed_working),
            "completed_runs": sorted(self._completed),
            "notification_retries": {
                run_id: {"attempts": attempts, "next_retry_at": next_retry}
                for run_id, (attempts, next_retry) in sorted(
                    self._notification_retries.items()
                )
            },
        }
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    @staticmethod
    def _string_set(value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            text
            for item in value
            if (text := str(item or "").strip())
        }

    @staticmethod
    def _retry_state(value: object) -> dict[str, tuple[int, float]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, tuple[int, float]] = {}
        for raw_run_id, raw_retry in value.items():
            run_id = str(raw_run_id or "").strip()
            if not run_id or not isinstance(raw_retry, dict):
                continue
            try:
                attempts = max(0, int(raw_retry.get("attempts") or 0))
                next_retry = max(
                    0.0,
                    float(raw_retry.get("next_retry_at") or 0.0),
                )
            except (TypeError, ValueError, OverflowError):
                continue
            result[run_id] = (attempts, next_retry)
        return result


class RelayRunCompletionMonitor:
    """Keep remote-machine completion notifications alive after the app exits."""

    def __init__(
        self,
        proxy: RelayCompletionProxy,
        state_path: Path,
        *,
        notifier: Callable[[str, str], bool] | None = None,
        poll_interval: float = 15.0,
        clock: Callable[[], float] | None = None,
        retry_base_seconds: float = 15.0,
        retry_max_seconds: float = 15 * 60.0,
    ) -> None:
        self.proxy = proxy
        self.state_path = state_path
        self.notifier = notifier or MacOSRunNotifier()
        self.poll_interval = max(1.0, float(poll_interval))
        self.clock = clock or time.time
        self.retry_base_seconds = max(1.0, float(retry_base_seconds))
        self.retry_max_seconds = max(
            self.retry_base_seconds,
            float(retry_max_seconds),
        )
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watches = self._read_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._completed = RunCompletionMonitor._string_set(payload.get("completed_watches"))
        except (OSError, ValueError, AttributeError):
            self._completed = set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="pcrd-relay-run-completion-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, min(self.poll_interval, 5.0) + 1.0))
        self._thread = None

    @staticmethod
    def watch_key(machine_id: str, run_id: str) -> str:
        return f"{machine_id}\u001f{run_id}"

    def watch(
        self,
        *,
        machine_id: str,
        machine_public_key: str,
        run_id: str,
        workspace_name: str,
    ) -> dict[str, Any]:
        machine_id = machine_id.strip()
        machine_public_key = machine_public_key.strip()
        run_id = run_id.strip()
        workspace_name = workspace_name.strip()
        if not machine_id or not machine_public_key or not run_id:
            raise ValueError("machine_id, machine_public_key, and run_id are required")
        key = self.watch_key(machine_id, run_id)
        with self._lock:
            if key in self._completed:
                return {"watching": True, "completed": True, "machine_id": machine_id,
                        "run_id": run_id, "watch_count": len(self._watches)}
            previous = self._watches.get(key, {})
            self._watches[key] = {
                "machine_id": machine_id,
                "machine_public_key": machine_public_key,
                "run_id": run_id,
                "workspace_name": workspace_name or "Parallel Codex",
                "attempts": int(previous.get("attempts") or 0),
                "next_poll_at": min(
                    float(previous.get("next_poll_at") or 0.0),
                    self.clock(),
                ),
            }
            self._save_state_locked()
            return {
                "watching": True,
                "machine_id": machine_id,
                "run_id": run_id,
                "watch_count": len(self._watches),
            }

    def unwatch(self, *, machine_id: str, run_id: str) -> dict[str, Any]:
        key = self.watch_key(machine_id.strip(), run_id.strip())
        with self._lock:
            removed = self._watches.pop(key, None) is not None
            if removed:
                self._save_state_locked()
            return {
                "watching": False,
                "removed": removed,
                "machine_id": machine_id.strip(),
                "run_id": run_id.strip(),
                "watch_count": len(self._watches),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "watch_count": len(self._watches),
                "watches": [
                    {
                        "machine_id": record["machine_id"],
                        "run_id": record["run_id"],
                        "workspace_name": record["workspace_name"],
                    }
                    for record in self._watches.values()
                ],
            }

    def poll_once(self) -> None:
        now = self.clock()
        with self._lock:
            due = [
                (key, dict(record))
                for key, record in self._watches.items()
                if float(record.get("next_poll_at") or 0.0) <= now
            ]
        for key, record in due:
            try:
                response = self.proxy.call(
                    machine_id=str(record["machine_id"]),
                    machine_public_key=str(record["machine_public_key"]),
                    action="turns.get",
                    body={
                        "run_id": str(record["run_id"]),
                        "include_events": False,
                        "cursor": 0,
                        "event_limit": 1,
                    },
                    expected_revision=None,
                    command_id=f"completion-watch-{uuid.uuid4().hex}",
                    timeout_seconds=30,
                )
                body = response.get("body")
                if not bool(response.get("ok")) or not isinstance(body, Mapping):
                    raise RuntimeError("The remote run status is unavailable")
                if (str(body.get("status") or "").strip().lower() in TERMINAL_RUN_STATUSES
                        and not bool(body.get("worker_active"))
                        and not execution_completion_eligible(body)):
                    with self._lock:
                        self._completed.add(key)
                        self._watches.pop(key, None)
                        self._save_state_locked()
                    continue
                if self._all_agents_terminal(body):
                    with self._lock:
                        if key not in self._watches or key in self._completed:
                            continue
                    delivered = self.notifier(
                        str(record.get("workspace_name") or "Parallel Codex"),
                        str(record["run_id"]),
                    )
                    with self._lock:
                        if delivered:
                            # Another window may refresh this watch during delivery.
                            # Its newer polling metadata must not rearm the same run.
                            self._completed.add(key)
                            self._watches.pop(key, None)
                        elif self._watches.get(key) == record:
                            self._schedule_retry_locked(key, record, now)
                        self._save_state_locked()
                    continue
                with self._lock:
                    if self._watches.get(key) == record:
                        record["attempts"] = 0
                        record["next_poll_at"] = now + self.poll_interval
                        self._watches[key] = record
                        self._save_state_locked()
            except Exception:
                with self._lock:
                    if self._watches.get(key) == record:
                        self._schedule_retry_locked(key, record, now)
                        self._save_state_locked()

    @staticmethod
    def _all_agents_terminal(run: Mapping[str, Any]) -> bool:
        if not execution_completion_eligible(run):
            return False
        if bool(run.get("worker_active")) or str(run.get("status") or "").lower() in {
            "preparing", "running", "stopping", "finalizing",
        }:
            return False
        agents = run.get("agents")
        if not agents or not isinstance(agents, list):
            return False
        return all(
            isinstance(agent, Mapping)
            and str(agent.get("status") or "").strip().lower()
            in TERMINAL_AGENT_STATUSES
            for agent in agents
        )

    def _schedule_retry_locked(
        self,
        key: str,
        record: dict[str, Any],
        now: float,
    ) -> None:
        attempts = int(record.get("attempts") or 0) + 1
        delay = min(
            self.retry_base_seconds * (2 ** min(attempts - 1, 16)),
            self.retry_max_seconds,
        )
        record["attempts"] = attempts
        record["next_poll_at"] = now + delay
        self._watches[key] = record

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(min(self.poll_interval, 5.0))

    def _read_state(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        raw_watches = payload.get("watches") if isinstance(payload, dict) else None
        if not isinstance(raw_watches, list):
            return {}
        watches: dict[str, dict[str, Any]] = {}
        for raw in raw_watches:
            if not isinstance(raw, dict):
                continue
            machine_id = str(raw.get("machine_id") or "").strip()
            machine_public_key = str(raw.get("machine_public_key") or "").strip()
            run_id = str(raw.get("run_id") or "").strip()
            if not machine_id or not machine_public_key or not run_id:
                continue
            key = self.watch_key(machine_id, run_id)
            watches[key] = {
                "machine_id": machine_id,
                "machine_public_key": machine_public_key,
                "run_id": run_id,
                "workspace_name": str(
                    raw.get("workspace_name") or "Parallel Codex"
                ).strip(),
                "attempts": max(0, int(raw.get("attempts") or 0)),
                "next_poll_at": max(0.0, float(raw.get("next_poll_at") or 0.0)),
            }
        return watches

    def _save_state_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        payload = {
            "version": 2,
            "watches": list(self._watches.values()),
            "completed_watches": sorted(self._completed),
        }
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)
