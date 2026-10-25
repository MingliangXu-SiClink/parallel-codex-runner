from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


PROTOCOL_VERSION = 1
MINIMUM_CLIENT_PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)
MAX_MESSAGE_BYTES = 1024 * 1024
# Leave room for the relay's authenticated response wrapper and future protocol
# metadata. Native clients can request the next page when a result is larger.
MAX_RESPONSE_BYTES = 896 * 1024
_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class ProtocolError(ValueError):
    """A request that cannot be safely decoded or dispatched."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def _identifier(value: object, name: str, *, generate: bool = False) -> str:
    text = str(value or "").strip()
    if not text and generate:
        text = uuid.uuid4().hex
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        raise ProtocolError(
            "invalid_request",
            f"{name} must be 1-128 URL-safe characters",
        )
    return text


@dataclass(frozen=True)
class RemoteRequest:
    """One idempotency-aware PCR command sent by a trusted client."""

    action: str
    body: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    command_id: str = ""
    client_id: str = "local"
    expected_revision: int | None = None
    protocol_version: int = PROTOCOL_VERSION
    sent_at: str = field(default_factory=utc_now)
    client_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.command_id:
            object.__setattr__(self, "command_id", self.request_id)

    @classmethod
    def from_mapping(cls, value: object) -> "RemoteRequest":
        if not isinstance(value, Mapping):
            raise ProtocolError("invalid_json", "Request must be a JSON object")
        try:
            version = int(value.get("protocol_version", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProtocolError(
                "invalid_request", "protocol_version must be an integer"
            ) from exc
        if version != PROTOCOL_VERSION:
            raise ProtocolError(
                "unsupported_protocol",
                f"Protocol version {version} is not supported",
                details={
                    "current_version": PROTOCOL_VERSION,
                    "minimum_client_version": MINIMUM_CLIENT_PROTOCOL_VERSION,
                    "supported_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                },
            )
        action = str(value.get("action") or "").strip()
        if not _ACTION_PATTERN.fullmatch(action):
            raise ProtocolError(
                "invalid_request",
                "action must use a namespaced lower-case name",
            )
        raw_body = value.get("body", {})
        if not isinstance(raw_body, Mapping):
            raise ProtocolError("invalid_request", "body must be a JSON object")
        expected_revision = value.get("expected_revision")
        if expected_revision is not None:
            if isinstance(expected_revision, bool):
                raise ProtocolError(
                    "invalid_request", "expected_revision must be an integer"
                )
            try:
                expected_revision = int(expected_revision)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProtocolError(
                    "invalid_request", "expected_revision must be an integer"
                ) from exc
            if expected_revision < 0:
                raise ProtocolError(
                    "invalid_request", "expected_revision must not be negative"
                )
        capabilities = value.get("client_capabilities", [])
        if (not isinstance(capabilities, list) or len(capabilities) > 16
            or any(not isinstance(item, str) or not _IDENTIFIER_PATTERN.fullmatch(item) for item in capabilities)):
            raise ProtocolError("invalid_request", "client_capabilities must contain at most 16 capability names")
        return cls(
            action=action,
            body=dict(raw_body),
            request_id=_identifier(value.get("request_id"), "request_id", generate=True),
            command_id=_identifier(
                value.get("command_id") or value.get("request_id"),
                "command_id",
                generate=True,
            ),
            client_id=_identifier(value.get("client_id") or "local", "client_id"),
            expected_revision=expected_revision,
            protocol_version=version,
            sent_at=str(value.get("sent_at") or utc_now()),
            client_capabilities=tuple(dict.fromkeys(capabilities)),
        )

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "kind": "request",
            "request_id": self.request_id,
            "command_id": self.command_id,
            "client_id": self.client_id,
            "sent_at": self.sent_at,
            "action": self.action,
            "body": self.body,
        }
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.client_capabilities:
            payload["client_capabilities"] = list(self.client_capabilities)
        return payload


def decode_message(raw: bytes | str) -> dict[str, Any]:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            "message_too_large",
            f"Message exceeds the {MAX_MESSAGE_BYTES}-byte limit",
        )
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid_json", "Message is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("invalid_json", "Message must be a JSON object")
    return value


def encode_message(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            "encoding_failed", "Response contains a non-JSON value"
        ) from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            "message_too_large",
            f"Message exceeds the {MAX_MESSAGE_BYTES}-byte limit",
        )
    return encoded + b"\n"


def success_response(
    request: RemoteRequest,
    body: Mapping[str, Any],
    *,
    revision: int | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "response",
        "request_id": request.request_id,
        "command_id": request.command_id,
        "sent_at": utc_now(),
        "ok": True,
        "body": dict(body),
    }
    if revision is not None:
        response["revision"] = revision
    return response


def error_response(
    request_id: str,
    code: str,
    message: str,
    *,
    command_id: str = "",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "response",
        "request_id": request_id,
        "command_id": command_id or request_id,
        "sent_at": utc_now(),
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
        },
    }


def bounded_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """Replace an oversized result with a correlated protocol error."""

    response = dict(value)
    try:
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        request_id = str(response.get("request_id") or "unknown")
        return error_response(
            request_id,
            "encoding_failed",
            "This response contains a value that cannot be encoded as JSON.",
            command_id=str(response.get("command_id") or request_id),
        )
    if len(encoded) <= MAX_RESPONSE_BYTES:
        return response
    request_id = str(response.get("request_id") or "unknown")
    return error_response(
        request_id,
        "response_too_large",
        "This response is too large. Retry with a smaller page size.",
        command_id=str(response.get("command_id") or request_id),
        details={"maximum_response_bytes": MAX_RESPONSE_BYTES},
    )
