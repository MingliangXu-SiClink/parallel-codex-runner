from __future__ import annotations

import base64
import binascii
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PublicKeyModel(BaseModel):
    public_key: str

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("public_key must be base64") from exc
        if len(decoded) != 32:
            raise ValueError("public_key must contain 32 bytes")
        return value


def validate_32_byte_base64(value: str, name: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{name} must be base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must contain 32 bytes")
    return value


class WeChatLoginRequest(PublicKeyModel):
    code: str = Field(min_length=1, max_length=512)
    device_name: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class DeviceLoginChallengeRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)


class DeviceLoginChallengeResponse(PublicKeyModel):
    version: Literal[1] = 1
    purpose: Literal["session-bootstrap"] = "session-bootstrap"
    challenge_id: str
    device_id: str
    user_id: str
    authorization_generation: int = Field(ge=0)
    relay_url: str
    server_public_key: str
    nonce: str
    expires_at: str


class DeviceLoginRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    challenge_id: str = Field(min_length=1, max_length=64)
    proof: str

    @field_validator("proof")
    @classmethod
    def validate_proof(cls, value: str) -> str:
        return validate_32_byte_base64(value, "proof")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    device_id: str


class MacEnrollRequest(PublicKeyModel):
    device_name: str = Field(min_length=1, max_length=128)
    pairing_commitment: str
    approval_commitment: str

    @field_validator("pairing_commitment")
    @classmethod
    def validate_commitment(cls, value: str) -> str:
        return validate_32_byte_base64(value, "pairing_commitment")

    @field_validator("approval_commitment")
    @classmethod
    def validate_approval_commitment(cls, value: str) -> str:
        return validate_32_byte_base64(value, "approval_commitment")


class MacEnrollResponse(BaseModel):
    mac_device_id: str
    pairing_id: str
    poll_token: str
    expires_at: str


class PairingApproveRequest(BaseModel):
    pairing_proof: str
    approval_token: str = Field(min_length=32, max_length=512)

    @field_validator("pairing_proof")
    @classmethod
    def validate_proof(cls, value: str) -> str:
        return validate_32_byte_base64(value, "pairing_proof")


class PairingLoginRequest(PublicKeyModel):
    pairing_id: str = Field(min_length=1, max_length=128)
    approval_token: str = Field(min_length=32, max_length=512)
    device_name: str = Field(min_length=1, max_length=128)
    device_kind: Literal["android", "macos"] = "android"


class PairingPollRequest(BaseModel):
    poll_token: str = Field(min_length=16, max_length=512)


class PairingPollResponse(BaseModel):
    status: str
    tokens: TokenResponse | None = None
    android_device_id: str | None = None
    android_public_key: str | None = None
    pairing_proof: str | None = None


class AndroidUpdateCheckRequest(BaseModel):
    mac_device_id: str = Field(min_length=1, max_length=64)
    package_name: str = Field(min_length=3, max_length=255)
    current_version_code: int = Field(ge=0, le=2**63 - 1)


class AndroidUpdateInfo(BaseModel):
    release_id: str
    package_name: str
    version_code: int
    version_name: str
    release_notes: str = ""
    sha256: str
    size_bytes: int
    signing_cert_sha256: str | None = None
    published_at: str
    available: bool = True


class AndroidUpdateCheckResponse(BaseModel):
    available: bool
    update: AndroidUpdateInfo | None = None


class AndroidUpdateHistoryResponse(BaseModel):
    cursor: int
    next_cursor: int
    has_more: bool
    releases: list[AndroidUpdateInfo]


class AndroidUpdateTicketRequest(BaseModel):
    mac_device_id: str = Field(min_length=1, max_length=64)
    release_id: str = Field(min_length=1, max_length=64)


class AndroidUpdateTicketResponse(BaseModel):
    release: AndroidUpdateInfo
    ticket: str
    expires_at: str
    download_path: str


class MacOSUpdateCheckRequest(BaseModel):
    mac_device_id: str = Field(min_length=1, max_length=64)
    bundle_id: str = Field(min_length=3, max_length=255)
    current_build_number: int = Field(ge=0, le=2**63 - 1)


class MacOSUpdateInfo(BaseModel):
    release_id: str
    bundle_id: str
    build_number: int
    version_name: str
    release_notes: str = ""
    sha256: str
    size_bytes: int
    designated_requirement: str
    signing_authority: str
    published_at: str


class MacOSUpdateCheckResponse(BaseModel):
    available: bool
    update: MacOSUpdateInfo | None = None


class MacOSUpdateTicketRequest(BaseModel):
    mac_device_id: str = Field(min_length=1, max_length=64)
    release_id: str = Field(min_length=1, max_length=64)


class MacOSUpdateTicketResponse(BaseModel):
    release: MacOSUpdateInfo
    ticket: str
    expires_at: str
    download_path: str


FeedbackDiagnosticValue = str | int | float | bool


class FeedbackSubmitRequest(BaseModel):
    client_feedback_id: str = Field(min_length=1, max_length=128)
    kind: Literal["bug", "feature", "experience"]
    description: str = Field(min_length=1, max_length=8_000)
    diagnostics: dict[str, FeedbackDiagnosticValue] = Field(default_factory=dict)
    related_device_id: str | None = Field(default=None, max_length=64)


class FeedbackNoteRequest(BaseModel):
    client_note_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4_000)


class FeedbackAttachmentRequest(BaseModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: str = Field(min_length=1, max_length=2_000_000)


class FeedbackAttachmentInfo(BaseModel):
    attachment_id: str
    content_type: str
    size_bytes: int
    sha256: str


class FeedbackNoteInfo(BaseModel):
    note_id: str
    author: str
    text: str
    created_at: str


class FeedbackActivityInfo(BaseModel):
    activity_id: str
    event_type: str
    actor: str
    status: str = ""
    text: str = ""
    version: str = ""
    created_at: str


class FeedbackInfo(BaseModel):
    feedback_id: str
    kind: str
    description: str
    status: str
    diagnostics: dict[str, FeedbackDiagnosticValue] = Field(default_factory=dict)
    related_device_id: str = ""
    attachments: list[FeedbackAttachmentInfo] = Field(default_factory=list)
    notes: list[FeedbackNoteInfo] = Field(default_factory=list)
    activity: list[FeedbackActivityInfo] = Field(default_factory=list)
    developer_reply: str = ""
    fixed_version: str = ""
    created_at: str
    updated_at: str
    withdrawn_at: str = ""


class FeedbackResponse(BaseModel):
    feedback: FeedbackInfo


class FeedbackListResponse(BaseModel):
    items: list[FeedbackInfo] = Field(default_factory=list)
    next_cursor: str = ""
    has_more: bool = False
