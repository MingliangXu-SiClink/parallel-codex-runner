# Internal Release Bundle

`release/` is the single staging directory for locally built Parallel Codex
artifacts. Run this from the repository root:

```bash
python3 scripts/build-release.py
```

The generated bundle is arranged by the software that consumes each artifact:

- `clients/`: Android APK and signed macOS app archive.
- `pcrd/`: the machine-service installer and its PCR wheel for macOS or Linux.
- `server/`: the relay-server wheel for the project operator.
- `manifest.json`: component versions plus the path, size, and SHA-256 digest of
  every installable artifact in this bundle.

Artifact versions remain visible in their filenames. The directory intentionally
contains only files that a user or operator installs, plus this short guide.

Install `pcrd` without putting the enrollment token in shell history:

```bash
cd release/pcrd
PCR_RELAY_MACHINE_ENROLLMENT_TOKEN='your-token' \
  ./install-pcrd.sh 'https://your-relay.example'
```

When run interactively without the environment variable, the installer asks for the
token with terminal echo disabled. Automated installation must use the environment
variable.

Generated files are intentionally ignored by Git. Both Android and macOS updates
must also be published through their authenticated Relay release catalogs; creating
an archive or updating this local manifest does not publish it to the server.
Nothing in this directory is a public download endpoint.

For macOS, use the existing `pcr-relay-admin publish-macos` operator workflow in
`remote/server/README.md`, with signing metadata read from the final verified ZIP.
Publication is complete only after catalog readback and an authenticated older-build
check/download agree on version, publisher, size and SHA-256. Installation is a
separate gate: verify the actually loaded client bytes, preserved drafts/task state,
and a fresh authenticated update check. Never label a local validation package as
a server release or relax verification to cross publishers.

The current signed release is **0.1.449 / build 449**. It uses the explicitly
authorized `Parallel Codex Release Signing` identity and an encrypted, offline
build-only store, not a Keychain password dialog. See `apps/macos/README.md` for the
signing setup and regression gates, and `clients/macos/README.md` for the actual
release record. Old-publisher/unsigned validation installations require a one-time
explicit client migration. This is internal publisher continuity, not Apple
Developer ID signing or notarization for public distribution.
