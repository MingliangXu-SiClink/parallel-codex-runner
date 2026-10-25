from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from .journal import CommandJournal
from .protocol import RemoteRequest


class PromptDeliveryCoordinator:
    """Advance saved, definitely-unaccepted prompts using their original identity."""

    def __init__(self, journal: CommandJournal, execute: Callable[[RemoteRequest], Any],
                 *, poll_seconds: float = 1.0, retry_seconds: float = 2.0) -> None:
        self.journal = journal
        self.execute = execute
        self.poll_seconds = max(0.01, poll_seconds)
        self.retry_seconds = max(0.01, retry_seconds)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pcrd-prompt-delivery")
        self._active: dict[str, Future[Any]] = {}
        self._retry_at: dict[str, float] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="pcrd-prompt-handoff", daemon=True)
            self._thread.start()

    def status(self) -> dict[str, Any]:
        pending_count = len(self.journal.pending_requests())
        with self._lock:
            return {"running": self._thread is not None and not self._stop.is_set(),
                    "pending_count": pending_count, "active_count": len(self._active)}

    def owns_inflight(self, request: RemoteRequest) -> bool:
        with self._lock:
            return self.journal._key(request) in self._active

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._pool.shutdown(wait=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                requests = self.journal.pending_requests()
                now = time.monotonic()
                with self._lock:
                    for key, future in list(self._active.items()):
                        if future.done():
                            del self._active[key]
                            self._retry_at[key] = now + self.retry_seconds
                    keys = {self.journal._key(request) for request in requests}
                    self._retry_at = {key: value for key, value in self._retry_at.items() if key in keys}
                    for request in requests:
                        key = self.journal._key(request)
                        if (len(self._active) >= 2 or key in self._active
                                or self._retry_at.get(key, 0) > now):
                            continue
                        self._active[key] = self._pool.submit(self.execute, request)
            except Exception:
                logging.getLogger(__name__).exception("Cannot advance pending prompt delivery")
            self._stop.wait(self.poll_seconds)
