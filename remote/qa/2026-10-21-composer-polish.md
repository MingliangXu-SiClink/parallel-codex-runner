# macOS 0.1.466: Composer Proportions and Controls

## Scope

The reported mismatch was primarily proportional: a tall empty input, a heavy
primary circle, misaligned footer controls and execution values dimmed through
multiple opacity layers. This is a client-only visual refinement, not another
composer interaction redesign.

- Reduce the standard one-line input surface from 120 to 96 points. Its complete
  component, including unchanged external margins, measures 116 instead of 140.
- Use a 28-point primary circle and plus-control slot, with a 16-point send or
  existing queue symbol. Keep the SF stop symbol at 12 points: its measured ink
  is 18 pixels inside the 56-pixel circle at 2x, matching the optical reference.
- Use 15-point native input text, a 13-point true left text inset, a flat adaptive
  surface and a 22-point composer corner radius. Preserve multiline expansion.
- Align the plus, execution summary and primary action on the same footer.
  Distinguish secondary role names from readable counts and full model names.
- Preserve one primary action, the original send/queue/stop rules, one-line
  complete candidate-count/model and synthesis-count/model fields, horizontal
  scrolling at narrow widths, and the existing upward execution menus.

Only three production files, one composer test file and the version plist
changed from 465. Native editor, input composition, question/answer delivery,
task queues, adoption, notifications and backend sources are unchanged.
No microphone, permission badge, extra action or new settings panel was added.
The dark band seen in the supplied comparison was not reproduced in the
captured production window or baseline component; no gradient-root-cause fix
is claimed. Both appearances now have explicit flat-surface pixel checks.

## Validation

- Canonical non-interactive native gate: **662 tests in 62 suites passed**,
  398.966 seconds. Focused strict-cooperative gate: **66 tests in 9 suites
  passed**, 16.548 seconds. Protocol/bootstrap contracts, app icon, signing-store
  and signing-disabled gates passed on the same frozen sources.
- Never-ordered production AppKit/SwiftUI components cover Chinese and English,
  dark/light appearances, supported zooms and widths 350, 560 and 960. Assert
  button diameter/baseline/spacing, complete single-line summary, upward menu
  containment, unchanged editor identity/selection and no submission side effect.
- Existing marked-text/input-composition and background-update tests pass.
  Previews include empty, send, running-stop and running-queue states.
  Actual menu-edge clicking in a live production window was not exercised.
- The first broad gate hit one unchanged receipt fixture's polling deadline.
  Its original failed log is retained. That same fixture then passed three
  isolated strict runs and the complete unchanged-source rerun. No delivery
  code was changed or purportedly fixed by this visual release.
- The first new pixel assertion compared calibrated and sRGB values directly;
  it was corrected to compare both in sRGB before the final frozen gates.
- Two actual exchanges in the existing ChatGPT Web discussion used verified
  Pro/max controls. Optical sizing was decided from native measurements, not
  partner agreement. No private competition transcript or credentials were sent.

## Delivery

| Stage | Verified Result |
| --- | --- |
| Signed build | Existing offline Parallel Codex Release Signing identity; same publisher; deep strict verification |
| Formal publication | Private Relay catalog latest version/build 0.1.466/466; catalog readback verified |
| Authenticated updater | Running 465 sees 466; one-use download matches exact archive size and SHA-256 |
| Native staging | Production verifier accepted the publisher and exact staged bundle bytes |
| Installation | **Not installed or restarted in this turn**; running client remains 0.1.465 PID 76563 with its real foreground window open |
| Inputs and execution | Main document state, immutable answers and service queues unchanged; pcrd 0.1.102 PID 88104, 10 owners and 16 original native receipts/processes retained |

Archive: 14,896,263 bytes, SHA-256
`7e2436fc5e2e7b3ef2b77a2f23a45faf6fc15c9c1ba6ee6bfb4973273184adc1`.

The real-window inspection returned one `main` document plus two auxiliary AX
windows: an empty titlebar-sized window and a small window containing the native
`WindowSharingSessionButton`. These were recorded, not replaced by a fictitious
zero-window baseline or used to bypass installation guards. No quit, install
helper, production task command, question answer, worker restart or focus change
was requested. The published update is available through Software Update.
Per-run API event counts advanced across publication; this is not a blanket
per-Agent or remote-computation health assertion.

Private evidence and native previews:
`~/Library/Caches/ParallelCodexMaintenance/ComposerPolish466-20261020/`.
The prior small-image backend projector remains source-only; no pcrd version was
built or enabled. Other release-manifest components were retained unchanged.
