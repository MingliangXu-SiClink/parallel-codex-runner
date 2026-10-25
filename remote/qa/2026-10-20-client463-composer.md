# macOS 0.1.463: Approved Composer

## Scope and Acceptance

The user accepted the native previews recorded in
`2026-10-20-composer-redesign-preview.md`. The exact approved production and test
source hashes were checked before testing, signing and publication. Only the
bundle version was added after visual acceptance. The delta from 0.1.462 is
limited to four composer/presentation source files, three native test files,
the client contract script and `Info.plist`.

The footer has one primary action, switching between send, queue and stop under
the existing submission plan. It no longer shows a separate secondary stop
button. The queue glyph reuses the existing licensed linear list-end icon;
submission, continuation and queue delivery policies are unchanged. The compact
configuration dropdown retains both counts and complete model names on one
line, beside the action. Its independent upward editor has no title/close row.

## Validation and Publication

- Frozen-source protocol, desktop-state and app-icon gates passed.
- The full native run passed **644 tests in 60 suites** (366.230 seconds).
- Strict focused coverage passed **65 tests in 10 suites** (13.443 seconds),
  including composer states/geometry, native configuration, IME/drafts, queue
  presentation, draft publications, lifecycle and update verification.
- Existing offline `Parallel Codex Release Signing` identity was reused.
  Signing-store and explicitly-no-signing regressions passed. The signed bundle
  and archive passed strict deep native signature and packaged-resource checks.
  No identity, login Keychain, system trust or security setting was changed.
- The immutable signed archive was formally published once through the existing
  server catalog. The publisher/requirement matched the preceding catalog row
  and installed client. A real authenticated check from build **462** returned
  `available=true` for **463**. Its one-use-ticket download matched the published
  size and SHA-256. No ticket was persisted in the evidence.
- The installed client's non-activating `--pcr-prepare-update` entry point
  accepted the downloaded archive under its existing publisher rules. Every
  staged bundle-file hash matched the signed publication. This was preparation,
  not installation or a normal application launch.

Archive: `release/clients/macos/Parallel-Codex-macos-0.1.463.zip`.
Size: **14,815,476 bytes**.
SHA-256: `2216e72f1c86f44ebdb3f4a8368a7d70190dcdf4b68a7af6696c1eace65dc718`.

## Runtime Boundary

The installed application directory still reports **0.1.462**. The observed
client/daemon PIDs, start times and active runtime path were unchanged across
publication and preparation. No client quit/restart/install, daemon action,
Agent command, result adoption, research operation or policy change was issued.
The pcrd source/artifact and all other release components remain unchanged.

Visual acceptance concerns the approved native offscreen previews; pointer
acceptance in a loaded 463 client has not been performed. No claim is made here
about competition progress or Agent health from process continuity alone.

Private audit evidence:
`~/Library/Caches/ParallelCodexMaintenance/Composer463-20261020/`.
It includes the approved source snapshot, frozen test/build gates, exact bundle
hashes, catalog readbacks, authenticated download, native preparation result and
`verification-summary.json`.
