# Five Workspace Recovery

## Scope And Safety

The user authorized diagnosing and continuing the five latest research conversations. No research workspace was edited, no task stopped, no result adopted or discarded, no original prompt resubmitted, and no visible QA or foreground input was used. The cancelled cloud cleanup remains cancelled. The previously deferred finalization performance redesign remains untouched.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/FiveWorkspaceRecovery-20261010`.

## Observed Causes

- Memory-state cross-individual/day decoding: run `pcr-20261009T165829Z-72e0a5ca` was active. Candidate 1 had failed before any Codex turn, while resolving developer instructions with an app-server initialize/config-read probe. The probe has a 15-second shared initialization/read budget, not a research execution deadline. The error had no thread ID or execution side effect. Other candidates continued normally.
- High-fidelity EEG compression: the opinion turn `pcr-20261009T152041Z-6809bd13` completed, including original selected synthesis Agent 8. The subsequent research request returned a durable `completed/confirmation_required` receipt, estimated 46.60 GiB; no next turn was created. Historical synthesis 6/7 preparation failures do not invalidate the successful selected source 8.
- Human epidural EEG motor decoding: research run `pcr-20261009T165903Z-b06ff0c7` continued generating current tool/training events; four candidates had succeeded by the 18:25 UTC check, with candidate 1 still active.
- Motor-behavior cross-day decoding: the opinion turn `pcr-20261009T152100Z-1765b3cb` completed successfully. Its research continuation also returned `completed/confirmation_required`, estimated 46.62 GiB, with original Agent 8 retained and no new turn created.
- Fine hand motor decoding: research run `pcr-20261009T172232Z-d47fdb49` had current substantive tool/training activity from all five candidates. GPU/resource waiting in research commands is not a terminal PCR failure.

The two storage estimates total 93.22 GiB, versus approximately 78 GiB available at the later check. The source projects are only a few MiB; most of the conservative estimate comes from replicated private Codex support metadata. Logical estimates are not a guarantee of physical copy-on-write disk consumption. No capacity confirmation was bypassed and no cleanup was performed. A user decision on capacity/ordering remains required.

## Recovery Performed

Only the failed, pre-submission candidate 1 of the memory-state run was retried through the existing worker, command `five-workspace-recovery-20261010-memory-preparation-1`. Worker PID 33933 and the original run were preserved. By 18:25 UTC its stdout was over 1 MiB with fresh current tool events, confirming execution resumed rather than merely a status change. Two other candidates in that run had succeeded. Workers 33987 and 40479 remained attached to their original research runs.

## Client Root Fix

The storage wait was persisted but its only actionable presentation was a transient confirmation dialog. Looking at a different Agent hid the original pending submission, while the workspace sending gate remained blocked. The structured capacity receipt was not persisted, so old messages had no durable explanation or confirmation controls. Returning the original message could also overwrite a newer draft.

The client now persists the structured receipt, hydrates legacy receipts via read-only `commands.get`, and displays a persistent capacity-confirmation banner in the original conversation even while viewing another Agent. Confirm/return actions bind the original command ID and original source, not the current editor or viewed Agent. Conditional serialized store updates give confirmation and draft return a single persistent owner; stale actions and late receipt hydration cannot resurrect a resolved item. Draft return is atomic and checks both current and persisted drafts before removing the original message. Capacity approval still uses the original backend preflight; no blanket permission or duplicate submission is introduced.

Source version is macOS 0.1.425/build 425. Release and real runtime verification are recorded below only after completion. The initial test attempt using the machine's default MacOSX27 SDK failed on an unavailable third-party SwiftUI macro. Validation uses the known working MacOSX26.5 SDK; this is separate from the user-task failures.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T18:33:54.065066+00:00",
  "macos": "0.1.425",
  "archive_sha256": "60861e56a6d50bd0eedc3cfcf751182e1d2d763a1a35faa5ee669092c5818335",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.423",
    "423"
  ],
  "client_tests": 416,
  "client_suites": 49,
  "python_contract_tests": 44,
  "python_subtests": 7,
  "storage_confirmation_regressions": 8,
  "test_sdk": "MacOSX26.5.sdk",
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T18:34:30.181188+00:00",
  "macos_prepared": "0.1.425",
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
  "checked_at_utc": "2026-10-09T18:34:54.208445+00:00",
  "published_and_prepared_macos": "0.1.425",
  "actual_running_client": [
    "0.1.423",
    "423"
  ],
  "actual_pcrd": "0.1.80",
  "pcrd_pid_unchanged": 46777,
  "client_and_node_processes_preserved": {
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
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_http_status": {
    "5173": 200
  },
  "installation_gate": {
    "reason": "active_research_and_unresolved_original_storage_confirmations",
    "active_runs": [
      "pcr-20261009T165829Z-72e0a5ca",
      "pcr-20261009T165903Z-b06ff0c7",
      "pcr-20261009T172232Z-d47fdb49"
    ],
    "client_outbox": [
      {
        "id": "6c9c306e-5aaf-4541-8fe7-235f217c564b",
        "workspaceID": "ws-aee1bfca139a41ab",
        "sourceRunID": "pcr-20261009T152100Z-1765b3cb",
        "sourceAgent": 8,
        "status": "awaiting_confirmation",
        "errorCode": null
      },
      {
        "id": "0d5f6ea6-8061-4852-9a78-21878c07b3d6",
        "workspaceID": "ws-32117750ea2e4ba2",
        "sourceRunID": "pcr-20261009T152041Z-6809bd13",
        "sourceAgent": 8,
        "status": "awaiting_confirmation",
        "errorCode": null
      }
    ],
    "client_not_restarted": true,
    "not_installed": true
  },
  "no_result_adopted_or_discarded": true,
  "no_task_stopped_or_resubmitted": true,
  "only_pre_submission_failed_candidate_retried": true,
  "backend_not_restarted": true,
  "client_fix_not_yet_enabled": true,
  "no_qa_window_or_input_takeover": true
}
```
