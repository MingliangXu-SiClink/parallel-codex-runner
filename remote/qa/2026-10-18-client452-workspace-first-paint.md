# macOS 452: Workspace Cache Hydration and Entry Navigation

## Scope

Follow-up to the user's 451 screenshots: entering a workspace initially showed
only a user prompt, and another entry showed the newer-workspace-result blocker.
This is a client read-path repair, not a change to result adoption or admission.
No production prompt, continuation, adoption, retry, stop, or directory operation
is part of this change. The current manager runtime is resolved from LaunchAgent;
it is not upgraded or restarted for this release.

## Confirmed Reproductions

- A background history read could run before the transcript reader hydrated the
  disk cache. Its first history page created a warm record without the saved
  answer, then persisted that incomplete record over the complete disk page.
  The new cold-cache regression failed three assertions on the prior source.
- Workspace entry accepted an already-warm older conversation and returned
  without hydrating the exact current run from disk. A recently updated older
  conversation could win over the workspace's actual task. The isolated entry
  regression failed four assertions on the prior source, including destination,
  answer and history, while the global health response was held.
- Workspace bootstrap still assigned its prefetched run after an asynchronous
  index advance. Unlike explicit conversation navigation, that path lacked the
  navigation-generation/latest-target guard. A delayed-response regression now
  exercises this entry path specifically.

The screenshots do not provide a request trace; these are directly reproduced
code paths consistent with the symptoms, not a claim to have reconstructed the
exact timing of either screenshot.

## Changes

- Hydrate the run's disk record before any background history page can publish.
  Complete history skips an unnecessary request; incomplete history enrichment
  preserves the saved answer and identity.
- Fresh workspace entry resolves its registered current task before accepting
  an unrelated warm page, and loads that exact disk key. Explicit historical
  navigation, saved native-session reading and genuine admission blockers remain.
- Start destination transcript/history reads before global health refresh.
- Check navigation generation and latest-turn intent after bootstrap awaits;
  late replies cannot replace the newer destination or a user-selected page.

No footer text or result-selection rule was removed to make the symptom disappear.

## Validation and Delivery

Private timestamped evidence, source hashes, red/green test logs, native offscreen
screenshots and release receipts are in
`~/Library/Caches/ParallelCodexMaintenance/WorkspaceFirstPaint452-20261018/`.
Only the actual observed main window was used for the initial cache inspection;
legacy QA windows were not treated as production windows. Draft/message contents
are not copied into this report.

### Source and Test Gates

- The frozen source passed 601 native non-interactive tests in 58 suites
  (330.233 seconds), plus a 92-test focused run with the strict cooperative pool.
- Six added regressions cover complete cold-cache hydration, incomplete history
  enrichment retaining answers, warm older conversations versus the current disk
  turn, cold body reads while global health is held, bootstrap without an index,
  and a delayed bootstrap reply after the index advances. The workspace-entry
  and cold-read tests each exercise two variants.
- A hidden native surface rendered both saved history and the selected Agent's
  answer while global health remained blocked. `first-paint.png` was visually
  inspected. It is a synthetic regression capture, not a production screenshot.
- Protocol/source contracts, app-icon validation, offline signing-store tests,
  no-signing build behavior, signed build, strict deep signature validation and
  archive validation passed. Every release gate checks identical source hashes.
- Relative to the 451 publication, changes are limited to Info.plist, four
  AppModel source files and WorkspaceReadinessTests.swift. No backend runtime or
  competition instructions changed.

### Formal Server Publication

- Version/build: `0.1.452` / `452`.
- Published: `2026-10-18T04:23:17.588121+00:00`.
- Release ID: `macos-release-9f43b182785f4c65b2093df1afdb5616`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.452.zip`.
- Size: `14796553` bytes.
- SHA-256: `a350bb0c937cb6a0dc0760ec25cbac9bafef21d5c2e68640d3fcc755fc561048`.
- Publisher: the existing `Parallel Codex Release Signing` offline identity,
  with the same designated requirement as the installed client. No Keychain,
  trust, identity or updater-verification changes were made.
- The server catalog was reread after publication. The existing authenticated
  controller offered 452 to installed build 451; ticketed download matched the
  published hash and size. The ticket was not persisted.

### Native Updater and Live State

- Installed 451's native updater accepted the authenticated archive in
  preparation-only mode. Every staged bundle file hash matched the signed
  publication, and its signing metadata matched the installed publisher.
- The actual running client remains `0.1.451`, PID `62345`, with the same loaded
  inode and binary hash. No installation, app restart or foreground activation
  was attempted. Publication and preparation are not installation.
- pcrd remains `0.1.99`, PID `55700`. Its LaunchAgent definition, receipt-store
  identity, process start time, and the five observed owner PID/start-time pairs
  were unchanged. These are non-disruption observations, not a replacement for
  per-Agent native/tool/remote-compute maintenance evidence.
- Local health was `ok`; pairing remained present. Separately, the existing
  Relay connection's `connected` state and connection-start timestamp remained
  unchanged, and the authenticated update check/download succeeded.
- Only the actual main window's persisted state was inspected. Draft, outbox,
  accepted-handoff, queue and Agent/conversation-selection hashes were unchanged;
  the selected-workspace hash changed while the user continued using the app.
  An unidentified AX dialog in the earlier observation was no longer present
  afterward; it was not mapped to or substituted with a legacy QA state file.
  No user task command was submitted by this work.

Already cached answers and history are preserved and restored first. Truly
uncached content still requires a source read. Production acceptance after the
user installs 452 is not claimed by this release record.

The requested embedded-browser thinking partner was unavailable in this tool
session; no browser backend was substituted and no consultation is claimed.
