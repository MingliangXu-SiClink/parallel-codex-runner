import asyncio
import base64
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from sqlalchemy import func, select

from pcr_relay.auth import AccessClaims, AuthenticationError, TokenService
from pcr_relay.app import create_app
from pcr_relay.config import Settings
from pcr_relay.db import Database, DeviceLoginChallengeRecord, DeviceRecord, RefreshTokenRecord, UserRecord, utc_now
from pcr_relay.device_login import challenge_proof
from pcr_relay.schemas import DeviceLoginChallengeResponse, DeviceLoginRequest
from pcr_relay.service import DeviceLoginRateLimited, IdentityService, PairingService


class DeviceLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            environment="development", public_base_url="https://relay.test",
            database_url=f"sqlite+aiosqlite:///{Path(self.temporary.name) / 'relay.db'}",
            jwt_secret="test-device-login-secret-not-a-real-credential",
            mac_enrollment_token="test-enrollment-secret-not-a-real-credential",
        )
        self.database = Database(self.settings.database_url)
        await self.database.create_schema()
        self.tokens = TokenService(self.settings)
        self.identity = IdentityService(self.settings, self.tokens, mock.Mock())
        self.keys = {kind: X25519PrivateKey.generate() for kind in ("mac", "macos", "android")}
        async with self.database.session_scope() as session:
            session.add(UserRecord(id="user-test", wechat_subject="test", status="approved"))
            await session.flush()
            for kind, key in self.keys.items():
                public = base64.b64encode(key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw,
                )).decode("ascii")
                session.add(DeviceRecord(id=f"{kind}-test", user_id="user-test", kind=kind,
                                         name="Test device", public_key=public, status="active"))
            await session.commit()

    async def asyncTearDown(self):
        await self.database.close()
        self.temporary.cleanup()

    async def challenge(self, kind="macos"):
        async with self.database.session_scope() as session:
            return await self.identity.device_challenge(session, f"{kind}-test")

    def request(self, challenge, key=None):
        kind = challenge.device_id.split("-")[0]
        shared = (key or self.keys[kind]).exchange(X25519PublicKey.from_public_bytes(
            base64.b64decode(challenge.server_public_key)
        ))
        return DeviceLoginRequest(device_id=challenge.device_id, challenge_id=challenge.challenge_id,
                                  proof=challenge_proof(shared, challenge))

    async def login(self, challenge, key=None):
        async with self.database.session_scope() as session:
            return await self.identity.authenticate_device(session, self.request(challenge, key))

    async def count_tokens(self):
        async with self.database.session_scope() as session:
            return await session.scalar(select(func.count()).select_from(RefreshTokenRecord))

    async def test_all_device_kinds_recover_after_months_and_expired_rows_are_pruned(self):
        origin = utc_now()
        for days in (31, 90, 180):
            now = origin + dt.timedelta(days=days)
            with mock.patch("pcr_relay.service.utc_now", return_value=now), mock.patch("pcr_relay.db.utc_now", return_value=now):
                await self.database.cleanup_expired()
                self.assertEqual(await self.count_tokens(), 0)
                for kind in self.keys:
                    challenge = await self.challenge(kind)
                    result = await self.login(challenge)
                    self.assertEqual(result.device_id, f"{kind}-test")
                    self.assertEqual(self.tokens.decode_access(result.access_token).user_id, "user-test")
                self.assertEqual(await self.count_tokens(), 3)

    async def test_public_cross_platform_vector(self):
        vector = json.loads((Path(__file__).resolve().parents[2] / "protocol/device-login-v1-vector.json").read_text())
        challenge = DeviceLoginChallengeResponse(**vector["challenge"])
        private = X25519PrivateKey.from_private_bytes(base64.b64decode(vector["server_private_key"]))
        shared = private.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(challenge.public_key)))
        self.assertEqual(challenge_proof(shared, challenge), vector["proof"])

    async def test_challenge_is_single_use(self):
        challenge = await self.challenge()
        await self.login(challenge)
        with self.assertRaises(AuthenticationError) as raised:
            await self.login(challenge)
        self.assertEqual(raised.exception.code, "challenge_used")
        self.assertEqual(await self.count_tokens(), 1)

    async def test_concurrent_submissions_issue_only_one_session(self):
        challenge = await self.challenge()
        results = await asyncio.gather(
            *(self.login(challenge) for _ in range(3)), return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertTrue(all(
            not isinstance(result, Exception) or isinstance(result, AuthenticationError)
            for result in results
        ), results)
        self.assertEqual(await self.count_tokens(), 1)

    async def test_failed_issuance_rolls_back_challenge_consumption(self):
        challenge = await self.challenge()
        with mock.patch.object(self.identity, "_issue_tokens", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                await self.login(challenge)
        self.assertEqual(await self.count_tokens(), 0)
        await self.login(challenge)

    async def test_verification_then_revocation_invalidates_just_issued_credentials(self):
        if self.database.engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")
        challenge = await self.challenge()
        locked, release = asyncio.Event(), asyncio.Event()
        original = self.identity._issue_tokens

        async def issue(*args):
            locked.set()
            await release.wait()
            return await original(*args)

        async def revoke():
            async with self.database.session_scope() as session:
                await PairingService(self.settings, self.tokens, self.identity).revoke_device(
                    session, AccessClaims("user-test", "macos-test", "macos"), "macos-test",
                )

        with mock.patch.object(self.identity, "_issue_tokens", side_effect=issue):
            verification = asyncio.create_task(self.login(challenge))
            await asyncio.wait_for(locked.wait(), timeout=5)
            revocation = asyncio.create_task(revoke())
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(revocation.done())
            finally:
                release.set()
            result, _ = await asyncio.wait_for(asyncio.gather(verification, revocation), timeout=5)
        async with self.database.session_scope() as session:
            with self.assertRaises(AuthenticationError):
                await self.identity.refresh(session, result.refresh_token)

    async def test_revocation_then_verification_cannot_issue_any_credentials(self):
        if self.database.engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")
        challenge = await self.challenge()
        locked, release = asyncio.Event(), asyncio.Event()

        async def revoke():
            async with self.database.session_scope() as session:
                device = await self.identity._trusted_device(session, "macos-test")
                device.status = "revoked"
                device.authorization_generation += 1
                locked.set()
                await release.wait()
                await session.commit()

        revocation = asyncio.create_task(revoke())
        await asyncio.wait_for(locked.wait(), timeout=5)
        verification = asyncio.create_task(self.login(challenge))
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(verification.done())
        finally:
            release.set()
        await asyncio.wait_for(revocation, timeout=5)
        with self.assertRaises(AuthenticationError):
            await asyncio.wait_for(verification, timeout=5)
        self.assertEqual(await self.count_tokens(), 0)

    async def test_http_bootstrap_and_legacy_access_respect_authorization_generation(self):
        app = create_app(self.settings)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=self.settings.public_base_url) as client:
                expired = await client.post("/v1/auth/refresh", json={"refresh_token": "missing-public-test-token-0123456789"})
                self.assertEqual(expired.status_code, 401)
                self.assertEqual(expired.json()["code"], "refresh_invalid")
                issued = await client.post("/v1/auth/device/challenge", json={"device_id": "macos-test"})
                self.assertEqual(issued.status_code, 200)
                self.assertEqual(issued.headers["cache-control"], "no-store")
                challenge = DeviceLoginChallengeResponse(**issued.json())
                logged_in = await client.post("/v1/auth/device", json=self.request(challenge).model_dump())
                self.assertEqual(logged_in.status_code, 200)
                self.assertEqual(logged_in.headers["cache-control"], "no-store")
                access = logged_in.json()["access_token"]
                payload = jwt.decode(access, self.settings.jwt_secret, algorithms=["HS256"], issuer=self.tokens.ISSUER)
                payload.pop("authorization_generation")
                legacy_access = jwt.encode(payload, self.settings.jwt_secret, algorithm="HS256")
                for token in (access, legacy_access):
                    self.assertEqual((await client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})).status_code, 200)
                await client.delete("/v1/devices/macos-test", headers={"Authorization": f"Bearer {access}"})
                async with self.database.session_scope() as session:
                    device = await session.get(DeviceRecord, "macos-test")
                    device.status = "active"
                    await session.commit()
                for token in (access, legacy_access):
                    self.assertEqual((await client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})).status_code, 401)
                current = await self.login(await self.challenge())
                self.assertEqual((await client.get("/v1/devices", headers={"Authorization": f"Bearer {current.access_token}"})).status_code, 200)
        finally:
            await app.state.database.close()

    async def test_wrong_proof_invalidates_challenge_without_issuing_tokens(self):
        challenge = await self.challenge()
        with self.assertRaises(AuthenticationError):
            await self.login(challenge, X25519PrivateKey.generate())
        self.assertEqual(await self.count_tokens(), 0)
        with self.assertRaises(AuthenticationError) as raised:
            await self.login(challenge)
        self.assertEqual(raised.exception.code, "challenge_used")
        await self.login(await self.challenge())

    async def test_challenge_expiry_is_enforced(self):
        challenge = await self.challenge()
        expiry = dt.datetime.fromisoformat(challenge.expires_at)
        with mock.patch("pcr_relay.service.utc_now", return_value=expiry):
            with self.assertRaises(AuthenticationError) as raised:
                await self.login(challenge)
        self.assertEqual(raised.exception.code, "challenge_expired")
        self.assertEqual(await self.count_tokens(), 0)

    async def test_revocation_prevents_new_and_already_issued_challenges(self):
        challenge = await self.challenge()
        async with self.database.session_scope() as session:
            await PairingService(self.settings, self.tokens, self.identity).revoke_device(
                session, AccessClaims("user-test", "macos-test", "macos"), "macos-test",
            )
        for action in (self.challenge, lambda: self.login(challenge)):
            with self.assertRaises(AuthenticationError):
                await action()
        self.assertEqual(await self.count_tokens(), 0)

    async def test_old_challenge_cannot_survive_revoke_then_reauthorization(self):
        challenge = await self.challenge()
        async with self.database.session_scope() as session:
            pairing = PairingService(self.settings, self.tokens, self.identity)
            await pairing.revoke_device(session, AccessClaims("user-test", "macos-test", "macos"), "macos-test")
            device = await session.get(DeviceRecord, "macos-test")
            device.status = "active"
            await session.commit()
        with self.assertRaises(AuthenticationError) as raised:
            await self.login(challenge)
        self.assertEqual(raised.exception.code, "device_authorization_changed")
        result = await self.login(await self.challenge())
        self.assertEqual(self.tokens.decode_access(result.access_token).authorization_generation, 1)

    async def test_disabled_account_cannot_issue_or_complete_a_challenge(self):
        challenge = await self.challenge()
        async with self.database.session_scope() as session:
            user = await session.get(UserRecord, "user-test")
            user.status = "revoked"
            await session.commit()
        with self.assertRaises(AuthenticationError):
            await self.challenge()
        with self.assertRaises(AuthenticationError):
            await self.login(challenge)

    async def test_key_or_account_change_invalidates_challenge(self):
        challenge = await self.challenge()
        async with self.database.session_scope() as session:
            device = await session.get(DeviceRecord, "macos-test")
            device.public_key = base64.b64encode(b"x" * 32).decode()
            await session.commit()
        with self.assertRaises(AuthenticationError):
            await self.login(challenge)

    async def test_replay_to_another_device_is_rejected(self):
        challenge = await self.challenge()
        request = self.request(challenge).model_copy(update={"device_id": "android-test"})
        async with self.database.session_scope() as session:
            with self.assertRaises(AuthenticationError):
                await self.identity.authenticate_device(session, request)

    async def test_unauthorized_or_low_order_device_cannot_get_challenge(self):
        async with self.database.session_scope() as session:
            device = await session.get(DeviceRecord, "macos-test")
            device.public_key = base64.b64encode(bytes(32)).decode()
            await session.commit()
        with self.assertRaises(AuthenticationError):
            await self.challenge()
        async with self.database.session_scope() as session:
            with self.assertRaises(AuthenticationError):
                await self.identity.device_challenge(session, "unknown")

    async def test_pending_challenges_are_bounded_without_invalidating_concurrent_clients(self):
        challenges = [await self.challenge() for _ in range(8)]
        with self.assertRaises(DeviceLoginRateLimited):
            await self.challenge()
        await self.login(challenges[0])
        await self.login(challenges[1])
        self.assertEqual(await self.count_tokens(), 2)
        later = dt.datetime.fromisoformat(challenges[0].expires_at) + dt.timedelta(seconds=1)
        with mock.patch("pcr_relay.service.utc_now", return_value=later):
            await self.challenge()

    async def test_lost_success_response_can_obtain_a_new_challenge_without_pairing(self):
        await self.login(await self.challenge())
        response = await self.login(await self.challenge())
        self.assertEqual(response.device_id, "macos-test")
        self.assertEqual(await self.count_tokens(), 2)

    async def test_transcript_changes_cannot_be_reused(self):
        challenge = await self.challenge()
        for name, value in (("relay_url", "https://elsewhere.test"), ("purpose", "other-purpose"),
                            ("authorization_generation", 99), ("nonce", base64.b64encode(b"n" * 32).decode())):
            altered = challenge.model_copy(update={name: value})
            with self.assertRaises(AuthenticationError):
                await self.login(altered)
        self.assertEqual(await self.count_tokens(), 0)
