from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from pcr_relay.app import create_app
from pcr_relay.auth import AccessClaims, TokenService
from pcr_relay.config import Settings
from pcr_relay.db import Database, DeviceRecord, UserRecord, utc_now
from pcr_relay.macos_updates import publish_macos_release


class MacOSUpdateTests(unittest.TestCase):
    def settings(self, root: Path) -> Settings:
        return Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{root / 'relay.db'}",
            public_base_url="https://relay.test",
            jwt_secret="jwt-secret-" * 4,
            mac_enrollment_token="enrollment-secret-" * 3,
            macos_update_dir=str(root / "private-macos-releases"),
        )

    def test_paired_macos_controller_can_download_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)
            archive = root / "Parallel-Codex.zip"
            archive.write_bytes(b"signed-macos-archive")
            release = asyncio.run(
                publish_macos_release(
                    settings,
                    archive_path=str(archive),
                    build_number=161,
                    version_name="0.1.161",
                    release_notes="Adds private in-app update downloads.",
                    designated_requirement=(
                        'identifier "com.parallelcodexrunner.mac" and anchor apple generic'
                    ),
                    signing_authority="Parallel Codex Local Signing",
                )
            )
            self.assertNotIn(str(root), str(release))

            async def seed() -> None:
                database = Database(settings.database_url)
                await database.create_schema()
                async with database.session_scope() as session:
                    now = utc_now()
                    session.add(
                        UserRecord(
                            id="user-1",
                            wechat_subject="test-user",
                            status="approved",
                            created_at=now,
                        )
                    )
                    await session.commit()
                    session.add_all(
                        [
                            DeviceRecord(
                                id="mac-1",
                                user_id="user-1",
                                kind="mac",
                                name="Mac",
                                public_key="m" * 44,
                                status="active",
                                created_at=now,
                                last_seen_at=now,
                            ),
                            DeviceRecord(
                                id="macos-1",
                                user_id="user-1",
                                kind="macos",
                                name="Desktop",
                                public_key="c" * 44,
                                status="active",
                                created_at=now,
                                last_seen_at=now,
                            ),
                        ]
                    )
                    await session.commit()
                await database.close()

            asyncio.run(seed())
            token = TokenService(settings).issue_access(
                AccessClaims("user-1", "macos-1", "macos")
            )
            headers = {"Authorization": f"Bearer {token}"}
            with TestClient(create_app(settings)) as client:
                checked = client.post(
                    "/v1/app-updates/macos/check",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-1",
                        "bundle_id": settings.macos_bundle_id,
                        "current_build_number": 160,
                    },
                )
                self.assertEqual(checked.status_code, 200)
                self.assertTrue(checked.json()["available"])
                self.assertEqual(
                    checked.json()["update"]["build_number"],
                    161,
                )

                ticket = client.post(
                    "/v1/app-updates/macos/tickets",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-1",
                        "release_id": release["release_id"],
                    },
                )
                self.assertEqual(ticket.status_code, 200)
                raw_ticket = ticket.json()["ticket"]
                downloaded = client.post(
                    "/v1/app-updates/macos/download",
                    headers={**headers, "X-PCR-Update-Ticket": raw_ticket},
                )
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.content, archive.read_bytes())
                replay = client.post(
                    "/v1/app-updates/macos/download",
                    headers={**headers, "X-PCR-Update-Ticket": raw_ticket},
                )
                self.assertEqual(replay.status_code, 404)

    def test_android_identity_cannot_use_macos_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)

            async def seed() -> None:
                database = Database(settings.database_url)
                await database.create_schema()
                async with database.session_scope() as session:
                    now = utc_now()
                    session.add(
                        UserRecord(
                            id="user-1",
                            wechat_subject="test-user",
                            status="approved",
                            created_at=now,
                        )
                    )
                    await session.commit()
                    session.add_all(
                        [
                            DeviceRecord(
                                id="mac-1",
                                user_id="user-1",
                                kind="mac",
                                name="Mac",
                                public_key="m" * 44,
                                status="active",
                                created_at=now,
                                last_seen_at=now,
                            ),
                            DeviceRecord(
                                id="android-1",
                                user_id="user-1",
                                kind="android",
                                name="Phone",
                                public_key="a" * 44,
                                status="active",
                                created_at=now,
                                last_seen_at=now,
                            ),
                        ]
                    )
                    await session.commit()
                await database.close()

            asyncio.run(seed())
            token = TokenService(settings).issue_access(
                AccessClaims("user-1", "android-1", "android")
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/v1/app-updates/macos/check",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "mac_device_id": "mac-1",
                        "bundle_id": settings.macos_bundle_id,
                        "current_build_number": 0,
                    },
                )
                self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
