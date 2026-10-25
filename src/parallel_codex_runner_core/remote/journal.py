from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .protocol import ProtocolError, RemoteRequest, utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - supported Unix endpoints have fcntl
    fcntl = None


class CommandJournalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommandJournal:
    """Durable at-most-once protection for commands replayed by the relay."""

    DEFAULT_RETENTION_SECONDS = 30 * 24 * 60 * 60
    DEFAULT_PRUNE_INTERVAL_SECONDS = 60 * 60
    PROMPT_ACTIONS = {"runs.start", "turns.start", "runs.continue", "turns.continue"}

    def __init__(
        self,
        directory: Path,
        *,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        prune_interval_seconds: float = DEFAULT_PRUNE_INTERVAL_SECONDS,
    ) -> None:
        self.directory = directory.expanduser().resolve()
        self.retention_seconds = max(24 * 60 * 60, int(retention_seconds))
        self.prune_interval_seconds = max(1.0, float(prune_interval_seconds))
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self._thread_lock = threading.RLock()
        self._key_locks: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()
        self._pending_keys: set[str] = set()
        with self._locked("receipt-store"):
            store_path = self.directory / ".store.json"
            store = self._read(store_path)
            if not store:
                store = {"id": uuid.uuid4().hex}
                self._write(store_path, store)
            self.store_id = str(store.get("id") or "")
            if not self.store_id:
                raise CommandJournalError("journal_corrupt", "Command receipt store identity is invalid")
        self.prune()
        self._last_prune_monotonic = time.monotonic()
        for path in self.directory.glob("*.json"):
            try:
                record = self._read(path)
                if record.get("status") == "retryable" and isinstance(record.get("request"), dict):
                    self._pending_keys.add(path.stem)
            except CommandJournalError:
                continue

    @staticmethod
    def _key(request: RemoteRequest) -> str:
        value = f"{request.client_id}\0{request.command_id}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _fingerprint(request: RemoteRequest) -> str:
        encoded = json.dumps(
            {
                "action": request.action,
                "body": request.body,
                "expected_revision": request.expected_revision,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def identity(cls, request: RemoteRequest) -> dict[str, str]:
        return {"client_id": request.client_id, "command_id": request.command_id,
                "fingerprint": cls._fingerprint(request)}

    @staticmethod
    def _history_recovery_required(record: dict[str, Any]) -> bool:
        error = record.get("error") or {}
        return isinstance(record.get("history_recovery"), dict) or (
            record.get("status") == "failed"
            and record.get("action") in {"runs.continue", "turns.continue"}
            and isinstance(error, dict) and error.get("type") == "RemoteControllerError"
            and error.get("code") == "invalid_argument"
            and error.get("delivery_outcome") != "not_applied"
            and str(error.get("message") or "").startswith(
                "Unsupported Codex history schema; no global database was copied: "))

    @staticmethod
    def _retryable_record(
        record: dict[str, Any], request: RemoteRequest | None = None,
        can_retry: Callable[[RemoteRequest], bool] | None = None,
    ) -> bool:
        if CommandJournal._history_recovery_required(record):
            return request is not None and can_retry is not None and can_retry(request)
        if record.get("status") == "retryable":
            return True
        # This mutation has its own transactional, immutable native-request
        # answer receipt. Retrying a crashed journal entry cannot answer twice.
        if (request is not None and request.action == "runs.answer_question"
            and record.get("action") == request.action and record.get("status") == "started"):
            return True
        error = record.get("error") or {}
        if not isinstance(error, dict):
            return False
        if error.get("delivery_outcome") == "not_applied":
            return False
        # Before 0.1.70 this preflight guard lost its pending classification.
        # It runs before adoption/start, so only this exact legacy record is safe.
        legacy = (record.get("status") == "failed"
                and record.get("action") in CommandJournal.PROMPT_ACTIONS
                and error.get("type") == "RemoteControllerError")
        if not legacy:
            return False
        # These exact guards ran before start/adoption. They must not leave a
        # saved original message permanently in doubt after the workspace settles.
        if error.get("code") == "workspace_busy" and error.get("message") in {
            "This workspace is still running or syncing another turn. Your new conversation can be sent when it finishes.",
            "Finish or discard the current run before changing workspaces.",
            "Resolve the interrupted finalization before changing workspaces.",
        }:
            return True
        if record.get("action") not in {"runs.continue", "turns.continue"}:
            return False
        if error.get("message") == "Resolve the previous finalization before continuing":
            return True
        # 0.1.71 could stop an in-flight save before reaching start_run. This
        # migration also requires committed proof that the original save won.
        return (error.get("code") == "pcr_error"
                and error.get("message") == "Timed out while stopping remaining Agents; try again after the run stops"
                and request is not None and can_retry is not None and can_retry(request))

    def lookup(
        self, request: RemoteRequest,
        recover: Callable[[RemoteRequest], dict[str, Any] | None] | None = None,
        can_retry: Callable[[RemoteRequest], bool] | None = None,
    ) -> dict[str, Any]:
        # Atomic records can be read without waiting for a running mutation's
        # journal lock. This query never executes or changes the command.
        previous = self._read(self.directory / f"{self._key(request)}.json")
        if previous and previous.get("fingerprint") != self._fingerprint(request):
            raise CommandJournalError("command_conflict", "command_id was already used for a different command")
        result = previous.get("result")
        if previous.get("status") == "completed" and isinstance(result, dict):
            return {"status": "completed", "result": dict(result)}
        recovered = recover(request) if recover else None
        if recovered is not None:
            return {"status": "completed", "result": recovered}
        if self._retryable_record(previous, request, can_retry):
            receipt: dict[str, Any] = {"status": "retryable"}
            with self._thread_lock:
                indexed = self._key(request) in self._pending_keys
            if previous.get("status") == "retryable" and indexed and self._stored_request(previous) is not None:
                receipt.update(recovery_owned_by_daemon=True, delivery_state="waiting_previous_turn")
            return receipt
        if not previous:
            return {"status": "not_found"}
        error = previous.get("error") or {}
        if (previous.get("status") == "failed" and isinstance(error, dict)
                and error.get("delivery_outcome") == "not_applied"):
            return {"status": "rejected", "error": error}
        return {"status": "in_doubt"}

    @contextmanager
    def _locked(self, key: str, *, blocking: bool = True) -> Iterator[bool]:
        with self._thread_lock:
            lock = self._key_locks.setdefault(key, threading.RLock())
        # Unrelated commands must not wait behind another workspace's save.
        if not lock.acquire(blocking=blocking):
            yield False
            return
        try:
            handle = (self.directory / f"{key}.lock").open("a+b")
            try:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                    except BlockingIOError:
                        yield False
                        return
                yield True
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()
        finally:
            lock.release()

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise CommandJournalError(
                "journal_corrupt", f"Cannot read command journal entry {path.name}"
            ) from exc
        if not isinstance(value, dict):
            raise CommandJournalError(
                "journal_corrupt", f"Command journal entry {path.name} is invalid"
            )
        return value

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def execute(
        self,
        request: RemoteRequest,
        callback: Callable[[], dict[str, Any]],
        recover: Callable[[RemoteRequest], dict[str, Any] | None] | None = None,
        can_retry: Callable[[RemoteRequest], bool] | None = None,
        *,
        retry_unstarted: Callable[[], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._maybe_prune()
        key = self._key(request)
        path = self.directory / f"{key}.json"
        fingerprint = self._fingerprint(request)
        with self._locked(key):
            previous = self._read(path)
            if previous:
                if previous.get("fingerprint") != fingerprint:
                    raise CommandJournalError(
                        "command_conflict",
                        "command_id was already used for a different command",
                    )
                status = str(previous.get("status") or "")
                if status == "completed" and isinstance(previous.get("result"), dict):
                    return dict(previous["result"]), True
                recovered = recover(request) if recover else None
                if recovered is not None:
                    self._write(path, {**previous, "status": "completed", "result": recovered,
                                       "finished_at": utc_now()})
                    return recovered, True
                retryable = self._retryable_record(previous, request, can_retry)
                if status == "failed" and not retryable:
                    error = previous.get("error")
                    message = (
                        str(error.get("message") or "Command previously failed")
                        if isinstance(error, dict)
                        else "Command previously failed"
                    )
                    raise CommandJournalError("command_failed", message)
                if not retryable:
                    raise CommandJournalError(
                        "command_in_doubt",
                        "A previous daemon stopped while executing this command. "
                        "Inspect current PCR state before issuing a new command id.",
                    )
            if not previous and recover is not None:
                recovered = recover(request)
                if recovered is not None:
                    self._write(path, {**self.identity(request), "action": request.action,
                                       "status": "completed", "result": recovered, "finished_at": utc_now()})
                    return recovered, True
            history_recovery = self._history_recovery_required(previous)
            if history_recovery and retry_unstarted is None:
                raise CommandJournalError("command_in_doubt", "This continuation requires a guarded history recovery")
            base = {
                "client_id": request.client_id,
                "command_id": request.command_id,
                "action": request.action,
                "fingerprint": fingerprint,
                "started_at": utc_now(),
                "status": "started",
            }
            if request.action in self.PROMPT_ACTIONS and request.client_id != "pcrd-follow-up":
                base["request"] = request.to_mapping()
            if history_recovery:
                # Preserve the old failure and the guarded recovery requirement
                # across busy retries and crashes, without indexing abandoned fails.
                base["history_recovery"] = previous.get("history_recovery") or {
                    "error": previous.get("error"), "started_at": previous.get("started_at"),
                    "finished_at": previous.get("finished_at"),
                }
            self._write(path, base)
            try:
                result = retry_unstarted() if history_recovery else callback()
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", False))
                self._write(
                    path,
                    {
                        **base,
                        "status": "retryable" if retryable else "failed",
                        "finished_at": utc_now(),
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "code": getattr(exc, "code", None),
                            "delivery_outcome": getattr(exc, "details", {}).get("delivery_outcome"),
                        },
                    },
                )
                if retryable and "request" in base:
                    with self._thread_lock:
                        self._pending_keys.add(key)
                raise
            self._write(
                path,
                {
                    **base,
                    "status": "completed",
                    "finished_at": utc_now(),
                    "result": result,
                },
            )
            return result, False

    def pending_requests(self) -> list[RemoteRequest]:
        pending = []
        with self._thread_lock:
            keys = list(self._pending_keys)
        for key in keys:
            path = self.directory / f"{key}.json"
            try:
                record = self._read(path)
                if record.get("status") != "retryable" or not isinstance(record.get("request"), dict):
                    if record.get("status") in {"completed", "failed"} or not record:
                        with self._locked(key, blocking=False) as acquired:
                            if acquired:
                                latest = self._read(path)
                                if latest.get("status") in {"completed", "failed"} or not latest:
                                    with self._thread_lock:
                                        self._pending_keys.discard(key)
                    continue
                request = self._stored_request(record)
                if request is not None and path.stem == self._key(request):
                    pending.append(request)
            except (CommandJournalError, ProtocolError, ValueError, TypeError):
                continue
        return sorted(pending, key=lambda request: (request.sent_at, request.command_id))

    def _stored_request(self, record: dict[str, Any]) -> RemoteRequest | None:
        if not isinstance(record.get("request"), dict):
            return None
        try:
            request = RemoteRequest.from_mapping(record["request"])
        except ProtocolError:
            return None
        if (request.action in self.PROMPT_ACTIONS and request.client_id != "pcrd-follow-up"
                and all(record.get(key) == value for key, value in self.identity(request).items())):
            return request
        return None

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        with self._thread_lock:
            if now - self._last_prune_monotonic < self.prune_interval_seconds:
                return
            # Advance before scanning so concurrent commands do not all repeat
            # the same directory walk. A later interval retries any entries
            # that could not be inspected this time.
            self._last_prune_monotonic = now
        self.prune()

    def prune(self, *, now: float | None = None) -> int:
        """Remove replay records older than the relay's delivery lifetime.

        In-doubt commands are intentionally retained: deleting one could make a
        retried mutation run twice. Completed and failed records remain for 30
        days by default, far longer than the relay's one-day envelope TTL.
        """

        cutoff = float(time.time() if now is None else now) - self.retention_seconds
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                with self._locked(path.stem, blocking=False) as acquired:
                    if not acquired:
                        continue
                    if path.stat().st_mtime >= cutoff:
                        continue
                    value = self._read(path)
                    if value.get("status") == "retryable" and isinstance(value.get("request"), dict):
                        continue
                    if value.get("status") not in {
                        "completed",
                        "failed",
                        "retryable",
                    }:
                        continue
                    path.unlink()
                    removed += 1
            except (CommandJournalError, OSError):
                continue
        for temporary in self.directory.glob(".*.tmp"):
            try:
                if temporary.stat().st_mtime < cutoff:
                    temporary.unlink()
            except OSError:
                continue
        return removed
