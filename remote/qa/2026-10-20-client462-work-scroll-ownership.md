# macOS 0.1.462: work-log scrolling and readable viewport

## Scope and Result

macOS-only repair for an expanded command history consuming scroll input while
only one row is visible in the conversation. Existing disclosure controls and
normal internal reading remain unchanged. No new window, mode or control.

Source repair, regression gates, signed packaging, formal publication,
authenticated download and native update preparation are complete. The loaded
client remains **0.1.461**; installation/restart and real-device acceptance of
the loaded 462 client have **not** been performed by this maintenance action.

## Reproduced Causes

- The inner `FadingWorkScrollView` consumed displacement until its own document
  boundary, without checking how much of its viewport the parent actually showed.
  At 24pt visible height, scrolling could cycle a long history through that strip.
- Ownership was cell-local, missed gestures starting outside the cell, and could
  be recaptured after reversal, recycling, reveal or a new wheel tick.
- The preferred 360pt/504pt viewport did not fit a 240pt conversation viewport.
  Height-only changes also needed the existing reflow/restoration path.
- A mixed native-handler fixture showed 45pt missing at the first drawable state
  when an already handed-off sequence changed to the parent's native path. This
  is evidence about this application/fixture, not a universal AppKit requirement
  to synthesize `began` events. No synthetic phases were added.
- Shrinking an internally followed 360pt viewport to 182pt left its old offset
  178pt short of the new bottom. The pre-resize reading/follow intent must survive.

## Implementation

- Intersect the actual inner clip viewport with the parent's visible document
  rectangle and ancestor clipping. Account for the sticky Agent bar's actual
  covered interval. `visibleRect` alone is not assumed to equal the child's bounds.
- Keep conversation ownership on the outer scroll view. A clipped log immediately
  passes input outward; reveal, reversal, streaming, retarget and momentum do not
  recapture that sequence. New gestures, cancellation, context change and detach
  retire old ownership. Unphased wheel bursts use the existing 180ms idle policy.
- After a nested handoff, later direct parent hits use the same existing vertical
  displacement path. Normal gestures starting on the parent retain their native
  path. At an internal boundary, only unused displacement is transferred once.
- Constrain actual cell height to the conversation viewport, existing insets and
  sticky-bar budget. Reuse existing resize snapshots for both the conversation
  and the original inner coordinator, preserving middle reading and tail intent.

## Verification

- Initial reproduction: four tests failed with 26 issues before the repair.
- Final frozen source: **640 native tests / 59 suites passed**; **51 focused tests
  / 7 suites passed**. Contracts, icon, signing-store, unsigned-build isolation,
  production build, strict deep signature and immutable archive gates passed.
- Covered top/bottom 24pt exposure, 1,500/150,000pt histories, outside-to-inner
  retarget, first parent `changed` and first parent `momentum.changed` at boundary
  handoff, delayed momentum, zero-delta phases, cancellation, recycling, new
  internal reading, small windows, large fonts and resize/queued-restore races.
- Production collection/cell fixtures saved nonblank intermediate native paints:
  one-row exposure, full reveal, conversation after the log and a small viewport.
  Test windows remained hidden and non-key. These are not real-device gestures
  in the currently loaded client; that acceptance remains separate.

## Published and Prepared

- Formal authenticated catalog: **0.1.462 / build 462**, existing publisher
  **Parallel Codex Release Signing**, unchanged strict updater verification.
- Archive: 14,822,902 bytes; SHA-256
  `2076f54812f6903912ae4de7bf022d8913f28cf567240b17f4a202c55288923e`.
- The existing 461 client receives `available=true`; its authenticated download
  matches the archive. Its native verifier accepted the update and staged bytes
  match every packaged file. Publication/preparation are not installation.
- Actual open main window, durable composer content, navigation, outbox/handoff,
  local queues and immutable answers were unchanged across publication/preparation.
  Client PID/start time, pcrd 0.1.102 PID/start time, original active worker start
  times and receipt-store identity stayed unchanged. No task operation or adoption.
- Local health and reported Relay connection were recorded separately; the
  authenticated catalog/download round trips succeeded. This is not a substitute
  for a per-Agent remote-computation health audit.

Private timestamped evidence, source hashes, failed/passed logs, native paints,
publication/download/preparation receipts and `verification-summary.json`:
`~/Library/Caches/ParallelCodexMaintenance/NestedScroll462-20261020/`.
