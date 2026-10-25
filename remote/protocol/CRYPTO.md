# PCR Remote envelope cryptography

This document is normative for protocol version 1. Paths, prompts, commands,
events, diffs, and responses are plaintext only on the paired Android and Mac.

## Device keys

Each device creates one X25519 private key and publishes the raw 32-byte public key
as standard padded Base64. Android wraps the private bytes with an Android Keystore
AES key. The development Mac daemon stores private bytes in an owner-only file.

## Pairing authentication

The Mac creates a random 32-byte pairing secret. The relay receives only
`Base64(SHA256(secret))`; the QR contains the secret, Mac device ID, and Mac public
key. A separate one-time QR token authenticates Android to the relay. Android pins
the Mac public key and then sends this proof:

```text
Base64(HMAC-SHA256(secret, canonical_pairing_context))
```

The context is compact JSON with `android_device_id`, `android_public_key`,
`mac_device_id`, `mac_public_key`, `pairing_id`, and `version`, sorted by key. The
Mac verifies the proof before trusting the Android public key or accepting relay
tokens. The relay can deny service but cannot substitute either paired key without
invalidating the proof or subsequent encrypted envelopes.

## Key derivation

For every peer pair:

1. Compute the 32-byte X25519 shared secret.
2. Sort the two raw public keys lexicographically as unsigned bytes.
3. Compute `salt = SHA256("pcr-remote-v1\0" || key[0] || key[1])`, where `\0`
   is one zero byte.
4. Derive 32 bytes with HKDF-SHA256, that salt, and info
   `PCR Remote envelope version 1`.

## Authenticated metadata

The AES-GCM additional authenticated data is compact UTF-8 JSON with keys in this
exact lexicographic order and no whitespace:

```json
{"message_id":"...","sender_device_id":"...","sequence":1,"target_device_id":"...","version":1}
```

Identifiers are relay-issued ASCII values. `sequence` is a non-negative signed
64-bit integer and is never reused by one sender device.

## Ciphertext field

Generate a random 12-byte nonce and encrypt compact UTF-8 JSON with AES-256-GCM.
Encode this compact object as UTF-8, then standard padded Base64:

```json
{"ciphertext":"BASE64_CIPHERTEXT_AND_TAG","nonce":"BASE64_NONCE","version":1}
```

The resulting Base64 string is the relay envelope's `ciphertext`. The relay stores
and forwards it without parsing. Its outer `message_id`, sender, target, and
sequence must match the authenticated metadata or decryption fails.

Version 1 authenticates static device identity but does not provide per-message
forward secrecy. Revoke and re-pair devices after suspected key compromise.

## Device Session Bootstrap, Version 1

This separate protocol proves possession of an already-authorized device key to
the relay. It does not change peer keys, envelope encryption, or sequence numbers.
It is not a signature protocol; X25519 is used only for key agreement.

1. The client posts its existing `device_id` to `/v1/auth/device/challenge` over
   verified HTTPS. The relay checks the approved account and active device, then
   creates an ephemeral X25519 key, a random 32-byte nonce, and a 120-second challenge.
2. The response contains `version=1`, `purpose="session-bootstrap"`, `challenge_id`,
   `device_id`, `user_id`, `authorization_generation`, the configured `relay_url`,
   trusted device `public_key`, `server_public_key`, `nonce`, and `expires_at`.
   The client must check its own device, account, key, origin, version, and purpose
   before answering. All key and nonce bytes use standard padded Base64.
3. Both sides compute the X25519 shared secret, then derive 32 bytes using
   HKDF-SHA256, nonce bytes as salt, and the ASCII info
   `PCR Relay session bootstrap v1`.
4. The proof is `Base64(HMAC-SHA256(derived_key, context))`. Context is compact,
   UTF-8 JSON with every response field except `expires_at`, sorted by key, without
   ASCII escaping. Server database time is authoritative for expiration.
5. The client posts `device_id`, `challenge_id`, and `proof` to `/v1/auth/device`.
   The relay rechecks account, device, authorized key, and authorization generation
   under row locks. A conditional single-use write and issuing new access/refresh
   credentials commit in the same transaction. Revocation advances the generation.

The relay retains only a digest of the expected proof, not its ephemeral private
key or plaintext session credentials. At most eight challenges per device may
exist in a 120-second window, including consumed challenges. Concurrent clients
can hold separate challenges without invalidating each other. A lost response
uses a new challenge on retry; replaying an already-consumed proof cannot issue
another session. Existing valid refresh sessions are not revoked by bootstrap.
An incorrect proof invalidates that challenge without issuing credentials. New
access tokens carry the authorization generation, which is checked against the
current device on HTTP and WebSocket authorization; legacy tokens default to the
original generation zero and cannot regain access after a revoke/re-pair cycle.

No device trust is created or reactivated here. Unknown, revoked, account-disabled,
or mismatched-key devices must not obtain credentials. Expired refresh records may
be pruned without deleting device authorization. Network errors never erase the
client's pairing. The public, synthetic vector in
[`device-login-v1-vector.json`](device-login-v1-vector.json) is shared by server,
Python-client, and Kotlin-client tests; it contains no production keys.
