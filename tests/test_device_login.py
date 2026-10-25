from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from parallel_codex_runner_core.remote.crypto import DeviceKeyPair, RemoteCryptoError
from parallel_codex_runner_core.remote.relay_credentials import RelayCredentialMaintainer
from parallel_codex_runner_core.remote.relay_http import RelayHttpClient, RelayHttpError
from parallel_codex_runner_core.remote.relay_state import RelayState, RelayStateError, utc_now


class _DeviceLoginRelay:
    def __init__(self):
        self.public_key = ""
        self.device_id = "macos-test"
        self.paths: list[str] = []
        self.proofs: dict[str, str] = {}
        self.challenge_changes: dict[str, object] = {}
        self.refresh_code = "refresh_expired"
        self.unavailable = False
        self.lose_response = False
        self.before_response = lambda: None

    def http(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        body = json.loads(request.content or b"{}")
        if self.unavailable:
            raise httpx.ConnectError("Test offline network", request=request)
        if path == "/v1/auth/refresh":
            return httpx.Response(401, json={"detail": "Expired refresh", "code": self.refresh_code})
        if path == "/v1/auth/device/challenge":
            private = X25519PrivateKey.generate()
            challenge = {
                "version": 1, "purpose": "session-bootstrap", "challenge_id": str(len(self.proofs)),
                "device_id": self.device_id, "user_id": "user-test", "authorization_generation": 0,
                "relay_url": "https://relay.example", "public_key": self.public_key,
                "server_public_key": base64.b64encode(private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw,
                )).decode(),
                "nonce": base64.b64encode(os.urandom(32)).decode(),
            }
            shared = private.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(self.public_key)))
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(challenge["nonce"]),
                       info=b"PCR Relay session bootstrap v1").derive(shared)
            transcript = json.dumps(challenge, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            self.proofs[challenge["challenge_id"]] = base64.b64encode(hmac.new(key, transcript, hashlib.sha256).digest()).decode()
            return httpx.Response(200, json={**challenge, "expires_at": (utc_now() + dt.timedelta(seconds=120)).isoformat(),
                                            **self.challenge_changes})
        if path == "/v1/auth/device":
            expected = self.proofs.pop(body["challenge_id"])
            if body["proof"] != expected or body["device_id"] != self.device_id:
                return httpx.Response(401, json={"detail": "Wrong key", "code": "key_mismatch"})
            self.before_response()
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadError("Test lost committed response", request=request)
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh",
                                            "expires_in": 900, "user_id": "user-test", "device_id": self.device_id})
        raise AssertionError(f"Unexpected request: {path}")


class DeviceLoginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.relay = _DeviceLoginRelay()
        self.client = RelayHttpClient(Path(self.temporary.name), transport=httpx.MockTransport(self.relay.http))
        self.relay.public_key = self.client.keys.public_key
        self.original = RelayState(
            base_url="https://relay.example", mac_device_id="macos-test", device_name="Test Mac",
            user_id="user-test", access_token="old-access", refresh_token="old-refresh",
            access_expires_at=(utc_now() - dt.timedelta(days=180)).isoformat(),
            sequence=172, trusted_device_keys={"android-test": "pinned-key"},
        )
        self.client.store.save(self.original)

    def tearDown(self):
        self.temporary.cleanup()

    def test_offline_months_recovers_without_pairing_or_changing_trust(self):
        state = self.client.ensure_access_token()
        self.assertEqual(state.access_token, "new-access")
        self.assertEqual(state.sequence, 172)
        self.assertEqual(state.trusted_device_keys, self.original.trusted_device_keys)
        self.assertEqual(state.mac_device_id, self.original.mac_device_id)
        self.assertEqual(self.relay.paths, ["/v1/auth/refresh", "/v1/auth/device/challenge", "/v1/auth/device"])
        self.assertEqual(self.client.ensure_access_token().access_token, "new-access")
        self.assertEqual(len(self.relay.paths), 3)

    def test_missing_session_credentials_keeps_device_identity_usable(self):
        self.client.store.save(replace(self.original, access_token="", refresh_token="", access_expires_at=""))
        self.assertTrue(self.client.status()["paired"])
        self.client.ensure_access_token()
        self.assertEqual(self.relay.paths, ["/v1/auth/device/challenge", "/v1/auth/device"])

    def test_concurrent_requests_bootstrap_only_once(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.client.ensure_access_token(), range(5)))
        self.assertTrue(all(state.access_token == "new-access" for state in results))
        self.assertEqual(self.relay.paths.count("/v1/auth/device"), 1)

    def test_expired_refresh_row_pruned_from_server_is_recoverable(self):
        self.relay.refresh_code = "refresh_invalid"
        self.client.ensure_access_token()
        self.assertIn("/v1/auth/device", self.relay.paths)

    def test_revocation_is_not_silently_overridden(self):
        for code in ("refresh_revoked", "device_inactive", "device_unknown", "user_inactive"):
            with self.subTest(code=code):
                self.relay.paths.clear()
                self.relay.refresh_code = code
                with self.assertRaises(RelayHttpError):
                    self.client.ensure_access_token()
                self.assertEqual(self.relay.paths, ["/v1/auth/refresh"])
                self.assertEqual(self.client.store.load(), self.original)

    def test_network_failure_preserves_pairing_then_recovers(self):
        self.relay.unavailable = True
        with self.assertRaises(RelayHttpError):
            self.client.ensure_access_token()
        self.assertEqual(self.client.store.load(), self.original)
        self.relay.unavailable = False
        self.client.ensure_access_token()

    def test_lost_success_response_gets_a_new_challenge(self):
        self.relay.lose_response = True
        with self.assertRaises(RelayHttpError):
            self.client.ensure_access_token()
        self.assertEqual(self.client.store.load(), self.original)
        self.client.ensure_access_token()
        self.assertEqual(self.relay.paths.count("/v1/auth/device/challenge"), 2)

    def test_mismatched_challenge_is_never_answered(self):
        for name, value in {"device_id": "other-device", "user_id": "other-user", "public_key": "other-key",
                            "relay_url": "https://another-relay.example", "version": 2,
                            "purpose": "different-purpose", "authorization_generation": -1}.items():
            with self.subTest(name=name):
                self.relay.paths.clear()
                self.relay.challenge_changes = {name: value}
                with self.assertRaises(RelayHttpError):
                    self.client.ensure_access_token()
                self.assertNotIn("/v1/auth/device", self.relay.paths)
                self.assertEqual(self.client.store.load(), self.original)

    def test_late_response_cannot_overwrite_a_new_pairing(self):
        next_state = replace(self.original, mac_device_id="new-device", user_id="new-user")
        self.relay.before_response = lambda: self.client.store.save(next_state)
        with self.assertRaises(RelayStateError):
            self.client.ensure_access_token()
        self.assertEqual(self.client.store.load(), next_state)

    def test_python_matches_public_cross_platform_vector(self):
        vector = json.loads((Path(__file__).resolve().parents[1] / "remote/protocol/device-login-v1-vector.json").read_text())
        key = DeviceKeyPair(X25519PrivateKey.from_private_bytes(base64.b64decode(vector["device_private_key"])))
        challenge = vector["challenge"]
        proof = key.device_login_proof(challenge, device_id=challenge["device_id"], user_id=challenge["user_id"],
                                       relay_url=challenge["relay_url"])
        self.assertEqual(proof, vector["proof"])
        for field, value in {"server_public_key": base64.b64encode(bytes(32)).decode(),
                             "nonce": "invalid", "authorization_generation": True}.items():
            with self.subTest(field=field), self.assertRaises(RemoteCryptoError):
                key.device_login_proof({**challenge, field: value}, device_id=challenge["device_id"],
                                      user_id=challenge["user_id"], relay_url=challenge["relay_url"])

    def test_expired_challenge_is_retryable_not_revoked(self):
        for code in ("challenge_expired", "challenge_used", "challenge_invalid"):
            self.assertFalse(RelayHttpError("Test", status_code=401, request_path="/v1/auth/device", code=code).refresh_token_rejected)


class CredentialMaintainerTests(unittest.TestCase):
    def test_both_roles_renew_without_foreground_client_requests(self):
        clients = [mock.Mock(), mock.Mock()]
        for client in clients:
            client.store.load.return_value = mock.Mock(paired=True, updated_at="v1")
        maintainer = RelayCredentialMaintainer(*clients)
        maintainer.poll_once()
        for client in clients:
            client.ensure_access_token.assert_called_once()

    def test_auth_failure_does_not_block_other_role_or_discard_pairing(self):
        host, controller = mock.Mock(), mock.Mock()
        host.store.load.return_value = mock.Mock(paired=True, updated_at="v1")
        controller.store.load.return_value = mock.Mock(paired=True, updated_at="v1")
        host.ensure_access_token.side_effect = RelayHttpError(
            "Revoked test device", status_code=401, request_path="/v1/auth/device/challenge", code="device_inactive",
        )
        maintainer = RelayCredentialMaintainer(host, controller)
        with self.assertLogs("parallel_codex_runner_core.remote.relay_credentials", level="WARNING"):
            maintainer.poll_once()
        maintainer.poll_once()
        self.assertEqual(host.ensure_access_token.call_count, 1)
        self.assertEqual(controller.ensure_access_token.call_count, 2)
        host.store.clear.assert_not_called()
        host.store.load.return_value.updated_at = "v2"
        host.ensure_access_token.side_effect = None
        maintainer.poll_once()
        self.assertEqual(host.ensure_access_token.call_count, 2)

    def test_network_failure_retries_after_backoff(self):
        client = mock.Mock()
        client.store.load.return_value = mock.Mock(paired=True, updated_at="v1")
        client.ensure_access_token.side_effect = RelayHttpError("Test offline")
        maintainer = RelayCredentialMaintainer(client, interval=60)
        with mock.patch("parallel_codex_runner_core.remote.relay_credentials.time.monotonic", return_value=1), self.assertLogs(
            "parallel_codex_runner_core.remote.relay_credentials", level="WARNING",
        ):
            maintainer.poll_once()
        with mock.patch("parallel_codex_runner_core.remote.relay_credentials.time.monotonic", return_value=30):
            maintainer.poll_once()
        self.assertEqual(client.ensure_access_token.call_count, 1)
        client.ensure_access_token.side_effect = None
        with mock.patch("parallel_codex_runner_core.remote.relay_credentials.time.monotonic", return_value=62):
            maintainer.poll_once()
        self.assertEqual(client.ensure_access_token.call_count, 2)


if __name__ == "__main__":
    unittest.main()
