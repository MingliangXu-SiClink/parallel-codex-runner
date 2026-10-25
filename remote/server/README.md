# PCR Remote Relay

The relay authenticates one-time pairing codes, pairs Android and Mac devices,
and forwards opaque encrypted envelopes over WebSocket. It never executes Codex
and does not receive workspace source files. A separate authenticated feedback
inbox stores only user-approved descriptions, limited diagnostics, and screenshots.

## Development

```bash
python3 -m pip install -e .

export PCR_RELAY_ENV=development
export PCR_RELAY_JWT_SECRET="$(openssl rand -hex 32)"
export PCR_RELAY_MAC_ENROLLMENT_TOKEN="$(openssl rand -hex 32)"

pcr-relay --host 127.0.0.1 --port 8765
```

Development defaults to SQLite. Production refuses to start unless
`PCR_RELAY_DATABASE_URL` uses PostgreSQL and `PCR_RELAY_PUBLIC_BASE_URL` uses HTTPS.
Android authentication is bootstrapped by a short-lived, one-time token embedded
in the Mac pairing QR code; no external identity provider is required.

To remove all access for an account, revoke it locally. This also revokes its
devices and refresh tokens:

```bash
pcr-relay-admin revoke user-...
```

Production does not create tables during application startup. Apply the checked-in
schema before starting or upgrading the service:

```bash
pcr-relay-migrate
```

The migration command and all Alembic revisions are included in the server wheel;
a production host does not need a source checkout.

## Persistent Device Login

Pairing authorizes a device key, not a session token. Device authorization has no
inactivity expiry: a computer or phone that was offline for months can obtain new
session credentials without a new QR code, provided its private key and paired
identity remain intact and neither the account nor the device was revoked.

Access tokens remain short-lived. Refresh credentials retain a sliding 30-day
inactivity limit. If a refresh credential expires or its database row is pruned,
clients prove possession of the already-authorized X25519 key using the
single-use `/v1/auth/device/challenge` and `/v1/auth/device` flow. The server does
not accept an expired credential as proof of identity. macOS pcrd also maintains
its host and desktop-controller sessions separately while running.

Network failures preserve pairing and retry later; explicit revocation never
silently restores access. Uninstalling or clearing secure storage can remove the
private key and requires pairing again. See [the proof protocol](../protocol/CRYPTO.md)
and its shared Python/Kotlin test vector for the cryptographic details.

## Private Android updates

Android updates are deliberately not exposed through a static web directory. The
download route is fixed and may be known publicly, but an anonymous request cannot
retrieve an APK. Access requires all of the following:

1. a valid bearer token belonging to an active Android device;
2. an active Mac on the same paired account;
3. a five-minute, single-use ticket bound to that Android device, Mac, release, and
   APK digest. The ticket is sent in `X-PCR-Update-Ticket`, never in a URL.

The APK lives in an owner-only directory outside every web root; the database stores
only release metadata and its digest, not the APK bytes. The Android client verifies
the byte count, SHA-256 digest, package name, newer version code, and signing lineage
before handing it to Android's system installer. Installation still requires the
user's system confirmation.

Configure the private store and, for the strongest release policy, pin the current
APK signing-certificate SHA-256 fingerprint:

```bash
PCR_RELAY_ANDROID_UPDATE_DIR=/var/lib/pcr-relay/android-releases
PCR_RELAY_ANDROID_UPDATE_TICKET_SECONDS=300
PCR_RELAY_ANDROID_RELEASE_RETENTION=20
PCR_RELAY_ANDROID_PACKAGE_NAME=com.parallelcodexrunner.remote
PCR_RELAY_ANDROID_SIGNING_CERTIFICATE_SHA256=<64-hex-character-digest>
PCR_RELAY_MAX_CIPHERTEXT_BYTES=2097152
PCR_RELAY_MAX_PENDING_ENVELOPES_PER_DEVICE=512
PCR_RELAY_MAX_PENDING_BYTES_PER_DEVICE=134217728
```

The relay caps each encrypted envelope at 2 MiB and each sender's unacknowledged
queue at 512 envelopes or 128 MiB, whichever is reached first. Acknowledgements
release quota immediately. Keep these limits aligned with the clients' 1 MiB
plaintext command limit; authenticated encryption and Base64 increase the wire
size.

Build and sign the APK on a trusted machine, transfer it to a private staging path
on the relay host, and publish it locally. There is no HTTP upload endpoint:

```bash
pcr-relay-admin publish-android /private/staging/Parallel-Codex.apk \
  --version-code 4 \
  --version-name 0.1.3 \
  --release-notes "后台发送与网络恢复能力改进" \
  --signing-cert-sha256 <64-hex-character-digest>

pcr-relay-admin android-releases
pcr-relay-admin prune-android-releases
pcr-relay-admin revoke-android-release android-release-...
```

Run `pcr-relay-migrate` before publishing after an upgrade. Use one stable Android
signing key for every release; changing from a development key may require one
manual uninstall and reinstall before in-app updates can work. Publishing retires
APKs beyond `PCR_RELAY_ANDROID_RELEASE_RETENTION`; the explicit prune command applies
the same policy immediately after changing that setting.

## Private macOS updates

The macOS app uses the same private model instead of a public download page. An
active paired desktop identity checks the catalog, requests a five-minute one-use
ticket, and asks its local `pcrd` to stream the archive into an owner-only cache.
The app verifies the archive size and SHA-256 digest, bundle identifier, build
number, complete code signature, signing authority, and designated requirement
before staging an update for the user's explicit Restart action. The installer
normally exits the client, atomically replaces only its program bundle and
relaunches it in the background. It preserves user data and does not restart pcrd.

Configure a private directory outside every web root:

```bash
PCR_RELAY_MACOS_UPDATE_DIR=/var/lib/pcr-relay/macos-releases
PCR_RELAY_MACOS_UPDATE_TICKET_SECONDS=300
PCR_RELAY_MACOS_RELEASE_RETENTION=20
PCR_RELAY_MACOS_UPDATE_MAX_BYTES=536870912
PCR_RELAY_MACOS_BUNDLE_ID=com.parallelcodexrunner.mac
```

Build and sign the app on the trusted Mac, transfer the generated zip to a private
staging path, then publish its signing metadata:

```bash
pcr-relay-admin publish-macos /private/staging/Parallel-Codex-macos-0.1.447.zip \
  --build-number 447 \
  --version-name 0.1.447 \
  --release-notes "Decouples client exit from result adoption; restores signed updates." \
  --designated-requirement '<codesign designated requirement>' \
  --signing-authority 'Parallel Codex Release Signing'

pcr-relay-admin macos-releases
pcr-relay-admin prune-macos-releases
pcr-relay-admin revoke-macos-release macos-release-...
```

Building an archive is not server publication. Confirm the catalog entry and an
authenticated older-build check/download before calling a release available.
The updater still requires the running and downloaded apps to have the same
publisher authority and designated requirement. The explicitly authorized new
447 publisher requires a one-time client-only migration for old-publisher or
unsigned validation clients; it does not disable that check or add system trust.
This internal self-signed channel is not an Apple Developer ID/notarized release.

## Product feedback inbox

Android and macOS clients submit feedback directly to the Relay account rather
than storing it on one selected pcrd machine. A machine, workspace, run, and Agent
may be attached as optional diagnostic context, but they do not own the feedback.
Submissions are idempotent, private to the paired account, rate limited, and kept
when a related machine goes offline or is removed.

Screenshots are authenticated, content-checked, SHA-256 identified, and limited by
per-file, per-feedback, and count quotas. Clients do not attach prompts, Codex
conversation text, source files, credentials, tokens, or complete local paths by
default.

The developer Inbox is available only from the server administration command:

```bash
pcr-relay-admin feedback --status submitted --limit 100
pcr-relay-admin feedback-show feedback-...
pcr-relay-admin feedback-update feedback-... --status in_progress \
  --reply "We reproduced this issue."
pcr-relay-admin feedback-update feedback-... --status fixed \
  --fixed-version 0.1.111 --reply "Fixed in the latest Android update."
pcr-relay-admin feedback-attachment feedback-... attachment-... \
  --output /private/review/screenshot.jpg
```

There is no anonymous upload route and no automatic GitHub Issue creation.

## Routes

- `POST /v1/auth/pairing`: authenticate an Android public key with the one-time QR
  authorization token.
- `POST /v1/auth/refresh`: renew access and the device credential's inactivity
  deadline. Active devices retain their stable refresh token; an expired token,
  revoked device or inactive account cannot renew it. With the default policy,
  thirty days without a successful refresh requires fresh authorization.
- `POST /v1/mac/enroll`: create a short-lived QR pairing challenge using the server
  enrollment token and a one-way commitment supplied by the Mac.
- `POST /v1/pairings/{id}/approve`: bind the Mac to the authenticated Android device.
- `POST /v1/pairings/{id}/poll`: let the Mac obtain its device tokens.
- `GET /v1/devices`: list this user's paired devices and public keys.
- `DELETE /v1/devices/{id}`: revoke a device and all of its refresh tokens.
- `POST /v1/app-updates/android/check`: return release metadata to an authenticated
  Android device with an active paired Mac.
- `POST /v1/app-updates/android/tickets`: issue a short-lived, one-use,
  device-bound download ticket.
- `POST /v1/app-updates/android/download`: stream the private APK only when bearer
  authentication and the ticket header are both valid.
- `POST /v1/app-updates/macos/check`: return newer signed macOS release metadata to
  an authenticated desktop controller with an active paired computer.
- `POST /v1/app-updates/macos/tickets`: issue a short-lived, one-use macOS download
  ticket.
- `POST /v1/app-updates/macos/download`: stream the private signed app archive only
  when bearer authentication and the ticket header are both valid.
- `POST /v1/feedback`: create or idempotently recover a product feedback record.
- `GET /v1/feedback`: page through the authenticated account's feedback.
- `POST /v1/feedback/{id}/notes`: add an idempotent user follow-up.
- `POST /v1/feedback/{id}/attachments`: add a validated screenshot.
- `POST /v1/feedback/{id}/withdraw`: withdraw feedback that has not been reviewed.
- `WS /v1/relay`: store and forward opaque, expiring, replay-resistant envelopes.

The WebSocket accepts only ciphertext. Android and Mac derive a device-to-device
key with X25519 and authenticate each command envelope with AES-GCM. FRP and TLS do
not replace that layer. The relay never receives the QR pairing secret; it carries
an HMAC proof that lets the Mac authenticate the Android public key.
