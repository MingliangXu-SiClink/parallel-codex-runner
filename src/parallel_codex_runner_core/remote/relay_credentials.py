from __future__ import annotations

import logging
import threading
import time

from .relay_http import RelayHttpClient, RelayHttpError
from .relay_state import RelayStateError


LOGGER = logging.getLogger(__name__)


class RelayCredentialMaintainer:
    """Renew host and controller sessions independently of client windows."""

    def __init__(self, *clients: RelayHttpClient, interval: float = 60.0) -> None:
        self.clients = clients
        self.interval = max(0.1, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._retries: dict[int, tuple[str, float]] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pcrd-relay-credentials", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def poll_once(self) -> None:
        for index, client in enumerate(self.clients):
            if self._stop.is_set():
                return
            updated = ""
            try:
                state = client.store.load()
                if state is None or not state.paired:
                    continue
                updated = state.updated_at
                previous, retry_at = self._retries.get(index, ("", 0.0))
                if previous == updated and time.monotonic() < retry_at:
                    continue
                client.ensure_access_token()
                self._retries.pop(index, None)
            except (RelayHttpError, RelayStateError) as exc:
                delay = 900.0 if isinstance(exc, RelayHttpError) and exc.refresh_token_rejected else self.interval
                self._retries[index] = (updated, time.monotonic() + delay)
                LOGGER.warning("Relay credential renewal will retry: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)
