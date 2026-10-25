# Resume Deadline Repair and Original Task Recovery

## Confirmed Cause

The text `Codex thread/resume did not respond within 90 seconds.` is generated
by PCR's 0.1.74 app-server adapter. It is not evidence of an OpenAI execution
deadline. The adapter wrapped every RPC, including restoring a source thread,
in a fixed 90-second timeout, then failed the Agent and closed its child process.

The user's failed run is `pcr-20261005T161214Z-e07f19f2`, Agent 1, workspace
`/Users/mingliangxu/Desktop/TunLSCI重投`, private metadata under
`/Users/mingliangxu/.codex_parallel_runs/20261006_001315/meta/agent_001`.
Its only stdout event is the exact resume-timeout error; no execution thread or
turn started. In that installed adapter, the original prompt is submitted only
after resume returns. Thus this failure occurred before submitting the new
question, rather than halfway through publishing a Feishu document.

The retained private history is approximately 1.84 GiB. A slow restore is a
plausible contributor, but the trace does not establish a single underlying
Codex performance bottleneck. Removing the arbitrary cutoff fixes premature
failure; it does not by itself prove that history restoration became faster.

## Repair

- Business RPCs remain pending until their real response, explicit error,
  disconnection, process exit, or user cancellation. No quiet-output deadline
  replaces the 90-second limit, and delayed receipts never cause resubmission.
- Process liveness polling remains responsive even if a descendant retains the
  stdout pipe. Cancellation yields before writing a new request.
- A matching real turn acceptance emits `turn.started`, not a local send attempt.
  A confirmed same-turn terminal event completes its business lifecycle even if
  the `turn/start` response is missing. An unrelated terminal event is ignored.
- Removed the other automatic 180-second cold-start kill driven by repeated
  model-refresh diagnostics. Real process errors and explicit cancellation remain.
- Existing idle workers retain their originally loaded adapter. An explicit,
  narrow startup recovery can retire only an idle worker after proving this exact
  failure was before submission. Normal retry behavior and crashed-worker guards
  are unchanged. It cannot replace active Agents or unknown turn receipts.
- Recovery preserves the original run, Agent, prompt, source session, command,
  private rollout, and SQLite paths. It archives the failed attempt's logs without
  recopying a newer source conversation. A changed binding between preflight and
  preparation prevents execution.

## Acceptance Boundary

All tests and protocol fixtures run at low priority without a foreground app,
QA window, focus change, or real model task. Actual scheduler activation is
allowed only after every global Agent, synthesis, finalization, operation,
prompt admission, and delivery is idle. The macOS client remains 0.1.416 and
is not restarted. Only the specifically authorized failed task may be recovered.

Publication, installed-runtime tests, an actual subprocess resume delayed over
90 seconds, scheduler health checks, and real task recovery will be appended
with their true outcomes. Staging alone is not activation or task recovery.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ResumeDeadline-20261006/`.

## Activation 2026-10-05T17:28:11.826920+00:00

```json
{
  "actual_version": "0.1.75",
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
  "scheduler_pid_before": 74200,
  "scheduler_pid_after": 12272,
  "model_count": 8,
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
  "checked_at_utc": "2026-10-05T17:28:11.826920+00:00"
}
```

## Acceptance and Corrected Recovery Observation

0.1.75 passed the source suite (983 tests and 120 subtests), and its installed
runtime passed 982 tests and 120 subtests; only the repository-layout test was
deselected. A real subprocess protocol fixture restored a thread for 91 seconds
and then successfully submitted exactly one turn (91.88 seconds total). Explicit
cancellation during restoration completed in 0.81 seconds with zero turn
submissions. These fixtures did not contact the user's actual model or open UI.

The earlier recovery observer recorded below watched the **old** artifact root
`20261006_001315`, so its zero-turn observation must not be interpreted as the
current task still being stuck in resume. The live run's new root is
`20261006_012831`. A read-only check at 2026-10-05 17:47 UTC found one
`thread.started`, one `turn.started`, 117 `item.started`, 116 `item.completed`
and 46 usage events there. The original prompt and source session are unchanged;
the original question was submitted once, and the task is actually executing.
No unknown turn was resubmitted and no running Agent was interrupted.

The root changed due to a newly identified persistent-worker handoff race:
the retiring idle worker's durable stopped event was consumed after the
replacement retry was marked busy. It cleared the mutable operation field, and
an empty operation defaulted to initial execution. The old private history and
failed logs remain untouched, but they were **not reused** by that production
recovery. It is incorrect to claim otherwise.

0.1.76 consumes the old terminal event before marking the replacement busy,
dispatches an immutable operation value, and rejects a retained-recovery request
with any initial/different operation before execution. Regression tests reproduce
the two failures against installed 0.1.75. A real detached-worker integration
test additionally verifies the same run root, original history and database
inodes, archived failed logs and exactly one accepted turn. The already-running
user task must not be retried to demonstrate this fix. 0.1.76 is a separate
immutable release; it will only be activated when every task and operation is
idle. Its validation and actual deployment state will be appended below.

## Original Task Recovery 2026-10-05T17:34:29.735006+00:00

This historical observer used the wrong artifact root. See the corrected
observation above and the confirmed completion below; its zero-turn result is
not the actual execution state of the recovered run.

```json
{
  "run_id": "pcr-20261005T161214Z-e07f19f2",
  "agent": 1,
  "original_worker_pid": 84582,
  "new_worker_pid": 12321,
  "actual_version": "0.1.75",
  "run_status": "running",
  "agent_status": "running",
  "worker_active": true,
  "observation_seconds": 361.5748391250381,
  "accepted_turns_observed": 0,
  "new_work_events_observed": 0,
  "confirmed_execution_resumed": false,
  "retained_private_history_inodes_preserved": true,
  "original_prompt_and_session_unchanged": true,
  "failed_attempt_logs_archived": false,
  "only_original_task_recovered": true,
  "no_automatic_resubmission_of_unknown_turn": true,
  "healthy": true,
  "active_runs": [
    "pcr-20261005T161214Z-e07f19f2"
  ],
  "client_restarted": false,
  "qa_windows_opened": false,
  "checked_at_utc": "2026-10-05T17:34:29.735006+00:00"
}
```

## Original Task Confirmed Complete 2026-10-05T17:56:49.747564+00:00

The same managed run `pcr-20261005T161214Z-e07f19f2` is now `completed`,
Agent 1 is `success` (return code 0), and its worker is idle. The actual
artifact root is `20261006_012831`; the original session remains
`01a0c81b-7e07-7b31-a2de-7b18a42d5db3`. The successful attempt took
1261.66 seconds in total, including its real document work, not just startup.
Its stdout contains exactly one thread start, one accepted turn
`01a10d1d-7292-74b3-9deb-2fd83907f932`, 121 completed work items and one
successful turn completion. There were no duplicate accepted turns.

The Agent's final response reports that the requested Feishu document was
created at `https://lcnftrea29v5.feishu.cn/docx/FUZadcIZhoOGcyxcg1Kcw44An3d`.
This is the recovered Agent's result, not a separate maintenance-side document
content verification. The scheduler reported no active runs, active operation,
pending/active delivery, or prompt admission after completion.

## Activation 2026-10-05T18:02:37.610554+00:00

```json
{
  "actual_version": "0.1.76",
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
  "scheduler_pid_before": 12272,
  "scheduler_pid_after": 23860,
  "model_count": 8,
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
  "checked_at_utc": "2026-10-05T18:02:37.610554+00:00"
}
```

## 0.1.76 Final Acceptance

0.1.76 is now published in the local release manifest, installed in its own
runtime and actually enabled as the healthy, paired scheduler. It was enabled
only after the recovered task had completed and all global runs, operations,
prompt admissions and deliveries were idle. GPT-6.1 Sol remains in the live
eight-model catalog. Existing client and Node PIDs, pairing, receipt store and
service settings were preserved. The macOS client is still 0.1.416 and was not
restarted; there was no foreground QA app, window or focus action.

- Source suite: 985 tests and 120 subtests passed in 95.51 seconds.
- Installed 0.1.76 suite: 984 tests and 120 subtests passed in 92.42 seconds.
  Only the repository-layout vendored-Textual test was deselected.
- The two targeted handoff regressions fail against immutable installed
  0.1.75, and pass in 0.1.76. The real detached-worker test also passes.
- Installed 0.1.76 real subprocess resume fixture: success after 91.094 seconds,
  one turn submission. Explicit cancellation during resume: cancelled after
  0.818 seconds, zero turn submissions. These are measured fixture results, not
  general latency guarantees.
- A separate controlled-clock test exercised the real outer `run_one_agent`
  loop with two model-refresh timeout diagnostics and 201 seconds of simulated
  startup elapsed time, without changing asyncio's clock. Installed 0.1.74
  prematurely failed and terminated its child; installed 0.1.76 waited for that
  child and succeeded. This independently covers the former 180-second guard.
- Published 0.1.75 wheel remains unchanged. The 0.1.76 wheel SHA-256 is
  `0916d0a82f9d6484c4ae3fb5f787a2a691e983ce517a555616830bf933d32202`.
  Its core changes versus 0.1.75 are limited to the runtime handoff manager and
  worker request validation; all timeout-removal adapter bytes are unchanged.

The old worker writes its stopped event before its stopped checkpoint and emits
no later events. Recovery consumes that durable range and persists its event
cursor before creating the replacement operation. The cursor and busy operation
are stored together in the run-state snapshot under the existing manager/store
locks; this is not a general claim that arbitrary injected stale events are
safe. The actual source-history performance remains a separate problem: removing
an arbitrary cutoff improves reliability, not proof of faster history parsing.

Full immutable release, protocol, handoff baseline and cold-start evidence:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ResumeHandoff-20261006/`.

The legacy follow-up ID `pcrd-0-1-68` no longer exists in the app; the automation
tool reported that it could not be updated. No duplicate or replacement
automation was created because deployment is already complete. This does not
change the independently verified live scheduler and recovered task results.
