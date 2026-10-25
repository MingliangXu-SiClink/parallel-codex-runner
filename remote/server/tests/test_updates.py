from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from pcr_relay.app import create_app
from pcr_relay.auth import AccessClaims, TokenService
from pcr_relay.config import Settings
from pcr_relay.db import (
    AndroidReleaseRecord,
    AndroidUpdateTicketRecord,
    Database,
    DeviceRecord,
    UserRecord,
)
from pcr_relay.updates import (
    AndroidUpdateService,
    UpdateAdminError,
    _secure_artifact_for_directory,
    publish_android_release,
    release_mapping,
    revoke_android_release,
)


class AndroidUpdateTests(unittest.TestCase):
    def settings(self, root: Path) -> Settings:
        return Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{root / 'relay.db'}",
            public_base_url="https://relay.test",
            jwt_secret="jwt-secret-" * 4,
            mac_enrollment_token="enrollment-secret-" * 3,
            android_update_dir=str(root / "private-android-releases"),
        )

    def publish(self, settings: Settings, source: Path) -> dict[str, object]:
        return self.publish_version(settings, source, version_code=4)

    def publish_version(
        self,
        settings: Settings,
        source: Path,
        *,
        version_code: int,
    ) -> dict[str, object]:
        source.write_bytes(b"signed-apk-placeholder")
        return asyncio.run(
            publish_android_release(
                settings,
                apk_path=str(source),
                version_code=version_code,
                version_name=f"0.1.{version_code}",
                release_notes="安全性与稳定性改进。",
                signing_cert_sha256="a" * 64,
            )
        )

    def test_publish_retires_old_apks_but_keeps_audit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(self.settings(root), android_release_retention=2)
            releases = [
                self.publish_version(
                    settings,
                    root / f"release-{version}.apk",
                    version_code=version,
                )
                for version in (1, 2, 3)
            ]

            async def inspect() -> list[tuple[int, bool]]:
                database = Database(settings.database_url)
                async with database.sessions() as session:
                    records = list(
                        (
                            await session.scalars(
                                select(AndroidReleaseRecord).order_by(
                                    AndroidReleaseRecord.version_code
                                )
                            )
                        ).all()
                    )
                await database.close()
                return [(record.version_code, record.revoked) for record in records]

            self.assertEqual(asyncio.run(inspect()), [(1, True), (2, False), (3, False)])
            self.assertEqual(
                len(list((root / "private-android-releases").glob("*.apk"))),
                2,
            )
            self.assertEqual(len(releases), 3)

            async def history() -> list[tuple[int, bool]]:
                database = Database(settings.database_url)
                service = AndroidUpdateService(settings)
                async with database.sessions() as session:
                    with patch.object(
                        service,
                        "authorize_android",
                        new=AsyncMock(return_value=None),
                    ):
                        records, _, has_more = await service.history(
                            session,
                            AccessClaims("user-1", "android-1", "android"),
                            mac_device_id="mac-1",
                            limit=10,
                        )
                await database.close()
                self.assertFalse(has_more)
                return [
                    (record.version_code, bool(release_mapping(record)["available"]))
                    for record in records
                ]

            self.assertEqual(
                asyncio.run(history()),
                [(3, True), (2, True), (1, False)],
            )

            async def page(cursor: int) -> tuple[list[int], int, bool]:
                database = Database(settings.database_url)
                service = AndroidUpdateService(settings)
                async with database.sessions() as session:
                    with patch.object(
                        service,
                        "authorize_android",
                        new=AsyncMock(return_value=None),
                    ):
                        records, next_cursor, has_more = await service.history(
                            session,
                            AccessClaims("user-1", "android-1", "android"),
                            mac_device_id="mac-1",
                            cursor=cursor,
                            limit=2,
                        )
                await database.close()
                return [record.version_code for record in records], next_cursor, has_more

            first_versions, next_cursor, has_more = asyncio.run(page(0))
            self.assertEqual(first_versions, [3, 2])
            self.assertTrue(has_more)
            self.publish_version(settings, root / "release-4.apk", version_code=4)
            second_versions, _, second_has_more = asyncio.run(page(next_cursor))
            self.assertEqual(second_versions, [1])
            self.assertFalse(second_has_more)

    def test_revoking_a_release_removes_its_private_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)
            release = self.publish(settings, root / "release.apk")
            artifact = next((root / "private-android-releases").glob("*.apk"))

            revoked = asyncio.run(
                revoke_android_release(settings, str(release["release_id"]))
            )

            self.assertTrue(revoked["revoked"])
            self.assertTrue(revoked["artifact_removed"])
            self.assertFalse(artifact.exists())

    def test_publish_uses_private_random_file_and_rejects_duplicate_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)
            release = self.publish(settings, root / "release.apk")

            self.assertNotIn("artifact", release)
            self.assertNotIn(str(root), str(release))
            artifacts = list((root / "private-android-releases").glob("*.apk"))
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].stat().st_mode & 0o777, 0o600)
            with self.assertRaises(UpdateAdminError):
                self.publish(settings, root / "duplicate.apk")

            english = root / "english.apk"
            english.write_bytes(b"signed-apk-placeholder")
            with self.assertRaisesRegex(UpdateAdminError, "Chinese description"):
                asyncio.run(
                    publish_android_release(
                        settings,
                        apk_path=str(english),
                        version_code=5,
                        version_name="0.1.4",
                        release_notes="Security improvements",
                        signing_cert_sha256="a" * 64,
                    )
                )

    def test_publish_matches_artifact_owner_to_release_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "release.apk"
            artifact.write_bytes(b"apk")
            current = artifact.stat()
            expected_uid = current.st_uid + 1
            expected_gid = current.st_gid + 1

            with (
                patch.object(
                    Path,
                    "stat",
                    side_effect=[
                        type(current)(
                            (
                                current.st_mode,
                                current.st_ino,
                                current.st_dev,
                                current.st_nlink,
                                expected_uid,
                                expected_gid,
                                current.st_size,
                                current.st_atime,
                                current.st_mtime,
                                current.st_ctime,
                            )
                        ),
                        current,
                    ],
                ),
                patch("pcr_relay.updates.os.chown") as chown,
            ):
                _secure_artifact_for_directory(artifact, root)

            chown.assert_called_once_with(artifact, expected_uid, expected_gid)
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)

    def test_download_requires_bearer_active_mac_and_one_use_header_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)
            release = self.publish(settings, root / "release.apk")

            async def seed() -> None:
                database = Database(settings.database_url)
                async with database.sessions() as session:
                    session.add(
                        UserRecord(
                            id="user-1",
                            wechat_subject="test-user",
                            status="approved",
                        )
                    )
                    session.add(
                        UserRecord(
                            id="user-2",
                            wechat_subject="other-user",
                            status="approved",
                        )
                    )
                    await session.flush()
                    session.add_all(
                        [
                            DeviceRecord(
                                id="mac-1",
                                user_id="user-1",
                                kind="mac",
                                name="Studio Mac",
                                public_key="mac-key",
                                status="active",
                            ),
                            DeviceRecord(
                                id="android-1",
                                user_id="user-1",
                                kind="android",
                                name="Phone",
                                public_key="android-key-1",
                                status="active",
                            ),
                            DeviceRecord(
                                id="android-2",
                                user_id="user-1",
                                kind="android",
                                name="Other phone",
                                public_key="android-key-2",
                                status="active",
                            ),
                            DeviceRecord(
                                id="mac-2",
                                user_id="user-2",
                                kind="mac",
                                name="Someone else's Mac",
                                public_key="mac-key-2",
                                status="active",
                            ),
                        ]
                    )
                    await session.commit()
                await database.close()

            asyncio.run(seed())
            tokens = TokenService(settings)
            phone_one = tokens.issue_access(
                AccessClaims("user-1", "android-1", "android")
            )
            phone_two = tokens.issue_access(
                AccessClaims("user-1", "android-2", "android")
            )
            headers = {"Authorization": f"Bearer {phone_one}"}

            with TestClient(create_app(settings)) as client:
                anonymous = client.post(
                    "/v1/app-updates/android/check",
                    json={
                        "mac_device_id": "mac-1",
                        "package_name": settings.android_package_name,
                        "current_version_code": 3,
                    },
                )
                self.assertEqual(anonymous.status_code, 401)

                other_account = client.post(
                    "/v1/app-updates/android/check",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-2",
                        "package_name": settings.android_package_name,
                        "current_version_code": 3,
                    },
                )
                self.assertEqual(other_account.status_code, 403)

                checked = client.post(
                    "/v1/app-updates/android/check",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-1",
                        "package_name": settings.android_package_name,
                        "current_version_code": 3,
                    },
                )
                self.assertEqual(checked.status_code, 200, checked.text)
                self.assertTrue(checked.json()["available"])
                self.assertEqual(
                    checked.json()["update"]["release_id"],
                    release["release_id"],
                )

                history = client.get(
                    "/v1/app-updates/android/releases",
                    headers=headers,
                    params={"mac_device_id": "mac-1", "limit": 1},
                )
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(history.json()["cursor"], 0)
                self.assertFalse(history.json()["has_more"])
                self.assertEqual(
                    history.json()["releases"][0]["release_id"],
                    release["release_id"],
                )
                self.assertIn("稳定性", history.json()["releases"][0]["release_notes"])

                grant = client.post(
                    "/v1/app-updates/android/tickets",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-1",
                        "release_id": release["release_id"],
                    },
                )
                self.assertEqual(grant.status_code, 200, grant.text)
                self.assertIn("no-store", grant.headers["cache-control"])
                payload = grant.json()
                self.assertEqual(
                    payload["download_path"],
                    "/v1/app-updates/android/download",
                )
                self.assertNotIn(payload["ticket"], payload["download_path"])

                async def stored_ticket_digest() -> str:
                    database = Database(settings.database_url)
                    async with database.sessions() as session:
                        digest = await session.scalar(
                            select(AndroidUpdateTicketRecord.token_digest)
                        )
                    await database.close()
                    return str(digest)

                digest = asyncio.run(stored_ticket_digest())
                self.assertNotEqual(digest, payload["ticket"])
                self.assertNotIn(payload["ticket"], digest)

                no_ticket = client.post(payload["download_path"], headers=headers)
                self.assertEqual(no_ticket.status_code, 404)
                wrong_phone = client.post(
                    payload["download_path"],
                    headers={
                        "Authorization": f"Bearer {phone_two}",
                        "X-PCR-Update-Ticket": payload["ticket"],
                    },
                )
                self.assertEqual(wrong_phone.status_code, 404)
                downloaded = client.post(
                    payload["download_path"],
                    headers={
                        **headers,
                        "X-PCR-Update-Ticket": payload["ticket"],
                    },
                )
                self.assertEqual(downloaded.status_code, 200, downloaded.text)
                self.assertEqual(downloaded.content, b"signed-apk-placeholder")
                self.assertIn("no-store", downloaded.headers["cache-control"])
                replay = client.post(
                    payload["download_path"],
                    headers={
                        **headers,
                        "X-PCR-Update-Ticket": payload["ticket"],
                    },
                )
                self.assertEqual(replay.status_code, 404)

                second_grant = client.post(
                    "/v1/app-updates/android/tickets",
                    headers=headers,
                    json={
                        "mac_device_id": "mac-1",
                        "release_id": release["release_id"],
                    },
                )
                self.assertEqual(second_grant.status_code, 200)

                async def revoke_mac() -> None:
                    database = Database(settings.database_url)
                    async with database.sessions() as session:
                        mac = await session.get(DeviceRecord, "mac-1")
                        assert mac is not None
                        mac.status = "revoked"
                        await session.commit()
                    await database.close()

                asyncio.run(revoke_mac())
                revoked_mac_download = client.post(
                    second_grant.json()["download_path"],
                    headers={
                        **headers,
                        "X-PCR-Update-Ticket": second_grant.json()["ticket"],
                    },
                )
                self.assertEqual(revoked_mac_download.status_code, 403)

    def test_tampered_private_artifact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self.settings(root)
            self.publish(settings, root / "release.apk")
            artifact = next((root / "private-android-releases").glob("*.apk"))
            artifact.write_bytes(b"tampered")

            async def seed() -> None:
                database = Database(settings.database_url)
                async with database.sessions() as session:
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
                                id="mac-1",
                                user_id="user-1",
                                kind="mac",
                                name="Mac",
                                public_key="mac-key",
                                status="active",
                            ),
                            DeviceRecord(
                                id="android-1",
                                user_id="user-1",
                                kind="android",
                                name="Phone",
                                public_key="android-key",
                                status="active",
                            ),
                        ]
                    )
                    await session.commit()
                await database.close()

            asyncio.run(seed())
            access = TokenService(settings).issue_access(
                AccessClaims("user-1", "android-1", "android")
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/v1/app-updates/android/check",
                    headers={"Authorization": f"Bearer {access}"},
                    json={
                        "mac_device_id": "mac-1",
                        "package_name": settings.android_package_name,
                        "current_version_code": 3,
                    },
                )
                self.assertEqual(response.status_code, 503, response.text)

                artifact.write_bytes(b"signed-apk-placeholder")
                with patch(
                    "pcr_relay.updates._hash_file",
                    side_effect=PermissionError("permission denied"),
                ):
                    unreadable = client.post(
                        "/v1/app-updates/android/check",
                        headers={"Authorization": f"Bearer {access}"},
                        json={
                            "mac_device_id": "mac-1",
                            "package_name": settings.android_package_name,
                            "current_version_code": 3,
                        },
                    )
                self.assertEqual(unreadable.status_code, 503, unreadable.text)


if __name__ == "__main__":
    unittest.main()
