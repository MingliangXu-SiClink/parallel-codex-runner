# Per-Agent Remote Environment Access

## Scope and Cause

The former PCR developer instruction treated the private local project directory
as the only writable project workspace without distinguishing local and remote
hosts. Its absolute prohibition on other workspaces and Git checkouts could
conflict with the user's authorized SSH-based research or development task.
PCR already requests full-access Codex execution; it does not disable SSH or
remove the user's SSH agent environment. This is an instruction-scope defect,
not evidence that the operating system had blocked all remote connections.

The new rule confines local filesystem/Git isolation to the local host and
explicitly allows each candidate and synthesis Agent to perform user-authorized
remote work. A per-run, per-Agent relative namespace keeps code copies, logs,
and outputs separate by default. Shared inputs can be read in place. Explicit
shared changes need coordination through an existing scheduler or lock.
Remote authentication and project restrictions are unchanged; namespaces are
execution guidance, not automatic provisioning or an OS sandbox.

Remote effects remain external to PCR local sync-back and cleanup. Candidate
selection/discard does not stop or roll back remote jobs. Agents must retain
host/workspace/job/result references for inspection.

## User Boundaries

- The earlier finalization/performance redesign is paused and is not included.
- The user authorized publication and activation only after global idle.
- No existing task is stopped, adopted, discarded, recovered, retried, or resent.
- No running candidate configuration or research directory is modified.
- No macOS app restart, foreground QA window, Android operation, or Node service interruption.

## Verification Boundary

Focused regression after adding caller identity: 59 passed and 2 subtests.
Full source/installed tests and native Codex configuration acceptance are recorded
below when completed. Native acceptance does not submit a model turn or launch
real SSH experiments. Tests prove instruction injection, identity isolation,
preserved SSH environment and execution settings, not remote experiment success.
An installed staged runtime is not considered active until live service checks
confirm the version, health, pairing, models, and preserved processes.

## Automated Regression and Staging

- Source suite: 1025 passed and 127 subtests passed (90.96 seconds).
- Installed 0.1.80 runtime: 1024 passed, 127 subtests passed (94.55 seconds).
  Only the repository-specific vendored Textual path assertion was deselected;
  the remaining suite imported the installed wheel, not the source tree.
- Isolated activation guard tests: 16 passed. They cover every busy gate,
  unavailable models, already-current versions, late queue arrivals, and
  rollback without interrupting newly started tasks.
- Native config/read acceptance preserved the exact instructions for two
  private Agent identities. No model turn or remote connection was submitted.
- Immutable wheel SHA-256:
  `45386a792392aae91d48c870107590396206a87e8232d0d6ac5dd192f422c465`.
- Only `parallel_codex_runner_core/app.py` differs from the 0.1.79 wheel.
  Unrelated execution, history and finalization definitions are unchanged.
- Runtime 0.1.80 is installed separately. No running candidate configuration
  was changed. Native SSH availability is not remote experiment acceptance.

An optional hidden embedded-browser consultation was not sent because that
browser restored an unrelated existing draft. The draft was left untouched;
the diagnosis and validation above are based on local code and tests.

## Deferred Activation

The guarded activation returned 75 at 2026-10-09T06:44:14Z. The actual service
remained healthy, paired and connected at 0.1.79, with detached workers and
active run `pcr-20261009T055904Z-aeca783c`. Active operations, admission and
delivery counts, and queued message count were zero. The scheduler and client
were not restarted. The 0.1.80 rules are published and staged, not yet active.

The user authorized enabling only after all tasks, finalization and message
queues are idle. A quiet thread heartbeat (automation ID `pcrd-0-1-80`)
checks every 15 minutes using the tested activation script. It must pause
itself after confirmed activation, an already-current service, cancellation,
or a deployment failure. Existing Agent instructions are not changed in place.

## Native Configuration Acceptance

```json
{
  "checked_at_utc": "2026-10-09T06:43:51.548312+00:00",
  "passed": true,
  "agents": [
    {
      "agent": 1,
      "native_config_preserved": true,
      "remote_namespace_present": true,
      "local_restriction_preserved": true
    },
    {
      "agent": 2,
      "native_config_preserved": true,
      "remote_namespace_present": true,
      "local_restriction_preserved": true
    }
  ],
  "native_ssh_binary_available": true,
  "model_turn_submitted": false,
  "remote_connection_opened": false,
  "qa_window_opened": false,
  "client_or_scheduler_restarted": false
}
```

## Published Backend

```json
{
  "checked_at_utc": "2026-10-09T06:44:07.548968+00:00",
  "pcrd": "0.1.80",
  "wheel_sha256": "45386a792392aae91d48c870107590396206a87e8232d0d6ac5dd192f422c465",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Final Read-Only Release Check

```json
{
  "checked_at_utc": "2026-10-09T06:49:07.228482+00:00",
  "published_version": "0.1.80",
  "staged_runtime_verified": true,
  "changed_runtime_modules": [
    "parallel_codex_runner_core/app.py"
  ],
  "other_release_artifacts_unchanged": true,
  "actual_version": "0.1.79",
  "healthy": true,
  "detached_workers": true,
  "active_runs": [
    "pcr-20261009T055904Z-aeca783c"
  ],
  "active_operation": null,
  "prompt_delivery": {
    "running": true,
    "pending_count": 0,
    "active_count": 0
  },
  "prompt_admission_count": 0,
  "controller_paired": true,
  "link_state": "connected",
  "gpt_6_1_sol_present": true,
  "installed_macos_build": "419",
  "launch_interpreter": "/Users/mingliangxu/Library/Application Support/parallel-codex-runner/runtime/0.1.79/bin/python",
  "activation_deferred": true,
  "automatic_followup_id": "pcrd-0-1-80",
  "tasks_resubmitted_or_stopped_by_this_release": false,
  "client_or_node_services_restarted_by_this_release": false
}
```

## Activation 2026-10-09T08:04:23.793177+00:00

```json
{
  "actual_version": "0.1.80",
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
  "scheduler_pid_before": 877,
  "scheduler_pid_after": 46777,
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
  "checked_at_utc": "2026-10-09T08:04:23.793177+00:00"
}
```

## Verified Deployment and Follow-up Paused

```json
{
  "checked_at_utc": "2026-10-09T08:07:53.846773+00:00",
  "actual_version": "0.1.80",
  "healthy": true,
  "detached_workers": true,
  "controller_paired": true,
  "link_connected": true,
  "gpt_6_1_sol_present": true,
  "active_runs": [],
  "active_operation": null,
  "script_activation_record_verified": true,
  "client_binary_unchanged_during_activation": true,
  "client_and_node_processes_preserved_during_activation": true,
  "client_restarted": false,
  "tasks_stopped_or_resubmitted": false,
  "automatic_followup_id": "pcrd-0-1-80",
  "automatic_followup_status": "PAUSED",
  "existing_agent_instructions_changed_in_place": false
}
```
