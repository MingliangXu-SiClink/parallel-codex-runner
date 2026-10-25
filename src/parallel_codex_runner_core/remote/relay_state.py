from __future__ import annotations

import datetime as dt
import json
import os
import threading
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - supported Unix endpoints have fcntl
    fcntl = None


RELAY_STATE_VERSION = 1


class RelayStateError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize_relay_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RelayStateError("Relay URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RelayStateError("Relay URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RelayStateError("Remote relay connections must use HTTPS")
    return raw


def relay_websocket_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(normalize_relay_url(base_url))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + "/v1/relay"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))


@dataclass
class RelayState:
    base_url: str
    mac_device_id: str
    device_name: str
    pairing_id: str = ""
    poll_token: str = ""
    pairing_secret: str = ""
    pairing_uri: str = ""
    pairing_expires_at: str = ""
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: str = ""
    user_id: str = ""
    trusted_device_keys: dict[str, str] = field(default_factory=dict)
    selected_device_id: str = ""
    sequence: int = 0
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "RelayState":
        if not isinstance(value, Mapping):
            raise RelayStateError("Relay state must be a JSON object")
        if int(value.get("version") or 0) != RELAY_STATE_VERSION:
            raise RelayStateError("Relay state version is not supported")
        base_url = normalize_relay_url(str(value.get("base_url") or ""))
        mac_device_id = str(value.get("mac_device_id") or "").strip()
        device_name = str(value.get("device_name") or "").strip()
        if not mac_device_id or not device_name:
            raise RelayStateError("Relay state is incomplete")
        try:
            sequence = int(value.get("sequence") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RelayStateError("Relay sequence is invalid") from exc
        if sequence < 0 or sequence > 2**63 - 1:
            raise RelayStateError("Relay sequence is invalid")
        raw_trusted = value.get("trusted_device_keys")
        trusted = (
            {
                str(device_id): str(public_key)
                for device_id, public_key in raw_trusted.items()
                if str(device_id) and str(public_key)
            }
            if isinstance(raw_trusted, Mapping)
            else {}
        )
        return cls(
            base_url=base_url,
            mac_device_id=mac_device_id,
            device_name=device_name,
            pairing_id=str(value.get("pairing_id") or ""),
            poll_token=str(value.get("poll_token") or ""),
            pairing_secret=str(value.get("pairing_secret") or ""),
            pairing_uri=str(value.get("pairing_uri") or ""),
            pairing_expires_at=str(value.get("pairing_expires_at") or ""),
            access_token=str(value.get("access_token") or ""),
            refresh_token=str(value.get("refresh_token") or ""),
            access_expires_at=str(value.get("access_expires_at") or ""),
            user_id=str(value.get("user_id") or ""),
            trusted_device_keys=trusted,
            selected_device_id=str(value.get("selected_device_id") or ""),
            sequence=sequence,
            updated_at=str(value.get("updated_at") or ""),
        )

    @property
    def paired(self) -> bool:
        return bool(self.user_id and self.mac_device_id)

    def public_mapping(
        self,
        *,
        include_pairing_uri: bool = False,
    ) -> dict[str, Any]:
        trusted_android_devices = sum(
            device_id.startswith("android-")
            for device_id in self.trusted_device_keys
        )
        result = {
            "configured": True,
            "paired": self.paired,
            "base_url": self.base_url,
            "mac_device_id": self.mac_device_id,
            "device_name": self.device_name,
            "pairing_id": self.pairing_id,
            "pairing_expires_at": self.pairing_expires_at,
            "user_id": self.user_id,
            "trusted_devices": len(self.trusted_device_keys),
            "trusted_android_devices": trusted_android_devices,
            "trusted_controllers": [
                {"device_id": device_id, "public_key": public_key}
                for device_id, public_key in sorted(self.trusted_device_keys.items())
            ],
            "sequence": self.sequence,
            "updated_at": self.updated_at,
        }
        if include_pairing_uri:
            result["pairing_uri"] = self.pairing_uri
        return result


class RelayStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._thread_lock = threading.RLock()

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

    def _read_unlocked(self) -> RelayState | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RelayStateError("Cannot read relay state") from exc
        return RelayState.from_mapping(value)

    def load(self) -> RelayState | None:
        with self._locked():
            return self._read_unlocked()

    def save(self, state: RelayState) -> RelayState:
        state.base_url = normalize_relay_url(state.base_url)
        state.updated_at = utc_now().isoformat()
        with self._locked():
            self._write_unlocked(state)
        return state

    def _write_unlocked(self, state: RelayState) -> None:
        value = {"version": RELAY_STATE_VERSION, **asdict(state)}
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
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def next_sequence(self) -> int:
        with self._locked():
            state = self._read_unlocked()
            if state is None:
                raise RelayStateError("Relay is not configured")
            if state.sequence >= 2**63 - 1:
                raise RelayStateError("Relay sequence is exhausted; re-pair this machine")
            state.sequence += 1
            state.updated_at = utc_now().isoformat()
            self._write_unlocked(state)
            return state.sequence

    def select_device(self, device_id: str) -> RelayState:
        with self._locked():
            state = self._read_unlocked()
            if state is None:
                raise RelayStateError("Relay is not configured")
            device_id = device_id.strip()
            if device_id and device_id not in state.trusted_device_keys:
                raise RelayStateError("Selected relay device is not trusted")
            state.selected_device_id = device_id
            state.updated_at = utc_now().isoformat()
            self._write_unlocked(state)
            return state

    def update_tokens(
        self, payload: Mapping[str, Any], *, expected_state: RelayState | None = None,
    ) -> RelayState:
        with self._locked():
            state = self._read_unlocked()
            if state is None:
                raise RelayStateError("Relay is not configured")
            if expected_state is not None and any(
                getattr(state, name) != getattr(expected_state, name)
                for name in ("base_url", "mac_device_id", "user_id", "pairing_id")
            ):
                raise RelayStateError("Device pairing changed while sign-in was completing")
            access_token = str(payload.get("access_token") or "")
            refresh_token = str(payload.get("refresh_token") or "")
            user_id = str(payload.get("user_id") or "")
            device_id = str(payload.get("device_id") or "")
            try:
                expires_in = int(payload.get("expires_in") or 0)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RelayStateError("Relay token response is invalid") from exc
            if (
                not access_token
                or not refresh_token
                or not user_id
                or device_id != state.mac_device_id
                or expires_in < 1
            ):
                raise RelayStateError("Relay token response is incomplete")
            state.access_token = access_token
            state.refresh_token = refresh_token
            state.user_id = user_id
            state.access_expires_at = (
                utc_now() + dt.timedelta(seconds=expires_in)
            ).isoformat()
            state.pairing_id = ""
            state.poll_token = ""
            state.pairing_secret = ""
            state.pairing_uri = ""
            state.pairing_expires_at = ""
            state.updated_at = utc_now().isoformat()
            self._write_unlocked(state)
            return state

    def trust_device_and_update_tokens(
        self,
        payload: Mapping[str, Any],
        *,
        device_id: str,
        public_key: str,
        expected_pairing_id: str | None = None,
    ) -> RelayState:
        with self._locked():
            state = self._read_unlocked()
            if state is None:
                raise RelayStateError("Relay is not configured")
            if (
                expected_pairing_id is not None
                and state.pairing_id != expected_pairing_id
            ):
                raise RelayStateError(
                    "This pairing code was replaced by a newer pairing request"
                )
            access_token = str(payload.get("access_token") or "")
            refresh_token = str(payload.get("refresh_token") or "")
            user_id = str(payload.get("user_id") or "")
            token_device_id = str(payload.get("device_id") or "")
            try:
                expires_in = int(payload.get("expires_in") or 0)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RelayStateError("Relay token response is invalid") from exc
            if (
                not device_id
                or not public_key
                or not access_token
                or not refresh_token
                or not user_id
                or token_device_id != state.mac_device_id
                or expires_in < 1
            ):
                raise RelayStateError("Relay pairing response is incomplete")
            state.trusted_device_keys[device_id] = public_key
            state.access_token = access_token
            state.refresh_token = refresh_token
            state.user_id = user_id
            state.access_expires_at = (
                utc_now() + dt.timedelta(seconds=expires_in)
            ).isoformat()
            state.pairing_id = ""
            state.poll_token = ""
            state.pairing_secret = ""
            state.pairing_uri = ""
            state.pairing_expires_at = ""
            state.updated_at = utc_now().isoformat()
            self._write_unlocked(state)
            return state

    def remove_unavailable_trusted_devices(
        self,
        observed: Mapping[str, str],
        *,
        expected: Mapping[str, str],
    ) -> RelayState | None:
        """Drop revoked peer keys without racing a newly completed pairing."""

        with self._locked():
            state = self._read_unlocked()
            if state is None:
                return None
            changed = False
            for device_id, public_key in expected.items():
                if (
                    state.trusted_device_keys.get(device_id) == public_key
                    and observed.get(device_id) != public_key
                ):
                    state.trusted_device_keys.pop(device_id, None)
                    changed = True
            if changed:
                state.updated_at = utc_now().isoformat()
                self._write_unlocked(state)
            return state

    def clear(self) -> bool:
        with self._locked():
            existed = self.path.exists()
            self.path.unlink(missing_ok=True)
            return existed
