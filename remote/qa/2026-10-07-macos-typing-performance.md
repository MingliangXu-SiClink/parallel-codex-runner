# macOS 0.1.419 Typing Performance

## Scope

- Client-only root fix based on the live 0.1.418 sampling in `2026-10-07-macos-typing-latency.md`.
- No pcrd restart, Agent/task operation, research-directory change, foreground QA window, or keyboard takeover.
- User authorized publication and background client installation/restart after verification.
- Installation must not implicitly adopt an unchosen result when the old app exits.

## Root Fix

1. Moved prompt text/history publications to composer-owned `PromptDraftState`. The reader/sidebar no longer subscribe to keystrokes. Existing context-bound bindings, persistence, selections, source/receipt identity and send readiness remain authoritative.
2. Activity dates are immutable decode-time projections on the transport decoder, outside the UI actor. Reader/switcher equality compares primitive minute keys and real lifecycle fields, never ISO parsing or localized date formatting. Raw strings and protocol/cache encoding remain unchanged; date formatting stays at the visible presentation boundary.
3. Skipped unchanged connection-loading/error publications. Polling frequency, live output, real waiting observations and compositor breathing animation are unchanged.

## Verification

- 360 Swift tests in 43 suites passed in the offscreen/background run (201.557 s).
- Full client driver also passed 360 tests in 43 suites (196.218 s), protocol contract tests, desktop-state tests and native icon checks.
- 32 Python protocol/release tests plus 7 subtests passed; generated protocol drift check passed.
- 1,000 draft edits interleaved with five-Agent metric snapshots on a 602-item conversation produced zero reader publications and zero timeline rebuilds. Full metrics and immediate send readiness remained current.
- Native editor tests used `NSHostingView` with no window: marked Chinese text, caret/selection and editor identity survived background updates; committed text, external history selection and persistence remained correct.
- Activity tests retain minute-equivalent observation updates, timezone offsets, precise wait deadlines, invalid/missing dates, state transitions and unchanged wire fields.
- Native waiting/activity strips rendered offscreen at 360/760 points in Chinese/English; no guessed external training progress.
- Fixed two stale verification assumptions (parameterized navigation invalidation and the existing destination-opening message), and completed the standalone test driver's SDK/dependency inputs. No product behavior was changed to satisfy an obsolete test.
- Performance numbers are isolated-path measurements, not a claim of measured physical-key-to-pixel latency on the user's desktop.
- Identical five-Agent inputs under the same `swiftc -O` build: original 0.1.418 equality warm median 2.038425 ms, new production equality 0.0017205584 ms. Baseline implementation was copied directly from the saved original source with only type/helper names changed. New equality performs zero date parse/format conversions (previously 20); decode-time work was outside both measurements.

Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/TypingPerformance-20261007`.

Publication, preparation, installation and actual-runtime results are appended below only after their verification.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-07T09:46:31.287210+00:00",
  "macos": "0.1.419",
  "archive_sha256": "bd280ef0e3fd49fff9feb2e1d36d19031d1622023b7f2a4400d4895671858f1a",
  "relay_catalog_verified": true,
  "pcrd_unchanged": "0.1.79",
  "actual_installed_client": [
    "0.1.418",
    "418"
  ],
  "client_tests": 360,
  "client_suites": 43,
  "python_contract_tests": 32,
  "python_subtests": 7,
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-07T09:47:21.561358+00:00",
  "macos_prepared": "0.1.419",
  "actual_installed_client": [
    "0.1.418",
    "418"
  ],
  "actual_pcrd": "0.1.79",
  "scheduler_pid": 31097,
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
  "checked_at_utc": "2026-10-07T09:50:10.520523+00:00",
  "published_and_prepared_macos": "0.1.419",
  "actual_running_client": [
    "0.1.418",
    "418"
  ],
  "client_pid_unchanged": [
    30884
  ],
  "actual_pcrd": "0.1.79",
  "pcrd_pid_unchanged": 31097,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_pids_and_start_times_preserved": true,
  "node_listeners": {
    "80080": [
      "127.0.0.1:5173"
    ],
    "92110": [
      "127.0.0.1:4317"
    ]
  },
  "node_http_status": {
    "5173": 200,
    "4317": 200
  },
  "client_installation_not_attempted_past_exit_gate": true,
  "installation_gate": {
    "reason": "graceful_exit_could_implicitly_adopt_result",
    "workspace_id": "ws-58b586ef27ff4e1b",
    "run_id": "pcr-20261007T091436Z-73f1bf67",
    "status": "completed",
    "client_not_restarted": true
  },
  "no_result_adopted_or_discarded": true,
  "no_task_stopped_or_resubmitted": true,
  "no_qa_window_or_keyboard_takeover": true,
  "backend_not_restarted": true,
  "client_fix_not_yet_enabled": true
}
```
