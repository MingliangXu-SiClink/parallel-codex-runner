from __future__ import annotations

import os
import re
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    pass


def _boolean(value: str, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean configuration value: {value}")


def _integer(name: str, value: str, default: int, *, minimum: int) -> int:
    if not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return parsed


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    public_base_url: str
    jwt_secret: str
    mac_enrollment_token: str
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    allowed_wechat_subjects: frozenset[str] = frozenset()
    auto_approve_wechat: bool = False
    access_token_seconds: int = 15 * 60
    refresh_token_seconds: int = 30 * 24 * 60 * 60
    pairing_seconds: int = 5 * 60
    offline_envelope_seconds: int = 24 * 60 * 60
    max_ciphertext_bytes: int = 2 * 1024 * 1024
    max_pending_envelopes_per_device: int = 512
    max_pending_bytes_per_device: int = 128 * 1024 * 1024
    cleanup_interval_seconds: int = 60 * 60
    android_update_dir: str = ""
    android_update_ticket_seconds: int = 5 * 60
    android_update_max_bytes: int = 512 * 1024 * 1024
    android_release_retention: int = 20
    android_package_name: str = "com.parallelcodexrunner.remote"
    android_signing_certificate_sha256: str = ""
    macos_update_dir: str = ""
    macos_update_ticket_seconds: int = 5 * 60
    macos_update_max_bytes: int = 512 * 1024 * 1024
    macos_release_retention: int = 20
    macos_bundle_id: str = "com.parallelcodexrunner.mac"
    feedback_max_attachment_bytes: int = 1024 * 1024
    feedback_max_total_attachment_bytes: int = 4 * 1024 * 1024
    feedback_max_attachments: int = 4
    feedback_submissions_per_hour: int = 20
    feedback_max_active_per_user: int = 500

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.environ.get("PCR_RELAY_ENV", "development").strip().lower()
        database_url = os.environ.get(
            "PCR_RELAY_DATABASE_URL",
            "sqlite+aiosqlite:///./pcr-relay.db",
        ).strip()
        allowed = frozenset(
            value.strip()
            for value in os.environ.get(
                "PCR_RELAY_ALLOWED_WECHAT_SUBJECTS", ""
            ).split(",")
            if value.strip()
        )
        settings = cls(
            environment=environment,
            database_url=database_url,
            public_base_url=os.environ.get(
                "PCR_RELAY_PUBLIC_BASE_URL", "https://localhost"
            ).strip().rstrip("/"),
            jwt_secret=os.environ.get("PCR_RELAY_JWT_SECRET", "").strip(),
            mac_enrollment_token=os.environ.get(
                "PCR_RELAY_MAC_ENROLLMENT_TOKEN", ""
            ).strip(),
            wechat_app_id=os.environ.get("PCR_WECHAT_APP_ID", "").strip(),
            wechat_app_secret=os.environ.get("PCR_WECHAT_APP_SECRET", "").strip(),
            allowed_wechat_subjects=allowed,
            auto_approve_wechat=_boolean(
                os.environ.get("PCR_RELAY_AUTO_APPROVE_WECHAT", ""),
                False,
            ),
            access_token_seconds=_integer(
                "PCR_RELAY_ACCESS_TOKEN_SECONDS",
                os.environ.get("PCR_RELAY_ACCESS_TOKEN_SECONDS", ""),
                15 * 60,
                minimum=60,
            ),
            refresh_token_seconds=_integer(
                "PCR_RELAY_REFRESH_TOKEN_SECONDS",
                os.environ.get("PCR_RELAY_REFRESH_TOKEN_SECONDS", ""),
                30 * 24 * 60 * 60,
                minimum=5 * 60,
            ),
            pairing_seconds=_integer(
                "PCR_RELAY_PAIRING_SECONDS",
                os.environ.get("PCR_RELAY_PAIRING_SECONDS", ""),
                5 * 60,
                minimum=30,
            ),
            offline_envelope_seconds=_integer(
                "PCR_RELAY_OFFLINE_ENVELOPE_SECONDS",
                os.environ.get("PCR_RELAY_OFFLINE_ENVELOPE_SECONDS", ""),
                24 * 60 * 60,
                minimum=60,
            ),
            max_ciphertext_bytes=_integer(
                "PCR_RELAY_MAX_CIPHERTEXT_BYTES",
                os.environ.get("PCR_RELAY_MAX_CIPHERTEXT_BYTES", ""),
                2 * 1024 * 1024,
                minimum=16 * 1024,
            ),
            max_pending_envelopes_per_device=_integer(
                "PCR_RELAY_MAX_PENDING_ENVELOPES_PER_DEVICE",
                os.environ.get(
                    "PCR_RELAY_MAX_PENDING_ENVELOPES_PER_DEVICE", ""
                ),
                512,
                minimum=1,
            ),
            max_pending_bytes_per_device=_integer(
                "PCR_RELAY_MAX_PENDING_BYTES_PER_DEVICE",
                os.environ.get("PCR_RELAY_MAX_PENDING_BYTES_PER_DEVICE", ""),
                128 * 1024 * 1024,
                minimum=1024 * 1024,
            ),
            cleanup_interval_seconds=_integer(
                "PCR_RELAY_CLEANUP_INTERVAL_SECONDS",
                os.environ.get("PCR_RELAY_CLEANUP_INTERVAL_SECONDS", ""),
                60 * 60,
                minimum=60,
            ),
            android_update_dir=os.environ.get(
                "PCR_RELAY_ANDROID_UPDATE_DIR", ""
            ).strip(),
            android_update_ticket_seconds=_integer(
                "PCR_RELAY_ANDROID_UPDATE_TICKET_SECONDS",
                os.environ.get("PCR_RELAY_ANDROID_UPDATE_TICKET_SECONDS", ""),
                5 * 60,
                minimum=30,
            ),
            android_update_max_bytes=_integer(
                "PCR_RELAY_ANDROID_UPDATE_MAX_BYTES",
                os.environ.get("PCR_RELAY_ANDROID_UPDATE_MAX_BYTES", ""),
                512 * 1024 * 1024,
                minimum=1024 * 1024,
            ),
            android_release_retention=_integer(
                "PCR_RELAY_ANDROID_RELEASE_RETENTION",
                os.environ.get("PCR_RELAY_ANDROID_RELEASE_RETENTION", ""),
                20,
                minimum=1,
            ),
            android_package_name=os.environ.get(
                "PCR_RELAY_ANDROID_PACKAGE_NAME",
                "com.parallelcodexrunner.remote",
            ).strip(),
            android_signing_certificate_sha256=os.environ.get(
                "PCR_RELAY_ANDROID_SIGNING_CERTIFICATE_SHA256", ""
            ).strip().lower().replace(":", ""),
            macos_update_dir=os.environ.get(
                "PCR_RELAY_MACOS_UPDATE_DIR", ""
            ).strip(),
            macos_update_ticket_seconds=_integer(
                "PCR_RELAY_MACOS_UPDATE_TICKET_SECONDS",
                os.environ.get("PCR_RELAY_MACOS_UPDATE_TICKET_SECONDS", ""),
                5 * 60,
                minimum=30,
            ),
            macos_update_max_bytes=_integer(
                "PCR_RELAY_MACOS_UPDATE_MAX_BYTES",
                os.environ.get("PCR_RELAY_MACOS_UPDATE_MAX_BYTES", ""),
                512 * 1024 * 1024,
                minimum=1024 * 1024,
            ),
            macos_release_retention=_integer(
                "PCR_RELAY_MACOS_RELEASE_RETENTION",
                os.environ.get("PCR_RELAY_MACOS_RELEASE_RETENTION", ""),
                20,
                minimum=1,
            ),
            macos_bundle_id=os.environ.get(
                "PCR_RELAY_MACOS_BUNDLE_ID",
                "com.parallelcodexrunner.mac",
            ).strip(),
            feedback_max_attachment_bytes=_integer(
                "PCR_RELAY_FEEDBACK_MAX_ATTACHMENT_BYTES",
                os.environ.get("PCR_RELAY_FEEDBACK_MAX_ATTACHMENT_BYTES", ""),
                1024 * 1024,
                minimum=64 * 1024,
            ),
            feedback_max_total_attachment_bytes=_integer(
                "PCR_RELAY_FEEDBACK_MAX_TOTAL_ATTACHMENT_BYTES",
                os.environ.get("PCR_RELAY_FEEDBACK_MAX_TOTAL_ATTACHMENT_BYTES", ""),
                4 * 1024 * 1024,
                minimum=64 * 1024,
            ),
            feedback_max_attachments=_integer(
                "PCR_RELAY_FEEDBACK_MAX_ATTACHMENTS",
                os.environ.get("PCR_RELAY_FEEDBACK_MAX_ATTACHMENTS", ""),
                4,
                minimum=1,
            ),
            feedback_submissions_per_hour=_integer(
                "PCR_RELAY_FEEDBACK_SUBMISSIONS_PER_HOUR",
                os.environ.get("PCR_RELAY_FEEDBACK_SUBMISSIONS_PER_HOUR", ""),
                20,
                minimum=1,
            ),
            feedback_max_active_per_user=_integer(
                "PCR_RELAY_FEEDBACK_MAX_ACTIVE_PER_USER",
                os.environ.get("PCR_RELAY_FEEDBACK_MAX_ACTIVE_PER_USER", ""),
                500,
                minimum=1,
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ConfigurationError("PCR_RELAY_ENV is invalid")
        if len(self.jwt_secret) < 32:
            raise ConfigurationError(
                "PCR_RELAY_JWT_SECRET must contain at least 32 characters"
            )
        if len(self.mac_enrollment_token) < 32:
            raise ConfigurationError(
                "PCR_RELAY_MAC_ENROLLMENT_TOKEN must contain at least 32 characters"
            )
        if (
            not self.public_base_url.startswith("https://")
            and self.environment == "production"
        ):
            raise ConfigurationError(
                "PCR_RELAY_PUBLIC_BASE_URL must use HTTPS in production"
            )
        if self.environment == "production" and not self.database_url.startswith(
            ("postgresql+asyncpg://", "postgresql://")
        ):
            raise ConfigurationError("Production relay storage must use PostgreSQL")
        if bool(self.wechat_app_id) != bool(self.wechat_app_secret):
            raise ConfigurationError(
                "PCR_WECHAT_APP_ID and PCR_WECHAT_APP_SECRET must be set together"
            )
        if self.android_update_ticket_seconds > 60 * 60:
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_UPDATE_TICKET_SECONDS must not exceed 3600"
            )
        if self.macos_update_ticket_seconds > 60 * 60:
            raise ConfigurationError(
                "PCR_RELAY_MACOS_UPDATE_TICKET_SECONDS must not exceed 3600"
            )
        if self.max_ciphertext_bytes > 2 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_MAX_CIPHERTEXT_BYTES must not exceed 2097152"
            )
        if self.max_pending_envelopes_per_device > 10_000:
            raise ConfigurationError(
                "PCR_RELAY_MAX_PENDING_ENVELOPES_PER_DEVICE must not exceed 10000"
            )
        if self.max_pending_bytes_per_device < self.max_ciphertext_bytes:
            raise ConfigurationError(
                "PCR_RELAY_MAX_PENDING_BYTES_PER_DEVICE must fit one ciphertext"
            )
        if self.max_pending_bytes_per_device > 2 * 1024 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_MAX_PENDING_BYTES_PER_DEVICE must not exceed 2147483648"
            )
        if self.android_update_max_bytes > 512 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_UPDATE_MAX_BYTES must not exceed 536870912"
            )
        if self.android_release_retention > 500:
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_RELEASE_RETENTION must not exceed 500"
            )
        if self.macos_update_max_bytes > 512 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_MACOS_UPDATE_MAX_BYTES must not exceed 536870912"
            )
        if self.macos_release_retention > 500:
            raise ConfigurationError(
                "PCR_RELAY_MACOS_RELEASE_RETENTION must not exceed 500"
            )
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",
            self.android_package_name,
        ):
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_PACKAGE_NAME must be a valid Android package name"
            )
        fingerprint = self.android_signing_certificate_sha256
        if fingerprint and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_SIGNING_CERTIFICATE_SHA256 must be a SHA-256 digest"
            )
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",
            self.macos_bundle_id,
        ):
            raise ConfigurationError(
                "PCR_RELAY_MACOS_BUNDLE_ID must be a valid bundle identifier"
            )
        if (
            self.environment == "production"
            and self.android_update_dir
            and not os.path.isabs(os.path.expanduser(self.android_update_dir))
        ):
            raise ConfigurationError(
                "PCR_RELAY_ANDROID_UPDATE_DIR must be absolute in production"
            )
        if (
            self.environment == "production"
            and self.macos_update_dir
            and not os.path.isabs(os.path.expanduser(self.macos_update_dir))
        ):
            raise ConfigurationError(
                "PCR_RELAY_MACOS_UPDATE_DIR must be absolute in production"
            )
        if self.feedback_max_attachment_bytes > 8 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_MAX_ATTACHMENT_BYTES must not exceed 8388608"
            )
        if self.feedback_max_total_attachment_bytes < self.feedback_max_attachment_bytes:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_MAX_TOTAL_ATTACHMENT_BYTES must fit one attachment"
            )
        if self.feedback_max_total_attachment_bytes > 32 * 1024 * 1024:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_MAX_TOTAL_ATTACHMENT_BYTES must not exceed 33554432"
            )
        if self.feedback_max_attachments > 10:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_MAX_ATTACHMENTS must not exceed 10"
            )
        if self.feedback_submissions_per_hour > 100:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_SUBMISSIONS_PER_HOUR must not exceed 100"
            )
        if self.feedback_max_active_per_user > 10_000:
            raise ConfigurationError(
                "PCR_RELAY_FEEDBACK_MAX_ACTIVE_PER_USER must not exceed 10000"
            )
