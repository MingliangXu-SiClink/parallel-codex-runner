# macOS Automatic Output, Directory Browsing, and Reading Position

## Scope

- macOS 0.1.390 / build 390. No pcrd, Android, Relay protocol, execution-policy,
  or Agent-selection changes. Unrelated working-tree changes were retained.
- Closes the three remaining user burdens from the 0.1.389 file-review round:
  manual command-output pages, manual directory pages, and three-line reopen drift.
- Existing full-context diffs, highlighting, independent result details, and
  adjustable columns remain available.

## Root Causes and Changes

- `WorkProcess.swift` marked an operation event complete after its first page.
  It now serially reads the immutable event to EOF, publishes readable prefixes,
  resumes partial reads, and only then marks that event complete. Revalidation
  retains complete visible output until replacement is ready. Collapse cancels
  reading, not the actual command. Request ownership covers success, failure,
  and cleanup, including event replacement and Agent switches.
- Command-output cache is bounded to 24 entries / 48 MiB. Completed records are
  not repeatedly downloaded. Real failures retain readable content and an
  explicit retry; stalled or incompatible cursors cannot loop indefinitely.
- Directory paging no longer occupies navigation's loading state. Near-end
  prefetch appends the next page without moving the current view or disabling
  navigation. A separate visit/request generation rejects late callbacks,
  including A -> B -> A. Paging failure is local to the list, with explicit retry.
- File restoration previously mixed temporary and highlighted font metrics.
  Initial text and syntax text now both use Menlo; within-line offsets scale
  with line height. Zero-size views retain pending reading intent. Layout,
  frame changes, and highlighting use one non-nested position transaction.
- Native reproduction exposed an additional cause after the first fix:
  `NSSplitViewController` animation completion calls `NSTextView.viewDidEndLiveResize`,
  which recenters a glyph after our precise restoration. The captured stack
  reached `_centeredScrollRectToVisible` and shifted x by 16 pt and y by 15.5 pt.
  The public live-resize lifecycle now uses the same position transaction around
  `super`; no private API, fixed delay, pixel compensation, or disabled animation.
- Full-suite checking also exposed stale persisted height for short Chinese user
  bubbles at large text sizes. These cheap natural-width rows now measure in the
  current AppKit environment rather than accepting a persisted glyph-height cache.

## Actual Native Acceptance

- Evidence directory:
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Paging-20260930-ZJVR5W`.
- Isolated native QA uses the release executable, a separate bundle ID/home, and
  the real IPC controller, filesystem browser, and diff snapshot implementation.
  It does not start a real Codex Agent or modify the user's workspaces.
- Long operation: 3,200 numbered Chinese output lines, 118,422 UTF-8 bytes,
  29 contiguous pages. Expanding once reached EOF without any pagination click.
  Native scrolling exposed line 03199 and `END-OF-COMMAND-OUTPUT`.
  `command-automatic-loaded.json` / `command-eof.json` and the fixture cursor log
  record completion; no "Load more output" control was present.
- Large directory: 1,205 real subdirectories. Initially only cursor 0 was read.
  Scrolling near the loaded end triggered cursor 500, then 1000. A real wheel
  scroll reached `Folder-0000`; screenshots record the appended and final pages.
  No next-page control was required. Full AX-tree enumeration was avoided for
  this check because asking for off-screen rows can itself create SwiftUI rows.
- Long highlighted diff: 5,200 original lines. Middle, true bottom, and three
  lines above bottom were each closed/reopened three times. All nine retained
  the same visible character range. Maximum vertical difference was 0.125 pt;
  horizontal difference was 0.1361 pt (native pixel rounding, not a line jump).
  The true bottom remained pinned, while the near-bottom position did not.
  `reading-acceptance.json` and before/stable AX captures record each check.
- Left-sidebar animation preserved the visible range and both scroll axes.
  A regression test also covers user scrolling during live resize and repeated
  resize-end callbacks; the latest reading position wins.
- 237 native tests in 32 suites passed in 77.977 seconds. Protocol, desktop-state,
  and icon checks passed. The expanded final reading-lifecycle tests also passed.
  Release build: 44.42 seconds. Strict signature and packaged syntax/math resource
  validation passed. Temporary geometry/call-stack instrumentation was removed.
- Cancellation, partial retry, stale event/visit callbacks, malformed cursors,
  zero viewport, and selection preservation are regression-tested. Native manual
  acceptance is local IPC, not a claim that every Relay/network fault was exercised.

## Delivery

- Published active Relay release `macos-release-cef7925bb31f4d589c7cd249d4332b26`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.390.zip`.
- SHA-256: `2d3df436026818b4000a6af5eff23658c03a78eecaa1036fcf02547645b2d8a1`.
- Signed with Parallel Codex Local Signing; other component artifacts/hashes
  were preserved unchanged. Re-read the catalog and verified build/version/hash.
- Installed `/Applications/Parallel Codex.app`, verified its strict signature,
  version, and executable hash, and launched the formal client (PID 11635).
  Its `hermes-webui` window restored the same 1754 x 1123 frame and empty draft.
- Before restart the old window was closed to flush its explicit state, then only
  the client process was terminated. This avoids its existing quit-time adoption
  path changing an Agent result during maintenance.
- pcrd PID 43170 and active worker PID 2592 remained unchanged across installation.
  No Agent or web service was stopped. QA app/fixture processes and remote upload
  staging were cleaned up; the previous signed app is backed up in the evidence directory.

## Remaining Follow-Up

- Four columns at a narrow 1040-pt window can squeeze/clip the conversation.
  Improve adaptive column sizing separately without removing persistent details.
- The earlier accepted-run hydration recovery messages still deserve a dedicated
  reconnection check. Never resubmit a successfully accepted task to restore its view.
- The existing normal-quit adoption policy should be reviewed independently;
  maintenance restart must not be interpreted as choosing an Agent result.
- Continued independent review in the existing ChatGPT Web conversation:
  https://chatgpt.com/c/6abbb5f0-d954-83e9-8da6-4c152c7acddf
