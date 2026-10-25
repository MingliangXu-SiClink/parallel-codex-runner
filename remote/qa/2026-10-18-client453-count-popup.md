# macOS 453: Compact Native Count Popup

## Scope and Interaction

The user allows a popup when clicking the candidate or synthesis count, but
rejected both the lower expanded number panel and the first detached minus /
large number / plus prototype. That prototype was never published or installed.

The replacement uses a small labeled native number field and native stepper.
There is no large display number, preset strip, visible numeric range or separate
confirmation button. Valid edits apply immediately. The existing backend limits
and concurrency policy are unchanged.

- Count editing no longer appends a section to the execution panel.
- The child popup prefers a position above the owning panel, with a visible gap,
  instead of looking like a tab attached to the panel. It remains bounded by the
  application window when space is constrained.
- Nested surfaces keep their correct hit-testing order. Clicking the child does
  not dismiss its owner; Escape and outside-click dismissal retain their scope.
- A workspace/context change or synthesis lock closes the stale editor. Existing
  context validation still rejects delayed edits for another run or workspace.
- The one-line summary, complete model names, model/effort/Fast controls, drafts,
  task state and adoption behavior are not changed by this release.

## Verification

Private evidence is in
`~/Library/Caches/ParallelCodexMaintenance/CountPopup453-20261018/`.

- 604 native non-interactive tests in 58 suites passed (329.859 seconds).
- A separate strict cooperative-pool run passed 95 tests in 6 suites.
- A nested hidden-window fixture verifies that opening/editing a count does not
  grow the main panel; the child receives native hits, stays inside the window,
  edits the intended setting and closes when the workspace changes.
- Native text-field edits and native stepper actions were tested without a
  confirmation action. Four-digit values fit at every app text size, in light
  and dark appearances, without ellipsis or font shrinking.
- Existing regression coverage retains synthesis locking, stale-context guards,
  single-line full model summaries, draft/marked-text preservation, lifecycle,
  navigation and updater integrity behavior.
- Protocol/source contracts, icon checks, the two offline signing-store tests
  and the no-signing build-behavior gate passed. Release gates record and compare
  exact source hashes.
- Offscreen native captures were visually inspected. No QA window was shown and
  no foreground activation or production task command was used. These captures
  verify the synthetic native fixture, not acceptance in the installed client.

Relative to published 452, source changes are limited to Info.plist, the two
execution-configuration view/presentation files and their two test files.

The embedded-browser thinking partner was unavailable in this tool session;
no other browser backend was substituted and no consultation is claimed.

## Delivery

- Signed build, native strict deep signature verification and immutable archive
  validation passed using the existing `Parallel Codex Release Signing` identity.
  No Keychain, system-trust, publisher or updater-verification change was made.
- Version/build: `0.1.453` / `453`.
- Server publication: `2026-10-18T04:47:58.995178+00:00`.
- Release ID: `macos-release-e4e9abfc951e44c0bd056fda0c12df61`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.453.zip`.
- Size: `14806929` bytes.
- SHA-256: `b9e4131df69c93d48e300e5a39839042b1ba9b2c3c0cf387ecd2a879578ece21`.
- Server catalog readback matched the publication. The existing authenticated
  controller offered 453 to installed build 452; its ticketed download matched
  the exact archive size and hash. No ticket was written to evidence.
- Installed 452's native updater accepted the archive in preparation-only mode.
  Every staged file hash matched the signed bundle and publisher requirement.
- The loaded client remained 452 during preparation; no installation, restart
  or foreground activation was performed. Publication and preparation are not
  installation or production UI acceptance.
- No competition message, retry, adoption, stop or concurrency change was sent.
  pcrd was neither upgraded nor restarted.

At `2026-10-18T04:49:00Z`, the loaded client was still 452 (PID 77550) and
pcrd was still 0.1.99 (PID 55700). Loaded binary identity, process start times,
observed owner identities, LaunchAgent bytes and receipt-store identity matched
the pre-publication observation. The actual main window's persisted draft,
outbox, accepted-handoff, queue and selection hashes were unchanged. No legacy
QA window was used as production evidence. Local health was `ok`; separately,
the existing Relay connection remained connected with the same connection-start
timestamp, and the authenticated update check/download succeeded. These are
delivery non-disruption observations, not a full per-Agent computation audit.
