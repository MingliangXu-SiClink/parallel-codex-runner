# Automatic Agent Recovery

## Scope

Backend 0.1.86 resumes retryable interrupted execution in the same private workspace,
Codex home and native thread. It preserves original input identity and completed
tool output; the original user prompt is not blindly replayed. Backoff is bounded
in delay, not in number of attempts. Explicit stop intent is durable and always
preempts recovery. No model, sandbox, approval or remote-access permissions change.

A healthy native app-server stays alive during upstream model outages so that its
background terminals can continue. A dead execution adapter is relaunched only
when its durable identity matches and its native owner has also exited. Receipt
ambiguity remains a background reconciliation state, not permission to resend.
Authentication, invalid configuration and damaged identity remain explicit blocks.

This does not replace a crashed persistent PCR batch worker with newer code. It
does not revive stopped, adopted or already superseded historical Agent results.
Existing pinned workers continue to use their original version. External training
exactly-once semantics cannot be guaranteed by model instructions alone; the
native receipt prevents input replay and recovery guidance requires read-only job
reconciliation before any repeated side effect.

## Verification Boundary

New recovery tests cover repeated transient failures, lost input/completion
receipts, durable restart, ownership conflicts, stop precedence, error metadata,
background output and in-place adapter recovery. Full source and independently
installed wheel suites are recorded below, along with immutable artifact hashes.

Native CLI acceptance uses isolated temporary homes and a loopback synthetic
Responses provider. It executes a synthetic job once, forces an overload, resumes
the same thread and checks completed tool output and a retained live background
terminal. It submits no real user request and launches no real research job.
The initial fixture exposed the native item-page envelope shape; a later fixture
assertion was tightened to inspect actual tool output rather than echoed command
text. Only the final corrected fixture is release acceptance evidence.

The source pytest suite also exposed an existing headless TUI assertion that
waited only one idle cycle despite the tail-follow implementation scheduling a
second geometry refresh. The assertion now waits up to three refresh cycles,
without forcing the scroll position or weakening its final condition. No TUI or
macOS scrolling implementation was changed for this recovery release.

The existing ChatGPT Web consultation was attempted in the embedded browser but
the reply could not be loaded. No unreadable partner reply is used as validation.

macOS and Node processes are not restarted by this backend release. Scheduler
activation is permitted only after repeated local/global task, operation, message
admission, delivery and follow-up queue idle checks, with real model availability,
pairing, compatible client and rollback gates. No user task is stopped or resent.

## Published Backend

```json
{
  "checked_at_utc": "2026-10-14T13:08:47.677546+00:00",
  "pcrd": "0.1.86",
  "wheel_sha256": "6f3df6e4534288727ab99f86290cfb5bf15d0252655914034d06219c7cf0e980",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Activation 2026-10-14T13:09:38.983356+00:00

```json
{
  "actual_version": "0.1.86",
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
  "scheduler_pid_before": 65368,
  "scheduler_pid_after": 56812,
  "model_count": 7,
  "gpt_6_1_sol_present": true,
  "controller_paired": true,
  "paired_machine_and_receipt_store_preserved": true,
  "scheduler_restarted": true,
  "client_restarted": false,
  "client_binary_unchanged": true,
  "baseline_client_and_node_processes_preserved": true,
  "changed_baseline_pids": [],
  "service_changed_only_runtime_interpreter": true,
  "qa_windows_opened": false,
  "tasks_resubmitted_or_stopped_by_activation": false,
  "checked_at_utc": "2026-10-14T13:09:38.983356+00:00"
}
```

## Verified Release

```json
{
  "checked_at_utc": "2026-10-14T13:13:21.705026+00:00",
  "source_tests": "1102 passed, 210 subtests passed in 96.25s (0:01:36)",
  "installed_tests": "1101 passed, 1 deselected, 210 subtests passed in 101.11s (0:01:41)",
  "activation_gate_tests": "16 passed in 1.49s",
  "native_cases_per_scope": 3,
  "native_scopes": [
    "source",
    "installed"
  ],
  "native_cli": "codex-cli 0.159.2",
  "real_user_turn_submissions": 0,
  "changed_runtime_modules": [
    "parallel_codex_runner_core/app.py",
    "parallel_codex_runner_core/codex_app_server.py",
    "parallel_codex_runner_core/models.py"
  ],
  "previous_runtime_source_bytes_preserved": true,
  "wheel_sha256": "6f3df6e4534288727ab99f86290cfb5bf15d0252655914034d06219c7cf0e980",
  "installed_macos_build": 429,
  "installed_macos_signature_verified": true,
  "actual_scheduler_version": "0.1.86",
  "activation_record": "/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentRecovery-20261014/deployment-0.1.86.json"
}
```
