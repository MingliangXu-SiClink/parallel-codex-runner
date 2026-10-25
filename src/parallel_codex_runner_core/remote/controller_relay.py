from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .crypto import DeviceKeyPair, RemoteCryptoError, pairing_proof
from .protocol import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, utc_now
from .relay_http import RelayHttpClient, RelayHttpError
from .relay_state import (
    RelayState,
    RelayStateError,
    normalize_relay_url,
    relay_websocket_url,
    utc_now as relay_now,
)


class ControllerRelayError(RuntimeError):
    pass


class ControllerRelayHttpError(ControllerRelayError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PairingLink:
    def __init__(self, value: str) -> None:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme.lower() != "pcr" or parsed.netloc.lower() != "pair":
            raise ControllerRelayError("This computer connection code is invalid")
        values = {
            name: entries[-1]
            for name, entries in urllib.parse.parse_qs(
                parsed.query,
                keep_blank_values=True,
            ).items()
            if entries
        }
        try:
            self.base_url = normalize_relay_url(str(values.get("relay") or ""))
        except RelayStateError as exc:
            raise ControllerRelayError(
                "This computer connection code is invalid"
            ) from exc
        self.pairing_id = str(values.get("pairing") or "")
        self.secret = str(values.get("secret") or "")
        self.approval_token = str(values.get("auth") or "")
        self.machine_id = str(values.get("mac") or "")
        self.machine_public_key = str(values.get("mac_key") or "")
        try:
            secret = base64.b64decode(self.secret, validate=True)
            machine_key = base64.b64decode(
                self.machine_public_key,
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            raise ControllerRelayError(
                "This computer connection code is invalid"
            ) from exc
        if (
            not self.base_url
            or not self.pairing_id
            or not self.approval_token
            or not self.machine_id
            or len(secret) != 32
            or len(machine_key) != 32
        ):
            raise ControllerRelayError("This computer connection code is invalid")


class ControllerRelayProxy:
    """pcrd-owned relay controller used by local native clients."""

    def __init__(
        self,
        state_dir: Path,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        websocket_connect: Callable[..., Any] = connect,
    ) -> None:
        self.state_dir = state_dir.expanduser().resolve() / "controller-relay"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass
        self.http = RelayHttpClient(
            self.state_dir,
            timeout=timeout,
            transport=transport,
        )
        self.keys = self.http.keys
        self.timeout = max(1.0, timeout)
        self.websocket_connect = websocket_connect
        self._connection_lock = threading.RLock()
        self._websocket: Any | None = None
        self._websocket_identity: tuple[str, str, str] | None = None

    def close(self) -> None:
        with self._connection_lock:
            self._close_websocket()

    def _close_websocket(self) -> None:
        websocket = self._websocket
        self._websocket = None
        self._websocket_identity = None
        if websocket is None:
            return
        try:
            websocket.close()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        state = self.http.store.load()
        return {
            "paired": bool(state and state.paired),
            "selected_machine_id": state.selected_device_id if state else "",
        }

    def identity(self) -> dict[str, Any]:
        state = self.http.store.load()
        return {
            "device_id": state.mac_device_id if state and state.paired else None,
            "public_key": self.keys.public_key,
        }

    def pair(self, raw_code: str, *, device_name: str) -> dict[str, Any]:
        with self._connection_lock:
            return self._pair(raw_code, device_name=device_name)

    def _pair(self, raw_code: str, *, device_name: str) -> dict[str, Any]:
        link = PairingLink(raw_code)
        previous = self.http.store.load()
        existing_access_token = ""
        if previous is not None and previous.paired and previous.base_url == link.base_url:
            try:
                existing_access_token = self.http.ensure_access_token().access_token
            except (RelayHttpError, RelayStateError):
                existing_access_token = ""
        headers = (
            {"Authorization": f"Bearer {existing_access_token}"}
            if existing_access_token
            else None
        )
        issued = self.http._request(
            "POST",
            f"{link.base_url}/v1/auth/pairing",
            headers=headers,
            json_body={
                "pairing_id": link.pairing_id,
                "approval_token": link.approval_token,
                "device_name": device_name.strip() or "Parallel Codex on Mac",
                "device_kind": "macos",
                "public_key": self.keys.public_key,
            },
        )
        device_id = str(issued.get("device_id") or "")
        access_token = str(issued.get("access_token") or "")
        refresh_token = str(issued.get("refresh_token") or "")
        user_id = str(issued.get("user_id") or "")
        try:
            expires_in = int(issued.get("expires_in") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ControllerRelayError("The server returned invalid connection details") from exc
        if not device_id or not access_token or not refresh_token or not user_id or expires_in < 1:
            raise ControllerRelayError("The server returned incomplete connection details")
        proof = pairing_proof(
            link.secret,
            pairing_id=link.pairing_id,
            mac_device_id=link.machine_id,
            mac_public_key=link.machine_public_key,
            android_device_id=device_id,
            android_public_key=self.keys.public_key,
        )
        self.http._request(
            "POST",
            f"{link.base_url}/v1/pairings/{link.pairing_id}/approve",
            headers={"Authorization": f"Bearer {access_token}"},
            json_body={
                "pairing_proof": proof,
                "approval_token": link.approval_token,
            },
        )
        same_account = (
            previous is not None
            and previous.base_url == link.base_url
            and previous.user_id == user_id
            and previous.mac_device_id == device_id
        )
        trusted = dict(previous.trusted_device_keys) if same_account else {}
        trusted[link.machine_id] = link.machine_public_key
        state = RelayState(
            base_url=link.base_url,
            mac_device_id=device_id,
            device_name=device_name.strip() or "Parallel Codex on Mac",
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=(
                relay_now() + dt.timedelta(seconds=expires_in)
            ).isoformat(),
            user_id=user_id,
            trusted_device_keys=trusted,
            selected_device_id=link.machine_id,
            sequence=previous.sequence if same_account else 0,
        )
        self.http.store.save(state)
        if (
            previous is not None
            and previous.paired
            and previous.base_url == link.base_url
            and previous.user_id == user_id
            and previous.mac_device_id != device_id
        ):
            try:
                self.http._request(
                    "DELETE",
                    f"{link.base_url}/v1/devices/{previous.mac_device_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except (RelayHttpError, RelayStateError):
                # The new controller identity is already durable. Failure to
                # revoke an obsolete identity must not undo a successful pair.
                pass
        self._close_websocket()
        return self.status()

    def machines(self) -> dict[str, Any]:
        state = self.http.ensure_access_token()
        expected = dict(state.trusted_device_keys)
        available: dict[str, str] = {}
        devices: list[dict[str, Any]] = []
        for raw in self.http.list_devices():
            device_id = str(raw.get("device_id") or "")
            public_key = str(raw.get("public_key") or "")
            if (
                raw.get("kind") != "mac"
                or raw.get("status") != "active"
                or expected.get(device_id) != public_key
            ):
                continue
            available[device_id] = public_key
            devices.append(dict(raw))
        self.http.store.remove_unavailable_trusted_devices(
            available,
            expected=expected,
        )
        devices.sort(
            key=lambda item: (
                item.get("online") is False,
                str(item.get("name") or "").casefold(),
            )
        )
        return {"devices": devices}

    def select_machine(self, machine_id: str) -> dict[str, Any]:
        self.http.store.select_device(machine_id)
        return self.status()

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
        client_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._connection_lock:
            last_error: Exception | None = None
            for force_refresh in ((False,) if action == "runs.answer_question" else (False, True)):
                try:
                    return self._call_once(
                        machine_id=machine_id,
                        machine_public_key=machine_public_key,
                        action=action,
                        body=body,
                        expected_revision=expected_revision,
                        command_id=command_id,
                        timeout_seconds=timeout_seconds,
                        force_refresh=force_refresh,
                        **({"client_capabilities": client_capabilities} if client_capabilities else {}),
                    )
                except (
                    OSError,
                    RelayHttpError,
                    RelayStateError,
                    TimeoutError,
                    WebSocketException,
                ) as exc:
                    last_error = exc
                    self._close_websocket()
        raise ControllerRelayError(
            str(last_error or "The server connection failed")
        ) from last_error

    def _call_once(
        self,
        *,
        machine_id: str,
        machine_public_key: str,
        action: str,
        body: Mapping[str, Any],
        expected_revision: int | None,
        command_id: str,
        timeout_seconds: float,
        force_refresh: bool,
        client_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        state = self.http.ensure_access_token(force_refresh=force_refresh)
        pinned_key = state.trusted_device_keys.get(machine_id)
        if not pinned_key or pinned_key != machine_public_key:
            raise ControllerRelayError(
                "This computer has not been connected to the desktop server"
            )
        request_id = uuid.uuid4().hex
        request: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "command_id": command_id or request_id,
            "client_id": state.mac_device_id,
            "sent_at": utc_now(),
            "action": action,
            "body": dict(body),
        }
        if expected_revision is not None:
            request["expected_revision"] = expected_revision
        if client_capabilities:
            request["client_capabilities"] = list(client_capabilities)
        message_id = f"message-{uuid.uuid4().hex}"
        sequence = self.http.store.next_sequence()
        ciphertext = self.keys.encrypt(
            machine_public_key,
            {"type": "request", "request": request},
            message_id=message_id,
            sender_device_id=state.mac_device_id,
            target_device_id=machine_id,
            sequence=sequence,
        )
        wire = {
            "message_id": message_id,
            "target_device_id": machine_id,
            "sequence": sequence,
            "ciphertext": ciphertext,
        }
        timeout_seconds = min(max(1.0, timeout_seconds), 15 * 60.0)
        deadline = time.monotonic() + timeout_seconds
        websocket = self._connected_websocket(
            state,
            timeout_seconds=timeout_seconds,
        )
        websocket.send(json.dumps(wire, ensure_ascii=False, separators=(",", ":")))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The connected computer did not respond in time")
            raw = websocket.recv(timeout=remaining)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                message = json.loads(raw)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(message, Mapping):
                continue
            message_type = str(message.get("type") or "")
            if message_type == "rejected" and message.get("message_id") == message_id:
                raise ControllerRelayError(
                    str(message.get("message") or "The server rejected this request")
                )
            if message_type != "envelope":
                continue
            delivery_id = str(message.get("message_id") or "")
            try:
                response = self._decode_delivery(
                    message,
                    state=state,
                    machine_id=machine_id,
                    machine_public_key=machine_public_key,
                    request_id=request_id,
                )
            finally:
                if delivery_id:
                    websocket.send(
                        json.dumps(
                            {"type": "ack", "message_id": delivery_id},
                            separators=(",", ":"),
                        )
                    )
            if response is not None:
                return response

    def _connected_websocket(
        self,
        state: RelayState,
        *,
        timeout_seconds: float,
    ) -> Any:
        identity = (state.base_url, state.mac_device_id, state.access_token)
        if self._websocket is not None and self._websocket_identity == identity:
            return self._websocket
        self._close_websocket()
        websocket = self.websocket_connect(
            relay_websocket_url(state.base_url),
            additional_headers={"Authorization": f"Bearer {state.access_token}"},
            proxy=None,
            open_timeout=min(15.0, timeout_seconds),
            ping_interval=20,
            ping_timeout=20,
            max_size=MAX_MESSAGE_BYTES * 2,
        )
        self._websocket = websocket
        self._websocket_identity = identity
        return websocket

    def _decode_delivery(
        self,
        message: Mapping[str, Any],
        *,
        state: RelayState,
        machine_id: str,
        machine_public_key: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        sender = str(message.get("sender_device_id") or "")
        target = str(message.get("target_device_id") or "")
        delivery_id = str(message.get("message_id") or "")
        ciphertext = str(message.get("ciphertext") or "")
        try:
            sequence = int(message.get("sequence"))
        except (TypeError, ValueError, OverflowError):
            return None
        if sender != machine_id or target != state.mac_device_id:
            return None
        try:
            plain = self.keys.decrypt(
                machine_public_key,
                ciphertext,
                message_id=delivery_id,
                sender_device_id=sender,
                target_device_id=target,
                sequence=sequence,
            )
        except RemoteCryptoError:
            return None
        if plain.get("type") != "response":
            return None
        response = plain.get("response")
        if not isinstance(response, Mapping) or response.get("request_id") != request_id:
            return None
        return dict(response)

    def http_request(
        self,
        *,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        method = method.upper()
        parsed = urllib.parse.urlsplit(path)
        allowed_path = parsed.path.startswith("/v1/feedback") or parsed.path in {
            "/v1/app-updates/macos/check",
            "/v1/app-updates/macos/tickets",
        }
        if (
            method not in {"GET", "POST"}
            or parsed.scheme
            or parsed.netloc
            or not allowed_path
            or ".." in parsed.path.split("/")
        ):
            raise ControllerRelayError("This server request is not allowed")
        state = self.http.ensure_access_token()
        try:
            return self.http._request(
                method,
                f"{state.base_url}{path}",
                headers={"Authorization": f"Bearer {state.access_token}"},
                json_body=body if method == "POST" else None,
            )
        except RelayHttpError as exc:
            raise ControllerRelayHttpError(
                str(exc),
                status_code=exc.status_code,
            ) from exc

    def download_macos_update(
        self,
        *,
        ticket: str,
        release_id: str,
        expected_sha256: str,
        expected_size: int,
    ) -> dict[str, Any]:
        ticket = ticket.strip()
        release_id = release_id.strip()
        expected_sha256 = expected_sha256.strip().lower()
        if not 16 <= len(ticket) <= 512:
            raise ControllerRelayError("The update authorization is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", release_id):
            raise ControllerRelayError("The update release identifier is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ControllerRelayError("The update checksum is invalid")
        if expected_size <= 0 or expected_size > 512 * 1024 * 1024:
            raise ControllerRelayError("The update size is invalid")

        downloads = self.state_dir / "downloads"
        if downloads.exists() and downloads.is_symlink():
            raise ControllerRelayError("The private update cache is invalid")
        downloads.mkdir(parents=True, exist_ok=True)
        try:
            downloads.chmod(0o700)
        except OSError:
            pass
        target = downloads / f"{release_id}.zip"
        if target.is_symlink():
            raise ControllerRelayError("The cached update is invalid")
        if self._download_matches(
            target,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        ):
            return {
                "path": str(target),
                "sha256": expected_sha256,
                "size_bytes": expected_size,
            }

        temporary = downloads / f".{release_id}.{uuid.uuid4().hex}.tmp"
        state = self.http.ensure_access_token()
        digest = hashlib.sha256()
        copied = 0
        try:
            with httpx.Client(
                timeout=httpx.Timeout(5 * 60, connect=self.timeout),
                transport=self.http.transport,
            ) as client:
                with client.stream(
                    "POST",
                    f"{state.base_url}/v1/app-updates/macos/download",
                    headers={
                        "Authorization": f"Bearer {state.access_token}",
                        "X-PCR-Update-Ticket": ticket,
                    },
                ) as response:
                    if response.is_error:
                        detail = ""
                        try:
                            response.read()
                            payload = response.json()
                            if isinstance(payload, Mapping):
                                detail = str(payload.get("detail") or "")
                        except (ValueError, httpx.HTTPError):
                            pass
                        raise ControllerRelayHttpError(
                            detail
                            or f"PCR relay request failed with HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    server_sha256 = str(
                        response.headers.get("X-PCR-Update-SHA256") or ""
                    ).strip().lower()
                    if server_sha256 and server_sha256 != expected_sha256:
                        raise ControllerRelayError(
                            "The update server returned an unexpected checksum"
                        )
                    with temporary.open("xb") as output:
                        try:
                            os.chmod(temporary, 0o600)
                        except OSError:
                            pass
                        for chunk in response.iter_bytes(1024 * 1024):
                            if not chunk:
                                continue
                            copied += len(chunk)
                            if copied > expected_size:
                                raise ControllerRelayError(
                                    "The downloaded update is larger than expected"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
            if copied != expected_size or digest.hexdigest() != expected_sha256:
                raise ControllerRelayError(
                    "The downloaded update failed integrity verification"
                )
            os.replace(temporary, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            return {
                "path": str(target),
                "sha256": expected_sha256,
                "size_bytes": expected_size,
            }
        except httpx.HTTPError as exc:
            raise ControllerRelayError(
                f"Cannot download the macOS update: {exc}"
            ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _download_matches(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> bool:
        try:
            if not path.is_file() or path.stat().st_size != expected_size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == expected_sha256
        except OSError:
            return False
