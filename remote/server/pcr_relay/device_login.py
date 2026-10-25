from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .schemas import DeviceLoginChallengeResponse


DEVICE_LOGIN_KEY_INFO = b"PCR Relay session bootstrap v1"


def challenge_proof(shared: bytes, challenge: DeviceLoginChallengeResponse) -> str:
    transcript = json.dumps(
        challenge.model_dump(exclude={"expires_at"}),
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    key = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=base64.b64decode(challenge.nonce, validate=True),
        info=DEVICE_LOGIN_KEY_INFO,
    ).derive(shared)
    return base64.b64encode(hmac.new(key, transcript, hashlib.sha256).digest()).decode("ascii")


def create_device_challenge(
    *, challenge_id: str, device_id: str, user_id: str, public_key: str,
    authorization_generation: int, relay_url: str, expires_at: str,
) -> tuple[DeviceLoginChallengeResponse, str]:
    ephemeral = X25519PrivateKey.generate()
    raw_public = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    # exchange() rejects low-order/all-zero shared secrets; the device key is
    # taken only from the previously authorized server record.
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(
        base64.b64decode(public_key, validate=True)
    ))
    challenge = DeviceLoginChallengeResponse(
        challenge_id=challenge_id, device_id=device_id, user_id=user_id,
        public_key=public_key, authorization_generation=authorization_generation,
        relay_url=relay_url.rstrip("/"),
        server_public_key=base64.b64encode(raw_public).decode("ascii"),
        nonce=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        expires_at=expires_at,
    )
    return challenge, challenge_proof(shared, challenge)
