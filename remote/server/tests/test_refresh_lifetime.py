import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select

from pcr_relay.auth import AuthenticationError, TokenService
from pcr_relay.config import Settings
from pcr_relay.db import Database, DeviceRecord, RefreshTokenRecord, UserRecord
from pcr_relay.service import IdentityService


class RefreshLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            environment="development", public_base_url="https://relay.test",
            database_url=f"sqlite+aiosqlite:///{Path(self.temporary.name) / 'relay.db'}",
            jwt_secret="test-refresh-secret-that-is-not-a-real-credential",
            mac_enrollment_token="test-enrollment-token-that-is-not-a-real-credential",
        )
        self.database = Database(self.settings.database_url)
        await self.database.create_schema()
        self.tokens = TokenService(self.settings)
        self.identity = IdentityService(self.settings, self.tokens, mock.Mock())
        async with self.database.session_scope() as session:
            session.add(UserRecord(id="user-test", wechat_subject="test", status="approved"))
            await session.flush()
            device = DeviceRecord(id="macos-test", user_id="user-test", kind="macos",
                                  name="Test Mac", public_key="test-key", status="active")
            session.add(device)
            await session.flush()
            issued = await self.identity._issue_tokens(session, "user-test", device)
            await session.commit()
            self.token = issued.refresh_token
        self.original_expiry = await self.expiry()

    async def asyncTearDown(self):
        await self.database.close()
        self.temporary.cleanup()

    async def expiry(self):
        async with self.database.session_scope() as session:
            record = await session.scalar(select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_digest == self.tokens.digest(self.token)
            ))
            return record.expires_at.replace(tzinfo=dt.timezone.utc)

    async def refresh_at(self, now):
        with mock.patch("pcr_relay.service.utc_now", return_value=now):
            async with self.database.session_scope() as session:
                return await self.identity.refresh(session, self.token)

    async def test_active_session_survives_its_original_thirty_day_deadline(self):
        near_expiry = self.original_expiry - dt.timedelta(days=1)
        response = await self.refresh_at(near_expiry)
        self.assertEqual(response.refresh_token, self.token)
        self.assertEqual(await self.expiry(), near_expiry + dt.timedelta(days=30))
        response = await self.refresh_at(self.original_expiry + dt.timedelta(days=1))
        self.assertEqual(response.device_id, "macos-test")

    async def test_lost_response_can_retry_the_same_credential(self):
        now = self.original_expiry - dt.timedelta(hours=1)
        await self.refresh_at(now)
        response = await self.refresh_at(now + dt.timedelta(seconds=1))
        self.assertEqual(response.refresh_token, self.token)
        self.assertEqual(await self.expiry(), now + dt.timedelta(days=30, seconds=1))

    async def test_refresh_never_shortens_an_existing_deadline(self):
        now = self.original_expiry - dt.timedelta(days=1)
        await self.refresh_at(now)
        deadline = await self.expiry()
        await self.refresh_at(now - dt.timedelta(minutes=1))
        self.assertEqual(await self.expiry(), deadline)

    async def test_expired_token_cannot_extend_itself(self):
        with self.assertRaises(AuthenticationError):
            await self.refresh_at(self.original_expiry)
        self.assertEqual(await self.expiry(), self.original_expiry)

    async def test_revoked_token_cannot_extend_itself(self):
        async with self.database.session_scope() as session:
            record = await session.scalar(select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_digest == self.tokens.digest(self.token)
            ))
            record.revoked = True
            await session.commit()
        with self.assertRaises(AuthenticationError):
            await self.refresh_at(self.original_expiry - dt.timedelta(days=1))
        self.assertEqual(await self.expiry(), self.original_expiry)

    async def test_inactive_device_or_account_cannot_extend_the_deadline(self):
        for device_status, user_status in (("revoked", "approved"), ("active", "revoked")):
            with self.subTest(device_status=device_status, user_status=user_status):
                async with self.database.session_scope() as session:
                    device = await session.get(DeviceRecord, "macos-test")
                    user = await session.get(UserRecord, "user-test")
                    device.status, user.status = device_status, user_status
                    await session.commit()
                with self.assertRaises(AuthenticationError):
                    await self.refresh_at(self.original_expiry - dt.timedelta(days=1))
                self.assertEqual(await self.expiry(), self.original_expiry)
