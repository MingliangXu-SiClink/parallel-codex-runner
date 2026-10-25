# Conversation continuity release, 2026-09-29

## Scope

Keep multi-candidate execution, synthesis-first recommendation and user overrides
at dispatch. Improve continuity rather than replacing the existing state or UI
frameworks. Preserve the wide transcript, shorter composer, adjustable sidebar,
and persistent result inspector.

The ongoing [product-design dialogue](https://chatgpt.com/c/6abbb5f0-d954-83e9-8da6-4c152c7acddf)
was used to challenge queue receipts, recovery evidence and deployment assumptions.
Its suggestions were checked against the implementation, not treated as proof.
The packaging fix also follows Apple's [signed bundle layout](https://developer.apple.com/library/archive/technotes/tn2206/):
resources stay inside Contents rather than adding aliases at the app root.

## Implemented

- macOS and Android accept durable follow-ups while the preceding result is
  being saved. Accepting a message does not bypass server execution dependencies.
- Delayed queue snapshots are version checked and merged with unacknowledged
  local messages. Acknowledgement is processed separately from snapshot age.
- macOS serializes transport while accepting consecutive local submissions.
  Android persists the outbox before clearing the submitted draft, and rejects
  superseded asynchronous state saves. Network callbacks do not clear new drafts.
- Failed submissions update only their own item. Other messages and current
  drafts are not rolled back. Background Android reconciliation preserves other
  conversations and newly submitted messages.
- An idempotent enqueue replay returns the current queue, not the obsolete
  snapshot saved with its first acknowledgement. A late second message in the
  same conversation follows the advanced run instead of returning to the old one.
- Automatic interrupted-save verification checks a pre-write receipt containing
  directory identity, complete file hashes, modes, links, deletions and Git
  HEAD/index/branch. It does not copy files. Verification holds the existing source
  workspace lease and only confirms the corresponding ambiguous finalization.
  Git receipt reads disable optional locks, fsmonitor and lazy fetching.
- Candidate browsing remains available during mutations. macOS avoids publishing
  unchanged queue snapshots. Android no longer inserts a full-width busy strip
  for unrelated run mutations. Queue receipt status is accessible per item.
- Installed macOS apps resolve math fonts and syntax-highlighting resources from
  Contents/Resources, never the build machine's source directory. Both SwiftMath
  font-loading paths are covered. Highlighter remains at 3.1.0, with the resource
  lookup patch and upstream license retained in the vendored copy.
- Service configuration preserves the venv interpreter rather than resolving its
  symlink back to the shared Python environment. The bundled installer creates a
  versioned, isolated runtime; its bootstrap skips unrelated Python startup hooks.

## Automated verification

- Backend full run before final hardening: 785 tests and 58 subtests passed.
- Final focused backend run: 307 tests and 35 subtests passed. Includes queue
  replay, late enqueue after dispatch, source-workspace locking, missing or
  changed receipts, file-mode/deletion checks, Git-hook suppression, and recovery
  without repeating synchronization.
- macOS: protocol, bootstrap and static interaction contracts passed; the final
  resource-fix run passed 206 native tests in 29 suites. App icon validation passed. One earlier animation timing assertion
  failed while builds were running; the complete rerun passed, without weakening
  or deleting that assertion.
- Final service, installer and release checks: 142 tests and 15 subtests passed.
- The signed .app renders formula attachments and highlighted Swift text in both
  themes in a separate-process smoke test. It also passes with the entire source
  repository denied by an OS sandbox. Removing either resource bundle from a
  re-signed test copy fails explicitly, even with development resources present.
  Archive validation now runs this content test with an external 20-second timeout.
- Android: 267 unit tests and 9 instrumentation tests passed on Android 15,
  emulator-5554. Final release APK installed successfully with the existing key.

## Native walkthrough

The isolated macOS app used the release executable with a separate bundle ID and
HOME. Its fixture uses the real IPC/controller/queue store and deterministic
candidate data; it never starts paid model runs.

- With six-second enqueue responses, submitted A and B without waiting, then
  typed draft C. Both server items remained in order exactly once; C survived.
- Rejected a third submission, then typed draft D. The failed item stayed editable
  with its own paused error; D survived the failure callback.
- Entered finalizing, submitted another follow-up, typed draft E, and switched
  Agent. The server accepted the message; E remained unchanged.
- Expanded and scrolled the bounded work process and opened result details.
- Quit and reopened with the final release executable. Three acknowledged items,
  the one paused local item, draft E, viewed Agent, inspector, and the exact
  transcript scroll fraction (0.4611338398597312) were restored.
- Android release: launched through adb, loaded an existing real conversation
  through the Relay, scrolled, used system Back, force-stopped and reopened.
  No physical phone was operated and no production prompt was submitted.
- Final installed macOS 0.1.388: opened the existing 37-turn conversation that had
  blocked .387, rendered its math-heavy answer, switched between a candidate and
  the original synthesis result, opened the real Markdown diff beside the result
  inspector, closed it and restored the original selection and bottom position.
  The main window remained responsive. Strict signing verification still passed
  after the walkthrough.

## Deployment

- macOS 0.1.388 is published in the Relay catalog, installed in /Applications and
  running. Installed and archived executables match. The intermediate .387
  release was withdrawn after its packaged-resource problem was reproduced.
- Android 0.1.267 is published for in-app updates. The signed release was installed
  and exercised on the emulator only; the user's physical phone was not changed.
- This Mac's pcrd 0.1.60 was restarted and reports healthy with a connected Relay.
  The deployed capability list includes runs.verify_finalization. No Agent was
  active at the scheduler restart; no model task or web process was stopped.
- Deployment exposed a shared-Python startup hang before pcrd code ran. The live
  LaunchAgent now uses the installed wheel in a dedicated runtime under
  ~/Library/Application Support/parallel-codex-runner/runtime/0.1.60, with the
  same dependency versions. Its site-packages contains no .pth files and does
  not inherit system site-packages. Worker import also succeeds in this runtime.
- Runtime creation happened at its final path; it was not copied or renamed into
  place. It still relies on this Mac's existing base Python installation.
- Final artifacts and matching checksums are also in the repository's release/
  directory. Relay code and other machines' pcrd services were not changed.

## Release artifacts

- macOS 0.1.388 (388), signed with Parallel Codex Local Signing.
- Android 0.1.267 (268), existing certificate SHA-256
  `9423f5a661bd146f976eae2dc9c04eb3bd7942c213d230f77a27e0d568708cd7`.
- pcrd 0.1.60. Relay remains 0.1.18; no Relay restart or schema change.
- APK SHA-256: `56adc6b66d794e3a4ae7df1c11d80fc3f4f53dc62954540d2dbb94a8ed141a30`.
- macOS archive SHA-256: `d77cd23cf2a3dfc7a3802b2633c48ada5d13aacea34cbb0a186f90368280ac7d`.
- pcrd wheel SHA-256: `5ffb841c8783f17e6e4cc57c67a6686ee99d1bb8015d95fae870d58a524ed0aa`.

Artifacts, source backup, old signed app/wheel, logs and emulator screenshot are
under `~/Library/Caches/ParallelCodexMaintenance/Continuity-20260929-RatzIm/`.

## Limitations

- Old runs without a recorded receipt cannot be automatically declared synced.
  Their pinned workers are not replaced. Manual recovery remains available.
- Verification establishes the recorded effects at check time, not a permanent
  filesystem snapshot. External programs do not participate in PCR's workspace
  lock; ignored runtime data is intentionally outside synchronization.
- Queue races were verified deterministically in backend/native tests and the
  macOS fixture. Android process death during an actual lost network receipt was
  not exercised end to end on a physical device.
- Emulator cold launches measured 691 ms and 361 ms in these two runs. macOS QA
  idle sampling was about 121 MiB RSS and 0.5% CPU. These are observations, not a
  comparative benchmark or evidence of a quantified performance improvement.
