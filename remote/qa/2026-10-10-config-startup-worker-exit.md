# Native Startup Guidance and Worker Exit Classification

## Observed Incident

The screenshot matches run `pcr-20261009T151908Z-87efbb06`. Its five
candidates succeeded, synthesis Agents 6 and 8 failed during the 15-second
configuration probe, and synthesis Agent 7 succeeded. The retained run is now
finalized. The original worker's log records initial completion and subsequent
finalization with exit code 0. Those records do not establish that the worker
actually crashed when the historical UI error appeared.

The deployed 0.1.80 source still had two independently verified defects:

- Native preparation launched an extra app-server to resolve developer
  guidance, with a shared initialization/configuration deadline and shared
  configuration cache. This could reject otherwise usable synthesis or history
  preparation. Legacy fallback could also lose configured guidance.
- Worker exit classification could inspect stale run state after its first
  event read, even though the worker committed completion before exiting.
  A deterministic regression reproduces this race. It does not prove that this
  race caused the exact historical screenshot.

## Implementation

Normal native preparation now constructs only PCR stage, tool-lifecycle and
workspace/remote requirements. The single actual execution app-server retains
native configuration and history semantics. PCR guidance is delivered through
`turn/start.additionalContext`, using one stable source key and application
context, rather than replacing native developer instructions. A complete
current value explicitly scopes older PCR stage/workspace guidance without
overriding non-PCR guidance.

The actual server's initialization identity must identify a supported release
(0.159.2 or later) before any input is submitted. Unknown/older servers fail
closed. Explicit legacy exec remains separate: it resolves the actual private
home/cwd each time, does not share another Agent's configuration, and cannot
silently drop configured guidance on failure. No automatic transport fallback
or duplicate submission was added after a turn might have started.

Worker failure detection now drains the durable event tail after observing
exit, before classification. Genuine unfinished operations, invalid/missing
checkpoints and pinned-source mismatch guards remain enforced. No replacement
worker is manufactured to continue a genuinely interrupted operation.

## Native Evidence

An initial prototype merged `config/read` guidance into thread-level developer
instructions. Mock tests passed, but actual Codex CLI 0.159.2 cold resume omitted
the next-stage marker from the model request. That prototype was rejected.

The final native acceptance runs the production command builder and Agent
adapter against Codex CLI 0.159.2, with isolated temporary homes and a loopback
Responses stub. External proxies are blocked for this fixture. No production
auth, real user task, research workspace, or external model is used in the final
acceptance. Earlier failed exploratory fixture attempts are not counted as
passed acceptance.

Each source/installed acceptance covers nine synthetic turns: candidate and
synthesis start, cold resume with new guidance, cold resume returning to the
first guidance, and a no-PCR native control through the same three stages.
The complete current fragment is verified in the actual provider request's
developer role, not merely in an RPC acknowledgement or historical substring.
Changed native config A/B behavior matches the no-PCR control. This proves
transport and context preservation, not model obedience or research quality.

## Boundaries

Only `app.py`, `codex_app_server.py` and the worker-exit classification method
in `plugin_runtime.py` change runtime behavior. Backend/plugin version metadata
is synchronized. Existing history, message receipts, queues, permissions,
finalization performance behavior, and client code remain unchanged.

Publication and isolated runtime installation are not service activation.
Activation is permitted only when all user runs, operations and message queues
are idle. Existing user tasks are not stopped, adopted, discarded, resumed,
retried or resent by this repair. No macOS/Node service restart, foreground QA
window, Android operation or remote research job is performed.

## Required Metadata Hardening in 0.1.82

Final review found a further fail-open edge in 0.1.81: native PCR execution
treated a missing prepared guidance file as a no-PCR adapter invocation. The
0.1.82 Agent execution entry requires this file before adapting or spawning
any native command. Missing/unreadable files, malformed JSON, non-string and
blank values fail before input submission. Candidate and synthesis roles are
covered by regressions asserting zero adapter and subprocess calls.

The no-PCR native acceptance control now invokes the adapter directly. It does
not weaken the mandatory PCR execution path. The source acceptance again
passed all nine synthetic loopback turns with preserved current context and
native configuration semantics. Full source/installed tests and the separate
idle-only activation gates are required before this version is enabled.

The already released 0.1.81 wheel and runtime remain immutable. Version 0.1.82
changes `run_one_agent` and the explicit retained-startup preparation relative to
that release. Full testing exposed the latter compatibility path: a verified
old 90-second pre-submission failure predates per-turn metadata. Only after its
original command/input/history fingerprint is checked, its preparer generates
the same current PCR requirements as normal native preparation. It does not
recopy history, change the original command/input, or permit unknown submissions
to be recovered. Existing per-turn guidance is validated and included in that
fingerprint, including its presence; changes after preflight and symlink paths
are rejected before archiving. The normal execution guard has no such fallback. This is
covered by real detached-worker RPC fixture acceptance, not a user task.
Its independent evidence
is in the `ConfigResolution-0.1.82-20261010` maintenance directory. The records
below distinguish the intermediate 0.1.81 deployment from the final version;
none of the retained old pinned workers is claimed to have been hot-upgraded.

## Activation 2026-10-09T21:36:18.615396+00:00

```json
{
  "actual_version": "0.1.81",
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
  "scheduler_pid_before": 46777,
  "scheduler_pid_after": 39040,
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
  "checked_at_utc": "2026-10-09T21:36:18.615396+00:00"
}
```

## Verified Deployment

```json
{
  "checked_at_utc": "2026-10-09T21:36:58.645528+00:00",
  "actual_pcrd": "0.1.81",
  "source_tests": "1042 passed, 138 subtests passed in 106.67s (0:01:46)",
  "installed_tests": "1041 passed, 1 deselected, 138 subtests passed in 105.45s (0:01:45)",
  "activation_tests": "16 passed in 0.32s",
  "native_synthetic_loopback_turns_per_runtime": 9,
  "real_user_turn_submissions": 0,
  "wheel_sha256": "c8cd367b7ee98b21bb0e2e48231fbd9e9aee05d164f6788908a3d4e5624f91e5",
  "client_and_node_processes_preserved": true,
  "idle_pinned_worker_processes_preserved": 6,
  "saved_awaiting_confirmation_messages_preserved": 2,
  "receipt_store_preserved": true,
  "actual_macos": [
    "0.1.429",
    "429"
  ],
  "healthy_paired_GPT_6_1_Sol_available": true
}
```

## Activation 2026-10-09T21:53:54.410426+00:00

```json
{
  "actual_version": "0.1.82",
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
  "scheduler_pid_before": 39040,
  "scheduler_pid_after": 48735,
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
  "checked_at_utc": "2026-10-09T21:53:54.410426+00:00"
}
```

## Verified Deployment

```json
{
  "checked_at_utc": "2026-10-09T21:54:31.141270+00:00",
  "actual_pcrd": "0.1.82",
  "source_tests": "1047 passed, 153 subtests passed in 103.95s (0:01:43)",
  "installed_tests": "1046 passed, 1 deselected, 153 subtests passed in 103.28s (0:01:43)",
  "activation_tests": "16 passed in 0.20s",
  "native_synthetic_loopback_turns_per_runtime": 9,
  "real_user_turn_submissions": 0,
  "wheel_sha256": "34fc598dc1cf4d3d1f116dde34365e9644ba1331870663a8f99baa111a5f2473",
  "client_and_node_processes_preserved": true,
  "idle_pinned_worker_processes_preserved": 6,
  "saved_awaiting_confirmation_messages_preserved": 2,
  "receipt_store_preserved": true,
  "actual_macos": [
    "0.1.429",
    "429"
  ],
  "healthy_paired_GPT_6_1_Sol_available": true
}
```
