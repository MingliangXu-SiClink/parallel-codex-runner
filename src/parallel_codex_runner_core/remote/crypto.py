from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


CRYPTO_VERSION = 1
MAX_PLAINTEXT_BYTES = 1024 * 1024


class RemoteCryptoError(RuntimeError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RemoteCryptoError(f"{name} is not valid base64") from exc


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def create_pairing_secret() -> str:
    return _b64encode(os.urandom(32))


def _pairing_secret(value: str) -> bytes:
    decoded = _b64decode(value, "Pairing secret")
    if len(decoded) != 32:
        raise RemoteCryptoError("Pairing secret must contain 32 bytes")
    return decoded


def pairing_commitment(secret: str) -> str:
    return _b64encode(hashlib.sha256(_pairing_secret(secret)).digest())


def pairing_proof(
    secret: str,
    *,
    pairing_id: str,
    mac_device_id: str,
    mac_public_key: str,
    android_device_id: str,
    android_public_key: str,
) -> str:
    context = _canonical(
        {
            "android_device_id": android_device_id,
            "android_public_key": android_public_key,
            "mac_device_id": mac_device_id,
            "mac_public_key": mac_public_key,
            "pairing_id": pairing_id,
            "version": CRYPTO_VERSION,
        }
    )
    return _b64encode(hmac.new(_pairing_secret(secret), context, hashlib.sha256).digest())


def envelope_aad(
    *,
    message_id: str,
    sender_device_id: str,
    target_device_id: str,
    sequence: int,
) -> bytes:
    return _canonical(
        {
            "message_id": message_id,
            "sender_device_id": sender_device_id,
            "sequence": sequence,
            "target_device_id": target_device_id,
            "version": CRYPTO_VERSION,
        }
    )


@dataclass(frozen=True)
class DeviceKeyPair:
    private_key: X25519PrivateKey

    @classmethod
    def generate(cls) -> "DeviceKeyPair":
        return cls(X25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: Path) -> "DeviceKeyPair":
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            encoded = path.read_text(encoding="ascii").strip()
            raw = _b64decode(encoded, "Device private key")
            key = X25519PrivateKey.from_private_bytes(raw)
        except FileNotFoundError:
            key = X25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            encoded = _b64encode(raw)
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                encoded = path.read_text(encoding="ascii").strip()
                raw = _b64decode(encoded, "Device private key")
                key = X25519PrivateKey.from_private_bytes(raw)
            else:
                with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except (OSError, ValueError) as exc:
            raise RemoteCryptoError(f"Cannot load device key: {path}") from exc
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return cls(key)

    @property
    def public_key(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return _b64encode(raw)

    def _shared_key(self, peer_public_key: str) -> bytes:
        raw_peer = _b64decode(peer_public_key, "Peer public key")
        if len(raw_peer) != 32:
            raise RemoteCryptoError("Peer public key must contain 32 bytes")
        try:
            peer = X25519PublicKey.from_public_bytes(raw_peer)
            shared = self.private_key.exchange(peer)
        except ValueError as exc:
            raise RemoteCryptoError("Peer public key is invalid") from exc
        own = _b64decode(self.public_key, "Device public key")
        salt = hashes.Hash(hashes.SHA256())
        salt.update(b"pcr-remote-v1\0")
        for item in sorted((own, raw_peer)):
            salt.update(item)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.finalize(),
            info=b"PCR Remote envelope version 1",
        ).derive(shared)

    def device_login_proof(
        self, challenge: Mapping[str, Any], *,
        device_id: str, user_id: str, relay_url: str,
    ) -> str:
        fields = (
            "authorization_generation", "challenge_id", "device_id", "nonce",
            "public_key", "purpose", "relay_url", "server_public_key", "user_id", "version",
        )
        context = {name: challenge.get(name) for name in fields}
        generation = context["authorization_generation"]
        if (
            context["version"] != 1 or isinstance(context["version"], bool)
            or context["purpose"] != "session-bootstrap"
            or context["device_id"] != device_id or context["user_id"] != user_id
            or context["public_key"] != self.public_key
            or context["relay_url"] != relay_url.rstrip("/")
            or not isinstance(generation, int) or isinstance(generation, bool) or generation < 0
            or any(not isinstance(context[name], str) or not context[name] or len(context[name]) > 2048
                   for name in fields if name not in {"version", "authorization_generation"})
        ):
            raise RemoteCryptoError("Device sign-in challenge does not match this device")
        nonce = _b64decode(context["nonce"], "Device sign-in nonce")
        peer = _b64decode(context["server_public_key"], "Device sign-in public key")
        if len(nonce) != 32 or len(peer) != 32:
            raise RemoteCryptoError("Device sign-in challenge is invalid")
        try:
            shared = self.private_key.exchange(X25519PublicKey.from_public_bytes(peer))
        except ValueError as exc:
            raise RemoteCryptoError("Device sign-in public key is invalid") from exc
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=nonce,
            info=b"PCR Relay session bootstrap v1",
        ).derive(shared)
        return _b64encode(hmac.new(key, _canonical(context), hashlib.sha256).digest())

    def encrypt(
        self,
        peer_public_key: str,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        sender_device_id: str,
        target_device_id: str,
        sequence: int,
    ) -> str:
        plaintext = _canonical(payload)
        if len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise RemoteCryptoError("Encrypted payload is too large")
        nonce = os.urandom(12)
        aad = envelope_aad(
            message_id=message_id,
            sender_device_id=sender_device_id,
            target_device_id=target_device_id,
            sequence=sequence,
        )
        ciphertext = AESGCM(self._shared_key(peer_public_key)).encrypt(
            nonce,
            plaintext,
            aad,
        )
        wire = _canonical(
            {
                "ciphertext": _b64encode(ciphertext),
                "nonce": _b64encode(nonce),
                "version": CRYPTO_VERSION,
            }
        )
        return _b64encode(wire)

    def decrypt(
        self,
        peer_public_key: str,
        ciphertext: str,
        *,
        message_id: str,
        sender_device_id: str,
        target_device_id: str,
        sequence: int,
    ) -> dict[str, Any]:
        try:
            wire = json.loads(_b64decode(ciphertext, "Ciphertext envelope"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteCryptoError("Ciphertext envelope is invalid") from exc
        if not isinstance(wire, dict) or wire.get("version") != CRYPTO_VERSION:
            raise RemoteCryptoError("Ciphertext version is not supported")
        nonce = _b64decode(str(wire.get("nonce") or ""), "Ciphertext nonce")
        encrypted = _b64decode(
            str(wire.get("ciphertext") or ""),
            "Ciphertext body",
        )
        if len(nonce) != 12:
            raise RemoteCryptoError("Ciphertext nonce is invalid")
        aad = envelope_aad(
            message_id=message_id,
            sender_device_id=sender_device_id,
            target_device_id=target_device_id,
            sequence=sequence,
        )
        try:
            plaintext = AESGCM(self._shared_key(peer_public_key)).decrypt(
                nonce,
                encrypted,
                aad,
            )
        except (InvalidTag, ValueError) as exc:
            raise RemoteCryptoError("Ciphertext authentication failed") from exc
        try:
            value = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteCryptoError("Decrypted payload is invalid JSON") from exc
        if not isinstance(value, dict):
            raise RemoteCryptoError("Decrypted payload must be a JSON object")
        return value
