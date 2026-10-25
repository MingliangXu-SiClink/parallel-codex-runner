from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessClaims, AuthenticationError
from .config import Settings
from .db import DeviceRecord, EnvelopeRecord, UserRecord, utc_now


class RelayProtocolError(RuntimeError):
    pass


class RelayConnectionClosed(RuntimeError):
    pass


def relay_device_lock_order(sender_id: str, target_id: str) -> tuple[str, ...]:
    """Use one lock order for both directions of a device conversation."""
    return tuple(sorted({sender_id, target_id}))


@dataclass(eq=False)
class RelayConnection:
    websocket: WebSocket
    send_timeout_seconds: float = 5.0
    in_flight_ids: set[str] = field(default_factory=set)
    recently_acknowledged_ids: set[str] = field(default_factory=set)
    acknowledged_order: deque[str] = field(default_factory=deque)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    MAX_RECENT_ACKNOWLEDGED_IDS = 1_024

    async def send(self, payload: dict[str, Any]) -> bool:
        message_id = (
            str(payload.get("message_id") or "")
            if payload.get("type") == "envelope"
            else ""
        )
        async with self.send_lock:
            if message_id and (
                message_id in self.in_flight_ids
                or message_id in self.recently_acknowledged_ids
            ):
                return False
            try:
                await asyncio.wait_for(
                    self.websocket.send_json(payload),
                    timeout=self.send_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise RelayConnectionClosed(
                    "Relay connection did not accept a message in time"
                ) from exc
            except RuntimeError as exc:
                if "close message has been sent" not in str(exc).lower():
                    raise
                raise RelayConnectionClosed("Relay connection is closed") from exc
            if message_id:
                self.in_flight_ids.add(message_id)
            return True

    async def acknowledge(self, message_id: str) -> None:
        if not message_id:
            return
        async with self.send_lock:
            self.in_flight_ids.discard(message_id)
            if message_id in self.recently_acknowledged_ids:
                return
            self.recently_acknowledged_ids.add(message_id)
            self.acknowledged_order.append(message_id)
            while len(self.acknowledged_order) > self.MAX_RECENT_ACKNOWLEDGED_IDS:
                expired = self.acknowledged_order.popleft()
                self.recently_acknowledged_ids.discard(expired)


class ConnectionHub:
    def __init__(self, *, send_timeout_seconds: float = 5.0) -> None:
        self._connections: dict[str, dict[WebSocket, RelayConnection]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._send_timeout_seconds = max(0.1, send_timeout_seconds)

    async def add(self, device_id: str, websocket: WebSocket) -> RelayConnection:
        connection = RelayConnection(
            websocket,
            send_timeout_seconds=self._send_timeout_seconds,
        )
        async with self._lock:
            self._connections[device_id][websocket] = connection
        return connection

    async def remove(self, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            values = self._connections.get(device_id)
            if values is None:
                return
            values.pop(websocket, None)
            if not values:
                self._connections.pop(device_id, None)

    async def connected_device_ids(self) -> set[str]:
        async with self._lock:
            return {
                device_id
                for device_id, connections in self._connections.items()
                if connections
            }

    async def send(self, device_id: str, payload: dict[str, Any]) -> bool:
        async with self._lock:
            targets = list(self._connections.get(device_id, {}).values())

        async def deliver(connection: RelayConnection) -> bool:
            try:
                return await connection.send(payload)
            except Exception:
                await self.remove(device_id, connection.websocket)
                return False

        if not targets:
            return False
        return any(await asyncio.gather(*(deliver(target) for target in targets)))

    async def disconnect(self, device_id: str, *, code: int = 4403) -> None:
        async with self._lock:
            targets = list(self._connections.pop(device_id, {}).values())
        for connection in targets:
            try:
                await connection.websocket.close(code=code)
            except Exception:
                pass

    async def acknowledge(self, device_id: str, message_id: str) -> None:
        async with self._lock:
            targets = list(self._connections.get(device_id, {}).values())
        for connection in targets:
            await connection.acknowledge(message_id)


class RelayService:
    def __init__(self, settings: Settings, hub: ConnectionHub) -> None:
        self.settings = settings
        self.hub = hub
        self._delivery_tasks: set[asyncio.Task[bool]] = set()

    def _push_after_accept(self, target_id: str, payload: dict[str, Any]) -> None:
        """Best-effort live delivery must not delay durable acceptance.

        Once the envelope transaction commits, the sender may safely stop
        retrying. A slow target socket is independent: it can receive this live
        push later or recover the same envelope from the offline queue.
        """

        task = asyncio.create_task(self.hub.send(target_id, payload))
        self._delivery_tasks.add(task)

        def finished(completed: asyncio.Task[bool]) -> None:
            self._delivery_tasks.discard(completed)
            try:
                completed.result()
            except (asyncio.CancelledError, Exception):
                # The committed offline envelope remains the source of truth.
                pass

        task.add_done_callback(finished)

    @staticmethod
    def _wire(record: EnvelopeRecord) -> dict[str, Any]:
        return {
            "type": "envelope",
            "message_id": record.id,
            "sender_device_id": record.sender_device_id,
            "target_device_id": record.target_device_id,
            "sequence": record.sequence,
            "ciphertext": record.ciphertext,
            "created_at": record.created_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
        }

    async def pending(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = (
            await session.scalars(
                select(EnvelopeRecord)
                .where(
                    EnvelopeRecord.target_device_id == claims.device_id,
                    EnvelopeRecord.acknowledged.is_(False),
                    EnvelopeRecord.expires_at > utc_now(),
                )
                .order_by(EnvelopeRecord.created_at)
                .limit(min(max(1, limit), 250))
            )
        ).all()
        return [self._wire(record) for record in records]

    async def submit(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        payload: object,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RelayProtocolError("WebSocket message must be a JSON object")
        message_id = str(payload.get("message_id") or "").strip()
        target_id = str(payload.get("target_device_id") or "").strip()
        ciphertext = str(payload.get("ciphertext") or "")
        try:
            sequence = int(payload.get("sequence"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RelayProtocolError("sequence must be an integer") from exc
        if not message_id or len(message_id) > 128:
            raise RelayProtocolError("message_id is invalid")
        if sequence < 0 or sequence > 2**63 - 1:
            raise RelayProtocolError("sequence must not be negative")
        if (
            not target_id
            or len(target_id) > 64
            or target_id == claims.device_id
        ):
            raise RelayProtocolError("target_device_id is invalid")
        if not ciphertext or len(ciphertext.encode("utf-8")) > self.settings.max_ciphertext_bytes:
            raise RelayProtocolError("ciphertext is empty or too large")
        try:
            base64.b64decode(ciphertext, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RelayProtocolError("ciphertext is not valid base64") from exc
        lock_ids = relay_device_lock_order(claims.device_id, target_id)
        devices = (
            await session.scalars(
                select(DeviceRecord)
                .where(DeviceRecord.id.in_(lock_ids))
                .order_by(DeviceRecord.id)
                .with_for_update()
            )
        ).all()
        devices_by_id = {device.id: device for device in devices}
        sender = devices_by_id.get(claims.device_id)
        target = devices_by_id.get(target_id)
        user = await session.get(UserRecord, claims.user_id)
        if (
            sender is None
            or sender.status != "active"
            or sender.user_id != claims.user_id
            or sender.authorization_generation != claims.authorization_generation
            or user is None
            or user.status != "approved"
            or target is None
            or target.status != "active"
            or target.user_id != claims.user_id
        ):
            raise AuthenticationError("Relay devices are not active for this user")
        existing = await session.get(EnvelopeRecord, message_id)
        if existing is not None:
            if (
                existing.sender_device_id != claims.device_id
                or existing.target_device_id != target_id
                or existing.sequence != sequence
                or existing.ciphertext != ciphertext
            ):
                raise RelayProtocolError("message_id or sequence was already used")
            # Release the deterministic device locks before a possibly slow
            # best-effort push to the target's current socket.
            await session.commit()
            if not existing.acknowledged:
                self._push_after_accept(target_id, self._wire(existing))
            return {"type": "accepted", "message_id": existing.id}

        now = utc_now()
        pending_count, pending_bytes = (
            await session.execute(
                select(
                    func.count(EnvelopeRecord.id),
                    func.coalesce(func.sum(func.length(EnvelopeRecord.ciphertext)), 0),
                ).where(
                    EnvelopeRecord.sender_device_id == claims.device_id,
                    EnvelopeRecord.acknowledged.is_(False),
                    EnvelopeRecord.expires_at > now,
                )
            )
        ).one()
        encoded_size = len(ciphertext)
        if (
            int(pending_count or 0)
            >= self.settings.max_pending_envelopes_per_device
            or int(pending_bytes or 0) + encoded_size
            > self.settings.max_pending_bytes_per_device
        ):
            raise RelayProtocolError(
                "Relay offline queue is full; reconnect the target device before retrying"
            )
        record = EnvelopeRecord(
            id=message_id,
            user_id=claims.user_id,
            sender_device_id=claims.device_id,
            target_device_id=target_id,
            sequence=sequence,
            ciphertext=ciphertext,
            expires_at=now
            + dt.timedelta(seconds=self.settings.offline_envelope_seconds),
        )
        session.add(record)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.get(EnvelopeRecord, message_id)
            if (
                existing is None
                or existing.sender_device_id != claims.device_id
                or existing.target_device_id != target_id
                or existing.sequence != sequence
                or existing.ciphertext != ciphertext
            ):
                raise RelayProtocolError(
                    "message_id or sequence was already used"
                )
            record = existing
        if not record.acknowledged:
            self._push_after_accept(target_id, self._wire(record))
        return {"type": "accepted", "message_id": record.id}

    async def acknowledge(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        message_id: str,
    ) -> dict[str, Any]:
        record = await session.get(EnvelopeRecord, message_id)
        if record is None or record.target_device_id != claims.device_id:
            raise AuthenticationError("Envelope is not addressed to this device")
        record.acknowledged = True
        await session.commit()
        return {"type": "acknowledged", "message_id": message_id}
