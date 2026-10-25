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
    Database,
    DeviceRecord,
    MacOSReleaseRecord,
    MacOSUpdateTicketRecord,
    new_id,
    utc_now,
)
from .updates import (
    UpdateAccessDenied,
    UpdateAdminError,
    UpdateTicketInvalid,
    UpdateUnavailable,
    _as_utc,
    _hash_file,
    _secure_artifact_for_directory,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacOSDownloadDescriptor:
    release: MacOSReleaseRecord
    artifact: Path


def macos_release_mapping(release: MacOSReleaseRecord) -> dict[str, Any]:
    return {
        "release_id": release.id,
        "bundle_id": release.bundle_id,
        "build_number": release.build_number,
        "version_name": release.version_name,
        "release_notes": release.release_notes,
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
        "designated_requirement": release.designated_requirement,
        "signing_authority": release.signing_authority,
        "published_at": release.created_at.isoformat(),
    }


def _remove_macos_artifact(root: Path, artifact_name: str) -> bool:
    name = Path(artifact_name)
    if name.name != artifact_name or name.suffix.lower() != ".zip":
        return False
    try:
        (root / name).unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


async def _retire_excess_macos_releases(
    settings: Settings,
    database: Database,
    *,
    bundle_id: str,
    root: Path,
) -> dict[str, int]:
    async with database.session_scope() as session:
        active = list(
            (
                await session.scalars(
                    select(MacOSReleaseRecord)
                    .where(
                        MacOSReleaseRecord.bundle_id == bundle_id,
                        MacOSReleaseRecord.revoked.is_(False),
                    )
                    .order_by(
                        MacOSReleaseRecord.build_number.desc(),
                        MacOSReleaseRecord.created_at.desc(),
                    )
                )
            ).all()
        )
        retired = active[settings.macos_release_retention :]
        retired_ids = [release.id for release in retired]
        if retired_ids:
            await session.execute(
                delete(MacOSUpdateTicketRecord).where(
                    MacOSUpdateTicketRecord.release_id.in_(retired_ids)
                )
            )
            await session.execute(
                update(MacOSReleaseRecord)
                .where(MacOSReleaseRecord.id.in_(retired_ids))
                .values(revoked=True)
            )
            await session.commit()

        artifact_names = list(
            await session.scalars(
                select(MacOSReleaseRecord.artifact_name).where(
                    MacOSReleaseRecord.bundle_id == bundle_id,
                    MacOSReleaseRecord.revoked.is_(True),
                )
            )
        )
    artifacts_cleaned = sum(
        1
        for artifact_name in artifact_names
        if _remove_macos_artifact(root, artifact_name)
    )
    return {
        "retained": min(len(active), settings.macos_release_retention),
        "newly_retired": len(retired),
        "revoked": len(artifact_names),
        "artifacts_cleaned": artifacts_cleaned,
    }


class MacOSUpdateService:
    """Private macOS release catalog and one-use controller tickets."""

    DOWNLOAD_PATH = "/v1/app-updates/macos/download"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _ticket_digest(self, raw_ticket: str) -> str:
        return hmac.new(
            self.settings.jwt_secret.encode("utf-8"),
            b"pcr-macos-update\0" + raw_ticket.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def authorize_controller(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        mac_device_id: str,
    ) -> DeviceRecord:
        if claims.device_kind != "macos":
            raise UpdateAccessDenied(
                "Only the paired macOS app can use macOS updates"
            )
        controller = await session.get(DeviceRecord, claims.device_id)
        mac = await session.get(DeviceRecord, mac_device_id)
        if (
            controller is None
            or controller.kind != "macos"
            or controller.status != "active"
            or controller.user_id != claims.user_id
            or mac is None
            or mac.kind != "mac"
            or mac.status != "active"
            or mac.user_id != claims.user_id
        ):
            raise UpdateAccessDenied(
                "An active paired computer is required for app updates"
            )
        return mac

    async def latest(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        mac_device_id: str,
    ) -> MacOSReleaseRecord | None:
        await self.authorize_controller(session, claims, mac_device_id)
        if not self.settings.macos_update_dir:
            return None
        release = await session.scalar(
            select(MacOSReleaseRecord)
            .where(
                MacOSReleaseRecord.bundle_id == self.settings.macos_bundle_id,
                MacOSReleaseRecord.revoked.is_(False),
            )
            .order_by(
                MacOSReleaseRecord.build_number.desc(),
                MacOSReleaseRecord.created_at.desc(),
            )
            .limit(1)
        )
        if release is not None:
            await self._verified_artifact(release)
        return release

    async def issue_ticket(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        mac_device_id: str,
        release_id: str,
    ) -> tuple[MacOSReleaseRecord, str, dt.datetime]:
        await self.authorize_controller(session, claims, mac_device_id)
        release = await session.scalar(
            select(MacOSReleaseRecord).where(
                MacOSReleaseRecord.id == release_id,
                MacOSReleaseRecord.bundle_id == self.settings.macos_bundle_id,
                MacOSReleaseRecord.revoked.is_(False),
            )
        )
        if release is None:
            raise UpdateTicketInvalid("Update is unavailable")
        await self._verified_artifact(release)

        now = utc_now()
        await session.execute(
            delete(MacOSUpdateTicketRecord).where(
                MacOSUpdateTicketRecord.controller_device_id == claims.device_id
            )
        )
        raw_ticket = secrets.token_urlsafe(48)
        expires_at = now + dt.timedelta(
            seconds=self.settings.macos_update_ticket_seconds
        )
        session.add(
            MacOSUpdateTicketRecord(
                id=new_id("macos-update"),
                token_digest=self._ticket_digest(raw_ticket),
                user_id=claims.user_id,
                controller_device_id=claims.device_id,
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
    ) -> MacOSDownloadDescriptor:
        if not 16 <= len(raw_ticket) <= 512:
            raise UpdateTicketInvalid("Update is unavailable")
        record = await session.scalar(
            select(MacOSUpdateTicketRecord)
            .where(
                MacOSUpdateTicketRecord.token_digest
                == self._ticket_digest(raw_ticket)
            )
            .with_for_update()
        )
        now = utc_now()
        if (
            record is None
            or record.user_id != claims.user_id
            or record.controller_device_id != claims.device_id
            or record.used_at is not None
            or _as_utc(record.expires_at) <= now
        ):
            raise UpdateTicketInvalid("Update is unavailable")

        await self.authorize_controller(session, claims, record.mac_device_id)
        release = await session.get(MacOSReleaseRecord, record.release_id)
        if (
            release is None
            or release.revoked
            or release.bundle_id != self.settings.macos_bundle_id
            or not secrets.compare_digest(release.sha256, record.release_sha256)
        ):
            raise UpdateTicketInvalid("Update is unavailable")
        artifact = await self._verified_artifact(release)

        consumed = await session.execute(
            update(MacOSUpdateTicketRecord)
            .where(
                MacOSUpdateTicketRecord.id == record.id,
                MacOSUpdateTicketRecord.used_at.is_(None),
                MacOSUpdateTicketRecord.expires_at > now,
            )
            .values(used_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            await session.rollback()
            raise UpdateTicketInvalid("Update is unavailable")
        await session.commit()
        return MacOSDownloadDescriptor(release=release, artifact=artifact)

    async def _verified_artifact(self, release: MacOSReleaseRecord) -> Path:
        configured = Path(self.settings.macos_update_dir).expanduser()
        if not self.settings.macos_update_dir or configured.is_symlink():
            raise UpdateUnavailable("macOS updates are not configured")
        root = configured.resolve()
        name = Path(release.artifact_name)
        if (
            name.name != release.artifact_name
            or name.suffix.lower() != ".zip"
            or release.size_bytes <= 0
            or release.size_bytes > self.settings.macos_update_max_bytes
        ):
            raise UpdateUnavailable("macOS update metadata is invalid")
        candidate = root / name
        if candidate.is_symlink():
            raise UpdateUnavailable("macOS update artifact is invalid")
        try:
            artifact = candidate.resolve()
            artifact.relative_to(root)
            stat = artifact.stat()
        except (OSError, ValueError) as exc:
            raise UpdateUnavailable("macOS update artifact is unavailable") from exc
        if not artifact.is_file() or stat.st_size != release.size_bytes:
            raise UpdateUnavailable("macOS update artifact is unavailable")
        try:
            digest = await asyncio.to_thread(_hash_file, artifact)
        except OSError as exc:
            raise UpdateUnavailable("macOS update artifact is unavailable") from exc
        if not secrets.compare_digest(digest, release.sha256):
            raise UpdateUnavailable(
                "macOS update artifact failed integrity verification"
            )
        return artifact


async def publish_macos_release(
    settings: Settings,
    *,
    archive_path: str,
    build_number: int,
    version_name: str,
    release_notes: str,
    bundle_id: str | None = None,
    designated_requirement: str,
    signing_authority: str,
) -> dict[str, Any]:
    if not settings.macos_update_dir:
        raise UpdateAdminError("PCR_RELAY_MACOS_UPDATE_DIR is not configured")
    if build_number <= 0:
        raise UpdateAdminError("build number must be positive")
    clean_version_name = version_name.strip()
    if not clean_version_name or len(clean_version_name) > 128:
        raise UpdateAdminError("version name must contain 1 to 128 characters")
    clean_release_notes = release_notes.strip()
    if not clean_release_notes:
        raise UpdateAdminError("release notes must not be empty")
    if len(clean_release_notes) > 20_000:
        raise UpdateAdminError("release notes must not exceed 20000 characters")
    selected_bundle = (bundle_id or settings.macos_bundle_id).strip()
    if selected_bundle != settings.macos_bundle_id:
        raise UpdateAdminError(f"bundle ID must be {settings.macos_bundle_id}")
    requirement = designated_requirement.strip()
    authority = signing_authority.strip()
    if not requirement or len(requirement) > 8_000:
        raise UpdateAdminError("designated requirement is missing or too long")
    if not authority or len(authority) > 256:
        raise UpdateAdminError("signing authority is missing or too long")

    source_input = Path(archive_path).expanduser()
    if source_input.is_symlink():
        raise UpdateAdminError("macOS archive path must not be a symlink")
    source = source_input.resolve()
    if source.suffix.lower() != ".zip" or not source.is_file():
        raise UpdateAdminError("macOS archive must be a regular .zip file")

    configured_root = Path(settings.macos_update_dir).expanduser()
    if configured_root.exists() and configured_root.is_symlink():
        raise UpdateAdminError("PCR_RELAY_MACOS_UPDATE_DIR must not be a symlink")
    root = configured_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        root.chmod(0o700)

    database = Database(settings.database_url)
    release_id = new_id("macos-release")
    artifact_name = f"{release_id}.zip"
    target = root / artifact_name
    temporary = root / f".{artifact_name}.{secrets.token_hex(8)}.tmp"
    try:
        if settings.environment == "production":
            await database.verify_schema()
        else:
            await database.create_schema()
        async with database.session_scope() as session:
            latest_build = await session.scalar(
                select(func.max(MacOSReleaseRecord.build_number)).where(
                    MacOSReleaseRecord.bundle_id == selected_bundle
                )
            )
            if latest_build is not None and build_number <= latest_build:
                raise UpdateAdminError(
                    "macOS build number must be greater than the latest "
                    f"published build ({latest_build})"
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
                if copied > settings.macos_update_max_bytes:
                    raise UpdateAdminError(
                        "macOS archive exceeds the configured size limit"
                    )
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if copied <= 0:
            raise UpdateAdminError("macOS archive must not be empty")
        _secure_artifact_for_directory(temporary, root)
        os.replace(temporary, target)

        release = MacOSReleaseRecord(
            id=release_id,
            bundle_id=selected_bundle,
            build_number=build_number,
            version_name=clean_version_name,
            release_notes=clean_release_notes,
            sha256=digest.hexdigest(),
            size_bytes=copied,
            designated_requirement=requirement,
            signing_authority=authority,
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
                f"macOS build {build_number} is already published"
            ) from exc
        try:
            await _retire_excess_macos_releases(
                settings,
                database,
                bundle_id=selected_bundle,
                root=root,
            )
        except Exception:
            LOGGER.exception("Unable to prune old macOS update artifacts")
        return macos_release_mapping(release)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise
    finally:
        await database.close()


async def list_macos_releases(settings: Settings) -> list[dict[str, Any]]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            releases = (
                await session.scalars(
                    select(MacOSReleaseRecord).order_by(
                        MacOSReleaseRecord.build_number.desc(),
                        MacOSReleaseRecord.created_at.desc(),
                    )
                )
            ).all()
            return [
                {**macos_release_mapping(release), "revoked": release.revoked}
                for release in releases
            ]
    finally:
        await database.close()


async def prune_macos_releases(settings: Settings) -> dict[str, int]:
    if not settings.macos_update_dir:
        raise UpdateAdminError("PCR_RELAY_MACOS_UPDATE_DIR is not configured")
    configured_root = Path(settings.macos_update_dir).expanduser()
    if configured_root.is_symlink():
        raise UpdateAdminError("PCR_RELAY_MACOS_UPDATE_DIR must not be a symlink")
    root = configured_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        return await _retire_excess_macos_releases(
            settings,
            database,
            bundle_id=settings.macos_bundle_id,
            root=root,
        )
    finally:
        await database.close()


async def revoke_macos_release(
    settings: Settings,
    release_id: str,
) -> dict[str, Any]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            release = await session.get(MacOSReleaseRecord, release_id)
            if release is None:
                raise UpdateAdminError(f"Unknown macOS release: {release_id}")
            await session.execute(
                delete(MacOSUpdateTicketRecord).where(
                    MacOSUpdateTicketRecord.release_id == release_id
                )
            )
            release.revoked = True
            await session.commit()
            result = {**macos_release_mapping(release), "revoked": True}
        configured_root = Path(settings.macos_update_dir).expanduser()
        removed = False
        if settings.macos_update_dir and not configured_root.is_symlink():
            removed = _remove_macos_artifact(
                configured_root.resolve(),
                release.artifact_name,
            )
        return {**result, "artifact_removed": removed}
    finally:
        await database.close()
