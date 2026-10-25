from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import AsyncIterator

import uvicorn
from anyio import CancelScope
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (
    AccessClaims,
    AuthenticationError,
    TokenService,
    WeChatClient,
)
from .config import Settings
from .db import Database, DeviceRecord, UserRecord, utc_now
from .feedback import (
    FeedbackAccessDenied,
    FeedbackInvalid,
    FeedbackNotFound,
    FeedbackQuotaExceeded,
    FeedbackService,
    FeedbackServiceError,
)
from .relay import (
    ConnectionHub,
    RelayConnectionClosed,
    RelayProtocolError,
    RelayService,
)
from .schemas import (
    AndroidUpdateCheckRequest,
    AndroidUpdateCheckResponse,
    AndroidUpdateHistoryResponse,
    AndroidUpdateInfo,
    AndroidUpdateTicketRequest,
    AndroidUpdateTicketResponse,
    DeviceLoginChallengeRequest,
    DeviceLoginChallengeResponse,
    DeviceLoginRequest,
    FeedbackAttachmentRequest,
    FeedbackInfo,
    FeedbackListResponse,
    FeedbackNoteRequest,
    FeedbackResponse,
    FeedbackSubmitRequest,
    MacOSUpdateCheckRequest,
    MacOSUpdateCheckResponse,
    MacOSUpdateInfo,
    MacOSUpdateTicketRequest,
    MacOSUpdateTicketResponse,
    MacEnrollRequest,
    MacEnrollResponse,
    PairingApproveRequest,
    PairingLoginRequest,
    PairingPollRequest,
    PairingPollResponse,
    RefreshRequest,
    TokenResponse,
    WeChatLoginRequest,
)
from .service import DeviceLoginRateLimited, IdentityService, PairingService, RelayServiceError
from .macos_updates import MacOSUpdateService, macos_release_mapping
from .updates import (
    AndroidUpdateService,
    UpdateAccessDenied,
    UpdateTicketInvalid,
    UpdateUnavailable,
    mobile_release_mapping,
)


def relay_version() -> str:
    try:
        return package_version("pcr-relay")
    except PackageNotFoundError:
        return "0.0.0+source"


bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: Settings | None = None,
    *,
    wechat_client: WeChatClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    database = Database(settings.database_url)
    tokens = TokenService(settings)
    wechat = wechat_client or WeChatClient(settings)
    identity = IdentityService(settings, tokens, wechat)
    pairing = PairingService(settings, tokens, identity)
    hub = ConnectionHub()
    relay = RelayService(settings, hub)
    updates = AndroidUpdateService(settings)
    macos_updates = MacOSUpdateService(settings)
    feedback = FeedbackService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.environment == "production":
            await database.verify_schema()
        else:
            await database.create_schema()
        await database.cleanup_expired()

        async def maintain_storage() -> None:
            while True:
                await asyncio.sleep(settings.cleanup_interval_seconds)
                await database.cleanup_expired()

        maintenance = asyncio.create_task(maintain_storage())
        try:
            yield
        finally:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance
            await database.close()

    app = FastAPI(
        title="PCR Remote Relay",
        version=relay_version(),
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.tokens = tokens
    app.state.hub = hub
    app.state.updates = updates
    app.state.macos_updates = macos_updates
    app.state.feedback = feedback

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with database.session_scope() as session:
            yield session

    async def optional_claims_dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        session: AsyncSession = Depends(session_dependency),
    ) -> AccessClaims | None:
        if credentials is None:
            return None
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
        try:
            claims = tokens.decode_access(credentials.credentials)
        except AuthenticationError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        device = await session.get(DeviceRecord, claims.device_id)
        user = await session.get(UserRecord, claims.user_id)
        if (
            device is None
            or device.status != "active"
            or device.user_id != claims.user_id
            or device.kind != claims.device_kind
            or device.authorization_generation != claims.authorization_generation
            or user is None
            or user.status != "approved"
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "User or device is not active",
            )
        return claims

    async def claims_dependency(
        claims: AccessClaims | None = Depends(optional_claims_dependency),
    ) -> AccessClaims:
        if claims is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
        return claims

    @app.exception_handler(AuthenticationError)
    async def authentication_error(_request: Request, exc: AuthenticationError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc), "code": exc.code},
        )

    @app.exception_handler(RelayServiceError)
    async def relay_service_error(_request: Request, exc: RelayServiceError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=(status.HTTP_429_TOO_MANY_REQUESTS
                         if isinstance(exc, DeviceLoginRateLimited) else status.HTTP_409_CONFLICT),
            content={"detail": str(exc)},
            headers={"Retry-After": "120"} if isinstance(exc, DeviceLoginRateLimited) else None,
        )

    @app.exception_handler(FeedbackServiceError)
    async def feedback_service_error(_request: Request, exc: FeedbackServiceError):
        from fastapi.responses import JSONResponse

        code = status.HTTP_400_BAD_REQUEST
        if isinstance(exc, FeedbackNotFound):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, FeedbackAccessDenied):
            code = status.HTTP_403_FORBIDDEN
        elif isinstance(exc, FeedbackQuotaExceeded):
            code = status.HTTP_429_TOO_MANY_REQUESTS
        elif not isinstance(exc, FeedbackInvalid):
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.get("/healthz")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "version": app.version,
            "authentication": "pairing",
            "environment": settings.environment,
        }

    @app.post("/v1/auth/wechat", response_model=TokenResponse)
    async def wechat_login(
        body: WeChatLoginRequest,
        session: AsyncSession = Depends(session_dependency),
    ) -> TokenResponse:
        return await identity.login(session, body)

    @app.post("/v1/auth/refresh", response_model=TokenResponse)
    async def refresh(
        body: RefreshRequest,
        session: AsyncSession = Depends(session_dependency),
    ) -> TokenResponse:
        return await identity.refresh(session, body.refresh_token)

    @app.post("/v1/auth/device/challenge", response_model=DeviceLoginChallengeResponse)
    async def device_challenge(
        body: DeviceLoginChallengeRequest, response: Response,
        session: AsyncSession = Depends(session_dependency),
    ) -> DeviceLoginChallengeResponse:
        response.headers["Cache-Control"] = "no-store"
        return await identity.device_challenge(session, body.device_id)

    @app.post("/v1/auth/device", response_model=TokenResponse)
    async def device_login(
        body: DeviceLoginRequest, response: Response,
        session: AsyncSession = Depends(session_dependency),
    ) -> TokenResponse:
        response.headers["Cache-Control"] = "no-store"
        return await identity.authenticate_device(session, body)

    @app.post("/v1/auth/pairing", response_model=TokenResponse)
    async def pairing_login(
        body: PairingLoginRequest,
        claims: AccessClaims | None = Depends(optional_claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> TokenResponse:
        return await pairing.authenticate_android(
            session,
            pairing_id=body.pairing_id,
            approval_token=body.approval_token,
            device_name=body.device_name,
            public_key=body.public_key,
            device_kind=body.device_kind,
            existing_claims=claims,
        )

    @app.post("/v1/mac/enroll", response_model=MacEnrollResponse)
    async def enroll_mac(
        body: MacEnrollRequest,
        x_pcr_enrollment: str = Header(default=""),
        session: AsyncSession = Depends(session_dependency),
    ) -> MacEnrollResponse:
        return await pairing.enroll_mac(
            session,
            enrollment_token=x_pcr_enrollment,
            device_name=body.device_name,
            public_key=body.public_key,
            pairing_commitment=body.pairing_commitment,
            approval_commitment=body.approval_commitment,
        )

    @app.post("/v1/pairings/{pairing_id}/approve", status_code=204)
    async def approve_pairing(
        pairing_id: str,
        body: PairingApproveRequest,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> None:
        await pairing.approve(
            session,
            claims,
            pairing_id,
            body.pairing_proof,
            body.approval_token,
        )

    @app.post(
        "/v1/pairings/{pairing_id}/poll",
        response_model=PairingPollResponse,
        response_model_exclude_none=True,
    )
    async def poll_pairing(
        pairing_id: str,
        body: PairingPollRequest,
        session: AsyncSession = Depends(session_dependency),
    ) -> PairingPollResponse:
        (
            pairing_status,
            response_tokens,
            android_device_id,
            android_public_key,
            pairing_proof,
        ) = await pairing.poll(
            session, pairing_id, body.poll_token
        )
        return PairingPollResponse(
            status=pairing_status,
            tokens=response_tokens,
            android_device_id=android_device_id,
            android_public_key=android_public_key,
            pairing_proof=pairing_proof,
        )

    @app.get("/v1/devices")
    async def devices(
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, object]:
        from sqlalchemy import select

        records = (
            await session.scalars(
                select(DeviceRecord).where(DeviceRecord.user_id == claims.user_id)
            )
        ).all()
        online_device_ids = await hub.connected_device_ids()
        return {
            "devices": [
                {
                    "device_id": item.id,
                    "kind": item.kind,
                    "name": item.name,
                    "public_key": item.public_key,
                    "status": item.status,
                    "online": item.id in online_device_ids,
                    "last_seen_at": item.last_seen_at.isoformat(),
                }
                for item in records
            ]
        }

    @app.delete("/v1/devices/{device_id}", status_code=204)
    async def revoke_device(
        device_id: str,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> None:
        await pairing.revoke_device(session, claims, device_id)
        await hub.disconnect(device_id)

    @app.post(
        "/v1/app-updates/android/check",
        response_model=AndroidUpdateCheckResponse,
    )
    async def check_android_update(
        body: AndroidUpdateCheckRequest,
        response: Response,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> AndroidUpdateCheckResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            release = await updates.latest(
                session,
                claims,
                mac_device_id=body.mac_device_id,
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except UpdateUnavailable as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Android updates are temporarily unavailable",
            ) from exc
        if (
            release is None
            or body.package_name != release.package_name
            or release.version_code <= body.current_version_code
        ):
            return AndroidUpdateCheckResponse(available=False)
        return AndroidUpdateCheckResponse(
            available=True,
            update=AndroidUpdateInfo(**mobile_release_mapping(release)),
        )

    @app.get(
        "/v1/app-updates/android/releases",
        response_model=AndroidUpdateHistoryResponse,
    )
    async def android_update_history(
        response: Response,
        mac_device_id: str = Query(min_length=1, max_length=64),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> AndroidUpdateHistoryResponse:
        response.headers["Cache-Control"] = "private, max-age=60"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            releases, next_cursor, has_more = await updates.history(
                session,
                claims,
                mac_device_id=mac_device_id,
                cursor=cursor,
                limit=limit,
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        return AndroidUpdateHistoryResponse(
            cursor=cursor,
            next_cursor=next_cursor,
            has_more=has_more,
            releases=[
                AndroidUpdateInfo(**mobile_release_mapping(release))
                for release in releases
            ],
        )

    @app.post(
        "/v1/app-updates/android/tickets",
        response_model=AndroidUpdateTicketResponse,
    )
    async def issue_android_update_ticket(
        body: AndroidUpdateTicketRequest,
        response: Response,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> AndroidUpdateTicketResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            release, raw_ticket, expires_at = await updates.issue_ticket(
                session,
                claims,
                mac_device_id=body.mac_device_id,
                release_id=body.release_id,
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except (UpdateTicketInvalid, UpdateUnavailable) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Update is unavailable") from exc
        return AndroidUpdateTicketResponse(
            release=AndroidUpdateInfo(**mobile_release_mapping(release)),
            ticket=raw_ticket,
            expires_at=expires_at.isoformat(),
            download_path=updates.DOWNLOAD_PATH,
        )

    @app.post("/v1/app-updates/android/download")
    async def download_android_update(
        x_pcr_update_ticket: str = Header(
            default="",
            alias="X-PCR-Update-Ticket",
        ),
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FileResponse:
        try:
            descriptor = await updates.consume_ticket(
                session,
                claims,
                x_pcr_update_ticket.strip(),
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except (UpdateTicketInvalid, UpdateUnavailable) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Update is unavailable") from exc
        return FileResponse(
            descriptor.artifact,
            media_type="application/vnd.android.package-archive",
            filename="Parallel-Codex.apk",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "X-PCR-Update-SHA256": descriptor.release.sha256,
            },
        )

    @app.post(
        "/v1/app-updates/macos/check",
        response_model=MacOSUpdateCheckResponse,
    )
    async def check_macos_update(
        body: MacOSUpdateCheckRequest,
        response: Response,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> MacOSUpdateCheckResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            release = await macos_updates.latest(
                session,
                claims,
                mac_device_id=body.mac_device_id,
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except UpdateUnavailable as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "macOS updates are temporarily unavailable",
            ) from exc
        if (
            release is None
            or body.bundle_id != release.bundle_id
            or release.build_number <= body.current_build_number
        ):
            return MacOSUpdateCheckResponse(available=False)
        return MacOSUpdateCheckResponse(
            available=True,
            update=MacOSUpdateInfo(**macos_release_mapping(release)),
        )

    @app.post(
        "/v1/app-updates/macos/tickets",
        response_model=MacOSUpdateTicketResponse,
    )
    async def issue_macos_update_ticket(
        body: MacOSUpdateTicketRequest,
        response: Response,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> MacOSUpdateTicketResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            release, raw_ticket, expires_at = await macos_updates.issue_ticket(
                session,
                claims,
                mac_device_id=body.mac_device_id,
                release_id=body.release_id,
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except (UpdateTicketInvalid, UpdateUnavailable) as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Update is unavailable",
            ) from exc
        return MacOSUpdateTicketResponse(
            release=MacOSUpdateInfo(**macos_release_mapping(release)),
            ticket=raw_ticket,
            expires_at=expires_at.isoformat(),
            download_path=macos_updates.DOWNLOAD_PATH,
        )

    @app.post("/v1/app-updates/macos/download")
    async def download_macos_update(
        x_pcr_update_ticket: str = Header(
            default="",
            alias="X-PCR-Update-Ticket",
        ),
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FileResponse:
        try:
            descriptor = await macos_updates.consume_ticket(
                session,
                claims,
                x_pcr_update_ticket.strip(),
            )
        except UpdateAccessDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except (UpdateTicketInvalid, UpdateUnavailable) as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Update is unavailable",
            ) from exc
        return FileResponse(
            descriptor.artifact,
            media_type="application/zip",
            filename="Parallel-Codex-macOS.zip",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "X-PCR-Update-SHA256": descriptor.release.sha256,
            },
        )

    @app.post("/v1/feedback", response_model=FeedbackResponse)
    async def submit_feedback(
        body: FeedbackSubmitRequest,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        item = await feedback.submit(
            session,
            claims,
            client_feedback_id=body.client_feedback_id,
            kind=body.kind,
            description=body.description,
            diagnostics=dict(body.diagnostics),
            related_device_id=body.related_device_id,
        )
        return FeedbackResponse(feedback=FeedbackInfo(**item))

    @app.get("/v1/feedback", response_model=FeedbackListResponse)
    async def list_feedback(
        cursor: str = Query(default="", max_length=512),
        limit: int = Query(default=30, ge=1, le=50),
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackListResponse:
        return FeedbackListResponse(
            **await feedback.list_for_user(
                session,
                claims,
                cursor=cursor,
                limit=limit,
            )
        )

    @app.get("/v1/feedback/{feedback_id}", response_model=FeedbackResponse)
    async def get_feedback(
        feedback_id: str,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        return FeedbackResponse(
            feedback=FeedbackInfo(
                **await feedback.get(session, claims, feedback_id)
            )
        )

    @app.post(
        "/v1/feedback/{feedback_id}/notes",
        response_model=FeedbackResponse,
    )
    async def add_feedback_note(
        feedback_id: str,
        body: FeedbackNoteRequest,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        return FeedbackResponse(
            feedback=FeedbackInfo(
                **await feedback.add_note(
                    session,
                    claims,
                    feedback_id,
                    client_note_id=body.client_note_id,
                    text=body.text,
                )
            )
        )

    @app.post(
        "/v1/feedback/{feedback_id}/attachments",
        response_model=FeedbackResponse,
    )
    async def add_feedback_attachment(
        feedback_id: str,
        body: FeedbackAttachmentRequest,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        return FeedbackResponse(
            feedback=FeedbackInfo(
                **await feedback.add_attachment(
                    session,
                    claims,
                    feedback_id,
                    content_type=body.content_type,
                    encoded=body.data,
                )
            )
        )

    @app.get("/v1/feedback/{feedback_id}/attachments/{attachment_id}")
    async def get_feedback_attachment(
        feedback_id: str,
        attachment_id: str,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        attachment = await feedback.attachment(
            session,
            claims,
            feedback_id,
            attachment_id,
        )
        return Response(
            content=attachment.content,
            media_type=attachment.content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-PCR-Content-SHA256": attachment.sha256,
            },
        )

    @app.delete(
        "/v1/feedback/{feedback_id}/attachments/{attachment_id}",
        response_model=FeedbackResponse,
    )
    async def remove_feedback_attachment(
        feedback_id: str,
        attachment_id: str,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        return FeedbackResponse(
            feedback=FeedbackInfo(
                **await feedback.remove_attachment(
                    session,
                    claims,
                    feedback_id,
                    attachment_id,
                )
            )
        )

    @app.post(
        "/v1/feedback/{feedback_id}/withdraw",
        response_model=FeedbackResponse,
    )
    async def withdraw_feedback(
        feedback_id: str,
        claims: AccessClaims = Depends(claims_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> FeedbackResponse:
        return FeedbackResponse(
            feedback=FeedbackInfo(
                **await feedback.withdraw(session, claims, feedback_id)
            )
        )

    async def websocket_claims(websocket: WebSocket) -> AccessClaims:
        authorization = websocket.headers.get("authorization", "")
        scheme, _, raw_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not raw_token:
            raise AuthenticationError("Bearer token required")
        claims = tokens.decode_access(raw_token)
        with CancelScope(shield=True):
            async with database.session_scope() as session:
                device = await session.get(DeviceRecord, claims.device_id)
                user = await session.get(UserRecord, claims.user_id)
                if (
                    device is None
                    or device.status != "active"
                    or device.user_id != claims.user_id
                    or device.kind != claims.device_kind
                    or device.authorization_generation != claims.authorization_generation
                    or user is None
                    or user.status != "approved"
                ):
                    raise AuthenticationError("User or device is not active")
                device.last_seen_at = utc_now()
                await session.commit()
        return claims

    @app.websocket("/v1/relay")
    async def relay_socket(websocket: WebSocket) -> None:
        try:
            claims = await websocket_claims(websocket)
        except AuthenticationError:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        connection = await hub.add(claims.device_id, websocket)

        async def send_pending() -> None:
            with CancelScope(shield=True):
                async with database.session_scope() as pending_session:
                    pending = await relay.pending(pending_session, claims)
            for pending_payload in pending:
                await connection.send(pending_payload)

        try:
            await send_pending()
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(
                        code=int(message.get("code") or 1000),
                        reason=str(message.get("reason") or ""),
                    )
                raw_payload = message.get("text")
                try:
                    if raw_payload is None:
                        raw_bytes = message.get("bytes")
                        raw_payload = raw_bytes.decode("utf-8") if raw_bytes is not None else ""
                    payload = json.loads(raw_payload or "null")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await connection.send(
                        {
                            "type": "rejected",
                            "message_id": "",
                            "code": "invalid_message",
                            "message": "Relay messages must contain valid JSON.",
                        }
                    )
                    continue
                acknowledged_message_id = ""
                try:
                    with CancelScope(shield=True):
                        async with database.session_scope() as session:
                            if (
                                isinstance(payload, dict)
                                and payload.get("type") == "ack"
                            ):
                                acknowledged_message_id = str(
                                    payload.get("message_id") or ""
                                )
                                response = await relay.acknowledge(
                                    session,
                                    claims,
                                    acknowledged_message_id,
                                )
                            else:
                                response = await relay.submit(session, claims, payload)
                except (AuthenticationError, RelayProtocolError) as exc:
                    message_id = (
                        str(payload.get("message_id") or "")
                        if isinstance(payload, dict)
                        else ""
                    )
                    await connection.send(
                        {
                            "type": "rejected",
                            "message_id": message_id,
                            "code": (
                                "not_allowed"
                                if isinstance(exc, AuthenticationError)
                                else "invalid_message"
                            ),
                            "message": str(exc),
                        }
                    )
                    continue
                if acknowledged_message_id:
                    await hub.acknowledge(
                        claims.device_id,
                        acknowledged_message_id,
                    )
                await connection.send(response)
                if acknowledged_message_id:
                    await send_pending()
        except (RelayConnectionClosed, WebSocketDisconnect):
            pass
        finally:
            await hub.remove(claims.device_id, websocket)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PCR Remote relay server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        ws_max_size=settings.max_ciphertext_bytes * 2,
    )


if __name__ == "__main__":
    main()
