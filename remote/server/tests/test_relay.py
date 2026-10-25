import asyncio
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from pcr_relay.admin import list_users, set_user_status
from pcr_relay.app import create_app
from pcr_relay.auth import AccessClaims, WeChatIdentity
from pcr_relay.config import ConfigurationError, Settings
from pcr_relay.db import Database, DeviceRecord, UserRecord
from pcr_relay.relay import (
    ConnectionHub,
    RelayConnection,
    RelayConnectionClosed,
    RelayProtocolError,
    RelayService,
    relay_device_lock_order,
)


class FakeWeChatClient:
    async def exchange(self, code: str) -> WeChatIdentity:
        if code != "valid-code":
            raise RuntimeError("unexpected code")
        return WeChatIdentity("unionid:approved-user")


def public_key(value: bytes) -> str:
    return base64.b64encode(value * 32).decode("ascii")


def ciphertext(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def pairing_values(
    secret: str,
    *,
    pairing_id: str,
    mac_device_id: str,
    mac_public_key: str,
    android_device_id: str,
    android_public_key: str,
) -> tuple[str, str]:
    raw_secret = base64.b64decode(secret)
    commitment = base64.b64encode(hashlib.sha256(raw_secret).digest()).decode("ascii")
    context = json.dumps(
        {
            "android_device_id": android_device_id,
            "android_public_key": android_public_key,
            "mac_device_id": mac_device_id,
            "mac_public_key": mac_public_key,
            "pairing_id": pairing_id,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    proof = base64.b64encode(hmac.new(raw_secret, context, hashlib.sha256).digest())
    return commitment, proof.decode("ascii")


class FakeWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


class ClosedFakeWebSocket(FakeWebSocket):
    async def send_json(self, payload: dict[str, object]) -> None:
        del payload
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class HangingFakeWebSocket(FakeWebSocket):
    async def send_json(self, payload: dict[str, object]) -> None:
        del payload
        await asyncio.Event().wait()


class RelayConnectionTests(unittest.IsolatedAsyncioTestCase):
    def test_bidirectional_envelopes_use_the_same_device_lock_order(self) -> None:
        self.assertEqual(
            relay_device_lock_order("mac-1", "android-1"),
            relay_device_lock_order("android-1", "mac-1"),
        )

    async def test_closed_websocket_is_not_reported_as_a_server_runtime_error(self) -> None:
        connection = RelayConnection(ClosedFakeWebSocket())  # type: ignore[arg-type]

        with self.assertRaises(RelayConnectionClosed):
            await connection.send({"type": "accepted"})

    async def test_hub_reports_only_devices_with_live_connections(self) -> None:
        hub = ConnectionHub()
        first = FakeWebSocket()
        second = FakeWebSocket()

        self.assertEqual(await hub.connected_device_ids(), set())
        await hub.add("mac-1", first)  # type: ignore[arg-type]
        await hub.add("android-1", second)  # type: ignore[arg-type]
        self.assertEqual(
            await hub.connected_device_ids(),
            {"mac-1", "android-1"},
        )
        await hub.remove("mac-1", first)  # type: ignore[arg-type]
        self.assertEqual(await hub.connected_device_ids(), {"android-1"})

    async def test_stale_socket_cannot_block_delivery_or_sender_acceptance(self) -> None:
        hub = ConnectionHub(send_timeout_seconds=0.1)
        hanging = HangingFakeWebSocket()
        healthy = FakeWebSocket()
        await hub.add("android-1", hanging)  # type: ignore[arg-type]
        await hub.add("android-1", healthy)  # type: ignore[arg-type]

        delivered = await asyncio.wait_for(
            hub.send(
                "android-1",
                {"type": "envelope", "message_id": "response-1"},
            ),
            timeout=0.5,
        )

        self.assertTrue(delivered)
        self.assertEqual(len(healthy.payloads), 1)
        self.assertEqual(await hub.connected_device_ids(), {"android-1"})
        await hub.remove("android-1", healthy)  # type: ignore[arg-type]
        self.assertEqual(await hub.connected_device_ids(), set())

    async def test_tracks_only_in_flight_and_recently_acknowledged_messages(self) -> None:
        websocket = FakeWebSocket()
        connection = RelayConnection(websocket)  # type: ignore[arg-type]

        envelope = {"type": "envelope", "message_id": "message-0"}
        self.assertTrue(await connection.send(envelope))
        self.assertFalse(await connection.send(envelope))
        await connection.acknowledge("message-0")
        self.assertFalse(await connection.send(envelope))

        for index in range(connection.MAX_RECENT_ACKNOWLEDGED_IDS + 1):
            message_id = f"message-{index + 1}"
            self.assertTrue(
                await connection.send(
                    {"type": "envelope", "message_id": message_id}
                )
            )
            await connection.acknowledge(message_id)

        self.assertEqual(connection.in_flight_ids, set())
        self.assertEqual(
            len(connection.recently_acknowledged_ids),
            connection.MAX_RECENT_ACKNOWLEDGED_IDS,
        )
        self.assertNotIn("message-0", connection.recently_acknowledged_ids)


class RelayServerTests(unittest.TestCase):
    def settings(self, database: Path) -> Settings:
        return Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database}",
            public_base_url="https://relay.test",
            jwt_secret="jwt-secret-" * 4,
            mac_enrollment_token="enrollment-secret-" * 3,
            allowed_wechat_subjects=frozenset({"unionid:approved-user"}),
        )

    def test_production_rejects_sqlite_and_plain_http(self) -> None:
        settings = Settings(
            environment="production",
            database_url="sqlite+aiosqlite:///relay.db",
            public_base_url="http://relay.test",
            jwt_secret="j" * 32,
            mac_enrollment_token="m" * 32,
        )
        with self.assertRaises(ConfigurationError):
            settings.validate()

    def test_pending_queue_quota_is_per_sender_and_released_by_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(
                self.settings(Path(tmp) / "relay.db"),
                max_pending_envelopes_per_device=2,
            )

            async def exercise() -> None:
                database = Database(settings.database_url)
                try:
                    await database.create_schema()
                    async with database.session_scope() as session:
                        session.add(
                            UserRecord(
                                id="user-1",
                                wechat_subject="test-user",
                                status="approved",
                            )
                        )
                        await session.flush()
                        session.add_all(
                            [
                                DeviceRecord(
                                    id="android-1",
                                    user_id="user-1",
                                    kind="android",
                                    name="Phone",
                                    public_key=public_key(b"a"),
                                    status="active",
                                ),
                                DeviceRecord(
                                    id="mac-1",
                                    user_id="user-1",
                                    kind="mac",
                                    name="Mac",
                                    public_key=public_key(b"m"),
                                    status="active",
                                ),
                            ]
                        )
                        await session.commit()

                    service = RelayService(settings, ConnectionHub())
                    sender = AccessClaims("user-1", "android-1", "android")
                    for index in range(2):
                        async with database.session_scope() as session:
                            await service.submit(
                                session,
                                sender,
                                {
                                    "message_id": f"message-{index}",
                                    "target_device_id": "mac-1",
                                    "sequence": index,
                                    "ciphertext": ciphertext(f"payload-{index}"),
                                },
                            )
                    async with database.session_scope() as session:
                        with self.assertRaisesRegex(
                            RelayProtocolError,
                            "offline queue is full",
                        ):
                            await service.submit(
                                session,
                                sender,
                                {
                                    "message_id": "message-2",
                                    "target_device_id": "mac-1",
                                    "sequence": 2,
                                    "ciphertext": ciphertext("payload-2"),
                                },
                            )
                    async with database.session_scope() as session:
                        await service.acknowledge(
                            session,
                            AccessClaims("user-1", "mac-1", "mac"),
                            "message-0",
                        )
                    async with database.session_scope() as session:
                        accepted = await service.submit(
                            session,
                            sender,
                            {
                                "message_id": "message-2",
                                "target_device_id": "mac-1",
                                "sequence": 2,
                                "ciphertext": ciphertext("payload-2"),
                            },
                        )
                    self.assertEqual(accepted["type"], "accepted")
                finally:
                    await database.close()

            asyncio.run(exercise())

    def test_committed_envelope_is_accepted_before_slow_live_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")

            async def exercise() -> None:
                database = Database(settings.database_url)
                try:
                    await database.create_schema()
                    async with database.session_scope() as session:
                        session.add(
                            UserRecord(
                                id="user-1",
                                wechat_subject="test-user",
                                status="approved",
                            )
                        )
                        await session.flush()
                        session.add_all(
                            [
                                DeviceRecord(
                                    id="android-1",
                                    user_id="user-1",
                                    kind="android",
                                    name="Phone",
                                    public_key=public_key(b"a"),
                                    status="active",
                                ),
                                DeviceRecord(
                                    id="mac-1",
                                    user_id="user-1",
                                    kind="mac",
                                    name="Mac",
                                    public_key=public_key(b"m"),
                                    status="active",
                                ),
                            ]
                        )
                        await session.commit()

                    hub = ConnectionHub(send_timeout_seconds=1.0)
                    hanging = HangingFakeWebSocket()
                    await hub.add("mac-1", hanging)  # type: ignore[arg-type]
                    service = RelayService(settings, hub)
                    async with database.session_scope() as session:
                        accepted = await asyncio.wait_for(
                            service.submit(
                                session,
                                AccessClaims("user-1", "android-1", "android"),
                                {
                                    "message_id": "message-slow-target",
                                    "target_device_id": "mac-1",
                                    "sequence": 1,
                                    "ciphertext": ciphertext("payload"),
                                },
                            ),
                            timeout=0.2,
                        )
                    self.assertEqual(accepted["type"], "accepted")
                finally:
                    await database.close()

            asyncio.run(exercise())

    def test_wechat_login_pairing_and_opaque_websocket_relay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                health = client.get("/healthz")
                self.assertEqual(health.status_code, 200)

                login = client.post(
                    "/v1/auth/wechat",
                    json={
                        "code": "valid-code",
                        "device_name": "Pixel",
                        "public_key": public_key(b"a"),
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)
                android_tokens = login.json()
                android_headers = {
                    "Authorization": f"Bearer {android_tokens['access_token']}"
                }

                refreshed = client.post(
                    "/v1/auth/refresh",
                    json={"refresh_token": android_tokens["refresh_token"]},
                )
                self.assertEqual(refreshed.status_code, 200, refreshed.text)
                self.assertEqual(
                    refreshed.json()["refresh_token"],
                    android_tokens["refresh_token"],
                )
                retried_refresh = client.post(
                    "/v1/auth/refresh",
                    json={"refresh_token": android_tokens["refresh_token"]},
                )
                self.assertEqual(retried_refresh.status_code, 200, retried_refresh.text)
                android_tokens = refreshed.json()
                android_headers = {
                    "Authorization": f"Bearer {android_tokens['access_token']}"
                }

                enroll = client.post(
                    "/v1/mac/enroll",
                    headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                    json={
                        "device_name": "Studio Mac",
                        "public_key": public_key(b"m"),
                        "pairing_commitment": base64.b64encode(
                            hashlib.sha256(b"s" * 32).digest()
                        ).decode("ascii"),
                        "approval_commitment": base64.b64encode(
                            hashlib.sha256(b"t" * 32).digest()
                        ).decode("ascii"),
                    },
                )
                self.assertEqual(enroll.status_code, 200, enroll.text)
                pairing = enroll.json()
                self.assertNotIn("pairing_secret", pairing)
                _commitment, proof = pairing_values(
                    base64.b64encode(b"s" * 32).decode("ascii"),
                    pairing_id=pairing["pairing_id"],
                    mac_device_id=pairing["mac_device_id"],
                    mac_public_key=public_key(b"m"),
                    android_device_id=android_tokens["device_id"],
                    android_public_key=public_key(b"a"),
                )

                approve = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/approve",
                    headers=android_headers,
                    json={
                        "pairing_proof": proof,
                        "approval_token": base64.b64encode(b"t" * 32).decode(
                            "ascii"
                        ),
                    },
                )
                self.assertEqual(approve.status_code, 204, approve.text)

                poll = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/poll",
                    json={"poll_token": pairing["poll_token"]},
                )
                self.assertEqual(poll.status_code, 200, poll.text)
                self.assertEqual(poll.json()["status"], "approved")
                self.assertEqual(
                    poll.json()["android_device_id"],
                    android_tokens["device_id"],
                )
                self.assertEqual(poll.json()["pairing_proof"], proof)
                mac_tokens = poll.json()["tokens"]
                mac_headers = {
                    "Authorization": f"Bearer {mac_tokens['access_token']}"
                }

                devices = client.get("/v1/devices", headers=android_headers)
                self.assertEqual(devices.status_code, 200, devices.text)
                self.assertEqual(len(devices.json()["devices"]), 2)

                replayed = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/poll",
                    json={"poll_token": pairing["poll_token"]},
                )
                self.assertEqual(replayed.status_code, 200, replayed.text)
                self.assertEqual(replayed.json()["status"], "approved")
                self.assertEqual(
                    replayed.json()["android_device_id"],
                    android_tokens["device_id"],
                )
                self.assertEqual(replayed.json()["pairing_proof"], proof)
                replayed_tokens = replayed.json()["tokens"]
                self.assertNotEqual(
                    replayed_tokens["refresh_token"],
                    mac_tokens["refresh_token"],
                )
                replayed_refresh = client.post(
                    "/v1/auth/refresh",
                    json={"refresh_token": replayed_tokens["refresh_token"]},
                )
                self.assertEqual(
                    replayed_refresh.status_code,
                    200,
                    replayed_refresh.text,
                )

                with client.websocket_connect(
                    "/v1/relay", headers=mac_headers
                ) as mac_socket:
                    with client.websocket_connect(
                        "/v1/relay", headers=android_headers
                    ) as android_socket:
                        android_socket.send_json(
                            {
                                "message_id": "invalid-target",
                                "target_device_id": android_tokens["device_id"],
                                "sequence": 0,
                                "ciphertext": ciphertext("invalid"),
                            }
                        )
                        rejected = android_socket.receive_json()
                        self.assertEqual(rejected["type"], "rejected")
                        self.assertEqual(rejected["message_id"], "invalid-target")
                        self.assertEqual(rejected["code"], "invalid_message")

                        android_socket.send_json(
                            {
                                "message_id": "message-1",
                                "target_device_id": pairing["mac_device_id"],
                                "sequence": 1,
                                "ciphertext": ciphertext("opaque-ciphertext"),
                            }
                        )
                        accepted = android_socket.receive_json()
                        delivered = mac_socket.receive_json()
                        self.assertEqual(accepted["type"], "accepted")
                        self.assertEqual(
                            delivered["ciphertext"],
                            ciphertext("opaque-ciphertext"),
                        )

                        mac_socket.send_json(
                            {"type": "ack", "message_id": "message-1"}
                        )
                        self.assertEqual(
                            mac_socket.receive_json()["type"],
                            "acknowledged",
                        )

                with client.websocket_connect(
                    "/v1/relay", headers=android_headers
                ) as android_socket:
                    for index in range(101):
                        android_socket.send_json(
                            {
                                "message_id": f"offline-{index:03d}",
                                "target_device_id": pairing["mac_device_id"],
                                "sequence": index + 2,
                                "ciphertext": ciphertext(f"ciphertext-{index:03d}"),
                            }
                        )
                        self.assertEqual(
                            android_socket.receive_json()["type"],
                            "accepted",
                        )

                with client.websocket_connect(
                    "/v1/relay", headers=mac_headers
                ) as mac_socket:
                    initial = [mac_socket.receive_json() for _ in range(100)]
                    self.assertEqual(initial[0]["message_id"], "offline-000")
                    self.assertEqual(initial[-1]["message_id"], "offline-099")
                    mac_socket.send_json(
                        {"type": "ack", "message_id": "offline-000"}
                    )
                    self.assertEqual(
                        mac_socket.receive_json()["type"],
                        "acknowledged",
                    )
                    continued = mac_socket.receive_json()
                    self.assertEqual(continued["message_id"], "offline-100")

                revoked = client.delete(
                    f"/v1/devices/{pairing['mac_device_id']}",
                    headers=android_headers,
                )
                self.assertEqual(revoked.status_code, 204, revoked.text)
                rejected = client.get("/v1/devices", headers=mac_headers)
                self.assertEqual(rejected.status_code, 401, rejected.text)

    def test_pairing_code_authenticates_android_without_wechat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                pairing_secret = base64.b64encode(b"s" * 32).decode("ascii")
                approval_token = base64.b64encode(b"t" * 32).decode("ascii")
                enroll = client.post(
                    "/v1/mac/enroll",
                    headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                    json={
                        "device_name": "Studio Mac",
                        "public_key": public_key(b"m"),
                        "pairing_commitment": base64.b64encode(
                            hashlib.sha256(b"s" * 32).digest()
                        ).decode("ascii"),
                        "approval_commitment": base64.b64encode(
                            hashlib.sha256(b"t" * 32).digest()
                        ).decode("ascii"),
                    },
                )
                self.assertEqual(enroll.status_code, 200, enroll.text)
                pairing = enroll.json()

                rejected = client.post(
                    "/v1/auth/pairing",
                    json={
                        "pairing_id": pairing["pairing_id"],
                        "approval_token": base64.b64encode(b"x" * 32).decode(
                            "ascii"
                        ),
                        "device_name": "Pixel",
                        "public_key": public_key(b"a"),
                    },
                )
                self.assertEqual(rejected.status_code, 401, rejected.text)

                login = client.post(
                    "/v1/auth/pairing",
                    json={
                        "pairing_id": pairing["pairing_id"],
                        "approval_token": approval_token,
                        "device_name": "Pixel",
                        "public_key": public_key(b"a"),
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)
                android_tokens = login.json()
                _commitment, proof = pairing_values(
                    pairing_secret,
                    pairing_id=pairing["pairing_id"],
                    mac_device_id=pairing["mac_device_id"],
                    mac_public_key=public_key(b"m"),
                    android_device_id=android_tokens["device_id"],
                    android_public_key=public_key(b"a"),
                )
                approve = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/approve",
                    headers={
                        "Authorization": f"Bearer {android_tokens['access_token']}"
                    },
                    json={
                        "pairing_proof": proof,
                        "approval_token": approval_token,
                    },
                )
                self.assertEqual(approve.status_code, 204, approve.text)

                poll = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/poll",
                    json={"poll_token": pairing["poll_token"]},
                )
                self.assertEqual(poll.status_code, 200, poll.text)
                self.assertEqual(poll.json()["status"], "approved")
                self.assertEqual(
                    poll.json()["android_device_id"],
                    android_tokens["device_id"],
                )

    def test_new_qr_repairs_an_android_login_without_the_old_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                original_identity = None
                for attempt in range(2):
                    secret = bytes([21 + attempt]) * 32
                    approval = bytes([31 + attempt]) * 32
                    enroll = client.post(
                        "/v1/mac/enroll",
                        headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                        json={
                            "device_name": "Recovery QA Mac",
                            "public_key": public_key(b"m"),
                            "pairing_commitment": base64.b64encode(hashlib.sha256(secret).digest()).decode(),
                            "approval_commitment": base64.b64encode(hashlib.sha256(approval).digest()).decode(),
                        },
                    )
                    self.assertEqual(enroll.status_code, 200, enroll.text)
                    pairing = enroll.json()
                    login_body = {
                        "pairing_id": pairing["pairing_id"],
                        "approval_token": base64.b64encode(approval).decode(),
                        "device_name": "Recovery QA Phone",
                        "public_key": public_key(b"a"),
                    }
                    if attempt:
                        rejected = client.post(
                            "/v1/auth/pairing", json=login_body,
                            headers={"Authorization": "Bearer expired-test-session"},
                        )
                        self.assertEqual(rejected.status_code, 401, rejected.text)
                        wrong_code = client.post(
                            "/v1/auth/pairing",
                            json={**login_body, "approval_token": base64.b64encode(b"x" * 32).decode()},
                        )
                        self.assertEqual(wrong_code.status_code, 401, wrong_code.text)

                    login = client.post("/v1/auth/pairing", json=login_body)
                    self.assertEqual(login.status_code, 200, login.text)
                    issued = login.json()
                    identity = (issued["user_id"], issued["device_id"])
                    if original_identity is None:
                        original_identity = identity
                    self.assertEqual(identity, original_identity)
                    _, proof = pairing_values(
                        base64.b64encode(secret).decode(),
                        pairing_id=pairing["pairing_id"],
                        mac_device_id=pairing["mac_device_id"],
                        mac_public_key=public_key(b"m"),
                        android_device_id=issued["device_id"],
                        android_public_key=public_key(b"a"),
                    )
                    headers = {"Authorization": f"Bearer {issued['access_token']}"}
                    approved = client.post(
                        f"/v1/pairings/{pairing['pairing_id']}/approve", headers=headers,
                        json={"pairing_proof": proof, "approval_token": login_body["approval_token"]},
                    )
                    self.assertEqual(approved.status_code, 204, approved.text)
                    polled = client.post(
                        f"/v1/pairings/{pairing['pairing_id']}/poll",
                        json={"poll_token": pairing["poll_token"]},
                    )
                    self.assertEqual(polled.status_code, 200, polled.text)
                    self.assertEqual(polled.json()["status"], "approved")
                    devices = client.get("/v1/devices", headers=headers)
                    self.assertEqual(devices.status_code, 200, devices.text)
                    self.assertEqual(
                        [device["device_id"] for device in devices.json()["devices"] if device["kind"] == "android"],
                        [issued["device_id"]],
                    )

    def test_one_mac_can_be_controlled_by_multiple_android_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                mac_public_key = public_key(b"m")

                def pair_phone(
                    phone_key: bytes,
                    secret_seed: bytes,
                    approval_seed: bytes,
                ) -> tuple[dict[str, str], dict[str, str]]:
                    secret = base64.b64encode(secret_seed * 32).decode("ascii")
                    approval = base64.b64encode(approval_seed * 32).decode("ascii")
                    enroll = client.post(
                        "/v1/mac/enroll",
                        headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                        json={
                            "device_name": "Studio Mac",
                            "public_key": mac_public_key,
                            "pairing_commitment": base64.b64encode(
                                hashlib.sha256(secret_seed * 32).digest()
                            ).decode("ascii"),
                            "approval_commitment": base64.b64encode(
                                hashlib.sha256(approval_seed * 32).digest()
                            ).decode("ascii"),
                        },
                    )
                    self.assertEqual(enroll.status_code, 200, enroll.text)
                    pairing = enroll.json()
                    login = client.post(
                        "/v1/auth/pairing",
                        json={
                            "pairing_id": pairing["pairing_id"],
                            "approval_token": approval,
                            "device_name": f"Phone {phone_key.decode('ascii')}",
                            "public_key": public_key(phone_key),
                        },
                    )
                    self.assertEqual(login.status_code, 200, login.text)
                    phone_tokens = login.json()
                    _commitment, proof = pairing_values(
                        secret,
                        pairing_id=pairing["pairing_id"],
                        mac_device_id=pairing["mac_device_id"],
                        mac_public_key=mac_public_key,
                        android_device_id=phone_tokens["device_id"],
                        android_public_key=public_key(phone_key),
                    )
                    approve = client.post(
                        f"/v1/pairings/{pairing['pairing_id']}/approve",
                        headers={
                            "Authorization": f"Bearer {phone_tokens['access_token']}"
                        },
                        json={
                            "pairing_proof": proof,
                            "approval_token": approval,
                        },
                    )
                    self.assertEqual(approve.status_code, 204, approve.text)
                    poll = client.post(
                        f"/v1/pairings/{pairing['pairing_id']}/poll",
                        json={"poll_token": pairing["poll_token"]},
                    )
                    self.assertEqual(poll.status_code, 200, poll.text)
                    return phone_tokens, poll.json()["tokens"]

                first_phone, _first_mac = pair_phone(b"a", b"s", b"t")
                second_phone, current_mac = pair_phone(b"b", b"u", b"v")

                self.assertNotEqual(first_phone["device_id"], second_phone["device_id"])
                self.assertEqual(first_phone["user_id"], second_phone["user_id"])
                first_headers = {
                    "Authorization": f"Bearer {first_phone['access_token']}"
                }
                second_headers = {
                    "Authorization": f"Bearer {second_phone['access_token']}"
                }
                devices = client.get("/v1/devices", headers=first_headers)
                self.assertEqual(devices.status_code, 200, devices.text)
                self.assertEqual(
                    {
                        item["device_id"]
                        for item in devices.json()["devices"]
                        if item["kind"] == "android"
                    },
                    {first_phone["device_id"], second_phone["device_id"]},
                )
                self.assertEqual(
                    client.get("/v1/devices", headers=second_headers).status_code,
                    200,
                )

                mac_headers = {
                    "Authorization": f"Bearer {current_mac['access_token']}"
                }
                mac_id = current_mac["device_id"]
                with client.websocket_connect("/v1/relay", headers=mac_headers) as mac_socket:
                    with client.websocket_connect(
                        "/v1/relay", headers=first_headers
                    ) as first_socket:
                        first_socket.send_json(
                            {
                                "message_id": "first-controller",
                                "target_device_id": mac_id,
                                "sequence": 1,
                                "ciphertext": ciphertext("first"),
                            }
                        )
                        self.assertEqual(first_socket.receive_json()["type"], "accepted")
                        self.assertEqual(
                            mac_socket.receive_json()["message_id"],
                            "first-controller",
                        )
                    with client.websocket_connect(
                        "/v1/relay", headers=second_headers
                    ) as second_socket:
                        second_socket.send_json(
                            {
                                "message_id": "second-controller",
                                "target_device_id": mac_id,
                                "sequence": 1,
                                "ciphertext": ciphertext("second"),
                            }
                        )
                        self.assertEqual(second_socket.receive_json()["type"], "accepted")
                        self.assertEqual(
                            mac_socket.receive_json()["message_id"],
                            "second-controller",
                        )

    def test_authenticated_android_can_add_a_second_mac_to_the_same_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                android_public_key = public_key(b"a")

                def enroll_and_pair(
                    mac_name: str,
                    mac_key_seed: bytes,
                    secret_seed: bytes,
                    approval_seed: bytes,
                    headers: dict[str, str] | None = None,
                ) -> tuple[dict[str, str], dict[str, str]]:
                    secret = base64.b64encode(secret_seed * 32).decode("ascii")
                    approval = base64.b64encode(approval_seed * 32).decode("ascii")
                    mac_public_key = public_key(mac_key_seed)
                    enroll = client.post(
                        "/v1/mac/enroll",
                        headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                        json={
                            "device_name": mac_name,
                            "public_key": mac_public_key,
                            "pairing_commitment": base64.b64encode(
                                hashlib.sha256(secret_seed * 32).digest()
                            ).decode("ascii"),
                            "approval_commitment": base64.b64encode(
                                hashlib.sha256(approval_seed * 32).digest()
                            ).decode("ascii"),
                        },
                    )
                    self.assertEqual(enroll.status_code, 200, enroll.text)
                    pairing = enroll.json()
                    login = client.post(
                        "/v1/auth/pairing",
                        headers=headers,
                        json={
                            "pairing_id": pairing["pairing_id"],
                            "approval_token": approval,
                            "device_name": "Pixel",
                            "public_key": android_public_key,
                        },
                    )
                    self.assertEqual(login.status_code, 200, login.text)
                    issued = login.json()
                    _commitment, proof = pairing_values(
                        secret,
                        pairing_id=pairing["pairing_id"],
                        mac_device_id=pairing["mac_device_id"],
                        mac_public_key=mac_public_key,
                        android_device_id=issued["device_id"],
                        android_public_key=android_public_key,
                    )
                    approve = client.post(
                        f"/v1/pairings/{pairing['pairing_id']}/approve",
                        headers={
                            "Authorization": f"Bearer {issued['access_token']}"
                        },
                        json={
                            "pairing_proof": proof,
                            "approval_token": approval,
                        },
                    )
                    self.assertEqual(approve.status_code, 204, approve.text)
                    return pairing, issued

                first_pairing, first_tokens = enroll_and_pair(
                    "Studio Mac",
                    b"m",
                    b"s",
                    b"t",
                )
                second_pairing, second_tokens = enroll_and_pair(
                    "MacBook Pro",
                    b"n",
                    b"u",
                    b"v",
                    headers={
                        "Authorization": f"Bearer {first_tokens['access_token']}"
                    },
                )

                self.assertEqual(second_tokens["user_id"], first_tokens["user_id"])
                self.assertEqual(second_tokens["device_id"], first_tokens["device_id"])
                devices = client.get(
                    "/v1/devices",
                    headers={
                        "Authorization": f"Bearer {second_tokens['access_token']}"
                    },
                )
                self.assertEqual(devices.status_code, 200, devices.text)
                mac_ids = {
                    device["device_id"]
                    for device in devices.json()["devices"]
                    if device["kind"] == "mac"
                }
                self.assertEqual(
                    mac_ids,
                    {
                        first_pairing["mac_device_id"],
                        second_pairing["mac_device_id"],
                    },
                )

    def test_macos_controller_can_pair_and_open_the_relay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                pairing_secret = base64.b64encode(b"s" * 32).decode("ascii")
                approval_token = base64.b64encode(b"t" * 32).decode("ascii")
                enroll = client.post(
                    "/v1/mac/enroll",
                    headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                    json={
                        "device_name": "Studio Mac",
                        "public_key": public_key(b"m"),
                        "pairing_commitment": base64.b64encode(
                            hashlib.sha256(b"s" * 32).digest()
                        ).decode("ascii"),
                        "approval_commitment": base64.b64encode(
                            hashlib.sha256(b"t" * 32).digest()
                        ).decode("ascii"),
                    },
                )
                self.assertEqual(enroll.status_code, 200, enroll.text)
                pairing = enroll.json()
                login = client.post(
                    "/v1/auth/pairing",
                    json={
                        "pairing_id": pairing["pairing_id"],
                        "approval_token": approval_token,
                        "device_name": "Parallel Codex on Mac",
                        "device_kind": "macos",
                        "public_key": public_key(b"c"),
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)
                controller_tokens = login.json()
                self.assertTrue(controller_tokens["device_id"].startswith("macos-"))
                _commitment, proof = pairing_values(
                    pairing_secret,
                    pairing_id=pairing["pairing_id"],
                    mac_device_id=pairing["mac_device_id"],
                    mac_public_key=public_key(b"m"),
                    android_device_id=controller_tokens["device_id"],
                    android_public_key=public_key(b"c"),
                )
                approve = client.post(
                    f"/v1/pairings/{pairing['pairing_id']}/approve",
                    headers={
                        "Authorization": f"Bearer {controller_tokens['access_token']}"
                    },
                    json={
                        "pairing_proof": proof,
                        "approval_token": approval_token,
                    },
                )
                self.assertEqual(approve.status_code, 204, approve.text)

                with client.websocket_connect(
                    "/v1/relay",
                    headers={
                        "Authorization": f"Bearer {controller_tokens['access_token']}"
                    },
                ) as controller_socket:
                    controller_socket.send_json(
                        {
                            "message_id": "controller-message",
                            "target_device_id": pairing["mac_device_id"],
                            "sequence": 1,
                            "ciphertext": ciphertext("opaque"),
                        }
                    )
                    self.assertEqual(
                        controller_socket.receive_json()["type"],
                        "accepted",
                    )

                with client.websocket_connect(
                    "/v1/relay",
                    headers={
                        "Authorization": f"Bearer {controller_tokens['access_token']}"
                    },
                ) as controller_socket:
                    controller_socket.send_bytes(
                        json.dumps(
                            {
                                "message_id": "legacy-binary-message",
                                "target_device_id": pairing["mac_device_id"],
                                "sequence": 2,
                                "ciphertext": ciphertext("opaque"),
                            }
                        ).encode("utf-8")
                    )
                    self.assertEqual(
                        controller_socket.receive_json()["type"],
                        "accepted",
                    )

    def test_reenrollment_reactivates_a_revoked_mac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(Path(tmp) / "relay.db")
            app = create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            with TestClient(app) as client:
                mac_public_key = public_key(b"m")

                def enroll() -> dict[str, str]:
                    response = client.post(
                        "/v1/mac/enroll",
                        headers={"X-PCR-Enrollment": settings.mac_enrollment_token},
                        json={
                            "device_name": "Studio Mac",
                            "public_key": mac_public_key,
                            "pairing_commitment": base64.b64encode(
                                hashlib.sha256(b"s" * 32).digest()
                            ).decode("ascii"),
                            "approval_commitment": base64.b64encode(
                                hashlib.sha256(b"t" * 32).digest()
                            ).decode("ascii"),
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()

                original = enroll()

                async def revoke_mac() -> None:
                    async with app.state.database.sessions() as session:
                        mac = await session.get(DeviceRecord, original["mac_device_id"])
                        assert mac is not None
                        mac.status = "revoked"
                        await session.commit()

                asyncio.run(revoke_mac())
                replacement = enroll()
                self.assertEqual(
                    replacement["mac_device_id"],
                    original["mac_device_id"],
                )

                login = client.post(
                    "/v1/auth/pairing",
                    json={
                        "pairing_id": replacement["pairing_id"],
                        "approval_token": base64.b64encode(b"t" * 32).decode(
                            "ascii"
                        ),
                        "device_name": "Pixel",
                        "public_key": public_key(b"a"),
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)

    def test_pending_user_requires_admin_approval_and_can_be_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "relay.db"
            settings = replace(
                self.settings(database),
                allowed_wechat_subjects=frozenset(),
            )
            login_body = {
                "code": "valid-code",
                "device_name": "Pixel",
                "public_key": public_key(b"a"),
            }

            with TestClient(
                create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            ) as client:
                pending = client.post("/v1/auth/wechat", json=login_body)
                self.assertEqual(pending.status_code, 401, pending.text)
                self.assertIn("awaiting approval", pending.json()["detail"])

            users = asyncio.run(list_users(settings))
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0]["status"], "pending")
            user_id = users[0]["user_id"]

            approved = asyncio.run(
                set_user_status(settings, user_id, "approved")
            )
            self.assertEqual(approved["status"], "approved")

            with TestClient(
                create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            ) as client:
                login = client.post("/v1/auth/wechat", json=login_body)
                self.assertEqual(login.status_code, 200, login.text)
                tokens = login.json()
                access_headers = {
                    "Authorization": f"Bearer {tokens['access_token']}"
                }

            revoked = asyncio.run(set_user_status(settings, user_id, "revoked"))
            self.assertEqual(revoked["status"], "revoked")

            with TestClient(
                create_app(settings, wechat_client=FakeWeChatClient())  # type: ignore[arg-type]
            ) as client:
                rejected = client.get("/v1/devices", headers=access_headers)
                self.assertEqual(rejected.status_code, 401, rejected.text)
                refresh = client.post(
                    "/v1/auth/refresh",
                    json={"refresh_token": tokens["refresh_token"]},
                )
                self.assertEqual(refresh.status_code, 401, refresh.text)


if __name__ == "__main__":
    unittest.main()
