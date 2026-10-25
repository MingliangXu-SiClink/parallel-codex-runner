from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .protocol import utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - supported Unix endpoints have fcntl
    fcntl = None


DEVICE_STATE_VERSION = 1
DEFAULT_PAIRING_TTL_SECONDS = 5 * 60


class DeviceRegistryError(RuntimeError):
    pass


def _parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _future(seconds: int) -> str:
    return (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(1, seconds))
    ).isoformat()


@dataclass
class PairedDevice:
    device_id: str
    user_id: str
    name: str
    kind: str
    public_key: str
    created_at: str = field(default_factory=utc_now)
    last_seen_at: str = field(default_factory=utc_now)
    revoked_at: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "PairedDevice | None":
        if not isinstance(value, dict):
            return None
        required = ("device_id", "user_id", "name", "kind", "public_key")
        if any(not str(value.get(name) or "").strip() for name in required):
            return None
        return cls(
            device_id=str(value["device_id"]),
            user_id=str(value["user_id"]),
            name=str(value["name"]),
            kind=str(value["kind"]),
            public_key=str(value["public_key"]),
            created_at=str(value.get("created_at") or utc_now()),
            last_seen_at=str(value.get("last_seen_at") or utc_now()),
            revoked_at=str(value.get("revoked_at") or ""),
        )


@dataclass
class PendingPairing:
    pairing_id: str
    secret_digest: str
    created_at: str
    expires_at: str

    @classmethod
    def from_mapping(cls, value: object) -> "PendingPairing | None":
        if not isinstance(value, dict):
            return None
        pairing_id = str(value.get("pairing_id") or "")
        digest = str(value.get("secret_digest") or "")
        if not pairing_id or not digest:
            return None
        return cls(
            pairing_id=pairing_id,
            secret_digest=digest,
            created_at=str(value.get("created_at") or utc_now()),
            expires_at=str(value.get("expires_at") or utc_now()),
        )


class DeviceRegistry:
    """Machine-side paired-device state; account identity is supplied by the relay."""

    def __init__(self, path: Path, *, pairing_key_path: Path | None = None) -> None:
        self.path = path.expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.pairing_key_path = pairing_key_path or self.path.with_name("pairing.key")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._thread_lock = threading.RLock()
        self._pairing_key = self._load_or_create_key()
        self._ensure_state()

    def _ensure_state(self) -> None:
        with self._locked():
            if self.path.exists():
                self._read_unlocked()
                return
            revision, mac_id, devices, pairings = self._read_unlocked()
            self._write_unlocked(revision, mac_id, devices, pairings)

    def _load_or_create_key(self) -> bytes:
        try:
            encoded = self.pairing_key_path.read_text(encoding="ascii").strip()
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except FileNotFoundError:
            key = secrets.token_bytes(32)
            encoded = base64.urlsafe_b64encode(key).decode("ascii")
            try:
                descriptor = os.open(
                    self.pairing_key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                encoded = self.pairing_key_path.read_text(encoding="ascii").strip()
                key = base64.urlsafe_b64decode(encoded.encode("ascii"))
            else:
                with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except (OSError, ValueError, binascii.Error) as exc:
            raise DeviceRegistryError("Cannot read the machine pairing key") from exc
        if len(key) != 32:
            raise DeviceRegistryError("The machine pairing key is invalid")
        try:
            self.pairing_key_path.chmod(0o600)
        except OSError:
            pass
        return key

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

    def _read_unlocked(
        self,
    ) -> tuple[int, str, dict[str, PairedDevice], dict[str, PendingPairing]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0, f"mac-{uuid.uuid4().hex[:16]}", {}, {}
        except (OSError, ValueError) as exc:
            raise DeviceRegistryError("Cannot read paired-device state") from exc
        if not isinstance(value, dict):
            raise DeviceRegistryError("Paired-device state version is invalid")
        try:
            version = int(value.get("version") or 0)
            revision = max(0, int(value.get("revision") or 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise DeviceRegistryError("Paired-device state is invalid") from exc
        if version != DEVICE_STATE_VERSION:
            raise DeviceRegistryError("Paired-device state version is invalid")
        devices: dict[str, PairedDevice] = {}
        for raw in value.get("devices", []):
            device = PairedDevice.from_mapping(raw)
            if device is not None:
                devices[device.device_id] = device
        pairings: dict[str, PendingPairing] = {}
        for raw in value.get("pending_pairings", []):
            pairing = PendingPairing.from_mapping(raw)
            if pairing is not None:
                pairings[pairing.pairing_id] = pairing
        return (
            revision,
            str(value.get("mac_endpoint_id") or f"mac-{uuid.uuid4().hex[:16]}"),
            devices,
            pairings,
        )

    def _write_unlocked(
        self,
        revision: int,
        mac_endpoint_id: str,
        devices: dict[str, PairedDevice],
        pairings: dict[str, PendingPairing],
    ) -> None:
        value = {
            "version": DEVICE_STATE_VERSION,
            "revision": revision,
            "mac_endpoint_id": mac_endpoint_id,
            "devices": [asdict(item) for item in devices.values()],
            "pending_pairings": [asdict(item) for item in pairings.values()],
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
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _digest(self, pairing_id: str, secret: str) -> str:
        return hmac.new(
            self._pairing_key,
            f"{pairing_id}\0{secret}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _validate_public_key(value: str) -> str:
        value = value.strip()
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DeviceRegistryError("Device public_key must be base64") from exc
        if len(decoded) != 32:
            raise DeviceRegistryError("Device public_key must contain 32 bytes")
        return value

    @staticmethod
    def _purge_expired(pairings: dict[str, PendingPairing]) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        for pairing_id, pairing in list(pairings.items()):
            try:
                expired = _parse_time(pairing.expires_at) <= now
            except ValueError:
                expired = True
            if expired:
                pairings.pop(pairing_id, None)

    def list(self) -> dict[str, Any]:
        with self._locked():
            revision, mac_id, devices, pairings = self._read_unlocked()
            previous_count = len(pairings)
            self._purge_expired(pairings)
            if len(pairings) != previous_count:
                revision += 1
                self._write_unlocked(revision, mac_id, devices, pairings)
        return {
            "revision": revision,
            "mac_endpoint_id": mac_id,
            "devices": [
                {
                    **asdict(device),
                    "active": not bool(device.revoked_at),
                }
                for device in sorted(
                    devices.values(),
                    key=lambda item: item.last_seen_at,
                    reverse=True,
                )
            ],
            "pending_pairings": len(pairings),
        }

    def create_pairing(self, ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS) -> dict[str, Any]:
        ttl_seconds = min(max(30, int(ttl_seconds)), 15 * 60)
        pairing_id = f"pair-{uuid.uuid4().hex[:16]}"
        secret = secrets.token_urlsafe(24)
        pairing = PendingPairing(
            pairing_id=pairing_id,
            secret_digest=self._digest(pairing_id, secret),
            created_at=utc_now(),
            expires_at=_future(ttl_seconds),
        )
        with self._locked():
            revision, mac_id, devices, pairings = self._read_unlocked()
            self._purge_expired(pairings)
            pairings[pairing_id] = pairing
            revision += 1
            self._write_unlocked(revision, mac_id, devices, pairings)
        query = urllib.parse.urlencode(
            {"mac": mac_id, "pairing": pairing_id, "secret": secret}
        )
        return {
            "revision": revision,
            "mac_endpoint_id": mac_id,
            "pairing_id": pairing_id,
            "secret": secret,
            "expires_at": pairing.expires_at,
            "pairing_uri": f"pcr://pair?{query}",
        }

    def complete_pairing(
        self,
        pairing_id: str,
        secret: str,
        *,
        user_id: str,
        device_name: str,
        public_key: str,
        kind: str = "android",
    ) -> dict[str, Any]:
        user_id = user_id.strip()
        device_name = device_name.strip()
        if not user_id or not device_name:
            raise DeviceRegistryError("user_id and device_name must not be empty")
        if kind not in {"android"}:
            raise DeviceRegistryError("Unsupported paired device kind")
        public_key = self._validate_public_key(public_key)
        with self._locked():
            revision, mac_id, devices, pairings = self._read_unlocked()
            self._purge_expired(pairings)
            pairing = pairings.get(pairing_id)
            if pairing is None:
                raise DeviceRegistryError("Pairing challenge is missing or expired")
            digest = self._digest(pairing_id, secret)
            if not hmac.compare_digest(digest, pairing.secret_digest):
                raise DeviceRegistryError("Pairing secret is invalid")
            pairings.pop(pairing_id, None)
            device = PairedDevice(
                device_id=f"android-{uuid.uuid4().hex[:16]}",
                user_id=user_id,
                name=device_name,
                kind=kind,
                public_key=public_key,
            )
            devices[device.device_id] = device
            revision += 1
            self._write_unlocked(revision, mac_id, devices, pairings)
        return {"revision": revision, "device": {**asdict(device), "active": True}}

    def revoke(self, device_id: str) -> dict[str, Any]:
        with self._locked():
            revision, mac_id, devices, pairings = self._read_unlocked()
            device = devices.get(device_id)
            if device is None:
                raise DeviceRegistryError(f"Unknown paired device: {device_id}")
            if not device.revoked_at:
                device.revoked_at = utc_now()
                revision += 1
                self._write_unlocked(revision, mac_id, devices, pairings)
        return {"revision": revision, "device": {**asdict(device), "active": False}}
