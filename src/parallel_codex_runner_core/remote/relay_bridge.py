from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from websockets.asyncio.client import connect

from .crypto import RemoteCryptoError
from .protocol import MAX_MESSAGE_BYTES, error_response
from .relay_http import RelayHttpClient, RelayHttpError
from .relay_state import RelayStateError, relay_websocket_url


LOGGER = logging.getLogger(__name__)


class RelayLinkStatus:
    """Tracks relay connection state and persists it to disk."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "relay-link.json"
        self._lock = threading.Lock()
        self._state = "unknown"
        self._since: str | None = None
        self._last_error: str | None = None
        self._last_error_at: str | None = None

    def update(
        self,
        state: str,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._state = state
            self._since = datetime.now(timezone.utc).isoformat()
            if error:
                self._last_error = error
                self._last_error_at = self._since
            self._persist_locked()

    def _persist_locked(self) -> None:
        temporary = self._path.with_name(
            f".{self._path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            data = {
                "state": self._state,
                "since": self._since,
            }
            if self._last_error:
                data["last_error"] = self._last_error
                data["last_error_at"] = self._last_error_at
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        except OSError as exc:
            LOGGER.warning("Failed to write relay link status: %s", exc)
            temporary.unlink(missing_ok=True)

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return {"state": "unknown"}
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"state": "unknown"}


class TrustedRequestExecutor(Protocol):
    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class RelayBridge:
    """Long-lived encrypted bridge between the relay and one pcrd process."""

    MAX_INFLIGHT_REQUESTS = 16
    MAX_DEFERRED_REQUESTS = 128
    DEVICE_KEY_REFRESH_INTERVAL_SECONDS = 60.0

    REMOTE_ACTIONS = frozenset(
        {
            "system.health",
            "system.capabilities",
            "filesystem.roots",
            "filesystem.list",
            "filesystem.inspect",
            "filesystem.mkdir",
            "workspaces.list",
            "workspaces.add",
            "workspaces.select",
            "workspaces.remove",
            "workspaces.settings.get",
            "workspaces.settings.update",
            "workspace.file.read",
            "workspace.item.resolve",
            "settings.defaults.get",
            "settings.defaults.update",
            "runs.estimate",
            "runs.start",
            "runs.list",
            "runs.get",
            "conversations.list",
            "conversations.get",
            "conversations.rename",
            "turns.start",
            "turns.get",
            "turns.continue",
            "turns.finalize",
            "runs.history",
            "runs.events",
            "runs.agent",
            "runs.question",
            "runs.answer_question",
            "runs.operation",
            "runs.diff",
            "storage.summary",
            "runs.stop",
            "runs.kill_agent",
            "runs.retry_agent",
            "runs.reject_agent",
            "runs.add_agents",
            "runs.update_synthesis",
            "runs.accept_agent",
        "runs.recover_finalization",
        "runs.verify_finalization",
            "runs.discard",
            "storage.cleanup",
            "runs.continue",
            "codex.resume_sessions",
            "codex.resume_history",
            "codex.model_options",
            "codex.account_usage",
            "prompt_history.list",
            "prompt_history.append",
            "followups.get",
            "followups.list",
            "followups.replace",
            "followups.enqueue",
            "followups.select_agent",
            "followups.clear",
            "relay.status",
            "relay.pairing_create",
            "relay.poll",
            "feedback.submit",
            "feedback.list",
            "feedback.get",
            "feedback.add_note",
            "feedback.add_attachment",
            "feedback.attachment",
            "feedback.remove_attachment",
            "feedback.withdraw",
        }
    )

    def __init__(self, state_dir: Path, executor: TrustedRequestExecutor) -> None:
        self.http = RelayHttpClient(state_dir)
        self.executor = executor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device_keys: dict[str, str] = {}
        self._pairing_poll_lock = threading.Lock()
        self.link_status = RelayLinkStatus(state_dir)

    def start(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="pcrd-relay",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            LOGGER.exception("PCR relay bridge stopped unexpectedly")

    async def _run(self) -> None:
        delay = 1.0
        force_refresh = False
        while not self._stop.is_set():
            try:
                configured = self.http.store.load()
            except RelayStateError as exc:
                LOGGER.error("PCR relay state is invalid: %s", exc)
                if self._stop.wait(5.0):
                    break
                continue
            if configured is None or not configured.paired:
                self.link_status.update("not_configured")
                if self._stop.wait(2.0):
                    break
                continue
            try:
                state = await asyncio.to_thread(
                    self.http.ensure_access_token,
                    force_refresh=force_refresh,
                )
                await self._refresh_device_keys()
                headers = {"Authorization": f"Bearer {state.access_token}"}
                async with connect(
                    relay_websocket_url(state.base_url),
                    additional_headers=headers,
                    # A user-level macOS SOCKS proxy may be configured even
                    # when its optional Python transport isn't installed.
                    # The relay is an explicitly configured endpoint, so keep
                    # this daemon connection direct and deterministic.
                    proxy=None,
                    max_size=MAX_MESSAGE_BYTES * 2,
                    open_timeout=15,
                    ping_interval=20,
                    ping_timeout=20,
                ) as websocket:
                    delay = 1.0
                    force_refresh = False
                    LOGGER.info("PCR relay bridge connected to %s", state.base_url)
                    self.link_status.update("connected")
                    await self._connected_loop(websocket)
            except RelayHttpError as exc:
                if exc.refresh_token_rejected:
                    LOGGER.warning(
                        "PCR relay credentials are no longer accepted; waiting for re-pairing"
                    )
                    self.link_status.update(
                        "credentials_rejected",
                        error="Credentials no longer accepted; re-pairing required",
                    )
                    self._wait_for_relay_state_change(
                        configured.updated_at,
                        timeout=15 * 60,
                    )
                    delay = 1.0
                    force_refresh = False
                    continue
                LOGGER.warning("PCR relay bridge is unavailable: %s", exc)
                self.link_status.update("connecting", error=str(exc))
                force_refresh = exc.status_code == 401
            except RelayStateError as exc:
                LOGGER.warning("PCR relay bridge is unavailable: %s", exc)
                self.link_status.update("connecting", error=str(exc))
                force_refresh = False
            except Exception as exc:
                LOGGER.warning("PCR relay connection closed: %s", exc)
                self.link_status.update("connecting", error=str(exc))
                force_refresh = True
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 30.0)

    def _wait_for_relay_state_change(
        self,
        previous_updated_at: str,
        *,
        timeout: float,
        poll_interval: float = 2.0,
    ) -> None:
        deadline = time.monotonic() + max(1.0, timeout)
        poll_interval = max(0.01, poll_interval)
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._stop.wait(min(poll_interval, remaining)):
                return
            try:
                current = self.http.store.load()
            except RelayStateError:
                return
            if current is None or current.updated_at != previous_updated_at:
                return

    async def _refresh_device_keys(self) -> None:
        state = self.http.store.load()
        trusted = dict(state.trusted_device_keys) if state is not None else {}
        devices = await asyncio.to_thread(self.http.list_devices)
        self._device_keys = {}
        for item in devices:
            device_id = str(item.get("device_id") or "")
            public_key = str(item.get("public_key") or "")
            if (
                item.get("status") == "active"
                and trusted.get(device_id) == public_key
            ):
                self._device_keys[device_id] = public_key
        self.http.store.remove_unavailable_trusted_devices(
            self._device_keys,
            expected=trusted,
        )

    async def _periodically_refresh_device_keys(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.DEVICE_KEY_REFRESH_INTERVAL_SECONDS)
            if self._stop.is_set():
                return
            try:
                await self._refresh_device_keys()
            except (RelayHttpError, RelayStateError) as exc:
                # The WebSocket may remain healthy while the device-list HTTP
                # request is briefly unavailable. Keep serving the current
                # peers and retry on the next interval.
                LOGGER.warning("Cannot refresh paired Relay devices: %s", exc)

    async def _connected_loop(self, websocket: Any) -> None:
        # Finalize and cleanup can take tens of seconds. Keep receiving refresh
        # and stop requests while those operations run, but cap concurrency so
        # a client cannot create an unbounded number of executor threads.
        tasks: set[asyncio.Task[None]] = set()
        deferred_envelopes: deque[dict[str, Any]] = deque()
        send_lock = asyncio.Lock()
        pending_submissions: dict[str, asyncio.Future[None]] = {}
        device_refresh_task = asyncio.create_task(
            self._periodically_refresh_device_keys()
        )

        def reap_finished() -> None:
            for task in tuple(tasks):
                if not task.done():
                    continue
                tasks.remove(task)
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.exception("PCR relay request handler failed")

        def launch_available() -> None:
            while (
                deferred_envelopes
                and len(tasks) < self.MAX_INFLIGHT_REQUESTS
            ):
                tasks.add(
                    asyncio.create_task(
                        self._handle_envelope(
                            websocket,
                            deferred_envelopes.popleft(),
                            send_lock=send_lock,
                            pending_submissions=pending_submissions,
                        )
                    )
                )

        try:
            while not self._stop.is_set():
                reap_finished()
                launch_available()

                state = self.http.store.load()
                if state is None or not state.paired:
                    await websocket.close(
                        code=1000,
                        reason="relay configuration removed",
                    )
                    return
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                message_type = str(message.get("type") or "")
                if message_type in {"accepted", "rejected"}:
                    message_id = str(message.get("message_id") or "")
                    waiter = pending_submissions.pop(message_id, None)
                    if waiter is None or waiter.done():
                        continue
                    if message_type == "accepted":
                        waiter.set_result(None)
                    else:
                        waiter.set_exception(
                            RelayStateError(
                                str(message.get("message") or "Relay rejected response")
                            )
                        )
                    continue
                if message_type != "envelope":
                    continue
                if len(tasks) < self.MAX_INFLIGHT_REQUESTS:
                    tasks.add(
                        asyncio.create_task(
                            self._handle_envelope(
                                websocket,
                                message,
                                send_lock=send_lock,
                                pending_submissions=pending_submissions,
                            )
                        )
                    )
                    continue
                if len(deferred_envelopes) < self.MAX_DEFERRED_REQUESTS:
                    deferred_envelopes.append(dict(message))
                    continue
                LOGGER.warning(
                    "PCR relay request queue is full; reconnecting to preserve backpressure"
                )
                await websocket.close(code=1013, reason="PCR request queue is full")
                return
        finally:
            device_refresh_task.cancel()
            await asyncio.gather(device_refresh_task, return_exceptions=True)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for waiter in pending_submissions.values():
                if not waiter.done():
                    waiter.cancel()
            pending_submissions.clear()

    async def _peer_key(self, device_id: str) -> str:
        value = self._device_keys.get(device_id, "")
        if value:
            return value
        await self._refresh_device_keys()
        value = self._device_keys.get(device_id, "")
        if value:
            return value

        # Android starts loading the machine immediately after approving the QR
        # code. That first request can reach the long-lived bridge just before
        # the desktop client has polled and persisted the approved pairing. Let
        # pcrd complete its own pending pairing instead of acknowledging and
        # permanently discarding the phone's bootstrap request.
        try:
            pairing_completed = await asyncio.to_thread(
                self._complete_pending_pairing
            )
        except (RelayHttpError, RelayStateError) as exc:
            LOGGER.warning("Cannot complete pending Relay pairing: %s", exc)
            pairing_completed = False
        if pairing_completed:
            await self._refresh_device_keys()
            value = self._device_keys.get(device_id, "")
        if not value:
            raise RemoteCryptoError("Sender device is not active or paired")
        return value

    def _complete_pending_pairing(self) -> bool:
        with self._pairing_poll_lock:
            state = self.http.store.load()
            if state is None or not state.pairing_id or not state.poll_token:
                return False
            result = self.http.poll_pairing(
                expected_pairing_id=state.pairing_id
            )
            return str(result.get("pairing_status") or "").casefold() == "approved"

    async def _handle_envelope(
        self,
        websocket: Any,
        message: Mapping[str, Any],
        *,
        send_lock: asyncio.Lock | None = None,
        pending_submissions: dict[str, asyncio.Future[None]] | None = None,
    ) -> None:
        message_id = str(message.get("message_id") or "")
        sender_id = str(message.get("sender_device_id") or "")
        target_id = str(message.get("target_device_id") or "")
        ciphertext = str(message.get("ciphertext") or "")
        try:
            sequence = int(message.get("sequence"))
        except (TypeError, ValueError, OverflowError):
            await self._acknowledge(websocket, message_id, send_lock=send_lock)
            return
        state = self.http.store.load()
        if state is None or target_id != state.mac_device_id:
            await self._acknowledge(websocket, message_id, send_lock=send_lock)
            return
        try:
            peer_key = await self._peer_key(sender_id)
            plaintext = self.http.keys.decrypt(
                peer_key,
                ciphertext,
                message_id=message_id,
                sender_device_id=sender_id,
                target_device_id=target_id,
                sequence=sequence,
            )
        except RemoteCryptoError as exc:
            LOGGER.warning("Discarding invalid encrypted relay envelope: %s", exc)
            await self._acknowledge(websocket, message_id, send_lock=send_lock)
            return
        if plaintext.get("type") != "request" or not isinstance(
            plaintext.get("request"), Mapping
        ):
            await self._acknowledge(websocket, message_id, send_lock=send_lock)
            return
        request = dict(plaintext["request"])
        request["client_id"] = sender_id
        action = str(request.get("action") or "")
        if action not in self.REMOTE_ACTIONS:
            request_id = str(request.get("request_id") or "unknown")
            response = error_response(
                request_id,
                "remote_action_forbidden",
                "This action is available only to local machine clients",
                command_id=str(request.get("command_id") or request_id),
            )
        else:
            response = await asyncio.to_thread(self.executor.execute, request)
            if action == "system.capabilities":
                response = self._remote_capabilities(response)
        await self._send_response(
            websocket,
            target_device_id=sender_id,
            peer_public_key=peer_key,
            response=response,
            send_lock=send_lock,
            pending_submissions=pending_submissions,
        )
        await self._acknowledge(websocket, message_id, send_lock=send_lock)

    @classmethod
    def _remote_capabilities(
        cls,
        response: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(response)
        body = response.get("body")
        if not bool(response.get("ok")) or not isinstance(body, Mapping):
            return normalized
        remote_body = dict(body)
        remote_body["transport"] = "relay"
        advertised = body.get("actions")
        if isinstance(advertised, list):
            available = {
                action for action in advertised if isinstance(action, str)
            }
            # Reaching this method proves the capability action itself works,
            # even when an older daemon omitted it from its advertised list.
            available.add("system.capabilities")
            remote_body["actions"] = sorted(cls.REMOTE_ACTIONS & available)
        else:
            remote_body["actions"] = sorted(cls.REMOTE_ACTIONS)
        mutating = body.get("mutating_actions")
        if isinstance(mutating, list):
            exposed_actions = set(remote_body["actions"])
            remote_body["mutating_actions"] = sorted(
                action
                for action in mutating
                if isinstance(action, str) and action in exposed_actions
            )
        normalized["body"] = remote_body
        return normalized

    async def _send_response(
        self,
        websocket: Any,
        *,
        target_device_id: str,
        peer_public_key: str,
        response: Mapping[str, Any],
        send_lock: asyncio.Lock | None = None,
        pending_submissions: dict[str, asyncio.Future[None]] | None = None,
    ) -> None:
        state = self.http.store.load()
        if state is None:
            raise RelayStateError("Relay state disappeared while sending a response")
        sequence = self.http.store.next_sequence()
        message_id = f"message-{uuid.uuid4().hex}"
        ciphertext = self.http.keys.encrypt(
            peer_public_key,
            {"type": "response", "response": dict(response)},
            message_id=message_id,
            sender_device_id=state.mac_device_id,
            target_device_id=target_device_id,
            sequence=sequence,
        )
        payload = json.dumps(
            {
                "message_id": message_id,
                "target_device_id": target_device_id,
                "sequence": sequence,
                "ciphertext": ciphertext,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        acceptance: asyncio.Future[None] | None = None
        if pending_submissions is not None:
            acceptance = asyncio.get_running_loop().create_future()
            pending_submissions[message_id] = acceptance
        try:
            if send_lock is None:
                await websocket.send(payload)
            else:
                async with send_lock:
                    await websocket.send(payload)
            if acceptance is not None:
                await asyncio.wait_for(acceptance, timeout=15.0)
        finally:
            if (
                acceptance is not None
                and pending_submissions is not None
                and pending_submissions.get(message_id) is acceptance
            ):
                pending_submissions.pop(message_id, None)

    @staticmethod
    async def _acknowledge(
        websocket: Any,
        message_id: str,
        *,
        send_lock: asyncio.Lock | None = None,
    ) -> None:
        if not message_id:
            return
        payload = json.dumps(
            {"type": "ack", "message_id": message_id},
            separators=(",", ":"),
        )
        if send_lock is None:
            await websocket.send(payload)
        else:
            async with send_lock:
                await websocket.send(payload)
