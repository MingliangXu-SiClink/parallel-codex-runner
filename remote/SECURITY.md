# PCR Remote Security Notes

## Deliberate broad filesystem access

A paired phone is allowed to browse filenames and directory structure anywhere the
Mac daemon's user can read, then use any directory as a PCR workspace. This is a
core product capability, not an accidental fallback.

Pairing a device therefore grants powerful access. Depending on the selected
workspace, Codex may read project files and create isolated copies containing their
contents. Only pair devices you control, protect the Android lock screen, and revoke
lost devices immediately.

PCR does not bypass macOS permissions. TCC-protected locations remain unavailable
unless the user explicitly grants the Mac helper the corresponding permission or
Full Disk Access. The app must explain that grant before opening System Settings;
it must never loop on permission prompts.

## Implemented controls

- QR pairing uses a short-lived, single-use authorization token and independently
  confirms both device public keys.
- Server enrollment settings are provisioned into an owner-only local file. The
  enrollment credential is never embedded in the Mac app, shown in its UI, or
  included in the QR code.
- Android wraps its X25519 private key and tokens with an Android Keystore key. The
  Mac private key is stored in the daemon's owner-only state directory with mode
  `0600`.
- Prompts, Agent output, paths, diffs, and commands are end-to-end encrypted between
  Android and Mac. The relay stores ciphertext only.
- Envelopes are authenticated with AES-GCM, expire, carry a replay-resistant
  sequence, and use durable `command_id` deduplication on the Mac.
- The server never receives the local Unix-socket token. That token is owner-only
  and authenticates local clients only.
- Remote actions are an explicit controller allowlist. There is no generic remote
  shell command.
- Workspace file links are served only from the selected registered workspace or
  selected Agent copy. Path traversal, escaping symlinks, non-regular files, files
  larger than 256 MiB, and files that change during transfer are rejected. File pages
  remain end-to-end encrypted and are never published as web downloads.
- Logs exclude prompts, source, diffs, access tokens, pairing tokens, and
  plaintext paths unless local diagnostics are explicitly enabled.
- Android APKs are stored outside every web root. Update checks require an active
  Android device and an active Mac on the same paired account. Downloads additionally
  require a short-lived, single-use ticket bound to both devices, the release, and
  its digest; the ticket is carried in a header rather than a logged URL.
- The Android client rejects an update unless its size, SHA-256 digest, package,
  newer version code, and signing lineage are valid. Android's system installer
  remains the final user-confirmed authority.

## Ongoing release checks

- Keep the independent `pcrd` identity in its owner-only service state and the
  macOS controller identity in protected application storage. The GUI must never
  own or restart the machine service as part of its normal lifecycle.
- Preserve explicit confirmation for finalize, discard, device revocation, and
  destructive workspace/run actions.
- Keep Android background reconnect and completion tracking covered by regression
  tests; daemon-owned macOS completion notifications must work while the GUI is
  closed.
- Continue independent protocol, mobile storage, server, and deployment review,
  including expired and replayed QR authorization attempts.
- Keep one production Android signing key in protected release infrastructure,
  publish APKs only through the relay's local admin command, and test key rotation
  before changing that key. The relay retains only a bounded number of downloadable
  APKs while preserving revoked release metadata for operator audit.

## Relay and FRP

The relay binds to loopback inside WSL and is exposed through an FRP HTTPS proxy.
FRP is transport, not authentication. TLS terminates only at a component covered by
the deployment threat model, and application envelopes remain end-to-end encrypted.

Do not replace or restart the existing FRP client until the public `frps` version,
configuration, and a temporary recovery route have been verified. Losing the only
SSH proxy while migrating the service would also remove the recovery path.

The WSL service runs as a dedicated non-root user under systemd. A Windows startup
task wakes WSL after reboot, and both relay and FRP units use restart policies and
health checks. Windows sleep still makes the endpoint unavailable and must be
handled by client reconnect/backoff rather than treated as command failure.
