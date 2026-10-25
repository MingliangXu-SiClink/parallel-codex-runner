from __future__ import annotations

import datetime as dt
import hmac
import platform
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

import httpx

from .crypto import (
    DeviceKeyPair,
    RemoteCryptoError,
    create_pairing_secret,
    pairing_commitment,
    pairing_proof,
)
from .relay_state import RelayState, RelayStateError, RelayStateStore, utc_now


class RelayHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_path: str = "",
        code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_path = request_path
        self.code = code

    @property
    def refresh_token_rejected(self) -> bool:
        return (
            self.status_code in {401, 403}
            and self.request_path in {"/v1/auth/refresh", "/v1/auth/device/challenge", "/v1/auth/device"}
            and self.code not in {"challenge_expired", "challenge_used", "challenge_invalid"}
        )


class RelayHttpClient:
    def __init__(
        self,
        state_dir: Path,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.store = RelayStateStore(self.state_dir / "relay.json")
        self.keys = DeviceKeyPair.load_or_create(self.state_dir / "relay-device.key")
        self.timeout = max(1.0, timeout)
        self.transport = transport
        self._pairing_lock = threading.RLock()
        self._refresh_lock = threading.RLock()

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_path = urllib.parse.urlsplit(url).path
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                request_options: dict[str, Any] = {
                    "headers": dict(headers or {}),
                }
                if json_body is not None:
                    request_options["json"] = dict(json_body)
                response = client.request(method, url, **request_options)
        except httpx.HTTPError as exc:
            raise RelayHttpError(
                f"Cannot contact PCR relay: {exc}",
                request_path=request_path,
            ) from exc
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            if response.is_error:
                raise RelayHttpError(
                    f"PCR relay request failed with HTTP {response.status_code}",
                    status_code=response.status_code,
                    request_path=request_path,
                ) from exc
            raise RelayHttpError(
                "PCR relay returned invalid JSON",
                request_path=request_path,
            ) from exc
        if response.is_error:
            detail = (
                str(payload.get("detail") or "")
                if isinstance(payload, dict)
                else ""
            )
            raise RelayHttpError(
                detail or f"PCR relay request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                request_path=request_path,
                code=str(payload.get("code") or "") if isinstance(payload, dict) else "",
            )
        if not isinstance(payload, dict):
            raise RelayHttpError(
                "PCR relay returned an invalid response",
                request_path=request_path,
            )
        return payload

    def enroll(
        self,
        *,
        base_url: str,
        enrollment_token: str,
        device_name: str = "",
    ) -> dict[str, Any]:
        with self._pairing_lock:
            return self._enroll(
                base_url=base_url,
                enrollment_token=enrollment_token,
                device_name=device_name,
            )

    def create_pairing(
        self,
        *,
        base_url: str,
        enrollment_token: str,
        device_name: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        from .relay_state import normalize_relay_url

        base_url = normalize_relay_url(base_url)
        with self._pairing_lock:
            state = self.store.load()
            if (
                not force
                and state is not None
                and state.base_url == base_url
                and self._pending_pairing_is_valid(state)
            ):
                return state.public_mapping(include_pairing_uri=True)
            return self._enroll(
                base_url=base_url,
                enrollment_token=enrollment_token,
                device_name=device_name,
            )

    @staticmethod
    def _pending_pairing_is_valid(state: RelayState) -> bool:
        if not state.pairing_id or not state.pairing_uri or not state.poll_token:
            return False
        try:
            expires_at = dt.datetime.fromisoformat(state.pairing_expires_at)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        return expires_at > utc_now() + dt.timedelta(seconds=5)

    def _enroll(
        self,
        *,
        base_url: str,
        enrollment_token: str,
        device_name: str = "",
    ) -> dict[str, Any]:
        from .relay_state import normalize_relay_url

        base_url = normalize_relay_url(base_url)
        enrollment_token = enrollment_token.strip()
        if len(enrollment_token) < 32:
            raise RelayStateError("Machine enrollment token must contain 32 characters")
        device_name = device_name.strip() or platform.node() or "Computer"
        secret = create_pairing_secret()
        approval_token = create_pairing_secret()
        payload = self._request(
            "POST",
            f"{base_url}/v1/mac/enroll",
            headers={"X-PCR-Enrollment": enrollment_token},
            json_body={
                "device_name": device_name,
                "public_key": self.keys.public_key,
                "pairing_commitment": pairing_commitment(secret),
                "approval_commitment": pairing_commitment(approval_token),
            },
        )
        required = (
            "mac_device_id",
            "pairing_id",
            "poll_token",
            "expires_at",
        )
        if any(not str(payload.get(name) or "") for name in required):
            raise RelayHttpError("PCR relay enrollment response is incomplete")
        previous = self.store.load()
        same_device = (
            previous is not None
            and previous.base_url == base_url
            and previous.mac_device_id == str(payload["mac_device_id"])
        )
        pairing_query = urllib.parse.urlencode(
            {
                "relay": base_url,
                "pairing": str(payload["pairing_id"]),
                "secret": secret,
                "auth": approval_token,
                "mac": str(payload["mac_device_id"]),
                "mac_key": self.keys.public_key,
            }
        )
        state = RelayState(
            base_url=base_url,
            mac_device_id=str(payload["mac_device_id"]),
            device_name=device_name,
            pairing_id=str(payload["pairing_id"]),
            poll_token=str(payload["poll_token"]),
            pairing_secret=secret,
            pairing_uri=f"pcr://pair?{pairing_query}",
            pairing_expires_at=str(payload["expires_at"]),
            access_token=previous.access_token if same_device else "",
            refresh_token=previous.refresh_token if same_device else "",
            access_expires_at=previous.access_expires_at if same_device else "",
            user_id=previous.user_id if same_device else "",
            sequence=previous.sequence if same_device else 0,
            trusted_device_keys=(
                dict(previous.trusted_device_keys)
                if same_device
                else {}
            ),
        )
        self.store.save(state)
        return state.public_mapping(include_pairing_uri=True)

    def poll_pairing(
        self,
        *,
        expected_pairing_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.store.load()
        if state is None or not state.pairing_id or not state.poll_token:
            raise RelayStateError("No pending machine pairing is configured")
        if (
            expected_pairing_id is not None
            and state.pairing_id != expected_pairing_id
        ):
            raise RelayStateError(
                "This pairing code was replaced by a newer pairing request"
            )
        pairing_id = state.pairing_id
        payload = self._request(
            "POST",
            f"{state.base_url}/v1/pairings/{pairing_id}/poll",
            json_body={"poll_token": state.poll_token},
        )
        pairing_status = str(payload.get("status") or "")
        raw_tokens = payload.get("tokens")
        if pairing_status == "approved":
            if not isinstance(raw_tokens, Mapping):
                raise RelayHttpError("PCR relay pairing approval is incomplete")
            android_device_id = str(payload.get("android_device_id") or "")
            android_public_key = str(payload.get("android_public_key") or "")
            supplied_proof = str(payload.get("pairing_proof") or "")
            if not android_device_id or not android_public_key or not supplied_proof:
                raise RelayHttpError("PCR relay pairing proof is incomplete")
            expected_proof = pairing_proof(
                state.pairing_secret,
                pairing_id=state.pairing_id,
                mac_device_id=state.mac_device_id,
                mac_public_key=self.keys.public_key,
                android_device_id=android_device_id,
                android_public_key=android_public_key,
            )
            if not hmac.compare_digest(supplied_proof, expected_proof):
                raise RelayHttpError("Android pairing proof is invalid")
            state = self.store.trust_device_and_update_tokens(
                raw_tokens,
                device_id=android_device_id,
                public_key=android_public_key,
                expected_pairing_id=pairing_id,
            )
        return {
            **state.public_mapping(include_pairing_uri=True),
            "pairing_id": pairing_id,
            "pairing_status": pairing_status,
        }

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            return {"configured": False, "paired": False}
        return state.public_mapping()

    @staticmethod
    def _access_expiring(state: RelayState) -> bool:
        try:
            expires = dt.datetime.fromisoformat(state.access_expires_at)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.timezone.utc)
        return expires <= utc_now() + dt.timedelta(seconds=60)

    def ensure_access_token(self, *, force_refresh: bool = False) -> RelayState:
        with self._refresh_lock:
            # Re-read after taking the lock. Another daemon thread may already
            # have refreshed and persisted the shared credential.
            state = self.store.load()
            if state is None or not state.paired:
                raise RelayStateError("This machine is not paired with the PCR relay")
            if not force_refresh and state.access_token and not self._access_expiring(state):
                return state
            if state.refresh_token:
                try:
                    payload = self._request(
                        "POST", f"{state.base_url}/v1/auth/refresh",
                        json_body={"refresh_token": state.refresh_token},
                    )
                except RelayHttpError as exc:
                    if not exc.refresh_token_rejected or exc.code in {
                        "refresh_revoked", "device_inactive", "device_unknown", "user_inactive",
                    }:
                        raise
                    payload = self._device_login(state)
            else:
                payload = self._device_login(state)
            if (payload.get("device_id") != state.mac_device_id
                    or payload.get("user_id") != state.user_id):
                raise RelayStateError("Relay sign-in returned a different device or account")
            return self.store.update_tokens(payload, expected_state=state)

    def _device_login(self, state: RelayState) -> dict[str, Any]:
        challenge = self._request(
            "POST", f"{state.base_url}/v1/auth/device/challenge",
            json_body={"device_id": state.mac_device_id},
        )
        try:
            proof = self.keys.device_login_proof(
                challenge, device_id=state.mac_device_id, user_id=state.user_id,
                relay_url=state.base_url,
            )
        except RemoteCryptoError as exc:
            raise RelayHttpError(str(exc), request_path="/v1/auth/device/challenge") from exc
        return self._request(
            "POST", f"{state.base_url}/v1/auth/device",
            json_body={"device_id": state.mac_device_id,
                       "challenge_id": challenge["challenge_id"], "proof": proof},
        )

    def list_devices(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        state = self.ensure_access_token(force_refresh=force_refresh)
        payload = self._request(
            "GET",
            f"{state.base_url}/v1/devices",
            headers={"Authorization": f"Bearer {state.access_token}"},
        )
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            raise RelayHttpError("PCR relay device response is invalid")
        return [dict(item) for item in raw_devices if isinstance(item, Mapping)]

    def forget(self) -> bool:
        state = self.store.load()
        if state is None:
            return False
        if state.paired:
            try:
                state = self.ensure_access_token()
                self._request(
                    "DELETE",
                    f"{state.base_url}/v1/devices/{state.mac_device_id}",
                    headers={"Authorization": f"Bearer {state.access_token}"},
                )
            except (RelayHttpError, RelayStateError):
                # Revocation is best effort. An offline or already-revoked relay
                # must not make local credentials impossible to remove.
                pass
        return self.store.clear()
