# Execution CLI Model Catalog and Queue Activation

## Scope

Repair the backend model-catalog read that blocked the user's requested
0.1.73 activation. Publish an immutable 0.1.74 backend containing the complete
0.1.73 queue-continuity repair plus the catalog repair. Do not replace an
existing wheel or runtime, restart a client, display a QA window, steal focus,
change a user queue, or stop/resubmit a task. Activate only while every global
run, finalization, operation, prompt admission, and prompt delivery is idle.

## Confirmed Cause

The execution CLI is `/opt/homebrew/bin/codex`, version 0.159.2. A different
installation wrote the shared native cache with version 0.160.0. Rejecting that
file for the execution CLI is correct and remains enforced.

The old menu reader treated production of `models_cache.json` as the completion
condition. Its private app-server process discarded stdout and waited for that
file. An independently initialized, read-only `model/list` actually returned
eight models including GPT-6.1 Sol, without producing the file. The failure was
the application's incorrect completion condition, not proof of an unavailable
model. Increasing the file wait does not fix that contract.

The old installed 0.1.73 reproduces the missing-menu failure against a local
protocol fixture returning a complete model list without a native cache.
`baseline.log` records the failing assertion. The fixture submits no thread,
turn, or real task.

## Repair

- A small synchronous protocol reader waits for the matching initialize result,
  sends the initialized notification, then reads all `model/list` pages.
- Request IDs, notification interleaving, timeouts, malformed entries, conflicting
  models, cursor cycles, and incomplete pagination are handled before publication.
- Menu metadata is atomically stored as application-owned
  `pcr-model-catalog.json`, with an explicit schema/source marker. It is never
  synthesized as, accepted as, or copied to an Agent's native execution cache.
- CLI version/path, account, configuration, and provider environment partition
  the snapshot. A changed source or CLI during refresh cannot publish or fall
  back to the previous source's late result. OAuth rotation for the same account
  does not unnecessarily discard valid menu metadata.
- Transient same-context failures preserve a last-good snapshot without changing
  its timestamp. A complete empty catalog is authoritative, not a failed refresh.
- Native worker-cache preparation remains optional and separate. The existing
  pipeline tests execute successfully when native-cache population returns false.

The independent architecture review highlighted the menu/native-cache boundary,
complete pagination, late context changes, and the optional worker-cache path.
These are verified with local tests and real IPC, not accepted solely because a
second model agreed.

## Verification

Final source suite: 971 passed and 104 subtests passed. Focused catalog, pipeline,
queue, receipt, and delivery suites: 255 passed and 22 subtests passed.

The macOS source and binary are not modified. The existing 339-test headless
client acceptance and offscreen resource acceptance are retained for the
unchanged 0.1.416 binary; signature and source timestamps are checked again.
No foreground client or QA is started for these checks.

Installed-runtime tests, the fresh real installed IPC probe, wheel hashes,
publication, and actual scheduler activation are recorded in the evidence
directory and appended below once executed. This section does not claim that
staging alone enabled the new code.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ModelCatalog-20261005/`.

## Installed and Production Acceptance

The installed 0.1.74 runtime passed 970 tests, one repository-only test was
deselected, and 104 subtests passed. Its package source bytes match the released
wheel. The only core-module differences from 0.1.73 are the catalog reader and
cache manager; every other core module, including the queue repairs, is preserved
byte for byte. The original 0.1.73 wheel remains unchanged.

Wheel SHA-256:
`05293903a2e06661416534aee9c18d54c11919c1c9e766cc62941a138f763b99`.

A fresh installed-runtime Unix IPC server returned eight models including
GPT-6.1 Sol through `codex.model_options` without a selected-model argument.
The first request took 2.3634 seconds; the same-context cached request took
0.0023 seconds. It used the real 0.159.2 CLI, did not require or forge a native
execution cache, did not submit a task, and left shared auth/config/cache bytes
unchanged. The activation preflight repeated this fresh isolated verification.

Actual scheduler activation completed at 2026-10-05 15:22:21 UTC after repeated
manager-wide idle and prompt-delivery checks. PID changed from 41934 to 74200.
Only the LaunchAgent runtime interpreter changed, from 0.1.72 to 0.1.74.
The service was healthy, paired, connected, and advertised the 0.1.73 enqueue
reconciliation capability and previous visual-comparison capability. Pairing
identity and the durable command receipt store remained unchanged.

An independent production check at 15:23:25 UTC confirmed the actual 0.1.74
interpreter, healthy service, no active runs/operation, connected pairing, and
eight real menu models including GPT-6.1 Sol. Its cached menu request took
0.0070 seconds. Catalog availability is not claimed to be an inference test;
no real research task was started for acceptance.

No macOS install/restart or QA window was used. The installed 0.1.416 client
binary, all baseline Node processes, and foreground PID 72242 were unchanged.
The already edited paused queue stayed at generation 34, attempt 0, with the
same item identity. Its version guard intentionally prevented maintenance from
overwriting the later user edit; the old item was not silently resubmitted.
The new queue admission and narrow recovery code is now active for eligible
records, but this edited record is not claimed to have recovered or been sent.

Evidence: `installed.log`, `installed-rpc-probe.json`, `staged-release.json`,
`activation-fresh-model-preflight.json`, `deployment-0.1.74.json`, and
`independent-production-verification.json`.

## Activation 2026-10-05T15:22:21.366049+00:00

```json
{
  "actual_version": "0.1.74",
  "healthy": true,
  "health_before": {
    "healthy": true,
    "detached_workers": true,
    "active_runs": [],
    "active_operation": null,
    "global_active_runs": [],
    "global_active_operation": null,
    "prompt_delivery": {
      "running": true,
      "pending_count": 0,
      "active_count": 0
    },
    "prompt_admission_count": 0,
    "link_state": "connected"
  },
  "health_after": {
    "healthy": true,
    "detached_workers": true,
    "active_runs": [],
    "active_operation": null,
    "global_active_runs": [],
    "global_active_operation": null,
    "prompt_delivery": {
      "running": true,
      "pending_count": 0,
      "active_count": 0
    },
    "prompt_admission_count": 0,
    "link_state": "connected"
  },
  "scheduler_pid_before": 41934,
  "scheduler_pid_after": 74200,
  "model_count": 8,
  "gpt_6_1_sol_present": true,
  "supports_follow_up_enqueue_reconciliation": true,
  "supports_visual_file_comparison": true,
  "controller_paired": true,
  "paired_machine_and_receipt_store_preserved": true,
  "scheduler_restarted": true,
  "client_restarted": false,
  "client_binary_unchanged": true,
  "baseline_client_and_node_processes_preserved": true,
  "changed_baseline_pids": [],
  "service_changed_only_runtime_interpreter": true,
  "foreground_before": 72242,
  "foreground_after": 72242,
  "queue_before": {
    "generation": 34,
    "state": "waiting",
    "run_id": "pcr-20261004T105322Z-702a431f",
    "items": [
      {
        "id": "4fba1ec5-0781-4a0d-bf72-47f611573a0b",
        "paused": true,
        "attempt": 0,
        "failure_count": 0
      }
    ]
  },
  "queue_after": {
    "generation": 34,
    "state": "waiting",
    "run_id": "pcr-20261004T105322Z-702a431f",
    "items": [
      {
        "id": "4fba1ec5-0781-4a0d-bf72-47f611573a0b",
        "paused": true,
        "attempt": 0,
        "failure_count": 0
      }
    ]
  },
  "edited_paused_queue_untouched": true,
  "queue_writes_by_maintenance": false,
  "tasks_resubmitted_or_stopped_by_maintenance": false,
  "qa_windows_opened": false,
  "checked_at_utc": "2026-10-05T15:22:21.366049+00:00"
}
```
