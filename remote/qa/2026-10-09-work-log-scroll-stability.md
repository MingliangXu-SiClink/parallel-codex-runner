# macOS Work-Log Viewport Stability

## Scope and Safety

- Investigated the user's 2026-10-09 19:01:45 recording without changing it.
- The affected surface is the bounded work-process log, not an Agent execution progress bar.
- The user confirmed there was no mouse or trackpad input during the recorded jumps.
- No QA window was ordered, no application was activated, and no desktop input was posted.
- No scheduler, Agent, task, research directory, or Node service was changed for this investigation.
- Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ScrollStability-20261009`.

## Observed Recording

The 2346 x 1046 recording contains 75 variable-rate frames over 5.55 seconds.
The outer reply and disclosure header remain stable while code in the inner log
repeatedly moves far away from its final position and returns. A matched code
line moves at least 537 source-video pixels in one return. Return trajectories
are consistent with the client's 0.16-second cubic ease-out scroll motion.
This is evidence of the presentation behavior, not a measurement of Agent work.

## Deterministic Root-Cause Reproduction

The production `ConversationTimelineView.Coordinator` previously handled reading
anchors and following the end differently. Reading was restored with new row
geometry in one native transaction. Following instead painted the new document
geometry first, queued `scrollToLatest` for a later main-loop callback, and then
animated to the end. Loading or reflowing output above the unchanged visible
tail therefore displaced that tail before the follow animation caught up.

An unshown native window with the real nested collection views reproduced the
gap: an earlier output expanded from one line to 100 lines, with the last 300
code lines unchanged. After `update` returned, the visible offset was 5141 but
the new end was 6923, a 1782-point error. Other expansions produced a 1404-point
gap. Later callbacks restored the end, so a test checking only the final settled
position passed and missed the fault. `reproduced-follow-gap.log` records the
original test's real exit code 1.

The recording alone does not identify which particular asynchronous output page
changed the document on every cycle. No cache corruption, server failure, or
user input is claimed as an additional cause without evidence. The native
reproduction establishes the defective layout/follow commit path independently.

## Repair

- Commit passive content geometry and its reading/following intent together,
  before the native viewport can paint. Passive updates no longer queue an
  animated chase of the end. Explicit jump-to-latest retains its animation.
- Keep the same restoration intent until collection batch completion, so
  AppKit insertion compensation cannot publish a provisional reading position.
- Separate following intent from geometric proximity. Restoring a reader into
  a temporarily shorter document does not silently enable automatic following.
- User navigation revokes pending restoration and motion. Actual viewport
  navigation to the end resumes following, but an outer wheel handoff with no
  inner movement does not silently change a reader's intent.
- New content geometry revokes an in-flight explicit scroll trajectory too;
  the old timer cannot apply a destination calculated for the previous layout.
- A fixed-height inner work-log update does not reposition the outer timeline.
- Keep native instances, output loading, gesture routing, and streaming active.

## Focused Verification

`focused-final-tests.log`: 31 tests in four suites passed, using unshown native windows.
The reproduction now compares actual rendered viewport PNGs immediately after
each content update, not only after asynchronous settling. Reading and following
both keep identical pixels when earlier output reflows; a new visible tail
marker changes the pixels, and the surface must contain nonblank painted text.
The fixed-height outer viewport remains unchanged.

Additional contracts cover four text sizes, equivalent metadata updates,
unchanged polls, rapid append batches, shortening/growing documents, user
navigation overtaking pending follow, mouse wheels, phased gestures, momentum,
boundary remainder handoff, reversal, cancellation, and view reuse. The existing
scroll-chaining suite now uses unshown windows and is included in ordinary
background QA. Foreground suites remain explicitly excluded and the guard still
rejects newly introduced foreground test actions.

Four motion-ownership tests exercise the actual native animation timer with an
injected monotonic clock and visibility eligibility while the window remains
hidden. They cover a geometry commit interrupting motion, user navigation
overtaking a queued jump, short-document zero-distance jump versus outer wheel
handoff, and context changes retiring the old motion. Production visibility and
reduced-motion behavior retain their existing defaults.

Headless rendering and direct responder events do not constitute physical
trackpad or WindowServer composition acceptance. The formal client must not be
called upgraded until publication, installation, and loaded-executable checks
actually succeed. Full-suite and release results are appended below when verified.

## Final Background Tests

- `client-final-tests.log`: exit 0, 387 tests in 46 suites passed in 226.107
  seconds, plus macOS protocol contracts, desktop-state checks, and app-icon
  tests. The final production source and build 421 metadata were in place.
- `protocol-release-tests.log`: exit 0, 32 tests and seven subtests passed.
- The source protocol generator's `--check` gate passed without rewriting it.
- No foreground interaction suites were enabled; all new rendering and motion
  tests used unshown windows, with no global input event submission.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T12:11:23.275706+00:00",
  "macos": "0.1.421",
  "archive_sha256": "4e3a42631558075ea1b2fb41629e45f53cc378ece582f316bde456447493e4b7",
  "relay_catalog_verified": true,
  "pcrd_unchanged": "0.1.80",
  "actual_installed_client": [
    "0.1.420",
    "420"
  ],
  "client_tests": 387,
  "client_suites": 46,
  "python_contract_tests": 32,
  "python_subtests": 7,
  "focused_tests": 31,
  "focused_suites": 4,
  "native_viewport_pixel_checks": true,
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T12:12:35.203858+00:00",
  "macos_prepared": "0.1.421",
  "actual_installed_client": [
    "0.1.420",
    "420"
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

## Verified Installation

```json
{
  "checked_at_utc": "2026-10-09T12:12:55.161660+00:00",
  "macos": "0.1.421",
  "build": "421",
  "client_pid": 85436,
  "loaded_executable_inode_verified": true,
  "builtin_updater_used": true,
  "background_relaunch": true,
  "pcrd": "0.1.80",
  "pcrd_pid_unchanged": 46777,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_processes_preserved": true,
  "existing_workers_unchanged": {
    "pcr-20261009T113942Z-adbe5611": {
      "pid": 80143,
      "fingerprint": "80143 Fri Oct  9 19:39:43 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
    }
  },
  "cancelled_cleanup_still_discarded": true,
  "rollback_performed": false,
  "no_qa_window_or_user_input": true,
  "no_real_user_turn_submitted_for_qa": true
}
```
