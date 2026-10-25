from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import secrets

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessClaims, AuthenticationError, TokenService, WeChatClient
from .config import Settings
from .db import (
    DeviceRecord,
    DeviceLoginChallengeRecord,
    PairingRecord,
    RefreshTokenRecord,
    UserRecord,
    new_id,
    utc_now,
)
from .device_login import create_device_challenge
from .schemas import (
    DeviceLoginChallengeResponse, DeviceLoginRequest,
    MacEnrollResponse, TokenResponse, WeChatLoginRequest,
)


class RelayServiceError(RuntimeError):
    pass


class DeviceLoginRateLimited(RelayServiceError):
    pass


def _approval_commitment(token: str) -> str:
    try:
        decoded = base64.b64decode(token, validate=True)
    except (ValueError, binascii.Error):
        return ""
    if len(decoded) != 32:
        return ""
    return base64.b64encode(hashlib.sha256(decoded).digest()).decode("ascii")


class IdentityService:
    DEVICE_CHALLENGE_SECONDS = 120
    MAX_RECENT_DEVICE_CHALLENGES = 8

    def __init__(
        self,
        settings: Settings,
        token_service: TokenService,
        wechat: WeChatClient,
    ) -> None:
        self.settings = settings
        self.tokens = token_service
        self.wechat = wechat

    async def login(
        self,
        session: AsyncSession,
        request: WeChatLoginRequest,
    ) -> TokenResponse:
        identity = await self.wechat.exchange(request.code)
        user = await session.scalar(
            select(UserRecord).where(UserRecord.wechat_subject == identity.subject)
        )
        approved = (
            self.settings.auto_approve_wechat
            or identity.subject in self.settings.allowed_wechat_subjects
        )
        if user is None:
            user = UserRecord(
                id=new_id("user"),
                wechat_subject=identity.subject,
                status="approved" if approved else "pending",
            )
            session.add(user)
            await session.flush()
        elif approved and user.status == "pending":
            user.status = "approved"
        if user.status != "approved":
            await session.commit()
            raise AuthenticationError("This WeChat account is awaiting approval")
        device = await session.scalar(
            select(DeviceRecord).where(
                DeviceRecord.kind == "android",
                DeviceRecord.public_key == request.public_key,
            )
        )
        if device is None:
            device = DeviceRecord(
                id=new_id("android"),
                user_id=user.id,
                kind="android",
                name=request.device_name,
                public_key=request.public_key,
                status="active",
            )
            session.add(device)
        elif device.user_id != user.id:
            raise AuthenticationError("This Android device belongs to another account")
        else:
            device.name = request.device_name
            device.status = "active"
            device.last_seen_at = utc_now()
        await session.flush()
        response = await self._issue_tokens(session, user.id, device)
        await session.commit()
        return response

    async def _issue_tokens(
        self,
        session: AsyncSession,
        user_id: str,
        device: DeviceRecord,
    ) -> TokenResponse:
        refresh, digest = self.tokens.new_refresh_token()
        session.add(
            RefreshTokenRecord(
                id=new_id("refresh"),
                device_id=device.id,
                token_digest=digest,
                expires_at=utc_now()
                + dt.timedelta(seconds=self.settings.refresh_token_seconds),
            )
        )
        access = self.tokens.issue_access(
            AccessClaims(user_id, device.id, device.kind, device.authorization_generation)
        )
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=self.settings.access_token_seconds,
            user_id=user_id,
            device_id=device.id,
        )

    async def refresh(
        self,
        session: AsyncSession,
        raw_token: str,
    ) -> TokenResponse:
        digest = self.tokens.digest(raw_token)
        record = await session.scalar(
            select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_digest == digest
            )
        )
        if record is None:
            raise AuthenticationError("Refresh token is invalid or expired", code="refresh_invalid")
        device = await self._trusted_device(session, record.device_id)
        record = await session.scalar(
            select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_digest == digest
            ).with_for_update().execution_options(populate_existing=True)
        )
        if record is None:
            raise AuthenticationError("Refresh token is invalid or expired", code="refresh_invalid")
        if record.revoked:
            raise AuthenticationError("Refresh token is invalid or expired", code="refresh_revoked")
        if record.expires_at.replace(tzinfo=dt.timezone.utc) <= utc_now():
            raise AuthenticationError("Refresh token is invalid or expired", code="refresh_expired")
        # Keep the refresh credential stable while the device remains active.
        # Rotating it here creates an unrecoverable gap if the server
        # commits but the client loses the response before saving the new token.
        # It also makes concurrent foreground/background refreshes invalidate
        # each other. Only authenticated activity extends the inactivity limit;
        # revoked devices, inactive accounts and already-expired tokens cannot
        # renew it. Use server time and never shorten an existing deadline.
        response = TokenResponse(
            access_token=self.tokens.issue_access(
                AccessClaims(device.user_id, device.id, device.kind, device.authorization_generation)
            ),
            refresh_token=raw_token,
            expires_in=self.settings.access_token_seconds,
            user_id=device.user_id,
            device_id=device.id,
        )
        record.expires_at = max(
            record.expires_at.replace(tzinfo=dt.timezone.utc),
            utc_now() + dt.timedelta(seconds=self.settings.refresh_token_seconds),
        )
        await session.commit()
        return response


    async def _trusted_device(
        self, session: AsyncSession, device_id: str,
    ) -> DeviceRecord:
        device = await session.get(DeviceRecord, device_id)
        if device is None or not device.user_id:
            raise AuthenticationError("This device is no longer authorized", code="device_unknown")
        # Match administrator revocation's user -> device lock order. Re-read
        # both rows under locks so a concurrent revoke cannot bootstrap a session.
        user = await session.scalar(select(UserRecord).where(
            UserRecord.id == device.user_id
        ).with_for_update().execution_options(populate_existing=True))
        device = await session.scalar(select(DeviceRecord).where(
            DeviceRecord.id == device_id
        ).with_for_update().execution_options(populate_existing=True))
        if user is None or user.status != "approved":
            raise AuthenticationError("User is not active", code="user_inactive")
        if device is None or device.status != "active" or device.user_id != user.id:
            raise AuthenticationError("This device is no longer authorized", code="device_inactive")
        return device

    async def device_challenge(
        self, session: AsyncSession, device_id: str,
    ) -> DeviceLoginChallengeResponse:
        device = await self._trusted_device(session, device_id)
        now = utc_now()
        await session.execute(delete(DeviceLoginChallengeRecord).where(
            DeviceLoginChallengeRecord.device_id == device.id,
            DeviceLoginChallengeRecord.expires_at <= now,
        ))
        recent = await session.scalar(select(func.count()).select_from(
            DeviceLoginChallengeRecord
        ).where(DeviceLoginChallengeRecord.device_id == device.id))
        if recent >= self.MAX_RECENT_DEVICE_CHALLENGES:
            raise DeviceLoginRateLimited("Please retry device sign-in shortly")
        expires = now + dt.timedelta(seconds=self.DEVICE_CHALLENGE_SECONDS)
        try:
            challenge, proof = create_device_challenge(
                challenge_id=new_id("device-login"), device_id=device.id,
                user_id=device.user_id, public_key=device.public_key,
                authorization_generation=device.authorization_generation,
                relay_url=self.settings.public_base_url, expires_at=expires.isoformat(),
            )
        except (ValueError, binascii.Error) as exc:
            raise AuthenticationError("The authorized device key is invalid", code="key_mismatch") from exc
        session.add(DeviceLoginChallengeRecord(
            id=challenge.challenge_id, device_id=device.id, user_id=device.user_id,
            public_key=device.public_key,
            authorization_generation=device.authorization_generation,
            proof_digest=self.tokens.digest(proof), expires_at=expires,
        ))
        await session.commit()
        return challenge

    async def authenticate_device(
        self, session: AsyncSession, request: DeviceLoginRequest,
    ) -> TokenResponse:
        device = await self._trusted_device(session, request.device_id)
        record = await session.scalar(select(DeviceLoginChallengeRecord).where(
            DeviceLoginChallengeRecord.id == request.challenge_id,
            DeviceLoginChallengeRecord.device_id == device.id,
        ).with_for_update().execution_options(populate_existing=True))
        now = utc_now()
        if record is None:
            raise AuthenticationError("The device challenge is unavailable", code="challenge_invalid")
        if record.used_at is not None:
            raise AuthenticationError("The device challenge has already been used", code="challenge_used")
        if record.expires_at.replace(tzinfo=dt.timezone.utc) <= now:
            raise AuthenticationError("The device challenge has expired", code="challenge_expired")
        if (record.authorization_generation != device.authorization_generation
                or record.public_key != device.public_key or record.user_id != device.user_id):
            raise AuthenticationError("Device authorization changed", code="device_authorization_changed")
        if not hmac.compare_digest(record.proof_digest, self.tokens.digest(request.proof)):
            record.used_at = now
            await session.commit()
            raise AuthenticationError("Device identity could not be verified", code="key_mismatch")
        # The conditional write also protects SQLite tests, where FOR UPDATE is
        # not supported. Challenge consumption and credential issuance commit once.
        consumed = await session.execute(update(DeviceLoginChallengeRecord).where(
            DeviceLoginChallengeRecord.id == record.id,
            DeviceLoginChallengeRecord.used_at.is_(None),
            DeviceLoginChallengeRecord.expires_at > now,
        ).values(used_at=now).execution_options(synchronize_session=False))
        if consumed.rowcount != 1:
            raise AuthenticationError("The device challenge has already been used", code="challenge_used")
        response = await self._issue_tokens(session, device.user_id, device)
        device.last_seen_at = now
        await session.commit()
        return response


class PairingService:
    def __init__(
        self,
        settings: Settings,
        token_service: TokenService,
        identity: IdentityService,
    ) -> None:
        self.settings = settings
        self.tokens = token_service
        self.identity = identity

    async def enroll_mac(
        self,
        session: AsyncSession,
        *,
        enrollment_token: str,
        device_name: str,
        public_key: str,
        pairing_commitment: str,
        approval_commitment: str,
    ) -> MacEnrollResponse:
        if not hmac.compare_digest(
            enrollment_token,
            self.settings.mac_enrollment_token,
        ):
            raise AuthenticationError("Mac enrollment token is invalid")
        mac = await session.scalar(
            select(DeviceRecord).where(
                DeviceRecord.kind == "mac",
                DeviceRecord.public_key == public_key,
            )
        )
        if mac is None:
            mac = DeviceRecord(
                id=new_id("mac"),
                user_id=None,
                kind="mac",
                name=device_name,
                public_key=public_key,
                status="pairing",
            )
            session.add(mac)
            await session.flush()
        else:
            mac.name = device_name
            if mac.status == "revoked":
                # A valid enrollment token is the explicit authorization to
                # reconnect this Mac after its previous relay access was
                # revoked. Keep active Macs active so adding another phone
                # does not interrupt an existing connection.
                mac.status = "pairing"
        poll = secrets.token_urlsafe(32)
        pairing = PairingRecord(
            id=new_id("pair"),
            mac_device_id=mac.id,
            pairing_commitment=pairing_commitment,
            approval_commitment=approval_commitment,
            poll_digest=self.tokens.digest(poll),
            status="pending",
            expires_at=utc_now()
            + dt.timedelta(seconds=self.settings.pairing_seconds),
        )
        session.add(pairing)
        await session.commit()
        return MacEnrollResponse(
            mac_device_id=mac.id,
            pairing_id=pairing.id,
            poll_token=poll,
            expires_at=pairing.expires_at.isoformat(),
        )

    async def authenticate_android(
        self,
        session: AsyncSession,
        *,
        pairing_id: str,
        approval_token: str,
        device_name: str,
        public_key: str,
        device_kind: str = "android",
        existing_claims: AccessClaims | None = None,
    ) -> TokenResponse:
        if device_kind not in {"android", "macos"}:
            raise AuthenticationError("Pairing controller kind is invalid")
        pairing = await session.get(PairingRecord, pairing_id)
        if pairing is None or pairing.status not in {"pending", "authorized"}:
            raise RelayServiceError("Pairing is unavailable")
        expires = pairing.expires_at.replace(tzinfo=dt.timezone.utc)
        if expires <= utc_now():
            pairing.status = "expired"
            await session.commit()
            raise RelayServiceError("Pairing has expired")
        if not hmac.compare_digest(
            pairing.approval_commitment,
            _approval_commitment(approval_token),
        ):
            raise AuthenticationError("Pairing authorization is invalid")

        mac = await session.get(DeviceRecord, pairing.mac_device_id)
        if mac is None or mac.status not in {"pairing", "active"}:
            raise AuthenticationError("Pairing Mac is unavailable")
        if existing_claims is not None:
            if existing_claims.device_kind != device_kind:
                raise AuthenticationError("Pairing controller identity does not match")
            android = await session.get(DeviceRecord, existing_claims.device_id)
            user = await session.get(UserRecord, existing_claims.user_id)
            if (
                android is None
                or android.user_id != existing_claims.user_id
                or android.kind != device_kind
                or android.public_key != public_key
                or user is None
                or user.status != "approved"
            ):
                raise AuthenticationError("Pairing Android device is invalid")
            if mac.user_id not in {None, user.id}:
                raise AuthenticationError("Pairing Mac belongs to another account")
            mac.user_id = user.id
            android.name = device_name
            android.last_seen_at = utc_now()
        else:
            user = await session.get(UserRecord, mac.user_id) if mac.user_id else None
            if user is None:
                subject = f"pairing:{mac.id}"
                user = await session.scalar(
                    select(UserRecord).where(UserRecord.wechat_subject == subject)
                )
                if user is None:
                    user = UserRecord(
                        id=new_id("user"),
                        wechat_subject=subject,
                        status="approved",
                    )
                    session.add(user)
                    await session.flush()
                mac.user_id = user.id
            if user.status != "approved":
                raise AuthenticationError("Pairing user is not active")

            android = await session.scalar(
                select(DeviceRecord).where(
                    DeviceRecord.kind == device_kind,
                    DeviceRecord.public_key == public_key,
                )
            )
            if android is None:
                android = DeviceRecord(
                    id=new_id(device_kind),
                    user_id=user.id,
                    kind=device_kind,
                    name=device_name,
                    public_key=public_key,
                    status="active",
                )
                session.add(android)
                await session.flush()
            elif android.user_id != user.id:
                raise AuthenticationError("This Android device belongs to another account")
            else:
                android.name = device_name
                android.status = "active"
                android.last_seen_at = utc_now()
        if (
            pairing.status == "authorized"
            and pairing.approved_by_device_id != android.id
        ):
            raise AuthenticationError("Pairing is authorized for another device")
        pairing.status = "authorized"
        pairing.approved_by_device_id = android.id
        response = await self.identity._issue_tokens(session, user.id, android)
        await session.commit()
        return response

    async def approve(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        pairing_id: str,
        proof: str,
        approval_token: str,
    ) -> None:
        if claims.device_kind not in {"android", "macos"}:
            raise AuthenticationError("Only controller devices may approve pairing")
        pairing = await session.get(PairingRecord, pairing_id)
        if pairing is None or pairing.status not in {"pending", "authorized"}:
            raise RelayServiceError("Pairing is unavailable")
        expires = pairing.expires_at.replace(tzinfo=dt.timezone.utc)
        if expires <= utc_now():
            pairing.status = "expired"
            await session.commit()
            raise RelayServiceError("Pairing has expired")
        if not hmac.compare_digest(
            pairing.approval_commitment,
            _approval_commitment(approval_token),
        ):
            raise AuthenticationError("Pairing authorization is invalid")
        android = await session.get(DeviceRecord, claims.device_id)
        mac = await session.get(DeviceRecord, pairing.mac_device_id)
        if (
            android is None
            or android.user_id != claims.user_id
            or android.kind != claims.device_kind
            or mac is None
            or mac.user_id not in {None, claims.user_id}
        ):
            raise AuthenticationError("Pairing devices are invalid")
        if (
            pairing.status == "authorized"
            and pairing.approved_by_device_id != android.id
        ):
            raise AuthenticationError("Pairing is authorized for another device")
        mac.user_id = claims.user_id
        mac.status = "active"
        pairing.status = "approved"
        pairing.approved_by_device_id = android.id
        pairing.pairing_proof = proof
        await session.commit()

    async def poll(
        self,
        session: AsyncSession,
        pairing_id: str,
        poll_token: str,
    ) -> tuple[
        str,
        TokenResponse | None,
        str | None,
        str | None,
        str | None,
    ]:
        pairing = await session.get(PairingRecord, pairing_id)
        if pairing is None or not hmac.compare_digest(
            pairing.poll_digest,
            self.tokens.digest(poll_token),
        ):
            raise AuthenticationError("Pairing poll token is invalid")
        expires = pairing.expires_at.replace(tzinfo=dt.timezone.utc)
        if expires <= utc_now() and pairing.status in {
            "pending",
            "authorized",
            "approved",
            "claimed",
        }:
            pairing.status = "expired"
            await session.commit()
        if pairing.status not in {"approved", "claimed"}:
            return pairing.status, None, None, None, None
        mac = await session.get(DeviceRecord, pairing.mac_device_id)
        android = (
            await session.get(DeviceRecord, pairing.approved_by_device_id)
            if pairing.approved_by_device_id
            else None
        )
        if mac is None or not mac.user_id or mac.status != "active":
            raise AuthenticationError("Paired Mac is unavailable")
        if android is None or android.status != "active" or not pairing.pairing_proof:
            raise AuthenticationError("Pairing approval is incomplete")
        tokens = await self.identity._issue_tokens(session, mac.user_id, mac)
        # Polling is retryable until the short-lived pairing expires. The
        # response may have reached the network while the client crashed or
        # disconnected before persisting it, so a claimed pairing must be able
        # to issue a replacement credential on the next authenticated poll.
        pairing.status = "claimed"
        pairing.claimed_at = utc_now()
        await session.commit()
        return (
            "approved",
            tokens,
            android.id,
            android.public_key,
            pairing.pairing_proof,
        )

    async def revoke_device(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        device_id: str,
    ) -> None:
        await session.scalar(select(UserRecord).where(
            UserRecord.id == claims.user_id
        ).with_for_update())
        device = await session.scalar(select(DeviceRecord).where(
            DeviceRecord.id == device_id
        ).with_for_update().execution_options(populate_existing=True))
        if device is None or device.user_id != claims.user_id:
            raise AuthenticationError("Device does not belong to this user")
        device.status = "revoked"
        device.authorization_generation += 1
        refresh_tokens = (
            await session.scalars(
                select(RefreshTokenRecord).where(
                    RefreshTokenRecord.device_id == device.id,
                    RefreshTokenRecord.revoked.is_(False),
                )
            )
        ).all()
        for token in refresh_tokens:
            token.revoked = True
        await session.commit()
