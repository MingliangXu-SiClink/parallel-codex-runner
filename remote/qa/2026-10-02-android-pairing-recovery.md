# Android Pairing Recovery, 2026-10-02

## Live Observations

- Relay 0.1.20 was healthy before and after publication. Its service PID remained
  886998 with the same September 30 start time; no relay or pcrd restart was needed.
- The recent refresh endpoint returned repeated 401 responses. Two newly created
  QR records were still pending, with no approving controller or completed claim.
  An already-paired desktop does not prove that a new phone QR approval completed.
- Two active Xiaomi device identities had no remaining refresh-session records.
  The actual phone's installed version and which stored identity it uses have not
  been confirmed. No physical phone was available through ADB.
- Production audits used a PostgreSQL READ ONLY transaction. No user/device trust,
  refresh tokens, private keys, research files, or running tasks were modified to
  work around authentication. Only the Android release catalog was published.

## Root Cause and Changes

- Android treated an existing login as a prerequisite for fresh QR authorization.
  An expired refresh credential could fail before `/v1/auth/pairing` was reached.
- Fresh QR approval is now independent of the old login. A usable same-relay login
  still preserves the account when adding another computer; a rejected old bearer
  is omitted for one QR retry. QR approval tokens and pairing proofs remain required.
- Pairing and credential refresh share one mutex. The new relay origin, credentials,
  pinned Mac key and pending-code removal commit together in encrypted preferences
  only after approval succeeds. Failed approval preserves the previous connection.
- HTTP requests and WebSocket handshakes capture origin and credentials together.
  Responses from an earlier pairing generation cannot refresh credentials or publish
  a stale authentication error after a successful re-pair.
- Re-pairing invalidates older UI error operations without clearing conversation,
  reader-position or queue state for the same account. Recovered authentication
  banners are cleared on successful pairing/device access, not merely hidden on failure.
- Session expiry, actual device authorization revocation and expired QR codes now
  have distinct Chinese and English messages. Revocation checks remain enforced.

## Verification

- Android JVM tests: 274 passed, zero failures/errors/skips, 29 suites.
- Native Android 35 ARM64 emulator tests: 12 passed. Eight use isolated real Android
  Keystore preferences and deterministic HTTP interceptors for QR recovery, account
  retention, failed approval/network preservation, cross-origin isolation, stale
  responses and pairing/refresh serialization. Four exercise persistent identity
  recovery against a real local HTTP relay, including client recreation and wrong-key
  rejection. No test QR authorization targeted the production relay.
- Relay suite: 51 tests run, 49 passed; two PostgreSQL row-lock tests skipped. Added
  an isolated server-contract regression verifying repair without an old bearer,
  unchanged phone/account identity, QR proof approval and invalid-code rejection.
- `lintDebug` succeeded: zero errors and 25 warnings. Dependency-update and API/KTX
  suggestions were not expanded into unrelated changes.
- Signed release APK build and signature verification succeeded. APK package/version
  inspection confirms `com.parallelcodexrunner.remote`, versionCode 270, versionName
  0.1.269. The new stale-request guard was also verified in the release DEX.
- ChatGPT Web diagnosis collaboration was attempted in the authorized embedded tab,
  but the account showed a feature/usage restriction. No consultation was claimed
  and no alternate browser/account was used to bypass the restriction.
- The owned emulator and local relay fixture were stopped after verification.

## Publication and Acceptance Boundary

- Published at 2026-10-02T12:35:11.735970+00:00 through `pcr-relay-admin publish-android`.
- Release: `android-release-e0bc03e9c0fa451d829418fbb8f2c79e`.
- APK SHA-256: `0ea248045d6b2e67ac0f9db43beea4e809293ab21907a1019a191c7d4641b44a`.
- Size: 7,994,229 bytes. Signing certificate SHA-256 remains
  `9423f5a661bd146f976eae2dc9c04eb3bd7942c213d230f77a27e0d568708cd7`.
- Read-only post-publication audit confirms catalog version 270 is available, the
  private artifact exists with mode 0600, and its digest and size match the catalog.
  No public/static APK download route was introduced. Temporary upload copies were removed.
- Current local signed APK: `apps/android/dist/Parallel Codex.apk`. The previous
  dist APK was preserved at `Library/Caches/ParallelCodexMaintenance/AndroidPairing-20261002/previous-dist.apk`
  under the user's home directory.
- Physical-phone installation and the user's actual connection are NOT verified.
  Awaiting the user's choice of signed-APK overwrite installation or USB installation.
  Do not uninstall the old app or clear its data: the update retains device identity
  and local records. A newer APK being published is not evidence of phone recovery.
