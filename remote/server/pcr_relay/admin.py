from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select, update

from .config import Settings
from .db import Database, DeviceRecord, RefreshTokenRecord, UserRecord
from .feedback import FeedbackService, FeedbackServiceError
from .macos_updates import (
    list_macos_releases,
    prune_macos_releases,
    publish_macos_release,
    revoke_macos_release,
)
from .updates import (
    list_android_releases,
    prune_android_releases,
    publish_android_release,
    revoke_android_release,
)


class AdminError(RuntimeError):
    pass


def _user_mapping(user: UserRecord) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "wechat_subject": user.wechat_subject,
        "status": user.status,
        "created_at": user.created_at.isoformat(),
    }


async def list_users(settings: Settings) -> list[dict[str, Any]]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            users = (
                await session.scalars(
                    select(UserRecord).order_by(UserRecord.created_at, UserRecord.id)
                )
            ).all()
            return [_user_mapping(user) for user in users]
    finally:
        await database.close()


async def set_user_status(
    settings: Settings,
    user_id: str,
    status: str,
) -> dict[str, Any]:
    if status not in {"approved", "revoked"}:
        raise AdminError(f"Unsupported user status: {status}")
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            user = await session.get(UserRecord, user_id)
            if user is None:
                raise AdminError(f"Unknown relay user: {user_id}")
            user.status = status
            if status == "revoked":
                device_ids = list(
                    await session.scalars(
                        select(DeviceRecord.id).where(
                            DeviceRecord.user_id == user.id
                        )
                    )
                )
                if device_ids:
                    await session.execute(
                        update(DeviceRecord)
                        .where(DeviceRecord.id.in_(device_ids))
                        .values(
                            status="revoked",
                            authorization_generation=DeviceRecord.authorization_generation + 1,
                        )
                    )
                    await session.execute(
                        update(RefreshTokenRecord)
                        .where(RefreshTokenRecord.device_id.in_(device_ids))
                        .values(revoked=True)
                    )
            await session.commit()
            return _user_mapping(user)
    finally:
        await database.close()


async def list_feedback(
    settings: Settings,
    *,
    status: str,
    version: str,
    device_id: str,
    workspace: str,
    limit: int,
) -> list[dict[str, Any]]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            return await FeedbackService(settings).admin_list(
                session,
                status=status,
                version=version,
                device_id=device_id,
                workspace=workspace,
                limit=limit,
            )
    finally:
        await database.close()


async def get_feedback(settings: Settings, feedback_id: str) -> dict[str, Any]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            return await FeedbackService(settings).admin_get(session, feedback_id)
    finally:
        await database.close()


async def update_feedback(
    settings: Settings,
    feedback_id: str,
    *,
    status: str,
    reply: str | None,
    fixed_version: str | None,
) -> dict[str, Any]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            return await FeedbackService(settings).admin_update(
                session,
                feedback_id,
                status=status,
                developer_reply=reply,
                fixed_version=fixed_version,
            )
    finally:
        await database.close()


async def export_feedback_attachment(
    settings: Settings,
    feedback_id: str,
    attachment_id: str,
    output: str,
) -> dict[str, Any]:
    database = Database(settings.database_url)
    try:
        await database.verify_schema()
        async with database.session_scope() as session:
            attachment = await FeedbackService(settings).admin_attachment(
                session,
                feedback_id,
                attachment_id,
            )
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(attachment.content)
        return {
            "feedback_id": feedback_id,
            "attachment_id": attachment_id,
            "output": str(target),
            "content_type": attachment.content_type,
            "size_bytes": len(attachment.content),
            "sha256": attachment.sha256,
        }
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcr-relay-admin",
        description="Manage PCR Relay accounts and private app releases.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("users", help="list relay users and approval states")
    approve = commands.add_parser("approve", help="approve one pending user")
    approve.add_argument("user_id")
    revoke = commands.add_parser("revoke", help="revoke one user and its devices")
    revoke.add_argument("user_id")
    commands.add_parser(
        "android-releases",
        help="list private Android releases",
    )
    commands.add_parser(
        "prune-android-releases",
        help="retain only the configured number of downloadable Android releases",
    )
    publish = commands.add_parser(
        "publish-android",
        help="copy a signed APK into the private release store",
    )
    publish.add_argument("apk")
    publish.add_argument("--version-code", type=int, required=True)
    publish.add_argument("--version-name", required=True)
    publish.add_argument("--release-notes", default="")
    publish.add_argument("--package-name")
    publish.add_argument("--signing-cert-sha256", default="")
    revoke_release = commands.add_parser(
        "revoke-android-release",
        help="prevent an Android release from being downloaded",
    )
    revoke_release.add_argument("release_id")
    commands.add_parser(
        "macos-releases",
        help="list private macOS releases",
    )
    commands.add_parser(
        "prune-macos-releases",
        help="retain only the configured number of downloadable macOS releases",
    )
    publish_macos = commands.add_parser(
        "publish-macos",
        help="copy a signed macOS app archive into the private release store",
    )
    publish_macos.add_argument("archive")
    publish_macos.add_argument("--build-number", type=int, required=True)
    publish_macos.add_argument("--version-name", required=True)
    publish_macos.add_argument("--release-notes", required=True)
    publish_macos.add_argument("--bundle-id")
    publish_macos.add_argument("--designated-requirement", required=True)
    publish_macos.add_argument("--signing-authority", required=True)
    revoke_macos = commands.add_parser(
        "revoke-macos-release",
        help="prevent a macOS release from being downloaded",
    )
    revoke_macos.add_argument("release_id")
    feedback = commands.add_parser(
        "feedback",
        help="list the private product feedback inbox",
    )
    feedback.add_argument("--status", default="")
    feedback.add_argument("--version", default="")
    feedback.add_argument("--device", default="")
    feedback.add_argument("--workspace", default="")
    feedback.add_argument("--limit", type=int, default=100)
    feedback_show = commands.add_parser(
        "feedback-show",
        help="show one private feedback record",
    )
    feedback_show.add_argument("feedback_id")
    feedback_update = commands.add_parser(
        "feedback-update",
        help="update feedback status and the developer response",
    )
    feedback_update.add_argument("feedback_id")
    feedback_update.add_argument(
        "--status",
        choices=("submitted", "confirmed", "in_progress", "fixed", "declined"),
        default="",
    )
    feedback_update.add_argument("--reply")
    feedback_update.add_argument("--fixed-version")
    feedback_attachment = commands.add_parser(
        "feedback-attachment",
        help="export one authorized feedback screenshot",
    )
    feedback_attachment.add_argument("feedback_id")
    feedback_attachment.add_argument("attachment_id")
    feedback_attachment.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command == "users":
            result: object = asyncio.run(list_users(settings))
        elif args.command in {"approve", "revoke"}:
            result = asyncio.run(
                set_user_status(settings, args.user_id, args.command + "d")
            )
        elif args.command == "android-releases":
            result = asyncio.run(list_android_releases(settings))
        elif args.command == "prune-android-releases":
            result = asyncio.run(prune_android_releases(settings))
        elif args.command == "publish-android":
            result = asyncio.run(
                publish_android_release(
                    settings,
                    apk_path=args.apk,
                    version_code=args.version_code,
                    version_name=args.version_name,
                    release_notes=args.release_notes,
                    package_name=args.package_name,
                    signing_cert_sha256=args.signing_cert_sha256,
                )
            )
        elif args.command == "revoke-android-release":
            result = asyncio.run(
                revoke_android_release(settings, args.release_id)
            )
        elif args.command == "macos-releases":
            result = asyncio.run(list_macos_releases(settings))
        elif args.command == "prune-macos-releases":
            result = asyncio.run(prune_macos_releases(settings))
        elif args.command == "publish-macos":
            result = asyncio.run(
                publish_macos_release(
                    settings,
                    archive_path=args.archive,
                    build_number=args.build_number,
                    version_name=args.version_name,
                    release_notes=args.release_notes,
                    bundle_id=args.bundle_id,
                    designated_requirement=args.designated_requirement,
                    signing_authority=args.signing_authority,
                )
            )
        elif args.command == "revoke-macos-release":
            result = asyncio.run(
                revoke_macos_release(settings, args.release_id)
            )
        elif args.command == "feedback":
            result = asyncio.run(
                list_feedback(
                    settings,
                    status=args.status,
                    version=args.version,
                    device_id=args.device,
                    workspace=args.workspace,
                    limit=max(1, min(args.limit, 1_000)),
                )
            )
        elif args.command == "feedback-show":
            result = asyncio.run(get_feedback(settings, args.feedback_id))
        elif args.command == "feedback-update":
            result = asyncio.run(
                update_feedback(
                    settings,
                    args.feedback_id,
                    status=args.status,
                    reply=args.reply,
                    fixed_version=args.fixed_version,
                )
            )
        elif args.command == "feedback-attachment":
            result = asyncio.run(
                export_feedback_attachment(
                    settings,
                    args.feedback_id,
                    args.attachment_id,
                    args.output,
                )
            )
        else:
            raise AdminError(f"Unsupported admin command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AdminError, FeedbackServiceError, RuntimeError) as exc:
        print(f"pcr-relay-admin: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
