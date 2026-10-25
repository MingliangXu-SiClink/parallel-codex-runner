# Result Inspector Scrolling And Resizing

## Reproduction

- On installed macOS 0.1.372, clicking the usage title rather than the small
  disclosure arrow left the section collapsed.
- Usage and file changes shared one outer scroll view. A long usage table could
  push the file section out of view, and there was no draggable divider.

## Repair

- Replaced the native disclosure-only hit target with a full-width button,
  including the title, blank space, and step count. Exposes its expanded state
  to accessibility and respects Reduce Motion for the arrow animation.
- Kept status and aggregate metrics outside the scrolling sections. Added a native
  vertical split between usage and file changes, reusing the sidebar's divider
  persistence. Only user drags save a position; folding and window resizing do not
  overwrite it. Reopening restores the expanded position.
- Usage and changed files now scroll independently within their available height.
  Their headings remain visible. Neither scroll area adds a box or border.
- Overflowing content fades at the top and bottom. A fully visible first or last
  row is not faded; content that fits has no fade.
- The first implementation used SwiftUI geometry preferences. Native inspection
  and a new integration test showed that they did not report the actual scroll
  edges across macOS's hosted scroll content. Replaced that path with notifications
  from the real NSClipView and document frame, publishing only edge-state changes.
- Preserved meaningful accessibility labels for individual status and metrics
  rather than allowing the parent result label to replace them.

## Verification

- Full macOS contract and client scripts passed: 132 tests across 19 suites.
- Final-source targeted inspector suite: 11 tests passed.
- Tests include native scroll/resize edge detection, 500 usage bins and 100 files
  in bounded scroll views, collapse/reopen divider persistence, four text sizes,
  both appearances and languages, and existing elapsed-time behavior.
- Built and signed the release app with Parallel Codex Local Signing.
- Manually exercised the built native client against an isolated, read-only QA
  fixture with 120 usage bins and 40 changed files. No model requests or production
  Agent actions were used to populate this fixture.
- Clicking the title text, empty heading space, and right-side count all toggled
  the section. Dragging changed the usage pane from 216 to 149 points. Collapse,
  reopening, client relaunch, and changing between compact overlay and inline
  inspector retained the chosen 149-point position.
- Scrolled each list separately through its middle and to the end. Both edge fades
  were visually checked in the native client, and the final usage row and file 40
  were fully readable at the bottom.
- Checked dark standard-size compact layout, larger inline layout, and light
  extra-large text. Selected file 40 and verified its specific patch was opened.
- Separate follow-up observed: the file-review column can exceed the available
  width in a compact window. This round does not claim to repair the entire
  file-review layout; the issue is outside the inspector split/scroll changes.

## Release And Activation

- macOS 0.1.373 (373).
- Relay release: `macos-release-58039381511241d9b23498b9bba0ecf9`.
- Archive SHA-256:
  `f0d4053167a34ccfbb4bda36bd0a7008a6ccd0e99772b6326a84be3bc7a60905`.
- Archive size: 8,539,574 bytes.
- Archive signature/version validation and authenticated app-update check,
  download ticket, download size, and digest validation all passed.
- Production client remains 0.1.372 pending the user's restart confirmation.
  Production pcrd remains PID 98927; no daemon, Agent, or web service was restarted.
- No Android, pcrd, Relay, or protocol code was changed in this iteration.

Build, test, and fixture records:
`~/Library/Caches/ParallelCodexMaintenance/ResultInspector-20260919-L6oQDT/`.
