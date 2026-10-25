# macOS Queued-Message Removal, 2026-10-10

## Scope

The user intentionally removes queued messages and does not want an Undo action.
Build 426 removes the composer removal banner, queue-manager restoration button,
deleted-item snapshot, and the operation that could reinsert the removed item.
The deletion still operates on the latest queue generation by message identity;
dispatch locks, concurrent-message protection and save failures remain unchanged.
Native editor undo, conversation restoration, drag ordering and editing remain.
No pcrd, Android, research workspace or running Agent changes are included.

## Verification Plan

- Isolated socket regressions: successful deletion is saved once, retains other
  messages and survives older responses; a failed save keeps the queue and error;
  deleting the final item removes the persisted queue and countdown.
- Full noninteractive client suite with SDK 26.5; focus-changing suites excluded.
- Protocol, release, receipt and existing queue regression tests.
- Background signed build and windowless packaged-resource smoke test.
- Immutable release archive, relay catalog and built-in update verification.
- Actual runtime versions and process continuity; no installation or restart
  while research tasks and original unconfirmed messages remain active.

Actual test counts, publication and runtime checks are appended after execution.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T18:50:50.667807+00:00",
  "macos": "0.1.426",
  "archive_sha256": "ba475ec2a7825a3b7a6863e474f5ba5f813f1dc295ac24c29600a1551ca9900b",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.423",
    "423"
  ],
  "client_tests": 419,
  "client_suites": 49,
  "python_contract_and_queue_tests": 112,
  "python_subtests": 7,
  "queue_removal_regressions": 3,
  "test_sdk": "MacOSX26.5.sdk",
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T18:51:20.614210+00:00",
  "macos_prepared": "0.1.426",
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

## Actual Runtime

```json
{
  "checked_at_utc": "2026-10-09T18:51:40.695324+00:00",
  "published_and_prepared_macos": "0.1.426",
  "actual_running_client": [
    "0.1.423",
    "423"
  ],
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
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
  "active_runs": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T165903Z-b06ff0c7",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "client_outbox": [
    {
      "id": "0d5f6ea6-8061-4852-9a78-21878c07b3d6",
      "workspaceID": "ws-32117750ea2e4ba2",
      "sourceRunID": "pcr-20261009T152041Z-6809bd13",
      "sourceAgent": 8,
      "status": "awaiting_confirmation",
      "errorCode": null
    },
    {
      "id": "6c9c306e-5aaf-4541-8fe7-235f217c564b",
      "workspaceID": "ws-aee1bfca139a41ab",
      "sourceRunID": "pcr-20261009T152100Z-1765b3cb",
      "sourceAgent": 8,
      "status": "awaiting_confirmation",
      "errorCode": null
    }
  ],
  "not_installed": true,
  "reason": "publication_requested_and_active_research_or_unresolved_messages",
  "no_task_or_queue_mutated": true,
  "no_result_adopted_or_discarded": true,
  "no_service_or_client_restarted": true,
  "no_qa_window_or_input_takeover": true
}
```
