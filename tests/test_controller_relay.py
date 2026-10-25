from __future__ import annotations

import base64
import hashlib
import json
import stat
import tempfile
import unittest
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from parallel_codex_runner_core.remote.controller_relay import (
    ControllerRelayError,
    ControllerRelayProxy,
)
from parallel_codex_runner_core.remote.crypto import DeviceKeyPair, pairing_proof


class _RelayFixture:
    def __init__(self) -> None:
        self.machine_id = "mac-machine"
        self.controller_id = "macos-controller"
        self.user_id = "user-1"
        self.machine_keys = DeviceKeyPair.generate()
        self.secret = base64.b64encode(b"s" * 32).decode("ascii")
        self.approval = base64.b64encode(b"a" * 32).decode("ascii")
        self.pairing_id = "pairing-1"
        self.controller_public_key = ""
        self.approved = False
        self.controller_ids: deque[str] = deque([self.controller_id])
        self.revoked_controller_ids: list[str] = []
        self.macos_update = b"private-macos-update"
        self.macos_update_sha256 = hashlib.sha256(self.macos_update).hexdigest()

    @property
    def pairing_code(self) -> str:
        query = urllib.parse.urlencode(
            {
                "relay": "https://relay.example",
                "pairing": self.pairing_id,
                "secret": self.secret,
                "auth": self.approval,
                "mac": self.machine_id,
                "mac_key": self.machine_keys.public_key,
            }
        )
        return f"pcr://pair?{query}"

    def http(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/v1/auth/pairing":
            if self.controller_ids:
                self.controller_id = self.controller_ids.popleft()
            self.controller_public_key = str(body["public_key"])
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "user_id": self.user_id,
                    "device_id": self.controller_id,
                },
            )
        if request.method == "DELETE" and request.url.path.startswith("/v1/devices/"):
            self.revoked_controller_ids.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        if request.url.path == f"/v1/pairings/{self.pairing_id}/approve":
            expected = pairing_proof(
                self.secret,
                pairing_id=self.pairing_id,
                mac_device_id=self.machine_id,
                mac_public_key=self.machine_keys.public_key,
                android_device_id=self.controller_id,
                android_public_key=self.controller_public_key,
            )
            self.approved = body["pairing_proof"] == expected
            return httpx.Response(204)
        if request.url.path == "/v1/devices":
            return httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "device_id": self.machine_id,
                            "kind": "mac",
                            "name": "Build Mac",
                            "public_key": self.machine_keys.public_key,
                            "status": "active",
                            "online": True,
                            "last_seen_at": "2026-08-25T00:00:00Z",
                        }
                    ]
                },
            )
        if request.url.path == "/v1/feedback":
            return httpx.Response(200, json={"items": [], "has_more": False})
        if request.url.path == "/v1/app-updates/macos/check":
            return httpx.Response(
                200,
                json={
                    "available": True,
                    "update": {
                        "release_id": "macos-release-1",
                        "sha256": self.macos_update_sha256,
                        "size_bytes": len(self.macos_update),
                    },
                },
            )
        if request.url.path == "/v1/app-updates/macos/tickets":
            return httpx.Response(200, json={"ticket": "t" * 48})
        if request.url.path == "/v1/app-updates/macos/download":
            if request.headers.get("X-PCR-Update-Ticket") != "t" * 48:
                return httpx.Response(404, json={"detail": "Update is unavailable"})
            return httpx.Response(
                200,
                content=self.macos_update,
                headers={"X-PCR-Update-SHA256": self.macos_update_sha256},
            )
        raise AssertionError(f"Unexpected relay request: {request.method} {request.url}")


class _FakeWebSocket:
    def __init__(self, fixture: _RelayFixture, controller_keys: DeviceKeyPair) -> None:
        self.fixture = fixture
        self.controller_keys = controller_keys
        self.messages: deque[str] = deque()
        self.closed = False

    def __enter__(self) -> "_FakeWebSocket":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def send(self, raw: str) -> None:
        wire = json.loads(raw)
        if wire.get("type") == "ack":
            return
        message_id = str(wire["message_id"])
        sequence = int(wire["sequence"])
        plain = self.fixture.machine_keys.decrypt(
            self.controller_keys.public_key,
            str(wire["ciphertext"]),
            message_id=message_id,
            sender_device_id=self.fixture.controller_id,
            target_device_id=self.fixture.machine_id,
            sequence=sequence,
        )
        request = plain["request"]
        self.messages.append(
            json.dumps({"type": "accepted", "message_id": message_id})
        )
        response_id = "response-message"
        response_sequence = 17
        ciphertext = self.fixture.machine_keys.encrypt(
            self.controller_keys.public_key,
            {
                "type": "response",
                "response": {
                    "protocol_version": 1,
                    "request_id": request["request_id"],
                    "command_id": request["command_id"],
                    "ok": True,
                    "body": {"status": "ok", "echo": request["body"]},
                },
            },
            message_id=response_id,
            sender_device_id=self.fixture.machine_id,
            target_device_id=self.fixture.controller_id,
            sequence=response_sequence,
        )
        self.messages.append(
            json.dumps(
                {
                    "type": "envelope",
                    "message_id": response_id,
                    "sender_device_id": self.fixture.machine_id,
                    "target_device_id": self.fixture.controller_id,
                    "sequence": response_sequence,
                    "ciphertext": ciphertext,
                }
            )
        )

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        return self.messages.popleft()


class ControllerRelayTests(unittest.TestCase):
    def test_pcrd_owns_pairing_credentials_and_forwards_requests(self) -> None:
        fixture = _RelayFixture()
        with tempfile.TemporaryDirectory() as tmp:
            sockets: list[_FakeWebSocket] = []

            def connect_websocket(*_args: Any, **_kwargs: Any) -> _FakeWebSocket:
                websocket = _FakeWebSocket(fixture, proxy.keys)
                sockets.append(websocket)
                return websocket

            proxy = ControllerRelayProxy(
                Path(tmp),
                transport=httpx.MockTransport(fixture.http),
                websocket_connect=connect_websocket,
            )
            status = proxy.pair(
                fixture.pairing_code,
                device_name="Parallel Codex on Test Mac",
            )
            self.assertTrue(fixture.approved)
            self.assertTrue(status["paired"])
            self.assertEqual(status["selected_machine_id"], fixture.machine_id)
            self.assertEqual(
                proxy.identity(),
                {
                    "device_id": fixture.controller_id,
                    "public_key": proxy.keys.public_key,
                },
            )
            self.assertEqual(
                [item["device_id"] for item in proxy.machines()["devices"]],
                [fixture.machine_id],
            )

            response = proxy.call(
                machine_id=fixture.machine_id,
                machine_public_key=fixture.machine_keys.public_key,
                action="system.health",
                body={"sample": "value"},
                expected_revision=None,
                command_id="command-1",
                timeout_seconds=5,
            )
            self.assertTrue(response["ok"])
            self.assertEqual(response["body"]["echo"], {"sample": "value"})
            second_response = proxy.call(
                machine_id=fixture.machine_id,
                machine_public_key=fixture.machine_keys.public_key,
                action="system.health",
                body={"sample": "second"},
                expected_revision=None,
                command_id="command-2",
                timeout_seconds=5,
            )
            self.assertEqual(second_response["body"]["echo"], {"sample": "second"})
            self.assertEqual(len(sockets), 1)
            self.assertEqual(
                proxy.http_request(
                    method="GET",
                    path="/v1/feedback?limit=1",
                    body=None,
                ),
                {"items": [], "has_more": False},
            )
            update_check = proxy.http_request(
                method="POST",
                path="/v1/app-updates/macos/check",
                body={
                    "mac_device_id": fixture.machine_id,
                    "bundle_id": "com.parallelcodexrunner.mac",
                    "current_build_number": 1,
                },
            )
            self.assertTrue(update_check["available"])
            download = proxy.download_macos_update(
                ticket="t" * 48,
                release_id="macos-release-1",
                expected_sha256=fixture.macos_update_sha256,
                expected_size=len(fixture.macos_update),
            )
            downloaded = Path(str(download["path"]))
            self.assertEqual(downloaded.read_bytes(), fixture.macos_update)
            self.assertEqual(stat.S_IMODE(downloaded.stat().st_mode), 0o600)

            controller_dir = Path(tmp) / "controller-relay"
            self.assertEqual(stat.S_IMODE(controller_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((controller_dir / "relay.json").stat().st_mode),
                0o600,
            )
            self.assertEqual(
                stat.S_IMODE((controller_dir / "relay-device.key").stat().st_mode),
                0o600,
            )
            self.assertNotIn("access_token", json.dumps(proxy.status()))
            proxy.close()
            self.assertTrue(sockets[0].closed)

    def test_repair_revokes_the_obsolete_controller_identity(self) -> None:
        fixture = _RelayFixture()
        fixture.controller_ids = deque(["macos-old", "macos-current"])
        with tempfile.TemporaryDirectory() as tmp:
            proxy = ControllerRelayProxy(
                Path(tmp),
                transport=httpx.MockTransport(fixture.http),
            )

            proxy.pair(fixture.pairing_code, device_name="Parallel Codex")
            proxy.pair(fixture.pairing_code, device_name="Parallel Codex")

            self.assertEqual(proxy.identity()["device_id"], "macos-current")
            self.assertEqual(fixture.revoked_controller_ids, ["macos-old"])

    def test_product_http_proxy_rejects_unrelated_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proxy = ControllerRelayProxy(Path(tmp))
            with self.assertRaises(ControllerRelayError):
                proxy.http_request(
                    method="GET",
                    path="/v1/devices",
                    body=None,
                )


if __name__ == "__main__":
    unittest.main()
