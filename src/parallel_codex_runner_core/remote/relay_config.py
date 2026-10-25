from __future__ import annotations

import json
import os
import platform
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .relay_state import RelayStateError, normalize_relay_url


RELAY_CONFIGURATION_VERSION = 1
MACHINE_ENROLLMENT_TOKEN_ENV = "PCR_RELAY_MACHINE_ENROLLMENT_TOKEN"
LEGACY_ENROLLMENT_TOKEN_ENV = "PCR_RELAY_MAC_ENROLLMENT_TOKEN"


class RelayConfigurationError(RuntimeError):
    pass


def environment_enrollment_token() -> str:
    return (
        os.environ.get(MACHINE_ENROLLMENT_TOKEN_ENV, "").strip()
        or os.environ.get(LEGACY_ENROLLMENT_TOKEN_ENV, "").strip()
    )


@dataclass(frozen=True)
class RelayConfiguration:
    base_url: str
    enrollment_token: str
    device_name: str

    @classmethod
    def create(
        cls,
        *,
        base_url: str,
        enrollment_token: str,
        device_name: str = "",
    ) -> "RelayConfiguration":
        try:
            normalized_url = normalize_relay_url(base_url)
        except RelayStateError as exc:
            raise RelayConfigurationError(str(exc)) from exc
        normalized_token = enrollment_token.strip()
        if len(normalized_token) < 32:
            raise RelayConfigurationError(
                "Server enrollment token must contain at least 32 characters"
            )
        normalized_name = device_name.strip() or platform.node().strip() or "Computer"
        if len(normalized_name) > 128:
            raise RelayConfigurationError(
                "Device name must contain at most 128 characters"
            )
        return cls(
            base_url=normalized_url,
            enrollment_token=normalized_token,
            device_name=normalized_name,
        )

    @classmethod
    def from_mapping(cls, value: object) -> "RelayConfiguration":
        if not isinstance(value, Mapping):
            raise RelayConfigurationError("Server configuration must be a JSON object")
        if int(value.get("version") or 0) != RELAY_CONFIGURATION_VERSION:
            raise RelayConfigurationError("Server configuration version is not supported")
        return cls.create(
            base_url=str(value.get("base_url") or ""),
            enrollment_token=str(value.get("enrollment_token") or ""),
            device_name=str(value.get("device_name") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {"version": RELAY_CONFIGURATION_VERSION, **asdict(self)}


class RelayConfigurationStore:
    """Owner-only deployment configuration kept outside user-facing clients."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.RLock()

    def load(self) -> RelayConfiguration | None:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                raise RelayConfigurationError(
                    "Cannot read the server configuration"
                ) from exc
            return RelayConfiguration.from_mapping(value)

    def resolve(self, *, current_base_url: str = "") -> RelayConfiguration | None:
        stored = self.load()
        environment_url = os.environ.get("PCR_RELAY_URL", "").strip()
        environment_token = environment_enrollment_token()
        environment_name = os.environ.get("PCR_RELAY_DEVICE_NAME", "").strip()
        if not environment_url and not environment_token and not environment_name:
            return stored

        base_url = environment_url or (stored.base_url if stored else current_base_url)
        token = environment_token or (stored.enrollment_token if stored else "")
        name = environment_name or (stored.device_name if stored else "")
        if not base_url or not token:
            raise RelayConfigurationError(
                "The installed server configuration is incomplete"
            )
        return RelayConfiguration.create(
            base_url=base_url,
            enrollment_token=token,
            device_name=name,
        )

    def save(self, configuration: RelayConfiguration) -> RelayConfiguration:
        with self._lock:
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        configuration.to_mapping(),
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass
            except OSError as exc:
                raise RelayConfigurationError(
                    "Cannot save the server configuration"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return configuration

    def configure(
        self,
        *,
        base_url: str,
        enrollment_token: str,
        device_name: str = "",
    ) -> RelayConfiguration:
        return self.save(
            RelayConfiguration.create(
                base_url=base_url,
                enrollment_token=enrollment_token,
                device_name=device_name,
            )
        )
