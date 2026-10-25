from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from .config import Settings


class AuthenticationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "authentication_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WeChatIdentity:
    subject: str


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    device_id: str
    device_kind: str
    authorization_generation: int = 0


class WeChatClient:
    ENDPOINT = "https://api.weixin.qq.com/sns/oauth2/access_token"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def exchange(self, code: str) -> WeChatIdentity:
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            raise AuthenticationError("WeChat login is not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self.ENDPOINT,
                    params={
                        "appid": self.settings.wechat_app_id,
                        "secret": self.settings.wechat_app_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthenticationError(
                "WeChat login is temporarily unavailable"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthenticationError("WeChat returned an invalid response") from exc
        if response.is_error or not isinstance(payload, dict) or payload.get("errcode"):
            raise AuthenticationError("WeChat rejected the authorization code")
        union_id = str(payload.get("unionid") or "").strip()
        open_id = str(payload.get("openid") or "").strip()
        if union_id:
            return WeChatIdentity(f"unionid:{union_id}")
        if open_id:
            return WeChatIdentity(
                f"openid:{self.settings.wechat_app_id}:{open_id}"
            )
        raise AuthenticationError("WeChat response does not identify a user")


class TokenService:
    ISSUER = "pcr-relay"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def issue_access(self, claims: AccessClaims) -> str:
        now = dt.datetime.now(dt.timezone.utc)
        payload: dict[str, Any] = {
            "iss": self.ISSUER,
            "sub": claims.user_id,
            "device_id": claims.device_id,
            "device_kind": claims.device_kind,
            "authorization_generation": claims.authorization_generation,
            "iat": now,
            "exp": now + dt.timedelta(seconds=self.settings.access_token_seconds),
            "jti": secrets.token_hex(16),
        }
        return jwt.encode(payload, self.settings.jwt_secret, algorithm="HS256")

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_secret,
                algorithms=["HS256"],
                issuer=self.ISSUER,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Access token is invalid or expired") from exc
        user_id = str(payload.get("sub") or "")
        device_id = str(payload.get("device_id") or "")
        kind = str(payload.get("device_kind") or "")
        generation = payload.get("authorization_generation", 0)
        if (not user_id or not device_id or kind not in {"android", "mac", "macos"}
                or not isinstance(generation, int) or isinstance(generation, bool) or generation < 0):
            raise AuthenticationError("Access token claims are invalid")
        return AccessClaims(user_id, device_id, kind, generation)

    def new_refresh_token(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(48)
        return token, self.digest(token)

    def digest(self, value: str) -> str:
        return hashlib.sha256(
            self.settings.jwt_secret.encode("utf-8") + b"\0" + value.encode("utf-8")
        ).hexdigest()
