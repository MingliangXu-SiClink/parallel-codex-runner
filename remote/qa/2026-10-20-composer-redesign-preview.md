# Composer Redesign Preview

Status: the user accepted the exact native preview in this thread. It was then
finalized and formally published as macOS 0.1.463. Publication evidence is in
`2026-10-20-client463-composer.md`; the client has not been installed or restarted
by this change. The sections below describe the original preview stage.

## Confirmed Interaction

The user's latest clarification supersedes the earlier asynchronous answer:
there must be only one primary action in the right-hand position. Sending must
still become a queue action when the existing submission plan requires it.
The rejected preview incorrectly showed a secondary stop button beside it and
used a filled native list/play-like glyph.

| Current Intent | Primary Action |
| --- | --- |
| Immediate submission is available | Up arrow |
| A drafted message must wait under the existing queue policy | Linear list-end icon |
| A turn is running and the draft is empty | Stop square |
| No runnable submission and no active turn | Disabled up arrow |

The secondary stop affordance and its presentation-state flag were removed.
Actual submission, continuation, queue ordering and stop/pause-queue operations
were not changed. The existing licensed Lucide queue icon is reused at a smaller
size; send and stop share a stable 36-point circular target, scaled by text size.

## Configuration

The single, unfilled text dropdown sits in the composer footer beside the
primary action. Both counts and complete model names remain on one line.
Overflow scrolls horizontally with the dropdown chevron still reachable; text
does not shrink, wrap or acquire an omission mark. The independent upward,
window-bounded editor retains its native controls without a title or close row.
Existing outside-click, Escape and context-change dismissal paths are retained.
No microphone, extra mode or extra editing panel was introduced.

## Verification

- Focused native checks: 42 tests in six suites passed.
- Final `apps/macos/scripts/test-client.sh`: protocol and desktop-state contracts,
  644 native tests in 60 suites (367.290 seconds), and app-icon checks passed.
- Native fixtures cover one-line placement at 350/560/960 points, Chinese and
  English, standard and large text, both appearances, all primary-action states,
  full model names, configuration locks, input-method text and draft retention.
- Hidden SwiftUI windows do not expose their accessibility subtree in this
  harness. Footer placement is checked using the real native anchor plus painted
  button pixels; the upward surface is presented programmatically through the
  production coordinator. These are native offscreen previews, not evidence of
  pointer acceptance in the installed client.

Private evidence: `~/Library/Caches/ParallelCodexMaintenance/ComposerRedesign-20261020/`.
Final screenshots are under `native/`; the complete test log is
`contract-and-native.log`. No production message or competition job was used as
a test during the preview stage, and that stage ran no release, release-signing,
updater, task or daemon operation. The later authorized publication is recorded
separately.
