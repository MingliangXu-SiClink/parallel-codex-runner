from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessClaims
from .config import Settings
from .db import (
    AndroidReleaseRecord,
    AndroidUpdateTicketRecord,
    Database,
    DeviceRecord,
    new_id,
    utc_now,
)


LOGGER = logging.getLogger(__name__)


class UpdateAccessDenied(RuntimeError):
    pass


class UpdateUnavailable(RuntimeError):
    pass


class UpdateTicketInvalid(RuntimeError):
    pass


class UpdateAdminError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadDescriptor:
    release: AndroidReleaseRecord
    artifact: Path


def release_mapping(release: AndroidReleaseRecord) -> dict[str, Any]:
    return {
        "release_id": release.id,
        "package_name": release.package_name,
        "version_code": release.version_code,
        "version_name": release.version_name,
        "release_notes": release.release_notes,
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
        "signing_cert_sha256": release.signing_cert_sha256,
        "published_at": release.created_at.isoformat(),
        "available": not release.revoked,
    }


def mobile_release_mapping(release: AndroidReleaseRecord) -> dict[str, Any]:
    result = release_mapping(release)
    notes = str(result.get("release_notes") or "").strip()
    if not re.search(r"[\u3400-\u9fff]", notes):
        result["release_notes"] = "本版本未提供中文更新说明。"
    return result


def _as_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _normalize_fingerprint(value: str) -> str:
    normalized = value.strip().lower().replace(":", "")
    if normalized and not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise UpdateAdminError("signing certificate must be a SHA-256 digest")
    return normalized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secure_artifact_for_directory(path: Path, directory: Path) -> None:
    """Make a published artifact readable by the relay directory owner only."""
    try:
        directory_stat = directory.stat()
        artifact_stat = path.stat()
        if (artifact_stat.st_uid, artifact_stat.st_gid) != (
            directory_stat.st_uid,
            directory_stat.st_gid,
        ):
            os.chown(path, directory_stat.st_uid, directory_stat.st_gid)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise UpdateAdminError(
            "Unable to assign the update artifact to the relay owner"
        ) from exc


def _remove_release_artifact(root: Path, artifact_name: str) -> bool:
    """Remove one server-owned APK without accepting paths outside the store."""
    name = Path(artifact_name)
    if name.name != artifact_name or name.suffix.lower() != ".apk":
        return False
    candidate = root / name
    try:
        candidate.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


async def _retire_excess_android_releases(
    settings: Settings,
    database: Database,
    *,
    package_name: str,
    root: Path,
) -> dict[str, int]:
    """Keep recent APKs downloadable while retaining old release metadata."""
    async with database.session_scope() as session:
        active = list(
            (
                await session.scalars(
                    select(AndroidReleaseRecord)
                    .where(
                        AndroidReleaseRecord.package_name == package_name,
                        AndroidReleaseRecord.revoked.is_(False),
                    )
                    .order_by(
                        AndroidReleaseRecord.version_code.desc(),
                        AndroidReleaseRecord.created_at.desc(),
                    )
                )
            ).all()
        )
        retired = active[settings.android_release_retention :]
        retired_ids = [release.id for release in retired]
        if retired_ids:
            await session.execute(
                delete(AndroidUpdateTicketRecord).where(
                    AndroidUpdateTicketRecord.release_id.in_(retired_ids)
                )
            )
            await session.execute(
                update(AndroidReleaseRecord)
                .where(AndroidReleaseRecord.id.in_(retired_ids))
                .values(revoked=True)
            )
            await session.commit()

        # Also retry cleanup for a previously revoked release whose unlink was
        # interrupted. Metadata stays available to administrators as an audit log.
        artifact_names = list(
            await session.scalars(
                select(AndroidReleaseRecord.artifact_name).where(
                    AndroidReleaseRecord.package_name == package_name,
                    AndroidReleaseRecord.revoked.is_(True),
                )
            )
        )
    artifacts_cleaned = sum(
        1 for artifact_name in artifact_names
        if _remove_release_artifact(root, artifact_name)
    )
    return {
        "retained": min(len(active), settings.android_release_retention),
        "newly_retired": len(retired),
        "revoked": len(artifact_names),
        "artifacts_cleaned": artifacts_cleaned,
    }


class AndroidUpdateService:
    """Private Android release catalog and device-bound download tickets."""

    DOWNLOAD_PATH = "/v1/app-updates/android/download"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _ticket_digest(self, raw_ticket: str) -> str:
        return hmac.new(
            self.settings.jwt_secret.encode("utf-8"),
            b"pcr-android-update\0" + raw_ticket.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def authorize_android(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        mac_device_id: str,
    ) -> DeviceRecord:
        if claims.device_kind != "android":
            raise UpdateAccessDenied("Only paired Android devices can use app updates")
        android = await session.get(DeviceRecord, claims.device_id)
        mac = await session.get(DeviceRecord, mac_device_id)
        if (
            android is None
            or android.kind != "android"
            or android.status != "active"
            or android.user_id != claims.user_id
            or mac is None
            or mac.kind != "mac"
            or mac.status != "active"
            or mac.user_id != claims.user_id
        ):
            raise UpdateAccessDenied("An active paired Mac is required for app updates")
        return mac

    async def latest(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        mac_device_id: str,
    ) -> AndroidReleaseRecord | None:
        await self.authorize_android(session, claims, mac_device_id)
        if not self.settings.android_update_dir:
            return None
        release = await session.scalar(
            select(AndroidReleaseRecord)
            .where(
                AndroidReleaseRecord.package_name
                == self.settings.android_package_name,
                AndroidReleaseRecord.revoked.is_(False),
            )
            .order_by(
                AndroidReleaseRecord.version_code.desc(),
                AndroidReleaseRecord.created_at.desc(),
            )
            .limit(1)
        )
        if release is not None:
            await self._verified_artifact(release)
        return release

    async def history(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        mac_device_id: str,
        cursor: int = 0,
        limit: int = 20,
    ) -> tuple[list[AndroidReleaseRecord], int, bool]:
        await self.authorize_android(session, claims, mac_device_id)
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), 50)
        # Changelog history is metadata, not a download catalog. Keep retired
        # releases visible after their private APK is removed so the phone can
        # still show the complete version history. Ticket issuance remains
        # restricted to active, non-revoked artifacts below.
        where = (
            AndroidReleaseRecord.package_name == self.settings.android_package_name,
        )
        page_where = where + (
            (AndroidReleaseRecord.version_code < cursor,) if cursor else ()
        )
        releases = list(
            (
                await session.scalars(
                    select(AndroidReleaseRecord)
                    .where(*page_where)
                    .order_by(
                        AndroidReleaseRecord.version_code.desc(),
                        AndroidReleaseRecord.created_at.desc(),
                    )
                    .limit(limit + 1)
                )
            ).all()
        )
        has_more = len(releases) > limit
        releases = releases[:limit]
        next_cursor = releases[-1].version_code if releases else cursor
        return releases, next_cursor, has_more

    async def issue_ticket(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        mac_device_id: str,
        release_id: str,
    ) -> tuple[AndroidReleaseRecord, str, dt.datetime]:
        await self.authorize_android(session, claims, mac_device_id)
        release = await session.scalar(
            select(AndroidReleaseRecord).where(
                AndroidReleaseRecord.id == release_id,
                AndroidReleaseRecord.package_name
                == self.settings.android_package_name,
                AndroidReleaseRecord.revoked.is_(False),
            )
        )
        if release is None:
            raise UpdateTicketInvalid("Update is unavailable")
        await self._verified_artifact(release)

        now = utc_now()
        await session.execute(
            delete(AndroidUpdateTicketRecord).where(
                AndroidUpdateTicketRecord.android_device_id == claims.device_id
            )
        )
        raw_ticket = secrets.token_urlsafe(48)
        expires_at = now + dt.timedelta(
            seconds=self.settings.android_update_ticket_seconds
        )
        session.add(
            AndroidUpdateTicketRecord(
                id=new_id("android-update"),
                token_digest=self._ticket_digest(raw_ticket),
                user_id=claims.user_id,
                android_device_id=claims.device_id,
                mac_device_id=mac_device_id,
                release_id=release.id,
                release_sha256=release.sha256,
                created_at=now,
                expires_at=expires_at,
            )
        )
        await session.commit()
        return release, raw_ticket, expires_at

    async def consume_ticket(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        raw_ticket: str,
    ) -> DownloadDescriptor:
        if not 16 <= len(raw_ticket) <= 512:
            raise UpdateTicketInvalid("Update is unavailable")
        record = await session.scalar(
            select(AndroidUpdateTicketRecord)
            .where(
                AndroidUpdateTicketRecord.token_digest
                == self._ticket_digest(raw_ticket)
            )
            .with_for_update()
        )
        now = utc_now()
        if (
            record is None
            or record.user_id != claims.user_id
            or record.android_device_id != claims.device_id
            or record.used_at is not None
            or _as_utc(record.expires_at) <= now
        ):
            raise UpdateTicketInvalid("Update is unavailable")

        await self.authorize_android(session, claims, record.mac_device_id)
        release = await session.get(AndroidReleaseRecord, record.release_id)
        if (
            release is None
            or release.revoked
            or release.package_name != self.settings.android_package_name
            or not secrets.compare_digest(release.sha256, record.release_sha256)
        ):
            raise UpdateTicketInvalid("Update is unavailable")
        artifact = await self._verified_artifact(release)

        consumed = await session.execute(
            update(AndroidUpdateTicketRecord)
            .where(
                AndroidUpdateTicketRecord.id == record.id,
                AndroidUpdateTicketRecord.used_at.is_(None),
                AndroidUpdateTicketRecord.expires_at > now,
            )
            .values(used_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            await session.rollback()
            raise UpdateTicketInvalid("Update is unavailable")
        await session.commit()
        return DownloadDescriptor(release=release, artifact=artifact)

    async def _verified_artifact(self, release: AndroidReleaseRecord) -> Path:
        configured = Path(self.settings.android_update_dir).expanduser()
        if not self.settings.android_update_dir or configured.is_symlink():
            raise UpdateUnavailable("Android updates are not configured")
        root = configured.resolve()
        name = Path(release.artifact_name)
        if (
            name.name != release.artifact_name
            or name.suffix.lower() != ".apk"
            or release.size_bytes <= 0
            or release.size_bytes > self.settings.android_update_max_bytes
        ):
            raise UpdateUnavailable("Android update metadata is invalid")
        candidate = root / name
        if candidate.is_symlink():
            raise UpdateUnavailable("Android update artifact is invalid")
        try:
            artifact = candidate.resolve()
            artifact.relative_to(root)
            stat = artifact.stat()
        except (OSError, ValueError) as exc:
            raise UpdateUnavailable("Android update artifact is unavailable") from exc
        if not artifact.is_file() or stat.st_size != release.size_bytes:
            raise UpdateUnavailable("Android update artifact is unavailable")
        try:
            digest = await asyncio.to_thread(_hash_file, artifact)
        except OSError as exc:
            raise UpdateUnavailable(
                "Android update artifact is unavailable"
            ) from exc
        if not secrets.compare_digest(digest, release.sha256):
            raise UpdateUnavailable("Android update artifact failed integrity verification")
        return artifact


async def publish_android_release(
    settings: Settings,
    *,
    apk_path: str,
    version_code: int,
    version_name: str,
    release_notes: str = "",
    package_name: str | None = None,
    signing_cert_sha256: str = "",
) -> dict[str, Any]:
    if not settings.android_update_dir:
        raise UpdateAdminError("PCR_RELAY_ANDROID_UPDATE_DIR is not configured")
    if version_code <= 0:
        raise UpdateAdminError("version code must be positive")
    clean_version_name = version_name.strip()
    if not clean_version_name or len(clean_version_name) > 128:
        raise UpdateAdminError("version name must contain 1 to 128 characters")
    clean_release_notes = release_notes.strip()
    if len(clean_release_notes) > 20_000:
        raise UpdateAdminError("release notes must not exceed 20000 characters")
    if not re.search(r"[\u3400-\u9fff]", clean_release_notes):
        raise UpdateAdminError("release notes must include a Chinese description")
    selected_package = (package_name or settings.android_package_name).strip()
    if selected_package != settings.android_package_name:
        raise UpdateAdminError(
            f"package name must be {settings.android_package_name}"
        )
    fingerprint = _normalize_fingerprint(signing_cert_sha256)
    configured_fingerprint = settings.android_signing_certificate_sha256
    if configured_fingerprint and fingerprint != configured_fingerprint:
        raise UpdateAdminError(
            "APK signing certificate does not match the relay configuration"
        )

    source_input = Path(apk_path).expanduser()
    if source_input.is_symlink():
        raise UpdateAdminError("APK path must not be a symlink")
    source = source_input.resolve()
    if source.suffix.lower() != ".apk" or not source.is_file():
        raise UpdateAdminError("APK path must point to a regular .apk file")

    configured_root = Path(settings.android_update_dir).expanduser()
    if configured_root.exists() and configured_root.is_symlink():
        raise UpdateAdminError("PCR_RELAY_ANDROID_UPDATE_DIR must not be a symlink")
    root = configured_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        root.chmod(0o700)

    database = Database(settings.database_url)
    release_id = new_id("android-release")
    artifact_name = f"{release_id}.apk"
    target = root / artifact_name
    temporary = root / f".{artifact_name}.{secrets.token_hex(8)}.tmp"
    try:
        if settings.environment == "production":
            await database.verify_schema()
        else:
            await database.create_schema()
        async with database.session_scope() as session:
            latest_version_code = await session.scalar(
                select(func.max(AndroidReleaseRecord.version_code)).where(
                    AndroidReleaseRecord.package_name == selected_package
                )
            )
            if latest_version_code is not None and version_code <= latest_version_code:
                raise UpdateAdminError(
                    "Android version code must be greater than the latest "
                    f"published version ({latest_version_code})"
                )

        digest = hashlib.sha256()
        copied = 0
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            with contextlib.suppress(OSError):
                os.chmod(temporary, 0o600)
            while True:
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > settings.android_update_max_bytes:
                    raise UpdateAdminError("APK exceeds the configured size limit")
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if copied <= 0:
            raise UpdateAdminError("APK must not be empty")
        _secure_artifact_for_directory(temporary, root)
        os.replace(temporary, target)

        release = AndroidReleaseRecord(
            id=release_id,
            package_name=selected_package,
            version_code=version_code,
            version_name=clean_version_name,
            release_notes=clean_release_notes,
            sha256=digest.hexdigest(),
            size_bytes=copied,
            signing_cert_sha256=fingerprint or None,
            artifact_name=artifact_name,
            created_at=utc_now(),
            revoked=False,
        )
        try:
            async with database.session_scope() as session:
                session.add(release)
                await session.commit()
        except IntegrityError as exc:
            raise UpdateAdminError(
                f"Android version code {version_code} is already published"
            ) from exc
        try:
            await _retire_excess_android_releases(
                settings,
                database,
                package_name=selected_package,
                root=root,
            )
        except Exception:
            # Publishing is already committed. Report success rather than make an
            # operator retry the same immutable version; the explicit prune command
            # can safely retry maintenance later.
            LOGGER.exception("Unable to prune old Android update artifacts")
        return release_mapping(release)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise
    finally:
        await database.close()


async def list_android_releases(settings: Settings) -> list[dict[str, Any]]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            releases = (
                await session.scalars(
                    select(AndroidReleaseRecord).order_by(
                        AndroidReleaseRecord.version_code.desc(),
                        AndroidReleaseRecord.created_at.desc(),
                    )
                )
            ).all()
            return [
                {**release_mapping(release), "revoked": release.revoked}
                for release in releases
            ]
    finally:
        await database.close()


async def prune_android_releases(settings: Settings) -> dict[str, int]:
    if not settings.android_update_dir:
        raise UpdateAdminError("PCR_RELAY_ANDROID_UPDATE_DIR is not configured")
    configured_root = Path(settings.android_update_dir).expanduser()
    if configured_root.is_symlink():
        raise UpdateAdminError("PCR_RELAY_ANDROID_UPDATE_DIR must not be a symlink")
    root = configured_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        return await _retire_excess_android_releases(
            settings,
            database,
            package_name=settings.android_package_name,
            root=root,
        )
    finally:
        await database.close()


async def revoke_android_release(
    settings: Settings,
    release_id: str,
) -> dict[str, Any]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            release = await session.get(AndroidReleaseRecord, release_id)
            if release is None:
                raise UpdateAdminError(f"Unknown Android release: {release_id}")
            await session.execute(
                delete(AndroidUpdateTicketRecord).where(
                    AndroidUpdateTicketRecord.release_id == release_id
                )
            )
            release.revoked = True
            await session.commit()
            result = {**release_mapping(release), "revoked": True}
        configured_root = Path(settings.android_update_dir).expanduser()
        removed = False
        if settings.android_update_dir and not configured_root.is_symlink():
            removed = _remove_release_artifact(
                configured_root.resolve(),
                release.artifact_name,
            )
        return {**result, "artifact_removed": removed}
    finally:
        await database.close()
