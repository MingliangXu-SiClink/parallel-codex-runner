# macOS Phase Timings Disclosure

## Scope And Cause

The installed 0.1.421 result inspector used macOS's default `DisclosureGroup`
for Phase timings. Its title was not part of the same explicit full-row button
as the disclosure arrow. This made the visible title an unreliable click target.
The issue is confined to the inspector control, not timing collection or pcrd.

The replacement uses one plain SwiftUI Button containing the chevron, localized
title and spacer, with an explicit full-width rectangular content shape. There
are no competing label/arrow gestures. Expansion remains owned by the result
inspector's State binding. Timing order, duration formatting, filtering and the
160-point scaled detail-scroll limit remain unchanged.

## Verification Boundaries

- Focused tests use the real ResultPhaseTimings view and SwiftUI's public
  PrimitiveButtonStyle configuration trigger in an unordered NSHostingView.
  They check the full label dimensions and single-button/content-shape contract,
  actual expand/collapse binding transitions and native timing-scroll creation.
- The matrix includes Chinese/English, light/dark, all four text sizes and
  320/440-point widths. Offscreen rendering checks nonblank collapsed/expanded
  pixels, and repeated metric updates must preserve the user's disclosure choice.
- Unordered SwiftUI hosts did not expose their virtual accessibility children in
  the initial test fixture. Those initial failures were fixture limitations, not
  a reproduced product regression. The final tests do not rely on that tree,
  undocumented visibility overrides or desktop input simulation.
- No QA window is ordered or activated. No global mouse/keyboard events are
  posted. These are source-contract and in-process/offscreen checks, not a claim
  of physical pointer acceptance on the user's visible desktop.
- Full client tests, protocol/release tests, signed archive publication,
  built-in updater preparation and actual running executable verification are
  separate gates. Their observed results are appended below when completed.

## Deployment Safety

The user authorized publication, installation and a background client restart.
Before exit, verify healthy paired local context, detached workers, no pending
message delivery or client outbox, no follow-up queue and no result that an
ordinary quit could implicitly adopt. Repeat that gate before preparation and
inside graceful termination. Do not force quit, adopt results, restart pcrd,
stop workers/Node services or resume the cancelled cloud-cleanup request.

Preserve the earlier scroll-stability, stopped-conversation and typing fixes.
Do not resume the paused finalization performance redesign.

## Verified Tests

- `focused-final-tests.log`: 3 tests passed. The label/frame matrix covers 32
  combinations; each activates the real SwiftUI Button trigger and creates the
  timing scroll. Expand/collapse round trips create/remove the native scroll
  exactly once, and 16 timing updates preserve the chosen state.
- `client-final-tests.log`: 390 tests in 47 suites passed in 234.364 seconds;
  protocol, desktop-state and all 8 icon slots/7 native rendering sizes passed.
  This includes the earlier one-paint viewport and stopped-turn regressions.
- `protocol-release-tests.log`: 32 tests and 7 subtests passed.
- Eight collapsed/expanded offscreen snapshots were generated. Chinese/dark
  and English/light expanded snapshots were visually checked for visible text,
  phase/duration alignment and lack of overlap. They are not desktop screenshots.
- Swift builds and tests used one low-priority build job and the macOS 26.5 SDK.
  No foreground-only interaction suite was run.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/PhaseTimingsDisclosure-20261009`.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T14:15:28.611104+00:00",
  "macos": "0.1.422",
  "archive_sha256": "952671557b5185c1cf00a59d73fb028fd8859eaa4a0ff6b899e57bc05ed526bb",
  "relay_catalog_verified": true,
  "pcrd_unchanged": "0.1.80",
  "actual_installed_client": [
    "0.1.421",
    "421"
  ],
  "client_tests": 390,
  "client_suites": 47,
  "python_contract_tests": 32,
  "python_subtests": 7,
  "focused_tests": 3,
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T14:16:49.941826+00:00",
  "macos_prepared": "0.1.422",
  "actual_installed_client": [
    "0.1.421",
    "421"
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

## Installed Runtime Verified With Focus Exception

The built-in installer exited 0, installed build 422 and did not roll back. The
independent install caller exited 1 at its later nonactivation assertion. The
first post-install snapshot showed the new client PID 220 in the background
(foreground PID 19457); a later snapshot showed PID 220 in the foreground. The
cause of that later focus transition is not established. Do not claim that the
user caused it or that background focus acceptance passed in full.

A separate read-only check verified the actual loaded executable inode/hash,
healthy paired pcrd 0.1.80 with its unchanged PID 46777, GPT-6.1 Sol availability,
the unchanged worker PID/start time and preserved Node processes. No additional
restart, rollback, hide/activate call or task operation was attempted to make the
focus check pass. This distinction is retained in the raw failure record below.

```json
{
  "checked_at_utc": "2026-10-09T14:21:43.130167+00:00",
  "macos": "0.1.422",
  "build": "422",
  "client_pid": 220,
  "loaded_executable_inode_verified": true,
  "installed_binary_sha256": "41312bc3671fff23a9cd6aeb7dd9e03507d537b0aa5e3dd967920d4b911a964f",
  "builtin_updater_used": true,
  "installer_exit_code": 0,
  "rollback_performed": false,
  "pcrd": "0.1.80",
  "pcrd_pid_unchanged": 46777,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_processes_preserved": true,
  "existing_workers_unchanged": {
    "pcr-20261009T140909Z-0c859419": {
      "pid": 98459,
      "fingerprint": "98459 Fri Oct  9 22:09:09 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
    }
  },
  "cancelled_cleanup_still_discarded": true,
  "activation_requested": false,
  "control_before_installation": {
    "client_pids": [
      85669
    ],
    "frontmost_pid": 85669
  },
  "control_immediately_after_installation": {
    "client_pids": [
      220
    ],
    "frontmost_pid": 19457
  },
  "control_at_readonly_verification": {
    "client_pids": [
      220
    ],
    "frontmost_pid": 220
  },
  "initial_background_relaunch_observed": true,
  "later_nonactivation_check_passed": false,
  "full_background_focus_acceptance_verified": false,
  "focus_transition_cause": "not_attributed; no claim that the user or restart caused it",
  "verification_exception": "install caller exited 1 at the later foreground assertion; installer exited 0",
  "no_extra_restart_or_focus_change_attempted_after_exception": true,
  "no_qa_window_or_global_input": true
}
```
