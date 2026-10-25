# Conversation usability and notifications: 2026-09-19

## Scope and causes

- Codex commentary and final answers lost their message phase in the transcript
  projection. Preserve that distinction through the existing optional activity
  type, and put reasoning, commentary, and tools inside the work disclosure.
  The final answer remains outside it. Legacy untyped streaming text remains
  visible until a canonical final answer is available.
- The result inspector duplicated the transcript's Agent controls. Keep one
  canonical selector and show a read-only viewing/continuation context in the
  inspector. An explicit adopted result still controls continuation.
- File changes were one undifferentiated list. Group source/docs, build/dependency
  files, and runtime/log files without dropping any file or its scoped diff.
  Keep the resizable split, whole-row usage disclosure, bounded scrolling, and
  edge fades. Give the usage columns readable labels.
- A web URL ending in a port matched the file-and-line parser first. Recognize
  allowed URL schemes before parsing workspace paths. Keep workspace path
  validation, reject unsafe schemes, and localize failures with the affected
  reference and an actionable explanation.
- Starting a new topic was coupled to adopting/discarding the old result. Let a
  new topic retain completed, unadopted candidates. The server still checks all
  workspace runs for active writes or unresolved synchronization before sending.
  Continuing an old candidate keeps its stricter synchronization checks.
- Finalization was treated as another execution cycle, rearming notifications.
  Persist completion consumption by run/watch, wait for synthesis and worker
  completion, and do not notify again for adoption, repeated watches, or restart.
  A refreshed watch during notification delivery must not discard its delivery
  acknowledgement.

## Native notifications

The macOS application now uses UNUserNotificationCenter, not an AppleScript text
notification. pcrd invokes a headless mode of the signed app, which does not
create windows, AppModel, or another daemon. Only the interactive client asks
for notification permission.

Check the foreground bundle and the user's notification preference both before
and after the asynchronous permission-status query. Foreground, disabled, or
permission-denied events are consumed instead of replayed later. Eligible
background completions use the app's identity/icon and a per-run request ID.

## Automated verification

- Full backend suite: 714 tests and 36 subtests passed in 469.48 seconds.
- Final targeted controller/queue/notification suite: 174 tests and 11 subtests.
- Full macOS suite: 150 tests in 22 suites, plus protocol and desktop-state checks.
- Final signed release build and Python wheel build succeeded.
- Cached transcript layout benchmark with 1,000 operations: P95 19.80 ms.
  This is a layout benchmark, not an end-to-end frame-rate measurement.
- Tests cover message-phase projection, legacy streaming, progress alignment at
  multiple widths/zoom levels, continuation labels, grouped files, port-bearing
  web URLs, busy-workspace guards, notification suppression, duplicate watches,
  synthesis completion, adoption, and notification-state reloads.

## Native acceptance

Used the final native release build in a separate app bundle and fixture home.
The fixture uses real controller/IPC code with synthetic run data; no production
message, adoption, discard, or model execution was used for acceptance.

1. Expanded/collapsed work and inspected final-answer separation and text/icon
   alignment. The first visual pass found commentary still styled as full
   answers inside work; fixed its icon, inset, text style, and layout measurement,
   then rebuilt and repeated the native check.
2. Switched Agents and checked the inspector against the composer. The first
   implementation incorrectly showed the automatic recommendation after a manual
   selection; fixed it and verified Agent 2 in both places in the final build.
3. Expanded all three file groups and opened an individual source-file diff.
   Verified the diff request was scoped to that file.
4. Expanded usage by clicking its header, scrolled 32 records in the bounded
   region, and adjusted the usage/file split. Checked light and dark appearance.
5. Opened a local HTTP URL containing a port. The local server received the page
   request; the client did not misclassify it as a workspace file.
6. Started and sent a new topic while the previous run was completed but
   unadopted. The old result remained unchanged. Repeated with the final build;
   the current prompt appeared once. An earlier duplicate prompt was caused by
   the synthetic fixture incorrectly putting the current prompt in run history;
   that fixture was corrected, not worked around in client code.
7. The native notification helper returned `delivered` with exit code 0 without
   launching a client window or daemon. This proves native API acceptance, not
   that a notification banner was visually observed. Foreground suppression is
   covered by automated tests; the background UI automation could not reliably
   establish true system frontmost ownership, so a live foreground/banner check
   remains for activation-time acceptance.
8. Quit the isolated client and stopped only its fixture server after testing.

No Android build/device acceptance was performed this round. The additional
transcript activity values use an existing optional protocol field.

## Publication without activation

The user's final instruction was to publish and not restart.

- macOS 0.1.377 (377) was signed and published to the private Relay update channel.
- pcrd 0.1.52 was built into the local release bundle; it was not installed.
- Authenticated update check, one-use-ticket download, archive version, SHA-256,
  and signing requirement were verified against the published archive.
- Installed macOS remains 0.1.376 and the running daemon still reports 0.1.51.
  Neither production process was restarted, and no Agent or web service was
  stopped. The new notification behavior requires both components to be activated.
- Android 0.1.266 (267) and Relay 0.1.18 were not changed.

macOS release ID: `macos-release-06fc44635c614e80a17a914a8d0d88bb`.

macOS archive: `release/clients/macos/Parallel-Codex-macos-0.1.377.zip`

- Size: 8,703,402 bytes.
- SHA-256: `a0f2f49a5877b492f4a22d74dd4e9911b9540287ae278ea515d48deabe02eb7e`.

pcrd wheel: `release/pcrd/parallel_codex_runner-0.1.52-py3-none-any.whl`

- Size: 1,096,202 bytes.
- SHA-256: `1e71e50f63a0299eb536f5202dca75b141658ac28707cf32214dee64cd97755e`.

Builds, synthetic fixture, native action log, and authenticated publication
verification are retained outside the repository:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ConversationUX-20260919-m6IUTx`

The remote temporary upload was removed after publication. `release/manifest.json`
records the final artifacts and preserves the unchanged Android/Relay entries.
