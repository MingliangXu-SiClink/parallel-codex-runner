# macOS 454: Native Count Menus

## Accepted Scope

The user approved the native small-arrow quantity preview and requested removal
of the redundant merging arrow beside the synthesis model. This supersedes the
unaccepted numeric-panel variants and the separate number-field/stepper surface
published in 453.

- Candidate and synthesis counts use actual borderless AppKit `NSPopUpButton`
  controls in their original header positions. Native menu choices apply
  immediately, without a separate confirmation checkmark.
- There is no additional count window, lower numeric section, preset grid,
  visible range label or permanently visible numeric input. Choosing Custom
  temporarily edits the number in the same fixed-size header slot. Invalid
  input restores the last valid value; existing backend bounds are unchanged.
- The inter-stage merging drawing is removed. The existing candidate/synthesis
  role icons remain in the headers. Narrow layouts no longer reserve a gutter
  for the deleted drawing.
- The main execution surface remains above the composer, inside the app window.
  Single-line summaries, full model names, synthesis locking, model/effort/Fast
  controls and context-validated edits retain their existing behavior.
- Native controls are retired on context changes. Dismantled controls lose their
  target/action, and delayed callbacks cannot modify another workspace or run.

## Verification

Private evidence:
`~/Library/Caches/ParallelCodexMaintenance/CountPopUp454-20261018/`.

- 605 non-interactive native tests in 58 suites passed (330.926 seconds).
- A separate strict cooperative-pool run passed 96 tests in 6 suites.
- Actual native menu selections and the Custom editing path were exercised in
  an isolated hidden-window fixture. Count edits do not grow the owning surface
  or create another application-owned configuration window. No confirmation
  action is required, and stale controls fail closed after context changes.
- Full four-digit values fit at all app text sizes in light/dark appearances;
  title rectangles are measured against the native font without truncation or
  font shrinking. Locked synthesis remains disabled and shows its actual count.
- Two-stage geometry, narrow/wide layouts, single-line summaries, draft/marked
  text, lifecycle, navigation and updater integrity regressions passed.
- Source/protocol contracts, app-icon checks, signing-store regressions and the
  disabled-signing gate passed on the same frozen source hashes.
- Offscreen native captures were inspected, including the user-approved resting
  configuration with the redundant arrow removed. These captures show collapsed
  count controls, not an opened system menu or the installed production app.
  Native menu actions were tested programmatically. No QA window, foreground
  activation or simulated global input was used.

Relative to published 453, the source delta is limited to Info.plist, the two
execution-configuration source files and their two test files. No backend,
concurrency, task, adoption, research-file or signing-publisher change is included.

## Delivery

- Version/build: `0.1.454` / `454`.
- Signed with the existing offline `Parallel Codex Release Signing` identity.
  Native strict deep signature, packaged rendering and unpacked-archive checks
  passed. No Keychain, trust, identity or updater-verification change was made.
- Immutable archive: `release/clients/macos/Parallel-Codex-macos-0.1.454.zip`.
- Size: `14796178` bytes.
- SHA-256: `9e7366ce1cc9154b5611fc4a1cd3e2cc16ef6ee1cbd8698f87c03a305ae7ced9`.
- Published: `2026-10-18T07:37:36.790274+00:00`.
- Release ID: `macos-release-e1186a42e2ce4aec9ce226de7d06e656`.
- Server catalog readback matched the exact version, publisher, requirement,
  archive size and hash. Published 453 remains immutable.
- The existing authenticated controller offered 454 to installed build 452. Its
  ticketed download matched the archive size and SHA-256; no ticket was saved.
- Installed 452's native verifier accepted the update in preparation-only mode.
  Every staged file hash matched the signed release bundle and publisher.
- **Publication and delivery preparation are complete; installation is not.**
  The client was not quit, replaced, relaunched or activated. The loaded app
  remained 452, PID 77550, with the same executable inode/hash and start time.
  pcrd remained 0.1.99, PID 55700, with unchanged LaunchAgent and receipt-store
  identities. No task stop, retry, adoption, input submission or daemon restart
  was performed.

The real main window was inspected, not legacy QA window stores. Draft/outbox,
accepted-handoff, local queue and explicit Agent/conversation-selection hashes
were unchanged. The selected-workspace hash changed during the observation
interval and was neither frozen nor restored. One previously listed owner left
the live set: a targeted `runs.get` read showed its original run finalized with
a successful candidate and no error. Remaining owner PIDs/start times were
unchanged. These observations are not a full competition-computation audit.

Local health was independently `ok`; separately, the existing Relay connection
remained connected with the same connection-start timestamp, and authenticated
update check/download succeeded. No replacement bridge consumer, token refresh
or command replay was used.
