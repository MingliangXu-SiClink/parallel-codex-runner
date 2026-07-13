from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is process-local
    fcntl = None


DEFAULT_RUN_TTL_SECONDS = 6 * 60 * 60
DEFAULT_ARTIFACT_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_STORAGE_QUOTA_BYTES = 20 * 1024**3


def configured_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def utc_deadline(seconds: int) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(1, seconds))
    return value.isoformat()


def deadline_timestamp(value: str) -> float | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class FileSignal:
    """A process-safe Event-like flag with an optional UTC deadline."""

    def __init__(self, path: Path, deadline: str = "") -> None:
        self.path = path
        self.deadline = deadline_timestamp(deadline)

    @property
    def deadline_elapsed(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    def is_set(self) -> bool:
        return self.path.exists() or self.deadline_elapsed

    def set(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(f"{os.getpid()}\n")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True


class RunOperationLock:
    """Crash-safe per-run operation lock backed by flock where available."""

    def __init__(self, locks_dir: Path, run_id: str, operation: str) -> None:
        self.path = locks_dir / f"{run_id}.lock"
        self.operation = operation
        self.handle: Any = None

    def __enter__(self) -> "RunOperationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.seek(0)
                owner = read_json_from_handle(handle)
                handle.close()
                active = str(owner.get("operation") or "another operation")
                raise RuntimeError(
                    f"This PCR run is already performing {active}"
                ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {"pid": os.getpid(), "operation": self.operation},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        handle.flush()
        self.handle = handle
        return self

    def __exit__(self, *_exc: Any) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def read_json_from_handle(handle: Any) -> Dict[str, Any]:
    try:
        payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def worker_request_path(state_dir: Path, operation_id: str) -> Path:
    return state_dir / "workers" / "requests" / f"{operation_id}.json"


def worker_status_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / "workers" / f"{run_id}.json"


def spawn_worker(
    state_dir: Path,
    run_id: str,
    operation: str,
    request: Dict[str, Any],
) -> tuple[subprocess.Popen[bytes], str]:
    operation_id = f"{run_id}-{uuid.uuid4().hex[:10]}"
    request_path = worker_request_path(state_dir, operation_id)
    payload = dict(request)
    payload.update(
        {
            "run_id": run_id,
            "operation": operation,
            "operation_id": operation_id,
        }
    )
    write_json_atomic(request_path, payload)
    command = [
        str(Path(sys.executable).resolve()),
        "-m",
        "parallel_codex_runner_core.plugin.worker",
        "--state-dir",
        str(state_dir),
        "--request",
        str(request_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return process, operation_id


def terminate_process_group(pid: int, timeout: float = 5.0) -> bool:
    if not pid_is_alive(pid):
        return True
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return not pid_is_alive(pid)
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_is_alive(pid)


@contextmanager
def installed_signal_handlers(cancel: FileSignal) -> Iterator[None]:
    previous: Dict[int, Any] = {}

    def request_cancel(_signum: int, _frame: Any) -> None:
        cancel.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_cancel)
        except (OSError, ValueError):
            previous.pop(signum, None)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass


def normalized_indices(values: Sequence[Any]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index > 0 and index not in result:
            result.append(index)
    return result
