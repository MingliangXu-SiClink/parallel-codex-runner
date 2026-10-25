# macOS Queue Interaction, 2026-10-10

## Scope And Constraints

- User requested draggable queued messages, the supplied right-facing list/turn-arrow icon, simpler queue operations, and automatic publication.
- Client-only change. Do not restart pcrd, interrupt Agents, mutate real queues for QA, submit real turns, or revive the cancelled cloud cleanup.
- All UI validation uses synthetic messages, a temporary socket/state store, or an NSWindow that is never shown. No global input, focus changes, or visible QA application.
- The separately postponed result-finalization performance work is unchanged.

## Old Interaction And Root Cause

- Composer rows permanently exposed edit/delete/more controls; ordering required menu steps.
- The manager copied the entire queue at opening, made every row an editor, and wrote the old array on Apply.
- The manager also retained its opening generation for writes and stopped the client queue monitor. These semantics were inconsistent with direct composer actions and with a server that continues to consume the queue.
- There was no stable-ID relative drag command or drag scope. A visual-only rearrangement would not solve consumption, concurrent enqueue, or sending-lock races.

## New Contract

- Compact and full views share the same live list and command path. Ordinary rows are summaries, not text editors.
- Drag starts only on a handle, previews an insertion line, and submits once on drop. Cancel/hover do not save. The native payload contains only message ID and queue context, not message text.
- Drop positions refer to another message ID, before/after, not an old index or a hidden queue end. Compact scrolling remains bounded to three rows.
- Each change reads the latest server queue and uses that queue's generation for the existing conditional replacement. Concurrent additions, message identity, settings and retry metadata are retained.
- Sending items and positions across their lock cannot be edited, removed, or reordered. The unlocked tail can still move. Missing source/target IDs cannot be reinserted by a stale drag.
- No-op placements do not write or reset the countdown. Existing revision filtering rejects late snapshots.
- The manager has no array draft or global Apply/Cancel. Close does not undo confirmed changes or pause daemon consumption. Single-message editing keeps its own Save/Cancel and original-text guard.
- Deletion retains the existing Undo operation. Precise Move Earlier/Later and editing remain available in the menu for non-drag use.
- The queue header/entry and enqueue action use a shared thin, right-facing Lucide list-end outline; its ISC notice is packaged.
- The full list height and action lanes remain stable during refresh and button appearance changes.

## Verification Boundary

Focused native provider/drop tests and real AppModel calls against an isolated socket cover queue semantics; hidden hosting tests cover widths, text sizes, row states and a live full manager. They do not constitute foreground pointer/VoiceOver acceptance or a real user-queue reorder.

Initial SwiftPM attempts used the default build system without the project's CLI Testing framework setup and did not run tests. The native build exposed an existing foreground-only observer's Swift 6 isolation error; the observer now captures an explicitly MainActor Sendable recording closure without weakening its assertions. That suite remains excluded from execution by the existing no-foreground gate.

The first full-script attempt stopped at an obsolete static check for the removed 62-point queue text editor. The check now requires scaled heights in the shared queue row; the existing real native size tests exercise all display sizes. The Python queue-binding tests require `tests` alongside `src` on PYTHONPATH, as they import the shared FakeRunManager fixture directly; the first collection attempt without that path did not execute tests.

Manual queue commands pass a generation scoped to their fresh visible queue. Background queue migration/enqueue retains its previous optional-generation behavior rather than borrowing another workspace's visible generation.

Publication, preparation, actual installed/running versions, and any installation deferral are recorded separately below after verification.

## Background Verification

- Focused native queue tests: 14 tests in 2 suites passed. Native NSItemProvider drop callbacks ran against synthetic payloads; no real pointer event or user queue was used.
- Full client script: 408 tests in 49 suites passed, plus the standalone protocol, desktop-state and icon checks. Foreground-only suites remained excluded by the existing safety gate.
- Python queue, turn-binding, release and conversation-contract regression: 100 tests and 7 subtests passed.
- Actual native hosting renders were inspected at narrow/wide widths and all four text sizes in light/dark appearances. Both queue views stay bounded; rows have fixed action lanes, two-line full-list previews and no always-open text editors.
- Offscreen render files: `QueueInteraction-20261010/queue-compact-350.png`, `queue-compact-1000.png`, `queue-manager.png` in the maintenance cache. These are synthetic verification artifacts, not screenshots of the user's current queue.

The inherited final runtime helper initially failed at its obsolete HTTP probe for port 4317. Read-only process/listener inspection confirms the retained Node PID 68341 is the SiCLab server listening on 127.0.0.1:5173; no 4317 frontend is part of the current baseline. The failure JSON is retained, and final verification checks the actual existing listener, HTTP response and unchanged PID/start time instead. No service was started, stopped or restarted to satisfy this check.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T17:36:16.748978+00:00",
  "macos": "0.1.424",
  "archive_sha256": "341214dfa251415a1328a9e9ba4d3d0e1fd6ccdd1160695ba9be0c5a11857cb8",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.423",
    "423"
  ],
  "client_tests": 408,
  "client_suites": 49,
  "python_contract_and_queue_tests": 100,
  "python_subtests": 7,
  "focused_tests": 14,
  "focused_suites": 2,
  "native_provider_drop_verified": true,
  "real_user_queue_untouched": true,
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T17:37:05.451885+00:00",
  "macos_prepared": "0.1.424",
  "actual_installed_client": [
    "0.1.423",
    "423"
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
  "checked_at_utc": "2026-10-09T17:45:00.573614+00:00",
  "published_and_prepared_macos": "0.1.424",
  "actual_running_client": [
    "0.1.423",
    "423"
  ],
  "client_and_node_pids_and_start_times_preserved": {
    "33777": [
      "Sat",
      "Oct",
      "10",
      "00:57:54",
      "2026",
      "/Applications/Parallel Codex.app/Contents/MacOS/Parallel Codex"
    ],
    "68341": [
      "Fri",
      "Oct",
      "9",
      "18:00:16",
      "2026",
      "/opt/homebrew/Cellar/node/26.0.0/bin/node"
    ]
  },
  "actual_pcrd": "0.1.80",
  "pcrd_pid_unchanged": 46777,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "active_runs": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T165903Z-b06ff0c7",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "node_listeners": {
    "68341": [
      "127.0.0.1:5173"
    ]
  },
  "node_http_status": {
    "5173": 200
  },
  "obsolete_4317_probe_failure_retained": {
    "step": "final-status",
    "error": "<urlopen error [Errno 61] Connection refused>",
    "actual_client": [
      "0.1.423",
      "423"
    ],
    "actual_pcrd": "0.1.80"
  },
  "client_installation_not_attempted_past_exit_gate": true,
  "installation_gate": {
    "reason": "active_task_or_message_delivery",
    "client_not_restarted": true
  },
  "no_result_adopted_or_discarded": true,
  "no_task_stopped_or_resubmitted": true,
  "no_qa_window_or_keyboard_takeover": true,
  "backend_not_restarted": true,
  "client_fix_not_yet_enabled": true
}
```
