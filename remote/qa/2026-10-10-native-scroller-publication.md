# Native Scroller Publication, macOS 0.1.423

## Scope And Evidence

- User input: `/Users/mingliangxu/Desktop/录屏2026-10-09 21.46.02.mov`.
- Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ScrollRecurrence-20261009`.
- All 132 original frames were decoded. The visible text line positions remain stable; the right overlay thumb changes length and fades. This is not the earlier reproduced text-displacement/animated-tail defect. The original video was not changed.
- The video has no native geometry telemetry. Pixel lengths must not be used to infer two document heights. Production-path fixtures did not reproduce competing FlowLayout and manually synchronized extents; that hypothesis was rejected, not turned into a layout rewrite.

## Confirmed Code Defects

1. The geometry key contained the collection transaction generation. A metadata/event ID update therefore invalidated the layout even with identical row measurements, then issued a same-origin scroll and native reflection. The key now represents actual width, content height and measurement revision; row-identity/order changes still invalidate it. Metadata and action updates remain deliverable.
2. NSScrollView automatically reflects a document resize before the coordinator restores the reading anchor. The earlier no-animation text transaction did not prevent the native overlay scroller from receiving the intermediate extent/origin pair. Native KVO reproduced `[0.7425971399682219, 1.0]` and `[0.7860734420234649, 1.0]` during followed reflow while the final text pixels were stable.

The new shared transcript NSScrollView base class aggregates native reflection only within a synchronous geometry-and-anchor commit, including nested commits, and reflects the final pair once without implicit animation. Outside that scope it forwards normal AppKit reflection immediately. It does not clamp the reader to the tail, fix the knob proportion, hide a scrollbar, change document-height computation, or use a retry timer. Initial/context/resize restores establish the accepted geometry key as well.

## Regression Evidence

- `root-regression-before.log`: intentional exit 1, three test methods, 63 issues before the production fix. It retains the intermediate native positions and metadata-only repeated updates.
- `root-regression-after.log`: exit 0, the same three test methods pass; all followed reflow position traces are `[1.0]`.
- `viewport-focused-verified-tests.log`: production-path SwiftUI hosting, four text sizes, reading/following, real reflow, equal-total-height changed row distribution, metadata interleaved with unfinished updates, nested native commits, immediate ordinary scrolling/resizing, prior text-pixel stability, scroll chaining and motion ownership.
- Earlier test iterations caught a missing test actor annotation and the first metadata poll needlessly invalidating an already restored layout. Those failures are retained in the evidence directory; final passing logs do not replace their records.
- The first full run (`client-full-first-failed.log`) exited 1 with 399 tests and one unrelated answer-preload request-count assertion after the held mock response was released. The preload test and its production model sources are byte-identical to the prior 0.1.422 evidence. A separate run of all 23 preload tests passed without source or test edits. The failure is retained; the final full run is rerun unchanged, not filtered to remove this test. This does not establish that the preload timing failure is permanently resolved.
- No test window was shown or activated. Constructed events in existing hidden fixtures exercise event handlers, not global device input. Foreground interaction suites remain opt-in and were not enabled.

## Acceptance Boundary

The native publication defects are reproduced and fixed. The exact 43/93-pixel overlay animation from the video has not been reproduced frame-for-frame in an unshown window, so background AppKit tests are not presented as formal visible-client animation acceptance. The user forbids intrusive QA, and no visible QA was launched to obtain it.

This is a client-only release. No scheduler, Agent, Node service, research directory, task configuration, canceled cloud-cleanup request or paused finalization-performance work is changed. Publication, installed bundle and running executable are verified separately below. A graceful client restart is forbidden when it could adopt an unchosen Agent result or disturb an unresolved message.

## Independent Technical Review

An existing hidden embedded-browser discussion was continued at `https://chatgpt.com/c/6ac90069-7e24-83ee-90d2-160e87759a03`. It distinguished measured native state from overlay pixels, challenged the unsupported two-extent hypothesis, and prompted tests for same-height row redistribution and metadata interleaving. Review agreement is not release or visual-acceptance evidence.

The follow-up review examined synchronous reentry, scope pairing, normal native passthrough, nested inner/outer commits and clip parameter identity. Source review confirms pending state is cleared before the final superclass call, with no global flushing suppression or stale snapshot replay; scopes do not span an await or queued completion; normal reflection has no geometry/visibility filter; production scroll intent reads clip bounds rather than stale scroller values. No speculative drain loop or global scroll manager was added.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T16:16:59.983993+00:00",
  "macos": "0.1.423",
  "archive_sha256": "19ef75b42fc318afb73109247e3b614801ae40e1cfbf872aa6fca640fadee505",
  "relay_catalog_verified": true,
  "pcrd_unchanged": "0.1.80",
  "actual_installed_client": [
    "0.1.422",
    "422"
  ],
  "client_tests": 399,
  "client_suites": 48,
  "python_contract_tests": 32,
  "python_subtests": 7,
  "focused_tests": 40,
  "focused_suites": 5,
  "native_publication_regression": true,
  "all_original_video_frames": 132,
  "foreground_qa": false,
  "exact_video_overlay_animation_reproduced": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T16:22:11.865266+00:00",
  "macos_prepared": "0.1.423",
  "actual_installed_client": [
    "0.1.422",
    "422"
  ],
  "actual_pcrd": "0.1.80",
  "scheduler_pid": 46777,
  "client_not_restarted": true,
  "builtin_integrity_and_signature_verifier_used": true,
  "healthy": true,
  "paired": true,
  "no_qa_window_or_user_input": true
}
```

## Actual Runtime And Installation Gate

```json
{
  "checked_at_utc": "2026-10-09T16:25:42.273565+00:00",
  "published_and_prepared_macos": "0.1.423",
  "actual_running_client": [
    "0.1.422",
    "422"
  ],
  "client_pid_unchanged": 220,
  "loaded_executable_inode_verified": true,
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "active_runs": [],
  "active_operation": null,
  "node_pids_and_start_times_preserved": true,
  "non_macos_release_components_unchanged": true,
  "installation_exit_code": 75,
  "installation_gate": {
    "reason": "retained_client_queue",
    "client_not_restarted": true
  },
  "retained_client_receipts": 0,
  "persisted_queue_contexts": 2,
  "persisted_queue_items": 2,
  "retained_state_not_treated_as_unsent_messages": true,
  "no_client_quit_or_restart_requested": true,
  "no_task_stopped_adopted_discarded_or_resubmitted": true,
  "cancelled_cleanup_still_discarded": true,
  "client_fix_not_yet_enabled": true,
  "backend_not_restarted": true,
  "no_qa_window_or_keyboard_takeover": true,
  "rollback_performed": false,
  "exact_video_overlay_animation_reproduced": false
}
```
